# -*- coding: utf-8 -*-
"""
Flujo unificado para pruebas:

    DXF -> JSON crudo -> LS READY V3 R3 240X96 UF-2 B
    JSON crudo -> LS READY V3 R3 240X96 UF-2 B

Uso:
    python run_ls_ready_flow.py
    python run_ls_ready_flow.py entrada.dxf
    python run_ls_ready_flow.py entrada.json
    python run_ls_ready_flow.py entrada.dxf salida_ls_ready.json

Notas:
- Para DXF directo requiere: pip install ezdxf
- Esta version usa el lector DXF oficial/historico incluido en dxf/official_lector.
- La base de datos queda desactivada; el DXF se toma desde selector de archivos o argumento CLI.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CONFIG_DIR = os.path.join(ROOT, "config")
if CONFIG_DIR not in sys.path:
    sys.path.insert(0, CONFIG_DIR)

import classification_config as cfg
from classification.classifier import classify_json_file, classify_json_data, _build_robot_programming, _normalize_meta_for_ls
from classification.ls_ready_v3 import apply_ls_ready_v3, refresh_ls_ready_v3_metadata
from classification.plate_profile import load_runtime_config
from dxf.official_lector.lector_dxf import process_dxf as official_process_dxf


def _select_file_dialog() -> Optional[str]:
    try:
        from tkinter import Tk, filedialog
    except Exception:
        return None

    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    initialdir = desktop if os.path.isdir(desktop) else os.getcwd()

    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    path = filedialog.askopenfilename(
        title="Selecciona DXF o JSON crudo",
        initialdir=initialdir,
        filetypes=[
            ("DXF o JSON", "*.dxf *.json"),
            ("DXF", "*.dxf"),
            ("JSON", "*.json"),
            ("Todos", "*.*"),
        ],
    )
    root.destroy()
    return path or None


def _default_raw_json_path(dxf_path: str) -> str:
    # El lanzador maestro puede dirigir el JSON crudo a la carpeta del trabajo.
    out_dir = os.environ.get("LS_FLOW_RAW_DIR") or os.path.join(ROOT, "json", "raw_from_dxf")
    base = os.path.splitext(os.path.basename(dxf_path))[0]
    return os.path.join(out_dir, base + ".json")


def _convert_dxf_with_official_reader(dxf_path: str, raw_json_path: str) -> str:
    """
    Ejecuta el lector DXF historico/oficial y regresa el JSON crudo generado.

    El lector se llama con output_folder fijo para que la salida quede dentro
    del paquete LS READY y no dependa de carpetas del RPA ni de la base de datos.
    """
    output_folder = os.path.dirname(os.path.normpath(raw_json_path))
    result = official_process_dxf(dxf_file=dxf_path, output_folder=output_folder)
    if not result or not result.get("ok"):
        message = (result or {}).get("message", "No se pudo convertir DXF a JSON crudo.")
        raise RuntimeError(message)
    return os.path.normpath(result.get("output_file") or raw_json_path)


def _default_ls_ready_path(input_path: str) -> str:
    out_dir = getattr(cfg, "CLASSIFIED_JSON_DIR", os.path.join(ROOT, "json", "classified"))
    base = os.path.splitext(os.path.basename(input_path))[0]
    if base.lower().endswith("_classified"):
        base = base[:-11]
    return os.path.join(out_dir, base + "_ls_ready_v3.json")


def _resolve_input(argv) -> Optional[str]:
    if len(argv) >= 2 and argv[1].strip():
        return os.path.normpath(argv[1])
    return _select_file_dialog()



def _write_json_atomic(path: str, data: dict) -> None:
    path = os.path.normpath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _json_kind(json_path: str) -> str:
    """raw | classified | unknown"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if any(k in data for k in ("cut_outer", "cut_inner", "mark")):
        return "raw"
    if data.get("pieces") is not None and data.get("plan") is not None:
        return "classified"
    return "unknown"


