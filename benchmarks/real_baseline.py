"""Baselines repetibles y fail-closed para snapshots de corpus real.

Ejemplo:
  python -m benchmarks.real_baseline \
    --scenario r_1000kva_critical \
    --engines arga_force,svgnest_ultra,cpp_v2_poc --runs 5
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.corpus_loader import (
    load_json,
    load_scenario,
    scenario_path,
)
from benchmarks.runner import run_scenario

_ROOT = Path(__file__).resolve().parent
_OUTPUT_DIR = _ROOT / "results_real"
_NAME_INSTANCE_SUFFIX = re.compile(r"#\d+$")


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _engine_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    timings = [float(row.get("elapsed_ms") or 0.0) for row in rows]
    efficiencies = [float(row.get("efi_directa") or 0.0) for row in rows]
    placed = [int(row.get("placed") or 0) for row in rows]
    geom_ok = all(
        not str(row.get("error") or "")
        and bool(row.get("solape_ok"))
        and int(row.get("kerf_violations") or 0) == 0
        and int(row.get("in_holes") or 0) == 0
        for row in rows
    )
    return {
        "runs": len(rows),
        "pass_runs": sum(1 for row in rows if row.get("pass_ok")),
        "geometry_ok_all_runs": geom_ok,
        "median_elapsed_ms": _median(timings),
        "range_elapsed_ms": [min(timings), max(timings)] if timings else [],
        "median_efi_directa": _median(efficiencies),
        "range_efi_directa": [min(efficiencies), max(efficiencies)] if efficiencies else [],
        "median_placed": _median([float(value) for value in placed]),
        "range_placed": [min(placed), max(placed)] if placed else [],
        "expected": int(rows[0].get("expected") or 0) if rows else 0,
        "nfp_cache_last": rows[-1].get("nfp_cache") if rows else None,
    }


def _bom_from_pieces(pieces: list[dict]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for piece in pieces:
        name = _NAME_INSTANCE_SUFFIX.sub("", str(piece.get("nombre") or ""))
        counts[name] += 1
    return dict(sorted(counts.items()))


def _cpu_gate(by_engine: dict[str, dict[str, Any]]) -> dict[str, Any]:
    force = by_engine.get("arga_force")
    candidate = by_engine.get("cpp_v2_poc")
    if not force or not candidate:
        return {
            "passed": False,
            "reason": "missing_reference_or_cpp_v2",
        }
    if not force.get("geometry_ok_all_runs") or not candidate.get("geometry_ok_all_runs"):
        return {
            "passed": False,
            "reason": "geometry_failure",
        }
    delta_pp = float(candidate["median_efi_directa"] or 0.0) - float(
        force["median_efi_directa"] or 0.0
    )
    placed_delta = float(candidate["median_placed"] or 0.0) - float(
        force["median_placed"] or 0.0
    )
    reasons: list[str] = []
    if delta_pp < -2.0:
        reasons.append("efficiency_below_force_by_more_than_2pp")
    if placed_delta < 0:
        reasons.append("placed_below_force")
    return {
        "passed": not reasons,
        "efficiency_delta_pp_vs_arga_force": delta_pp,
        "placed_delta_vs_arga_force": placed_delta,
        "reason": ",".join(reasons) if reasons else "passed",
    }


def benchmark(
    *,
    scenarios: list[str],
    engines: list[str],
    runs: int,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": "arga_real_baseline_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runs_per_engine": runs,
        "scenarios": {},
    }
    for scenario_id in scenarios:
        params, pieces = load_scenario(scenario_id)
        source_data = load_json(scenario_path(scenario_id))
        source_kind = str(source_data.get("source_kind") or "embedded")
        scenario_rows: dict[str, list[dict[str, Any]]] = {}
        for engine_id in engines:
            rows: list[dict[str, Any]] = []
            for index in range(runs):
                print(f"== {scenario_id} / {engine_id} / run {index + 1}/{runs} ==")
                rows.append(run_scenario(scenario_id, engine_id))
            scenario_rows[engine_id] = rows
        summaries = {
            engine_id: _engine_summary(rows)
            for engine_id, rows in scenario_rows.items()
        }
        report["scenarios"][scenario_id] = {
            "source_kind": source_kind,
            "params": {
                key: params[key]
                for key in (
                    "plate_w_in",
                    "plate_h_in",
                    "kerf_in",
                    "margin_in",
                    "corner",
                    "opt",
                    "require_full_place",
                )
            },
            "bom_expected": _bom_from_pieces(pieces),
            "export_audit": (
                "not_applicable_geometry_snapshot"
                if source_kind == "workspace_geometry_snapshot"
                else "pending"
            ),
            "runs": scenario_rows,
            "summary": summaries,
            "cpu_gate": _cpu_gate(summaries),
        }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Baselines CPU de corpus real")
    parser.add_argument("--scenario", action="append", required=True)
    parser.add_argument(
        "--engines",
        default="arga_force,svgnest_ultra,cpp_v2_poc",
    )
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--out", default="")
    parser.add_argument("--fail-on-gate", action="store_true")
    args = parser.parse_args(argv)

    engines = [part.strip() for part in str(args.engines).split(",") if part.strip()]
    report = benchmark(
        scenarios=list(args.scenario),
        engines=engines,
        runs=max(1, int(args.runs)),
    )
    output = Path(args.out) if args.out else _OUTPUT_DIR / "cpu_baseline.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(output)
    gate_ok = all(
        bool(case.get("cpu_gate", {}).get("passed"))
        for case in report["scenarios"].values()
    )
    return 0 if gate_ok or not args.fail_on_gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
