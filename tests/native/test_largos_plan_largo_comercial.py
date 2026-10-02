"""Candado 2026-10-02: el plan de la estación usa el largo comercial que se compra.

Caso real SWO-093: el plan armaba CAN011 en una tira de 480" con 7 × 65.75", pero se
compran barras de 240" (caben 3 por barra). La estación indicaba cortes imposibles y
sobrantes falsos (17" en vez de ~42" por barra).
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

CAN011 = "CAN011 | CANAL perfil | A 36 | 4 X 5.4 LB/FT"


def _piezas(n: int, largo: float, material: str = CAN011) -> list[dict]:
    return [{"nombre": "ITEM 1", "material": material, "largo": largo} for _ in range(n)]


def test_barras_con_largo_comercial_240_no_exceden_la_barra_real():
    barras = lc._ll_resolver_pendientes_con_stock(_piezas(8, 65.75), 240.0)
    assert all(b["largo_stock"] == 240.0 for b in barras), barras
    assert [len(b["cortes"]) for b in barras] == [3, 3, 2], barras
    for b in barras:
        usado = sum(c["largo"] + lc.LISTA_LARGOS_KERF for c in b["cortes"])
        assert usado <= lc._ll_largo_util_bruto(240.0) + 1e-6, b


def test_sin_catalogo_conserva_eleccion_240_480():
    barras = lc._ll_resolver_pendientes_con_stock(_piezas(8, 65.75))
    assert any(b["largo_stock"] == 480.0 for b in barras), barras


def test_material_comprado_en_480_sigue_en_480():
    barras = lc._ll_resolver_pendientes_con_stock(_piezas(7, 65.75, "TUB007"), 480.0)
    assert [b["largo_stock"] for b in barras] == [480.0], barras


def test_generar_plan_pasa_el_largo_comercial_de_cada_material():
    payload = {"rows": [{"nombre": "ITEM 1", "clasificacion": CAN011, "largo_in": 65.75, "cantidad": 8}]}
    with patch.object(lc, "_ll_listar_remanentes_para_material", return_value=[]), patch.object(
        lc, "_ll_largos_comerciales_por_material", return_value={lc._ll_material_key(CAN011): 240.0}
    ):
        plan, _ = lc._ll_generar_plan_desde_payload(MagicMock(), "SWO-093", "SWO", payload)
    barras = next(iter(plan["data"].values()))
    assert plan["total_piezas"] == 8
    assert all(b["largo_stock"] == 240.0 for b in barras), barras
    assert len(barras) == 3


def test_largo_de_catalogo_fuera_de_rango_se_ignora():
    lc._LL_CATALOGO_CACHE.update({"datos": [], "ts": 0.0})
    with patch("catalogo_largos._cargar_placas_largos_desde_herinox", return_value=[]), patch(
        "catalogo_largos.datos_material_requerido_pedido",
        side_effect=lambda m, c, catalogo=None: {"largo": {"A": 240.0, "B": 18.0, "C": 0.0}[m]},
    ):
        out = lc._ll_largos_comerciales_por_material(["A", "B", "C"])
    lc._LL_CATALOGO_CACHE.update({"datos": None, "ts": 0.0})
    assert out == {"A": 240.0}, out


if __name__ == "__main__":
    test_barras_con_largo_comercial_240_no_exceden_la_barra_real()
    test_sin_catalogo_conserva_eleccion_240_480()
    test_material_comprado_en_480_sigue_en_480()
    test_generar_plan_pasa_el_largo_comercial_de_cada_material()
    test_largo_de_catalogo_fuera_de_rango_se_ignora()
    print("OK")
