"""Candado SWO-076: export plasma no tumba por margen si el DXF ≠ nest.

Caso planta (W.O. 89 Placa Base, H7 240x72):
- Nest outer maxX=5928.5 mm (pieza 66.125\" compensada, bien colocada)
- DXF Plasma Compensated stale/inflado → CUT maxX=6334.9 mm (+16\")
- Con plasma_offset_mm=0 el validador SALTABA nest↔corte y solo decía
  \"margen placa X max … Renestee\", ocultando la causa real.

Ahora: con offset=0 también se coteja nest↔corte y el mensaje apunta al DXF.
"""
from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import ezdxf

from modules.dxf_export.validate import validate_plasma_piece
from modules.nest_exporter import _export_source_dxf_at_placement, _msp_snapshot
from modules.plasma_dxf_export import _sizes_match_mm

ESCALA = 25.4
SHEET = {
    "length": 6096.0,  # 240\"
    "width": 1828.8,  # 72\"
    "kerf_usado": 0.25,
    "margin_usado": 0.25,
}


def _rect_dxf(path: Path, *, w_in: float, h_in: float) -> None:
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0.0, 0.0), (w_in, 0.0), (w_in, h_in), (0.0, h_in)],
        close=True,
        dxfattribs={"layer": "CUT_OUTER"},
    )
    doc.saveas(str(path))


def _nest_rect(x0: float, y0: float, w_mm: float, h_mm: float):
    return [
        (x0, y0),
        (x0 + w_mm, y0),
        (x0 + w_mm, y0 + h_mm),
        (x0, y0 + h_mm),
        (x0, y0),
    ]


def test_validate_detecta_dxf_inflado_aunque_offset_cero():
    """Reproduce maxX=6334 vs nest 5928: mensaje DXF≠nest, no solo margen placa."""
    nest_w = 66.125 * ESCALA  # 1679.575
    nest_h = 45.754 * ESCALA
    x0, y0 = 4248.9, 6.5
    nest_outer = _nest_rect(x0, y0, nest_w, nest_h)

    # CUT exportado como si el DXF midiera +16\" (caso planta).
    bad_w = nest_w + 16.0 * ESCALA
    cut_max_x = x0 + bad_w
    assert abs(cut_max_x - 6334.9) < 0.6

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    ents = [
        msp.add_line((x0, y0), (x0 + bad_w, y0), dxfattribs={"layer": "CUT_OUTER"}),
        msp.add_line(
            (x0 + bad_w, y0),
            (x0 + bad_w, y0 + nest_h),
            dxfattribs={"layer": "CUT_OUTER"},
        ),
        msp.add_line(
            (x0 + bad_w, y0 + nest_h),
            (x0, y0 + nest_h),
            dxfattribs={"layer": "CUT_OUTER"},
        ),
        msp.add_line((x0, y0 + nest_h), (x0, y0), dxfattribs={"layer": "CUT_OUTER"}),
    ]
    p = {
        "part_name": "W.O. 89 X1__Placa Base_PLASMA",
        "outer": nest_outer,
        "plasma_fuente_ya_compensada": True,
    }
    issues = validate_plasma_piece(p, ents, offset_mm=0.0, sheet=SHEET)
    assert issues, "debía fallar con DXF inflado"
    assert any("DXF fuente ≠ nest" in i for i in issues), issues
    # El diagnóstico de tamaño debe aparecer (no solo el margen de placa).
    assert issues[0].startswith("DXF fuente ≠ nest"), issues[0]


def test_validate_ok_cuando_dxf_empata_nest():
    nest_w = 66.125 * ESCALA
    nest_h = 45.754 * ESCALA
    x0, y0 = 4248.9, 6.5
    nest_outer = _nest_rect(x0, y0, nest_w, nest_h)
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    ents = [
        msp.add_line((x0, y0), (x0 + nest_w, y0), dxfattribs={"layer": "CUT_OUTER"}),
        msp.add_line(
            (x0 + nest_w, y0),
            (x0 + nest_w, y0 + nest_h),
            dxfattribs={"layer": "CUT_OUTER"},
        ),
        msp.add_line(
            (x0 + nest_w, y0 + nest_h),
            (x0, y0 + nest_h),
            dxfattribs={"layer": "CUT_OUTER"},
        ),
        msp.add_line((x0, y0 + nest_h), (x0, y0), dxfattribs={"layer": "CUT_OUTER"}),
    ]
    p = {"part_name": "Placa Base_PLASMA", "outer": nest_outer}
    issues = validate_plasma_piece(p, ents, offset_mm=0.0, sheet=SHEET)
    assert issues == [], issues


