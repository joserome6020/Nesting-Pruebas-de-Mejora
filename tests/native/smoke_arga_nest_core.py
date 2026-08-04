#!/usr/bin/env python
"""Smoke test ArgaNestCore (ANS C++ Fase A).

Uso (desde raíz del repo):
  python tests/native/smoke_arga_nest_core.py

Sin el .pyd compilado:
  - Verifica que el bridge reporta estado degradado limpio
  - Exit code 0 con mensaje SKIP (o 2 si --require-core)

Con el .pyd:
  - Empaca un rectángulo 100x50 en placa 300x200
  - Exige placed_count >= 1 y ok=true
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def rect_rings(w: float, h: float):
    return [[(0.0, 0.0), (w, 0.0), (w, h), (0.0, h), (0.0, 0.0)]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--require-core",
        action="store_true",
        help="Fallar si arga_nest_core no está compilado",
    )
    ap.add_argument(
        "--engine",
        default="svgnest_ultra",
        help="Engine id (svgnest_ultra|arga_force|burke_blf|libnest2d)",
    )
    args = ap.parse_args()

    from modules.nesting_engine import arga_nest_core_bridge as bridge

    status = bridge.core_status()
    print("core_status:", json.dumps(status, indent=2, ensure_ascii=False))

    if not status["module_loaded"]:
        msg = "SKIP: arga_nest_core.pyd no compilado aún (ejecuta native\\build_arga_nest_core.ps1)"
        print(msg)
        return 2 if args.require_core else 0

    os.environ["ARGA_NEST_CORE"] = "1"
    # re-check
    assert bridge.core_available()

    req = {
        "engine": args.engine,
        "plate_w": 300.0,
        "plate_h": 200.0,
        "kerf": 0.2,
        "margin": 0.0,
        "ga_population": 4,
        "ga_generations": 4,
        "rotation_step_deg": 90.0,
        "part_in_part": False,
        "pieces": [
            {
                "nombre": "RECT_A",
                "area": 5000.0,
                "calibre": "1/4",
                "material": "SS",
                "rings": rect_rings(100.0, 50.0),
            }
        ],
    }
    print("request engine:", args.engine)
    result = bridge.pack_sheet_json(req)
    print("result:", json.dumps(result, indent=2, ensure_ascii=False)[:2000])

    assert result.get("ok") is True, result
    assert result.get("core") == "ArgaNestCore", result
    metrics = result.get("metrics") or {}
    placed = int(metrics.get("placed_count") or 0)
    assert placed >= 1, f"expected >=1 placed, got {placed}"
    print("SMOKE PASS — ArgaNestCore pack_sheet OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
