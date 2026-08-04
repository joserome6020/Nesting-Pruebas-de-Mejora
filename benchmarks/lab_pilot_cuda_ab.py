"""A/B end-to-end del piloto LAB con/sin acelerador CUDA.

CUDA no es un motor nuevo: acelera el escaneo raster dentro de
``arga_lab_pilot``. Clipper2 sigue validando la shortlist.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

from benchmarks.lab_cal11_compare import _audit_sheet, _remaining_from_placed
from benchmarks.corpus_loader import load_scenario


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _run_engine(
    *,
    use_cuda: bool,
    params: dict[str, Any],
    pieces: list[dict[str, Any]],
) -> dict[str, Any]:
    previous = os.environ.get("ARGA_LAB_PILOT_CUDA")
    # El polish Venom es no-determinista y no forma parte del acelerador CUDA.
    import modules.nesting_engine.venom_ai as venom_ai

    previous_polisher = venom_ai.apply_smart_polisher
    venom_ai.apply_smart_polisher = lambda hoja, engine_id: hoja
    try:
        if use_cuda:
            os.environ["ARGA_LAB_PILOT_CUDA"] = "1"
        else:
            os.environ.pop("ARGA_LAB_PILOT_CUDA", None)

        from modules.nesting_engine.lab_pilot_adapter import pack_one_sheet

        plate_w_in = float(params["plate_w_in"])
        plate_h_in = float(params["plate_h_in"])
        kerf_in = float(params.get("kerf_in") or 0.3)
        margin_in = float(params.get("margin_in") or 0.0)
        opt = str(params.get("opt") or "OPTIMIZAR LARGO Y ANCHO")
        corner = str(params.get("corner") or "INFERIOR IZQUIERDA")

        pool = list(pieces)
        sheets: list[dict[str, Any]] = []
        started = time.perf_counter()
        while pool:
            hoja, restos = pack_one_sheet(
                pool,
                plate_w_mm=plate_w_in * 25.4,
                plate_h_mm=plate_h_in * 25.4,
                kerf_in=kerf_in,
                margin_in=margin_in,
                opt=opt,
                corner=corner,
                mc_iterations=1,
            )
            audit = _audit_sheet(hoja, kerf_in)
            sheets.append(
                {
                    "placed": audit["placed"],
                    "efi_directa": audit["efi_directa"],
                    "solape_ok": audit["solape_ok"],
                    "kerf_violations": audit["kerf_violations"],
                    "cuda_screen": dict(hoja.get("cuda_screen") or {}),
                    "names": [str(p.get("nombre") or "") for p in (hoja.get("piezas") or [])],
                }
            )
            next_pool = restos if restos is not None else _remaining_from_placed(pool, hoja)
            if len(next_pool) >= len(pool):
                break
            pool = next_pool
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        placed = sum(int(s["placed"]) for s in sheets)
        return {
            "elapsed_ms": elapsed_ms,
            "sheets": len(sheets),
            "placed": placed,
            "expected": len(pieces),
            "efi_directa_total": (
                sum(float(s["efi_directa"]) for s in sheets) / len(sheets) if sheets else 0.0
            ),
            "solape_ok": all(bool(s["solape_ok"]) for s in sheets),
            "kerf_violations": sum(int(s["kerf_violations"]) for s in sheets),
            "cuda_used": any(
                bool((s.get("cuda_screen") or {}).get("cuda_used")) for s in sheets
            ),
            "cuda_candidates": sum(
                int((s.get("cuda_screen") or {}).get("candidates_evaluated") or 0)
                for s in sheets
            ),
            "sheet_details": sheets,
        }
    finally:
        venom_ai.apply_smart_polisher = previous_polisher
        if previous is None:
            os.environ.pop("ARGA_LAB_PILOT_CUDA", None)
        else:
            os.environ["ARGA_LAB_PILOT_CUDA"] = previous


def benchmark(*, scenario_id: str, repeats: int) -> dict[str, Any]:
    params, pieces = load_scenario(scenario_id)
    cpu_rows = [
        _run_engine(use_cuda=False, params=params, pieces=pieces) for _ in range(repeats)
    ]
    cuda_rows = [
        _run_engine(use_cuda=True, params=params, pieces=pieces) for _ in range(repeats)
    ]

    cpu_ms = _median([float(r["elapsed_ms"]) for r in cpu_rows])
    cuda_ms = _median([float(r["elapsed_ms"]) for r in cuda_rows])
    speedup = (cpu_ms / cuda_ms) if cpu_ms and cuda_ms and cuda_ms > 0 else None

    def _fingerprint(row: dict[str, Any]) -> tuple:
        return (
            int(row["placed"]),
            int(row["sheets"]),
            int(row["kerf_violations"]),
            bool(row["solape_ok"]),
            tuple(tuple(s.get("names") or []) for s in (row.get("sheet_details") or [])),
        )

    cpu_fp = _fingerprint(cpu_rows[0])
    parity_ok = all(_fingerprint(row) == cpu_fp for row in cpu_rows + cuda_rows)
    cuda_active = all(bool(row.get("cuda_used")) for row in cuda_rows)
    # Geometría: no peor que el baseline CPU del mismo motor.
    geometry_ok = all(
        bool(row["solape_ok"])
        and int(row["kerf_violations"]) <= int(cpu_rows[0]["kerf_violations"])
        for row in cuda_rows
    )

    return {
        "schema": "arga_lab_pilot_cuda_e2e_ab_v1",
        "scenario": scenario_id,
        "repeats": repeats,
        "cpu": {
            "median_elapsed_ms": cpu_ms,
            "placed": cpu_rows[0]["placed"],
            "sheets": cpu_rows[0]["sheets"],
            "efi_directa_total": cpu_rows[0]["efi_directa_total"],
        },
        "cuda": {
            "median_elapsed_ms": cuda_ms,
            "placed": cuda_rows[0]["placed"],
            "sheets": cuda_rows[0]["sheets"],
            "efi_directa_total": cuda_rows[0]["efi_directa_total"],
            "cuda_used_all_runs": cuda_active,
            "candidates_evaluated": cuda_rows[0]["cuda_candidates"],
        },
        "speedup": speedup,
        "gate": {
            "parity_ok": parity_ok,
            "cuda_active": cuda_active,
            "geometry_ok": all(
                row["solape_ok"] and row["kerf_violations"] == 0
                for row in cpu_rows + cuda_rows
            ),
            "faster_or_equal": bool(speedup is not None and speedup >= 1.0),
            "production_ready": False,
            "next_requirement": (
                "promover solo si speedup estable en Cal11/GIGA y sombra multi-corpus"
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A/B CUDA end-to-end del lab_pilot")
    parser.add_argument("--scenario", default="r_1000kva_critical")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--out",
        default="benchmarks/results_real/lab_pilot_cuda_e2e.json",
    )
    args = parser.parse_args(argv)
    report = benchmark(scenario_id=args.scenario, repeats=max(1, args.repeats))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(out)
    gate = report["gate"]
    return 0 if gate["parity_ok"] and gate["geometry_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
