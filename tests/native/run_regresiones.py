#!/usr/bin/env python
"""Regresiones de logica de negocio: cada bug corregido deja aqui su candado.

No requiere el core C++ compilado ni conexion a PostgreSQL: son pruebas puras,
pensadas para correrse SIEMPRE antes de cerrar una sesion o publicar un build.

    py -3.14 tests\\native\\run_regresiones.py

Al corregir un bug, agrega su test a REGRESIONES con el caso real que lo motivo.
Un bug sin candado aqui es un bug que va a volver.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

REGRESIONES = [
    (
        "test_export_sin_lista_largos.py",
        "2026-08-05a - job sin CSV de largos (251008-COMPARTMENT) no debe tumbar el export",
    ),
    (
        "test_largos_swo_factor.py",
        "2026-08-05b - largos de una SWO se multiplican por WO (X3+X3+X3+X2), no por lote_k",
    ),
]


def main() -> int:
    fallos: list[str] = []

    # Las consolas de Windows suelen venir en cp1252 y los avisos traen acentos.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    entorno = dict(os.environ, PYTHONIOENCODING="utf-8")

    for archivo, motivo in REGRESIONES:
        ruta = Path(__file__).with_name(archivo)
        print(f"\n=== {archivo} ===")
        print(f"    {motivo}")
        if not ruta.is_file():
            print("    FALTA EL ARCHIVO")
            fallos.append(archivo)
            continue

        res = subprocess.run(
            [sys.executable, str(ruta)],
            cwd=str(RAIZ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=entorno,
        )
        salida = (res.stdout or "").strip()
        if salida:
            print("\n".join(f"    {ln}" for ln in salida.splitlines()))
        if res.returncode != 0:
            print("    FAIL")
            print("\n".join(f"    {ln}" for ln in (res.stderr or "").strip().splitlines()))
            fallos.append(archivo)
        else:
            print("    PASS")

    print()
    if fallos:
        print(f"REGRESIONES FAIL ({len(fallos)}/{len(REGRESIONES)}): {', '.join(fallos)}")
        return 1
    print(f"REGRESIONES PASS ({len(REGRESIONES)}/{len(REGRESIONES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
