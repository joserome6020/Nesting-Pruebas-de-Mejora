"""Candado: sin_gap escalón → guillotinas CyPTube + contorno sin caras de stock."""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ezdxf  # noqa: E402

from modules.dxf_export.cobre_nest import export_cobre_hoja_to_dxf  # noqa: E402
from modules.nesting_engine.cu_largos_nesting import (  # noqa: E402
    _solo_cortes_guillotina_vertical,
)

MM = 25.4
TOL = 0.5


def _perfil_escalon_bck() -> list[tuple[float, float]]:
    l_mm = 26.104 * MM
    h_full = 5.635 * MM
    h_thin = 4.2 * MM
    x_step = 2.5 * MM
    return [
        (0.0, 0.0),
        (l_mm, 0.0),
        (l_mm, h_full),
        (x_step, h_full),
        (0.0, h_thin),
        (0.0, 0.0),
    ]


def _write_source_dxf(path: str, outer: list[tuple[float, float]]) -> None:
    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 1  # pulgadas (como DXF procesado de producción)
    msp = doc.modelspace()
    pts = [(float(x) / MM, float(y) / MM) for x, y in outer]
    msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "CUT_OUTER"})
    doc.saveas(path)


def _line_pts(ent) -> list[tuple[float, float]]:
    if ent.dxftype() != "LINE":
        return []
    return [
        (float(ent.dxf.start.x), float(ent.dxf.start.y)),
        (float(ent.dxf.end.x), float(ent.dxf.end.y)),
    ]


def _is_horizontal(p1, p2, tol: float = TOL) -> bool:
    return abs(p1[1] - p2[1]) <= tol


def _is_vertical(p1, p2, tol: float = TOL) -> bool:
    return abs(p1[0] - p2[0]) <= tol


def test_escalon_sin_gap_guillotinas_y_sin_cara_stock() -> None:
    outer = _perfil_escalon_bck()
    assert not _solo_cortes_guillotina_vertical(outer)
    piece_len = outer[1][0]
    bar_w = 6.0 * MM
    bar_l = 144.0 * MM

    placements: list[dict] = []
    x0 = 10.0
    for i in range(3):
        ox = x0 + i * piece_len
        shifted = [(ox + x, y) for x, y in outer]
        placements.append(
            {
                "part_name": f"ABB-22-BCK-106_{i + 1}",
                "outer": shifted,
                "holes": [],
                "marks": [],
                "cu_largos_piece": True,
                "cu_bar_w_mm": bar_w,
                "cu_bar_l_mm": bar_l,
                "cu_slice_idx": i,
                "cu_slice_count": 3,
                "orig_minx": 0.0,
                "orig_miny": 0.0,
                "shift_x": 0.0,
                "shift_y": 0.0,
                "rot_deg": 0.0,
                "rot_origin_cx": 0.0,
                "rot_origin_cy": 0.0,
            }
        )
        placements.append(
            {
                "part_name": f"CU_CORTE__SUP__ABB-22-BCK-106_{i + 1}",
                "outer": [(ox, max(p[1] for p in shifted)), (ox + piece_len, max(p[1] for p in shifted))],
                "holes": [],
                "marks": [],
                "layer_override": "CUT_OUTER",
                "closed": False,
            }
        )
        if i > 0:
            placements.append(
                {
                    "part_name": f"CU_CORTE__V__{i}",
                    "outer": [(ox, 0.0), (ox, bar_w)],
                    "holes": [],
                    "marks": [],
                    "layer_override": "CUT_OUTER",
                    "closed": False,
                }
            )

    placements.append(
        {
            "part_name": "CU_CORTE__V__3",
            "outer": [(x0 + 3 * piece_len, 0.0), (x0 + 3 * piece_len, bar_w)],
            "holes": [],
            "marks": [],
            "layer_override": "CUT_OUTER",
            "closed": False,
        }
    )

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "ABB-22-BCK-106.dxf")
        _write_source_dxf(src, outer)
        for p in placements:
            if p.get("cu_largos_piece"):
                p["ruta"] = src
                p["prefer_source_dxf"] = True

        out = os.path.join(tmp, "nest_escalon_vertical.dxf")
        export_cobre_hoja_to_dxf(
            out,
            {
                "modo_largos_cu": True,
                "cu_modo_separacion_barra": "sin_gap",
                "cu_export_vertical": True,
                "export_3d_format": "dxf",
                "length": bar_l,
                "width": bar_w,
                "Length": bar_l,
                "Width": bar_w,
            },
            placements,
            draw_holes=False,
            draw_marks=True,
            strict=False,
        )

        doc = ezdxf.readfile(out)
        lines = [
            e
            for e in doc.modelspace()
            if e.dxftype() == "LINE"
            and str(getattr(e.dxf, "layer", "") or "").upper() == "CUT_OUTER"
        ]
        assert lines, "debe exportar CUT_OUTER"

        guillotinas = [
            ent
            for ent in lines
            if _is_horizontal(*_line_pts(ent))
            and abs(_line_pts(ent)[0][1] - _line_pts(ent)[1][1]) <= TOL
            and abs(abs(_line_pts(ent)[0][0] - _line_pts(ent)[1][0]) - bar_w) <= 2.0
        ]
        assert len(guillotinas) >= 3, (
            "debe exportar guillotinas a ancho completo entre piezas y al final"
        )

        fin_y = x0 + 3 * piece_len
        fin_guill = [
            ent
            for ent in guillotinas
            if abs(_line_pts(ent)[0][1] - fin_y) <= 2.0
        ]
        assert fin_guill, "falta guillotina al final de la última pieza"

        for ent in lines:
            p1, p2 = _line_pts(ent)
            if _is_vertical(p1, p2):
                x = (p1[0] + p2[0]) / 2.0
                if x >= bar_w - 1.0:
                    raise AssertionError(
                        "no exportar arista en la cara inferior/ancho del stock (x=ancho barra)"
                    )

        pts: list[tuple[float, float]] = []
        for ent in lines:
            pts.extend(_line_pts(ent))
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)
        assert span_y > span_x * 1.5, "sin_gap debe exportar barra en vertical (largo en Y)"
        assert span_x <= bar_w + 2.0, "ancho exportado no debe exceder barra de 6\""


