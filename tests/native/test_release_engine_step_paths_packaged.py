"""Candado: Create STEPs / Ver STEP requieren `engine.step_paths` en el build.

Bug real: el .exe fallaba al elegir ruta en Crear STEPs con ModuleNotFoundError
porque `engine` vive en `CAD (OCCT)/` y PyInstaller no tenía `--paths` ni
hidden-import para ese paquete.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))


def main() -> int:
    build = importlib.import_module("build_arga_exe")
    cad = ROOT / "CAD (OCCT)"
    assert cad.is_dir(), f"Falta carpeta {cad}"
    assert (cad / "engine" / "step_paths.py").is_file()

    build._ensure_build_import_path()
    assert any(
        Path(p).resolve() == cad.resolve() for p in sys.path if p
    ), "CAD (OCCT) debe estar en sys.path del build (Crear STEPs)."

    for name in (
        "engine",
        "engine.step_paths",
        "interface.qt.dialogs.crear_steps",
        "interface.qt.dialogs.support_inbox",
    ):
        assert name in build.SMOKE_IMPORT_MODULES or name.startswith("engine"), name
        if name in build.SMOKE_IMPORT_MODULES:
            importlib.import_module(name)

    for name in ("engine", "engine.step_paths", "engine.dxf_to_step", "engine.step_io"):
        assert name in build.HIDDEN_IMPORTS, f"{name} debe estar en HIDDEN_IMPORTS"
    assert "engine" in build.COLLECT_SUBMODULES

    # Smoke real del import que rompía en frozen.
    mod = importlib.import_module("engine.step_paths")
    assert hasattr(mod, "escanear_jobs_con_model_core")
    assert hasattr(mod, "listar_wo_con_dxf")
    assert hasattr(mod, "listar_swo_con_dxf")
    print("OK engine.step_paths empaquetable + smoke Crear STEPs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
