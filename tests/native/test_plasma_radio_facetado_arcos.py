"""Candado 2026-09-28 — SWO-076-H2_PLASMA: radios facetados salían como ~300 LINE.

Repro de planta (S.W.O 76 X1, cal 0.1875): 20 piezas de 150.75 x 77.09 mm con
dos radios R 11.1126 mm (7/16") llegaron al DXF de plasma como 600 vértices
cada una (micro-segmentos de ~0.06 mm) en lugar de LINE + ARC.

Causas:
1. ``_ring_is_rectilinear`` evaluaba cada segmento por separado con tol
   0.55 mm; una faceta de 0.06 mm nunca excede tol en X y Y a la vez, así que
   el radio se declaraba "rectilíneo" y ``_export_ring_exact`` escribía cada
   faceta como LINE.
2. ``export_ring_native`` cortaba el arco en un punto casi duplicado
   (retroceso de 0.0013°) y, al no caer justo en el índice 0, recorría el
   anillo cerrado hasta 4 veces (2400 entidades para 600 puntos = cortes
   duplicados).
3. Origen del facetado: el Plasma Compensated de «BRACE A 90 New, QTY 20»
   (TANK261138) lo escribió el respaldo Clipper2 (601 vértices) porque OCCT
   rechazaba la polilínea cuando su primer vértice abre un arco (bulge). La
   misma pieza en TANK25502 empieza en recta y salía bien por OCCT.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

FIXTURE = Path(__file__).with_name("swo076_h2_ring_facetado.json")
RADIO = 11.1126


def _ring():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [(float(x), float(y)) for x, y in data["ring"]]


def test_radio_facetado_no_es_rectilineo() -> None:
    from modules.plasma_dxf_export import _outer_export_line_exact

    assert _outer_export_line_exact(_ring()) is False, (
        "un radio de 7/16 facetado en micro-segmentos es curva, no perfil recto"
    )


def test_export_ring_native_una_vuelta_con_arcos() -> None:
    import ezdxf  # type: ignore

    from modules.dxf_native_curves import export_ring_native

    pts = _ring()
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    assert export_ring_native(msp, pts, "CUT_OUTER", closed=True)
    ents = list(msp)
    tipos = Counter(e.dxftype() for e in ents)
    arcs = [e for e in ents if e.dxftype() == "ARC"]
    assert len(ents) <= 12, f"esperado ~4 LINE + 2 ARC, salió {dict(tipos)}"
    assert len(arcs) >= 2, dict(tipos)
    for a in arcs:
        assert abs(float(a.dxf.radius) - RADIO) < 0.01, a.dxf.radius

    perimetro = 0.0
    for e in ents:
        if e.dxftype() == "LINE":
            perimetro += math.dist((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y))
        else:
            sweep = (float(e.dxf.end_angle) - float(e.dxf.start_angle)) % 360.0
            perimetro += math.radians(sweep) * float(e.dxf.radius)
    n = len(pts)
    ref = sum(math.dist(pts[i], pts[(i + 1) % n]) for i in range(n))
    assert abs(perimetro - ref) < 0.5, (
        f"perímetro {perimetro:.3f} vs anillo {ref:.3f}: contorno duplicado o incompleto"
    )


# Processed Files/BRACE A 90 New, A 36, QTY 20, Cal 0.1875.dxf (pulgadas).
BRACE_TANK261138 = [
    (5.81, -0.0, 0.414214), (5.435, 0.375, 0.0), (0.375, 0.375, 0.414214),
    (-0.0, -0.0, 0.0), (-0.0, -2.535, 0.0), (5.81, -2.535, 0.0), (5.81, -0.0, 0.0),
]
OFF_IN = 1.5875 / 25.4


def _brace_src(tmp: Path) -> Path:
    import ezdxf  # type: ignore

    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    doc.modelspace().add_lwpolyline(
        [(x, y, 0, 0, b) for x, y, b in BRACE_TANK261138],
        format="xyseb",
        close=True,
        dxfattribs={"layer": "CUT_OUTER"},
    )
    src = tmp / "BRACE A 90 New, A 36, QTY 20, Cal 0.1875.dxf"
    doc.saveas(src)
    return src


def _outer_compensado(dst: Path):
    import ezdxf  # type: ignore

    ents = [e for e in ezdxf.readfile(dst).modelspace() if e.dxf.layer == "CUT_OUTER"]
    assert len(ents) == 1 and ents[0].dxftype() == "LWPOLYLINE", ents
    return list(ents[0].get_points("xyseb"))


def _assert_brace_compensado(pts) -> None:
    assert len(pts) <= 8, f"compensado facetado: {len(pts)} vértices"
    radios = [p for p in pts if abs(p[4] - math.tan(math.radians(90) / 4)) < 1e-3]
    assert len(radios) == 2, f"se esperaban 2 arcos de 90°: {pts}"
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    assert abs((max(xs) - min(xs)) - (5.81 + 2 * OFF_IN)) < 1e-3
    assert abs((max(ys) - min(ys)) - (2.91 + 2 * OFF_IN)) < 1e-3


def test_occt_compensa_polilinea_que_inicia_en_arco() -> None:
    import tempfile

    from modules.plasma_compensator import compensate_dxf_for_plasma
    from modules.plasma_occt_offset import occt_available

    if not occt_available():
        print("SKIP: OCCT no disponible")
        return
    with tempfile.TemporaryDirectory() as td:
        src = _brace_src(Path(td))
        dst = Path(td) / "out.dxf"
        st = compensate_dxf_for_plasma(src, dst, offset_mm=1.5875)
        assert st["backend"] == "occt", (
            f"OCCT debe compensar la pieza aunque inicie en arco; backend={st['backend']}"
        )
        _assert_brace_compensado(_outer_compensado(dst))


def test_fallback_clipper_escribe_bulges() -> None:
    import tempfile

    import modules.plasma_occt_offset as occt
    from modules.plasma_compensator import compensate_dxf_for_plasma
    from modules.plasma_offset_clipper import clipper_disponible

    if not clipper_disponible():
        print("SKIP: pyclipr no disponible")
        return
    original = occt.occt_available
    occt.occt_available = lambda: False
    try:
        with tempfile.TemporaryDirectory() as td:
            src = _brace_src(Path(td))
            dst = Path(td) / "out.dxf"
            st = compensate_dxf_for_plasma(src, dst, offset_mm=1.5875)
            assert st["backend"] == "clipper2", st
            _assert_brace_compensado(_outer_compensado(dst))
    finally:
        occt.occt_available = original


def test_ring_to_bulge_vertices_anillo_real() -> None:
    import ezdxf  # type: ignore
    from ezdxf import path as ezpath  # type: ignore

    from modules.dxf_native_curves import ring_to_bulge_vertices

    ring = _ring()
    verts = ring_to_bulge_vertices(ring, tol=0.0159)
    assert len(verts) == 6, verts
    assert sum(1 for v in verts if abs(v[2] - 0.41421) < 1e-3) == 2, verts
    doc = ezdxf.new()
    lw = doc.modelspace().add_lwpolyline(
        [(x, y, 0, 0, b) for x, y, b in verts], format="xyseb", close=True
    )
    from shapely.geometry import LineString, Point

    flat = LineString([(q.x, q.y) for q in ezpath.make_path(lw).flattening(0.001)])
    dev = max(flat.distance(Point(p)) for p in ring)
    assert dev < 0.02, f"desviación {dev:.4f} mm vs anillo original"


def test_perfil_escalonado_sigue_rectilineo() -> None:
    from modules.plasma_dxf_export import _outer_export_line_exact

    pts = [
        (0, 0), (4, 0), (4, 1), (3, 1), (3, 2), (4, 2),
        (4, 3), (0, 3), (0, 2), (1, 2), (1, 1), (0, 1),
    ]
    assert _outer_export_line_exact(pts) is True
    ruido = [(0, 0), (100, 0.003), (100.002, 50), (0, 50.001)]
    assert _outer_export_line_exact(ruido) is True


if __name__ == "__main__":
    test_radio_facetado_no_es_rectilineo()
    test_export_ring_native_una_vuelta_con_arcos()
    test_occt_compensa_polilinea_que_inicia_en_arco()
    test_fallback_clipper_escribe_bulges()
    test_ring_to_bulge_vertices_anillo_real()
    test_perfil_escalonado_sigue_rectilineo()
    print("OK plasma_radio_facetado_arcos")
