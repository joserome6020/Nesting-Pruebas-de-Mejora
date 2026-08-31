"""Candado SWO-047: pedido MRL y validación deben usar el MISMO plan.

Caso real 2026-08-31: modal/nesting pedía 15× ANG037 @ 240\", pero al exportar
``validar_mrl_swo_canonica_tras_export`` regeneraba el plan en BD (14 barras) y
tiraba el export: ``MRL pide 15 ... (plan=14)``.

La demanda (68×38\" + 34×24.5\" ≈ 3417\") no cabe en 14×239\" útiles → el conteo
correcto del modal es 15. El bug era comparar contra otro packing regenerado.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from interface.largos_nesting_service import (  # noqa: E402
    _esperadas_mrl_desde_plan,
    aplicar_pedido_largos_swo_acumulado_tras_export,
    validar_mrl_swo_canonica_tras_export,
)


def _plan_ang037(n_barras: int, *, total_piezas: int = 272) -> dict:
    """Plan mínimo: N tiras STOCK ANG037 de 240\" con un corte dummy cada una."""
    material = "ANG037 | ANGULO perfil | A 36 | 3 X 3 X 0.375 IN"
    barras = []
    for _ in range(n_barras):
        barras.append(
            {
                "source": "STOCK",
                "largo_stock": 240.0,
                "cortes": [{"largo": 38.0, "nombre": "ITEM 3"}],
            }
        )
    return {
        "data": {material: barras},
        "total_barras": n_barras,
        "total_piezas": total_piezas,
        "orden_id": "SWO-047",
        "tipo_orden": "SWO",
    }


def test_esperadas_desde_plan_cuenta_slots_stock():
    """15 tiras STOCK → 15 barras comerciales esperadas (techo MRL)."""
    with patch(
        "catalogo_largos.datos_material_requerido_pedido",
        side_effect=lambda material, cantidad, catalogo=None: {
            "codigo": "ANG037",
            "largo": 240.0,
            "costo": 1.0 * cantidad,
        },
    ), patch(
        "catalogo_largos._cargar_placas_largos_desde_herinox",
        return_value={},
    ):
        esp = _esperadas_mrl_desde_plan(_plan_ang037(15))
    assert esp.get(("ANG037", 240.0)) == 15


def test_validar_mrl_con_plan_referencia_acepta_15_aunque_bd_diga_14():
    """
    Reproduce SWO-047: MRL=15 y plan regenerado BD=14.
    Con plan_referencia=modal(15) debe PASAR; sin él fallaría contra BD.
    """
    mrl_rows = [
        {"codigo": "ANG037", "largo": 240.0, "cantidad": 15},
    ]

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = mrl_rows
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    plan_modal = _plan_ang037(15)
    plan_bd_malo = _plan_ang037(14)

    with patch(
        "interface.largos_nesting_service._conexion_bd",
        return_value=(mock_conn, dict),
    ), patch(
        "interface.largos_nesting_service.cargar_plan_largos",
        return_value=plan_bd_malo,
    ), patch(
        "catalogo_largos.datos_material_requerido_pedido",
        side_effect=lambda material, cantidad, catalogo=None: {
            "codigo": "ANG037",
            "largo": 240.0,
            "costo": 1.0 * cantidad,
        },
    ), patch(
        "catalogo_largos._cargar_placas_largos_desde_herinox",
        return_value={},
    ):
        # Sin referencia: regenera BD (14) → tumba como en producción.
        ok_sin, msg_sin = validar_mrl_swo_canonica_tras_export("SWO-047")
        assert ok_sin is False, msg_sin
        assert "MRL pide 15" in msg_sin and "plan=14" in msg_sin

        # Con el plan del modal: misma fuente del pedido → OK.
        ok_con, msg_con = validar_mrl_swo_canonica_tras_export(
            "SWO-047",
            plan_referencia=plan_modal,
        )
        assert ok_con is True, msg_con


def test_aplicar_pedido_devuelve_plan_memoria_para_validar():
    """El export debe recibir el plan del modal para no reconsultar BD."""
    plan_modal = _plan_ang037(15)
    app = MagicMock()
    app.plan_largos_por_lote = {0: plan_modal}
    app.plan_largos_sin_demanda_por_lote = set()

    with patch(
        "interface.largos_nesting_service.cargar_plan_largos",
        return_value=_plan_ang037(14),  # misma #piezas → no fuerza canónico
    ), patch(
        "interface.largos_nesting_service.obtener_exclusiones_mrl_unidades",
        return_value=set(),
    ), patch(
        "interface.largos_nesting_service.previsualizar_pedido_mrl_unidades",
        return_value=[{"codigo": "ANG037", "largo": 240.0, "cantidad": 1}] * 15,
    ), patch(
        "interface.largos_nesting_service.agregar_filas_desde_unidades_mrl",
        return_value=[
            {"codigo": "ANG037", "largo": 240.0, "cantidad": 15, "material": "ANG037"}
        ],
    ), patch(
        "interface.largos_nesting_service.enviar_pedido_largos_filas",
        return_value=(True, "ok"),
    ):
        ok, msg, plan_ref = aplicar_pedido_largos_swo_acumulado_tras_export(
            app, "SWO-047", [0]
        )

    assert ok is True, msg
    assert plan_ref is plan_modal
    assert int(plan_ref.get("total_barras") or 0) == 15


if __name__ == "__main__":
    test_esperadas_desde_plan_cuenta_slots_stock()
    test_validar_mrl_con_plan_referencia_acepta_15_aunque_bd_diga_14()
    test_aplicar_pedido_devuelve_plan_memoria_para_validar()
    print("OK")