def test_sizes_match_helper_rechaza_mas_8_pct():
    nest = (1679.6, 1162.2)
    good = (1679.6, 1162.2)
    bad = (1679.6 + 16 * ESCALA, 1162.2)
    assert _sizes_match_mm(nest, good)
    assert not _sizes_match_mm(nest, bad)


def test_sizes_match_acepta_rotacion_90():
    """BRACE girado en placa: nest (h,w) vs DXF (w,h) no debe tumbar el export."""
    nest_rot = (291.72, 1492.38)
    dxf = (1492.38, 291.72)
    assert _sizes_match_mm(nest_rot, dxf)
    assert _sizes_match_mm(dxf, nest_rot)


def test_export_1to1_con_dxf_bueno_respeta_placa_240():
    """Sanity: DXF correcto colocado en nest no dispara margen placa."""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        dxf = td_path / "placa.dxf"
        _rect_dxf(dxf, w_in=66.125, h_in=45.754)
        nest_w = 66.125 * ESCALA
        nest_h = 45.754 * ESCALA
        x0, y0 = 4248.9, 6.5
        p = {
            "part_name": "Placa Base_PLASMA",
            "ruta": str(dxf),
            "outer": _nest_rect(x0, y0, nest_w, nest_h),
            "shift_x": 0.0,
            "shift_y": 0.0,
            "rot_deg": 0.0,
            "plasma_fuente_ya_compensada": True,
            "plasma_export": True,
        }
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        before = len(_msp_snapshot(msp))
        _export_source_dxf_at_placement(msp, doc, p, draw_marks=False, strict=False)
        new_ents = _msp_snapshot(msp)[before:]
        issues = validate_plasma_piece(p, new_ents, offset_mm=0.0, sheet=SHEET)
        assert issues == [], issues


def test_export_fallback_poligono_si_dxf_sigue_inflado():
    """Placa Base: tras regen el DXF sigue +16\" → exportar nest, no tumbar hoja."""
    from modules.plasma_dxf_export import export_plasma_placement

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        bad = td_path / "Placa Base.dxf"
        # Inflado +16\" en X (mismo caso planta).
        _rect_dxf(bad, w_in=66.125 + 16.0, h_in=42.754)
        nest_w = 66.125 * ESCALA
        nest_h = 45.754 * ESCALA
        x0, y0 = 4248.9, 6.5
        p = {
            "part_name": "W.O. 89 X1__Placa Base_PLASMA",
            "ruta": str(bad),
            "ruta_plasma": str(bad),
            "outer": _nest_rect(x0, y0, nest_w, nest_h),
            "nested_poligonos": [_nest_rect(x0, y0, nest_w, nest_h)],
            "shift_x": x0,
            "shift_y": y0,
            "rot_deg": 0.0,
            "plasma_fuente_ya_compensada": True,
            "plasma_offset_mm": 0.0,
            "plasma_offset_mm_manual": 0.0625 * ESCALA,
            "plasma_export": True,
        }
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        # Placa de referencia para validación de margen.
        msp.add_lwpolyline(
            [(0, 0), (SHEET["length"], 0), (SHEET["length"], SHEET["width"]), (0, SHEET["width"])],
            close=True,
            dxfattribs={"layer": "Plate"},
        )
        ok = export_plasma_placement(
            msp, doc, p, draw_holes=False, draw_marks=False, sheet=SHEET
        )
        assert ok is True, p.get("_plasma_validation_error")
        assert not p.get("_plasma_validation_error"), p.get("_plasma_validation_error")


if __name__ == "__main__":
    test_validate_detecta_dxf_inflado_aunque_offset_cero()
    test_validate_ok_cuando_dxf_empata_nest()
    test_sizes_match_helper_rechaza_mas_8_pct()
    test_sizes_match_acepta_rotacion_90()
    test_export_1to1_con_dxf_bueno_respeta_placa_240()
    test_export_fallback_poligono_si_dxf_sigue_inflado()
    print("OK test_plasma_export_dxf_vs_nest")
