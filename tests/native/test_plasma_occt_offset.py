"""Candado OCCT: OFFSET plasma conserva LINE/ARC/CIRCLE nativos."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import ezdxf

from modules.plasma_compensator import compensate_dxf_for_plasma, compute_plasma_offset_mm
from modules.plasma_occt_offset import occt_available


def test_occt_offset_preserva_primitivas():
    assert occt_available(), "OCP debe estar empaquetado: no hay fallback degradante"
    td = Path(tempfile.mkdtemp())
    src, dst = td / "in.dxf", td / "out.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    # Perfil LINE/ARC (muesca semicircular) y barreno CIRCLE.
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_line((10, 0), (10, 5), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_line((10, 5), (6, 5), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_arc((5, 5), 1.0, 0, 180, dxfattribs={"layer": "CUT_OUTER"})
    msp.add_line((4, 5), (0, 5), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_line((0, 5), (0, 0), dxfattribs={"layer": "CUT_OUTER"})
    msp.add_circle((7.5, 2.5), 0.75, dxfattribs={"layer": "CUT_INNER"})
    doc.saveas(src)

    off = compute_plasma_offset_mm(0.375)
    stats = compensate_dxf_for_plasma(src, dst, offset_mm=off)
    assert stats["backend"] == "occt_BRepOffsetAPI_MakeOffset", stats
    out = ezdxf.readfile(dst).modelspace()
    outer = [e for e in out if str(e.dxf.layer) == "CUT_OUTER"]
    assert any(e.dxftype() == "LINE" for e in outer)
    assert any(e.dxftype() == "ARC" for e in outer)
    circles = [e for e in out if e.dxftype() == "CIRCLE" and str(e.dxf.layer) == "CUT_INNER"]
    assert len(circles) == 1
    assert abs(float(circles[0].dxf.radius) - (0.75 - off / 25.4)) < 1e-7


def test_occt_offset_preserva_bulge_como_arc():
    td = Path(tempfile.mkdtemp())
    src, dst = td / "bulge.dxf", td / "bulge_out.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    # Semicírculo superior (bulge CW = hacia afuera) + base rectangular.
    doc.modelspace().add_lwpolyline(
        [(0, 0, -1.0), (10, 0, 0.0), (10, -5, 0.0), (0, -5, 0.0)],
        close=True,
        dxfattribs={"layer": "CUT_OUTER"},
        format="xyb",
    )
    doc.saveas(src)
    stats = compensate_dxf_for_plasma(src, dst, offset_mm=compute_plasma_offset_mm(0.375))
    assert stats["backend"] == "occt_BRepOffsetAPI_MakeOffset", stats
    out = ezdxf.readfile(dst).modelspace()
    assert any(e.dxftype() == "ARC" for e in out), "el bulge debe salir como ARC nativo"


def test_occt_offset_parking_cw_no_brep_api():
    """Perfil tipo H.V parking (CW + muesca) no debe tumbar con BRep_API."""
    td = Path(tempfile.mkdtemp())
    src, dst = td / "hv_cw.dxf", td / "hv_cw_out.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    layer = {"layer": "CUT_OUTER"}
    w, h, r = 3.25, 4.50, 1.50
    cx, cy = w, h / 2.0
    # Contorno horario (CW) con muesca circular — caso real de fallo OCCT.
    msp.add_line((0.0, 0.0), (0.0, h), dxfattribs=layer)
    msp.add_line((0.0, h), (w, h), dxfattribs=layer)
    msp.add_line((w, h), (cx, cy + r), dxfattribs=layer)
    msp.add_arc((cx, cy), r, 90.0, 270.0, dxfattribs=layer)
    msp.add_line((cx, cy - r), (w, 0.0), dxfattribs=layer)
    msp.add_line((w, 0.0), (0.0, 0.0), dxfattribs=layer)
    doc.saveas(src)
    stats = compensate_dxf_for_plasma(
        src, dst, offset_mm=compute_plasma_offset_mm(0.1046)
    )
    assert stats["backend"] == "occt_BRepOffsetAPI_MakeOffset", stats
    assert int(stats["changed"]) >= 1
    out = list(ezdxf.readfile(dst).modelspace())
    assert any(e.dxftype() == "LINE" for e in out)
    assert any(e.dxftype() == "ARC" for e in out)


def _bbox_capa(msp, capa: str) -> tuple[float, float, float, float]:
    from modules.plasma_occt_offset import ring_from_specs, specs_from_dxf_entities

    ents = [e for e in msp if str(e.dxf.layer) == capa]
    pts = []
    for spec in specs_from_dxf_entities(ents):
        pts.extend(ring_from_specs([spec]))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def test_occt_offset_placa_con_filetes_y_slot_crece_exacto():
    """Réplica de la pieza real: filetes + slot obround, entrada CW.

    Candado del bug que deformaba el perfil (lazos en esquinas y contorno
    abierto con AREA NETA 0.00): el ARC de OCCT se escribía con los ángulos
    del arco complementario.
    """
    from modules.plasma_occt_offset import ring_from_specs, ring_is_simple, specs_from_dxf_entities

    td = Path(tempfile.mkdtemp())
    src, dst = td / "placa.dxf", td / "placa_out.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    out = {"layer": "CUT_OUTER"}
    W, H, R = 33.40, 17.40, 1.00

    # Contorno horario (CW) con filete en las cuatro esquinas.
    msp.add_line((0.0, R), (0.0, H - R), dxfattribs=out)
    msp.add_arc((R, H - R), R, 90.0, 180.0, dxfattribs=out)
    msp.add_line((R, H), (W - R, H), dxfattribs=out)
    msp.add_arc((W - R, H - R), R, 0.0, 90.0, dxfattribs=out)
    msp.add_line((W, H - R), (W, R), dxfattribs=out)
    msp.add_arc((W - R, R), R, 270.0, 360.0, dxfattribs=out)
    msp.add_line((W - R, 0.0), (R, 0.0), dxfattribs=out)
    msp.add_arc((R, R), R, 180.0, 270.0, dxfattribs=out)

    # Slot obround interno (LINE + ARC), como el corte verde del visor.
    sx, sy, sl, sr = 12.0, 8.0, 4.0, 0.75
    inner = {"layer": "CUT_INNER"}
    msp.add_line((sx, sy + sr), (sx + sl, sy + sr), dxfattribs=inner)
    msp.add_arc((sx + sl, sy), sr, 270.0, 90.0, dxfattribs=inner)
    msp.add_line((sx + sl, sy - sr), (sx, sy - sr), dxfattribs=inner)
    msp.add_arc((sx, sy), sr, 90.0, 270.0, dxfattribs=inner)
    doc.saveas(src)

    off_mm = compute_plasma_offset_mm(0.250)
    stats = compensate_dxf_for_plasma(src, dst, offset_mm=off_mm)
    d = off_mm / 25.4
    msp_out = ezdxf.readfile(dst).modelspace()

    x0, y0, x1, y1 = _bbox_capa(msp_out, "CUT_OUTER")
    assert abs((x1 - x0) - (W + 2 * d)) < 1e-6, (x1 - x0, W + 2 * d)
    assert abs((y1 - y0) - (H + 2 * d)) < 1e-6, (y1 - y0, H + 2 * d)
    assert abs(x0 + d) < 1e-6 and abs(y0 + d) < 1e-6

    filetes = [
        e
        for e in msp_out
        if e.dxftype() == "ARC" and str(e.dxf.layer) == "CUT_OUTER"
    ]
    assert len(filetes) == 4, f"deben quedar 4 filetes, hay {len(filetes)}"
    for arc in filetes:
        assert abs(float(arc.dxf.radius) - (R + d)) < 1e-9

    # El slot interno encoge: el metal crece hacia dentro del corte.
    ix0, iy0, ix1, iy1 = _bbox_capa(msp_out, "CUT_INNER")
    assert abs((ix1 - ix0) - (sl + 2 * sr - 2 * d)) < 1e-6
    assert abs((iy1 - iy0) - (2 * sr - 2 * d)) < 1e-6

    # Y nada de lazos: el contorno resultante es simple.
    ring = ring_from_specs(
        specs_from_dxf_entities([e for e in msp_out if str(e.dxf.layer) == "CUT_OUTER"])
    )
    assert ring_is_simple(ring), "el contorno compensado no debe auto-intersectarse"


def _placa_con_hueco(path: Path, hueco: float) -> None:
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    out = {"layer": "CUT_OUTER"}
    W, H = 20.0, 8.0
    msp.add_line((0.0, 0.0), (W, 0.0), dxfattribs=out)
    msp.add_line((W, 0.0), (W, H), dxfattribs=out)
    msp.add_line((W, H), (0.0, H), dxfattribs=out)
    # Último tramo con hueco deliberado contra el punto de inicio.
    msp.add_line((0.0, H), (0.0, hueco), dxfattribs=out)
    doc.saveas(path)


def test_micro_hueco_de_cad_se_puentea():
    """Los exports de CAD dejan micro-gaps: deben cerrarse, no ofsetear cintas."""
    td = Path(tempfile.mkdtemp())
    src, dst = td / "gap_ok.dxf", td / "gap_ok_out.dxf"
    _placa_con_hueco(src, 0.004)
    off_mm = compute_plasma_offset_mm(0.250)
    d = off_mm / 25.4
    compensate_dxf_for_plasma(src, dst, offset_mm=off_mm)
    x0, y0, x1, y1 = _bbox_capa(ezdxf.readfile(dst).modelspace(), "CUT_OUTER")
    assert abs((x1 - x0) - (20.0 + 2 * d)) < 1e-6
    assert abs((y1 - y0) - (8.0 + 2 * d)) < 1e-6


def test_contorno_muy_abierto_falla_cerrado():
    """Un perfil realmente roto se rechaza con mensaje claro y sin escribir DXF."""
    td = Path(tempfile.mkdtemp())
    src, dst = td / "gap_bad.dxf", td / "gap_bad_out.dxf"
    _placa_con_hueco(src, 1.5)
    try:
        compensate_dxf_for_plasma(src, dst, offset_mm=compute_plasma_offset_mm(0.250))
    except RuntimeError as exc:
        assert "no cierra" in str(exc) or "abierto" in str(exc), exc
    else:
        raise AssertionError("un contorno abierto no debe compensarse")
    assert not dst.exists(), "no debe quedar DXF compensado inválido en disco"


def test_offset_deforme_es_rechazado():
    """La compuerta debe tumbar un resultado con lazos/contorno abierto."""
    from modules.plasma_occt_offset import ring_is_simple, validate_offset_ring

    cuadro = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0), (0.0, 0.0)]
    lazo = [(0.0, 0.0), (10.0, 0.0), (0.0, 5.0), (10.0, 5.0), (0.0, 0.0)]
    assert not ring_is_simple(lazo)
    assert validate_offset_ring(cuadro, lazo, 0.0125)
    abierto = [(-0.0125, -0.0125), (10.0125, -0.0125), (10.0125, 5.0125), (3.0, 5.0125)]
    assert validate_offset_ring(cuadro, abierto, 0.0125)
    bueno = [
        (-0.0125, -0.0125),
        (10.0125, -0.0125),
        (10.0125, 5.0125),
        (-0.0125, 5.0125),
        (-0.0125, -0.0125),
    ]
    assert validate_offset_ring(cuadro, bueno, 0.0125) == ""


if __name__ == "__main__":
    test_occt_offset_preserva_primitivas()
    test_occt_offset_preserva_bulge_como_arc()
    test_occt_offset_parking_cw_no_brep_api()
    test_occt_offset_placa_con_filetes_y_slot_crece_exacto()
    test_micro_hueco_de_cad_se_puentea()
    test_contorno_muy_abierto_falla_cerrado()
    test_offset_deforme_es_rechazado()
    print("SMOKE OK")
