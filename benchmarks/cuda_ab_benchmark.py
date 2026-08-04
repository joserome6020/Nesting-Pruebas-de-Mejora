"""A/B end-to-end del packer v2 con y sin filtro CUDA."""
from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path
from typing import Any

from benchmarks.runner import run_scenario


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _run(scenario: str, repeats: int, *, use_cuda: bool) -> list[dict[str, Any]]:
    previous = os.environ.get("ARGA_CPP_V2_CUDA_RASTER")
    try:
        if use_cuda:
            os.environ["ARGA_CPP_V2_CUDA_RASTER"] = "1"
        else:
            os.environ.pop("ARGA_CPP_V2_CUDA_RASTER", None)
        return [run_scenario(scenario, "cpp_v2_poc") for _ in range(repeats)]
    finally:
        if previous is None:
            os.environ.pop("ARGA_CPP_V2_CUDA_RASTER", None)
        else:
            os.environ["ARGA_CPP_V2_CUDA_RASTER"] = previous


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    elapsed = [float(row.get("elapsed_ms") or 0.0) for row in rows]
    efi = [float(row.get("efi_directa") or 0.0) for row in rows]
    placed = [int(row.get("placed") or 0) for row in rows]
    raster = [dict(row.get("cuda_raster") or {}) for row in rows]
    timings = [dict(row.get("packer_timing") or {}) for row in rows]
    timing_keys = (
        "candidate_count",
        "candidate_generation_ms",
        "exact_collision_ms",
        "rasterization_ms",
    )
    return {
        "median_elapsed_ms": _median(elapsed),
        "median_efi_directa": _median(efi),
        "median_placed": _median([float(value) for value in placed]),
        "geometry_ok_all_runs": all(
            bool(row.get("solape_ok"))
            and int(row.get("kerf_violations") or 0) == 0
            and not row.get("error")
            for row in rows
        ),
        "cuda_used_all_runs": bool(raster) and all(
            bool(item.get("cuda_used")) for item in raster
        ),
        "cuda_raster": raster,
        "packer_timing": {
            key: _median([float(item.get(key) or 0.0) for item in timings])
            for key in timing_keys
        },
    }


def benchmark(scenario: str, repeats: int) -> dict[str, Any]:
    cpu_rows = _run(scenario, repeats, use_cuda=False)
    cuda_rows = _run(scenario, repeats, use_cuda=True)
    cpu = _summary(cpu_rows)
    cuda = _summary(cuda_rows)
    cpu_ms = float(cpu["median_elapsed_ms"] or 0.0)
    cuda_ms = float(cuda["median_elapsed_ms"] or 0.0)
    transfer_share = None
    if cuda.get("cuda_raster"):
        values = []
        for item in cuda["cuda_raster"]:
            total = float(item.get("h2d_ms") or 0.0) + float(item.get("d2h_ms") or 0.0)
            elapsed = cuda_ms
            if elapsed > 0:
                values.append(total / elapsed)
        transfer_share = _median(values)
    same_quality = (
        cpu["geometry_ok_all_runs"]
        and cuda["geometry_ok_all_runs"]
        and cpu["median_placed"] == cuda["median_placed"]
        and cpu["median_efi_directa"] == cuda["median_efi_directa"]
    )
    speedup = (cpu_ms / cuda_ms) if cuda_ms > 0 else None
    return {
        "schema": "arga_cuda_end_to_end_ab_v1",
        "scenario": scenario,
        "repeats": repeats,
        "cpu": cpu,
        "cuda": cuda,
        "quality_parity": same_quality,
        "speedup": speedup,
        "transfer_share": transfer_share,
        "gate": {
            "cuda_active": cuda["cuda_used_all_runs"],
            "zero_geometry_regression": same_quality,
            "speedup_minimum_1_5x": bool(speedup and speedup >= 1.5),
            "transfer_under_20pct": (
                transfer_share is not None and transfer_share < 0.2
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A/B CUDA end-to-end para cpp_v2")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--out", default="benchmarks/results_real/cuda_end_to_end.json")
    args = parser.parse_args(argv)
    report = benchmark(args.scenario, max(1, int(args.repeats)))
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    gate = report["gate"]
    return 0 if all(gate.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
