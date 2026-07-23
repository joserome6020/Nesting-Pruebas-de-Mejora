import psycopg2

cfg = dict(host="192.168.2.80", port=5433, dbname="nestingpro_db", user="postgres", password="nesting123", connect_timeout=8)
PIDS = [1779, 1781, 2046, 2066, 2068, 36208]
conn = psycopg2.connect(**cfg)
conn.autocommit = True
cur = conn.cursor()
for pid in PIDS:
    try:
        cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
        print(pid, cur.fetchone()[0])
    except Exception as e:
        print(pid, e)
conn.close()
