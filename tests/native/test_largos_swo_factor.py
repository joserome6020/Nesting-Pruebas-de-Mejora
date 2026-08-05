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
    _generar_plan_desde_filas,
    _obtener_filas_demanda_lote,
    factor_demanda_swo,
    iter_barras_plan,
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


# --- Invariantes que no se deben volver a romper -------------------------------

CSV_UN_JOB = [
    {
        "status": "ok",
        "job": "251008",
        "rows": [
            {"nombre": "ANG A", "clasificacion": "ANG037", "largo_in": 84.0, "cantidad_base": 4},
            {"nombre": "ANG B", "clasificacion": "ANG037", "largo_in": 52.5, "cantidad_base": 2},
            {"nombre": "SOL A", "clasificacion": "SLC046", "largo_in": 44.8, "cantidad_base": 4},
            {"nombre": "CAN A", "clasificacion": "CAN009", "largo_in": 26.25, "cantidad_base": 4},
        ],
    }
]


def _huella_plan(plan):
    """Cortes por material, independiente del orden de barras."""
    out: dict[str, list] = {}
    for material, _idx, barra in iter_barras_plan(plan):
        largos = tuple(sorted(round(float(c.get("largo") or 0), 4) for c in (barra.get("cortes") or [])))
        out.setdefault(material, []).append(largos)
    return {m: sorted(v) for m, v in out.items()}


def _plan_para_wos(wos):
    """Plan de una SWO formada por esas WO. cursor=None: sin remanentes no toca BD."""
    meta = {f"r{i}": {"job": "251008", "work_order": wo} for i, wo in enumerate(wos)}
    app = _AppFalsa(meta=meta, grupos=CSV_UN_JOB)
    filas, _origen = _obtener_filas_demanda_lote(app, None, "SWO-009", 1)
    return _generar_plan_desde_filas(None, "TEST-SWO", "SWO", "251008", 11, filas), filas


def test_una_wo_x11_equivale_a_x3_x3_x3_x2():
    """Fusionar 3 WO X3 + 1 X2 debe dar exactamente lo mismo que una sola WO X11."""
    plan_a, filas_a = _plan_para_wos(["W.O. 9 X11"])
    plan_b, filas_b = _plan_para_wos(["W.O. 9 X3", "W.O. 10 X3", "W.O. 11 X3", "W.O. 12 X2"])

    piezas_a = sum(int(f["cantidad"]) for f in filas_a)
    piezas_b = sum(int(f["cantidad"]) for f in filas_b)

    assert piezas_a == 14 * 11 == piezas_b, f"{piezas_a} vs {piezas_b}"
    assert plan_a["total_piezas"] == plan_b["total_piezas"]
    assert plan_a["total_barras"] == plan_b["total_barras"]
    assert _huella_plan(plan_a) == _huella_plan(plan_b)


def test_el_lote_del_nesteo_no_afecta_a_una_swo():
    """Causa raiz del bug: una SWO se nestea en un solo lote, factor_lote es irrelevante."""
    app = _AppFalsa(
        meta={"r1": {"job": "251008", "work_order": "W.O. 9 X11"}},
        grupos=CSV_UN_JOB,
    )
    base, _ = _obtener_filas_demanda_lote(app, None, "SWO-009", 1)
    piezas = sum(int(f["cantidad"]) for f in base)
    assert piezas == 14 * 11

    for factor_lote in (1, 2, 11, 99):
        filas, origen = _obtener_filas_demanda_lote(app, None, "SWO-009", factor_lote)
        assert origen == "swo_csv"
        assert sum(int(f["cantidad"]) for f in filas) == piezas, (
            f"factor_lote={factor_lote} alteró la demanda de la SWO"
        )


def test_una_wo_normal_si_usa_el_lote():
    """La ruta WO no cambia: ahi lote_k si es el numero de tanques."""
    app = _AppFalsa(grupos=CSV_UN_JOB)
    app.job_activo = "251008"

    filas_x1, _ = _obtener_filas_demanda_lote(app, None, "251008", 1)
    filas_x11, _ = _obtener_filas_demanda_lote(app, None, "251008", 11)

    assert sum(int(f["cantidad"]) for f in filas_x1) == 14
    assert sum(int(f["cantidad"]) for f in filas_x11) == 14 * 11


def test_jobs_distintos_no_comparten_csv():
    """Cada WO multiplica el CSV de SU job; los totales se suman, las listas no se mezclan."""
    grupos = CSV_UN_JOB + [
        {
            "status": "ok",
            "job": "62176",
            "rows": [{"nombre": "SOL Z", "clasificacion": "SLC046", "largo_in": 30.0, "cantidad_base": 5}],
        }
    ]
    app = _AppFalsa(
        meta={
            "r1": {"job": "251008", "work_order": "W.O. 9 X2"},
            "r2": {"job": "62176", "work_order": "W.O. 10 X3"},
        },
        grupos=grupos,
    )
    filas, _ = _obtener_filas_demanda_lote(app, None, "SWO-009", 1)

    por_job: dict[str, int] = {}
    for f in filas:
        por_job[f["job"]] = por_job.get(f["job"], 0) + int(f["cantidad"])

    assert por_job == {"251008": 14 * 2, "62176": 5 * 3}


if __name__ == "__main__":
    test_pares_wo_sin_duplicados_y_sin_la_swo()
    test_factor_swo_es_la_suma_de_sus_wo()
    test_csv_se_expande_con_el_factor_de_cada_wo()
    test_sin_csv_no_revienta()
    test_una_wo_x11_equivale_a_x3_x3_x3_x2()
    test_el_lote_del_nesteo_no_afecta_a_una_swo()
    test_una_wo_normal_si_usa_el_lote()
    test_jobs_distintos_no_comparten_csv()
    print("SMOKE OK")
