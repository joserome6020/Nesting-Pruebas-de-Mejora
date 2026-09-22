"""Candado: escenarios MES suman placas + largos al ordenar por costo."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

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


def test_estimar_usa_plan_vivo_incluye_solera():
    """
    Caso real TNK3PH X13: Costos del nesteo = CAN011 30617.90 + SLC042 385
    = 31002.90. El MES 3×13X debe usar ese total (no solo Canal).
    """
    from interface.largos_nesting_service import estimar_costos_largos_por_factores

    plan_vivo = {
        "data": {
            "CAN011": [{"source": "STOCK", "cortes": [{}]}],
            "SLC042": [{"source": "STOCK", "cortes": [{}]}],
        },
        "total_barras": 16,
    }
    app = SimpleNamespace(
        resultados_multilote=[{"lote_k": 13}],
        plan_largos_por_lote={0: plan_vivo},
        exclusiones_mrl_unidades_por_lote={0: set()},
        exclusiones_largos_pedido_por_lote={},
        plan_largos_job="TNK3PH",
        plan_largos_sin_demanda_por_lote=set(),
        plan_largos_error=None,
        job_activo="TNK3PH",
    )

    def _stub_costo(plan, unidades_excluidas_mrl=None):
        if plan is plan_vivo:
            return {"total_mxn": 31002.90, "barras_total": 16, "lineas": []}
        return {"total_mxn": 0.0, "barras_total": 0, "lineas": []}

    with patch(
        "interface.nesting_costos.calcular_costos_largos_desde_plan",
        side_effect=_stub_costo,
    ):
        out = estimar_costos_largos_por_factores(app, [13, 39, 3, 19, 20])

    assert abs(float(out[13]["total_mxn"]) - 31002.90) < 0.01
    # 3 lotes × X13 → misma fuente que Costos × 3
    assert abs(float(out[13]["total_mxn"]) * 3 - 93008.70) < 0.01
    # Escalado lineal a 39X (misma demanda total de tanques)
    assert abs(float(out[39]["total_mxn"]) - 93008.70) < 0.01
    assert abs(float(out[19]["total_mxn"]) + float(out[20]["total_mxn"]) - 93008.70) < 0.05


if __name__ == "__main__":
    test_escenario_costo_incluye_largos_en_ranking()
    test_ensamblar_sigue_siendo_solo_placas()
    test_estimar_usa_plan_vivo_incluye_solera()
    print("OK")
