import psycopg2

cfg = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=12,
)
conn = psycopg2.connect(**cfg)
cur = conn.cursor()
cur.execute(
    """
    DELETE FROM diccionario_swo
    WHERE TRIM(swo_id) = %s
      AND (
        TRIM(job_numero) = %s
        OR TRIM(prefijo_carpeta) ILIKE %s
      )
    RETURNING id, prefijo_carpeta, job_numero
    """,
    ("SWO-001", "251007", "%X11%"),
)
print("deleted:", cur.fetchall())
cur.execute(
    """
    SELECT id, prefijo_carpeta, job_numero, cliente
    FROM diccionario_swo
    WHERE TRIM(swo_id) = %s
    """,
    ("SWO-001",),
)
print("remain:", cur.fetchall())
conn.commit()
cur.close()
conn.close()
