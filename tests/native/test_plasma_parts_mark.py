"""Smoke: compensación plasma desde PARTS (flags + split + offset)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shapely.geometry import box

from modules.plasma_compensator import (
    aplicar_compensacion_poligono,
    compute_plasma_offset_mm,
)
from modules.nesting_engine.manager import MotorNesting


def test_offset_regla():
    assert abs(compute_plasma_offset_mm(0.25) - 0.0125 * 25.4) < 1e-9
    assert abs(compute_plasma_offset_mm(1.0) - 0.250 * 25.4) < 1e-9


def test_buffer_aplica():
    base = box(0.0, 0.0, 100.0, 40.0)
    off = compute_plasma_offset_mm(0.5)
    comp = aplicar_compensacion_poligono(base, off)
    assert comp is not None and not comp.is_empty
    assert float(comp.area) > float(base.area)


def test_split_jobs_plasma():
    piezas = [
        {"nombre": "A", "plasma_compensada_manual": True},
        {"nombre": "B"},
        {"nombre": "C", "plasma_compensada_manual": True},
    ]
    normal, plasma = MotorNesting._partir_piezas_plasma(piezas)
    assert len(plasma) == 2 and len(normal) == 1
    jobs = MotorNesting._jobs_acero_separando_plasma([("0.5_A 36", piezas)])
    assert len(jobs) == 2
    assert jobs[0][2] is True and len(jobs[0][1]) == 2
    assert jobs[1][2] is False and len(jobs[1][1]) == 1


def test_marcar_lote_plasma():
    res = {
        "hojas": [
            {
                "piezas": [
                    {"nombre": "P1", "plasma_offset_mm_manual": 3.175},
                    {"nombre": "REF__x"},
                ]
            }
        ]
    }
    out = MotorNesting._marcar_resultado_lote_plasma(res)
    h = out["hojas"][0]
    assert h.get("plasma_compensado_manual") is True
    assert h["piezas"][0].get("plasma_compensada_manual") is True
    assert not h["piezas"][1].get("plasma_compensada_manual")


def test_ruta_plasma_compensado():
    from modules.plasma_compensator import ruta_dxf_plasma_compensado

    p = ruta_dxf_plasma_compensado(r"C:\job\Processed Files\FOO.dxf")
    assert p.name == "FOO.dxf"
    assert p.parent.name == "Plasma Compensated"


if __name__ == "__main__":
    test_offset_regla()
    test_buffer_aplica()
    test_split_jobs_plasma()
    test_marcar_lote_plasma()
    test_ruta_plasma_compensado()
    print("ok")
