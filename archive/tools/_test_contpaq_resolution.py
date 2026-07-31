"""Resolución ContPAQi: equivalencia distinta o match directo mismo código."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from interface.material_code_mapping_service import (
    _SEMILLAS_EQUIVALENCIA_DISTINTA,
    mapeo_es_verificado,
    registrar_matches_directos_catalogo,
    resolver_codigo_contpaq,
    sembrar_equivalencias_verificadas,
)


class ContpaqResolutionTests(unittest.TestCase):
    def test_semillas_incluyen_hr166_tub017(self):
        pares = {
            (s["herinox_codigo"], s["codigo_contpaq"])
            for s in _SEMILLAS_EQUIVALENCIA_DISTINTA
        }
        self.assertIn(("HR164", "TUB010"), pares)
        self.assertIn(("HR166", "TUB017"), pares)

    def test_sembrar_no_corta_en_primera_existente(self):
        cursor = MagicMock()
        # Primera semilla ya existe; segunda no.
        cursor.fetchone.side_effect = [(1,), None]
        with patch(
            "interface.material_code_mapping_service.registrar_equivalencia"
        ) as registrar:
            sembrar_equivalencias_verificadas(cursor)
        self.assertEqual(registrar.call_count, 1)
        kwargs = registrar.call_args.kwargs
        self.assertEqual(kwargs["herinox_codigo"], "HR166")
        self.assertEqual(kwargs["codigo_contpaq"], "TUB017")

    def test_match_directo_registra_mismo_codigo(self):
        cursor = MagicMock()
        with patch(
            "interface.material_code_mapping_service.resolver_equivalencia",
            return_value={
                "herinox_codigo": "ANG022",
                "codigo_contpaq": None,
                "estatus": "PENDING",
                "mapping_id": None,
            },
        ), patch(
            "interface.material_code_mapping_service.registrar_equivalencia"
        ) as registrar:
            out = registrar_matches_directos_catalogo(
                cursor,
                {
                    "ANG022": {
                        "existe": True,
                        "descripcion": "ANGULO A36 2 X 2 X 0.25 IN A 20 FT",
                    }
                },
            )
        self.assertEqual(out["registradas"], 1)
        self.assertEqual(registrar.call_args.kwargs["herinox_codigo"], "ANG022")
        self.assertEqual(registrar.call_args.kwargs["codigo_contpaq"], "ANG022")
        self.assertEqual(registrar.call_args.kwargs["estatus"], "VERIFIED")

    def test_resolver_usa_equivalencia_antes_que_catalogo(self):
        cursor = MagicMock()
        verified = {
            "herinox_codigo": "HR166",
            "codigo_contpaq": "TUB017",
            "estatus": "VERIFIED",
            "mapping_id": 9,
            "origen": "CATALOGO_CONTPAQ_AUDITADO",
        }
        with patch(
            "interface.material_code_mapping_service.resolver_equivalencia",
            return_value=verified,
        ), patch(
            "interface.material_code_mapping_service.registrar_matches_directos_catalogo"
        ) as directos:
            out = resolver_codigo_contpaq(
                cursor,
                "HR166",
                resultados_catalogo={"HR166": {"existe": False}},
            )
        self.assertTrue(mapeo_es_verificado(out))
        self.assertEqual(out["codigo_contpaq"], "TUB017")
        directos.assert_not_called()


if __name__ == "__main__":
    unittest.main()
