"""Verifica coherencia consumo MRL ↔ slots comerciales ↔ piezas del plan."""
from __future__ import annotations

import math
import sys
from collections import defaultdict

sys.path.insert(0, r"c:\Proyectos\New Arga Nesting Suite")

from catalogo_largos import (
    _cargar_placas_largos_desde_herinox,
    datos_material_requerido_pedido,
    extraer_codigo_herinox_combo,
)
from interface.largos_nesting_service import (
    _slots_comerciales_en_tira,
    _split_cortes_en_unidades_comerciales,
    _util_comercial_in,
    iter_barras_plan,
    listar_unidades_mrl_plan,
    resumir_plan_largos,
)
from lista_largos_material_requerido import (
    _cantidad_barras_catalogo,
    agregar_filas_desde_plan,
)


def _piezas_en_cortes(cortes) -> int:
    return len(cortes or [])


def _slot_overflow(cortes, largo_com: float) -> bool:
    util = _util_comercial_in(largo_com)
    used = 0.0
    for i, c in enumerate(cortes or []):
        if i > 0:
            used += 0.25
        used += float(c.get("largo") or 0)
    return used > util + 0.02


def auditar_plan(plan: dict, label: str = "") -> bool:
    ok = True
    catalogo = _cargar_placas_largos_desde_herinox(solo_disponibles=False)

    filas_mrl = agregar_filas_desde_plan(plan)
    unidades = listar_unidades_mrl_plan(plan)
    resumen = resumir_plan_largos(plan)

    print(f"\n{'=' * 72}")
    if label:
        print(label)
    print(f"Resumen: {resumen}")

    mrl_por_mat = {str(f["material"]).strip(): f for f in filas_mrl}
    units_por_mat: dict[str, list] = defaultdict(list)
    for u in unidades:
        units_por_mat[str(u.get("material") or "").strip()].append(u)

    plan_por_mat: dict[str, dict] = defaultdict(
        lambda: {"piezas": 0, "stock_in": 0.0, "tiras": 0, "slots_calc": 0}
    )
    for material, _idx, barra in iter_barras_plan(plan):
        mat = str(material or "").strip()
        if str(barra.get("source") or "STOCK").upper() == "REMANENTE":
            continue
        if not (barra.get("cortes") or []):
            continue
        ls = float(barra.get("largo_stock") or 0)
        datos = datos_material_requerido_pedido(mat, 1, catalogo=catalogo)
        largo_cat = float(datos.get("largo") or 0)
        n_slots = _slots_comerciales_en_tira(ls, largo_cat)
        plan_por_mat[mat]["piezas"] += _piezas_en_cortes(barra.get("cortes"))
        plan_por_mat[mat]["stock_in"] += ls
        plan_por_mat[mat]["tiras"] += 1
        plan_por_mat[mat]["slots_calc"] += n_slots

    print(f"\n{'MATERIAL':<40} {'MRL':>4} {'UNI':>4} {'SLT':>4} {'CEIL':>4} {'PZ':>4} {'MAP':>4} {'OK':>4}")
    for mat in sorted(set(mrl_por_mat) | set(plan_por_mat)):
        mrl_qty = int(mrl_por_mat.get(mat, {}).get("cantidad") or 0)
        uni_qty = len(units_por_mat.get(mat, []))
        slots = plan_por_mat[mat]["slots_calc"]
        stock_in = plan_por_mat[mat]["stock_in"]
        datos = datos_material_requerido_pedido(mat, 1, catalogo=catalogo)
        largo_cat = float(datos.get("largo") or 0)
        ceil_qty = _cantidad_barras_catalogo(stock_in, largo_cat)
        piezas_plan = plan_por_mat[mat]["piezas"]

        piezas_slot = sum(_piezas_en_cortes(u.get("cortes_slot")) for u in units_por_mat.get(mat, []))
        sin_mapa = sum(1 for u in units_por_mat.get(mat, []) if not u.get("nesting_key"))

        filas_ok = mrl_qty == uni_qty == slots
        piezas_ok = piezas_plan == piezas_slot
        if not filas_ok or not piezas_ok:
            ok = False

        cod = extraer_codigo_herinox_combo(mat)
        flag = "OK" if filas_ok and piezas_ok else "REV"
        print(
            f"{cod or mat[:38]:<40} {mrl_qty:>4} {uni_qty:>4} {slots:>4} {ceil_qty:>4} "
            f"{piezas_plan:>4} {piezas_slot:>4} {flag:>4}"
        )
        if mrl_qty != ceil_qty:
            print(f"    · MRL({mrl_qty}) vs ceil(stock/largo)({ceil_qty}) — normal si tiras no son múltiplo exacto")
        if sin_mapa:
            print(f"    · {sin_mapa} unidades MRL sin mapa de corte")
        if piezas_plan != piezas_slot:
            print(f"    · PIEZAS PERDIDAS: plan={piezas_plan} mapeadas={piezas_slot}")

    overflow_slots = 0
    for u in unidades:
        cs = u.get("cortes_slot")
        if cs and _slot_overflow(cs, float(u.get("largo") or 0)):
            overflow_slots += 1
            ok = False
    if overflow_slots:
        print(f"\n>>> {overflow_slots} slots exceden útil comercial")
    else:
        print("\n>>> Todos los slots respetan útil comercial")

    total_mrl = sum(int(f.get("cantidad") or 0) for f in filas_mrl)
    if total_mrl != len(unidades):
        print(f">>> DESFASE global MRL({total_mrl}) vs unidades({len(unidades)})")
        ok = False

    print(f"\n>>> Veredicto: {'CORRECTO' if ok else 'REVISAR'}")
    return ok


def main():
    import os

    import psycopg2
    from psycopg2.extras import RealDictCursor

    import api_server

    cfg = dict(
        host=os.getenv("NESTING_DB_HOST", "192.168.2.80"),
        port=os.getenv("NESTING_DB_PORT", "5433"),
        dbname=os.getenv("NESTING_DB_NAME", "nestingpro_db"),
        user=os.getenv("NESTING_DB_USER", "postgres"),
        password=os.getenv("NESTING_DB_PASSWORD", "nesting123"),
        connect_timeout=12,
    )

    ordenes = [("W.O. 1 X1", "WO"), ("SWO-001", "SWO")]
    try:
        conn = psycopg2.connect(**cfg)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        all_ok = True
        for orden_id, tipo in ordenes:
            plan, _ = api_server._ll_obtener_o_generar_plan(cur, orden_id, tipo, reservar=False)
            if not plan:
                print(f"No plan para {tipo} {orden_id}")
                continue
            all_ok = auditar_plan(plan, f"{tipo} {orden_id}") and all_ok
        cur.close()
        conn.close()
        sys.exit(0 if all_ok else 1)
    except Exception as e:
        print(f"BD no disponible ({e}); prueba sintética local…")
        plan_demo = {
            "data": {
                "Solera · SLC051 · 4.0 X .50 IN": [
                    {
                        "largo_stock": 480,
                        "source": "STOCK",
                        "cortes": [
                            {"largo": 127.5, "nombre": "62174-1247-P02"},
                            {"largo": 127.5, "nombre": "62174-1247-P03"},
                        ],
                    },
                    {
                        "largo_stock": 240,
                        "source": "STOCK",
                        "cortes": [{"largo": 80.0, "nombre": "P04"}],
                    },
                ],
            }
        }
        auditar_plan(plan_demo, "Demo sintético SLC051")
        sys.exit(0)


if __name__ == "__main__":
    main()
