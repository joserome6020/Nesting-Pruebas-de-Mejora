import psycopg2

cfg = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=5,
)
PIDS = [1528, 1529, 1531, 1566, 1610, 1622]

conn = psycopg2.connect(**cfg)
conn.autocommit = True
cur = conn.cursor()
for pid in PIDS:
    try:
        cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
        print("terminated", pid, cur.fetchone()[0])
    except Exception as e:
        print("fail", pid, e)
conn.close()
