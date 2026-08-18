#!/usr/bin/env python
"""Candado: calibre 2 → 0.375" entre piezas; pokayoke rechaza gaps de 0.15"."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from shapely.geometry import box

    from modules.nesting_engine.cut_gaps_table import gaps_for_calibre
    from modules.nesting_engine.nest_poka_yoke import validar_separacion_minima_hoja

    for cal in ("2", "2.0", "2.000", '2"'):
        kerf, margin, rule = gaps_for_calibre(cal)
        assert abs(kerf - 0.375) < 1e-9, (cal, kerf, rule)
        assert abs(margin - 0.250) < 1e-9, (cal, margin)
        assert "2.000" in str(rule.get("label") or ""), rule

    # Caso real: APEX dejó ~0.15" → debe RECHAZARSE con kerf tabla 0.375.
    # Coordenadas en mm (contrato hoja).
    g15 = 0.15 * 25.4
    a = box(0, 0, 200, 100)
    b = box(0, 100 + g15, 200, 200 + g15)
    hoja_mala = {
        "piezas": [
            {"nombre": "P64", "poly": a, "poligonos": [list(a.exterior.coords)]},
            {"nombre": "P01", "poly": b, "poligonos": [list(b.exterior.coords)]},
        ],
        "kerf_usado": 0.375,
    }
    ok, detail = validar_separacion_minima_hoja(hoja_mala, 0.375)
    assert ok is False, f"debía fallar gap 0.15 vs 0.375: {detail}"
    assert "gap_insuficiente" in detail, detail

    # Caller UI 0.15 + calibre en hoja: la TABLA gana y sigue rechazando.
    hoja_ui = {
        "placa_cal": "2",
        "piezas": hoja_mala["piezas"],
        "kerf_usado": 0.15,
    }
    ok_ui, det_ui = validar_separacion_minima_hoja(hoja_ui, 0.15, clave="2_SS")
    assert ok_ui is False, f"tabla debe ganar a UI: {det_ui}"

    # Gap correcto 0.375" → OK.
    g375 = 0.375 * 25.4
    b2 = box(0, 100 + g375, 200, 200 + g375)
    hoja_ok = {
        "piezas": [
            {"nombre": "P64", "poly": a, "poligonos": [list(a.exterior.coords)]},
            {"nombre": "P01", "poly": b2, "poligonos": [list(b2.exterior.coords)]},
        ],
        "kerf_usado": 0.375,
    }
    ok2, detail2 = validar_separacion_minima_hoja(hoja_ok, 0.375)
    assert ok2 is True, detail2

    print("KERF_CALIBRE2_POKA PASS", detail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
