#!/usr/bin/env python
"""Candado: piezas expulsadas por kerf vuelven al pool CON poly."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from shapely.geometry import box

    from modules.nesting_engine.manager import _piezas_expulsadas_a_pool

    poly = box(100, 50, 200, 150)
    colocada = {
        "nombre": "FPP-PELSUE",
        "poligonos": [list(poly.exterior.coords)],
        "marcas": [],
        "area": float(poly.area),
        "debug_id": "x::FPP::rep1",
        # Sin clave 'poly' — así salían tras pop de la hoja.
    }
    pool = _piezas_expulsadas_a_pool([colocada])
    assert len(pool) == 1, pool
    assert pool[0].get("poly") is not None
    assert not pool[0]["poly"].is_empty
    assert abs(pool[0]["poly"].bounds[0]) < 1e-6
    assert abs(pool[0]["poly"].bounds[1]) < 1e-6
    assert pool[0]["nombre"] == "FPP-PELSUE"
    print("EXPULSADAS_A_POOL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
