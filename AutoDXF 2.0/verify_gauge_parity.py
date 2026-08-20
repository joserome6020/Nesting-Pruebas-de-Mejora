#!/usr/bin/env python3
"""Candado: arga_gauge_equivalences.json + snap todos los calibres vs herinox_sync."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.arga_gauge_snap import (  # noqa: E402
    EXACT_DECIMALS,
    assert_all_gauges_snap_stable,
    snap_thickness_inches,
)
from modules.herinox_sync import HerinoxPlateSync  # noqa: E402


def main() -> int:
    path = Path(__file__).resolve().parent / "arga_gauge_equivalences.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    errs: list[str] = []
    checks = (
        ("steel", HerinoxPlateSync.STEEL_GAUGE_TO_INCHES),
        ("stainless", HerinoxPlateSync.STAINLESS_GAUGE_TO_INCHES),
        ("aluminum", HerinoxPlateSync.ALUMINUM_GAUGE_TO_INCHES),
    )
    for key, ans_map in checks:
        js = {int(k): float(v) for k, v in (data.get(key) or {}).items()}
        if js != dict(ans_map):
            errs.append(f"{key}: JSON≠ANS")

    js_exact = [float(x) for x in data.get("exact_decimals") or []]
    if js_exact != list(EXACT_DECIMALS):
        errs.append(f"exact_decimals JSON≠snap module: {js_exact}")

    try:
        assert_all_gauges_snap_stable()
    except AssertionError as exc:
        errs.append(str(exc))

    # Casos planta multi-calibre
    samples = (
        (0.11811, "steel", 0.1196),
        (0.075, "steel", 0.0747),
        (0.060, "steel", 0.0598),
        (0.110, "stainless", 0.1094),
        (0.091, "aluminum", 0.0907),
    )
    for cad, mat, exp in samples:
        got = snap_thickness_inches(cad, mat)
        if abs(got - exp) > 1e-9:
            errs.append(f"snap {cad}/{mat} → {got}, esperado {exp}")

    if errs:
        print("FAIL AutoDXF gauge parity:")
        for e in errs:
            print(" ", e)
        return 1
    print("OK AutoDXF gauge parity: all gauges + known CAD snaps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
