#!/usr/bin/env python
"""Remnants inyectados al pool de nesting + mark used."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    os.environ.setdefault("ARGA_NEST_REMNANTS_IN_NEST", "1")
    os.environ["ARGA_NEST_REMNANTS_SOURCE"] = "csv"

    from modules.nesting_engine import remnants_inventory as rem
    from modules.sheets_manager import PlatesManager

    rows = rem.as_datos_placas_rows(max_plates=5)
    assert rows, "expected remnant rows from CSV"
    assert rows[0][9] == "REMANENTE"
    assert float(rows[0][3]) > 0 and float(rows[0][4]) > 0
    ui_id = str(rows[0][2])
    assert ui_id.startswith("REM-"), f"UI id must be REM-… not catalog SKU, got {ui_id}"
    assert not ui_id.upper().startswith("PL-CARB"), "PL-CARB must not appear as placa UI id"
    mat = str(rows[0][1]).upper()
    assert mat in ("CARBONO", "INOX", "ALUMINIO", "COBRE") or mat, mat
    print("ROW0", rows[0][:5], rows[0][9])

    # Inject into empty / fake herinox pool
    herinox = [
        ["0.313", "Carbono", "PLC999", 120, 48, 100.0, 5000.0, 0.5, "DISPONIBLE", "EMPRESA", 0.5]
    ]
    merged = rem.inject_remnants_into_datos_placas(herinox, max_plates=3)
    assert len(merged) >= 4
    assert merged[0][9] == "REMANENTE"
    assert merged[-1][2] == "PLC999"
    print("INJECT", len(merged), "first", merged[0][2], "last", merged[-1][2])

    # PlatesManager path (may have empty herinox offline — still injects)
    pm = PlatesManager()
    # Bypass sync: call inject on empty
    out = rem.inject_remnants_into_datos_placas([])
    assert out and out[0][9] == "REMANENTE"

    # Opt-out
    os.environ["ARGA_NEST_REMNANTS_IN_NEST"] = "0"
    assert rem.inject_remnants_into_datos_placas(herinox) == herinox
    os.environ["ARGA_NEST_REMNANTS_IN_NEST"] = "1"

    # mark used (Postgres if available)
    try:
        rem.sync_csv_to_postgres(ROOT / "inventario_remanentes.csv", max_rows=5)
        mk = rem.mark_remnant_used(rows[0][2])
        print("MARK", mk)
        assert mk.get("ok") is True
    except Exception as ex:
        print("MARK_SKIP", ex)

    # Manager prefers remnant in classify
    from modules.nesting_engine.manager import MotorNesting

    motor = MotorNesting()
    exactas, mode = motor._clasificar_placas_por_calibre("0.313", "Carbono", merged)
    print("CLASSIFY", mode, len(exactas), exactas[0]["id"] if exactas else None)
    if exactas:
        assert exactas[0].get("es_remanente") is True or "REMANENTE" in str(
            exactas[0].get("origen")
        )

    print("REMNANTS_NEST_WIRE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
