"""Compara lista_largos_job → plan → material_requerido_ldg para WO/SWO."""
import json
import psycopg2
from psycopg2.extras import RealDictCursor

CFG = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=12,
)

import sys

sys.path.insert(0, r"c:\Proyectos\ANS Pruebas de mejora")
import api_server
from lista_largos_material_requerido import agregar_filas_desde_plan


def audit_orden(cur, orden_id: str, tipo: str):
    print(f"\n{'='*60}\n{tipo} {orden_id}\n{'='*60}")
    rows, jobs = api_server._ll_rows_para_orden(cur, orden_id, tipo)
    print(f"Jobs vinculados: {jobs}")
    print(f"Filas lista expandida: {len(rows)}")
    piezas = sum(int(r.get("cantidad") or 0) for r in rows)
    print(f"Piezas totales (sum cantidad): {piezas}")

    if rows:
        print("\nMuestra CSV expandido (top 5):")
        for r in rows[:5]:
            print(
                f"  {r.get('nombre')} | {r.get('clasificacion','')[:40]} | "
                f"largo_in={r.get('largo_in')} | cant={r.get('cantidad')} | WO={r.get('work_order')}"
            )

    try:
        plan, _ = api_server._ll_obtener_o_generar_plan(cur, orden_id, tipo, reservar=False)
        stock_por_mat = {}
        for mat, barras in (plan.get("data") or {}).items():
            for b in barras or []:
                if str(b.get("source") or "").upper() == "REMANENTE":
                    continue
                ls = float(b.get("largo_stock") or 0)
                stock_por_mat.setdefault(mat, {})
                stock_por_mat[mat][ls] = stock_por_mat[mat].get(ls, 0) + 1
        print("\nBarras STOCK del plan (material → largo_stock → qty):")
        for mat in sorted(stock_por_mat):
            for ls, n in sorted(stock_por_mat[mat].items()):
                print(f"  {mat[:50]}: {n} barra(s) de {ls}\"")

        sim = agregar_filas_desde_plan(plan)
        print("\nMRL simulado desde plan:")
        for f in sim:
            print(f"  {f.get('codigo')} | cant={f['cantidad']} | largo={f['largo']}")
    except Exception as e:
        print(f"Plan error: {e}")

    cur.execute(
        """
        SELECT codigo, material, largo, cantidad, costo
        FROM material_requerido_ldg
        WHERE TRIM(orden_id) = %s AND tipo_orden = %s
        ORDER BY codigo
        """,
        (orden_id, tipo),
    )
    db_rows = cur.fetchall()
    print(f"\nMRL en BD ({len(db_rows)} filas):")
    for r in db_rows:
        print(f"  {r['codigo']} | cant={r['cantidad']} | largo={r['largo']}")


def main():
    conn = psycopg2.connect(**CFG)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    for orden, tipo in [("W.O. 1 X1", "WO"), ("SWO-001", "SWO")]:
        try:
            audit_orden(cur, orden, tipo)
        except Exception as e:
            print(f"Error {tipo} {orden}: {e}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
