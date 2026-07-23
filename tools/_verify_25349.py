import psycopg2
from psycopg2.extras import RealDictCursor

DB = dict(
    host="192.168.2.80",
    port="5433",
    database="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=12,
)
conn = psycopg2.connect(**DB)
cur = conn.cursor(cursor_factory=RealDictCursor)
cur.execute(
    "SELECT orden_id, tipo_orden, estado, updated_at FROM lista_largos_planes WHERE TRIM(orden_id)=%s",
    ("W.O. 11 X9",),
)
print("plan", cur.fetchall())
cur.execute(
    "SELECT COUNT(*) AS n FROM reporte_cortes WHERE TRIM(job)=%s",
    ("25349HEADIRON",),
)
print("cortes", dict(cur.fetchone() or {}))
cur.execute(
    "SELECT SUM(cantidad) AS barras FROM material_requerido_ldg WHERE TRIM(orden_id)=%s AND tipo_orden='WO'",
    ("W.O. 11 X9",),
)
print("mrl_barras", dict(cur.fetchone() or {}))
cur.close()
conn.close()
