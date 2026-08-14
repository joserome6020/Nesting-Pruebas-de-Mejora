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
    # Semicírculo superior como bulge + base inferior.
    doc.modelspace().add_lwpolyline(
        [(0, 0, 1.0), (10, 0, 0.0), (10, -5, 0.0), (0, -5, 0.0)],
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


if __name__ == "__main__":
    test_occt_offset_preserva_primitivas()
    test_occt_offset_preserva_bulge_como_arc()
    test_occt_offset_parking_cw_no_brep_api()
    print("SMOKE OK")
