"""Candado: espesor SWO-NNN_<cal>_ en freecad_runner (paridad OCCT)."""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from freecad_runner import thickness_mm_from_dxf_name  # noqa: E402


def test_swo_dxf_name_thickness_inches_to_mm():
    thk = thickness_mm_from_dxf_name("SWO-001_0.375_SWO-001-H3.dxf", default_mm=12.7)
    assert abs(thk - 9.525) < 0.01, thk


def test_nesting_name_still_works():
    thk = thickness_mm_from_dxf_name("NESTING_0.25_W.O. 01.dxf", default_mm=12.7)
    assert abs(thk - 6.35) < 0.01, thk


if __name__ == "__main__":
    test_swo_dxf_name_thickness_inches_to_mm()
    test_nesting_name_still_works()
    print("OK test_freecad_runner_swo_thickness")
