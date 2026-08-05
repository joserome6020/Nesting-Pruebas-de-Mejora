"""Smoke: la demanda de largos de una SWO se expande por WO, no por lote.

Caso real: SWO-003 fusiona W.O. 3 X11 del job 251008-COMP-HI. Como una SWO se
nestea en un solo lote (lote_k=1), el modal calculaba los largos en X1 mientras
que la WO original si salia en X11. El multiplicador de una SWO es la suma de
los factores de sus WO, no el lote del nesteo.
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))

from largos_nesting_service import (
    _filas_desde_csv_para_pares,
    factor_demanda_swo,
    resolver_wos_fuente_swo,
)


class _PartsFalso:
    def __init__(self, grupos):
        self._grupos = grupos

    def _cargar_listas_largos_desde_rutas(self):
        return self._grupos


class _AppFalsa:
    def __init__(self, meta=None, grupos=None):
        self.job_activo = "SWO-009"
        self.meta_pdf_por_ruta = meta or {}
        self.vista_parts = _PartsFalso(grupos or []) if grupos is not None else None


META_SWO = {
    "r1": {"job": "251008", "item": "ITEM 1", "work_order": "W.O. 3 X2"},
    "r2": {"job": "251008", "item": "ITEM 2", "work_order": "W.O. 3 X2"},
    "r3": {"job": "251008", "item": "ITEM 1", "work_order": "W.O. 4 X3"},
    "r4": {"job": "62176", "item": "ITEM 7", "work_order": "W.O. 5 X6"},
}


def test_pares_wo_sin_duplicados_y_sin_la_swo():
    app = _AppFalsa(meta=dict(META_SWO, r5={"job": "SWO-009", "work_order": "SWO-009"}))
    pares = resolver_wos_fuente_swo(app, "SWO-009")

    assert pares == [
        ("251008", "W.O. 3 X2"),
        ("251008", "W.O. 4 X3"),
        ("62176", "W.O. 5 X6"),
    ]


def test_factor_swo_es_la_suma_de_sus_wo():
    app = _AppFalsa(meta=META_SWO)
    assert factor_demanda_swo(app, "SWO-009") == 11

    assert factor_demanda_swo(_AppFalsa(), "SWO-009") == 1


def test_csv_se_expande_con_el_factor_de_cada_wo():
    grupos = [
        {
            "status": "ok",
            "job": "251008",
            "rows": [
                {"nombre": "ITEM 1", "clasificacion": "Angulo", "largo_in": 30.0, "cantidad_base": 4},
                {"nombre": "ITEM 2", "clasificacion": "Canal", "largo_in": 20.0, "cantidad_base": 2},
            ],
        },
        {
            "status": "ok",
            "job": "62176",
            "rows": [
                {"nombre": "ITEM 7", "clasificacion": "Solera", "largo_in": 44.0, "cantidad_base": 1},
            ],
        },
        {"status": "error", "job": "251008", "rows": [{"nombre": "X", "largo_in": 9.0, "cantidad_base": 99}]},
    ]
    app = _AppFalsa(meta=META_SWO, grupos=grupos)
    pares = resolver_wos_fuente_swo(app, "SWO-009")
    filas = _filas_desde_csv_para_pares(app, pares)

    # 251008 (4+2 base) x2 + 251008 (4+2 base) x3 + 62176 (1 base) x6
    assert sum(int(f["cantidad"]) for f in filas) == 6 * 2 + 6 * 3 + 1 * 6
    assert {f["factor_wo"] for f in filas} == {2, 3, 6}
    assert all(int(f["cantidad"]) == int(f["cantidad_base"]) * int(f["factor_wo"]) for f in filas)


def test_sin_csv_no_revienta():
    assert _filas_desde_csv_para_pares(_AppFalsa(meta=META_SWO), [("251008", "W.O. 3 X2")]) == []


if __name__ == "__main__":
    test_pares_wo_sin_duplicados_y_sin_la_swo()
    test_factor_swo_es_la_suma_de_sus_wo()
    test_csv_se_expande_con_el_factor_de_cada_wo()
    test_sin_csv_no_revienta()
    print("SMOKE OK")
