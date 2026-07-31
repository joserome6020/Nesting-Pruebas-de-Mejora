"""Verifica que no queden filas con W.O. 2 X1 en nestingpro_db."""
import psycopg2
from psycopg2.extras import RealDictCursor

CFG = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=12,
)
PAT = "%W.O. 2 X1%"


def main():
    conn = psycopg2.connect(**CFG)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND data_type IN ('character varying', 'text', 'character')
        ORDER BY table_name, ordinal_position
        """
    )
    cols_by_table = {}
    for r in cur.fetchall():
        cols_by_table.setdefault(r["table_name"], []).append(r["column_name"])

    print("=== Búsqueda", PAT, "===\n")
    total = 0
    for table, cols in sorted(cols_by_table.items()):
        for col in cols:
            try:
                cur.execute(
                    f'SELECT COUNT(*) AS n FROM "{table}" WHERE CAST("{col}" AS TEXT) ILIKE %s',
                    (PAT,),
                )
                n = int(cur.fetchone()["n"] or 0)
                if n:
                    total += n
                    print(f"  {table}.{col}: {n}")
            except Exception:
                conn.rollback()
    print(f"\nTotal filas: {total}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
