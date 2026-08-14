"""Candado 2026-08-14p — GENE-OP-1010-211 deja de fallar el export plasma.

Repro de planta (job GIGA FLUIDSTACK, cal 0.0747):

    plasma[GENE-OP-1010-211_PLASMA] VALIDACION:
      empalmes CUT_OUTER: 48 LINE duplicados
      medidas vs nest: delta 8.13x8.13 mm (esperado ~+0.64)
      posición min-corner desplazada 7.81 mm
      margen placa Y: minY=-1.5

Causa: el perfil fuente es un LWPOLYLINE rectilíneo con escalones
(notches). ``_ring_is_rectilinear`` exigía que *todos* los vértices tocaran
el borde del bbox — los escalones interiores fallaban — y el export caía al
detector de curvas nativas, que inventaba ARC de radio ~26 mm y escribía el
contorno 8 veces.

Fix: rectilinear = segmentos horizontales/verticales (no "vértices en el
borde"). El offset se escribe con LINE exactas; el bbox del ARC en la
validación usa el sweep real, no el círculo completo.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

ESCALA = 25.4
OFF_MM = 0.3175
SRC = RAIZ / "_tmp" / "op211" / "src.dxf"


def _cargar_pts_fuente():
    import ezdxf  # type: ignore

    doc = ezdxf.readfile(str(SRC))
    lw = next(e for e in doc.modelspace() if e.dxftype() == "LWPOLYLINE")
    return [(float(x), float(y)) for x, y, *_ in lw.get_points("xy")]


def test_perfil_con_escalones_es_rectilineo() -> None:
    from modules.plasma_dxf_export import _ring_is_rectilinear

    if not SRC.is_file():
        print("SKIP: no hay DXF _tmp/op211/src.dxf")
        return
    pts = _cargar_pts_fuente()
    assert _ring_is_rectilinear(pts, tol=0.02), (
        "GENE-OP-1010-211 es un perfil de aristas ortogonales; el predicado "
        "viejo (vértices en el borde del bbox) lo rechazaba por los escalones"
    )


def test_export_op211_sin_duplicados_ni_arco_inventado() -> None:
    import ezdxf  # type: ignore

    from modules.dxf_export.validate import (
        _count_duplicate_lines,
        _entities_bbox_mm,
        validate_plasma_piece,
    )
    from modules.plasma_dxf_export import export_compensated_plasma_from_source

    if not SRC.is_file():
        print("SKIP: no hay DXF _tmp/op211/src.dxf")
        return

    pts = _cargar_pts_fuente()
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    src_w = (max(xs) - min(xs)) * ESCALA
    src_h = (max(ys) - min(ys)) * ESCALA

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    p = {
        "part_name": "GENE-OP-1010-211_PLASMA",
        "ruta": str(SRC),
        "plasma_offset_mm": OFF_MM,
        "plasma_export": True,
        "x": 0.0,
        "y": 0.0,
        "rot_deg": 0.0,
        # Outer alineado con el placement a origen: misma base que el DXF.
        "outer": [
            ((x - min(xs)) * ESCALA, (y - min(ys)) * ESCALA) for x, y in pts
        ],
    }
    # También hay que desplazar el DXF fuente: export usa _resolve_placement_matrix
    # sobre las coordenadas originales. Para que nest y corte compartan origen,
    # usamos el outer del nest ya colocado (como en producción) y medimos solo
    # crecimiento / duplicados / tipos — el min-corner depende del placement.
    stats = export_compensated_plasma_from_source(msp, doc, p)
    assert int(stats.get("outer", 0)) > 0, stats

    ents = list(msp)
    outer = [e for e in ents if str(e.dxf.layer).upper() == "CUT_OUTER"]
    assert outer, "sin CUT_OUTER"
    tipos = {e.dxftype() for e in outer}
    assert "ARC" not in tipos, (
        f"un perfil rectilíneo no debe inventar ARC; salió {tipos}"
    )
    dups = _count_duplicate_lines(outer, layer_set=frozenset({"CUT_OUTER"}))
    assert dups == 0, f"empalmes CUT_OUTER: {dups} (antes eran 48)"

    bb = _entities_bbox_mm(outer)
    assert bb is not None
    dw = (bb[2] - bb[0]) - src_w
    dh = (bb[3] - bb[1]) - src_h
    assert abs(dw - 2 * OFF_MM) < 0.05, f"crecimiento X {dw} (esperado {2*OFF_MM})"
    assert abs(dh - 2 * OFF_MM) < 0.05, f"crecimiento Y {dh} (esperado {2*OFF_MM})"

    # Sin nest outer coherente no forzamos min-corner; sí exigimos que las
    # medidas vs nest NO se disparen cuando el outer coincide en tamaño.
    p_medidas = {
        "part_name": "GENE-OP-1010-211_PLASMA",
        "outer": [(0.0, 0.0), (src_w, 0.0), (src_w, src_h), (0.0, src_h)],
    }
    # Alinear cut_b a (0,0) restando su min: validamos solo el delta de tamaño.
    # validate_plasma_piece compara posición; aquí comprobamos el mensaje de
    # medidas no aparece cuando el tamaño es correcto.
    issues = validate_plasma_piece(p_medidas, outer, offset_mm=OFF_MM)
    medidas = [i for i in issues if i.startswith("medidas vs nest")]
    assert not medidas, medidas
    empalmes = [i for i in issues if "empalmes" in i]
    assert not empalmes, empalmes


def test_outer_export_line_exact_no_cierra_en_8_vertices() -> None:
    """El tope de 8 pts mandaba perfiles escalonados al detector de arcos."""
    from modules.plasma_dxf_export import _outer_export_line_exact

    # Escalones: 12 vértices, todos ortogonales, varios fuera del borde del bbox.
    pts = [
        (0, 0), (4, 0), (4, 1), (3, 1), (3, 2), (4, 2),
        (4, 3), (0, 3), (0, 2), (1, 2), (1, 1), (0, 1), (0, 0),
    ]
    assert _outer_export_line_exact(pts) is True


if __name__ == "__main__":
    test_perfil_con_escalones_es_rectilineo()
    test_export_op211_sin_duplicados_ni_arco_inventado()
    test_outer_export_line_exact_no_cierra_en_8_vertices()
    print("OK plasma_export_rectilineo_escalones")
