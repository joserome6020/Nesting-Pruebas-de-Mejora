"""Candado: auditoría STEP cobre no inventa 'DXF con 3D' fuera del manifiesto.

Bug real (W.O. GIGA cobre / VM): carpeta NESTEOS DE COBRE/DXF con muchos
leftovers (p.ej. 102). OCCT solo convierte lo que viene en cu_formato_por_dxf;
si el mapa está vacío (sin_gap / CyPTube) o no lista un archivo, debe tratarlo
como solo-DXF. El código viejo usaba default 'step' → '102 DXF con 3D, 0 STEP'
falso y abortaba el export.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from modules.nesting_engine.exporter import (  # noqa: E402
    RUTA_NESTEOS_COBRE,
    _auditar_steps_en_rutas,
    _validar_steps_tras_export,
)


def _touch(path: str, size: int = 64) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"0" * size)


def test_audit_cobre_sin_fmt_map_no_exige_step():
    with tempfile.TemporaryDirectory() as tmp:
        dxf_dir = os.path.join(tmp, RUTA_NESTEOS_COBRE, "DXF")
        step_dir = os.path.join(tmp, RUTA_NESTEOS_COBRE, "STEP")
        os.makedirs(dxf_dir, exist_ok=True)
        os.makedirs(step_dir, exist_ok=True)
        for i in range(5):
            _touch(os.path.join(dxf_dir, f"H{i}.dxf"))
        rutas = {
            "nesteos_cobre_dxf": dxf_dir,
            "nesteos_cobre_step": step_dir,
            "nestee_dxf": os.path.join(tmp, "NESTEO DXF", "DXF"),
            "nestee_step": os.path.join(tmp, "NESTEO DXF", "STEP"),
        }
        resumen = _auditar_steps_en_rutas(rutas, cu_formato_por_dxf={})
        cob = resumen["NESTEOS DE COBRE"]
        assert cob["dxf"] == 5
        assert cob["dxf_3d"] == 0
        assert cob["step"] == 0
        # No debe lanzar: cobre solo-DXF / sin manifiesto 3D
        _validar_steps_tras_export(
            rutas, cu_formato_por_dxf={}, motor_3d="occt", log_fn=lambda _m: None
        )


def test_audit_cobre_leftovers_default_dxf_no_step():
    """Archivos fuera del mapa no cuentan como con 3D (default dxf, no step)."""
    with tempfile.TemporaryDirectory() as tmp:
        dxf_dir = os.path.join(tmp, RUTA_NESTEOS_COBRE, "DXF")
        step_dir = os.path.join(tmp, RUTA_NESTEOS_COBRE, "STEP")
        os.makedirs(dxf_dir, exist_ok=True)
        os.makedirs(step_dir, exist_ok=True)
        _touch(os.path.join(dxf_dir, "BARRA_OK.dxf"))
        _touch(os.path.join(dxf_dir, "LEFTOVER_A.dxf"))
        _touch(os.path.join(dxf_dir, "LEFTOVER_B.dxf"))
        # STEP presente solo para la barra del manifiesto
        _touch(os.path.join(step_dir, "BARRA_OK.step"), size=1024)
        rutas = {
            "nesteos_cobre_dxf": dxf_dir,
            "nesteos_cobre_step": step_dir,
            "nestee_dxf": os.path.join(tmp, "NESTEO DXF", "DXF"),
            "nestee_step": os.path.join(tmp, "NESTEO DXF", "STEP"),
        }
        fmt = {"BARRA_OK.dxf": "step"}
        resumen = _auditar_steps_en_rutas(rutas, cu_formato_por_dxf=fmt)
        cob = resumen["NESTEOS DE COBRE"]
        assert cob["dxf"] == 3
        assert cob["dxf_3d"] == 1, cob
        assert cob["step"] == 1, cob
        _validar_steps_tras_export(
            rutas, cu_formato_por_dxf=fmt, motor_3d="occt", log_fn=lambda _m: None
        )


def main() -> None:
    test_audit_cobre_sin_fmt_map_no_exige_step()
    test_audit_cobre_leftovers_default_dxf_no_step()
    print("OK test_cobre_step_audit_fmt_map")


if __name__ == "__main__":
    main()
