"""Candado: barra vertical Amada/CyPTube exporta MARK sin barrenos."""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ezdxf  # noqa: E402

from modules.dxf_export.cobre_nest import export_cobre_hoja_to_dxf  # noqa: E402


def test_amada_vertical_barra_exporta_mark_sin_barrenos() -> None:
    """Réplica del export en exporter.py (es_cu_especial): sin barrenos, con MARK."""
    w_mm = 5.0 * 25.4
    l_mm = 35.251 * 25.4
    outer = [(0.0, 0.0), (l_mm, 0.0), (l_mm, w_mm), (0.0, w_mm), (0.0, 0.0)]
    mark = [(50.0, 10.0), (200.0, 10.0)]
    hole = [(100.0, 20.0), (120.0, 20.0), (120.0, 40.0), (100.0, 40.0)]
    placement = {
        "part_name": "GENE-ROU-S-102",
        "outer": outer,
        "holes": [hole],
        "marks": [mark],
        "cu_largos_piece": True,
        "cu_bar_w_mm": w_mm,
        "cu_bar_l_mm": l_mm,
        "cu_especial_vertical": True,
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
        "length": 144.0 * 25.4,
        "width": w_mm,
        "Length": 144.0 * 25.4,
        "Width": w_mm,
    }

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "NESTING_0.25_W.O.53_X1-H18.dxf")
        export_cobre_hoja_to_dxf(
            out,
            sheet,
            [placement],
            title="AMADA/VERTICAL | SC0014",
            draw_holes=False,
            draw_marks=True,
            strict=False,
        )
        doc = ezdxf.readfile(out)
        msp = doc.modelspace()
        marks = [
            e for e in msp if str(getattr(e.dxf, "layer", "") or "").upper() == "MARK"
        ]
        inners = [
            e for e in msp if str(getattr(e.dxf, "layer", "") or "").upper() == "CUT_INNER"
        ]
        assert marks, "barra vertical Amada debe exportar MARK para CyPTube"
        assert not inners, "barra vertical Amada no debe exportar barrenos"


def test_amada_vertical_draw_marks_false_purga_mark() -> None:
    """Documenta el bug corregido: draw_marks=False eliminaba MARK al final del export."""
    w_mm = 5.0 * 25.4
    l_mm = 10.0 * 25.4
    outer = [(0.0, 0.0), (l_mm, 0.0), (l_mm, w_mm), (0.0, w_mm)]
    mark = [(20.0, 10.0), (80.0, 10.0)]
    placement = {
        "part_name": "ESP-MARK-PURGE",
        "outer": outer,
        "holes": [],
        "marks": [mark],
        "cu_largos_piece": True,
        "cu_bar_w_mm": w_mm,
        "cu_bar_l_mm": l_mm,
        "cu_especial_vertical": True,
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
        out_bad = os.path.join(tmp, "sin_mark.dxf")
        export_cobre_hoja_to_dxf(
            out_bad,
            sheet,
            [placement],
            draw_holes=False,
            draw_marks=False,
            strict=False,
        )
        doc_bad = ezdxf.readfile(out_bad)
        marks_bad = [
            e
            for e in doc_bad.modelspace()
            if str(getattr(e.dxf, "layer", "") or "").upper() == "MARK"
        ]
        assert not marks_bad, "draw_marks=False debe purgar MARK (comportamiento FIXTURA)"


if __name__ == "__main__":
    test_amada_vertical_barra_exporta_mark_sin_barrenos()
    test_amada_vertical_draw_marks_false_purga_mark()
    print("OK vertical Amada: MARK sí, barrenos no")
