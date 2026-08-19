"""Candado 2026-08-19t — offset plasma 0.0625\" por lado en TODOS los calibres.

Planta: el stock de corte plasma es 1/16\" por lado (el largo/ancho crecen
1/8\"). Antes la regla partía en 0.75\": fino 0.0125\" y grueso 0.250\".
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from modules.plasma_compensator import compute_plasma_offset_mm  # noqa: E402

OFFSET_IN = 0.0625
OFFSET_MM = OFFSET_IN * 25.4


def test_offset_0625_fino_y_grueso() -> None:
    for thk in (0.0747, 0.25, 0.375, 0.75, 0.751, 1.0, 2.0):
        got = compute_plasma_offset_mm(thk)
        assert abs(got - OFFSET_MM) < 1e-9, (thk, got, OFFSET_MM)


def test_regla_unica_en_fuente() -> None:
    src = (RAIZ / "modules" / "plasma_compensator.py").read_text(encoding="utf-8")
    fn = src.split("def compute_plasma_offset_mm", 1)[1].split("\ndef ", 1)[0]
    assert "0.0625" in fn
    assert "0.0125" not in fn
    assert "0.250" not in fn
    assert "> 0.75" not in fn


def test_despachador_reusa_la_regla() -> None:
    src = (RAIZ / "despachador_nocturno.py").read_text(encoding="utf-8")
    assert "compute_plasma_offset_mm" in src
    assert "0.250 if espesor" not in src
    assert "else 0.0125" not in src


if __name__ == "__main__":
    test_offset_0625_fino_y_grueso()
    test_regla_unica_en_fuente()
    test_despachador_reusa_la_regla()
    print("OK plasma_offset_todos_calibres")
