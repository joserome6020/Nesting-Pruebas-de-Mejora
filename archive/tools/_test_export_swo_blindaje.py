"""Suite final: overlays + MRL apply/validate + detección SWO + costeo."""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IFACE = os.path.join(ROOT, "interface")
for p in (ROOT, IFACE):
    if p not in sys.path:
        sys.path.insert(0, p)

from modules.nesting_engine.efficiency_metrics import _es_pieza_real_nombre
from interface.qt.tabs._mixin_export import _es_job_swo
from interface.largos_nesting_service import (
    aplicar_pedido_largos_swo_acumulado_tras_export,
    validar_mrl_swo_canonica_tras_export,
)
from interface.utils_nesting import generar_csv_compras


def test_overlays():
    bad = [
        "REF__x",
        "TATUAJE__x",
        "RETAZO_GUILLOTINA__x",
        "REMANENTE__x",
        "CU_CORTE__x",
        "RTZCU_ZONA__x",
    ]
    good = ["W.O. 1 X1__P11", "62176-P11"]
    assert all(not _es_pieza_real_nombre(n) for n in bad)
    assert all(_es_pieza_real_nombre(n) for n in good)
    print("OK overlays")


def test_swo_detect():
    assert _es_job_swo("SWO-001")
    assert _es_job_swo("S.W.O 01 X1")
    assert _es_job_swo("s.w.o 02 x3")
    assert not _es_job_swo("W.O. 1 X1")
    assert not _es_job_swo("62176")
    print("OK swo detect")


def test_mrl_apply_then_validate():
    class _App:
        plan_largos_por_lote = {}
        plan_largos_sin_demanda_por_lote = set()
        exclusiones_mrl_unidades_por_lote = {}

    ok_a, msg_a = aplicar_pedido_largos_swo_acumulado_tras_export(
        _App(), "SWO-001", [0]
    )
    print("aplicar:", ok_a, msg_a)
    assert ok_a, msg_a
    ok_v, msg_v = validar_mrl_swo_canonica_tras_export("SWO-001")
    print("validar:", ok_v, msg_v)
    assert ok_v, msg_v
    print("OK mrl apply+validate")


def test_costeo_end_to_end():
    cfg = dict(
        host="192.168.2.80",
        port=5433,
        dbname="nestingpro_db",
        user="postgres",
        password="nesting123",
        connect_timeout=12,
    )
    resultados = {
        "0.375_A36": {
            "hojas": [
                {
                    "placa_id": "P1",
                    "placa_w": 2438.4,
                    "placa_h": 1219.2,
                    "precio_placa": 50.0,
                    "piezas": [
                        {
                            "nombre": "W.O. 1 X1__62176-1248-P11",
                            "area": 40000.0,
                            "poligonos": [[[0, 0], [100, 0], [100, 40], [0, 40]]],
                        },
                        {"nombre": "REF__x", "area": 1.0, "poligonos": [[[0, 0], [1, 0], [1, 1], [0, 1]]]},
                        {"nombre": "TATUAJE__RTZ1", "area": 1.0, "poligonos": [[[0, 0], [1, 0], [1, 1], [0, 1]]]},
                        {
                            "nombre": "RETAZO_GUILLOTINA__RTZ1",
                            "area": 1.0,
                            "poligonos": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
                        },
                    ],
                }
            ]
        }
    }
    test_wo = "S.W.O 98 PREFLIGHT"
    with tempfile.TemporaryDirectory() as td:
        estado = generar_csv_compras(
            td, test_wo, resultados, ruta_destino=td, es_swo=True, db_config=cfg
        )
    print("costeo:", estado)
    assert estado.get("ok") is True, estado

    import psycopg2

    conn = psycopg2.connect(**cfg)
    cur = conn.cursor()
    cur.execute("DELETE FROM costos_prorrateo WHERE work_order = %s", (test_wo,))
    conn.commit()
    cur.close()
    conn.close()
    print("OK costeo end-to-end + cleanup")


if __name__ == "__main__":
    test_overlays()
    test_swo_detect()
    test_mrl_apply_then_validate()
    test_costeo_end_to_end()
    print("\nALL PASS — export SWO blindado")
