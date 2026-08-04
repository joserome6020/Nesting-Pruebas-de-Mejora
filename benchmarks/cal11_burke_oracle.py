"""Burke como oráculo acotado de calidad para una placa calibre 11."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from benchmarks.corpus_loader import load_scenario
from benchmarks.lab_cal11_compare import IN_TO_MM, _audit_sheet
from benchmarks.nest_svg import write_sheet_svg


def _representative_pieces(pieces: list[dict], maximum: int) -> list[dict]:
    """Toma perfiles distintos y grandes, no muchas copias de una pieza simple."""
    if maximum <= 0:
        return list(pieces)
    representatives: dict[str, dict] = {}
    for piece in pieces:
        name = str(piece.get("nombre") or "")
        current = representatives.get(name)
        if current is None or piece["poly"].area > current["poly"].area:
            representatives[name] = piece
    return sorted(
        representatives.values(),
        key=lambda piece: float(piece["poly"].area or 0.0),
        reverse=True,
    )[:maximum]


def run_oracle(
    scenario: str,
    *,
    timeout_s: float,
    svg_dir: Path,
    max_pieces: int,
    engine_id: str,
) -> dict:
    from modules.nesting_engine.sim_lab import run_plate_sim

    params, all_pieces = load_scenario(scenario)
    pieces = _representative_pieces(all_pieces, max_pieces)
    started = time.perf_counter()
    timeline = run_plate_sim(
        pieces,
        w_mm=float(params["plate_w_in"]) * IN_TO_MM,
        h_mm=float(params["plate_h_in"]) * IN_TO_MM,
        kerf_in=float(params["kerf_in"]),
        margin_in=float(params["margin_in"]),
        corner=str(params["corner"]),
        opt=str(params["opt"]),
        mc_iterations=1,
        engine_id=engine_id,
        isolate_process=True,
        timeout_s=max(1.0, timeout_s),
    )
    wall_elapsed_ms = (time.perf_counter() - started) * 1000.0
    sheet = dict(timeline.hoja or {})
    audit = _audit_sheet(sheet, float(params["kerf_in"]))
    if sheet.get("piezas"):
        svg = write_sheet_svg(
            sheet,
            plate_w_mm=float(params["plate_w_in"]) * IN_TO_MM,
            plate_h_mm=float(params["plate_h_in"]) * IN_TO_MM,
            output=svg_dir / f"{engine_id}_placa_01.svg",
            title=f"{engine_id} · {scenario} · placa 1 · muestra {len(pieces)}",
        )
        audit["svg_path"] = str(svg)
    audit.pop("hoja", None)
    return {
        "schema": "arga_cal11_burke_oracle_v1",
        "scenario": scenario,
        "engine": engine_id,
        "timeout_s": timeout_s,
        # `ok` requiere colocar el lote entero; esta prueba compara
        # deliberadamente una sola placa y por ello puede ser parcial.
        "completed": not bool(timeline.error),
        "full_placement": bool(timeline.ok),
        "error": str(timeline.error or ""),
        "elapsed_ms": float(timeline.elapsed_ms or wall_elapsed_ms),
        "isolated_wall_elapsed_ms": wall_elapsed_ms,
        "requested": len(pieces),
        "source_requested": len(all_pieces),
        "selection": (
            "all pieces" if max_pieces <= 0 else "largest distinct profiles"
        ),
        "remaining": len(timeline.restos or []) if not timeline.error else len(pieces),
        "audit": audit,
        "interpretation": (
            "Referencia de calidad de una placa; no cambia producción ni "
            "autoriza ejecutar Burke sin timeout."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Oráculo Burke acotado calibre 11")
    parser.add_argument("--scenario", default="r_autodxf_desktop_cal11")
    parser.add_argument("--engine", default="burke_blf")
    parser.add_argument("--timeout-s", type=float, default=90.0)
    parser.add_argument(
        "--max-pieces",
        type=int,
        default=0,
        help="0 = lote completo; otro valor usa perfiles distintos de mayor área.",
    )
    parser.add_argument(
        "--svg-dir",
        default="benchmarks/results_real/autodxf_desktop_cal11_layout",
    )
    parser.add_argument(
        "--out",
        default="benchmarks/results_real/autodxf_desktop_cal11_burke_oracle.json",
    )
    args = parser.parse_args(argv)
    report = run_oracle(
        str(args.scenario),
        timeout_s=float(args.timeout_s),
        svg_dir=Path(args.svg_dir),
        max_pieces=int(args.max_pieces),
        engine_id=str(args.engine),
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0 if report["completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
