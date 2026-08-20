"""2026-08-20 - AutoDXF 2.0: Cal DXF = decimal Herinox/ANS (no 0.11811 vs 0.1196)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from modules.herinox_sync import HerinoxPlateSync  # noqa: E402


def _snap_steel(thk: float) -> float:
    tabla = HerinoxPlateSync.STEEL_GAUGE_TO_INCHES
    best = min(tabla.items(), key=lambda kv: abs(float(kv[1]) - thk))
    if abs(float(best[1]) - thk) <= 0.008:
        return float(best[1])
    return thk


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


def test_ilogic_tiene_snap_y_galvanizado():
    src = (RAIZ / "AutoDXF 2.0" / "AutoDXF 2.0.iLogicVb").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "FormatThicknessForArga" in src
    assert "SnapThicknessToArga" in src
    assert "FillGaugeTableForMaterial" in src
    assert "0.1196" in src
    assert 'Return "Galvanizado"' in src
    # Galvanizado antes que A 36 (bug: A 36 GALV caía en A 36).
    i_galv = src.find("GALVAN")
    i_a36 = src.find('Return "A 36"', src.find("NormalizeMaterialAlias"))
    assert i_galv > 0 and i_a36 > 0 and i_galv < i_a36
    assert "FormatThicknessForArga(thkIn, materialName)" in src
    assert "Nunca tumbar el export" in src or "Catch" in src


def test_cad_cal11_planta_snap_a_herinox():
    # Inventor suele traer 0.11811; Herinox Cal 11 acero = 0.1196.
    assert _snap_steel(0.11811) == 0.1196
    assert _snap_steel(0.118) == 0.1196
    assert _snap_steel(0.1196) == 0.1196
    assert _snap_steel(0.25) == 0.25  # placa gruesa exacta (no gauge)


if __name__ == "__main__":
    test_json_parity_con_herinox_sync()
    test_ilogic_tiene_snap_y_galvanizado()
    test_cad_cal11_planta_snap_a_herinox()
    print("AUTODXF_GAUGE_PARITY PASS")
