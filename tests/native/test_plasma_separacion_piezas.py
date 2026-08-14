"""Candado 2026-08-14q/r - validación de separación en plasma.

Bug real (GENE-OP-1010-211, W.O. 44 X3 / H1, cal 0.0747): el export abortaba
con ``separación pieza-pieza 3.8 mm < kerf nest 7.6 mm`` → ``plasma: sin
contorno exportable desde el nest``, con un nest CORRECTO. Tres defectos:

1. **La causa raíz.** ``sheet_info`` (el dict que el exportador pasa al
   validador) no incluía ``kerf_usado`` ni ``margin_usado``, así que el
   validador caía a defaults inventados: kerf 0.30" y margen 0.15". La TABLA
   GAPS DE CORTE fija para cal 14 kerf **0.150"** y margen **0.250"**. Suponer
   0.30" reprobaba un nest válido; suponer margen 0.15" habría dejado pasar
   violaciones reales de los 0.250".
2. Se comparaba contra el kerf pelado, ignorando que la compensación hace
   crecer cada contorno ``off`` hacia afuera: entre contornos compensados la
   separación real es ``kerf - 2*off`` por construcción (y el margen a placa
   es ``margen - off``).
3. La separación se medía entre bounding boxes axis-aligned, que mienten en
   cuanto dos perfiles con escalones se entrelazan.

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


def test_exporter_pasa_kerf_y_margen_reales_de_la_hoja():
    """`sheet_info` debe llevar kerf/margen del nest, no dejarlos al default."""
    src = (
        Path(__file__).resolve().parents[2]
        / "modules" / "nesting_engine" / "exporter.py"
    )
    txt = src.read_text(encoding="utf-8", errors="ignore")
    assert '"kerf_usado": float(hoja.get("kerf_usado")' in txt, (
        "sheet_info debe propagar kerf_usado de la hoja al validador"
    )
    assert '"margin_usado": float(hoja.get("margin_usado")' in txt, (
        "sheet_info debe propagar margin_usado de la hoja al validador"
    )


def test_validador_no_inventa_kerf_ni_margen():
    """Sin dato real no se juzga: un default equivocado bloqueó producción."""
    src = (
        Path(__file__).resolve().parents[2] / "modules" / "dxf_export" / "validate.py"
    )
    txt = src.read_text(encoding="utf-8", errors="ignore")
    assert 'sheet.get("kerf_usado") or 0.3' not in txt, (
        "no se puede suponer kerf 0.30 in: cal 14 usa 0.150 in por tabla"
    )
    assert 'sheet.get("margin_usado") or 0.15' not in txt, (
        "no se puede suponer margen 0.15 in: la tabla fija 0.250 in"
    )


def test_hoja_sin_kerf_no_reprueba_por_separacion():
    """Sin kerf en la hoja el chequeo se omite en vez de inventar un umbral."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    ents_a = _pieza_con_muesca(msp)
    ents_b = _pieza_con_lengueta(msp, 1.0)  # pegadísimas a propósito

    issues = validate_plasma_piece(
        {"part_name": "SIN_KERF"},
        ents_b,
        offset_mm=OFF_MM,
        sheet={"length": 3048.0, "width": 1524.0},  # sin kerf_usado/margin_usado
        all_piece_bounds=[piece_clearance_record(ents_a)],
    )
    assert not [m for m in issues if "separación pieza-pieza" in m], (
        f"sin kerf real no se debe inventar umbral; issues={issues}"
    )


def test_caso_h1_de_produccion_pasa_con_el_kerf_de_tabla():
    """Números exactos del log de H1: cal 0.0747 → kerf 0.150 in, gap real 3.8 mm."""
    from modules.nesting_engine.cut_gaps_table import gaps_for_calibre

    kerf_in, margin_in, regla = gaps_for_calibre(0.0747)
    assert abs(kerf_in - 0.150) < 1e-9, f"cal 0.0747 debe usar kerf 0.150; dio {kerf_in}"
    assert abs(margin_in - 0.250) < 1e-9, margin_in

    off = 0.318  # plasma_offset_mm del log
    minimo = kerf_in * ESCALA - 2.0 * off
    gap_real_h1 = 3.80  # (1047.3-0.3) - 1043.2, bboxes exportados del log
    assert gap_real_h1 >= minimo - 0.5, (
        f"H1 debe pasar: real {gap_real_h1} vs mínimo {minimo:.2f}"
    )
    # Y con el default inventado viejo tenía que reprobar (esto es el bug).
    minimo_viejo = 0.30 * ESCALA - 2.0 * off
    assert gap_real_h1 < minimo_viejo - 0.5, (
        "si el default viejo no reprobaba, este candado no cubre el bug"
    )


def test_dimensiones_de_tabla_incluyen_la_compensacion():
    """L/W deben medir la pieza como se corta, no el perfil sin compensar."""
    src = Path(__file__).resolve().parents[2] / "interface" / "qt" / "nesting_graphics.py"
    txt = src.read_text(encoding="utf-8", errors="ignore")
    assert "2.0 * off_pieza" in txt, (
        "las dimensiones de la tabla deben sumar el desfase por lado"
    )
    assert "compute_plasma_offset_mm" in txt, (
        "si la pieza llega marcada sin mm de desfase, la tabla debe "
        "recalcularlo con la misma regla del export"
    )


if __name__ == "__main__":
    print(
        "    2026-08-14q/r - kerf/margen reales de la hoja al validar plasma; "
        "separación por geometría real; tabla de placa correcta"
    )
    test_piezas_entrelazadas_con_kerf_respetado_no_reprueban()
    test_piezas_de_verdad_encimadas_siguen_reprobando()
    test_tabla_de_placa_muestra_solo_cantidad_de_esa_placa()
    test_exporter_pasa_kerf_y_margen_reales_de_la_hoja()
    test_validador_no_inventa_kerf_ni_margen()
    test_hoja_sin_kerf_no_reprueba_por_separacion()
    test_caso_h1_de_produccion_pasa_con_el_kerf_de_tabla()
    test_dimensiones_de_tabla_incluyen_la_compensacion()
    print("    OK plasma_separacion_piezas")
