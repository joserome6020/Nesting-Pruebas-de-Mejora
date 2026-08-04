#!/usr/bin/env python
"""Smoke: preferencias de carpetas STEP."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from modules.nesting_engine import step_export_prefs as sep

    p = sep.default_step_export_prefs()
    assert all(p.values())
    p["nesteos_cobre"] = False
    path = sep.save_step_export_prefs(p)
    assert path.is_file()
    loaded = sep.load_step_export_prefs()
    assert loaded["nesteos_cobre"] is False
    assert loaded["robot_laser"] is True
    assert sep.step_enabled_for_label("NESTEOS DE COBRE") is False
    assert sep.step_enabled_for_label("ROBOT LASER + MINI NEST") is True
    assert sep.step_enabled_for_label("ROBOT LASER A") is True
    # restore defaults
    sep.save_step_export_prefs(sep.default_step_export_prefs())
    print("STEP_EXPORT_PREFS PASS", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
