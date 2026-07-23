"""Limpia huérfanos ERP/pqart/sheets para jobs no-VSM."""
from __future__ import annotations

import psycopg2
from psycopg2.extras import RealDictCursor

NEST = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=12,
)

DELETE_JOBS = {"1000 kva de prueba", "1000 KVA DE PRUEBA", "GIGA FLUIDSTACK"}
DELETE_SWO_PATTERNS = ["%kva%prueba%", "%GIGA%FLUIDSTACK%", "W.O. 6 X1"]


def main() -> int:
    conn = psycopg2.connect(**NEST)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT id_job, job_number FROM erp_jobs ORDER BY 1")
    rows = cur.fetchall()
    print("=== erp_jobs antes ===")
    for r in rows:
        print(dict(r))

    ids_del = [r["id_job"] for r in rows if str(r["job_number"]).strip() in DELETE_JOBS]
    ids_keep = [r["id_job"] for r in rows if str(r["job_number"]).strip() not in DELETE_JOBS]
    print(f"ids_del={ids_del} ids_keep={ids_keep}")

    if ids_del:
        cur.execute("SELECT id_wo FROM erp_work_orders WHERE id_job = ANY(%s)", (ids_del,))
        wo_ids = [r["id_wo"] for r in cur.fetchall()]
        if wo_ids:
            cur.execute("DELETE FROM erp_piezas_tracking WHERE id_wo = ANY(%s)", (wo_ids,))
            print(f"erp_piezas_tracking: {cur.rowcount}")

        for pat in DELETE_SWO_PATTERNS:
            cur.execute(
                """
                DELETE FROM erp_placas_tracking
                WHERE id_swo IN (
                    SELECT id_swo FROM erp_super_work_orders WHERE nombre_swo ILIKE %s
                )
                """,
                (pat,),
            )
            if cur.rowcount:
                print(f"erp_placas_tracking [{pat}]: {cur.rowcount}")
            cur.execute(
                "DELETE FROM erp_super_work_orders WHERE nombre_swo ILIKE %s",
                (pat,),
            )
            if cur.rowcount:
                print(f"erp_super_work_orders [{pat}]: {cur.rowcount}")

        cur.execute(
            "DELETE FROM components WHERE id_sheet IN (SELECT id_sheet FROM sheets WHERE id_job = ANY(%s))",
            (ids_del,),
        )
        print(f"components: {cur.rowcount}")
        cur.execute("DELETE FROM sheets WHERE id_job = ANY(%s)", (ids_del,))
        print(f"sheets: {cur.rowcount}")
        cur.execute("DELETE FROM erp_work_orders WHERE id_job = ANY(%s)", (ids_del,))
        print(f"erp_work_orders: {cur.rowcount}")
        cur.execute("DELETE FROM erp_jobs WHERE id_job = ANY(%s)", (ids_del,))
        print(f"erp_jobs: {cur.rowcount}")

    for job in DELETE_JOBS:
        for tbl, col in [
            ("pqart_wo", "nombre_wo"),
            ("pqart_swo", "nombre_swo"),
            ("diccionario_swo", "job_numero"),
        ]:
            cur.execute(f"DELETE FROM {tbl} WHERE TRIM({col}::text) ILIKE %s", (job,))
            if cur.rowcount:
                print(f"{tbl}.{col} [{job}]: {cur.rowcount}")

    cur.execute(
        """
        DELETE FROM material_requerido_ldg
        WHERE orden_id ILIKE %s OR orden_id ILIKE %s OR orden_id ILIKE %s
        """,
        ("%kva%prueba%", "%GIGA%FLUIDSTACK%", "%1000%"),
    )
    print(f"material_requerido_ldg: {cur.rowcount}")

    conn.commit()

    cur.execute("SELECT id_job, job_number FROM erp_jobs ORDER BY 1")
    print("\n=== erp_jobs después ===")
    for r in cur.fetchall():
        print(dict(r))

    cur.execute("SELECT DISTINCT job FROM reporte_cortes ORDER BY 1")
    print("reporte_cortes:", [r["job"] for r in cur.fetchall()])
    cur.execute("SELECT DISTINCT job FROM lista_largos_job ORDER BY 1")
    print("lista_largos_job:", [r["job"] for r in cur.fetchall()])

    conn.close()
    print("\n[OK] Limpieza ERP completada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
