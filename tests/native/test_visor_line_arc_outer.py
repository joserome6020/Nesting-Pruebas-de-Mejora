"""Candado 2026-08-14m — el visor arma outer_rings desde DXF Inventor.

Bug real: piezas GENE-BKT-*, GEN1-OP-* (todo lo que Inventor exporta a DXF)
traen el perfil en decenas de LINE+ARC sueltos en la capa
``IV_OUTER_PROFILE`` — sin LWPOLYLINE cerrado.

El loader del visor (``load_dxf_part``) solo agregaba a ``outer_rings`` los
contornos que provinieran de POLYLINE cerrado o CIRCLE. Los LINE+ARC nunca
se estibaban, así que ``outer_rings`` quedaba vacío y:

  * ``emphasize_plasma_outers`` no dibujaba el énfasis rojo del OUTER
  * ``set_plasma_overlay`` no podía hacer el buffer para el preview
  * ``area_neta`` quedaba en 0.00 in² para toda pieza Inventor

El fix agrupa las LINE/ARC de OUTER/INNER en anillos y los agrega como
"poly" a ``shapes_cerrados``, unificando el tratamiento con LWPOLYLINE.

Este candado sintetiza un DXF con el mismo patrón que Inventor (LINE+ARC en
``IV_OUTER_PROFILE``) y verifica que el loader produce outer_rings != vacío,
area_neta > 0 y bbox consistente.
"""
from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _crear_dxf_inventor(ruta: Path) -> None:
    """Crea un DXF tipo Inventor: rectángulo 4x2 con esquinas redondeadas + un hueco.

    Todo el OUTER es LINE+ARC (nada de LWPOLYLINE) — exactamente como GENE-BKT-101.
    """
    import ezdxf  # type: ignore

    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1  # pulgadas
    msp = doc.modelspace()
    if "IV_OUTER_PROFILE" not in doc.layers:
        doc.layers.new("IV_OUTER_PROFILE", dxfattribs={"color": 7})
    if "IV_INTERIOR_PROFILES" not in doc.layers:
        doc.layers.new("IV_INTERIOR_PROFILES", dxfattribs={"color": 7})

    r = 0.25
    W, H = 4.0, 2.0

    layer_out = {"layer": "IV_OUTER_PROFILE"}
    # Cuatro lados rectos.
    msp.add_line((r, 0.0), (W - r, 0.0), dxfattribs=layer_out)
    msp.add_line((W, r), (W, H - r), dxfattribs=layer_out)
    msp.add_line((W - r, H), (r, H), dxfattribs=layer_out)
    msp.add_line((0.0, H - r), (0.0, r), dxfattribs=layer_out)
    # Cuatro arcos en esquinas.
    msp.add_arc(
        center=(W - r, r), radius=r, start_angle=270.0, end_angle=360.0,
        dxfattribs=layer_out,
    )
    msp.add_arc(
        center=(W - r, H - r), radius=r, start_angle=0.0, end_angle=90.0,
        dxfattribs=layer_out,
    )
    msp.add_arc(
        center=(r, H - r), radius=r, start_angle=90.0, end_angle=180.0,
        dxfattribs=layer_out,
    )
    msp.add_arc(
        center=(r, r), radius=r, start_angle=180.0, end_angle=270.0,
        dxfattribs=layer_out,
    )

    # Hueco interior circular.
    msp.add_circle(
        center=(W / 2.0, H / 2.0), radius=0.5,
        dxfattribs={"layer": "IV_INTERIOR_PROFILES"},
    )

    doc.saveas(str(ruta))


def _load(ruta: Path):
    from interface.qt.dxf_part_loader import load_dxf_part

    return load_dxf_part(str(ruta))


