#!/usr/bin/env python3
"""Publica Consulta_Herinox.py en AutoDXF 2.0 (Z: y UNC)."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "modules" / "consulta_herinox_bridge.py"
REL = (
    r"♦♦GRUPO ARGA CARPETAS COMPARTIDAS♦♦\BIENVENIDO\Departamentos _antes TIK"
    r"\21. Desarrollo y Tecnologia\2.- Códigos Desarrollo y Tecnología"
    r"\7.- Configuración para equipos de Computo\AutoDXF 2.0"
)
DESTS = [
    Path("Z:") / REL / "Consulta_Herinox.py",
    Path(r"\\192.168.2.47\arga") / REL / "Consulta_Herinox.py",
]


def main() -> int:
    if not SRC.is_file():
        print(f"FALLO: no existe {SRC}")
        return 1
    ok_any = False
    for dst in DESTS:
        try:
            if not dst.parent.is_dir():
                print(f"SKIP (sin acceso): {dst.parent}")
                continue
            shutil.copy2(SRC, dst)
            print(f"OK -> {dst} ({dst.stat().st_size} bytes)")
            ok_any = True
        except Exception as exc:
            print(f"SKIP {dst}: {exc}")
    return 0 if ok_any else 1


if __name__ == "__main__":
    raise SystemExit(main())
