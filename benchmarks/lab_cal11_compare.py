"""Comparación multi-placa de ARGA Force y LAB SIMULATOR.

El reporte conserva métricas y timeline por placa para revisar el acomodo de
calibre 11 sin tocar producción. El escenario es un snapshot local del corpus.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

from benchmarks.corpus_loader import load_scenario
from benchmarks.geom_audit import count_in_holes, gap_audit

IN_TO_MM = 25.4


def _remaining_from_placed(pool: list[dict[str, Any]], hoja: dict[str, Any]) -> list[dict[str, Any]]:
    placed = Counter(str(piece.get("nombre") or "") for piece in (hoja.get("piezas") or []))
    remaining: list[dict[str, Any]] = []
    for piece in pool:
        name = str(piece.get("nombre") or "")
        if placed.get(name, 0) > 0:
            placed[name] -= 1
        else:
            remaining.append(piece)
    return remaining


def _audit_sheet(hoja: dict[str, Any], kerf_in: float) -> dict[str, Any]:
    from modules.nesting_engine.efficiency_metrics import actualizar_eficiencias_hoja
    from modules.nesting_engine.sheet_integrity import hoja_tiene_solapes_metal

    data = dict(hoja or {})
    actualizar_eficiencias_hoja(data)
    has_overlap, overlap_detail = hoja_tiene_solapes_metal(data, kerf_in=kerf_in)
    gap = gap_audit(data, kerf_in=kerf_in)
    return {
        "placed": len(data.get("piezas") or []),
        "efi_directa": float(data.get("efi_directa") or 0.0),
        "efi_real": float(data.get("efi_real") or 0.0),
        "solape_ok": not has_overlap,
        "solape_detail": str(overlap_detail or ""),
        "kerf_violations": int(gap.get("kerf_violations") or 0),
        "overlap_violations": int(gap.get("overlap_violations") or 0),
        "min_gap_mm": gap.get("min_gap_mm"),
        "in_holes": count_in_holes(data),
        "hoja": data,
    }


def _compact_timeline(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Conserva el orden y estrategia sin duplicar toda la geometría DXF."""
    fields = (
        "colocada",
        "nombre",
        "px",
        "py",
        "rotacion_grados",
        "categoria",
        "estrategia",
        "score",
        "bbox_w_mm",
        "bbox_h_mm",
        "variaciones_evaluadas",
    )
    return [
        {field: step[field] for field in fields if field in step}
        for step in steps
        if isinstance(step, dict)
    ]


def _run_one_sheet(
    pool: list[dict[str, Any]],
    *,
    kind: str,
    w_mm: float,
    h_mm: float,
    kerf_in: float,
    margin_in: float,
    corner: str,
    opt: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], float, list[dict[str, Any]], str]:
    from modules.nesting_engine.sim_lab import run_plate_sim, run_timeline_sim

    if kind == "lab_pilot":
        from modules.nesting_engine.lab_pilot_adapter import pack_one_sheet

        started = time.perf_counter()
        try:
            hoja, remaining = pack_one_sheet(
                pool,
                plate_w_mm=w_mm,
                plate_h_mm=h_mm,
                kerf_in=kerf_in,
                margin_in=margin_in,
                opt=opt,
                corner=corner,
                mc_iterations=1,
            )
            return hoja, remaining, (time.perf_counter() - started) * 1000.0, [], ""
        except Exception as exc:
            return {}, list(pool), (time.perf_counter() - started) * 1000.0, [], str(exc)

    if kind in {"lab_timeline", "production_timeline"}:
        previous = os.environ.get("ARGA_NEST_LAB")
        try:
            if kind == "lab_timeline":
                os.environ["ARGA_NEST_LAB"] = "1"
            else:
                os.environ.pop("ARGA_NEST_LAB", None)
            timeline = run_timeline_sim(
                pool,
                w_mm=w_mm,
                h_mm=h_mm,
                kerf_in=kerf_in,
                margin_in=margin_in,
                corner=corner,
                opt=opt,
                mc_iterations=1,
            )
        finally:
            if previous is None:
                os.environ.pop("ARGA_NEST_LAB", None)
            else:
                os.environ["ARGA_NEST_LAB"] = previous
        hoja = dict(timeline.hoja or {})
        remaining = _remaining_from_placed(pool, hoja)
        return hoja, remaining, float(timeline.elapsed_ms or 0.0), list(timeline.pasos or []), str(
            timeline.error or ""
        )

    timeline = run_plate_sim(
        pool,
        w_mm=w_mm,
        h_mm=h_mm,
        kerf_in=kerf_in,
        margin_in=margin_in,
        corner=corner,
        opt=opt,
        mc_iterations=1,
        engine_id=kind,
        isolate_process=False,
    )
    hoja = dict(timeline.hoja or {})
    remaining = _remaining_from_placed(pool, hoja)
    return hoja, remaining, float(timeline.elapsed_ms or 0.0), list(timeline.pasos or []), str(
        timeline.error or ""
    )


