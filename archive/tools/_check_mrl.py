import psycopg2
cfg = dict(host="192.168.2.80", port=5433, dbname="nestingpro_db", user="postgres", password="nesting123")
conn = psycopg2.connect(**cfg)
cur = conn.cursor()
cur.execute("SELECT orden_id, tipo_orden, COUNT(*) FROM material_requerido_ldg GROUP BY 1,2 ORDER BY 1,2")
print("material_requerido_ldg:")
for r in cur.fetchall():
    print(r)
cur.execute("SELECT DISTINCT TRIM(super_work_order), TRIM(work_order) FROM reporte_cortes WHERE super_work_order IS NOT NULL")
print("reporte_cortes swo/wo:")
for r in cur.fetchall():
    print(r)
conn.close()
