"""Quita basura de diccionario_swo: jobs que no están en el nest SWO."""
import psycopg2

CFG = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=12,
)

SWO = "SWO-001"


def main():
    conn = psycopg2.connect(**CFG)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT DISTINCT TRIM(job)
        FROM reporte_cortes
        WHERE TRIM(super_work_order) = %s AND job IS NOT NULL AND BTRIM(job) <> ''
        """,
        (SWO,),
    )
    jobs_nest = {str(r[0]).strip() for r in cur.fetchall() if r[0]}
    print("jobs en nest:", sorted(jobs_nest))

    cur.execute(
        """
        SELECT id, prefijo_carpeta, job_numero, cliente
        FROM diccionario_swo
        WHERE TRIM(swo_id) = %s
        ORDER BY id
        """,
        (SWO,),
    )
    rows = cur.fetchall()
    print("diccionario antes:")
    for r in rows:
        print(" ", r)

    cur.execute(
        """
        DELETE FROM diccionario_swo d
        WHERE TRIM(d.swo_id) = %s
          AND (
            TRIM(d.job_numero) IS NULL
            OR BTRIM(d.job_numero) = ''
            OR NOT EXISTS (
              SELECT 1
              FROM reporte_cortes rc
              WHERE TRIM(rc.super_work_order) = TRIM(d.swo_id)
                AND TRIM(rc.job) = TRIM(d.job_numero)
            )
          )
        RETURNING id, prefijo_carpeta, job_numero
        """,
        (SWO,),
    )
    deleted = cur.fetchall()
    print("deleted:", deleted)

    cur.execute(
        """
        SELECT id, prefijo_carpeta, job_numero, cliente
        FROM diccionario_swo
        WHERE TRIM(swo_id) = %s
        ORDER BY id
        """,
        (SWO,),
    )
    print("diccionario despues:")
    for r in cur.fetchall():
        print(" ", r)

    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
