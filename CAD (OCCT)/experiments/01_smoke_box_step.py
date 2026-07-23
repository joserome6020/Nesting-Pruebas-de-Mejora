"""
Smoke test paralelo: caja → STEP con OCCT (OCP), sin FreeCAD.

Uso:
  python "CAD (OCCT)/experiments/01_smoke_box_step.py"
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # CAD (OCCT)/
sys.path.insert(0, str(ROOT))

from engine.occt_runtime import ensure_ocp, solid_volume, write_step_shape  # noqa: E402


def main() -> int:
    ensure_ocp()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    # Caja 10 x 20 x 5 (unidades modelo; en pruebas no fijamos aún IN/MM)
    shape = BRepPrimAPI_MakeBox(10.0, 20.0, 5.0).Shape()
    vol = solid_volume(shape)
    out = ROOT / "out" / "smoke_box.step"
    written = write_step_shape(shape, out)

    print("OCCT smoke OK")
    print(f"  volume ~= {vol:.6f}")
    print(f"  step   -> {written} ({written.stat().st_size} bytes)")
    print("  (experimento paralelo; FreeCAD sigue siendo el flujo normal)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
