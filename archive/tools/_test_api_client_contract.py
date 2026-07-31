"""Pruebas de contrato HTTP para centralización VSM/ContPAQ."""
from __future__ import annotations

import sys
import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.nesting_engine import api_client


class ApiClientContractTests(unittest.TestCase):
    def test_web_report_serializes_geometry_protocol(self):
        class PolygonLike:
            __geo_interface__ = {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
            }

        payload = json.dumps({"geometry": PolygonLike()}, default=api_client._json_export_safe)
        self.assertIn('"type": "Polygon"', payload)

    def test_http_ok_accepts_all_2xx(self):
        for code in (200, 201, 202, 204, 299):
            self.assertTrue(api_client._http_ok(code))
        for code in (199, 300, None):
            self.assertFalse(api_client._http_ok(code))

    def test_dashboard_completion_flag_beats_inventor_status(self):
        self.assertTrue(
            api_client._job_ingenieria_finalizada(
                {"status": "inventor", "engineering_completed": True}
            )
        )
        self.assertTrue(
            api_client._job_ingenieria_finalizada(
                {"status": "inventor", "stage": "engineering_complete"}
            )
        )
        self.assertTrue(
            api_client._job_ingenieria_finalizada(
                {"status": "inventor", "dxf_count": 0}
            )
        )
        self.assertFalse(api_client._job_ingenieria_finalizada({"status": "inventor"}))

    @patch("modules.nesting_engine.api_client.time.sleep")
    @patch("modules.nesting_engine.api_client._leer_job_centralizado")
    @patch("modules.nesting_engine.api_client._patch_json", return_value=204)
    @patch("modules.nesting_engine.api_client.resolver_job_centralizado")
    def test_complete_204_is_accepted_when_readback_is_legacy(
        self,
        resolver,
        patch_json,
        leer_job,
        _sleep,
    ):
        resolver.return_value = ("251007", {"id": 1, "status": "inventor"})
        leer_job.return_value = {"id": 1, "job_number": "251007", "status": "inventor"}

        result = api_client.avanzar_job_centralizado("251007")

        self.assertTrue(result)
        self.assertEqual(result.http_status, 204)
        patch_json.assert_called_once()

    @patch("modules.nesting_engine.api_client._post_json", return_value=(204, {}))
    def test_swo_204_is_accepted(self, post_json):
        result = api_client.avanzar_swo_centralizado("SWO-001")
        self.assertTrue(result)
        self.assertEqual(result.http_status, 204)
        post_json.assert_called_once()

    @patch("modules.nesting_engine.api_client._post_json", return_value=(204, {}))
    def test_swo_contpaq_204_is_accepted(self, post_json):
        result = api_client.trigger_po_contpaq("SWO-001")
        self.assertTrue(result)
        self.assertEqual(result.http_status, 204)
        post_json.assert_called_once()

    @patch(
        "modules.nesting_engine.api_client.verificar_contrato_contpaq",
        return_value=api_client.ApiOperationResult(
            True, "contrato InsertaPO", "http://test", "Contrato OK"
        ),
    )
    @patch(
        "modules.nesting_engine.api_client._post_json",
        return_value=(200, {"valid": True, "detail": "Catálogo OK"}),
    )
    def test_swo_contpaq_validate_accepts_catalog(self, post_json, _contrato):
        result = api_client.validar_po_contpaq("SWO-001")
        self.assertTrue(result)
        self.assertEqual(result.operation, "preflight ContPAQ SWO")
        self.assertEqual(result.response, {"valid": True, "detail": "Catálogo OK"})
        post_json.assert_called_once()

    @patch(
        "modules.nesting_engine.api_client.verificar_contrato_contpaq",
        return_value=api_client.ApiOperationResult(
            True, "contrato InsertaPO", "http://test", "Contrato OK"
        ),
    )
    @patch(
        "modules.nesting_engine.api_client._post_json",
        return_value=(500, {"detail": "Equivalencias pendientes: HR166"}),
    )
    def test_swo_contpaq_validate_preserves_mapping_failure(self, post_json, _contrato):
        result = api_client.validar_po_contpaq("SWO-001")
        self.assertFalse(result)
        self.assertEqual(result.http_status, 500)
        self.assertIn("HR166", result.detail)
        post_json.assert_called_once()

    @patch(
        "modules.nesting_engine.api_client._get_json",
        return_value={"paths": {"/validate": {"post": {}}}},
    )
    def test_contpaq_contract_rejects_old_image(self, get_json):
        result = api_client.verificar_contrato_contpaq()

        self.assertFalse(result)
        self.assertIn("/catalog/verify-codes", result.detail)
        get_json.assert_called_once()

    @patch(
        "modules.nesting_engine.api_client._get_json",
        return_value={
            "paths": {
                "/validate": {"post": {}},
                "/catalog/verify-codes": {"post": {}},
                "/catalog/search": {"post": {}},
            }
        },
    )
    def test_contpaq_contract_accepts_mapping_image(self, get_json):
        result = api_client.verificar_contrato_contpaq()

        self.assertTrue(result)
        self.assertIn("equivalencias", result.detail)
        get_json.assert_called_once()

    @patch("modules.nesting_engine.api_client._post_json")
    def test_wo_contpaq_is_intentionally_omitted(self, post_json):
        result = api_client.trigger_pedido_po("251007")
        self.assertTrue(result)
        self.assertIsNone(result.http_status)
        self.assertEqual(result.response, {"po_scope": "SWO_ONLY", "omitted": True})
        post_json.assert_not_called()

    @patch("modules.nesting_engine.api_client.urllib.request.urlopen")
    @patch("modules.nesting_engine.api_client._listar_jobs_centralizado", return_value=[])
    def test_normal_wo_preflight_does_not_require_contpaq(self, _jobs, urlopen):
        result = api_client.preflight_servicios_centralizados(es_swo=False)
        self.assertTrue(result)
        self.assertIn("no aplica", result.detail)
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
