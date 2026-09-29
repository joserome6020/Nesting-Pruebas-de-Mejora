"""Candado 2026-09-29d: la exportación de acero no crea NESTEO DXF/JSON/Cama A|B.

Cada export creaba JSON/Cama A y JSON/Cama B aunque no hubiera hojas de Robot
Láser, y en las que sí había corría el clasificador LS-READY; nadie consume
esos JSON. Se quitó del flujo (el clasificador queda como herramienta manual).
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import modules.nesting_engine.exporter as exporter  # noqa: E402


def main() -> int:
    fallos: list[str] = []
    src = inspect.getsource(exporter)
    for marca in ("ls_ready", "robot_laser_json", '"JSON", "Cama'):
        if marca in src:
            fallos.append(f"exporter.py todavía contiene {marca!r}")
    if hasattr(exporter, "_generar_json_ls_ready_robot_laser"):
        fallos.append("exporter conserva el hook LS-READY")
    if fallos:
        print("FAIL:\n  " + "\n  ".join(fallos))
        return 1
    print("OK export acero sin JSON LS-READY ni carpetas JSON/Cama A|B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
