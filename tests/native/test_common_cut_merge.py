#!/usr/bin/env python
"""Common-line path merge: geometría real + pierce_saved + capa COMMON_CUT."""
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
    from modules.nesting_engine import arga_nest_core as core
    from modules.nesting_engine import arga_nest_core_bridge as bridge

    kerf = 0.2
    # Posiciones YA colocadas (mundo): L | R adyacentes + T arriba de L
    placed = [
        {"nombre": "L", "poligonos": rect(100, 100, 0, 0)},
        {"nombre": "R", "poligonos": rect(100, 100, 100 + kerf, 0)},
        {"nombre": "T", "poligonos": rect(100, 50, 0, 100 + kerf)},
        # Segundo par colineal vertical para forzar merge de paths
        {"nombre": "R2", "poligonos": rect(100, 100, 100 + kerf, 100 + kerf)},
    ]
    ana = core.common_line_analyze(placed, max_gap_mm=0.5, min_length_mm=5.0, join_tol_mm=1.5)
    print("ANALYZE", ana)
    assert int(ana["pair_count"]) >= 2, ana
    assert float(ana["total_shared_mm"]) >= 50.0, ana
    assert int(ana["segments_in"]) >= 2, ana
    assert all(p.get("has_geom") for p in ana["pairs"]), ana
    # Con varios bordes adyacentes, merge debe reducir pierces o al menos reportar paths
    assert int(ana["merged_paths"]) >= 1, ana
    assert int(ana["pierce_saved"]) >= 0, ana

    # Export DXF via pack (smoke) + certify capa
    req = {
        "engine": "svgnest_ultra",
        "profile": "first",
        "plate_w": 400.0,
        "plate_h": 300.0,
        "kerf": kerf,
        "certify": True,
        "enable_tabu": False,
        "pieces": [
            {"nombre": "A", "area": 5000, "rings": rect(80, 60)},
            {"nombre": "B", "area": 5000, "rings": rect(80, 60)},
            {"nombre": "C", "area": 5000, "rings": rect(80, 60)},
        ],
        "common_cut_layer": True,
    }
    dxf = bridge.export_dxf_json(req)
    assert "COMMON_CUT" in dxf
    cert = bridge.certify_dxf(dxf)
    assert cert.get("ok") is True
    print("DXF_CERT", cert)
    print("COMMON_CUT_MERGE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
