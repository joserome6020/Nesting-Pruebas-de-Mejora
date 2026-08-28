"""
Candado SWO-042: hoja con piezas plasma_compensada_manual pero SIN
plasma_compensado_manual a nivel hoja NO debe exportar DXF láser/cama
duplicado (mismas H, cotas 1:1 vs compensadas).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.nesting_engine.efficiency_metrics import (
    hoja_export_solo_plasma,
    hoja_tiene_compensacion_plasma,
    promover_compensacion_plasma_en_hoja,
)
from modules.nesting_engine.exporter import _debe_generar_plasma


def _hoja_piezas_compensadas_sin_flag_hoja():
    # Caso real: transfer/mini-nest dejó flags solo en piezas.
    return {
        "placa_id": "SWO-042-H6",
        "piezas": [
            {"nombre": "PART_A", "plasma_compensada_manual": True, "plasma_offset_mm_manual": 1.5875},
            {"nombre": "PART_B", "plasma_compensada_manual": True},
            {"nombre": "REF__ignorar"},
        ],
    }


def test_pieza_compensada_implica_solo_plasma():
    hoja = _hoja_piezas_compensadas_sin_flag_hoja()
    assert hoja.get("plasma_compensado_manual") is not True
    assert hoja_tiene_compensacion_plasma(hoja) is True
    assert hoja_export_solo_plasma(hoja) is True
    assert _debe_generar_plasma("0.3125_A 36", hoja) is True


def test_promover_sella_flag_hoja():
    hoja = _hoja_piezas_compensadas_sin_flag_hoja()
    assert promover_compensacion_plasma_en_hoja(hoja) is True
    assert hoja.get("plasma_compensado_manual") is True
    assert hoja.get("plasma_piezas_compensadas") == 2
    assert float(hoja.get("plasma_offset_mm_manual") or 0) > 0


def test_hoja_sin_compensacion_sigue_laser():
    hoja = {
        "placa_id": "SWO-042-H99",
        "piezas": [{"nombre": "NORMAL_1"}, {"nombre": "NORMAL_2"}],
    }
    assert hoja_export_solo_plasma(hoja) is False
    assert _debe_generar_plasma("0.3125_A 36", hoja) is False


if __name__ == "__main__":
    test_pieza_compensada_implica_solo_plasma()
    test_promover_sella_flag_hoja()
    test_hoja_sin_compensacion_sigue_laser()
    print("ok")
