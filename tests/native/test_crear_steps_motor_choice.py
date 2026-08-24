"""Candado: Crear STEPs / Exportar 3D usan OCCT por defecto (FreeCAD solo env legacy)."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def main() -> int:
    for name in list(sys.modules):
        if name == "despachador_nocturno" or name.startswith("despachador_nocturno."):
            del sys.modules[name]

    from despachador_nocturno import _usar_occt_para_crear_steps
    from modules.nesting_engine.step_export_prefs import motor_3d_crear_steps, motor_3d_export
    import interface.qt.dialogs.crear_steps as crear

    assert not hasattr(crear, "_preguntar_motor_3d")
    assert "motor_3d" in crear._ejecutar_conversion.__code__.co_varnames

    assert motor_3d_crear_steps() == "occt"
    assert motor_3d_export() == "occt"
    assert _usar_occt_para_crear_steps("occt") is True
    assert _usar_occt_para_crear_steps("freecad") is False
    assert _usar_occt_para_crear_steps(motor="freecad") is False

    prev_c = os.environ.get("ARGA_CREAR_STEPS_MOTOR")
    prev_e = os.environ.get("ARGA_EXPORT_3D_MOTOR")
    try:
        os.environ["ARGA_CREAR_STEPS_MOTOR"] = "freecad"
        os.environ["ARGA_EXPORT_3D_MOTOR"] = "freecad"
        assert motor_3d_crear_steps() == "freecad"
        assert motor_3d_export() == "freecad"
        assert _usar_occt_para_crear_steps(motor="freecad") is False
    finally:
        if prev_c is None:
            os.environ.pop("ARGA_CREAR_STEPS_MOTOR", None)
        else:
            os.environ["ARGA_CREAR_STEPS_MOTOR"] = prev_c
        if prev_e is None:
            os.environ.pop("ARGA_EXPORT_3D_MOTOR", None)
        else:
            os.environ["ARGA_EXPORT_3D_MOTOR"] = prev_e

    print("OK crear/export STEP motor default OCCT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
