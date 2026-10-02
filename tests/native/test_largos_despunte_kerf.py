"""Candado 2026-10-02 (v2 de la config de corte de largos).

A partir del 02/10/2026 las barras se cortan con:
  - Despunte 0.25" en cada extremo (antes 0.5").
  - Gap entre cortes 0.38" (antes 0.25").

El plan guardado debe traer la metadata (`kerf_in`, `despunte_in`, `config_version`)
para que la Estación de corte, la UI y los auditores sepan con qué parámetros se
generó cada plan y los planes previos (sin metadata) se interpreten con los valores
legacy. Así los planes viejos y los nuevos pueden coexistir sin recalcular sobrantes
con parámetros distintos a los que se usaron al nestear.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

RAIZ = Path(__file__).resolve().parents[2]
for p in (RAIZ, RAIZ / "api", RAIZ / "interface"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from api import legacy_core as lc  # noqa: E402
import largos_nesting_service as lns  # noqa: E402


CAN011 = "CAN011 | CANAL perfil | A 36 | 4 X 5.4 LB/FT"


def test_constantes_nuevas_son_038_y_025():
    assert lc.LISTA_LARGOS_KERF == 0.38
    assert lc.LISTA_LARGOS_RECORTE_EXTREMO == 0.25
    assert lc.LISTA_LARGOS_CONFIG_VERSION == 2
    assert lns.KERF_LARGOS_IN == 0.38
    assert lns.RECORTE_EXTREMO_LARGOS_IN == 0.25


def test_visor_de_tira_usa_la_misma_config_que_el_servicio():
    # 2026-10-02: el visor tenía kerf 0.25 / despunte 0.5 fijos y mostraba "Útil 239.00 · Kerf 0.25"
    # aunque el reparto ya era v2.
    from interface.qt.widgets import largos_tira_canvas as canvas

    assert canvas.KERF_IN == 0.38
    assert canvas.RECORTE_EXTREMO_IN == 0.25


def test_pdfs_de_largos_usan_config_v2_y_respetan_planes_viejos():
    # 2026-10-02: ambos PDF (lista de largos y consumo en piso) tenían 0.25 / 0.5 fijos.
    import reporte_pdf_lista_largos as pdf_ll
    import reporte_pdf_nesteo_largos_piso as pdf_piso

    assert (pdf_ll.KERF, pdf_ll.RECORTE_EXTREMO) == (0.38, 0.25)
    assert (pdf_piso.KERF, pdf_piso.RECORTE_EXTREMO) == (0.38, 0.25)
    assert pdf_ll.kerf_despunte_de_plan({"kerf_in": 0.38, "despunte_in": 0.25}) == (0.38, 0.25)
    assert pdf_ll.kerf_despunte_de_plan({"orden_id": "SWO-050"}) == (0.25, 0.5)

    data = {CAN011: [{"source": "STOCK", "largo_stock": 240.0, "remanente_show": 40.0,
                      "barra_index": 1, "cortes": [{"nombre": "ITEM 1", "largo": 65.75}] * 3}]}
    _, _, ef_v2, _ = pdf_ll.calcular_kpis(data, *pdf_ll.kerf_despunte_de_plan({"kerf_in": 0.38, "despunte_in": 0.25}))
    _, _, ef_legacy, _ = pdf_ll.calcular_kpis(data, *pdf_ll.kerf_despunte_de_plan({}))
    assert round(ef_v2, 2) == round(3 * (65.75 + 0.38) / 239.5 * 100, 2)
    assert round(ef_legacy, 2) == round(3 * (65.75 + 0.25) / 239.0 * 100, 2)

    for plan in ({"kerf_in": 0.38, "despunte_in": 0.25, "config_version": 2}, {}):
        buf = pdf_ll.generar_pdf_lista_largos({"orden_id": "SWO-T", "plan": plan, "data_nesteo": data})
        assert buf.getvalue()[:4] == b"%PDF"


def test_despunte_consume_025_en_cada_extremo():
    # util = 240 − 0.25*2 = 239.5
    assert lc._ll_largo_util_bruto(240.0) == 239.5
    assert lc._ll_largo_util_bruto(480.0) == 479.5


def test_kerf_se_suma_038_por_pieza():
    assert lc._ll_largo_requerido_pieza({"largo": 65.75}) == 66.13


def test_plan_generado_trae_la_metadata_de_corte():
    payload = {"rows": [{"nombre": "ITEM 1", "clasificacion": CAN011, "largo_in": 65.75, "cantidad": 8}]}
    with patch.object(lc, "_ll_listar_remanentes_para_material", return_value=[]), patch.object(
        lc, "_ll_largos_comerciales_por_material", return_value={lc._ll_material_key(CAN011): 240.0}
    ):
        plan, _ = lc._ll_generar_plan_desde_payload(MagicMock(), "SWO-TEST", "SWO", payload)
    assert plan["kerf_in"] == 0.38
    assert plan["despunte_in"] == 0.25
    assert plan["config_version"] == 2


def test_plan_vacio_tambien_trae_metadata():
    plan, _ = lc._ll_generar_plan_desde_payload(MagicMock(), "SWO-X", "SWO", {"rows": []})
    assert plan["kerf_in"] == 0.38
    assert plan["despunte_in"] == 0.25
    assert plan["config_version"] == 2


def test_plan_viejo_sin_metadata_cae_a_los_valores_legacy():
    plan_viejo = {"orden_id": "SWO-050", "tipo_orden": "SWO", "data": {}, "total_piezas": 0, "total_barras": 0}
    assert lc.plan_kerf_despunte(plan_viejo) == (0.25, 0.5)
    assert lns.kerf_despunte_de_plan(plan_viejo) == (0.25, 0.5)


def test_plan_nuevo_trae_sus_propios_valores_no_los_del_modulo():
    plan_nuevo = {"kerf_in": 0.38, "despunte_in": 0.25, "config_version": 2}
    assert lc.plan_kerf_despunte(plan_nuevo) == (0.38, 0.25)
    assert lns.kerf_despunte_de_plan(plan_nuevo) == (0.38, 0.25)


def test_8x6575_siguen_cabiendo_3_por_barra_de_240_con_nueva_config():
    # 3 × 65.75 = 197.25; + 2 kerfs 0.38 = 198.01; ≤ util 239.5 ⇒ caben 3 por barra.
    barras = lc._ll_resolver_pendientes_con_stock(
        [{"nombre": "ITEM 1", "material": CAN011, "largo": 65.75} for _ in range(8)],
        240.0,
    )
    assert [len(b["cortes"]) for b in barras] == [3, 3, 2]


if __name__ == "__main__":
    test_constantes_nuevas_son_038_y_025()
    test_visor_de_tira_usa_la_misma_config_que_el_servicio()
    test_pdfs_de_largos_usan_config_v2_y_respetan_planes_viejos()
    test_despunte_consume_025_en_cada_extremo()
    test_kerf_se_suma_038_por_pieza()
    test_plan_generado_trae_la_metadata_de_corte()
    test_plan_vacio_tambien_trae_metadata()
    test_plan_viejo_sin_metadata_cae_a_los_valores_legacy()
    test_plan_nuevo_trae_sus_propios_valores_no_los_del_modulo()
    test_8x6575_siguen_cabiendo_3_por_barra_de_240_con_nueva_config()
    print("OK")
