"""Benchmark local ArgaNestCore vs baseline (S0/S1 sintético).

Uso:
  python benchmarks/arga_nest_core_bench.py
  python benchmarks/arga_nest_core_bench.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def rect(w: float, h: float):
    return [[(0.0, 0.0), (w, 0.0), (w, h), (0.0, h), (0.0, 0.0)]]


def corpus_s0():
    # Micro: 9 rects en placa chica
    pieces = []
    for i in range(9):
        pieces.append(
            {
                "nombre": f"S0_{i}",
                "area": 40 * 30,
                "calibre": "11",
                "material": "A36",
                "rings": rect(40, 30),
            }
        )
    return {
        "name": "s0_micro",
        "engine": "svgnest_ultra",
        "profile": "first",
        "plate_w": 300.0,
        "plate_h": 200.0,
        "kerf": 0.2,
        "certify": True,
        "pieces": pieces,
    }


def corpus_s1():
    pieces = []
    sizes = [(50, 40), (60, 30), (45, 45), (70, 25), (35, 55)]
    for i in range(33):
        w, h = sizes[i % len(sizes)]
        pieces.append(
            {
                "nombre": f"S1_{i}",
                "area": w * h,
                "calibre": "11",
                "material": "A36",
                "rings": rect(w, h),
            }
        )
    return {
        "name": "s1_single_plate",
        "engine": "svgnest_ultra",
        "profile": "first",
        "plate_w": 1200.0,
        "plate_h": 600.0,
        "kerf": 0.2,
        "certify": True,
        "pieces": pieces,
    }


def run_one(req: dict) -> dict:
    from modules.nesting_engine import arga_nest_core as core

    name = req.pop("name")
    t0 = time.perf_counter()
    raw = core.pack_sheet_json(json.dumps(req))
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    result = json.loads(raw)
    metrics = result.get("metrics") or {}
    certify = result.get("certify") or {}
    expected = len(req["pieces"])
    placed = int(metrics.get("placed_count") or 0)
    return {
        "scenario": name,
        "engine": "arga_nest_core/svgnest_ultra",
        "placed": f"{placed}/{expected}",
        "placed_count": placed,
        "expected": expected,
        "efi": float(metrics.get("eficiencia") or 0.0),
        "solape_ok": bool(certify.get("ok")),
        "kerf_ok": not any(
            (i or {}).get("code") == "kerf" for i in (certify.get("issues") or [])
        ),
        "elapsed_ms": round(elapsed_ms, 2),
        "certify_issues": certify.get("issues") or [],
        "pass": bool(certify.get("ok")) and placed >= 1,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=str, default="")
    args = ap.parse_args()

    from modules.nesting_engine import arga_nest_core_bridge as bridge

    st = bridge.core_status()
    if not st["module_loaded"]:
        print("FAIL: arga_nest_core no cargado")
        return 2

    rows = []
    for builder in (corpus_s0, corpus_s1):
        req = builder()
        row = run_one(req)
        rows.append(row)
        print(
            f"{row['scenario']}: placed={row['placed']} efi={row['efi']:.2f}% "
            f"solape_ok={row['solape_ok']} kerf_ok={row['kerf_ok']} "
            f"ms={row['elapsed_ms']} PASS={row['pass']}"
        )

    cuda = None
    try:
        from modules.nesting_engine import arga_nest_core as core

        cuda = core.cuda_status()
        print("cuda_status:", cuda)
    except Exception as ex:
        print("cuda_status error:", ex)

    out = {"core": st, "cuda": cuda, "rows": rows, "all_pass": all(r["pass"] for r in rows)}
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print("wrote", args.json)

    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
