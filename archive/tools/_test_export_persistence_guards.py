"""Pruebas sin BD real para guardas de persistencia y lecturas GET."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "interface"))

from api import legacy_core
from interface import postgres_connector


def _resultados_con_rutas(*rutas: str) -> dict:
    return {
        "material": {
            "hojas": [
                {
                    "pqart_exports": [
                        {"ruta": ruta, "nombre_dxf": Path(ruta).name}
                        for ruta in rutas
                    ]
                }
            ]
        }
    }


class _Cursor:
    def __init__(self, fetchone=(0,), fetchall=None):
        self.fetchone_value = fetchone
        self.fetchall_value = fetchall or []
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((str(query), params))

    def fetchone(self):
        return self.fetchone_value

    def fetchall(self):
        return self.fetchall_value

    @property
    def rowcount(self):
        return 0

    def close(self):
        return None


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, **_kwargs):
        return self._cursor

    def close(self):
        return None


class PersistenceGuardTests(unittest.TestCase):
    def test_pqart_rejects_duplicate_route_before_delete(self):
        cursor = _Cursor()
        with self.assertRaisesRegex(RuntimeError, "rutas PQART duplicadas"):
            postgres_connector._guardar_pqart_wo(
                cursor,
                "W.O. 1 X1",
                _resultados_con_rutas(r"C:\x\A.dxf", r"C:\x\A.dxf"),
            )
        self.assertEqual(cursor.executed, [])

    def test_pqart_protects_wo_already_absorbed_by_swo(self):
        cursor = _Cursor(fetchone=(1,))
        with self.assertRaisesRegex(RuntimeError, "SWO procesada"):
            postgres_connector._guardar_pqart_wo(
                cursor,
                "W.O. 1 X1",
                _resultados_con_rutas(r"C:\x\A.dxf"),
            )
        self.assertFalse(any("DELETE" in query.upper() for query, _ in cursor.executed))

    def test_member_wo_mrl_is_skipped_when_swo_is_canonical(self):
        cursor = _Cursor(fetchone=("SWO-001",))
        ok, message = legacy_core._asegurar_material_requerido_orden(
            cursor, "W.O. 1 X1", "WO"
        )
        self.assertTrue(ok)
        self.assertIn("canónico es SWO", message)

    def test_lista_largos_get_does_not_generate_or_commit_material(self):
        cursor = _Cursor()
        connection = _Connection(cursor)
        jobs = [{"job": "251007", "work_order": "W.O. 1 X1"}]
        rows = [{"job": "251007", "work_order": "W.O. 1 X1"}]
        with (
            patch("api.legacy_core.db_connect", return_value=connection),
            patch("api.legacy_core._obtener_jobs_de_wo", return_value=jobs),
            patch("api.legacy_core._expandir_lista_para_wo", return_value=rows),
            patch(
                "api.legacy_core._asegurar_material_requerido_orden",
                side_effect=AssertionError("GET no debe generar MRL"),
            ),
        ):
            response = legacy_core.construir_lista_largos_wo("W.O. 1 X1")
        self.assertEqual(response["total_registros"], 1)


if __name__ == "__main__":
    unittest.main()
