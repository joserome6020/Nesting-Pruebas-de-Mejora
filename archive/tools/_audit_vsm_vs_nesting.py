"""Audita jobs en VSM (foldertree) vs nestingpro_db."""
from __future__ import annotations

import os
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

NEST = dict(
    host="192.168.2.80",
    port=5433,
    dbname="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=12,
)
VSM = dict(
    host="192.168.2.80",
    port=5437,
    dbname="foldertree",
    user="user",
    password="password",
    connect_timeout=12,
)


def _cols(cur, table: str) -> list[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position
        """,
        (table,),
    )
    return [r[0] if isinstance(r, tuple) else r["column_name"] for r in cur.fetchall()]


def fetch_vsm_jobs() -> set[str]:
    out: set[str] = set()
    conn = psycopg2.connect(**VSM)
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if "jobs" not in [t for t in _tables(cur)]:
            print("[VSM] tabla jobs no encontrada")
            return out
        cols = _cols(cur, "jobs")
        num_col = next((c for c in ("job_number", "numero_job", "number", "code") if c in cols), None)
        id_col = next((c for c in ("id", "id_job") if c in cols), None)
        title_col = next((c for c in ("title", "name", "titulo") if c in cols), None)
        sel = [c for c in (id_col, num_col, title_col) if c]
        cur.execute(f'SELECT {", ".join(sel)} FROM jobs ORDER BY {id_col or sel[0]}')
        for row in cur.fetchall():
            if num_col and row.get(num_col):
                out.add(str(row[num_col]).strip())
            if title_col and row.get(title_col):
                t = str(row[title_col]).strip()
                if t:
                    out.add(t)
            if id_col and row.get(id_col) is not None:
                out.add(str(row[id_col]).strip())
        print(f"[VSM] jobs en foldertree: {len(out)}")
        for j in sorted(out)[:40]:
            print(f"  VSM: {j}")
        if len(out) > 40:
            print(f"  ... +{len(out)-40} más")
    finally:
        conn.close()
    return out


def _tables(cur) -> list[str]:
    cur.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1
        """
    )
    return [r[0] if isinstance(r, tuple) else r["table_name"] for r in cur.fetchall()]


def fetch_nesting_jobs() -> dict[str, dict]:
    sources: dict[str, set[str]] = {}
    conn = psycopg2.connect(**NEST)
    try:
        cur = conn.cursor()
        probes = [
            ("jobs", "numero_job"),
            ("reporte_cortes", "job"),
            ("lista_largos_job", "job"),
            ("lista_largos_swo", "job"),
            ("erp_jobs", "job_number"),
            ("material_requerido_ldg", "job"),
            ("pqart_wo", "nombre_wo"),
            ("pqart_swo", "nombre_swo"),
            ("sheets", "titulo"),
        ]
        for table, col in probes:
            try:
                cur.execute(
                    f"SELECT DISTINCT TRIM({col}::text) AS v FROM {table} "
                    f"WHERE {col} IS NOT NULL AND TRIM({col}::text) <> '' ORDER BY 1"
                )
                vals = {str(r[0]).strip() for r in cur.fetchall() if r[0]}
                sources[table] = vals
                print(f"[NEST] {table}.{col}: {len(vals)}")
                for v in sorted(vals)[:25]:
                    print(f"    {v}")
                if len(vals) > 25:
                    print(f"    ... +{len(vals)-25}")
            except Exception as exc:
                print(f"[NEST] skip {table}.{col}: {exc}")

        all_jobs: set[str] = set()
        for vals in sources.values():
            all_jobs |= vals
        return {"sources": sources, "all": all_jobs}
    finally:
        conn.close()


def main() -> int:
    print("=== AUDIT VSM vs NESTING ===\n")
    vsm = fetch_vsm_jobs()
    print()
    nest = fetch_nesting_jobs()
    all_nest = nest["all"]

    # Normalizar: job numérico 62140 etc.
    vsm_nums = {v for v in vsm if v.isdigit()}
    nest_nums = {v for v in all_nest if v.isdigit()}

    only_nest = sorted(all_nest - vsm)
    only_vsm = sorted(vsm - all_nest)

    print("\n=== SOLO EN NESTING (candidatos a borrar) ===")
    for x in only_nest[:50]:
        print(f"  {x}")
    if len(only_nest) > 50:
        print(f"  ... +{len(only_nest)-50}")

    print("\n=== SOLO EN VSM (no en nesting) ===")
    for x in only_vsm[:30]:
        print(f"  {x}")

    test_hits = [x for x in all_nest if "prueba" in x.lower() or "kva" in x.lower() or "test" in x.lower()]
    print("\n=== PATRONES PRUEBA/TEST ===")
    for x in sorted(test_hits):
        print(f"  {x}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
