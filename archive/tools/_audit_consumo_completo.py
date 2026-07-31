"""Auditoría completa: CSV → plan → pedido Cominox para WO/SWO job 62174."""
import math
import sys
from collections import defaultdict

sys.path.insert(0, r"c:\Proyectos\ANS Pruebas de mejora")

import psycopg2
from psycopg2.extras import RealDictCursor

import api_server
from catalogo_largos import (
    _cargar_placas_largos_desde_herinox,
    extraer_codigo_herinox_combo,
)
from lista_largos_material_requerido import (
    _cantidad_barras_catalogo,
    agregar_filas_desde_plan,
)

CFG = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=12,
)

WO = "W.O. 1 X1"
SWO = "SWO-001"
JOB = "62174"


def herinox_por_codigo():
    cat = _cargar_placas_largos_desde_herinox(solo_disponibles=False)
    out = {}
    for p in cat:
        c = str(p.get("codigo") or "").strip().upper()
        if c and c not in out:
            out[c] = p
    return out


def audit_orden(cur, orden_id: str, tipo: str, hx: dict):
    print(f"\n{'='*72}")
    print(f"{tipo} {orden_id}")
    print("=" * 72)

    rows, jobs = api_server._ll_rows_para_orden(cur, orden_id, tipo)
    print(f"Jobs: {jobs}")
    print(f"Líneas CSV expandidas: {len(rows)}")
    print(f"Piezas totales (suma cantidades): {sum(int(r.get('cantidad') or 0) for r in rows)}")

    # Agrupar demanda del CSV por código Herinox
    demanda = defaultdict(lambda: {"piezas": 0, "pulgadas": 0.0, "detalle": []})
    for r in rows:
        mat = str(r.get("clasificacion") or "")
        cod = extraer_codigo_herinox_combo(mat)
        qty = int(r.get("cantidad") or 0)
        largo = float(r.get("largo_in") or 0)
        demanda[cod]["piezas"] += qty
        demanda[cod]["pulgadas"] += qty * largo
        if len(demanda[cod]["detalle"]) < 3:
            demanda[cod]["detalle"].append(
                f"{r.get('nombre','')[:30]} L={largo}\" x{qty}"
            )

    print("\n--- DEMANDA desde CSV (por código) ---")
    print(f"{'COD':<8} {'PZAS':>5} {'PULG TOTAL':>12} {'HERINOX ft':>10}")
    for cod in sorted(demanda):
        pl = hx.get(cod, {})
        ft = float(pl.get("largo_ft") or 0)
        d = demanda[cod]
        print(f"{cod:<8} {d['piezas']:>5} {d['pulgadas']:>12.1f} {ft:>10}")

    plan, _ = api_server._ll_obtener_o_generar_plan(cur, orden_id, tipo, reservar=False)
    sim = {extraer_codigo_herinox_combo(f["material"]): f for f in agregar_filas_desde_plan(plan)}

    print("\n--- PLAN DE CORTE (barras STOCK abiertas) ---")
    print(f"{'COD':<8} {'BARRAS':>7} {'PULG PLAN':>10} {'LARGO BAR':>10}")
    plan_by_cod = defaultdict(lambda: {"barras": 0, "pulg": 0.0, "largos": []})
    for mat, barras in (plan.get("data") or {}).items():
        cod = extraer_codigo_herinox_combo(mat)
        for b in barras or []:
            if str(b.get("source") or "").upper() == "REMANENTE":
                continue
            ls = float(b.get("largo_stock") or 0)
            plan_by_cod[cod]["barras"] += 1
            plan_by_cod[cod]["pulg"] += ls
            plan_by_cod[cod]["largos"].append(ls)
    for cod in sorted(plan_by_cod):
        p = plan_by_cod[cod]
        lg = p["largos"][0] if len(set(p["largos"])) == 1 else p["largos"]
        print(f"{cod:<8} {p['barras']:>7} {p['pulg']:>10.0f} {str(lg):>10}")

    cur.execute(
        """
        SELECT codigo, cantidad, largo, costo
        FROM material_requerido_ldg
        WHERE TRIM(orden_id) = %s AND tipo_orden = %s
        ORDER BY codigo
        """,
        (orden_id, tipo),
    )
    mrl = {str(r["codigo"]).upper(): r for r in cur.fetchall()}

    print("\n--- PEDIDO COMINOX (material_requerido_ldg) ---")
    print(f"{'COD':<8} {'QTY':>4} {'LARGO':>7} {'CAT ft':>7} {'ceil':>5} {'OK':>4}")
    all_ok = True
    for cod in sorted(set(demanda) | set(sim)):
        row = mrl.get(cod, {})
        pl = hx.get(cod, {})
        ft = float(pl.get("largo_ft") or 0)
        cat_in = round(ft * 12, 2) if ft else 0
        plan_pulg = plan_by_cod[cod]["pulg"]
        esperada = _cantidad_barras_catalogo(plan_pulg, cat_in)
        qty = int(row.get("cantidad") or sim.get(cod, {}).get("cantidad") or 0)
        largo = float(row.get("largo") or 0)
        ok = (
            cat_in > 0
            and abs(largo - cat_in) < 1
            and qty == esperada
            and plan_pulg >= demanda[cod]["pulgadas"] - 50  # plan debe cubrir demanda (+kerf)
        )
        if not ok:
            all_ok = False
        print(
            f"{cod:<8} {qty:>4} {largo:>7.0f} {ft:>6}ft {esperada:>5} "
            f"{'OK' if ok else 'REV':>4}"
        )

    # Verificar cobertura: pulgadas plan >= demanda CSV (con margen kerf)
    print("\n--- COBERTURA plan vs demanda CSV ---")
    kerf_note = f"(kerf plan ~{api_server.LISTA_LARGOS_KERF}\"/corte)"
    for cod in sorted(demanda):
        dem = demanda[cod]["pulgadas"]
        plan_p = plan_by_cod[cod]["pulg"]
        # plan incluye kerf en cada pieza; stock bars tienen recorte extremo
        cubre = plan_p >= dem - 1
        print(
            f"{cod}: demanda={dem:.1f}\" material abierto en plan={plan_p:.0f}\" "
            f"{'CUBRE' if cubre else 'NO CUBRE'} {kerf_note}"
        )

    print(f"\n>>> Veredicto {tipo} {orden_id}: {'CORRECTO' if all_ok else 'REVISAR'}")
    return demanda, plan_by_cod, mrl


