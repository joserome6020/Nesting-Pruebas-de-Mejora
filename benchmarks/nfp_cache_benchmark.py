"""Mide NFP sin caché frente a la caché L1 de cpp_v2.

Uso:
  python -m benchmarks.nfp_cache_benchmark --all --iterations 3
  python -m benchmarks.nfp_cache_benchmark --scenario s1_single_plate --out resultado.json

El workload usa pares ordenados de piezas reales del corpus. No mide el packer:
mide estrictamente la hipótesis de valor de reutilizar NFP repetidos. La
geometría canónica se prepara una vez por lote, que es el patrón que usará el
packer al generar variaciones, y se informa ese coste por separado.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Callable

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from benchmarks.corpus_loader import list_scenarios, load_scenario, pieces_to_native


def _ordered_pairs(native_pieces: list[dict]) -> list[tuple[list, list]]:
    """Pares A→B: la dirección importa para el NFP exterior."""
    pairs: list[tuple[list, list]] = []
    for i, a in enumerate(native_pieces):
        rings_a = a.get("rings") or []
        if not rings_a:
            continue
        for j, b in enumerate(native_pieces):
            if i == j:
                continue
            rings_b = b.get("rings") or []
            if rings_b:
                pairs.append((rings_a, rings_b))
    return pairs


def _run_calls(
    func: Callable[[list, list], Any],
    pairs: list[tuple[list, list]],
    iterations: int,
) -> tuple[float, int]:
    calls = 0
    started = time.perf_counter()
    for _ in range(iterations):
        for rings_a, rings_b in pairs:
            func(rings_a, rings_b)
            calls += 1
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return elapsed_ms, calls


def benchmark_scenario(scenario_id: str, iterations: int, capacity: int) -> dict[str, Any]:
    from modules.nesting_engine.algorithm_bridge_v2 import (
        compute_nfp_outer,
        reset_nfp_cache,
        run_nfp_cache_workload,
        set_nfp_cache_capacity,
    )

    params, pieces = load_scenario(scenario_id)
    native = pieces_to_native(pieces)
    pairs = _ordered_pairs(native)
    kerf_mm = float(params["kerf_in"]) * 25.4

    uncached_ms, calls = _run_calls(
        lambda a, b: compute_nfp_outer(a, b),
        pairs,
        iterations,
    )

    set_nfp_cache_capacity(capacity)
    reset_nfp_cache()
    workload = run_nfp_cache_workload(
        native,
        iterations=iterations,
        kerf_mm=kerf_mm,
    )
    cached_calls = int(workload["calls"])
    cached_lookup_ms = float(workload["lookup_ms"])
    preparation_ms = float(workload["preparation_ms"])
    cached_total_ms = preparation_ms + cached_lookup_ms
    stats = dict(workload["cache"])
    assert calls == cached_calls

    return {
        "scenario": scenario_id,
        "pieces": len(native),
        "ordered_pairs_per_iteration": len(pairs),
        "iterations": iterations,
        "calls": calls,
        "uncached_ms": uncached_ms,
        "cached_lookup_ms": cached_lookup_ms,
        "preparation_ms": preparation_ms,
        "cached_total_ms": cached_total_ms,
        "lookup_speedup": (
            (uncached_ms / cached_lookup_ms) if cached_lookup_ms > 0.0 else None
        ),
        "end_to_end_speedup": (
            (uncached_ms / cached_total_ms) if cached_total_ms > 0.0 else None
        ),
        "cache": stats,
        "cache_hit_rate": float(stats.get("hit_rate") or 0.0),
        "kerf_mm": kerf_mm,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark L1 NFP cache for cpp_v2")
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--capacity", type=int, default=4096)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    scenarios = list(args.scenario)
    if args.all or not scenarios:
        scenarios = list_scenarios()
    iterations = max(1, int(args.iterations))
    capacity = max(1, int(args.capacity))

    results = [benchmark_scenario(s, iterations, capacity) for s in scenarios]
    for row in results:
        cache = row["cache"]
        print(
            f"{row['scenario']}: calls={row['calls']} "
            f"uncached={row['uncached_ms']:.2f}ms "
            f"prep={row['preparation_ms']:.2f}ms "
            f"lookup={row['cached_lookup_ms']:.2f}ms "
            f"lookup_speedup={row['lookup_speedup']:.2f}x "
            f"e2e_speedup={row['end_to_end_speedup']:.2f}x "
            f"hit_rate={row['cache_hit_rate']:.1%} "
            f"hits={cache['hits']} misses={cache['misses']} entries={cache['entries']}"
        )

    payload = {"engine": "cpp_v2_poc", "results": results}
    if args.out:
        out_path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