def test_sin_gap_rtz_omite_guillotina_en_gap() -> None:
    """Con RTZCU activo: solo cierre al fin de última pieza madre, no en el gap."""
    outer = _perfil_escalon_bck()
    piece_len = outer[1][0]
    bar_w = 6.0 * MM
    bar_l = 144.0 * MM
    x0 = 10.0
    fin_madre = x0 + 3 * piece_len
    gap = 0.375 * MM
    rtz_inicio = fin_madre + gap

    placements: list[dict] = []
    for i in range(3):
        ox = x0 + i * piece_len
        shifted = [(ox + x, y) for x, y in outer]
        placements.append(
            {
                "part_name": f"ABB-22-BCK-106_{i + 1}",
                "outer": shifted,
                "holes": [],
                "marks": [],
                "cu_largos_piece": True,
                "cu_bar_w_mm": bar_w,
                "cu_bar_l_mm": bar_l,
                "cu_slice_idx": i,
                "cu_slice_count": 3,
                "orig_minx": 0.0,
                "orig_miny": 0.0,
                "shift_x": 0.0,
                "shift_y": 0.0,
                "rot_deg": 0.0,
                "rot_origin_cx": 0.0,
                "rot_origin_cy": 0.0,
            }
        )
        if i > 0:
            placements.append(
                {
                    "part_name": f"CU_CORTE__V__{i}",
                    "outer": [(ox, 0.0), (ox, bar_w)],
                    "holes": [],
                    "marks": [],
                    "layer_override": "CUT_OUTER",
                    "closed": False,
                }
            )

    placements.append(
        {
            "part_name": "CU_CORTE__V__3",
            "outer": [(fin_madre, 0.0), (fin_madre, bar_w)],
            "holes": [],
            "marks": [],
            "layer_override": "CUT_OUTER",
            "closed": False,
        }
    )
    placements.append(
        {
            "part_name": "CU_CORTE__V__4",
            "outer": [(rtz_inicio, 0.0), (rtz_inicio, bar_w)],
            "holes": [],
            "marks": [],
            "layer_override": "CUT_OUTER",
            "closed": False,
        }
    )
    placements.append(
        {
            "part_name": "CU_CORTE__V__5",
            "outer": [(bar_l - 5.0, 0.0), (bar_l - 5.0, bar_w)],
            "holes": [],
            "marks": [],
            "layer_override": "CUT_OUTER",
            "closed": False,
        }
    )

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "ABB-22-BCK-106.dxf")
        _write_source_dxf(src, outer)
        for p in placements:
            if p.get("cu_largos_piece"):
                p["ruta"] = src
                p["prefer_source_dxf"] = True

        out = os.path.join(tmp, "nest_rtz_gap.dxf")
        export_cobre_hoja_to_dxf(
            out,
            {
                "modo_largos_cu": True,
                "cu_modo_separacion_barra": "sin_gap",
                "cu_export_vertical": True,
                "export_3d_format": "dxf",
                "cu_rtz_activo": True,
                "cu_fin_piezas_mm": fin_madre,
                "cu_rtz_inicio_mm": rtz_inicio,
                "length": rtz_inicio,
                "width": bar_w,
                "Length": rtz_inicio,
                "Width": bar_w,
            },
            placements,
            draw_holes=False,
            draw_marks=True,
            strict=False,
        )

        doc = ezdxf.readfile(out)
        guillotinas = [
            e
            for e in doc.modelspace()
            if e.dxftype() == "LINE"
            and str(getattr(e.dxf, "layer", "") or "").upper() == "CUT_OUTER"
            and _is_horizontal(*_line_pts(e))
            and abs(abs(_line_pts(e)[0][0] - _line_pts(e)[1][0]) - bar_w) <= 2.0
        ]
        ys = sorted({(_line_pts(g)[0][1] + _line_pts(g)[1][1]) / 2.0 for g in guillotinas})
        assert any(abs(y - fin_madre) <= 2.0 for y in ys), "debe haber cierre al fin de madre"
        assert not any(abs(y - rtz_inicio) <= 2.0 for y in ys), (
            "no debe exportar guillotina en el gap RTZCU"
        )
        assert not any(abs(y - (bar_l - 5.0)) <= 2.0 for y in ys), (
            "no debe exportar guillotina al final de barra RTZ"
        )


if __name__ == "__main__":
    test_escalon_sin_gap_guillotinas_y_sin_cara_stock()
    test_sin_gap_rtz_omite_guillotina_en_gap()
    print("OK sin_gap escalón: guillotinas + sin cara stock")
