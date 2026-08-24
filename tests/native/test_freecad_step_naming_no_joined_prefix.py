"""Candado: STEP FreeCAD conserva nombre del DXF original (sin prefijo _joined_)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from freecad_runner import _cad_path_for_dxf, _step_path_for_dxf  # noqa: E402


def test_step_name_ignores_joined_temp_dxf_basename():
    joined = r"C:\temp\_joined_SWO-001_0.5_SWO-001-H12.dxf"
    original = r"C:\nest\SWO-001_0.5_SWO-001-H12.dxf"
    out = r"C:\nest\STEP"
    assert _cad_path_for_dxf(joined, out).endswith("SWO-001_0.5_SWO-001-H12.step")
    assert _step_path_for_dxf(original, out).endswith("SWO-001_0.5_SWO-001-H12.step")


def test_step_name_strips_joined_prefix_if_passed():
    path = os.path.join("C:", "x", "_joined_NESTING_0.25_W.O. 01-H1.dxf")
    got = os.path.basename(_cad_path_for_dxf(path, "C:\\out"))
    assert got == "W.O. 01-H1.step", got


if __name__ == "__main__":
    test_step_name_ignores_joined_temp_dxf_basename()
    test_step_name_strips_joined_prefix_if_passed()
    print("OK test_freecad_step_naming_no_joined_prefix")
