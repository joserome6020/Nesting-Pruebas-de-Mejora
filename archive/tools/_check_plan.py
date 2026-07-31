import psycopg2
cfg = dict(host="192.168.2.80", port=5433, dbname="nestingpro_db", user="postgres", password="nesting123")
conn = psycopg2.connect(**cfg)
cur = conn.cursor()
for tbl in ("lista_largos_plan", "lista_largos_swo"):
    try:
        cur.execute(f"SELECT orden_id, tipo_orden FROM {tbl} LIMIT 5")
        print(tbl, cur.fetchall())
    except Exception as e:
        print(tbl, "err", e)
        conn.rollback()
conn.close()
