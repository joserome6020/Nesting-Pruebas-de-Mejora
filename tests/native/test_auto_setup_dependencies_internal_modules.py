"""Candado 2026-08-13 — `arga_nest_core` / `engine` no son paquetes pip.

Motivo real: al correr `python tools/build_arga_exe.py --release`, el helper
`tools/auto_setup_dependencies.py` marcaba `arga_nest_core` y `engine` como
"faltantes" y trataba de instalarlos desde PyPI con
`pip install arga_nest_core engine ...`, que fallaba con:

    ERROR: Could not find a version that satisfies the requirement arga_nest_core
    ERROR: No matching distribution found for arga_nest_core

Rompiendo el build entero antes de siquiera llegar a PyInstaller.

Causa: `_discover_local_top_modules` sólo escaneaba `.py`, así que ignoraba
el `.pyd` compilado (`modules/nesting_engine/arga_nest_core.pyd`) y el
paquete interno `engine/` bajo `CAD (OCCT)/engine/` (fuera del scan).

Fix: (a) sumar `arga_nest_core` y `engine` a `NON_PIP_IMPORTS`; (b) que
`_discover_local_top_modules` reconozca `.pyd` / `.dll` compilados como
módulos locales del repo. Este candado verifica ambos caminos.

Este test no requiere red ni build. Si en el futuro alguien vuelve a
quitar `arga_nest_core` de la lista o revierte el escaneo de `.pyd`,
el candado falla y avisa antes de que se rompa el build de nuevo.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))


def _reload_module():
    if "auto_setup_dependencies" in sys.modules:
        return importlib.reload(sys.modules["auto_setup_dependencies"])
    return importlib.import_module("auto_setup_dependencies")


def main() -> int:
    mod = _reload_module()

    # 1) Blocklist explícito: nombres de módulos internos NUNCA deben resolverse
    #    a un paquete pip (aunque `_discover_local_top_modules` los pase por alto).
    for name in (
        "arga_nest_core",
        "engine",
        "algorithm_cpp",
        "classification",
        "carousel_config",
        "dxf",
    ):
        assert name in mod.NON_PIP_IMPORTS, (
            f"'{name}' debe estar en NON_PIP_IMPORTS para no bajarlo de PyPI."
        )

    # 2) Escaneo local: `.pyd` compilados deben aparecer como módulos locales
    #    (defensa en profundidad si alguien quita el blocklist).
    local = mod._discover_local_top_modules()
    pyd_files = list(ROOT.rglob("arga_nest_core*.pyd"))
    if pyd_files:
        assert "arga_nest_core" in local, (
            "arga_nest_core.pyd existe en el repo pero no se detectó como "
            "módulo local. `_discover_local_top_modules` debe escanear .pyd."
        )

    # 3) Resolución de paquetes: el pipeline completo NO debe incluir
    #    módulos internos en la lista de pip.
    resolved = mod.resolve_required_packages(
        include_optional=True,
        include_legacy_tk=False,
    )
    for prohibido in (
        "arga_nest_core",
        "engine",
        "algorithm_cpp",
        "classification",
        "carousel_config",
        "dxf",
    ):
        assert prohibido not in resolved, (
            f"resolve_required_packages devolvió '{prohibido}': "
            "el build va a intentar bajarlo de PyPI y fallar."
        )

    print("[OK] auto_setup_dependencies filtra correctamente módulos internos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
