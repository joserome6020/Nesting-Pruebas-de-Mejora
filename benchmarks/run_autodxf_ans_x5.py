"""Ejecuta una orden local AutoDXF mediante el flujo real de MotorNesting.

No es un benchmark de packer: conserva la nomenclatura AutoDXF, agrupa por
calibre/material, selecciona placas desde el snapshot local de ANS y genera un
workspace .arganest abrible cuando la orden pasa los poka-yokes de producción.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def _same_calibre(value: str, expected: str) -> bool:
    try:
        return abs(float(str(value).replace(",", ".")) - float(expected)) <= 0.0001
    except (TypeError, ValueError):
        return str(value).strip().casefold() == str(expected).strip().casefold()


def _build_parts(
    source_dir: Path,
    *,
    calibre: str,
    multiplier: int,
    material: str = "",
    steel_gauge: int = 0,
) -> list[tuple]:
    from interface.autodxf_metadata import combinar_metadata_dxf
    from modules.nesting_engine.manager import MotorNesting

    parts: list[tuple] = []
    for path in sorted(source_dir.rglob("*.dxf"), key=lambda item: item.name.casefold()):
        piece, parsed_material, qty, parsed_calibre, _extras = combinar_metadata_dxf(
            str(path),
            default_material="CARBONO",
            default_calibre="",
        )
        same_gauge = False
        if steel_gauge > 0:
            thickness = MotorNesting._thickness_inches_for_match(parsed_calibre)
            same_gauge = (
                thickness is not None
                and MotorNesting._nearest_steel_gauge(float(thickness)) == steel_gauge
            )
        if not same_gauge and not _same_calibre(parsed_calibre, calibre):
            continue
        if material and str(parsed_material).strip().casefold() != str(material).strip().casefold():
            continue
        try:
            base_qty = max(1, int(str(qty).strip()))
        except (TypeError, ValueError):
            base_qty = 1
        parts.append(
            (
                str(piece),
                str(parsed_material),
                str(base_qty * multiplier),
                str(parsed_calibre),
                "LISTO",
                str(path),
            )
        )
    if not parts:
        raise ValueError(f"No se encontraron DXF con calibre {calibre} en {source_dir}.")
    return parts


def _load_local_available_plates(snapshot: Path) -> list[list[Any]]:
    from modules.sheets_manager import PlatesManager

    raw = json.loads(snapshot.read_text(encoding="utf-8"))
    rows = list(raw.get("empresa") or [])
    available = PlatesManager().filtrar_placas_para_nesting(rows)
    if not available:
        raise ValueError(f"El snapshot local no contiene placas DISPONIBLE: {snapshot}")
    return available


def _json_safe(value: Any) -> Any:
    """Solo para el reporte de error previo a generar un workspace."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__geo_interface__"):
        return str(value)
    return value


