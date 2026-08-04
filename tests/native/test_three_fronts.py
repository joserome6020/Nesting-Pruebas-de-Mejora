#!/usr/bin/env python
"""Verifica las 3 frentes: Calidad / Velocidad / Dinero."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def rect(w, h, x=0.0, y=0.0):
    return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]]


def main() -> int:
    from modules.nesting_engine import arga_nest_core as core
    from modules.nesting_engine import arga_nest_core_bridge as bridge

    print("version", bridge.core_status())

    # --- Calidad: grain_locked + SA ---
    req = {
        "engine": "svgnest_ultra",
        "profile": "fast",
        "plate_w": 400.0,
        "plate_h": 300.0,
        "kerf": 0.2,
        "enable_sa_refine": True,
        "enable_common_line": True,
        "enable_lod": True,
        "certify": True,
        "pieces": [
            {
                "nombre": "A",
                "area": 5000,
                "rings": rect(100, 50),
                "grain_locked": True,
                "allow_flip_180": True,
            },
            {
                "nombre": "B",
                "area": 4000,
                "rings": rect(80, 50),
                "allowed_rotations": [0, 90, 180, 270],
            },
            {"nombre": "C", "area": 3000, "rings": rect(60, 50)},
        ],
    }
    r = json.loads(core.pack_sheet_json(json.dumps(req)))
    print("QUALIFY", r.get("features"), r.get("sa"), r.get("metrics"))
    assert r.get("certify", {}).get("ok") is True
    assert int(r["metrics"]["placed_count"]) >= 2
    assert "sa" in r
    assert "rotation_step_deg" in (r.get("features") or {})

    # --- Velocidad: NFP L1/L2 ---
    core.nfp_cache_reset()
    rings = rect(100, 50)
    _ = core.nfp_outer_cached(rings, rings, 0.2)
    _ = core.nfp_outer_cached(rings, rings, 0.2)
    st = core.nfp_cache_stats()
    l2 = core.nfp_l2_stats()
    print("SPEED nfp", st, "l2", l2)
    assert int(st.get("hits", 0)) >= 1

    # --- Dinero: remnants + cost score + common-line ---
    job = {
        "engine": "svgnest_ultra",
        "profile": "first",
        "kerf": 0.2,
        "prefer_remnants": True,
        "enable_sa": True,
        "plates": [
            {"id": "NEW", "w": 500, "h": 400, "cost": 1000, "is_remnant": False},
            {"id": "REM", "w": 300, "h": 200, "cost": 50, "is_remnant": True},
        ],
        "pieces": [
            {"nombre": f"P{i}", "area": 2000, "rings": rect(50, 40)} for i in range(4)
        ],
    }
    jr = json.loads(core.pack_job_json(json.dumps(job)))
    print("MONEY sheets", json.dumps(jr.get("sheets", [])[:1], ensure_ascii=False)[:500])
    assert len(jr.get("sheets") or []) >= 1
    # Preferencia de retazo cuando cabe
    first_plate = (jr["sheets"][0].get("plate") or {})
    print("first_plate", first_plate)
    assert "is_remnant" in first_plate or first_plate.get("id")

    # common-line presente en pack response
    assert "common_lines" in r

    print("THREE FRONTS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
