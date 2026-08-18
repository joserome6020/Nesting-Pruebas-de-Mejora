#!/usr/bin/env python
"""Candado: movers usan distancia metal↔metal ≥ kerf completo (no buffer flojo).

El buffer(kerf/2) de Shapely con resolución baja dejaba gaps ~0.17" cuando
la tabla pedía 0.250" (WO 62176). Band-close / hole-fill deben fallar-closed.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from shapely.geometry import box

    from modules.nesting_engine.nest_poka_yoke import (
        distancia_menor_que_kerf_mm,
        validar_separacion_minima_hoja,
    )
    from modules.nesting_engine.venom_band_close import _collides
    from modules.nesting_engine.venom_hole_fill import _place_ok

    kerf_in = 0.250
    kerf_full_mm = kerf_in * 25.4
    kerf_half = kerf_full_mm / 2.0

    # Gap real ~0.17" (caso típico post-buffer) → DEBE chocar.
    gap_malo_mm = 0.17 * 25.4
    a = box(0, 0, 100, 50)
    b = box(0, 50 + gap_malo_mm, 100, 100 + gap_malo_mm)
    assert distancia_menor_que_kerf_mm(a, b, kerf_full_mm), "0.17\" < 0.250\""
    assert _collides(a, [b], kerf_half), "band-close debe rechazar gap 0.17\""

    # Cavidad grande + host lejos: guest↔guest con gap 0.17" → _place_ok False.
    cav = box(-10, -10, 200, 200)
    host = box(500, 500, 600, 600)  # lejos: no limita
    assert not _place_ok(a, cav, [b], host, kerf_half), (
        "hole-fill guest↔guest debe rechazar gap 0.17\""
    )

    # Gap exacto tabla → OK
    gap_ok_mm = kerf_full_mm
    c = box(0, 50 + gap_ok_mm, 100, 100 + gap_ok_mm)
    assert not distancia_menor_que_kerf_mm(a, c, kerf_full_mm)
    assert not _collides(a, [c], kerf_half)
    assert _place_ok(a, cav, [c], host, kerf_half)

    # Pokayoke hoja con calibre 0.375 → kerf tabla 0.250"
    hoja = {
        "placa_cal": "0.375",
        "placa_w": 3000.0,
        "placa_h": 1500.0,
        "piezas": [
            {"nombre": "A", "poly": a, "poligonos": [list(a.exterior.coords)]},
            {"nombre": "B", "poly": b, "poligonos": [list(b.exterior.coords)]},
        ],
        "kerf_usado": 0.15,
        "margin_usado": 0.15,
    }
    ok, detail = validar_separacion_minima_hoja(
        hoja, 0.15, margin_in=0.15, clave="0.375_A36"
    )
    assert ok is False, f"gap 0.17\" debe fallar pokayoke: {detail}"

    # 0.240" NO es 100% de 0.250" (tol vieja 0.025" lo dejaba pasar).
    gap240 = 0.240 * 25.4
    d = box(0, 50 + gap240, 100, 100 + gap240)
    hoja240 = {
        "placa_cal": "0.5",
        "placa_w": 3000.0,
        "placa_h": 1500.0,
        "piezas": [
            {"nombre": "A", "poly": a, "poligonos": [list(a.exterior.coords)]},
            {"nombre": "D", "poly": d, "poligonos": [list(d.exterior.coords)]},
        ],
        "kerf_usado": 0.15,
        "margin_usado": 0.15,
    }
    ok240, det240 = validar_separacion_minima_hoja(
        hoja240, 0.15, margin_in=0.15, clave="0.5_A36"
    )
    assert ok240 is False, f"0.240\" debe fallar vs tabla 0.250\": {det240}"

    # Margen placa 0.200" < 0.250" tabla → fail.
    inset200 = 0.200 * 25.4
    e = box(inset200, inset200, inset200 + 100, inset200 + 50)
    hoja_m = {
        "placa_cal": "0.5",
        "placa_w": 3000.0,
        "placa_h": 1500.0,
        "piezas": [
            {"nombre": "E", "poly": e, "poligonos": [list(e.exterior.coords)]},
        ],
        "kerf_usado": 0.25,
        "margin_usado": 0.15,
    }
    okm, detm = validar_separacion_minima_hoja(
        hoja_m, 0.25, margin_in=0.15, clave="0.5_A36"
    )
    assert okm is False, f"margen 0.200\" debe fallar vs 0.250\": {detm}"
    assert "margen_placa" in detm, detm

    print("EXACT_KERF_MOVERS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
