import psycopg2
c = psycopg2.connect(host="192.168.2.80", port=5433, dbname="nestingpro_db", user="postgres", password="nesting123")
cur = c.cursor()
cur.execute("SELECT id, codigo, cantidad, largo FROM material_requerido_ldg WHERE tipo_orden='SWO' ORDER BY codigo")
for r in cur.fetchall():
    print(r)
c.close()
