#!/usr/bin/env python
"""Candado: todo mover de piezas usa TABLA GAPS (no UI 0.15 en calibre 2)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from modules.nesting_engine.cut_gaps_table import (
        PLATE_TO_PIECE_DEFAULT_IN,
        gaps_efectivos_para_hoja,
        gaps_for_calibre,
    )
    from modules.nesting_engine.nest_poka_yoke import validar_separacion_minima_hoja
    from modules.nesting_engine.sheet_integrity import kerf_efectivo_hoja
    from shapely.geometry import box

    # Tabla: Cal 2 → 0.375" / 0.250"
    k, m = gaps_efectivos_para_hoja({"placa_cal": "2"}, kerf_fallback=0.15)
    assert abs(k - 0.375) < 1e-9 and abs(m - 0.250) < 1e-9, (k, m)

    k2, m2 = gaps_efectivos_para_hoja(None, clave="2_SS", kerf_fallback=0.15)
    assert abs(k2 - 0.375) < 1e-9 and abs(m2 - PLATE_TO_PIECE_DEFAULT_IN) < 1e-9

    # kerf_efectivo_hoja no puede devolver 0.15 si el calibre es 2.
    hoja = {"placa_cal": "2", "kerf_usado": 0.0}
    assert abs(kerf_efectivo_hoja(hoja, clave="2_CS", kerf_global=0.15) - 0.375) < 1e-9

    # Pokayoke: caller pasa 0.15 pero hoja es calibre 2 → sigue rechazando gap 0.15".
    g15 = 0.15 * 25.4
    a = box(10, 10, 210, 110)
    b = box(10, 110 + g15, 210, 210 + g15)
    hoja_mala = {
        "placa_cal": "2",
        "placa_w": 3000.0,
        "placa_h": 1500.0,
        "piezas": [
            {"nombre": "P64", "poly": a, "poligonos": [list(a.exterior.coords)]},
            {"nombre": "P01", "poly": b, "poligonos": [list(b.exterior.coords)]},
        ],
        "kerf_usado": 0.15,  # sellado malo (UI)
        "margin_usado": 0.15,
    }
    ok, detail = validar_separacion_minima_hoja(hoja_mala, 0.15, margin_in=0.15, clave="2_SS")
    assert ok is False, f"tabla debe ganar a UI 0.15: {detail}"
    assert "gap_insuficiente" in detail, detail

    # Band-close: sin plate_inset explícito → default placa 0.250", no kerf/2.
    from modules.nesting_engine.venom_band_close import _group_can_move

    poly = box(0, 0, 100, 50)
    # Pieza con borde a 1 mm del origen: kerf_half de 0.375" (~4.76) cabría,
    # pero inset 0.250" (~6.35) NO.
    members = [{"idx": 0, "poly": poly, "p": {}}]
    all_e = [{"idx": 0, "poly": poly, "p": {}}]
    kerf_half = (0.375 * 25.4) / 2.0
    assert _group_can_move(
        members, all_e, 0.0, 0.0, kerf_half, 3000.0, 1500.0
    ) is False, "default plate inset debe ser 0.250\""

    # Con inset 0 y pieza ya en 0: permitiría si no hay colisión; forzamos inset tabla.
    poly2 = box(7.0, 7.0, 107.0, 57.0)
    members2 = [{"idx": 0, "poly": poly2, "p": {}}]
    all2 = [{"idx": 0, "poly": poly2, "p": {}}]
    assert _group_can_move(
        members2,
        all2,
        0.0,
        0.0,
        kerf_half,
        3000.0,
        1500.0,
        plate_inset_mm=0.250 * 25.4,
    ) is True

    k375, m250, _ = gaps_for_calibre("2")
    assert abs(k375 - 0.375) < 1e-9 and abs(m250 - 0.250) < 1e-9

    print("MOVERS_TABLA_GAPS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
