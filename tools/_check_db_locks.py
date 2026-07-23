import psycopg2

cfg = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=5,
)
conn = psycopg2.connect(**cfg)
conn.autocommit = True
cur = conn.cursor()
cur.execute(
    """
    SELECT pid, state, wait_event_type, wait_event, left(query, 120) AS q
    FROM pg_stat_activity
    WHERE datname = 'nestingpro_db'
      AND pid <> pg_backend_pid()
    ORDER BY state, pid
    """
)
rows = cur.fetchall()
print("sessions:", len(rows))
for r in rows:
    print(r)
cur.execute(
    """
    SELECT blocked_locks.pid AS blocked_pid,
           blocking_locks.pid AS blocking_pid,
           left(blocked_activity.query, 80) AS blocked_q
    FROM pg_catalog.pg_locks blocked_locks
    JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
    JOIN pg_catalog.pg_locks blocking_locks
      ON blocking_locks.locktype = blocked_locks.locktype
     AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
     AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
     AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
     AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
     AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
     AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
     AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
     AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
     AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
     AND blocking_locks.pid != blocked_locks.pid
    JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
    WHERE NOT blocked_locks.granted
    """
)
blocks = cur.fetchall()
print("blocks:", len(blocks))
for b in blocks:
    print(b)
conn.close()