def _workspace_payload(
    *,
    result: dict[str, Any],
    parts: list[tuple],
    engine_id: str,
    multiplier: int,
    mode: str = "ans_manager_local_snapshot",
    job_name: str = "",
) -> dict[str, Any]:
    from interface.nesting_workspace import SCHEMA_WORKSPACE, clasificar_material_workspace

    first_key = ""
    first_sheet: dict[str, Any] = {}
    for key, group in result.items():
        if not isinstance(group, dict) or not isinstance(group.get("hojas"), list):
            continue
        if group["hojas"]:
            first_key = str(key)
            first_sheet = dict(group["hojas"][0] or {})
            break
    lote = {
        "lote_k": multiplier,
        "nest_engine_id": engine_id,
        "data": result,
    }
    resolved_job = str(job_name or f"VALIDACION_AUTODXF_CAL11_X{multiplier}")
    payload = {
        "schema": SCHEMA_WORKSPACE,
        "workspace_type": "nesting_workspace",
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "workspace_label": resolved_job.replace("_", " "),
        "job_activo": resolved_job,
        "lote_actual_idx": 0,
        "resultados_multilote": [lote],
        "datos_partes_actuales": [list(part) for part in parts],
        "editable_inputs_by_lote": [[list(part) for part in parts]],
        "editable_inputs_actuales": [list(part) for part in parts],
        "source_dxf_paths": [str(part[5]) for part in parts],
        "source_dxf_paths_by_lote": [[str(part[5]) for part in parts]],
        "wo_reales_por_lote": {0: resolved_job},
        "ultimos_escenarios": [],
        "dxf_export_cache": {
            "transform_ready": False,
            "note": "Workspace de validación local: la UI prepara transformaciones DXF al abrir.",
        },
        "ui_state": {
            "cantidad_tanques": f"X{multiplier}",
            "multiplicador_tanques": multiplier,
            "lote_k_activo": multiplier,
            "global_kerf_val": 0.3,
            "global_margin_val": 0.15,
            "global_corner_val": "INFERIOR IZQUIERDA",
            "cmb_opt_val": "OPTIMIZAR LARGO Y ANCHO",
        },
        "vista_actual": {
            "clave_actual": first_key,
            "placa_id": first_sheet.get("placa_id"),
            "sheet_uid": first_sheet.get("sheet_uid"),
            "nest_list_idx": first_sheet.get("_nest_list_idx", 0),
        },
        "validation_meta": {
            "mode": mode,
            "quantity_multiplier": multiplier,
            "engine_id": engine_id,
            "inventory_source": "cache/herinox_plates_snapshot.json",
        },
    }
    payload["workspace_material_kind"] = clasificar_material_workspace(payload)
    return payload


