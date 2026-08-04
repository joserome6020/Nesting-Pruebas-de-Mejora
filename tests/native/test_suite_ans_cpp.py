#!/usr/bin/env python
"""Suite de verificación ANS C++ (Fases A–D)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "native" / "python"))


def rect(w, h):
    return [[(0.0, 0.0), (w, 0.0), (w, h), (0.0, h), (0.0, 0.0)]]


def main() -> int:
    from modules.nesting_engine import arga_nest_core_bridge as bridge
    from arga_nest_sdk import ArgaNestSDK

    st = bridge.core_status()
    print("STATUS", json.dumps(st, ensure_ascii=False))
    assert st["module_loaded"], "arga_nest_core missing — rebuild"

    sdk = ArgaNestSDK(use_worker=False)
    print("VERSION", sdk.version())

    base_req = {
        "engine": "svgnest_ultra",
        "profile": "first",
        "plate_w": 400.0,
        "plate_h": 300.0,
        "kerf": 0.2,
        "certify": True,
        "pieces": [
            {"nombre": "A", "area": 5000, "calibre": "1/4", "material": "SS", "rings": rect(100, 50)},
            {"nombre": "B", "area": 4000, "calibre": "1/4", "material": "SS", "rings": rect(80, 50)},
        ],
    }
    r = sdk.pack_sheet(base_req)
    print("PACK", r.get("metrics"), "certify", r.get("certify"))
    assert r.get("certify", {}).get("ok") is True
    assert int(r["metrics"]["placed_count"]) >= 1

    # NFP cache
    from modules.nesting_engine import arga_nest_core as core

    core.nfp_cache_reset()
    rings = rect(100, 50)
    _ = core.nfp_outer_cached(rings, rings, 0.2)
    _ = core.nfp_outer_cached(rings, rings, 0.2)
    stats = core.nfp_cache_stats()
    print("NFP", stats)
    assert int(stats.get("hits", 0)) >= 1

    # Multi-plate
    job = {
        "engine": "svgnest_ultra",
        "profile": "first",
        "kerf": 0.2,
        "plates": [{"id": "P1", "w": 200, "h": 150}, {"id": "P2", "w": 300, "h": 200}],
        "pieces": [
            {"nombre": f"P{i}", "area": 3000, "calibre": "1/4", "material": "SS", "rings": rect(60, 50)}
            for i in range(6)
        ],
    }
    jr = sdk.pack_job(job)
    print("JOB sheets", len(jr.get("sheets") or []), "leftovers", jr.get("leftovers"))
    assert len(jr.get("sheets") or []) >= 1

    # Copper strip
    cu = sdk.pack_cu(
        {
            "strip_length_mm": 1000,
            "strip_width_mm": 100,
            "kerf_mm": 0.2,
            "pieces": [
                {"nombre": "CU1", "length_mm": 200, "width_mm": 40},
                {"nombre": "CU2", "length_mm": 150, "width_mm": 40},
                {"nombre": "CU3", "length_mm": 300, "width_mm": 50},
            ],
        }
    )
    print("CU placed", cu.get("metrics"))
    assert int(cu["metrics"]["placed_count"]) >= 2

    # DXF / STEP ASCII
    dxf = sdk.export_dxf({**base_req, "mark_text": "HELLO-ANS"})
    assert "LWPOLYLINE" in dxf or "LINE" in dxf
    step = sdk.export_step({**base_req, "thickness_mm": 6.0})
    assert "ISO-10303-21" in step
    print("DXF bytes", len(dxf), "STEP bytes", len(step))

    # CUDA status
    cuda = sdk.cuda_status()
    print("CUDA", cuda)
    assert "build_has_cuda" in cuda

    # STEP OCCT upgrade (si OCP disponible)
    try:
        out_step = ROOT / "_logs" / "ans_cpp_occt_test.step"
        out_step.parent.mkdir(parents=True, exist_ok=True)
        path_txt = sdk.export_step(
            {**base_req, "thickness_mm": 6.0},
            prefer_occt=True,
            out_path=str(out_step),
        )
        assert out_step.is_file() and out_step.stat().st_size > 100
        print("OCCT STEP OK", out_step, out_step.stat().st_size, "chars_preview", len(path_txt))
    except Exception as ex:
        print("OCCT STEP SKIP/FAIL:", ex)
        raise

    # Auto-update stub
    sys.path.insert(0, str(ROOT / "native" / "python"))
    from auto_update import check_for_update, apply_update_instructions

    info = check_for_update()
    print("UPDATE", info)
    assert "signtool" in apply_update_instructions() or "Auto-update" in apply_update_instructions()

    # Worker IPC
    worker = ROOT / "native" / "bin" / "ArgaNestWorker.exe"
    if worker.is_file():
        wsdk = ArgaNestSDK(use_worker=True, worker_exe=str(worker))
        print("WORKER", wsdk.version())
        wr = wsdk.pack_sheet(base_req)
        assert int(wr["metrics"]["placed_count"]) >= 1
        wsdk.close()
        print("WORKER IPC PASS")
    else:
        print("WORKER SKIP (exe missing)")

    # Bridge env hook smoke
    os.environ["ARGA_NEST_CORE"] = "1"
    assert bridge.core_enabled()

    print("SUITE PASS — ANS C++ depth pending closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
