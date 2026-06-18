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

WO = "W.O. 1 X1"

conn = psycopg2.connect(**cfg)
cur = conn.cursor(cursor_factory=RealDictCursor)

ok, msg = api_server._asegurar_material_requerido_orden(cur, WO, "WO")
conn.commit()
print("RESULT:", ok, msg)

cur.execute(
    """
    SELECT orden_id, tipo_orden, material, codigo, largo, cantidad, created_at
    FROM material_requerido_ldg
    WHERE tipo_orden = 'WO' AND TRIM(orden_id) = %s
    ORDER BY material, largo
    """,
    (WO,),
)
rows = cur.fetchall() or []
print("ROWS:", len(rows))
for r in rows:
    print(dict(r))

cur.close()
conn.close()
