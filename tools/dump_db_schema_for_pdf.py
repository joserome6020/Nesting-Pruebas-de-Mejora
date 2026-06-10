"""One-off: introspect nestingpro_db and emit schema JSON for PDF builder."""
import argparse
import json
import sys
from pathlib import Path

import psycopg2

DB = {
    "host": "192.168.2.52",
    "port": 5433,
    "dbname": "nestingpro_db",
    "user": "postgres",
    "password": "nesting123",
}


def main(out_path=None):
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """
    )
    tables = [r[0] for r in cur.fetchall()]
    out = []
    for t in tables:
        cur.execute(
            """
            SELECT column_name, data_type, udt_name, is_nullable,
                   column_default, ordinal_position,
                   COALESCE(col_description((quote_ident(table_schema)||'.'||quote_ident(table_name))::regclass, ordinal_position), '')
            FROM information_schema.columns c
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (t,),
        )
        cols = []
        for row in cur.fetchall():
            cols.append(
                {
                    "name": row[0],
                    "data_type": row[1],
                    "udt_name": row[2],
                    "nullable": row[3],
                    "default": row[4],
                    "ordinal": row[5],
                    "pg_comment": row[6] or "",
                }
            )
        cur.execute(
            """
            SELECT pg_catalog.obj_description((quote_ident(%s)||'.'||quote_ident(%s))::regclass, 'pg_class')
            """,
            ("public", t),
        )
        tbl_comment = cur.fetchone()[0] or ""
        out.append({"table": t, "table_comment": tbl_comment, "columns": cols})
    conn.close()
    text = json.dumps(out, indent=2, ensure_ascii=False)
    if out_path:
        Path(out_path).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "-o",
        "--output",
        default=str(Path(__file__).resolve().parent / "schema_snapshot.json"),
        help="UTF-8 JSON output path",
    )
    args = ap.parse_args()
    main(out_path=args.output)
