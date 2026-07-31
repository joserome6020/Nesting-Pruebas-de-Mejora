"""Regresión: una SWO no puede emitir MRL desde un plan parcial."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "interface"))

from interface import largos_nesting_service as service


class SwoCanonicalMrlTests(unittest.TestCase):
    def test_uses_persisted_canonical_plan_when_memory_is_partial(self):
        partial = {"data": {"A": [{"cortes": [{}]}]}, "total_piezas": 65, "total_barras": 11}
        canonical = {
            "data": {"A": [{"cortes": [{}]}]},
            "total_piezas": 715,
            "total_barras": 55,
        }
        app = SimpleNamespace(
            plan_largos_por_lote={0: partial},
            plan_largos_sin_demanda_por_lote=set(),
            exclusiones_mrl_unidades_por_lote={0: set()},
        )
        with (
            patch.object(service, "cargar_plan_largos", return_value=canonical),
            patch.object(service, "previsualizar_pedido_mrl_unidades", return_value=[]),
            patch.object(
                service,
                "enviar_pedido_largos_filtrado",
                return_value=(True, "Pedido regenerado"),
            ) as enviar,
        ):
            ok, mensaje = service.aplicar_pedido_largos_swo_acumulado_tras_export(
                app, "SWO-001", [0]
            )
        self.assertTrue(ok)
        self.assertIn("715 piezas", mensaje)
        self.assertEqual(enviar.call_args.args[2], canonical)


if __name__ == "__main__":
    unittest.main()
