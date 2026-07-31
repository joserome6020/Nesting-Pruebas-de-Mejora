import psycopg2

cfg = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
)
conn = psycopg2.connect(**cfg)
cur = conn.cursor()
cur.execute(
    "DELETE FROM material_requerido_ldg WHERE tipo_orden = 'SWO' AND TRIM(orden_id) = %s",
    ("SWO-001",),
)
cur.execute(
    """
    INSERT INTO material_requerido_ldg (orden_id, tipo_orden, material, codigo, largo, cantidad, costo)
    SELECT %s, 'SWO', material, codigo, largo, cantidad, costo
    FROM material_requerido_ldg
    WHERE tipo_orden = 'WO' AND TRIM(orden_id) = %s
    """,
    ("SWO-001", "W.O. 1 X1"),
)
conn.commit()
cur.execute(
    "SELECT COUNT(*) FROM material_requerido_ldg WHERE tipo_orden = 'SWO' AND TRIM(orden_id) = %s",
    ("SWO-001",),
)
print("SWO rows:", cur.fetchone()[0])
conn.close()
