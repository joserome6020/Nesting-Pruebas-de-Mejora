"""Verifica que exportación láser y cobre no mezclen capas DXF."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf

from modules.nest_exporter import export_nest_to_dxf

COBRE_ONLY = frozenset({"CUT_CU", "BAR_START", "ARGA_META"})
LASER_FORBIDDEN_EMPTY = COBRE_ONLY


def _layer_names(path: str) -> set[str]:
    doc = ezdxf.readfile(path)
    return {str(l.dxf.name) for l in doc.layers}


def test_laser_robot_mini_nest() -> None:
    sheet = {
        "length": 1854.0,
        "width": 914.0,
        "material": "A36",
        "thickness": 0.25,
        "arga_code": "TEST",
    }
    placements = [
        {
            "part_name": "PIEZA-A",
            "outer": [(10, 10), (110, 10), (110, 60), (10, 60)],
            "holes": [[(30, 25), (50, 25), (50, 45), (30, 45)]],
        }
    ]
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "laser.dxf")
        export_nest_to_dxf(
            out,
            sheet,
            placements,
            title="ROBOT LASER + MINI NEST",
            canal="ROBOT LASER + MINI NEST",
        )
        layers = _layer_names(out)
        leaked = layers & LASER_FORBIDDEN_EMPTY
        assert not leaked, f"láser: capas cobre en tabla DXF: {sorted(leaked)}"
        assert "CUT_OUTER" in layers and "CUT_INNER" in layers


def test_cobre_largos_con_gap() -> None:
    sheet = {
        "length": 6000.0,
        "width": 40.0,
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "con_gap",
    }
    placements = [
        {
            "part_name": "CU-1",
            "cu_largos_piece": True,
            "outer": [(0, 0), (100, 0), (100, 40), (0, 40)],
        }
    ]
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "cobre.dxf")
        export_nest_to_dxf(
            out,
            sheet,
            placements,
            title="CU LARGOS",
            modo_largos_cu=True,
            strict=False,
        )
        layers = _layer_names(out)
        assert "CUT_CU" in layers, "cobre: falta CUT_CU"
        assert "BAR_START" in layers, "cobre: falta BAR_START"
        msp = ezdxf.readfile(out).modelspace()
        used = {str(e.dxf.layer) for e in msp}
        assert "CUT_CU" in used, "cobre: sin geometría CUT_CU"


def main() -> int:
    test_laser_robot_mini_nest()
    print("[OK] láser ROBOT LASER + MINI NEST: sin capas cobre fantasma")
    test_cobre_largos_con_gap()
    print("[OK] cobre largos: CUT_CU + BAR_START presentes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
