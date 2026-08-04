"""A/B del filtro raster conservador CPU vs CUDA.

No mide nesting completo: valida el hot path de una rejilla BLF con máscaras
interiores. Todo candidato no rechazado debe pasar después por Clipper2.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from modules.nesting_engine.algorithm_bridge_v2 import (
    cuda_raster_filter_available,
    cuda_raster_safe_reject_batch,
)


def _median(values: list[float]) -> float:
    return float(statistics.median(values))


def _make_workload() -> tuple[list[int], int, int, list[int], int, int, list[tuple[int, int]]]:
    """Máscaras interiores sintéticas con candidatos BLF a una rejilla de 5 mm."""
    fixed_w, fixed_h = 300, 240
    fixed = [0] * (fixed_w * fixed_h)
    # Islas llenas: cada celda marcada está totalmente en metal fijo.
    for left, top, width, height in (
        (18, 20, 70, 45),
        (115, 30, 55, 90),
        (210, 15, 60, 60),
        (35, 145, 130, 50),
        (205, 150, 75, 65),
    ):
        for y in range(top, top + height):
            for x in range(left, left + width):
                fixed[y * fixed_w + x] = 1

    candidate_w, candidate_h = 17, 13
    candidate = [1] * (candidate_w * candidate_h)
    offsets = [
        (x, y)
        for y in range(0, fixed_h - candidate_h + 1, 2)
        for x in range(0, fixed_w - candidate_w + 1, 2)
    ]
    return fixed, fixed_w, fixed_h, candidate, candidate_w, candidate_h, offsets


def _run(prefer_cuda: bool, repeats: int) -> tuple[dict, list[float]]:
    fixed, fixed_w, fixed_h, candidate, candidate_w, candidate_h, offsets = _make_workload()
    elapsed: list[float] = []
    response: dict = {}
    for _ in range(repeats):
        started = time.perf_counter()
        response = cuda_raster_safe_reject_batch(
            fixed,
            fixed_w,
            fixed_h,
            candidate,
            candidate_w,
            candidate_h,
            offsets,
            prefer_cuda=prefer_cuda,
        )
        elapsed.append((time.perf_counter() - started) * 1000.0)
    return response, elapsed


def benchmark(repeats: int) -> dict:
    cpu_response, cpu_elapsed = _run(prefer_cuda=False, repeats=repeats)
    cuda_available = cuda_raster_filter_available()
    gpu_response, gpu_elapsed = _run(prefer_cuda=True, repeats=repeats)

    if list(cpu_response.get("rejected") or []) != list(gpu_response.get("rejected") or []):
        raise RuntimeError("Fallo fail-closed: CUDA y CPU discrepan en rechazos seguros.")

    cpu_ms = _median(cpu_elapsed)
    gpu_ms = _median(gpu_elapsed)
    stats = dict(gpu_response.get("stats") or {})
    return {
        "schema": "arga_cuda_raster_ab_v1",
        "repeats": repeats,
        "cuda_available": cuda_available,
        "candidate_count": int(stats.get("candidates_evaluated") or 0),
        "safe_rejected": int(stats.get("safe_rejected") or 0),
        "parity_ok": True,
        "cpu_median_ms": cpu_ms,
        "cuda_median_ms": gpu_ms,
        "speedup": (cpu_ms / gpu_ms) if gpu_ms > 0 else None,
        "cuda_stats": stats,
        "gate": {
            "zero_geometry_regression": True,
            "transfer_share": (
                (float(stats.get("h2d_ms") or 0.0) + float(stats.get("d2h_ms") or 0.0))
                / gpu_ms
                if stats.get("cuda_used") and gpu_ms > 0
                else None
            ),
            "requires_end_to_end_real_corpus": True,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A/B CPU vs CUDA raster filter")
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    report = benchmark(max(1, int(args.repeats)))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        print(output)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
