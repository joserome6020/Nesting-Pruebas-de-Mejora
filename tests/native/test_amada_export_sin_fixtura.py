"""Candado: AMADA/FIXTURA — colchón +10\", join cerrado, solo barrenos (sin MARK)."""
from __future__ import annotations

import math
import os
import re
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
    validate_amada_esp_entities_closed,
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
        assert not validate_amada_esp_entities_closed(outers + inners), (
            "CUT_OUTER/CUT_INNER no deben tener LINE/ARC sueltos"
        )
        pad_mm = AMADA_ESP_SOFT_PADDING_IN * MM
        for e in inners:
            assert e.dxftype() in ("CIRCLE", "LWPOLYLINE"), (
                f"barreno debe ser CIRCLE o LWPOLYLINE cerrada, no {e.dxftype()}"
            )
            if e.dxftype() == "LWPOLYLINE":
                assert bool(e.closed), "barreno LWPOLYLINE debe estar cerrado"
                ys = [float(v[1]) for v in e.get_points("xy")]
                assert min(ys) >= pad_mm - 0.5, "barrenos deben quedar en banda superior (+10\")"
            elif e.dxftype() == "CIRCLE":
                assert float(e.dxf.center.y) >= pad_mm - 0.5, (
                    "barrenos deben quedar en banda superior (+10\")"
                )