def main():
    conn = psycopg2.connect(**CFG)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    hx = herinox_por_codigo()

    # CSV base del job
    cur.execute(
        """
        SELECT nombre, clasificacion, largo_in, cantidad_base, cantidad_job, cantidad_total
        FROM lista_largos_job
        WHERE TRIM(job) = %s OR job_key = %s
        ORDER BY clasificacion, nombre
        LIMIT 5
        """,
        (JOB, JOB.upper()),
    )
    sample = cur.fetchall()
    cur.execute(
        "SELECT COUNT(*) AS n FROM lista_largos_job WHERE TRIM(job) = %s",
        (JOB,),
    )
    n_csv = cur.fetchone()["n"]
    print(f"CSV lista_largos_job job={JOB}: {n_csv} líneas base")
    print("Muestra CSV base (sin factor WO):")
    for r in sample:
        print(
            f"  {extraer_codigo_herinox_combo(r['clasificacion'])} | "
            f"L={r['largo_in']}\" base_qty={r.get('cantidad_base')} job_qty={r.get('cantidad_job')}"
        )

    d1, p1, m1 = audit_orden(cur, WO, "WO", hx)
    d2, p2, m2 = audit_orden(cur, SWO, "SWO", hx)

    print(f"\n{'='*72}")
    print("WO vs SWO (misma lista de largos)")
    print("=" * 72)
    same_demand = d1 == d2
    same_plan = {k: round(v["pulg"], 1) for k, v in p1.items()} == {
        k: round(v["pulg"], 1) for k, v in p2.items()
    }
    same_mrl = all(
        m1.get(c, {}).get("cantidad") == m2.get(c, {}).get("cantidad")
        and m1.get(c, {}).get("largo") == m2.get(c, {}).get("largo")
        for c in set(m1) | set(m2)
    )
    print(f"Demanda CSV idéntica: {same_demand}")
    print(f"Plan de corte idéntico: {same_plan}")
    print(f"Pedido Cominox idéntico: {same_mrl}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