def run_multisheet(
    *,
    pieces: list[dict[str, Any]],
    params: dict[str, Any],
    kind: str,
    max_sheets: int,
    svg_dir: Path | None = None,
) -> dict[str, Any]:
    w_mm = float(params["plate_w_in"]) * IN_TO_MM
    h_mm = float(params["plate_h_in"]) * IN_TO_MM
    kerf_in = float(params["kerf_in"])
    margin_in = float(params["margin_in"])
    pool = list(pieces)
    sheets: list[dict[str, Any]] = []
    total_elapsed = 0.0
    area_used = 0.0
    errors: list[str] = []
    strategies: Counter[str] = Counter()

    for index in range(1, max_sheets + 1):
        if not pool:
            break
        hoja, remaining, elapsed_ms, pasos, error = _run_one_sheet(
            pool,
            kind=kind,
            w_mm=w_mm,
            h_mm=h_mm,
            kerf_in=kerf_in,
            margin_in=margin_in,
            corner=str(params["corner"]),
            opt=str(params["opt"]),
        )
        total_elapsed += elapsed_ms
        if error:
            errors.append(f"sheet_{index}: {error}")
        audit = _audit_sheet(hoja, kerf_in)
        audit.update(
            {
                "sheet_index": index,
                "elapsed_ms": elapsed_ms,
                "timeline": _compact_timeline(pasos),
            }
        )
        if svg_dir is not None:
            from benchmarks.nest_svg import write_sheet_svg

            svg_path = write_sheet_svg(
                dict(audit.get("hoja") or {}),
                plate_w_mm=w_mm,
                plate_h_mm=h_mm,
                output=svg_dir / f"placa_{index:02d}.svg",
                title=f"{kind} · placa {index}",
            )
            audit["svg_path"] = str(svg_path)
        area_used += float((audit.get("hoja") or {}).get("area_piezas_mm2") or 0.0)
        # El DXF completo ya vive en el snapshot; no repetir megabytes de
        # polígonos en el reporte de métricas.
        audit.pop("hoja", None)
        sheets.append(audit)
        for step in pasos:
            strategies[str(step.get("estrategia") or "unknown")] += 1
        if audit["placed"] <= 0 or len(remaining) >= len(pool):
            errors.append(f"sheet_{index}: no_progress")
            break
        pool = remaining

    placed = sum(int(sheet["placed"]) for sheet in sheets)
    sheet_count = len(sheets)
    plate_area = w_mm * h_mm
    return {
        "engine": kind,
        "requested": len(pieces),
        "placed": placed,
        "unplaced": len(pool),
        "sheet_count": sheet_count,
        "elapsed_ms": total_elapsed,
        "efficiency_total": (area_used / (sheet_count * plate_area) * 100.0)
        if sheet_count and plate_area > 0
        else 0.0,
        "geometry_ok": all(
            bool(sheet["solape_ok"])
            and int(sheet["kerf_violations"]) == 0
            and int(sheet["overlap_violations"]) == 0
            for sheet in sheets
        ),
        "kerf_violations": sum(int(sheet["kerf_violations"]) for sheet in sheets),
        "overlap_violations": sum(int(sheet["overlap_violations"]) for sheet in sheets),
        "in_holes": sum(int(sheet["in_holes"]) for sheet in sheets),
        "strategies": dict(sorted(strategies.items())),
        "errors": errors,
        "sheets": sheets,
    }