def _upgrade_classified_json_to_ls_ready(json_path: str, output_path: str, logs: list) -> dict:
    """Permite usar un JSON ya clasificado v2/v3 sin volver a clasificarlo como raw."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    legacy = data.get("legacy") or {}
    schema_v3 = str(data.get("schema_version") or "") == "carlos_rpa.piece_plan.v3.uf2_b"
    map_id = str((data.get("motion_map") or {}).get("map_id") or "")
    map_robot = str(((data.get("motion_map") or {}).get("meta") or {}).get("robot") or "").upper()
    programming_robot = str((data.get("robot_programming") or {}).get("robot") or "")
    already_r3 = map_id == "R3_B_240X96_E1_J2_V1" and map_robot == "R3" and programming_robot == "3"

    # Solo se permite refrescar sin reordenar cuando los previews E1/J2 ya fueron
    # calculados con el mapa R3. Un JSON R4 debe reconstruirse desde legacy/raw.
    if schema_v3 and data.get("validation") and already_r3:
        data = refresh_ls_ready_v3_metadata(data, logs=logs)
        data.setdefault("source", {})["classified_json_path"] = os.path.normpath(output_path)
        _write_json_atomic(output_path, data)
        logs.append("ls_ready_v3_r3_uf2_b: input_already_r3 refreshed_without_reordering")
        return data

    # Si llega un classified anterior (incluido R4) y trae legacy raw, se reconstruye
    # para recalcular todas las posturas con el mapa R3.
    if any(legacy.get(k) for k in ("cut_outer", "cut_inner", "mark")):
        raw_data = {
            "meta": data.get("meta") or {},
            "cut_outer": legacy.get("cut_outer") or [],
            "cut_inner": legacy.get("cut_inner") or [],
            "mark": legacy.get("mark") or [],
        }
        rebuilt = classify_json_data(raw_data, source_path=json_path, logs=logs)
        rebuilt.setdefault("source", {})["classified_json_path"] = os.path.normpath(output_path)
        _write_json_atomic(output_path, rebuilt)
        logs.append("ls_ready_v3_uf2_b: rebuilt_from_legacy_raw")
        return rebuilt

    if schema_v3 and not already_r3:
        raise ValueError(
            "El JSON clasificado no contiene geometría legacy/raw y sus previews E1/J2 no pertenecen al mapa R3. "
            "Use el DXF o JSON crudo para reconstruirlo de forma segura."
        )

    meta = _normalize_meta_for_ls(data.get("meta") or {})
    data["meta"] = meta
    runtime_cfg, plate_profile = load_runtime_config(meta, logs=logs)
    data["plate_profile"] = plate_profile
    # Clasificador exclusivo cama B: siempre reconstruir robot_programming para UF-2,
    # aunque el JSON clasificado de entrada venga de otra cama/UF previo.
    data["robot_programming"] = _build_robot_programming(meta, runtime_cfg)
    data.setdefault("source", {})["json_path"] = os.path.normpath(json_path)
    data.setdefault("source", {})["classifier_root"] = ROOT

    upgraded = apply_ls_ready_v3(data, runtime_cfg=runtime_cfg, logs=logs)
    upgraded.setdefault("source", {})["classified_json_path"] = os.path.normpath(output_path)
    _write_json_atomic(output_path, upgraded)
    return upgraded

def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv
    input_path = _resolve_input(argv)
    if not input_path or not os.path.isfile(input_path):
        print("No se encontró entrada.")
        print("Uso: python run_ls_ready_flow.py <entrada.dxf|entrada.json> [salida_ls_ready.json]")
        return 1

    ext = os.path.splitext(input_path)[1].lower()
    final_output = os.path.normpath(argv[2]) if len(argv) >= 3 and argv[2].strip() else _default_ls_ready_path(input_path)
    logs = []

    try:
        if ext == ".dxf":
            raw_json_path = _default_raw_json_path(input_path)
            print("Convirtiendo DXF a JSON crudo con lector oficial:", raw_json_path)
            json_path = _convert_dxf_with_official_reader(input_path, raw_json_path)
        elif ext == ".json":
            json_path = input_path
        else:
            print("Extensión no soportada:", ext)
            return 1

        if ext == ".json" and _json_kind(json_path) == "classified":
            print("JSON ya clasificado detectado. Actualizando/confirmando LS READY V3 R3 240X96 UF-2 B:", final_output)
            result = _upgrade_classified_json_to_ls_ready(json_path, final_output, logs)
        elif ext == ".json" and _json_kind(json_path) == "unknown":
            print("El JSON no parece ser raw del lector DXF ni classified.")
            return 1
        else:
            print("Clasificando a LS READY V3 R3 240X96 UF-2 B:", final_output)
            result = classify_json_file(json_path, output_path=final_output, logs=logs)

    except Exception as exc:
        print("ERROR:", exc)
        traceback.print_exc()
        return 1

    summary = result.get("summary") or {}
    track = result.get("track_flow") or {}
    profile = result.get("plate_profile") or {}
    validation = result.get("validation") or {}
    nesting_size = result.get("nesting_size") or {}

    print("=" * 60)
    print("LS READY V3 R3 240X96 UF-2 B OK")
    print("Entrada original:", input_path)
    print("JSON usado:", json_path)
    print("Salida:", result.get("source", {}).get("classified_json_path", final_output))
    print("Perfil:", profile.get("key"), "(solicitado:", profile.get("requested_key"), ")")
    print(
        "Tamaño informativo del nesting:",
        nesting_size.get("width_mm"), "x", nesting_size.get("height_mm"), "mm",
        "(fuente:", nesting_size.get("reported_source"), ")",
    )
    print("Envolvente máxima de programación: 6096.0 x 2438.0 mm")
    print("Piezas:", summary.get("piece_count"))
    print("Orden MARK:", " -> ".join(track.get("piece_order_mark") or []))
    print("Orden CUT :", " -> ".join(track.get("piece_order_cut") or []))
    print("Validación:", validation.get("status"))
    if validation.get("warnings"):
        for w in validation["warnings"]:
            print("  WARNING:", w)
    if validation.get("errors"):
        for e in validation["errors"]:
            print("  ERROR:", e)
    print("=" * 60)
    for line in logs:
        print(line)
    return 0 if validation.get("status") != "error" else 2


if __name__ == "__main__":
    sys.exit(main())
