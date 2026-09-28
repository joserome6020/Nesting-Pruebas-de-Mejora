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
    test_perfil_escalonado_sigue_rectilineo()
    print("OK plasma_radio_facetado_arcos")
