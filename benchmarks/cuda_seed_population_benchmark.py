"""A/B de cribado raster para una población de semillas.

Compara:
1. CPU chatty (una llamada por semilla, prefer_cuda=False)
2. CUDA sesión chatty (máscara fija residente, loop Python)
3. CUDA screen_population (una sola llamada C++; candidata residente)

No sustituye el nesting exacto: cada ``False`` pasa después a Clipper2.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from pathlib import Path

from benchmarks.cuda_raster_benchmark import _make_workload
from modules.nesting_engine.cuda_seed_screener import (
    screen_seed_population,
    screen_seed_population_loop_baseline,
)


def _median(values: list[float]) -> float:
    return float(statistics.median(values))


def _population(
    offsets: list[tuple[int, int]],
    *,
    seeds: int,
    candidates_per_seed: int,
) -> list[list[tuple[int, int]]]:
    population: list[list[tuple[int, int]]] = []
    for seed in range(seeds):
        rng = random.Random(10_000 + seed)
        if candidates_per_seed >= len(offsets):
            batch = list(offsets)
            rng.shuffle(batch)
        else:
            batch = rng.sample(offsets, candidates_per_seed)
        population.append(batch)
    return population


def benchmark(*, seeds: int, candidates_per_seed: int, repeats: int) -> dict:
    fixed, fixed_w, fixed_h, candidate, candidate_w, candidate_h, offsets = _make_workload()
    population = _population(
        offsets,
        seeds=max(1, seeds),
        candidates_per_seed=max(1, candidates_per_seed),
    )
    cpu_times: list[float] = []
    loop_times: list[float] = []
    oneshot_times: list[float] = []
    parity_ok = True
    cuda_active = False
    safe_rejected = 0
    oneshot_stats: dict = {}
    loop_stats: dict = {}

    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        cpu_result = screen_seed_population_loop_baseline(
            fixed,
            fixed_w,
            fixed_h,
            candidate,
            candidate_w,
            candidate_h,
            population,
            prefer_cuda=False,
        )
        cpu_times.append((time.perf_counter() - started) * 1000.0)

        started = time.perf_counter()
        loop_result = screen_seed_population_loop_baseline(
            fixed,
            fixed_w,
            fixed_h,
            candidate,
            candidate_w,
            candidate_h,
            population,
            prefer_cuda=True,
        )
        loop_times.append((time.perf_counter() - started) * 1000.0)

        started = time.perf_counter()
        oneshot = screen_seed_population(
            fixed,
            fixed_w,
            fixed_h,
            candidate,
            candidate_w,
            candidate_h,
            population,
            prefer_cuda=True,
        )
        oneshot_times.append((time.perf_counter() - started) * 1000.0)

        cpu_rejected = cpu_result.get("rejected_per_seed") or []
        loop_rejected = loop_result.get("rejected_per_seed") or []
        oneshot_rejected = oneshot.get("rejected_per_seed") or []
        parity_ok = (
            parity_ok
            and cpu_rejected == loop_rejected
            and cpu_rejected == oneshot_rejected
        )
        cuda_active = cuda_active or bool(oneshot.get("cuda_active"))
        safe_rejected = int((oneshot.get("stats") or {}).get("safe_rejected") or 0)
        oneshot_stats = dict(oneshot.get("stats") or {})
        loop_stats = dict(loop_result.get("stats") or {})

    cpu_ms = _median(cpu_times)
    loop_ms = _median(loop_times)
    oneshot_ms = _median(oneshot_times)
    speedup_vs_cpu = cpu_ms / oneshot_ms if oneshot_ms > 0 else None
    speedup_vs_loop = loop_ms / oneshot_ms if oneshot_ms > 0 else None
    transfer_share = None
    if oneshot_stats:
        compute = (
            float(oneshot_stats.get("h2d_ms") or 0.0)
            + float(oneshot_stats.get("kernel_ms") or 0.0)
            + float(oneshot_stats.get("d2h_ms") or 0.0)
        )
        transfer = float(oneshot_stats.get("h2d_ms") or 0.0) + float(
            oneshot_stats.get("d2h_ms") or 0.0
        )
        transfer_share = (transfer / compute) if compute > 0 else None

    return {
        "schema": "arga_cuda_seed_population_ab_v2",
        "population_size": len(population),
        "candidates_per_seed": len(population[0]) if population else 0,
        "total_candidates": sum(len(batch) for batch in population),
        "repeats": max(1, repeats),
        "safe_rejected": safe_rejected,
        "cpu_chatty_median_ms": cpu_ms,
        "cuda_session_loop_median_ms": loop_ms,
        "cuda_screen_population_median_ms": oneshot_ms,
        "speedup_oneshot_vs_cpu": speedup_vs_cpu,
        "speedup_oneshot_vs_session_loop": speedup_vs_loop,
        "parity_ok": parity_ok,
        "cuda_active": cuda_active,
        "oneshot_stats": oneshot_stats,
        "loop_stats": loop_stats,
        "scope": (
            "cribado raster de población; los supervivientes requieren "
            "validación Clipper2 y nesting exacto"
        ),
        "gate": {
            "parity_ok": parity_ok,
            "cuda_active": cuda_active,
            "screening_speedup_ge_1_2x": bool(
                speedup_vs_cpu and speedup_vs_cpu >= 1.2
            ),
            "transfer_share_under_0_5": bool(
                transfer_share is not None and transfer_share < 0.5
            ),
            "production_ready": False,
            "next_requirement": (
                "integrar en piloto de semillas solo si reduce el tiempo "
                "total del motor piloto end-to-end"
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A/B CUDA para población de semillas")
    parser.add_argument("--seeds", type=int, default=16)
    parser.add_argument("--candidates-per-seed", type=int, default=2_000)
    parser.add_argument("--repeats", type=int, default=11)
    parser.add_argument(
        "--out",
        default="benchmarks/results_real/cuda_seed_population_rtx4050.json",
    )
    args = parser.parse_args(argv)
    report = benchmark(
        seeds=args.seeds,
        candidates_per_seed=args.candidates_per_seed,
        repeats=args.repeats,
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    gate = report["gate"]
    return 0 if gate["parity_ok"] and gate["cuda_active"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
