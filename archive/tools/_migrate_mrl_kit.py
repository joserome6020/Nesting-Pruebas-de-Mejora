import psycopg2

cfg = dict(host="192.168.2.80", port=5433, dbname="nestingpro_db", user="postgres", password="nesting123", connect_timeout=8)
conn = psycopg2.connect(**cfg)
conn.autocommit = True
cur = conn.cursor()
for sql in [
    "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS kit_recibido BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS kit_recibido_por VARCHAR(120) NULL",
    "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS kit_recibido_fecha TIMESTAMP NULL",
]:
    cur.execute(sql)
    print("ok:", sql[:60])
cur.execute(
    "SELECT column_name FROM information_schema.columns WHERE table_name='material_requerido_ldg' AND column_name LIKE 'kit%'"
)
print("cols:", [r[0] for r in cur.fetchall()])
conn.close()
