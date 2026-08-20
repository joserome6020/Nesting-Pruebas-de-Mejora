"""2026-08-20 - AutoDXF/ANS: todo calibre empatado a decimal Herinox (no solo Cal 11)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from modules.arga_gauge_snap import (  # noqa: E402
    EXACT_DECIMALS,
    KNOWN_CAD_SNAPS,
    assert_all_gauges_snap_stable,
    fmt_decimal,
    snap_calibre_token,
    snap_thickness_inches,
)
from modules.herinox_sync import HerinoxPlateSync  # noqa: E402
from interface.autodxf_metadata import (  # noqa: E402
    combinar_metadata_dxf,
    normalizar_material_autodxf,
)


def test_json_parity_con_herinox_sync():
    path = RAIZ / "AutoDXF 2.0" / "arga_gauge_equivalences.json"
    assert path.is_file(), path
    data = json.loads(path.read_text(encoding="utf-8"))
    assert {int(k): float(v) for k, v in data["steel"].items()} == dict(
        HerinoxPlateSync.STEEL_GAUGE_TO_INCHES
    )
    assert {int(k): float(v) for k, v in data["stainless"].items()} == dict(
        HerinoxPlateSync.STAINLESS_GAUGE_TO_INCHES
    )
    assert {int(k): float(v) for k, v in data["aluminum"].items()} == dict(
        HerinoxPlateSync.ALUMINUM_GAUGE_TO_INCHES
    )
    js_exact = [float(x) for x in data.get("exact_decimals") or []]
    assert js_exact == list(EXACT_DECIMALS)


def test_ilogic_tiene_snap_y_materiales():
    src = (RAIZ / "AutoDXF 2.0" / "AutoDXF 2.0.iLogicVb").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "FormatThicknessForArga" in src
    assert "SnapThicknessToArga" in src
    assert "FillGaugeTableForMaterial" in src
    assert "0.1196" in src
    assert "0.0747" in src  # Cal 14 steel
    assert "0.1406" in src  # Cal 10 stainless
    assert "0.1285" in src  # Cal 8 aluminum
    assert 'Return "Galvanizado"' in src
    assert "G90" in src
    i_galv = src.find("GALVAN")
    i_a36 = src.find('Return "A 36"', src.find("NormalizeMaterialAlias"))
    assert i_galv > 0 and i_a36 > 0 and i_galv < i_a36
    assert "FormatThicknessForArga(thkIn, materialName)" in src
    assert "Nunca tumbar el export" in src or "Catch" in src
    # Exactos ≥ 3/16; no 0.0625 en lista exacta (rompe Cal 16).
    assert "0.0625" not in src.split("exacts(")[1].split("FillGaugeTable")[0]


def test_todos_los_calibres_con_offset_cad():
    assert_all_gauges_snap_stable()


def test_casos_planta_conocidos():
    for cad, mat, esperado in KNOWN_CAD_SNAPS:
        got = snap_thickness_inches(cad, mat)
        assert abs(got - esperado) < 1e-9, (cad, mat, got, esperado)
        assert snap_calibre_token(fmt_decimal(cad), mat) == fmt_decimal(esperado)


def test_calibre_nominal_entero():
    assert snap_calibre_token("11", "Galvanizado") == "0.1196"
    assert snap_calibre_token("14", "A 36") == "0.0747"
    assert snap_calibre_token("16", "A 36") == "0.0598"
    assert snap_calibre_token("11", "SSTL 304") == "0.125"
    assert snap_calibre_token("11", "Aluminio") == "0.0907"


def test_ans_import_snap_legacy_dxf_name():
    # DXF viejo con Cal CAD crudo: ANS debe canonicar al leer metadata.
    _pieza, mat, _qty, cal, _ex = combinar_metadata_dxf(
        r"C:\job\AutoDXF\Cal 0.11811 Galvanizado\FOO, Galvanizado, QTY 1, Cal 0.11811.dxf",
        default_material="GALVANIZADO",
        default_calibre="0.11811",
    )
    assert cal == "0.1196"
    assert normalizar_material_autodxf("Galvanizado") == "GALVANIZADO"
    assert normalizar_material_autodxf("G90") == "GALVANIZADO"
    assert normalizar_material_autodxf("A 36 GALV") == "GALVANIZADO"


def test_cal16_no_cae_en_0625():
    assert snap_thickness_inches(0.060, "A 36") == 0.0598
    assert snap_thickness_inches(0.0598, "GALVANIZADO") == 0.0598


if __name__ == "__main__":
    test_json_parity_con_herinox_sync()
    test_ilogic_tiene_snap_y_materiales()
    test_todos_los_calibres_con_offset_cad()
    test_casos_planta_conocidos()
    test_calibre_nominal_entero()
    test_ans_import_snap_legacy_dxf_name()
    test_cal16_no_cae_en_0625()
    print("AUTODXF_GAUGE_PARITY PASS")
