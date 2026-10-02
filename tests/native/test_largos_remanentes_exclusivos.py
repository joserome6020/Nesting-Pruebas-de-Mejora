"""Candado 2026-10-02: cada remanente queda reservado para una sola SWO.

Caso real (02/10/2026, SWO-092 a SWO-106): había un solo remanente de ANG004 (169"),
uno de ANG037 (204") y uno de SLC035 (229"), pero siete SWO contaban con los mismos
porque el plan se generaba con `reservar=False`. Como el MRL se arma a partir del plan,
ninguna de esas siete órdenes pedía ANG004 ni ANG037 ni SLC035; cuando la primera SWO
abría sesión reservaba los remanentes y las demás quedaban sin material.

A partir de este candado, cada vez que se genera (o regenera) un plan, los remanentes
que ese plan usa quedan reservados para la orden en el acto. La segunda orden ve esos
remanentes como RESERVADO para otra y planea con barra nueva (y la pide en su MRL).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

RAIZ = Path(__file__).resolve().parents[2]
for p in (RAIZ, RAIZ / "api"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from api import legacy_core as lc  # noqa: E402


ANG004 = "ANG004 | ANGULO perfil | A 36 | 1 X 1 X 0.125 IN"


def _rem(rem_uid: int, largo: float, material: str = ANG004, status: str = "DISPONIBLE",
         reservado_para: str | None = None) -> dict:
    return {
        "rem_uid": rem_uid,
        "rem_id": f"SOL4{int(largo)}A36",
        "material": material,
        "largo_real": largo,
        "largo_id": int(largo),
        "tipo": "SOL",
        "seccion": None,
        "status": status,
        "reservado_para_orden_id": reservado_para,
        "reservado_para_tipo_orden": "SWO" if reservado_para else None,
    }


class _CursorFalso:
    """Simula lo mínimo que usan `_ll_obtener_o_generar_plan` / `_ll_reservar_remanentes`."""

    def __init__(self):
        self.reservas: dict[int, str] = {}
        self.plan_guardado: dict[tuple[str, str], dict] = {}
        self.liberaciones: list[str] = []

    def execute(self, *args, **kwargs):
        pass

    def fetchone(self):
        return None


def _payload_una_pieza_160in():
    # 160" + kerf 0.38 = 160.38 ≤ util del remanente de 169" (168.5"), así que
    # la SWO que llegue primero logra encajar la pieza en el remanente.
    return {
        "rows": [{
            "nombre": "ITEM 26",
            "clasificacion": ANG004,
            "largo_in": 160.0,
            "cantidad": 1,
        }]
    }


def test_generar_plan_dos_veces_para_dos_swo_no_comparte_el_mismo_remanente():
    cur = _CursorFalso()
    disponible = {169: _rem(10, 169.0)}

    def listar(_c, material, orden_id, _tipo):
        rem = disponible.get(169)
        if rem is None:
            return []
        if rem["status"] == "DISPONIBLE":
            return [dict(rem)]
        if rem["reservado_para_orden_id"] == orden_id:
            return [dict(rem)]
        return []

    def reservar(_c, uids, orden_id, _tipo):
        for u in uids:
            if disponible.get(169, {}).get("rem_uid") == u:
                disponible[169]["status"] = "RESERVADO"
                disponible[169]["reservado_para_orden_id"] = orden_id

    with patch.object(lc, "_ll_listar_remanentes_para_material", side_effect=listar), \
         patch.object(lc, "_ll_reservar_remanentes", side_effect=reservar), \
         patch.object(lc, "_ll_liberar_reservas_de_orden", return_value=None), \
         patch.object(lc, "_ll_source_payload", side_effect=lambda *_a, **_k: _payload_una_pieza_160in()), \
         patch.object(lc, "_ll_cargar_plan_row", return_value=None), \
         patch.object(lc, "_ll_guardar_plan", side_effect=lambda c, o, t, h, p: {"orden_id": o, "tipo_orden": t, "plan_json": p}), \
         patch.object(lc, "_ll_hash_payload", return_value="h"), \
         patch.object(lc, "_ll_plan_puede_regenerarse", return_value=True), \
         patch.object(lc, "_ll_largos_comerciales_por_material", return_value={}):
        plan_a, _ = lc._ll_obtener_o_generar_plan(cur, "SWO-A", "SWO")
        plan_b, _ = lc._ll_obtener_o_generar_plan(cur, "SWO-B", "SWO")

    # SWO-A agarró el remanente; su barra NO es stock nueva.
    barra_a = plan_a["data"][lc._ll_material_key(ANG004)][0]
    assert barra_a["source"] == "REMANENTE"
    assert barra_a["rem_uid"] == 10

    # SWO-B ya no vio el remanente (está RESERVADO para SWO-A) → stock nuevo.
    barra_b = plan_b["data"][lc._ll_material_key(ANG004)][0]
    assert barra_b["source"] == "STOCK", (
        "candado SWO-092: dos órdenes no pueden compartir el mismo remanente; "
        "al generar el plan hay que reservar en el acto."
    )
    assert barra_b["rem_uid"] is None


def test_remanente_reservado_para_la_orden_sigue_disponible_para_ella():
    """Si yo mismo soy quien lo reservó, aún lo puedo usar al regenerar."""
    cur = _CursorFalso()
    disponible = {169: _rem(10, 169.0, status="RESERVADO", reservado_para="SWO-A")}

    def listar(_c, material, orden_id, _tipo):
        rem = disponible.get(169)
        if rem and rem["reservado_para_orden_id"] == orden_id:
            return [dict(rem)]
        return []

    with patch.object(lc, "_ll_listar_remanentes_para_material", side_effect=listar), \
         patch.object(lc, "_ll_reservar_remanentes", return_value=None), \
         patch.object(lc, "_ll_liberar_reservas_de_orden", return_value=None), \
         patch.object(lc, "_ll_source_payload", side_effect=lambda *_a, **_k: _payload_una_pieza_160in()), \
         patch.object(lc, "_ll_cargar_plan_row", return_value=None), \
         patch.object(lc, "_ll_guardar_plan", side_effect=lambda c, o, t, h, p: {"orden_id": o, "tipo_orden": t, "plan_json": p}), \
         patch.object(lc, "_ll_hash_payload", return_value="h"), \
         patch.object(lc, "_ll_plan_puede_regenerarse", return_value=True), \
         patch.object(lc, "_ll_largos_comerciales_por_material", return_value={}):
        plan_a, _ = lc._ll_obtener_o_generar_plan(cur, "SWO-A", "SWO")

    barra = plan_a["data"][lc._ll_material_key(ANG004)][0]
    assert barra["source"] == "REMANENTE"
    assert barra["rem_uid"] == 10


if __name__ == "__main__":
    test_generar_plan_dos_veces_para_dos_swo_no_comparte_el_mismo_remanente()
    test_remanente_reservado_para_la_orden_sigue_disponible_para_ella()
    print("OK")
