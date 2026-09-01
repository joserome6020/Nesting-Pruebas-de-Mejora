"""Candado: conteo DXF export = barra de progreso (CyPTube solo Corte)."""
from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.nesting_engine.exporter import (  # noqa: E402
    _dxfs_por_hoja_en_export,
    estimar_conteos_export,
)


def _hoja_sin_gap() -> dict:
    return {
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "sin_gap",
        "export_3d_format": "dxf",
        "placa_w": 3657.6,
        "placa_h": 101.6,
    }


def test_estimar_dxf_sin_gap_solo_corte():
    res = {"0.25_CU": {"hojas": [_hoja_sin_gap()] * 200}}
    d_on, _ = estimar_conteos_export(res, generar_step=False, cu_sin_marcaje=True)
    d_off, _ = estimar_conteos_export(res, generar_step=False, cu_sin_marcaje=False)
    assert d_on == 200
    assert d_off == 400
    assert _dxfs_por_hoja_en_export("0.25_CU", _hoja_sin_gap(), cu_sin_marcaje=True) == 1
    assert _dxfs_por_hoja_en_export("0.25_CU", _hoja_sin_gap(), cu_sin_marcaje=False) == 2


if __name__ == "__main__":
    test_estimar_dxf_sin_gap_solo_corte()
    print("[OK] export DXF count estimate")
