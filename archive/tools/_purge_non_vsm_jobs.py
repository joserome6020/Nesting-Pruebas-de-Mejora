"""Elimina de nestingpro_db jobs que NO están en VSM (foldertree)."""
from __future__ import annotations

import os
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Eliminar_job_bd import DB_CONFIG, eliminar_job  # noqa: E402

NEST = {**DB_CONFIG, "host": "192.168.2.80", "connect_timeout": 12}
VSM = dict(
    host="192.168.2.80",
    port=5437,
    dbname="foldertree",
    user="user",
    password="password",
    connect_timeout=12,
)


def vsm_job_numbers() -> set[str]:
  """Números de job activos en VSM (foldertree.jobs)."""
  conn = psycopg2.connect(**VSM)
  try:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
      """
      SELECT column_name FROM information_schema.columns
      WHERE table_schema='public' AND table_name='jobs'
      """
    )
    cols = {r["column_name"] for r in cur.fetchall()}
    num_col = next((c for c in ("job_number", "numero_job", "number", "code") if c in cols), None)
    if not num_col:
      cur.execute("SELECT * FROM jobs LIMIT 1")
      if cur.description:
        cols_row = [d.name for d in cur.description]
        num_col = next((c for c in cols_row if "job" in c.lower() or "number" in c.lower()), cols_row[0])
    cur.execute(f'SELECT DISTINCT TRIM("{num_col}"::text) AS n FROM jobs WHERE "{num_col}" IS NOT NULL')
    nums = {str(r["n"]).strip() for r in cur.fetchall() if r.get("n")}
    # Solo números de job reales (5 dígitos típicos ARGA)
    return {n for n in nums if n.isdigit() and len(n) >= 5}
  finally:
    conn.close()


def nesting_distinct_jobs() -> set[str]:
  conn = psycopg2.connect(**NEST)
  try:
    cur = conn.cursor()
    found: set[str] = set()
    probes = [
      ("reporte_cortes", "job"),
      ("lista_largos_job", "job"),
      ("lista_largos_swo", "job"),
      ("erp_jobs", "job_number"),
      ("jobs", "numero_job"),
      ("sheets", "titulo"),
    ]
    for table, col in probes:
      try:
        cur.execute(
          f"SELECT DISTINCT TRIM({col}::text) FROM {table} "
          f"WHERE {col} IS NOT NULL AND TRIM({col}::text) <> ''"
        )
        found |= {str(r[0]).strip() for r in cur.fetchall()}
      except Exception:
        conn.rollback()
    return found
  finally:
    conn.close()


def main() -> int:
  vsm = vsm_job_numbers()
  nest_jobs = nesting_distinct_jobs()
  print(f"VSM jobs (5+ dígitos): {sorted(vsm)}")
  print(f"Jobs en nesting DB: {sorted(nest_jobs)}")

  to_delete: list[str] = []
  for job in sorted(nest_jobs):
    jnorm = job.strip()
    if not jnorm:
      continue
    # Conservar si el número de job está en VSM
    if jnorm.isdigit() and jnorm in vsm:
      print(f"[KEEP] {jnorm} (en VSM)")
      continue
    # Conservar si contiene un job VSM como token (ej. rutas con 62174)
    if any(v in jnorm for v in vsm):
      print(f"[KEEP] {jnorm} (referencia VSM)")
      continue
    to_delete.append(jnorm)

  if not to_delete:
    print("Nada que eliminar.")
    return 0

  print("\n=== A ELIMINAR ===")
  for j in to_delete:
    print(f"  - {j}")

  conn = psycopg2.connect(**NEST)
  conn.autocommit = False
  try:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    for job in to_delete:
      print(f"\n>>> Eliminando job: {job}")
      res = eliminar_job(cur, job)
      print(res)
    conn.commit()
    print("\n[OK] Commit realizado.")
  except Exception as exc:
    conn.rollback()
    print(f"\n[ERROR] Rollback: {exc}")
    raise
  finally:
    conn.close()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
