"""Pruebas de reanudación sin CAD para checkpoints de centralización."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "interface"))

from interface.qt.tabs._mixin_export import ExportMixin, ExportStageError
from modules.nesting_engine.api_client import ApiOperationResult


class _Cursor:
    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Connection:
    def cursor(self):
        return _Cursor()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _SwoCursor(_Cursor):
    def __init__(self):
        self._reads = 0

    def fetchall(self):
        self._reads += 1
        return [("251008",)] if self._reads == 1 else []


class _SwoConnection(_Connection):
    def cursor(self):
        return _SwoCursor()


class _Tab(ExportMixin):
    def __init__(self):
        self.app = SimpleNamespace(resultados_multilote=[])


class CheckpointFlowTests(unittest.TestCase):
    def _run(
        self,
        *,
        checkpoint_ok,
        vsm_result=None,
    ):
        if vsm_result is None:
            vsm_result = ApiOperationResult(True, "vsm", "251007", "ok", 204)
        writes = []
        with (
            patch("psycopg2.connect", return_value=_Connection()),
            patch(
                "interface.qt.tabs._mixin_export.checkpoint_export_ok",
                side_effect=checkpoint_ok,
            ),
            patch(
                "interface.qt.tabs._mixin_export.guardar_checkpoint_export",
                side_effect=lambda *a, **k: writes.append((a, k)),
            ),
            patch(
                "modules.nesting_engine.api_client.avanzar_job_centralizado",
                return_value=vsm_result,
            ) as vsm,
            patch(
                "modules.nesting_engine.api_client.trigger_po_contpaq",
            ) as contpaq,
        ):
            tab = _Tab()
            tab._centralizar_exportacion_confirmada(
                db_conf={"host": "fake"},
                job_activo="251007",
                es_swo=False,
                run_id="test-run",
            )
        return writes, vsm, contpaq

    def test_normal_wo_omits_contpaq_and_persists_policy_checkpoint(self):
        writes, vsm, contpaq = self._run(checkpoint_ok=lambda *_a, **_k: False)
        vsm.assert_called_once_with("251007")
        contpaq.assert_not_called()
        self.assertEqual([entry[0][2] for entry in writes], ["VSM_JOB:251007", "CONTPAQ"])
        self.assertTrue(all(entry[1]["status"] == "OK" for entry in writes))
        self.assertIn("la SWO", writes[1][1]["detail"])

    def test_completed_stages_skip_repeated_external_calls(self):
        writes, vsm, contpaq = self._run(checkpoint_ok=lambda *_a, **_k: True)
        self.assertEqual([entry[0][2] for entry in writes], ["CONTPAQ"])
        vsm.assert_not_called()
        contpaq.assert_not_called()

    def test_swo_preflight_failure_blocks_vsm_and_purchase(self):
        writes = []
        preflight_failure = ApiOperationResult(
            False,
            "preflight ContPAQ SWO",
            "SWO-002",
            "HR166 pendiente",
            500,
        )
        with (
            patch("psycopg2.connect", return_value=_SwoConnection()),
            patch(
                "interface.qt.tabs._mixin_export.checkpoint_export_ok",
                return_value=False,
            ),
            patch(
                "interface.qt.tabs._mixin_export.guardar_checkpoint_export",
                side_effect=lambda *a, **k: writes.append((a, k)),
            ),
            patch(
                "modules.nesting_engine.api_client.validar_po_contpaq",
                return_value=preflight_failure,
            ) as preflight,
            patch("modules.nesting_engine.api_client.avanzar_job_centralizado") as vsm_job,
            patch("modules.nesting_engine.api_client.avanzar_swo_centralizado") as vsm_swo,
            patch("modules.nesting_engine.api_client.trigger_po_contpaq") as contpaq,
        ):
            with self.assertRaisesRegex(ExportStageError, r"\[CONTPAQ_PREFLIGHT\]"):
                _Tab()._centralizar_exportacion_confirmada(
                    db_conf={"host": "fake"},
                    job_activo="SWO-002",
                    es_swo=True,
                    run_id="test-run",
                )

        preflight.assert_called_once_with("SWO-002")
        vsm_job.assert_not_called()
        vsm_swo.assert_not_called()
        contpaq.assert_not_called()
        self.assertEqual(writes[0][0][2], "CONTPAQ_PREFLIGHT")
        self.assertEqual(writes[0][1]["status"], "FAILED")

if __name__ == "__main__":
    unittest.main()
