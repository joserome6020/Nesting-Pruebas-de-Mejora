"""Candado 2026-08-14q - separación pieza-pieza en plasma se mide con geometría.

Bug real (GENE-OP-1010-211, W.O. 44 X3 / H1): el export abortaba con
``separación pieza-pieza 3.8 mm < kerf nest 7.6 mm`` y el usuario veía
``plasma: sin contorno exportable desde el nest`` con un nest perfectamente
válido. Dos defectos sumados:

1. La separación se medía entre bounding boxes axis-aligned. Dos perfiles con
   escalones se entrelazan, así que sus cajas se acercan mucho más que el metal.
2. Se comparaba contra el kerf pelado del nest, ignorando que la compensación
   hace crecer cada contorno ``off`` hacia afuera: la separación real entre
   contornos compensados es ``kerf - 2*off`` por construcción.

Además la tabla de la placa debe mostrar SOLO las piezas colocadas en esa placa
y las dimensiones de la pieza tal como se corta (compensada).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import ezdxf

from modules.dxf_export.validate import (
    _bbox_separation_mm,
    _segments_min_distance_mm,
    piece_clearance_record,
    validate_plasma_piece,
)

KERF_IN = 0.30
OFF_MM = 0.3175  # 0.0125 in por lado
ESCALA = 25.4


KERF_MM = KERF_IN * ESCALA  # 7.62 mm
GAP_REAL = KERF_MM - 2.0 * OFF_MM  # separación real esperada tras compensar

SHEET = {
    "length": 3048.0,
    "width": 1524.0,
    "kerf_usado": KERF_IN,
    "margin_usado": 0.25,
}


def _polilinea(msp, pts: list[tuple[float, float]]) -> list:
    ents = []
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        ents.append(msp.add_line(a, b, dxfattribs={"layer": "CUT_OUTER"}))
    return ents


def _pieza_con_muesca(msp) -> list:
    """Perfil rectilíneo con muesca arriba-derecha (flat pattern OP-1010-211).

    Material en x[10,110] para y[10,60] y en x[10,80] para y[60,90]; la muesca
    vacía es x[80,110] x y[60,90].
    """
    return _polilinea(
        msp,
        [(10.0, 10.0), (110.0, 10.0), (110.0, 60.0), (80.0, 60.0), (80.0, 90.0), (10.0, 90.0)],
    )


def _pieza_con_lengueta(msp, holgura: float) -> list:
    """Perfil cuya lengüeta entra en la muesca del anterior, dejando `holgura`.

    Su bbox se ENCIMA en X con el de la pieza anterior aunque el metal conserve
    `holgura` en todo su perímetro: es el caso que el chequeo por bounding box
    reprobaba en falso.
    """
    tab_left = 80.0 + holgura
    tab_bot = 60.0 + holgura
    body_left = 110.0 + holgura
    return _polilinea(
        msp,
        [
            (body_left, 10.0),
            (body_left + 100.0, 10.0),
            (body_left + 100.0, 90.0),
            (tab_left, 90.0),
            (tab_left, tab_bot),
            (body_left, tab_bot),
        ],
    )


def test_piezas_entrelazadas_con_kerf_respetado_no_reprueban():
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    ents_a = _pieza_con_muesca(msp)
    ents_b = _pieza_con_lengueta(msp, GAP_REAL)

    rec_a = piece_clearance_record(ents_a)
    rec_b = piece_clearance_record(ents_b)
    assert rec_a and rec_b, "el record de clearance debe traer bbox + segmentos"
    assert rec_a["segs"], "sin segmentos no se puede medir separación real"

    # Premisa del bug: las CAJAS se enciman pese a que el kerf es correcto, así
    # que el chequeo viejo (bbox contra kerf pelado) reprobaba este nest válido.
    caja_a, caja_b = rec_a["bbox"], rec_b["bbox"]
    assert caja_b[0] < caja_a[2], (
        "el caso debe tener bounding boxes encimados en X para reproducir el bug"
    )
    sep_caja = _bbox_separation_mm(caja_a, caja_b)
    assert sep_caja is not None and sep_caja < KERF_MM - 0.5, (
        f"el criterio viejo debe reprobar este caso (bbox {sep_caja} mm)"
    )

    d = _segments_min_distance_mm(rec_a["segs"], rec_b["segs"], limit=KERF_MM)
    assert d is not None
    assert abs(d - GAP_REAL) < 0.05, f"distancia real {d:.3f} != esperada {GAP_REAL:.3f}"

    issues = validate_plasma_piece(
        {"part_name": "OP-1010-211_PLASMA"},
        ents_b,
        offset_mm=OFF_MM,
        sheet=SHEET,
        all_piece_bounds=[rec_a],
    )
    sep = [m for m in issues if "separación pieza-pieza" in m]
    assert not sep, f"falso positivo de separación con kerf respetado: {sep}"


def test_piezas_de_verdad_encimadas_siguen_reprobando():
    """El candado no debe volverse permisivo: poca holgura real sí reprueba."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    ents_a = _pieza_con_muesca(msp)
    ents_b = _pieza_con_lengueta(msp, 1.0)  # 1 mm de aire real

    issues = validate_plasma_piece(
        {"part_name": "PEGADA"},
        ents_b,
        offset_mm=OFF_MM,
        sheet=SHEET,
        all_piece_bounds=[piece_clearance_record(ents_a)],
    )
    assert any("separación pieza-pieza" in m for m in issues), (
        f"1 mm de separación con kerf 7.6 debe reprobar; issues={issues}"
    )


def test_tabla_de_placa_muestra_solo_cantidad_de_esa_placa():
    """La columna es 'Cant.' con lo colocado en la placa, sin PLACA/NEST/REQ."""
    src = Path(__file__).resolve().parents[2] / "interface" / "qt" / "nesting_graphics.py"
    txt = src.read_text(encoding="utf-8", errors="ignore")
    assert '"Cant."' in txt, "la columna de cantidad debe llamarse 'Cant.'"
    assert "PLACA / NEST / REQ" not in txt, (
        "la tabla de la placa no debe mostrar tres cantidades"
    )


def test_dimensiones_de_tabla_incluyen_la_compensacion():
    """L/W deben medir la pieza como se corta, no el perfil sin compensar."""
    src = Path(__file__).resolve().parents[2] / "interface" / "qt" / "nesting_graphics.py"
    txt = src.read_text(encoding="utf-8", errors="ignore")
    assert "2.0 * off_pieza" in txt, (
        "las dimensiones de la tabla deben sumar el desfase por lado"
    )


if __name__ == "__main__":
    print(
        "    2026-08-14q - separación plasma por geometría real + tabla de placa "
        "con cantidad y medidas compensadas"
    )
    test_piezas_entrelazadas_con_kerf_respetado_no_reprueban()
    test_piezas_de_verdad_encimadas_siguen_reprobando()
    test_tabla_de_placa_muestra_solo_cantidad_de_esa_placa()
    test_dimensiones_de_tabla_incluyen_la_compensacion()
    print("    OK plasma_separacion_piezas")
