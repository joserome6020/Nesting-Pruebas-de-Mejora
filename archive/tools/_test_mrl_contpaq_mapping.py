"""Contratos de equivalencia MRL -> SKU ContPAQ, sin bases externas."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTPAQ_SOURCE = Path(
    r"\\192.168.2.80\Users\Administrator\Music\Desarrollo\InsertaPOContPaq"
)
sys.path.insert(0, str(CONTPAQ_SOURCE))

from mrl_po import MaterialCodeMappingError, construir_lineas_largos


def _fila(**override):
    base = {
        "id": 1,
        "codigo": "HR164",
        "codigo_herinox": "HR164",
        "codigo_contpaq": "TUB010",
        "codigo_contpaq_estatus": "VERIFIED",
        "material": "HR164 | TUBO perfil | A 36 | TUBO A36 CED 40 5 IN",
        "largo": 240,
        "cantidad": 1,
        "costo": 130,
        "kit_recibido": False,
        "provider_handshake_at": None,
        "almacen_received_at": None,
        "incoming_handshake_at": None,
        "rechazado_incoming": False,
    }
    base.update(override)
    return base


class MrlContpaqMappingTests(unittest.TestCase):
    def test_verified_mapping_uses_contpaq_sku_and_keeps_herinox_trace(self):
        lineas, excluidas = construir_lineas_largos([_fila()])
        self.assertEqual(excluidas, [])
        self.assertEqual(len(lineas), 1)
        self.assertEqual(lineas[0]["codigo"], "TUB010")
        self.assertEqual(lineas[0]["herinox_codigos"], ["HR164"])
        self.assertIn("HERINOX HR164", lineas[0]["descripcion_po"])

    def test_pending_mapping_blocks_without_any_line(self):
        with self.assertRaises(MaterialCodeMappingError) as ctx:
            construir_lineas_largos(
                [
                    _fila(
                        id=7,
                        codigo_contpaq=None,
                        codigo_contpaq_estatus="PENDING",
                    )
                ]
            )
        self.assertEqual(ctx.exception.issues[0]["mrl_id"], 7)
        self.assertEqual(ctx.exception.issues[0]["codigo_herinox"], "HR164")

    def test_operational_row_is_excluded_before_mapping_check(self):
        lineas, excluidas = construir_lineas_largos(
            [
                _fila(
                    id=9,
                    codigo_contpaq=None,
                    codigo_contpaq_estatus="PENDING",
                    kit_recibido=True,
                )
            ]
        )
        self.assertEqual(lineas, [])
        self.assertEqual(excluidas, [9])


if __name__ == "__main__":
    unittest.main()