def _facet_stadium_horizontal(
    cx: float, cy: float, length: float, r: float, *, n: int = 8
) -> list[tuple[float, float]]:
    """Cápsula horizontal facetada para candado (debe reconstruirse con bulge)."""
    x0 = cx - length / 2.0 + r
    x1 = cx + length / 2.0 - r
    pts: list[tuple[float, float]] = []
    for i in range(n):
        t = i / max(n - 1, 1)
        pts.append((x0 + (x1 - x0) * t, cy - r))
    for i in range(n):
        ang = -math.pi / 2.0 + math.pi * i / max(n - 1, 1)
        pts.append((x1 + r * math.cos(ang), cy + r * math.sin(ang)))
    for i in range(n):
        t = i / max(n - 1, 1)
        pts.append((x1 - (x1 - x0) * t, cy + r))
    for i in range(n):
        ang = math.pi / 2.0 + math.pi * i / max(n - 1, 1)
        pts.append((x0 + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def test_amada_slot_barreno_exporta_lwpolyline_cerrada() -> None:
    w_mm = 5.0 * MM
    l_mm = 20.0 * MM
    outer = [(0.0, 0.0), (l_mm, 0.0), (l_mm, w_mm), (0.0, w_mm)]
    slot = _facet_stadium_horizontal(10.0 * MM, 2.5 * MM, 10.0 * MM, 0.5 * MM)
    outer_p, holes_p, len_out, alto_out = build_amada_esp_padded_geometry(outer, [slot])
    placement = {
        "part_name": "GENE-SLOT-TEST",
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
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "TEST_AMADA_SLOT.dxf")
        export_cobre_hoja_to_dxf(
            out,
            sheet,
            [placement],
            title="AMADA/FIXTURA | SLOT",
            draw_holes=True,
            draw_marks=False,
            strict=True,
            force_horizontal=True,
        )
        doc = ezdxf.readfile(out)
        inners = [
            e
            for e in doc.modelspace()
            if str(getattr(e.dxf, "layer", "") or "").upper() == "CUT_INNER"
        ]
        assert len(inners) == 1
        assert inners[0].dxftype() == "LWPOLYLINE"
        assert bool(inners[0].closed)
        pts = list(inners[0].get_points("xyb"))
        assert len(pts) <= 4, f"ranura debe ser 4 vértices+bulge, no {len(pts)} facetas"
        assert any(abs(float(p[2] or 0.0)) > 1e-6 for p in pts), (
            "ranura ovalada requiere bulge (arco real), no segmentos"
        )


def test_amada_barrenos_source_dxf_escala_pulgadas_a_mm() -> None:
    """Processed Files en pulgadas: barrenos clonados deben ir a mm (+ colchón 10\")."""
    w_in = 5.0
    l_in = 28.8744
    hole_r_in = 0.218
    hole_cy_in = 2.5
    hole_cx_in = 10.0
    pad_mm = AMADA_ESP_SOFT_PADDING_IN * MM

    with tempfile.TemporaryDirectory() as tmp:
        src_path = os.path.join(tmp, "GENE-BCU-5-170, CU, QTY 1, Cal 0.25.dxf")
        src = ezdxf.new("R2000")
        src.layers.new("CUT_INNER", dxfattribs={"color": 3})
        src.layers.new("CUT_OUTER", dxfattribs={"color": 1})
        msp = src.modelspace()
        msp.add_lwpolyline(
            [(0, 0), (l_in, 0), (l_in, w_in), (0, w_in)],
            close=True,
            dxfattribs={"layer": "CUT_OUTER"},
        )
        msp.add_circle(
            (hole_cx_in, hole_cy_in),
            hole_r_in,
            dxfattribs={"layer": "CUT_INNER"},
        )
        src.saveas(src_path)

        outer_p, holes_p, len_out, alto_out = build_amada_esp_padded_geometry(
            [(0, 0), (l_in * MM, 0), (l_in * MM, w_in * MM), (0, w_in * MM)],
            [[(hole_cx_in * MM, hole_cy_in * MM)]],
        )
        placement = {
            "part_name": "GENE-BCU-5-170",
            "outer": outer_p,
            "holes": holes_p,
            "marks": [],
            "ruta": src_path,
            "ruta_origen": src_path,
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
        out = os.path.join(tmp, "NESTING_AMADA.dxf")
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
        circles = [
            e
            for e in doc.modelspace()
            if str(getattr(e.dxf, "layer", "") or "").upper() == "CUT_INNER"
            and e.dxftype() == "CIRCLE"
        ]
        assert circles, "debe clonar barreno CIRCLE del DXF fuente"
        c = circles[0]
        r_mm = float(c.dxf.radius)
        assert math.isclose(r_mm, hole_r_in * MM, rel_tol=0.02), (
            f"radio barreno {r_mm:.2f} mm; esperado ~{hole_r_in * MM:.2f} mm"
        )
        assert float(c.dxf.center.y) >= pad_mm - 0.5, (
            "barreno debe quedar en banda superior tras colchón +10\""
        )


def test_cobre_export_limmax_cubre_geometria() -> None:
    """AutoCAD: LIMMAX y VPORT *Active deben cubrir la barra (no A4 / no centro 0,0)."""
    bar_l = 900.0
    bar_w = 50.8
    placement = {
        "part_name": "SOLERA-TEST",
        "outer": [(0.0, 0.0), (bar_l, 0.0), (bar_l, bar_w), (0.0, bar_w)],
        "holes": [],
        "marks": [],
        "cu_largos_piece": True,
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
        "cu_modo_separacion_barra": "con_gap",
        "export_3d_format": "dxf",
        "length": bar_l,
        "width": bar_w,
        "Length": bar_l,
        "Width": bar_w,
        "cu_rtz_activo": False,
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "NESTING_CU_LIM.dxf")
        export_cobre_hoja_to_dxf(
            out,
            sheet,
            [placement],
            title="NESTEOS DE COBRE | TEST",
            draw_holes=False,
            draw_marks=False,
            strict=False,
            force_horizontal=True,
        )
        doc = ezdxf.readfile(out)
        limmax = doc.header.get("$LIMMAX")
        extmax = doc.header.get("$EXTMAX")
        assert limmax is not None and extmax is not None
        assert float(limmax[0]) >= float(extmax[0]) - 1.0, (
            f"$LIMMAX X={limmax[0]} no cubre EXTMAX X={extmax[0]}"
        )
        assert float(limmax[0]) > 500.0, (
            f"$LIMMAX X={limmax[0]} parece A4; barra={bar_l} mm"
        )
        assert str(doc.dxfversion) in ("AC1015", "R2000") or doc.header.get("$ACADVER") in (
            "AC1015",
            "AC1014",
        ), f"cobre debe salir R2000/AC1015, got {doc.dxfversion}/{doc.header.get('$ACADVER')}"
        text = open(out, encoding="utf-8", errors="replace").read()
        assert not re.search(r"1\.\d+\.\d+\s+@\s+20\d\d-", text), (
            "fingerprint ezdxf no debe quedar en DXF de máquina"
        )
        assert "$ACADVER" in text and "AC1015" in text
        # Candado: el assert post-save del export debe haber pasado (misma regla).
        from modules.nest_exporter import _assert_dxf_autocad_safe_on_disk

        _assert_dxf_autocad_safe_on_disk(out)
        meta = [
            e
            for e in doc.modelspace()
            if str(getattr(e.dxf, "layer", "") or "").upper() == "ARGA_META"
        ]
        assert not meta, "DXF cobre de máquina no debe llevar ARGA_META"
        for vp in doc.viewports:
            if str(getattr(vp.dxf, "name", "") or "") == "*Active":
                assert float(vp.dxf.center.x) > 100.0, (
                    f"VPORT center X={vp.dxf.center.x} debe centrar la barra"
                )
                assert float(vp.dxf.height) > bar_w, (
                    f"VPORT height={vp.dxf.height} demasiado baja"
                )
                break
        else:
            raise AssertionError("falta VPORT *Active")


if __name__ == "__main__":
    test_amada_pieza_export_padding_join_sin_marcaje()
    test_amada_slot_barreno_exporta_lwpolyline_cerrada()
    test_amada_barrenos_source_dxf_escala_pulgadas_a_mm()
    test_cobre_export_limmax_cubre_geometria()
    print("[OK] AMADA/FIXTURA colchón +10 join cerrado + barrenos")
