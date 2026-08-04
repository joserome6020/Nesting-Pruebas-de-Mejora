"""Mide el PoC v2 con el filtro de candidatos NFP integrado.

Uso:
  python -m benchmarks.pack_nfp_filter_benchmark --all --iterations 5

Cada repetición reinicia el caché antes de empaquetar, por lo que cada resultado
incluye sus propios misses iniciales y no depende del orden de escenarios.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from benchmarks.corpus_loader import list_scenarios
from benchmarks.runner import run_scenario


def benchmark_scenario(scenario: str, iterations: int) -> dict[str, Any]:
    samples = [run_scenario(scenario, "cpp_v2_poc") for _ in range(iterations)]
    failures = [row for row in samples if not row.get("pass_ok") or not row.get("compare_ok", True)]
    if failures:
        raise RuntimeError(f"{scenario}: {len(failures)} corrida(s) fail-closed")

    elapsed = [float(row["elapsed_ms"]) for row in samples]
    hits = [int((row.get("nfp_cache") or {}).get("hits") or 0) for row in samples]
    misses = [int((row.get("nfp_cache") or {}).get("misses") or 0) for row in samples]
    return {
        "scenario": scenario,
        "iterations": iterations,
        "elapsed_ms": {
            "min": min(elapsed),
            "median": statistics.median(elapsed),
            "max": max(elapsed),
        },
        "nfp_cache": {
            "hits_median": statistics.median(hits),
            "misses_median": statistics.median(misses),
            "hit_rate_median": statistics.median(
                [h / (h + m) if h + m else 0.0 for h, m in zip(hits, misses)]
            ),
        },
        "validation": {
            "placed": samples[0]["placed"],
            "expected": samples[0]["expected"],
            "efi_directa": samples[0]["efi_directa"],
            "solape_ok": all(bool(row["solape_ok"]) for row in samples),
            "kerf_violations": max(int(row["kerf_violations"]) for row in samples),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="cpp_v2 NFP candidate-filter benchmark")
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    scenarios = list(args.scenario)
    if args.all or not scenarios:
        scenarios = list_scenarios()
    iterations = max(1, int(args.iterations))
    rows = [benchmark_scenario(scenario, iterations) for scenario in scenarios]

    for row in rows:
        cache = row["nfp_cache"]
        elapsed = row["elapsed_ms"]
        valid = row["validation"]
        print(
            f"{row['scenario']}: median={elapsed['median']:.2f}ms "
            f"range={elapsed['min']:.2f}-{elapsed['max']:.2f}ms "
            f"cache_hit_rate={cache['hit_rate_median']:.2%} "
            f"placed={valid['placed']}/{valid['expected']} "
            f"solape_ok={valid['solape_ok']} kerf_viol={valid['kerf_violations']}"
        )

    payload = {"engine": "cpp_v2_poc", "results": rows}
    if args.out:
        out_path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
