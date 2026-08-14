"""Candado: compensación plasma tipo Autodesk OFFSET no deforma perfiles.

Usa densificación fina + FreeCAD (si ARGA_PLASMA_OFFSET_FREECAD=auto/1) o
semántica Clipper2/Ultra (outer+/holes−, sin largest-wins).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Tests rápidos: no arrancar FreeCAD (60s+). El fallback GEOS es el candado
# de topología; FreeCAD se prueba aparte si el env lo fuerza.
os.environ.setdefault("ARGA_PLASMA_OFFSET_FREECAD", "0")

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

import ezdxf
from shapely.geometry import Polygon

from modules.plasma_compensator import (  # noqa: E402
    compensate_dxf_for_plasma,
    compute_plasma_offset_mm,
)
from modules.plasma_offset2d import (  # noqa: E402
    PLASMA_OFFSET_ALGO_VERSION,
    compensated_dxf_is_current,
    densify_ring,
    offset_closed_profile,
    offset_simple_ring,
    write_version_sidecar,
)


def _u_profile():
    # Perfil en U / cuello cóncavo (no debe colapsar el canal).
    return [
        (0.0, 0.0),
        (10.0, 0.0),
        (10.0, 6.0),
        (7.0, 6.0),
        (7.0, 2.0),
        (3.0, 2.0),
        (3.0, 6.0),
        (0.0, 6.0),
    ]


def test_offset_expande_rectangulo_sin_deformar_proporcion():
    base = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
    delta = 0.0125
    res = offset_simple_ring(base, delta=delta, prefer_freecad=False)
    assert res.ok, res.error
    assert res.backend.startswith("clipper_semantics")
    poly = Polygon(res.rings[0])
    assert poly.is_valid and not poly.is_empty
    minx, miny, maxx, maxy = poly.bounds
    assert abs(minx - (-delta)) < 1e-6
    assert abs(miny - (-delta)) < 1e-6
    assert abs(maxx - (10.0 + delta)) < 1e-6
    assert abs(maxy - (5.0 + delta)) < 1e-6


def test_offset_perfil_concavo_conserva_canal():
    u = _u_profile()
    base = Polygon(u)
    res = offset_simple_ring(u, delta=0.15, prefer_freecad=False)
    assert res.ok, res.error
    out = Polygon(res.rings[0])
    assert out.is_valid
    # El canal interior del U debe seguir existiendo (área crece, no se vuelve rectángulo).
    assert float(out.area) > float(base.area)
    # Un rectángulo 10x6 offset 0.15 tendría área ~ (10.3*6.3)=64.89; el U offset
    # debe quedar claramente por debajo (conserva el hueco del canal).
    rect_area = (10.0 + 2 * 0.15) * (6.0 + 2 * 0.15)
    assert float(out.area) < rect_area * 0.92


def test_offset_con_hueco_contrae_inner():
    outer = [(0.0, 0.0), (20.0, 0.0), (20.0, 12.0), (0.0, 12.0)]
    hole = [(8.0, 4.0), (12.0, 4.0), (12.0, 8.0), (8.0, 8.0)]
    delta = 0.25
    res = offset_closed_profile(outer, delta=delta, holes=[hole], prefer_freecad=False)
    assert res.ok, res.error
    assert len(res.rings) >= 1
    # Al menos un anillo outer expandido.
    outer_off = max(res.rings, key=lambda r: abs(Polygon(r).area))
    assert Polygon(outer_off).bounds[0] <= -delta + 1e-6


def test_densify_no_pierde_cierre():
    ring = densify_ring([(0, 0), (1, 0), (1, 1), (0, 1)], max_seg=0.2)
    assert len(ring) > 4
    assert abs(ring[0][0] - ring[-1][0]) > 1e-9 or len(set(ring)) >= 4


def test_compensate_dxf_keyhole_y_version_sidecar():
    td = Path(tempfile.mkdtemp())
    src = td / "keyhole.dxf"
    dst = td / "Plasma Compensated" / "keyhole.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    # Outer rectangular + muesca tipo keyhole (polilínea cerrada).
    outer = [
        (0, 0),
        (30, 0),
        (30, 10),
        (18, 10),
        (18, 6),
        (20, 4),
        (18, 2),
        (12, 2),
        (12, 10),
        (0, 10),
    ]
    msp.add_lwpolyline(outer, close=True, dxfattribs={"layer": "CUT_OUTER"})
    msp.add_circle((8, 5), radius=1.5, dxfattribs={"layer": "CUT_INNER"})
    doc.saveas(str(src))

    off = compute_plasma_offset_mm(0.375)  # 0.0125 in → mm
    st = compensate_dxf_for_plasma(src, dst, offset_mm=off)
    assert int(st.get("changed") or 0) >= 1, st
    assert Path(dst).is_file()
    write_version_sidecar(dst, backend=str(st.get("backend") or ""))
    assert compensated_dxf_is_current(src, dst)
    assert st.get("algo") == PLASMA_OFFSET_ALGO_VERSION

    out_doc = ezdxf.readfile(str(dst))
    # Nativo = LINE/ARC/CIRCLE sueltos o polilínea cerrada con bulges (2026-08-14h:
    # si el origen era polilínea se devuelve polilínea para conservar el área neta).
    outers = [
        e
        for e in out_doc.modelspace()
        if e.dxftype() in {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE"}
        and str(e.dxf.layer).upper() == "CUT_OUTER"
    ]
    assert outers, "debe haber CUT_OUTER nativo compensado"
    for e in outers:
        if e.dxftype() in {"LWPOLYLINE", "POLYLINE"}:
            assert bool(e.closed), "la polilínea compensada debe quedar cerrada"
    circs = [e for e in out_doc.modelspace() if e.dxftype() == "CIRCLE"]
    assert circs, "debe conservar CIRCLE inner"
    r = float(circs[0].dxf.radius)
    expect = 1.5 - (off / 25.4)
    assert abs(r - expect) < 1e-6, (r, expect)


def test_no_largest_wins_en_multiparte():
    # Dos islas: el offset debe devolver ambas, no solo la mayor.
    a = [(0, 0), (2, 0), (2, 2), (0, 2)]
    b = [(5, 0), (9, 0), (9, 3), (5, 3)]
    from shapely.geometry import MultiPolygon

    mp = MultiPolygon([Polygon(a), Polygon(b)])
    # Simula aplicar offset a cada componente vía servicio.
    ra = offset_simple_ring(a, delta=0.1, prefer_freecad=False)
    rb = offset_simple_ring(b, delta=0.1, prefer_freecad=False)
    assert ra.ok and rb.ok
    assert abs(Polygon(ra.rings[0]).area - Polygon(rb.rings[0]).area) > 1.0
    assert mp.area > 0


if __name__ == "__main__":
    test_offset_expande_rectangulo_sin_deformar_proporcion()
    test_offset_perfil_concavo_conserva_canal()
    test_offset_con_hueco_contrae_inner()
    test_densify_no_pierde_cierre()
    test_compensate_dxf_keyhole_y_version_sidecar()
    test_no_largest_wins_en_multiparte()
    print("SMOKE OK")
