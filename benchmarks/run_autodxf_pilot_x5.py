"""Genera workspace interactivo ×5 con el piloto sobre placas madre locales.

No reemplaza la corrida completa de MotorNesting: no usa retazos irregulares.
Su propósito es inspeccionar en la UI de ANS el acomodo del motor piloto usando
los mismos AutoDXF, nomenclatura, calibre, material y cantidades ×N.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from benchmarks.run_autodxf_ans_x5 import (
    _build_parts,
    _load_local_available_plates,
    _workspace_payload,
)


def _load_group_pieces(parts: list[tuple]) -> dict[str, list[dict[str, Any]]]:
    from modules.nesting_engine.sim_lab import piece_from_dxf

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    errors: list[str] = []
    for name, material, qty, calibre, _state, route in parts:
        pieces, error = piece_from_dxf(
            route,
            nombre=name,
            qty=int(qty),
            calibre=calibre,
            material=material,
        )
        if error:
            errors.append(f"{name}: {error}")
            continue
        groups[f"{calibre}_{material}"].extend(pieces)
    if errors:
        raise ValueError(" | ".join(errors))
    return dict(groups)


def _remaining(pool: list[dict], hoja: dict) -> list[dict]:
    placed = Counter(str(piece.get("nombre") or "") for piece in (hoja.get("piezas") or []))
    pending: list[dict] = []
    for piece in pool:
        name = str(piece.get("nombre") or "")
        if placed.get(name, 0):
            placed[name] -= 1
        else:
            pending.append(piece)
    return pending


def _run_group(
    *,
    key: str,
    pieces: list[dict],
    plate: dict[str, Any],
    kerf_in: float,
    margin_in: float,
    max_sheets: int,
) -> tuple[dict[str, Any], float]:
    from modules.nesting_engine.efficiency_metrics import calcular_eficiencias_grupo
    from modules.nesting_engine.lab_pilot_adapter import pack_one_sheet
    from modules.nesting_engine.sheet_integrity import asegurar_identidad_hojas

    started = time.perf_counter()
    pending = list(pieces)
    sheets: list[dict[str, Any]] = []
    for index in range(1, max_sheets + 1):
        if not pending:
            break
        hoja, remains = pack_one_sheet(
            pending,
            plate_w_mm=float(plate["w"]),
            plate_h_mm=float(plate["h"]),
            kerf_in=kerf_in,
            margin_in=margin_in,
            opt="OPTIMIZAR LARGO Y ANCHO",
            corner="INFERIOR IZQUIERDA",
            mc_iterations=1,
        )
        if not hoja.get("piezas"):
            raise RuntimeError(f"{key}: piloto no progresó en placa {index}.")
        hoja.update(
            {
                "placa_w": float(plate["w"]),
                "placa_h": float(plate["h"]),
                "placa_id": str(plate["id"]),
                "placa_cal": str(plate["calibre"]),
                "precio_placa": float(plate["precio"]),
                "origen_placa": str(plate["origen"]),
                "kerf_usado": kerf_in,
                "margin_usado": margin_in,
                "corner_usado": "INFERIOR IZQUIERDA",
                "opt_usado": "OPTIMIZAR LARGO Y ANCHO",
                "es_retazo": False,
                "pilot_sheet_index": index,
            }
        )
        sheets.append(hoja)
        # `restos` del adaptador ya mantiene repetidos, pero el conteo de
        # colocadas es la salvaguarda contra nombres idénticos del AutoDXF.
        pending = _remaining(pending, hoja)
        if remains and len(pending) >= len(pieces) and index == 1:
            raise RuntimeError(f"{key}: piloto no redujo el pool inicial.")

    if pending:
        raise RuntimeError(f"{key}: quedaron {len(pending)} piezas tras {len(sheets)} placas.")
    asegurar_identidad_hojas(sheets, clave=key)
    group = {
        "placa": "Óptima (piloto, placas madre)",
        "dim": "Multi",
        "hojas": sheets,
        "piezas_pool": [{"nombre": str(piece.get("nombre") or "")} for piece in pieces],
        "piezas_pool_engine": True,
        "costo_total": sum(float(sheet.get("precio_placa") or 0.0) for sheet in sheets),
        "costo_empresa": sum(float(sheet.get("precio_placa") or 0.0) for sheet in sheets),
        "costo_proveedor": 0.0,
        "reporte": "Piloto ×5: placas madre rectangulares; sin RTZ.",
        "match_placa": "snapshot_local",
        **calcular_eficiencias_grupo(sheets),
    }
    return group, (time.perf_counter() - started) * 1000.0


def run(
    *,
    source_dir: Path,
    calibre: str,
    multiplier: int,
    plates_snapshot: Path,
    report_path: Path,
    workspace_path: Path,
    max_sheets: int,
) -> dict[str, Any]:
    from interface.nesting_workspace import guardar_workspace_payload
    from modules.nesting_engine.efficiency_metrics import actualizar_eficiencias_resultados
    from modules.nesting_engine.manager import MotorNesting
    from modules.nesting_engine.nest_poka_yoke import listar_fallas_resultados_nest

    parts = _build_parts(source_dir, calibre=calibre, multiplier=multiplier)
    pieces_by_group = _load_group_pieces(parts)
    plates = _load_local_available_plates(plates_snapshot)
    selector = MotorNesting()
    result: dict[str, Any] = {"_nest_engine_id": "arga_lab_pilot"}
    timings: dict[str, float] = {}
    selected_plates: dict[str, dict[str, Any]] = {}
    for key, pieces in pieces_by_group.items():
        req_calibre, material = key.split("_", 1)
        candidates, match = selector._clasificar_placas_por_calibre(req_calibre, material, plates)
        if not candidates:
            raise ValueError(f"{key}: no hay placa disponible compatible en snapshot local.")
        selected = candidates[0]
        group, elapsed_ms = _run_group(
            key=key,
            pieces=pieces,
            plate=selected,
            kerf_in=0.3,
            margin_in=0.15,
            max_sheets=max_sheets,
        )
        group["match_placa"] = match
        result[key] = group
        timings[key] = elapsed_ms
        selected_plates[key] = {
            "placa_id": selected["id"],
            "w_mm": selected["w"],
            "h_mm": selected["h"],
            "match": match,
        }

    actualizar_eficiencias_resultados(result)
    failures = listar_fallas_resultados_nest(result)
    report: dict[str, Any] = {
        "schema": "arga_autodxf_pilot_x5_v1",
        "mode": "pilot_mother_plates_only",
        "calibre": calibre,
        "multiplier": multiplier,
        "engine_id": "arga_lab_pilot",
        "input_dxf_count": len(parts),
        "requested_piece_count": sum(int(part[2]) for part in parts),
        "group_timings_ms": timings,
        "selected_plates": selected_plates,
        "integrity_failures": failures,
        "groups": {
            key: {
                "sheets": len(group.get("hojas") or []),
                "efficiency_direct": float(group.get("eficiencia_tanque_directa") or 0.0),
                "efficiency_real": float(group.get("eficiencia_tanque_real") or 0.0),
            }
            for key, group in result.items()
            if isinstance(group, dict) and "hojas" in group
        },
    }
    if not failures:
        payload = _workspace_payload(
            result=result,
            parts=parts,
            engine_id="arga_lab_pilot",
            multiplier=multiplier,
            mode="pilot_mother_plates_only",
        )
        guardar_workspace_payload(payload, str(workspace_path))
        report["workspace"] = str(workspace_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Workspace interactivo piloto AutoDXF ×N")
    parser.add_argument(
        "--source-dir",
        default=r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\AutoDXF",
    )
    parser.add_argument("--calibre", default="0.11811")
    parser.add_argument("--multiplier", type=int, default=5)
    parser.add_argument("--plates-snapshot", default="cache/herinox_plates_snapshot.json")
    parser.add_argument("--max-sheets", type=int, default=80)
    parser.add_argument("--report", default="benchmarks/results_real/autodxf_pilot_x5/report.json")
    parser.add_argument(
        "--workspace",
        default="benchmarks/results_real/autodxf_pilot_x5/VALIDACION_PILOTO_CAL11_X5.arganest",
    )
    args = parser.parse_args(argv)
    report = run(
        source_dir=Path(args.source_dir),
        calibre=str(args.calibre),
        multiplier=max(1, int(args.multiplier)),
        plates_snapshot=Path(args.plates_snapshot),
        report_path=Path(args.report),
        workspace_path=Path(args.workspace),
        max_sheets=max(1, int(args.max_sheets)),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["integrity_failures"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
