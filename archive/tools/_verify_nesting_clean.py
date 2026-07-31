"""Verificación final post-limpieza nesting DB."""
import psycopg2
from psycopg2.extras import RealDictCursor

cfg = dict(host="192.168.2.80", port=5433, dbname="nestingpro_db", user="postgres", password="nesting123", connect_timeout=12)
conn = psycopg2.connect(**cfg)
cur = conn.cursor(cursor_factory=RealDictCursor)

checks = [
    ("erp_jobs", "SELECT id_job, job_number FROM erp_jobs ORDER BY 1"),
    ("reporte_cortes", "SELECT DISTINCT job, COUNT(*) c FROM reporte_cortes GROUP BY 1"),
    ("lista_largos_job", "SELECT DISTINCT job, COUNT(*) c FROM lista_largos_job GROUP BY 1"),
    ("lista_largos_swo", "SELECT DISTINCT job, COUNT(*) c FROM lista_largos_swo GROUP BY 1"),
    ("pqart_wo", "SELECT COUNT(*) c FROM pqart_wo"),
    ("pqart_swo", "SELECT COUNT(*) c FROM pqart_swo"),
    ("sheets", "SELECT id_job, COUNT(*) c FROM sheets GROUP BY 1"),
    ("costos_prorrateo", "SELECT DISTINCT job, COUNT(*) c FROM costos_prorrateo GROUP BY 1"),
]
for name, sql in checks:
    try:
        cur.execute(sql)
        rows = cur.fetchall()
        print(f"\n{name}:")
        for r in rows:
            print(" ", dict(r))
        if not rows:
            print("  (vacío)")
    except Exception as e:
        conn.rollback()
        print(f"\n{name}: ERR {e}")
conn.close()
