"""Candado SWO-079 H29: RTZ de acero en pqart_swo conserva SWO-xxx-H##.

El VSM busca la hoja de reporte_cortes (SWO-079-H29) en pqart_swo. Desde
3079f6f el export reescribía sheet_code/nombre DXF del RTZ de acero con el
placa_id (RTZ1-0.5-48.0x120.0-SWO-079) y la estación no la encontraba.
Solo RTZCU virtual (cobre, fuera de la numeración global) usa placa_id.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))


def _hoja_rtz_acero():
    return {
        "placa_id": "RTZ1-0.5-48.0x120.0-SWO-079",
        "es_retazo": True,
        "is_rtz": True,
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "piezas": [],
    }


def test_numeracion_y_metadata_rtz_acero_quedan_en_h():
    from modules.nesting_engine.exporter import _inyectar_metadata_hoja
    from modules.nesting_engine.sheet_numbering import asignar_numeracion_global_hojas

    madre = {"placa_id": "PLC031", "placa_w": 3048.0, "placa_h": 1524.0, "piezas": []}
    rtz = _hoja_rtz_acero()
    resultados = {"0.5_A 36": {"hojas": [madre, rtz]}}
    asignar_numeracion_global_hojas(resultados, "SWO-079", sobrescribir=True)
    assert rtz["sheet_code"] == "SWO-079-H2"

    _inyectar_metadata_hoja(
        rtz,
        order_label="SWO-079",
        thickness_name="0.5",
        sheet_seq=int(rtz["sheet_seq"]),
        display_name=rtz["placa_id"],
        source_nest_name="SWO-079",
    )
    assert rtz["sheet_code"] == "SWO-079-H2", rtz["sheet_code"]
    assert rtz["sheet_display_name"] == "RTZ1-0.5-48.0x120.0-SWO-079"


def test_rtzcu_virtual_sigue_con_placa_id():
    from modules.nesting_engine.exporter import _inyectar_metadata_hoja, _sheet_code_usa_placa_id

    cu = {
        "placa_id": "RTZCU1-0.25-4.0x144.0-SWO-079",
        "cu_rtz_virtual": True,
        "placa_w": 3657.6,
        "placa_h": 101.6,
        "piezas": [],
    }
    assert _sheet_code_usa_placa_id(cu)
    assert not _sheet_code_usa_placa_id(_hoja_rtz_acero())
    _inyectar_metadata_hoja(
        cu,
        order_label="SWO-079",
        thickness_name="0.25",
        sheet_seq=1,
        display_name=cu["placa_id"],
        source_nest_name="SWO-079",
    )
    assert cu["sheet_code"] == "RTZCU1-0.25-4.0x144.0-SWO-079"


def test_nombre_dxf_export_usa_helper():
    import inspect

    from modules.nesting_engine import exporter

    src = inspect.getsource(exporter)
    assert 'str(hoja.get("placa_id") or "").upper().startswith("RTZ")' not in src, (
        "El nombre del DXF de RTZ acero no debe salir del placa_id (VSM busca H##)."
    )
    assert src.count("_sheet_code_usa_placa_id(hoja)") >= 2


def main() -> None:
    test_numeracion_y_metadata_rtz_acero_quedan_en_h()
    test_rtzcu_virtual_sigue_con_placa_id()
    test_nombre_dxf_export_usa_helper()
    print("OK test_rtz_acero_sheet_code_h")


if __name__ == "__main__":
    main()
