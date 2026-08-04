#!/usr/bin/env python
"""Machine path: CUT_OUTER sin aristas compartidas + COMMON_CUT fusionado."""
from __future__ import annotations

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
    placed = [
        {"nombre": "L", "poligonos": rect(100, 100, 0, 0)},
        {"nombre": "R", "poligonos": rect(100, 100, 100 + kerf, 0)},
        {"nombre": "T", "poligonos": rect(100, 50, 0, 100 + kerf)},
        {"nombre": "R2", "poligonos": rect(100, 100, 100 + kerf, 100 + kerf)},
    ]

    dxf_guide = core.export_machine_dxf(placed, machine_path=False)
    dxf_mach = core.export_machine_dxf(placed, machine_path=True)
    print("GUIDE_LEN", len(dxf_guide), "MACH_LEN", len(dxf_mach))
    assert "COMMON_CUT" in dxf_mach
    assert "CUT_OUTER" in dxf_mach

    cert_g = bridge.certify_dxf(dxf_guide)
    cert_m = bridge.certify_dxf(dxf_mach)
    print("CERT_GUIDE", cert_g)
    print("CERT_MACH", cert_m)
    assert cert_m.get("ok") is True
    assert int(cert_m.get("common_cut_segments") or 0) >= 1
    # Machine path debe abrir outers (omitir shared) o al menos no ser peor
    open_m = int(cert_m.get("open_outer_segments") or 0)
    closed_m = int(cert_m.get("closed_outers") or 0)
    closed_g = int(cert_g.get("closed_outers") or 0)
    print("closed_guide", closed_g, "closed_mach", closed_m, "open_mach", open_m)
    assert open_m >= 1 or closed_m < closed_g, (
        "expected shared edges removed from CUT_OUTER in machine_path mode"
    )
    assert cert_m.get("machine_path") is True or open_m >= 1

    print("MACHINE_PATH PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
