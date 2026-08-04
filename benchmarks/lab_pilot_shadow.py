"""Ejecuta el piloto registrado en sombra sobre varios corpus.

El reporte no cambia el motor seleccionado ni permite promoción automática.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.corpus_loader import load_scenario
from benchmarks.lab_cal11_compare import run_multisheet


DEFAULT_SCENARIOS = (
    "s0_micro",
    "s1_single_plate",
    "r_1000kva_critical",
    "r_2500kva_x29_critical",
    "r_2500kva_x30_critical",
    "r_giga_cal11",
)


def run_shadow(scenarios: list[str], *, max_sheets: int) -> dict:
    reports: list[dict] = []
    for scenario in scenarios:
        try:
            params, pieces = load_scenario(scenario)
            result = run_multisheet(
                pieces=pieces,
                params=params,
                kind="arga_lab_pilot",
                max_sheets=max_sheets,
            )
            requires_full = bool(params.get("require_full_place", True))
            complete = int(result["placed"]) == len(pieces)
            reports.append(
                {
                    "scenario": scenario,
                    "requested": len(pieces),
                    "placed": int(result["placed"]),
                    "sheet_count": int(result["sheet_count"]),
                    "efficiency_total": float(result["efficiency_total"]),
                    "elapsed_ms": float(result["elapsed_ms"]),
                    "geometry_ok": bool(result["geometry_ok"]),
                    "errors": list(result["errors"]),
                    "shadow_gate": {
                        "geometry_ok": bool(result["geometry_ok"]),
                        "complete_when_required": complete if requires_full else True,
                        "no_engine_errors": not result["errors"],
                        "manual_promotion_ready": False,
                    },
                }
            )
        except Exception as exc:
            reports.append(
                {
                    "scenario": scenario,
                    "shadow_gate": {
                        "geometry_ok": False,
                        "complete_when_required": False,
                        "no_engine_errors": False,
                        "manual_promotion_ready": False,
                    },
                    "error": str(exc),
                }
            )

    for report in reports:
        gate = report["shadow_gate"]
        gate["passed"] = bool(
            gate["geometry_ok"]
            and gate["complete_when_required"]
            and gate["no_engine_errors"]
        )
    return {
        "schema": "arga_lab_pilot_shadow_v1",
        "engine": "arga_lab_pilot",
        "reports": reports,
        "all_geometry_ok": all(
            bool((report.get("shadow_gate") or {}).get("geometry_ok"))
            for report in reports
        ),
        "all_gates_passed": all(
            bool((report.get("shadow_gate") or {}).get("passed"))
            for report in reports
        ),
        "promotion": "manual_only; hidden_in_ui; no_default_change",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sombra multi-corpus ARGA LAB Pilot")
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--max-sheets", type=int, default=30)
    parser.add_argument(
        "--out",
        default="benchmarks/results_real/lab_pilot_shadow.json",
    )
    args = parser.parse_args(argv)
    report = run_shadow(
        list(args.scenario or DEFAULT_SCENARIOS),
        max_sheets=max(1, int(args.max_sheets)),
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0 if report["all_gates_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
