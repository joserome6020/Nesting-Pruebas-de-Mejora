#!/usr/bin/env python
"""Smoke: Lite NO debe enrutar a FORCE/base; compact catálogo sigue OK."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    os.environ["ARGA_NEST_COMPACT"] = "1"
    os.environ["ARGA_NEST_VENOM"] = "0"

    from modules.nesting_engine import compact_lite as cl
    from modules.nesting_engine.algorithm_bridge import empaquetar_una_hoja_legacy_mc
    from shapely.geometry import box

    assert cl.compact_enabled() is True
    assert not hasattr(empaquetar_una_hoja_legacy_mc, "_removed")

    # Catálogo pasillo sigue disponible (densify), sin tocar packer FORCE.
    voids = cl._catalog_voids([box(0, 900, 1800, 1000), box(0, 0, 2000, 800)], 0.0)
    assert voids and voids[0]["corridor"] is True

    try:
        from modules.nesting_engine import algorithm_cpp  # noqa: F401

        p = {
            "nombre": "P1",
            "poly": box(0, 0, 100, 80),
            "area": 8000.0,
            "poligonos": [[[0.0, 0.0], [100.0, 0.0], [100.0, 80.0], [0.0, 80.0], [0.0, 0.0]]],
        }
        hoja, _ = empaquetar_una_hoja_legacy_mc(
            [p],
            120 * 25.4,
            48 * 25.4,
            kerf_override=0.15,
            mc_iterations=1,
        )
        assert not hoja.get("lite_void_first"), "Lite no debe marcar void-first/FORCE"
        print("PACK mc_ok void_first=", hoja.get("lite_void_first"))
    except ImportError:
        print("SKIP pack (no algorithm_cpp)")

    print("LITE_MC_NOT_FORCE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
