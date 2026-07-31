"""Borra nesteo accidental '1000 kva de prueba' de nestingpro_db."""
import psycopg2

CFG = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=12,
)
JOB_PAT = "%kva%prueba%"


def exec_delete(cur, label, sql, params=()):
    try:
        cur.execute(sql, params)
        n = cur.rowcount
        if n:
            print(f"  {label}: {n}")
        return n
    except Exception as e:
        print(f"  {label}: SKIP ({e})")
        return 0


def main():
    conn = psycopg2.connect(**CFG)
    conn.autocommit = True
    cur = conn.cursor()

    print("ANTES reporte_cortes:")
    cur.execute(
        "SELECT job, work_order, COUNT(*) FROM reporte_cortes WHERE job ILIKE %s GROUP BY 1,2",
        (JOB_PAT,),
    )
    print(cur.fetchall())

    print("\nBORRANDO...")
    for tbl in (
        "lista_largos_cortes",
        "lista_largos_eventos_pieza",
        "lista_largos_eventos_sobrante",
        "lista_largos_remanentes",
        "lista_largos_sobrantes",
        "lista_largos_sesiones",
        "lista_largos_turnos",
        "lista_largos_planes",
    ):
        exec_delete(
            cur,
            tbl,
            f"""
            DELETE FROM {tbl}
            WHERE job_id IN (SELECT id FROM lista_largos_job WHERE job ILIKE %s)
            """,
            (JOB_PAT,),
        )

    exec_delete(cur, "lista_largos_swo", "DELETE FROM lista_largos_swo WHERE job ILIKE %s", (JOB_PAT,))
    exec_delete(cur, "lista_largos_job", "DELETE FROM lista_largos_job WHERE job ILIKE %s", (JOB_PAT,))
    exec_delete(
        cur,
        "reporte_cortes",
        "DELETE FROM reporte_cortes WHERE job ILIKE %s OR work_order ILIKE %s",
        (JOB_PAT, JOB_PAT),
    )
    exec_delete(
        cur,
        "pqart_wo",
        "DELETE FROM pqart_wo WHERE nombre_wo ILIKE %s OR ruta ILIKE %s",
        (JOB_PAT, JOB_PAT),
    )
    exec_delete(
        cur,
        "pqart_swo",
        "DELETE FROM pqart_swo WHERE nombre_swo ILIKE %s OR ruta ILIKE %s",
        (JOB_PAT, JOB_PAT),
    )
    exec_delete(cur, "erp_jobs", "DELETE FROM erp_jobs WHERE job_number ILIKE %s", (JOB_PAT,))

    print("\nDESPUÉS reporte_cortes:")
    cur.execute(
        "SELECT job, work_order, COUNT(*) FROM reporte_cortes WHERE job ILIKE %s GROUP BY 1,2",
        (JOB_PAT,),
    )
    rows = cur.fetchall()
    print(rows if rows else "(vacío)")

    cur.execute("SELECT COUNT(*) FROM pqart_wo WHERE nombre_wo ILIKE %s", (JOB_PAT,))
    print("pqart_wo restante:", cur.fetchone()[0])

    cur.execute("SELECT COUNT(*) FROM lista_largos_job WHERE job ILIKE %s", (JOB_PAT,))
    print("lista_largos_job restante:", cur.fetchone()[0])

    cur.close()
    conn.close()
    print("\nListo.")


if __name__ == "__main__":
    main()
