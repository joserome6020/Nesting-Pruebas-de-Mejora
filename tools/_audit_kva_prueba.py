"""Auditar y opcionalmente borrar nesteo accidental '1000 kva de prueba'."""
import sys
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


def audit(cur, conn=None):
    print("=== reporte_cortes ===")
    cur.execute(
        """
        SELECT job, super_work_order, work_order, estatus, COUNT(*)
        FROM reporte_cortes
        WHERE job ILIKE %s OR super_work_order ILIKE %s OR work_order ILIKE %s
        GROUP BY 1, 2, 3, 4
        ORDER BY 1, 2, 3
        """,
        (JOB_PAT, JOB_PAT, JOB_PAT),
    )
    rows = cur.fetchall()
    for r in rows:
        print(r)
    print(f"  total grupos: {len(rows)}")

    print("=== pqart_wo ===")
    cur.execute(
        """
        SELECT nombre_wo, COUNT(*)
        FROM pqart_wo
        WHERE nombre_wo ILIKE %s OR ruta ILIKE %s
        GROUP BY 1
        """,
        (JOB_PAT, JOB_PAT),
    )
    for r in cur.fetchall():
        print(r)

    print("=== lista_largos_job ===")
    cur.execute(
        "SELECT job, COUNT(*) FROM lista_largos_job WHERE job ILIKE %s GROUP BY 1",
        (JOB_PAT,),
    )
    for r in cur.fetchall():
        print(r)

    print("=== lista_largos_swo ===")
    try:
        cur.execute(
            """
            SELECT job, COUNT(*) FROM lista_largos_swo
            WHERE job ILIKE %s
            GROUP BY 1
            """,
            (JOB_PAT,),
        )
        for r in cur.fetchall():
            print(r)
    except Exception as e:
        print("  skip:", e)

    print("=== material_requerido_ldg ===")
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'material_requerido_ldg'
        ORDER BY ordinal_position
        """
    )
    cols = [r[0] for r in cur.fetchall()]
    job_col = next((c for c in ("job", "nombre_job", "super_work_order") if c in cols), None)
    if job_col:
        try:
            cur.execute(
                f'SELECT {job_col}, COUNT(*) FROM material_requerido_ldg WHERE {job_col} ILIKE %s GROUP BY 1',
                (JOB_PAT,),
            )
            for r in cur.fetchall():
                print(r)
        except Exception as e:
            if conn:
                conn.rollback()
            print("  skip:", e)

    print("=== erp_jobs / erp_work_orders ===")
    for tbl, col in (("erp_jobs", "job_number"), ("erp_work_orders", "wo_number")):
        try:
            cur.execute(
                f'SELECT {col}, COUNT(*) FROM {tbl} WHERE CAST({col} AS TEXT) ILIKE %s GROUP BY 1',
                (JOB_PAT,),
            )
            for r in cur.fetchall():
                print(tbl, r)
        except Exception as e:
            conn.rollback()
            print(tbl, "skip:", e)


def delete_kva(cur):
    """Borra registros del job de prueba (hijos antes que padres)."""
    deleted = {}

    def run(sql, params=()):
        cur.execute(sql, params)
        n = cur.rowcount
        key = sql.strip().split()[0] + " " + sql.strip().split()[1]
        deleted[key] = deleted.get(key, 0) + n
        return n

    # lista_largos hijos (por job en tablas relacionadas)
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
        try:
            cur.execute(
                f"""
                DELETE FROM {tbl}
                WHERE job_id IN (SELECT id FROM lista_largos_job WHERE job ILIKE %s)
                   OR swo_id IN (SELECT id FROM lista_largos_swo WHERE job ILIKE %s)
                """,
                (JOB_PAT, JOB_PAT),
            )
            if cur.rowcount:
                deleted[tbl] = cur.rowcount
        except Exception:
            pass

    run(
        "DELETE FROM material_requerido_ldg WHERE COALESCE(job, '') ILIKE %s OR COALESCE(super_work_order, '') ILIKE %s",
        (JOB_PAT, JOB_PAT),
    )
    run("DELETE FROM lista_largos_swo WHERE job ILIKE %s", (JOB_PAT,))
    run("DELETE FROM lista_largos_job WHERE job ILIKE %s", (JOB_PAT,))
    run(
        "DELETE FROM reporte_cortes WHERE job ILIKE %s OR super_work_order ILIKE %s OR work_order ILIKE %s",
        (JOB_PAT, JOB_PAT, JOB_PAT),
    )
    run("DELETE FROM pqart_wo WHERE nombre_wo ILIKE %s OR ruta ILIKE %s", (JOB_PAT, JOB_PAT))
    run("DELETE FROM pqart_swo WHERE nombre_swo ILIKE %s OR ruta ILIKE %s", (JOB_PAT, JOB_PAT))

    for tbl, col in (
        ("erp_piezas_tracking", "job_name"),
        ("erp_placas_tracking", "job_name"),
        ("erp_work_orders", "wo_number"),
        ("erp_super_work_orders", "swo_number"),
        ("erp_jobs", "job_number"),
        ("jobs", "name"),
    ):
        try:
            cur.execute(
                f"DELETE FROM {tbl} WHERE CAST({col} AS TEXT) ILIKE %s",
                (JOB_PAT,),
            )
            if cur.rowcount:
                deleted[tbl] = cur.rowcount
        except Exception:
            pass

    return deleted


def main():
    do_delete = "--delete" in sys.argv
    conn = psycopg2.connect(**CFG)
    cur = conn.cursor()
    audit(cur, conn)
    if do_delete:
        print("\n=== BORRANDO ===")
        conn.rollback()
        deleted = delete_kva(cur)
        conn.commit()
        print("Eliminados:", deleted)
        print("\n=== DESPUÉS ===")
        audit(cur, conn)
    else:
        print("\n(Pase --delete para borrar)")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