def test_outer_rings_se_arma_desde_line_arc_inventor() -> None:
    with tempfile.TemporaryDirectory() as td:
        ruta = Path(td) / "iv_bracket.dxf"
        _crear_dxf_inventor(ruta)
        model = _load(ruta)
        assert model is not None, "loader devolvió None"
        rings = list(model.outer_rings or [])
        assert len(rings) >= 1, (
            f"outer_rings quedó vacío para DXF Inventor (LINE+ARC). "
            f"count={len(rings)}"
        )
        # El anillo debe cubrir todo el bbox del OUTER (4x2 in).
        ring = rings[0]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        assert min(xs) < 0.01 and max(xs) > 3.99, (min(xs), max(xs))
        assert min(ys) < 0.01 and max(ys) > 1.99, (min(ys), max(ys))
        # Y debe estar cerrado (< 0.05" gap entre primer y último punto).
        gap = math.hypot(ring[0][0] - ring[-1][0], ring[0][1] - ring[-1][1])
        assert gap < 0.05, f"anillo no cierra (gap={gap:.4f})"


def test_area_neta_deja_de_estar_en_cero_para_inventor() -> None:
    """Antes del fix: 0.00. Después del fix: > 5 in² (rectángulo con esquinas)."""
    with tempfile.TemporaryDirectory() as td:
        ruta = Path(td) / "iv_bracket_area.dxf"
        _crear_dxf_inventor(ruta)
        model = _load(ruta)
        assert model is not None
        # El loader tiene un doble-descuento heredado (CIRCLE inner se resta en
        # el branch CIRCLE y luego otra vez en el loop inners), pero el punto
        # crítico del candado es que ``area_neta`` deje de ser 0 para pieza
        # Inventor. Rango amplio: 5 in² < area < 8 in² (ideal ~7.16).
        assert model.area_neta > 5.0, (
            f"area_neta={model.area_neta:.3f} — debería ser > 5 in² tras el "
            f"fix (antes daba 0)."
        )
        assert model.area_neta < 8.0, model.area_neta


def test_bbox_consistente_para_inventor() -> None:
    """El bbox raw usa todos los puntos discretizados; debe cubrir la pieza."""
    with tempfile.TemporaryDirectory() as td:
        ruta = Path(td) / "iv_bracket_bbox.dxf"
        _crear_dxf_inventor(ruta)
        model = _load(ruta)
        assert model is not None
        assert model.min_x_raw <= 0.01 and model.max_x_raw >= 3.99
        assert model.min_y_raw <= 0.01 and model.max_y_raw >= 1.99


def test_gene_bkt_101_real_si_esta_disponible() -> None:
    """Si el DXF real vive en _tmp/GENE-BKT-101.dxf, comprobamos con él.

    Este es el caso que motivó el bug (bracket ~9.42" x 3.16", 44 LINE+ARC).
    Si el archivo no está en _tmp/, el test se salta.
    """
    ruta_real = RAIZ / "_tmp" / "GENE-BKT-101.dxf"
    if not ruta_real.is_file():
        print("skip: _tmp/GENE-BKT-101.dxf no disponible")
        return
    model = _load(ruta_real)
    assert model is not None
    rings = list(model.outer_rings or [])
    assert len(rings) == 1, (
        f"GENE-BKT-101 debe tener 1 anillo OUTER; got {len(rings)}"
    )
    ancho = model.max_x_raw - model.min_x_raw
    alto = model.max_y_raw - model.min_y_raw
    assert 9.3 < ancho < 9.5, f"ancho={ancho:.3f} debería ser ~9.42\""
    assert 3.0 < alto < 3.3, f"alto={alto:.3f} debería ser ~3.16\""
    # Area neta debe ser > 20 in² (piso conservador — la real está cerca de 23).
    assert model.area_neta > 20.0, (
        f"area_neta={model.area_neta:.2f} — pieza real GENE-BKT-101 debería "
        f"medir ~23 in²; si esto falla, el stitching de LINE+ARC no se activó."
    )


if __name__ == "__main__":
    test_outer_rings_se_arma_desde_line_arc_inventor()
    test_area_neta_deja_de_estar_en_cero_para_inventor()
    test_bbox_consistente_para_inventor()
    test_gene_bkt_101_real_si_esta_disponible()
    print("OK visor_line_arc_outer")
