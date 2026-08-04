#!/usr/bin/env python
"""Ola de paridad CAM: common-cut DXF, kerf_mm, remnants CSV, tabu, certify DXF."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def rect(w, h, x=0.0, y=0.0):
    return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]]


def main() -> int:
    os.environ.setdefault("ARGA_NEST_CORE", "1")
    # Forzar legacy solo en el bloque TABU para comparar features.kerf_mm_contract=False
    os.environ["ARGA_NEST_KERF_CONTRACT"] = "legacy"

    from modules.nesting_engine import arga_nest_core as core
    from modules.nesting_engine import arga_nest_core_bridge as bridge
    from modules.nesting_engine import remnants_inventory as rem

    st = bridge.core_status()
    print("STATUS", st)
    assert st.get("module_loaded"), st
    assert str(core.ABI_VERSION).startswith("1.4"), core.ABI_VERSION
    ver = core.version_string()
    assert "0.5" in ver or ver.startswith("ArgaNestCore 0.5"), ver

    pieces = [
        {"nombre": "A", "area": 5000, "rings": rect(100, 50)},
        {"nombre": "B", "area": 4800, "rings": rect(100, 48)},
        {"nombre": "C", "area": 3000, "rings": rect(60, 50)},
    ]

    # --- Tabu + features ---
    req = bridge.prepare_pack_request(
        plate_w=400,
        plate_h=300,
        pieces=pieces,
        kerf=0.2,
        profile="fast",
        ga_population=6,
        ga_generations=4,
        enable_tabu=True,
        tabu_seed_trials=3,
    )
    r = bridge.pack_sheet_json(req)
    print("TABU", r.get("features"), r.get("metrics"))
    assert r.get("certify", {}).get("ok") is True
    assert (r.get("features") or {}).get("tabu") is True
    assert int(r["metrics"]["placed_count"]) >= 2

    # --- kerf_mm contract (identity) ---
    os.environ["ARGA_NEST_KERF_CONTRACT"] = "identity"
    req2 = bridge.apply_kerf_contract({"kerf": 0.2, "plate_w": 200, "plate_h": 200, "pieces": pieces[:1]})
    assert abs(float(req2["kerf_mm"]) - 0.2) < 1e-9
    r2 = bridge.pack_sheet_json(
        {
            **req2,
            "engine": "svgnest_ultra",
            "profile": "first",
            "certify": True,
            "enable_tabu": False,
        }
    )
    assert r2.get("features", {}).get("kerf_mm_contract") is True
    assert abs(float(r2.get("kerf_used", -1)) - 0.2) < 1e-6
    os.environ.pop("ARGA_NEST_KERF_CONTRACT", None)
    print("KERF_MM ok", r2.get("kerf_used"))

    # --- Remnants inventory ---
    plates = rem.load_remnant_plates(ROOT / "inventario_remanentes.csv", max_plates=8)
    print("REM_SAMPLE", plates[:2] if plates else None)
    assert plates, "inventario_remanentes.csv vacio o no parseable"
    assert plates[0]["is_remnant"] is True
    assert plates[0]["w"] > 10 and plates[0]["h"] > 10
    job = {
        "engine": "svgnest_ultra",
        "profile": "first",
        "kerf": 0.2,
        "prefer_remnants": True,
        "plates": plates[:4]
        + [{"id": "NEW", "w": 3000, "h": 1500, "cost": 99999, "is_remnant": False}],
        "pieces": [{"nombre": f"P{i}", "area": 2000, "rings": rect(50, 40)} for i in range(3)],
    }
    job_r = bridge.pack_job_json(job)
    print("JOB", [(s.get("plate") or {}).get("id") for s in job_r.get("sheets") or []])
    assert job_r.get("sheets"), job_r
    first_id = (job_r["sheets"][0].get("plate") or {}).get("id", "")
    assert first_id.startswith("PL-") or first_id == "REM" or "is_remnant" in (
        job_r["sheets"][0].get("plate") or {}
    )

    # --- Common-cut DXF + post-export certify ---
    dxf = bridge.export_dxf_json(
        {
            "engine": "svgnest_ultra",
            "profile": "first",
            "plate_w": 400,
            "plate_h": 300,
            "kerf": 0.2,
            "certify": True,
            "common_cut_layer": True,
            "enable_tabu": False,
            "pieces": pieces,
        }
    )
    assert "COMMON_CUT" in dxf
    assert "CUT_OUTER" in dxf or "LWPOLYLINE" in dxf
    cert = bridge.certify_dxf(dxf)
    print("DXF_CERT", cert)
    assert cert.get("ok") is True
    assert int(cert.get("closed_outers") or 0) >= 1

    print("PARITY_WAVE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
