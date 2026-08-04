#!/usr/bin/env python
"""Worker IPC + remnants CSV/Postgres loader (offline-safe)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def rect(w, h):
    return [[(0.0, 0.0), (w, 0.0), (w, h), (0.0, h), (0.0, 0.0)]]


def main() -> int:
    os.environ.setdefault("ARGA_NEST_CORE", "1")
    # Este test fuerza worker explícitamente
    os.environ["ARGA_NEST_WORKER"] = "1"
    os.environ["ARGA_NEST_REMNANTS_SOURCE"] = "csv"

    from modules.nesting_engine import arga_nest_core_bridge as bridge
    from modules.nesting_engine import arga_nest_worker_client as wcli
    from modules.nesting_engine import remnants_inventory as rem

    # --- Remnants CSV ---
    plates = rem.load_remnant_plates(ROOT / "inventario_remanentes.csv", max_plates=5)
    assert plates and plates[0]["is_remnant"] is True
    assert plates[0].get("source") == "csv"
    print("REM_CSV", plates[0]["id"], plates[0]["area_in2"])

    # --- Remnants Postgres (optional) ---
    os.environ["ARGA_NEST_REMNANTS_SOURCE"] = "auto"
    try:
        sync = rem.sync_csv_to_postgres(ROOT / "inventario_remanentes.csv", max_rows=20)
        print("REM_SYNC", sync)
        pg = rem.load_remnant_plates_from_postgres(max_plates=5)
        print("REM_PG", len(pg), pg[0]["id"] if pg else None)
        if pg:
            assert pg[0].get("source") == "postgres"
    except Exception as ex:
        print("REM_PG_SKIP", ex)

    # --- Worker ---
    st = wcli.worker_status()
    print("WORKER_STATUS", st)
    assert st["exe_exists"], f"missing worker exe: {st['exe']}"
    assert wcli.ping_worker() is True
    print("WORKER_PING ok")

    req = bridge.prepare_pack_request(
        plate_w=300,
        plate_h=200,
        pieces=[
            {"nombre": "A", "area": 4000, "rings": rect(80, 50)},
            {"nombre": "B", "area": 3000, "rings": rect(60, 50)},
        ],
        kerf=0.2,
        profile="first",
        ga_population=4,
        ga_generations=3,
        enable_tabu=False,
    )
    # pack via bridge (routes to worker because ARGA_NEST_WORKER=1)
    r = bridge.pack_sheet_json(req)
    print("WORKER_PACK", r.get("metrics"), r.get("certify"))
    assert (r.get("certify") or {}).get("ok") is True
    assert int(r["metrics"]["placed_count"]) >= 1

    cst = bridge.core_status()
    assert "worker" in cst
    print("CORE_STATUS_WORKER", cst["worker"])

    wcli.close_worker()
    print("WORKER_REMNANTS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
