"""Prueba: sin_gap vertical + cama láser sin Plate/Plate_Text."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf
from ezdxf import bbox as ezb

from modules.nest_exporter import export_nest_to_dxf
from modules.nesting_engine.exporter import RUTA_CAMA_LASER, RUTA_ROBOT_LASER


def _layers(path: str) -> set[str]:
    doc = ezdxf.readfile(path)
    return {str(e.dxf.layer).upper() for e in doc.modelspace()}


def _extents(path: str):
    doc = ezdxf.readfile(path)
    ext = ezb.extents(doc.modelspace())
    w = float(ext.extmax.x - ext.extmin.x)
    h = float(ext.extmax.y - ext.extmin.y)
    return w, h


def test_sin_gap_vertical():
    placements = [
        {
            "part_name": "SOLERA_A",
            "outer": [(10, 5), (110, 5), (110, 25), (10, 25), (10, 5)],
            "holes": [],
            "marks": [],
            "ruta": "",
            "orig_minx": 0.0,
            "orig_miny": 0.0,
            "shift_x": 10.0,
            "shift_y": 5.0,
            "rot_deg": 0.0,
            "cu_largos_piece": True,
        },
        {
            "part_name": "SOLERA_B",
            "outer": [(120, 5), (220, 5), (220, 25), (120, 25), (120, 5)],
            "holes": [],
            "marks": [],
            "ruta": "",
            "orig_minx": 0.0,
            "orig_miny": 0.0,
            "shift_x": 120.0,
            "shift_y": 5.0,
            "rot_deg": 0.0,
            "cu_largos_piece": True,
        },
    ]
    sheet = {
        "length": 300.0,
        "width": 40.0,
        "material": "CU",
        "thickness": "0.25",
        "arga_code": "TEST",
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "sin_gap",
        "export_3d_format": "dxf",
    }
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "sin_gap.dxf")
        export_nest_to_dxf(
            out,
            sheet,
            placements,
            title="TEST",
            canal=RUTA_CAMA_LASER,
            modo_largos_cu=True,
            strict=True,
        )
        layers = _layers(out)
        assert "PLATE" not in layers, layers
        assert "PLATE_TEXT" not in layers, layers
        w, h = _extents(out)
        assert h > w, f"sin_gap debe quedar vertical: {w:.1f}x{h:.1f}"
        doc = ezdxf.readfile(out)
        bar_lines = [
            e
            for e in doc.modelspace()
            if str(e.dxf.layer).upper() == "BAR_START" and e.dxftype() == "LINE"
        ]
        assert len(bar_lines) == 1, "debe haber una sola linea BAR_START"
        ln = bar_lines[0]
        y0 = float(ln.dxf.start.y)
        y1 = float(ln.dxf.end.y)
        xspan = abs(float(ln.dxf.end.x) - float(ln.dxf.start.x))
        assert abs(y0) < 0.05 and abs(y1) < 0.05, f"BAR_START debe estar en y=0: {y0},{y1}"
        assert abs(xspan - 40.0) < 0.5, f"BAR_START debe abarcar ancho de barra: {xspan}"
    print("OK sin_gap vertical sin Plate")


def test_cama_laser_sin_plate_acero():
    placements = [
        {
            "part_name": "PIEZA_1",
            "outer": [(20, 20), (80, 20), (80, 60), (20, 60), (20, 20)],
            "holes": [],
            "marks": [],
            "ruta": "",
            "orig_minx": 0.0,
            "orig_miny": 0.0,
            "shift_x": 20.0,
            "shift_y": 20.0,
            "rot_deg": 0.0,
        },
    ]
    sheet = {
        "length": 200.0,
        "width": 100.0,
        "material": "A36",
        "thickness": "0.25",
        "arga_code": "TEST-H1",
    }
    with tempfile.TemporaryDirectory() as td:
        out_laser = os.path.join(td, "cama.dxf")
        out_robot = os.path.join(td, "robot.dxf")
        export_nest_to_dxf(
            out_laser,
            sheet,
            placements,
            title="TEST",
            canal=RUTA_CAMA_LASER,
            strict=True,
        )
        export_nest_to_dxf(
            out_robot,
            sheet,
            placements,
            title="TEST",
            canal=RUTA_ROBOT_LASER,
            strict=True,
        )
        lay_laser = _layers(out_laser)
        lay_robot = _layers(out_robot)
        assert "PLATE" not in lay_laser, lay_laser
        assert "PLATE_TEXT" not in lay_laser, lay_laser
        assert "PLATE" in lay_robot, lay_robot
        assert "PLATE_TEXT" in lay_robot, lay_robot
    print("OK cama laser sin Plate; robot laser conserva Plate")


if __name__ == "__main__":
    test_sin_gap_vertical()
    test_cama_laser_sin_plate_acero()
    print("Todas las pruebas pasaron.")
