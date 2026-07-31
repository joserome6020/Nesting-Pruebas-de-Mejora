"""Verifica diccionario/ERP para S.W.O 01 / W.O. 1 X1."""
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


def main():
    conn = psycopg2.connect(**CFG)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    print("=== diccionario_swo ===")
    cur.execute(
        """
        SELECT * FROM diccionario_swo
        WHERE prefijo_carpeta ILIKE %s
           OR swo_id ILIKE %s
           OR swo_id ILIKE %s
        ORDER BY id DESC
        """,
        ("%W.O. 1%", "%S.W.O%01%", "%SWO%001%"),
    )
    rows = cur.fetchall()
    for r in rows:
        print(dict(r))
    if not rows:
        print(" (vacío)")

    print("\n=== erp_work_orders ~ W.O. 1 ===")
    cur.execute(
        """
        SELECT w.nombre_wo, j.job_number, j.cliente, j.producto
        FROM erp_work_orders w
        JOIN erp_jobs j ON w.id_job = j.id_job
        WHERE w.nombre_wo ILIKE %s
        ORDER BY w.id_wo DESC
        LIMIT 10
        """,
        ("%W.O. 1%",),
    )
    for r in cur.fetchall():
        print(dict(r))

    print("\n=== erp_super_work_orders ===")
    cur.execute(
        """
        SELECT * FROM erp_super_work_orders
        WHERE nombre_swo ILIKE %s OR nombre_swo ILIKE %s
        ORDER BY id_swo DESC
        """,
        ("%S.W.O%01%", "%SWO%001%"),
    )
    for r in cur.fetchall():
        print(dict(r))

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
