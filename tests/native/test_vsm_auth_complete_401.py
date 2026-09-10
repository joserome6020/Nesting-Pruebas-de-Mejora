"""Candado: VSM :8010 /complete exige auth; WO ya fusionadas no tumban export.

Caso real 2026-09-10 — 9919-BOARD1 / SWO-059:
- PATCH /jobs/{id}/complete → HTTP 401 {"detail":"No autenticado"}
- Las 4 WO ya estaban en Pendiente SWO (SWO-059…062)
- El export abortaba en VSM_JOB aunque CAD/Postgres/MRL estaban OK
"""
from __future__ import annotations

import sys
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.nesting_engine import api_client


class _FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, code: int = 401, body: bytes = b'{"detail":"No autenticado"}'):
        super().__init__(
            url="http://192.168.2.80:8010/jobs/71/complete",
            code=code,
            msg="Unauthorized",
            hdrs=None,
            fp=BytesIO(body),
        )


class VsmAuthComplete401Tests(unittest.TestCase):
    def tearDown(self):
        api_client._CENTRALIZED_BEARER = None
        api_client._CENTRALIZED_LOGIN_OK = False

    def test_load_creds_from_env(self):
        with patch.dict(
            "os.environ",
            {
                "CENTRALIZED_AUTH_EMAIL": "ans@test.local",
                "CENTRALIZED_AUTH_PASSWORD": "secret",
            },
            clear=False,
        ):
            email, password = api_client._load_centralized_creds()
        self.assertEqual(email, "ans@test.local")
        self.assertEqual(password, "secret")

    def test_historial_fusion_skips_complete(self):
        with patch(
            "modules.nesting_engine.api_client.resolver_job_centralizado",
            return_value=(
                "9919-BOARD1",
                {
                    "id": 71,
                    "status": "inventor",
                    "history": [
                        {
                            "stage": "nesting",
                            "notes": "Fusión parcial a SWO-059",
                            "end_time": None,
                        }
                    ],
                },
            ),
        ), patch(
            "modules.nesting_engine.api_client._patch_json"
        ) as patch_json:
            result = api_client.avanzar_job_centralizado("9919-BOARD1")
        self.assertTrue(result)
        patch_json.assert_not_called()
        self.assertIn("fusion", result.detail.lower())

    @patch(
        "modules.nesting_engine.api_client.job_nesting_totalmente_fusionado",
        return_value=True,
    )
    @patch("modules.nesting_engine.api_client._patch_json")
    @patch(
        "modules.nesting_engine.api_client.resolver_job_centralizado",
        return_value=("9919-BOARD1", {"id": 71, "status": "inventor"}),
    )
    def test_nesting_fused_skips_complete_before_patch(
        self, _resolver, patch_json, _fused
    ):
        result = api_client.avanzar_job_centralizado("9919-BOARD1")
        self.assertTrue(result)
        patch_json.assert_not_called()
        self.assertIn("fusion", result.detail.lower())

    @patch(
        "modules.nesting_engine.api_client.job_nesting_totalmente_fusionado",
        return_value=False,
    )
    @patch(
        "modules.nesting_engine.api_client._patch_json",
        side_effect=_FakeHTTPError(401),
    )
    @patch(
        "modules.nesting_engine.api_client.resolver_job_centralizado",
        return_value=("9919-BOARD1", {"id": 71, "status": "inventor"}),
    )
    def test_complete_401_fails_when_not_fused(
        self, _resolver, _patch_json, _fused
    ):
        result = api_client.avanzar_job_centralizado("9919-BOARD1")
        self.assertFalse(result)
        self.assertEqual(result.http_status, 401)
        self.assertIn("401", result.detail)

    def test_http_error_401_is_not_retryable(self):
        self.assertFalse(api_client._is_retryable_http_error(_FakeHTTPError(401)))
        self.assertTrue(api_client._is_retryable_http_error(_FakeHTTPError(503)))

    def test_ensure_session_false_without_creds(self):
        with patch.dict(
            "os.environ",
            {"CENTRALIZED_AUTH_EMAIL": "", "CENTRALIZED_AUTH_PASSWORD": ""},
            clear=False,
        ):
            with patch(
                "modules.nesting_engine.api_client._centralized_auth_settings_path",
                return_value=Path("__no_such_centralized_auth__.json"),
            ):
                self.assertFalse(api_client.ensure_centralized_session(force=True))


if __name__ == "__main__":
    unittest.main()
