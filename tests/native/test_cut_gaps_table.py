"""Candado de la tabla oficial de gaps de corte."""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from modules.nesting_engine.cut_gaps_table import (  # noqa: E402
    CutGapTableError,
    default_cut_gap_settings,
    gaps_for_calibre,
    normalize_cut_gap_settings,
    verify_cut_gap_edit_password,
)
from modules.nesting_engine.sheet_integrity import kerf_efectivo_hoja  # noqa: E402


def _assert_gap(calibre, kerf):
    got_kerf, got_margin, _rule = gaps_for_calibre(
        calibre,
        settings=default_cut_gap_settings(),
    )
    assert got_kerf == kerf, (calibre, got_kerf, kerf)
    assert got_margin == 0.250, (calibre, got_margin)


def test_tabla_oficial_por_calibre_y_espesor():
    # Valores exactos de la TABLA GAPS DE CORTE de planta.
    for calibre in ("18", "16", "14", "12", "11", "10", "0.188"):
        _assert_gap(calibre, 0.150)
    for calibre in ("0.250", "5/16", "0.375"):
        _assert_gap(calibre, 0.200)
    for calibre in ("0.500", "5/8", "0.750"):
        _assert_gap(calibre, 0.250)
    for calibre in ("1.000", "1 1/4"):
        _assert_gap(calibre, 0.313)
    for calibre in ("1.500", "1.750", "2.000"):
        _assert_gap(calibre, 0.375)


def test_decimales_reales_herinox_resuelven_su_calibre():
    for calibre in ("0.0478", "0.0598", "0.0747", "0.0781", "0.1046", "0.1094", "0.1196", "0.125", "0.1345"):
        _assert_gap(calibre, 0.150)


def test_calibre_fuera_de_tabla_falla_cerrado():
    try:
        gaps_for_calibre("13", settings=default_cut_gap_settings())
    except CutGapTableError:
        pass
    else:
        raise AssertionError("Cal 13 no tiene regla oficial y debe rechazar el nest.")


def test_edicion_protegida_y_validada():
    assert verify_cut_gap_edit_password("DYT361")
    assert not verify_cut_gap_edit_password("incorrecta")

    custom = default_cut_gap_settings()
    custom["plate_to_piece_in"] = 0.300
    custom["kerf_by_rule"]["cal_18"] = 0.125
    normalized = normalize_cut_gap_settings(custom)
    kerf, margin, _rule = gaps_for_calibre("0.0478", settings=normalized)
    assert kerf == 0.125
    assert margin == 0.300


def test_integridad_no_reemplaza_kerf_de_hoja_por_fallback_legacy():
    assert kerf_efectivo_hoja({"kerf_usado": 0.375}, "2_A 36", kerf_global=0.150) == 0.375
    assert kerf_efectivo_hoja({}, "18_A 36", kerf_global=0.100) == 0.100


if __name__ == "__main__":
    test_tabla_oficial_por_calibre_y_espesor()
    test_decimales_reales_herinox_resuelven_su_calibre()
    test_calibre_fuera_de_tabla_falla_cerrado()
    test_edicion_protegida_y_validada()
    test_integridad_no_reemplaza_kerf_de_hoja_por_fallback_legacy()
    print("SMOKE OK")
