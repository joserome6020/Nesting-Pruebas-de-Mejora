"""Candado: cobre sin_gap (CyPTube) exporta capa MARK en el DXF."""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ezdxf  # noqa: E402

from modules.dxf_export.cobre_nest import export_cobre_hoja_to_dxf  # noqa: E402


def test_cobre_sin_gap_exporta_mark() -> None:
    w_mm = 4.0 * 25.4
    l_mm = 8.0 * 25.4
    outer = [(0.0, 0.0), (l_mm, 0.0), (l_mm, w_mm), (0.0, w_mm), (0.0, 0.0)]
    mark = [(20.0, 10.0), (80.0, 10.0)]
    placement = {
        "part_name": "SOLERA-TEST",
        "outer": outer,
        "holes": [],
        "marks": [mark],
        "cu_largos_piece": True,
        "cu_bar_w_mm": w_mm,
        "cu_bar_l_mm": l_mm,
        "orig_minx": 0.0,
        "orig_miny": 0.0,
        "shift_x": 0.0,
        "shift_y": 0.0,
        "rot_deg": 0.0,
        "rot_origin_cx": 0.0,
        "rot_origin_cy": 0.0,
    }
    sheet = {
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "sin_gap",
        "export_3d_format": "dxf",
        "length": l_mm,
        "width": w_mm,
        "Length": l_mm,
        "Width": w_mm,
    }

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "cobre_sin_gap_mark.dxf")
        export_cobre_hoja_to_dxf(
            out,
            sheet,
            [placement],
            title="NESTEOS DE COBRE",
            draw_holes=True,
            draw_marks=True,
            strict=True,
        )
        doc = ezdxf.readfile(out)
        marks = [
            e
            for e in doc.modelspace()
            if str(getattr(e.dxf, "layer", "") or "").upper() == "MARK"
        ]
        assert marks, "sin_gap debe exportar entidades en capa MARK"
        assert all(e.dxftype() == "LINE" for e in marks), "MARK debe ser LINE stick"


if __name__ == "__main__":
    test_cobre_sin_gap_exporta_mark()
    print("OK cobre sin_gap exporta MARK")
