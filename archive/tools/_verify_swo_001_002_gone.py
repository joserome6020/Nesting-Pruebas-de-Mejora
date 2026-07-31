"""Verificacion post-borrado SWO-001/002."""
import json
import urllib.request
import urllib.parse
import psycopg2
from psycopg2.extras import RealDictCursor

BASE = "http://192.168.2.80:8003"
ANS = dict(host="192.168.2.80", port=5433, dbname="nestingpro_db", user="postgres", password="nesting123", connect_timeout=15)

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return r.status, json.loads(r.read().decode())

for swo in ("SWO-001", "SWO-002"):
    st, body = get(f"/nesting/swo/{urllib.parse.quote(swo)}/jobs")
    print(f"{swo} jobs:", st, body)

for job in ("251007", "VANTRAN251007"):
    try:
        st, body = get(f"/jobs/by-number/{urllib.parse.quote(job)}")
        print(f"job {job}:", st, {k: body.get(k) for k in ("id", "job_number", "status")})
    except Exception as e:
        print(f"job {job}:", e)

conn = psycopg2.connect(**ANS)
cur = conn.cursor(cursor_factory=RealDictCursor)
for wo in ("W.O. 1 X11", "W.O. 2 X4"):
    cur.execute(
        """
        SELECT TRIM(work_order) wo, TRIM(COALESCE(super_work_order,'')) swo,
               TRIM(COALESCE(estatus,'')) estatus, COUNT(*) n
        FROM reporte_cortes
        WHERE TRIM(work_order)=%s
        GROUP BY 1,2,3
        ORDER BY 1,2,3
        """,
        (wo,),
    )
    print(wo, [dict(r) for r in cur.fetchall()])

for swo in ("SWO-001", "SWO-002"):
    cur.execute("SELECT COUNT(*) n FROM reporte_cortes WHERE TRIM(super_work_order)=%s", (swo,))
    print(f"reporte still on {swo}:", dict(cur.fetchone()))
    cur.execute("SELECT COUNT(*) n FROM pqart_swo WHERE TRIM(nombre_swo)=%s", (swo,))
    print(f"pqart_swo {swo}:", dict(cur.fetchone()))
    cur.execute("SELECT COUNT(*) n FROM material_requerido_ldg WHERE tipo_orden='SWO' AND TRIM(orden_id)=%s", (swo,))
    print(f"mrl {swo}:", dict(cur.fetchone()))

conn.close()
print("OK")
