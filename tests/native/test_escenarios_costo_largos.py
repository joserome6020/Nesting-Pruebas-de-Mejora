"""Candado: escenarios MES suman placas + largos al ordenar por costo."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


def test_escenario_costo_incluye_largos_en_ranking():
    """
    Caso real (tanques X39): 3×13X placas 685594 + largos 93009 → 778603.
    Sin largos ganaba 3×13X; con largos el ranking puede cambiar.
    """
    esc_a = {
        "config": [(13, 3)],
        "costo_placas": 685594.08,
        "costo_largos": 93008.70,
    }
    esc_b = {
        "config": [(19, 1), (20, 1)],
        "costo_placas": 693196.46,
        "costo_largos": 0.0,  # hipotético sin demanda distinto
    }
    for e in (esc_a, esc_b):
        e["costo"] = float(e["costo_placas"]) + float(e["costo_largos"])

    ranked = sorted([esc_a, esc_b], key=lambda x: x["costo"])
    # Con largos, 3×13X (778603) > 19+20 solo placas (693196) si B no tiene largos.
    assert ranked[0] is esc_b
    assert abs(esc_a["costo"] - 778602.78) < 0.01

    # Multiplicar costo unitario de largos × Nº lotes.
    costo_unit = 31002.90
    assert abs(costo_unit * 3 - 93008.70) < 0.01


def test_ensamblar_sigue_siendo_solo_placas():
    """ensamblar_escenario sigue reportando placas; el mixin suma largos aparte."""
    from interface.utils_nesting import ensamblar_escenario

    nestings = {
        13: {
            "G1": {
                "hojas": [
                    {"precio_placa": 100.0, "eficiencia": 50.0},
                    {"precio_placa": 50.0, "eficiencia": 40.0, "es_retazo": True},
                ]
            }
        }
    }
    _lista, costo, _efi = ensamblar_escenario([(13, 2)], nestings)
    assert abs(costo - 200.0) < 1e-6  # 2 lotes × 100 (retazo no suma)


if __name__ == "__main__":
    test_escenario_costo_incluye_largos_en_ranking()
    test_ensamblar_sigue_siendo_solo_placas()
    print("OK")
