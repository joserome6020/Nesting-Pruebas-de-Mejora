"""Corre STEPs de W.O. 16 X1 (CAMA LASER cobre) sin el dialogo Qt manual."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from despachador_nocturno import procesar_ruta_nesting

RUTA_NESTING = (
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\Nesteos Locales"
    r"\GIGA\GIGA FLUIDSTACK - COBRE\MODEL CORE FILES\W.O. 16 X1\ARGA MODEL CORE\NESTING"
)


if __name__ == "__main__":
    res = procesar_ruta_nesting(
        RUTA_NESTING,
        calibre_str=None,
        actualizar_bd=False,
        ruta_bd=None,
        cursor=None,
        conexion=None,
    )
    print("RESULTADO:", res)
