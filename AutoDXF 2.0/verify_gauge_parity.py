#!/usr/bin/env python3
"""Candado: arga_gauge_equivalences.json = tablas de modules/herinox_sync.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.herinox_sync import HerinoxPlateSync  # noqa: E402


def main() -> int:
    path = Path(__file__).resolve().parent / "arga_gauge_equivalences.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    checks = (
        ("steel", HerinoxPlateSync.STEEL_GAUGE_TO_INCHES),
        ("stainless", HerinoxPlateSync.STAINLESS_GAUGE_TO_INCHES),
        ("aluminum", HerinoxPlateSync.ALUMINUM_GAUGE_TO_INCHES),
    )
    errs: list[str] = []
    for key, ans_map in checks:
        js = {int(k): float(v) for k, v in (data.get(key) or {}).items()}
        if js != dict(ans_map):
            errs.append(f"{key}: JSON≠ANS  json={sorted(js.items())} ans={sorted(ans_map.items())}")

    # Snap Cal 11 CAD típico planta → 0.1196
    steel = HerinoxPlateSync.STEEL_GAUGE_TO_INCHES
    for cad in (0.11811, 0.118, 0.119, 0.1196):
        best = min(steel.items(), key=lambda kv: abs(kv[1] - cad))
        if best[0] != 11 or abs(best[1] - 0.1196) > 1e-9:
            errs.append(f"snap {cad} → gauge {best}, esperado 11 / 0.1196")
        if abs(cad - best[1]) > 0.008 and cad != 0.1196:
            errs.append(f"tol: {cad} no debería estar a >0.008 de Cal 11")

    if errs:
        print("FAIL AutoDXF gauge parity:")
        for e in errs:
            print(" ", e)
        return 1
    print("OK AutoDXF gauge parity vs herinox_sync + Cal 11 -> 0.1196")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
