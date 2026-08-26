"""Candado: AMADA/FIXTURA — colchón +10\", join cerrado, solo barrenos (sin MARK)."""
from __future__ import annotations

import math
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ezdxf  # noqa: E402

from modules.dxf_export.amada_fixture import AMADA_FIXTURE_LAYER  # noqa: E402
from modules.dxf_export.amada_esp import (  # noqa: E402
    AMADA_ESP_SOFT_PADDING_IN,
    build_amada_esp_padded_geometry,
)
from modules.dxf_export.cobre_nest import export_cobre_hoja_to_dxf  # noqa: E402

MM = 25.4


def test_amada_pieza_export_padding_join_sin_marcaje() -> None:
    w_mm = 5.0 * MM
    l_mm = 28.8744 * MM
    outer = [(0.0, 0.0), (l_mm, 0.0), (l_mm, w_mm), (0.0, w_mm)]
    hole = [(10.0 * MM, 2.0 * MM), (12.0 * MM, 2.0 * MM), (12.0 * MM, 3.0 * MM), (10.0 * MM, 3.0 * MM)]
    outer_p, holes_p, len_out, alto_out = build_amada_esp_padded_geometry(outer, [hole])
    assert math.isclose(len_out, l_mm, abs_tol=0.5)
    assert math.isclose(alto_out, w_mm + AMADA_ESP_SOFT_PADDING_IN * MM, abs_tol=0.5)

    placement = {
        "part_name": "GENE-BCU-5-170",
        "outer": outer_p,
        "holes": holes_p,
        "marks": [],
        "cu_amada_outer_padded": True,
        "cu_amada_pieza_export": True,
        "cu_largos_piece": True,
        "cu_bar_w_mm": alto_out,
        "cu_bar_l_mm": len_out,
        "cu_especial_vertical": True,
        "omit_cut_cu": True,
        "closed": True,
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
        "cu_export_amada": True,
        "cu_modo_separacion_barra": "con_gap",
        "export_3d_format": "dxf",
        "length": len_out,
        "width": alto_out,
        "Length": len_out,
        "Width": alto_out,
        "cu_rtz_activo": False,
        "cu_rtz_inicio_mm": 0.0,
    }

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "TEST_AMADA_PIEZA.dxf")
        export_cobre_hoja_to_dxf(
            out,
            sheet,
            [placement],
            title="AMADA/FIXTURA | TEST",
            draw_holes=True,
            draw_marks=False,
            strict=True,
            force_horizontal=True,
        )
        doc = ezdxf.readfile(out)
        msp = doc.modelspace()
        fixture = [
            e
            for e in msp
            if str(getattr(e.dxf, "layer", "") or "").upper() == AMADA_FIXTURE_LAYER
        ]
        assert not fixture, "AMADA/FIXTURA no debe incluir geometría de fixtura"
        marks = [e for e in msp if str(getattr(e.dxf, "layer", "") or "").upper() == "MARK"]
        assert not marks, "AMADA/FIXTURA no debe incluir marcaje"
        outers = [
            e for e in msp if str(getattr(e.dxf, "layer", "") or "").upper() == "CUT_OUTER"
        ]
        assert len(outers) == 1, f"CUT_OUTER debe ser 1 LWPOLYLINE join, hay {len(outers)}"
        assert outers[0].dxftype() == "LWPOLYLINE", "CUT_OUTER debe ser LWPOLYLINE cerrado"
        assert bool(outers[0].closed), "CUT_OUTER debe estar cerrado (join)"
        inners = [
            e for e in msp if str(getattr(e.dxf, "layer", "") or "").upper() == "CUT_INNER"
        ]
        assert inners, "AMADA/FIXTURA debe incluir barrenos"
        pad_mm = AMADA_ESP_SOFT_PADDING_IN * MM
        for e in inners:
            if e.dxftype() == "LWPOLYLINE":
                ys = [float(v[1]) for v in e.get_points("xy")]
                assert min(ys) >= pad_mm - 0.5, "barrenos deben quedar en banda superior (+10\")"


if __name__ == "__main__":
    test_amada_pieza_export_padding_join_sin_marcaje()
    print("[OK] AMADA/FIXTURA colchón +10 join + barrenos")
