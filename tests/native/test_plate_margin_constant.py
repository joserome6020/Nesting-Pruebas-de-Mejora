"""Candado dimensional: placa→pieza siempre conserva el margen de la tabla."""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from modules.nesting_engine.engine_registry import (  # noqa: E402
    _request_con_margen_final_placa,
)
from modules.nesting_engine.engines.types import PackSheetRequest  # noqa: E402


def _request(*, kerf: float, margin: float, limite_poly=None) -> PackSheetRequest:
    return PackSheetRequest(
        piezas=[],
        w_placa=1000.0,
        h_placa=2000.0,
        kerf_override=kerf,
        margin_override=margin,
        limite_poly=limite_poly,
    )


def test_margen_final_placa_es_constante_0250():
    """El packer recibe 0.250\" de metal, no 0.250-kerf/2 (eso era 4.85 mm)."""
    for engine, kerf in (
        ("svgnest_ultra", 0.100),
        ("arga_force", 0.150),
        ("burke_blf", 0.250),
        ("libnest2d", 0.313),
        ("arga_apex", 0.375),
        ("arga_lite", 0.375),
    ):
        packed = _request_con_margen_final_placa(
            _request(kerf=kerf, margin=0.250),
            engine,
        )
        assert packed.margin_override == 0.250, (engine, packed.margin_override, kerf)


def test_limite_irregular_conserva_margen_exacto():
    """RTZ/huecos se validan con geometría exacta y no usan la corrección."""
    request = _request(kerf=0.150, margin=0.250, limite_poly=object())
    packed = _request_con_margen_final_placa(request, "svgnest_ultra")
    assert packed is request
    assert packed.margin_override == 0.250


def test_motor_experimental_no_cambia_su_contrato():
    request = _request(kerf=0.150, margin=0.250)
    packed = _request_con_margen_final_placa(request, "arga_lab_pilot")
    assert packed is request
    assert packed.margin_override == 0.250


if __name__ == "__main__":
    test_margen_final_placa_es_constante_0250()
    test_limite_irregular_conserva_margen_exacto()
    test_motor_experimental_no_cambia_su_contrato()
    print("SMOKE OK")
