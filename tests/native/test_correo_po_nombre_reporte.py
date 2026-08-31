"""Candado SWO-047: PO ContPAQ OK con nombreReporte vacío = correo NO enviado.

Caso real 2026-08-31 GAM 13040: InsertaPO creó la OC + PDF y devolvió HTTP 200
con ``nombreReporte=""`` porque falló el SMTP. El ANS trataba eso como éxito
total y el correo no salió a la primera.

Reglas:
- ``correo_po_confirmado`` es False si falta nombreReporte.
- ``trigger_po_contpaq`` sigue ``ok=True`` (la OC existe; no re-disparar /run).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from modules.nesting_engine.api_client import (  # noqa: E402
    ApiOperationResult,
    correo_po_confirmado,
    nombre_reporte_po,
    trigger_po_contpaq,
)


def test_nombre_reporte_po_vacio_y_presente():
    assert nombre_reporte_po(None) == ""
    assert nombre_reporte_po({}) == ""
    assert nombre_reporte_po({"nombreReporte": ""}) == ""
    assert nombre_reporte_po({"nombreReporte": "  "}) == ""
    assert nombre_reporte_po({"nombreReporte": "PO_GAM_13040.pdf"}) == "PO_GAM_13040.pdf"
    assert nombre_reporte_po({"nombre_reporte": "PO_GAM_1.pdf"}) == "PO_GAM_1.pdf"


def test_correo_po_confirmado_exige_nombre():
    ok_sin = ApiOperationResult(
        True, "pedido ContPAQ SWO", "S.W.O 47 X1", "{}", 200, {"nombreReporte": ""}
    )
    ok_con = ApiOperationResult(
        True,
        "pedido ContPAQ SWO",
        "S.W.O 47 X1",
        "{}",
        200,
        {"nombreReporte": "PO_GAM_13040.pdf"},
    )
    fail = ApiOperationResult(False, "pedido ContPAQ SWO", "x", "boom", 500, None)
    assert correo_po_confirmado(ok_sin) is False
    assert correo_po_confirmado(ok_con) is True
    assert correo_po_confirmado(fail) is False
    assert correo_po_confirmado(None) is False


def test_trigger_po_ok_aunque_nombre_reporte_vacio():
    """HTTP 200 + OC creada → ok=True aunque el correo no se confirmó."""
    body = {"execution_time": 1.66, "nombreReporte": ""}
    with patch(
        "modules.nesting_engine.api_client._post_json",
        return_value=(200, body),
    ):
        resultado = trigger_po_contpaq("S.W.O 47 X1")
    assert resultado.ok is True
    assert correo_po_confirmado(resultado) is False
    assert resultado.response == body


def test_trigger_po_ok_con_correo():
    body = {"execution_time": 12.0, "nombreReporte": "PO_GAM_13040.pdf"}
    with patch(
        "modules.nesting_engine.api_client._post_json",
        return_value=(200, body),
    ):
        resultado = trigger_po_contpaq("S.W.O 47 X1")
    assert resultado.ok is True
    assert correo_po_confirmado(resultado) is True


if __name__ == "__main__":
    test_nombre_reporte_po_vacio_y_presente()
    test_correo_po_confirmado_exige_nombre()
    test_trigger_po_ok_aunque_nombre_reporte_vacio()
    test_trigger_po_ok_con_correo()
    print("OK test_correo_po_nombre_reporte")
