import os
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import api_server

cfg = {
    "host": "192.168.2.80",
    "port": 5433,
    "dbname": "nestingpro_db",
    "user": "postgres",
    "password": "nesting123",
}

SWO = "SWO-001"
JOB = None

conn = psycopg2.connect(**cfg)
cur = conn.cursor(cursor_factory=RealDictCursor)

cur.execute(
    "SELECT DISTINCT TRIM(job) AS job FROM reporte_cortes WHERE TRIM(super_work_order) = %s LIMIT 1",
    (SWO,),
)
row = cur.fetchone()
if row:
    JOB = row["job"]
    print("Job detectado:", JOB)

if JOB:
    logs = api_server._propagar_material_requerido_por_job(cfg, JOB)
    print("Propagacion job:", logs)

ok, msg = api_server._asegurar_material_requerido_orden(cur, SWO, "SWO")
conn.commit()
print("SWO direct:", ok, msg)

cur.execute(
    """
    SELECT orden_id, tipo_orden, material, codigo, largo, cantidad
    FROM material_requerido_ldg
    WHERE tipo_orden = 'SWO' AND TRIM(orden_id) = %s
    ORDER BY material, largo
    """,
    (SWO,),
)
rows = cur.fetchall() or []
print("SWO ROWS:", len(rows))
for r in rows:
    print(dict(r))

cur.close()
conn.close()