def run(
    *,
    source_dir: Path,
    calibre: str,
    multiplier: int,
    engine_id: str,
    plates_snapshot: Path,
    report_path: Path,
    workspace_path: Path,
    material: str = "",
    steel_gauge: int = 0,
    job_name: str = "",
) -> dict[str, Any]:
    from modules.nesting_engine.efficiency_metrics import actualizar_eficiencias_resultados
    from modules.nesting_engine.manager import MotorNesting
    from modules.nesting_engine.nest_poka_yoke import listar_fallas_resultados_nest
    from modules.nesting_engine.sheet_integrity import (
        asegurar_identidad_hojas,
        deduplicar_resultados_nesting,
    )

    parts = _build_parts(
        source_dir,
        calibre=calibre,
        multiplier=multiplier,
        material=material,
        steel_gauge=steel_gauge,
    )
    plates = _load_local_available_plates(plates_snapshot)
    started = time.perf_counter()
    motor = MotorNesting()
    result = motor.ejecutar_nesting_visual(
        parts,
        plates,
        progress_callback=lambda message, pct: print(f"[{pct:05.1%}] {message}", flush=True),
        config_kerf=0.3,
        config_margin=0.15,
        config_corner="INFERIOR IZQUIERDA",
        config_opt="OPTIMIZAR LARGO Y ANCHO",
        wo_name=str(job_name or f"VALIDACION_AUTODXF_CAL11_X{multiplier}"),
        engine_id=engine_id,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    error = str((result or {}).get("error") or "")
    report: dict[str, Any] = {
        "schema": "arga_ans_autodxf_x5_v1",
        "source_dir": str(source_dir),
        "calibre": calibre,
        "steel_gauge": steel_gauge,
        "material_filter": material,
        "job_name": str(job_name or ""),
        "multiplier": multiplier,
        "engine_id": engine_id,
        "input_dxf_count": len(parts),
        "requested_piece_count": sum(int(part[2]) for part in parts),
        "material_calibre_groups": sorted(
            {f"{part[3]}_{part[1]}" for part in parts}
        ),
        "available_plate_count": len(plates),
        "elapsed_ms": elapsed_ms,
        "error": error,
        "dxf_audit": _json_safe(getattr(motor, "_ultima_auditoria_dxf", {})),
        "input_parts": [
            {
                "pieza": str(part[0]),
                "material": str(part[1]),
                "cantidad": int(part[2]),
                "calibre": str(part[3]),
                "dxf": str(part[5]),
            }
            for part in parts
        ],
    }
    if error:
        report["status"] = "blocked_by_production_poka_yoke"
        report["result"] = _json_safe(result)
    else:
        deduplicar_resultados_nesting(result, kerf_global=0.3)
        for key, group in result.items():
            if isinstance(group, dict) and isinstance(group.get("hojas"), list):
                asegurar_identidad_hojas(group["hojas"], clave=str(key))
        actualizar_eficiencias_resultados(result)
        failures = listar_fallas_resultados_nest(result)
        report["status"] = "ready_for_workspace" if not failures else "integrity_failed"
        report["integrity_failures"] = failures
        report["groups"] = {
            str(key): {
                "sheet_count": len((group or {}).get("hojas") or []),
                "efficiency_direct": float((group or {}).get("eficiencia_tanque_directa") or 0.0),
                "efficiency_real": float((group or {}).get("eficiencia_tanque_real") or 0.0),
            }
            for key, group in result.items()
            if isinstance(group, dict) and "hojas" in group
        }
        report["selected_plates"] = [
            {
                "group": str(key),
                "sheet_number": index,
                "plate_id": str(sheet.get("placa_id") or ""),
                "width_mm": float(sheet.get("placa_w") or 0.0),
                "height_mm": float(sheet.get("placa_h") or 0.0),
                "source": str(sheet.get("origen_placa") or ""),
                "price": float(sheet.get("precio_placa") or 0.0),
                "is_remnant": bool(sheet.get("es_retazo", False)),
                "piece_count": len(sheet.get("piezas") or []),
                "efficiency": float(sheet.get("eficiencia") or 0.0),
            }
            for key, group in result.items()
            if isinstance(group, dict)
            for index, sheet in enumerate(group.get("hojas") or [], start=1)
            if isinstance(sheet, dict)
        ]
        report["selection_policy"] = {
            "flow": "MotorNesting.ejecutar_nesting_visual",
            "inventory": "snapshot local de Herinox, placas DISPONIBLE",
            "filter": "material y calibre nominal exacto",
            "choice": (
                "simulación por formato apto, score de piezas colocadas, sobrante, "
                "costo y, cuando el perfil lo activa, lookahead; con retazos e "
                "integridad ANS"
            ),
        }
        if not failures:
            from interface.nesting_workspace import guardar_workspace_payload

            payload = _workspace_payload(
                result=result,
                parts=parts,
                engine_id=engine_id,
                multiplier=multiplier,
                job_name=job_name,
            )
            guardar_workspace_payload(payload, str(workspace_path))
            report["workspace"] = str(workspace_path)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(_json_safe(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Orden AutoDXF ×N por MotorNesting ANS")
    parser.add_argument(
        "--source-dir",
        default=r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\AutoDXF",
    )
    parser.add_argument("--calibre", default="0.11811")
    parser.add_argument("--multiplier", type=int, default=5)
    parser.add_argument(
        "--material",
        default="",
        help="Material exacto de la nomenclatura, p. ej. 'A 36 GALV'.",
    )
    parser.add_argument(
        "--steel-gauge",
        type=int,
        default=0,
        help="Filtra por calibre nominal de acero; 11 incluye 0.1181 y 0.11811.",
    )
    parser.add_argument("--job-name", default="")
    parser.add_argument("--engine", default="svgnest_ultra")
    parser.add_argument(
        "--plates-snapshot",
        default="cache/herinox_plates_snapshot.json",
    )
    parser.add_argument(
        "--report",
        default="benchmarks/results_real/autodxf_ans_x5/report.json",
    )
    parser.add_argument(
        "--workspace",
        default="benchmarks/results_real/autodxf_ans_x5/VALIDACION_AUTODXF_CAL11_X5.arganest",
    )
    args = parser.parse_args(argv)
    if int(args.multiplier) < 1:
        raise ValueError("--multiplier debe ser >= 1")
    report = run(
        source_dir=Path(args.source_dir),
        calibre=str(args.calibre),
        multiplier=int(args.multiplier),
        engine_id=str(args.engine),
        plates_snapshot=Path(args.plates_snapshot),
        report_path=Path(args.report),
        workspace_path=Path(args.workspace),
        material=str(args.material),
        steel_gauge=int(args.steel_gauge),
        job_name=str(args.job_name),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ready_for_workspace" else 2


if __name__ == "__main__":
    raise SystemExit(main())
