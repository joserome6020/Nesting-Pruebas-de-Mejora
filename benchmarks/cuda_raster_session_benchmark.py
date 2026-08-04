"""Mide reutilización de memoria GPU para lotes de semillas/poblaciones."""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from benchmarks.cuda_raster_benchmark import _make_workload
from modules.nesting_engine.algorithm_bridge_v2 import (
    create_cuda_raster_session,
    cuda_raster_safe_reject_batch,
)


def _median(values: list[float]) -> float:
    return float(statistics.median(values))


def benchmark(repeats: int) -> dict:
    fixed, fixed_w, fixed_h, candidate, candidate_w, candidate_h, offsets = _make_workload()
    stateless_ms: list[float] = []
    session_ms: list[float] = []
    stateless_response: dict = {}
    session_response: dict = {}

    for _ in range(repeats):
        started = time.perf_counter()
        stateless_response = cuda_raster_safe_reject_batch(
            fixed,
            fixed_w,
            fixed_h,
            candidate,
            candidate_w,
            candidate_h,
            offsets,
            prefer_cuda=True,
        )
        stateless_ms.append((time.perf_counter() - started) * 1000.0)

    session = create_cuda_raster_session(fixed, fixed_w, fixed_h, prefer_cuda=True)
    for _ in range(repeats):
        started = time.perf_counter()
        session_response = session.safe_reject_batch(
            candidate,
            candidate_w,
            candidate_h,
            offsets,
        )
        session_ms.append((time.perf_counter() - started) * 1000.0)

    same_rejections = list(stateless_response.get("rejected") or []) == list(
        session_response.get("rejected") or []
    )
    stateless = dict(stateless_response.get("stats") or {})
    session_stats = dict(session_response.get("stats") or {})
    return {
        "schema": "arga_cuda_raster_session_ab_v1",
        "repeats": repeats,
        "cuda_active": bool(session.cuda_active()),
        "candidate_count": len(offsets),
        "parity_ok": same_rejections,
        "stateless_median_ms": _median(stateless_ms),
        "session_median_ms": _median(session_ms),
        "speedup": _median(stateless_ms) / _median(session_ms)
        if _median(session_ms) > 0
        else None,
        "stateless_stats": stateless,
        "session_stats": session_stats,
        "gate": {
            "cuda_active": bool(session.cuda_active()),
            "parity_ok": same_rejections,
            "session_reuses_fixed_upload": int(session_stats.get("h2d_bytes") or 0)
            < int(stateless.get("h2d_bytes") or 0),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A/B sesión CUDA persistente")
    parser.add_argument("--repeats", type=int, default=31)
    parser.add_argument(
        "--out",
        default="benchmarks/results_real/cuda_raster_session_rtx4050.json",
    )
    args = parser.parse_args(argv)
    report = benchmark(max(1, int(args.repeats)))
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0 if all(report["gate"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