def compare(
    scenario: str,
    engines: list[str],
    runs: int,
    max_sheets: int,
    *,
    cal11_gate: bool = False,
    max_p95_ms: float = 45_000.0,
    max_sheets_gate: int = 7,
    min_efficiency_gate: float = 60.0,
    svg_dir: Path | None = None,
) -> dict[str, Any]:
    params, pieces = load_scenario(scenario)
    # La promoción mide el tiempo de nest, no la carga única del .pyd al abrir
    # la aplicación. Se precarga el piloto antes de iniciar las corridas.
    if "lab_pilot" in engines:
        from modules.nesting_engine.lab_pilot_adapter import is_ready

        if not is_ready():
            raise RuntimeError("El binario algorithm_cpp_lab_pilot no está listo.")
    reports: dict[str, list[dict[str, Any]]] = {engine: [] for engine in engines}
    for engine in engines:
        for _ in range(runs):
            reports[engine].append(
                run_multisheet(
                    pieces=pieces,
                    params=params,
                    kind=engine,
                    max_sheets=max_sheets,
                    svg_dir=(svg_dir / engine) if svg_dir is not None else None,
                )
            )
    summary: dict[str, Any] = {}
    for engine, rows in reports.items():
        exemplar = rows[0]
        summary[engine] = {
            "median_elapsed_ms": float(statistics.median(row["elapsed_ms"] for row in rows)),
            "median_sheet_count": float(statistics.median(row["sheet_count"] for row in rows)),
            "median_placed": float(statistics.median(row["placed"] for row in rows)),
            "median_efficiency_total": float(
                statistics.median(row["efficiency_total"] for row in rows)
            ),
            "geometry_ok_all_runs": all(bool(row["geometry_ok"]) for row in rows),
            "kerf_violations": max(int(row["kerf_violations"]) for row in rows),
            "overlap_violations": max(int(row["overlap_violations"]) for row in rows),
            "in_holes": max(int(row["in_holes"]) for row in rows),
            "exemplar": exemplar,
        }
        if cal11_gate:
            elapsed = [float(row["elapsed_ms"]) for row in rows]
            all_placed = all(int(row["placed"]) == len(pieces) for row in rows)
            geometry_ok = all(bool(row["geometry_ok"]) for row in rows)
            sheets_ok = all(int(row["sheet_count"]) <= max_sheets_gate for row in rows)
            efficiency_ok = all(
                float(row["efficiency_total"]) >= min_efficiency_gate for row in rows
            )
            # Con solo tres corridas, el máximo es un p95 conservador.
            p95_conservative = max(elapsed, default=0.0)
            summary[engine]["cal11_gate"] = {
                "passed": all_placed
                and geometry_ok
                and sheets_ok
                and efficiency_ok
                and p95_conservative <= max_p95_ms,
                "all_placed": all_placed,
                "geometry_ok": geometry_ok,
                "sheets_ok": sheets_ok,
                "efficiency_ok": efficiency_ok,
                "p95_conservative_ms": p95_conservative,
                "max_p95_ms": max_p95_ms,
                "max_sheets": max_sheets_gate,
                "min_efficiency_total": min_efficiency_gate,
            }
    return {
        "schema": "arga_lab_cal11_compare_v1",
        "scenario": scenario,
        "runs": runs,
        "params": params,
        "engines": summary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compara calibre 11 en LAB y producción")
    parser.add_argument("--scenario", default="r_giga_cal11")
    parser.add_argument("--engines", default="arga_force,lab_timeline")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--max-sheets", type=int, default=30)
    parser.add_argument(
        "--out",
        default="benchmarks/results_real/lab_cal11_compare.json",
    )
    parser.add_argument("--gate-cal11", action="store_true")
    parser.add_argument("--max-p95-ms", type=float, default=45_000.0)
    parser.add_argument("--gate-max-sheets", type=int, default=7)
    parser.add_argument("--gate-min-efficiency", type=float, default=60.0)
    parser.add_argument(
        "--svg-dir",
        default="",
        help="Carpeta opcional para un SVG tangible por placa.",
    )
    args = parser.parse_args(argv)
    engines = [item.strip() for item in str(args.engines).split(",") if item.strip()]
    report = compare(
        str(args.scenario),
        engines,
        max(1, int(args.runs)),
        max(1, int(args.max_sheets)),
        cal11_gate=bool(args.gate_cal11),
        max_p95_ms=max(1.0, float(args.max_p95_ms)),
        max_sheets_gate=max(1, int(args.gate_max_sheets)),
        min_efficiency_gate=max(0.0, float(args.gate_min_efficiency)),
        svg_dir=Path(args.svg_dir) if args.svg_dir else None,
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    if args.gate_cal11:
        return 0 if all(
            bool((result.get("cal11_gate") or {}).get("passed"))
            for result in (report.get("engines") or {}).values()
        ) else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
