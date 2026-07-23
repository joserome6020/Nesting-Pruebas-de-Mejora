import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
print("start", flush=True)
import psycopg2
from psycopg2.extras import RealDictCursor
import api_server
print("imported", flush=True)

cfg = dict(host="192.168.2.80", port=5433, dbname="nestingpro_db", user="postgres", password="nesting123")
SWO = "SWO-001"
conn = psycopg2.connect(**cfg)
cur = conn.cursor(cursor_factory=RealDictCursor)
print("calling asegurar SWO", flush=True)
ok, msg = api_server._asegurar_material_requerido_orden(cur, SWO, "SWO")
conn.commit()
print("RESULT:", ok, msg, flush=True)
cur.execute("SELECT COUNT(*) AS n FROM material_requerido_ldg WHERE tipo_orden='SWO' AND TRIM(orden_id)=%s", (SWO,))
print("count:", cur.fetchone()["n"], flush=True)
conn.close()
