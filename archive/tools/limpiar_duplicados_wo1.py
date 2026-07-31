"""Limpia duplicados WO1: conserva bloque mas viejo, borra re-nests posteriores."""
import psycopg2
from psycopg2.extras import RealDictCursor

cfg = dict(host="192.168.2.80", port=5433, dbname="nestingpro_db", user="postgres", password="nesting123")
WO_PATTERN = "W.O. 1%"

conn = psycopg2.connect(**cfg)
conn.autocommit = False
cur = conn.cursor(cursor_factory=RealDictCursor)

print("=== ANTES ===")
cur.execute(
    """
    SELECT DATE_TRUNC('minute', fecha_registro) AS bloque, COUNT(*) AS n
    FROM costos_prorrateo
    WHERE work_order ILIKE %s
    GROUP BY 1 ORDER BY 1
    """,
    (WO_PATTERN,),
)
bloques = cur.fetchall()
for b in bloques:
    print(dict(b))

if not bloques:
    print("Sin costos_prorrateo para WO1")
    conn.rollback()
    cur.close()
    conn.close()
    raise SystemExit(0)

bloque_keep = bloques[0]["bloque"]
print(f"\nConservar bloque: {bloque_keep}")

cur.execute(
    """
    DELETE FROM costos_prorrateo
    WHERE work_order ILIKE %s
      AND DATE_TRUNC('minute', fecha_registro) > %s
    RETURNING id, plate_id, fecha_registro
    """,
    (WO_PATTERN, bloque_keep),
)
deleted_costos = cur.fetchall()
print(f"Eliminados costos_prorrateo: {len(deleted_costos)}")

# pqart_wo: duplicado exacto PLC120 P1 (misma WO y timestamp)
cur.execute(
    """
    SELECT id, nombre_wo, sheet_display_name, nombre_dxf, created_at
    FROM pqart_wo
    WHERE nombre_wo ILIKE %s AND sheet_display_name = 'PLC120 P1'
    ORDER BY id
    """,
    (WO_PATTERN,),
)
pq_dupes = cur.fetchall()
if len(pq_dupes) > 1:
    ids_del = [r["id"] for r in pq_dupes[1:]]
    cur.execute("DELETE FROM pqart_wo WHERE id = ANY(%s) RETURNING id", (ids_del,))
    deleted_pq = cur.fetchall()
    print(f"Eliminados pqart_wo duplicados: {len(deleted_pq)} ids={[r['id'] for r in deleted_pq]}")
else:
    print("pqart_wo: sin duplicados PLC120")

conn.commit()

print("\n=== DESPUES ===")
cur.execute(
    """
    SELECT DATE_TRUNC('minute', fecha_registro) AS bloque, COUNT(*) AS n
    FROM costos_prorrateo
    WHERE work_order ILIKE %s
    GROUP BY 1 ORDER BY 1
    """,
    (WO_PATTERN,),
)
for b in cur.fetchall():
    print(dict(b))

cur.execute(
    """
    SELECT COUNT(*) AS n, MIN(fecha_corte) AS min_f, MAX(fecha_corte) AS max_f
    FROM reporte_cortes WHERE work_order ILIKE %s
    """,
    (WO_PATTERN,),
)
print("reporte_cortes:", dict(cur.fetchone()))

cur.execute("SELECT COUNT(*) AS n FROM pqart_wo WHERE nombre_wo ILIKE %s", (WO_PATTERN,))
print("pqart_wo:", dict(cur.fetchone()))

cur.close()
conn.close()
print("\nLIMPIEZA WO1 COMPLETA")
