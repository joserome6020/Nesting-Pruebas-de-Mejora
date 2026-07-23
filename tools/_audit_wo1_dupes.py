import psycopg2
from psycopg2.extras import RealDictCursor

cfg = dict(host="192.168.2.80", port=5433, dbname="nestingpro_db", user="postgres", password="nesting123")
WO = "W.O. 1"

conn = psycopg2.connect(**cfg)
cur = conn.cursor(cursor_factory=RealDictCursor)

print("=== reporte_cortes por WO ===")
cur.execute(
    """
    SELECT work_order, COUNT(*) AS n,
           MIN(fecha_corte) AS min_fecha, MAX(fecha_corte) AS max_fecha,
           COUNT(DISTINCT placa_id) AS placas_distintas,
           COUNT(DISTINCT sheet_uid) AS sheets_distintas
    FROM reporte_cortes
    WHERE TRIM(work_order) = %s OR TRIM(work_order) ILIKE %s
    GROUP BY work_order
    ORDER BY work_order
    """,
    (WO, f"{WO}%"),
)
for r in cur.fetchall():
    print(dict(r))

print("\n=== reporte_cortes por fecha_corte (WO) ===")
cur.execute(
    """
    SELECT DATE_TRUNC('minute', fecha_corte) AS bloque,
           COUNT(*) AS piezas,
           COUNT(DISTINCT placa_id) AS placas,
           MIN(fecha_corte) AS desde, MAX(fecha_corte) AS hasta
    FROM reporte_cortes
    WHERE TRIM(work_order) = %s
    GROUP BY 1
    ORDER BY 1
    """,
    (WO,),
)
for r in cur.fetchall():
    print(dict(r))

print("\n=== placas duplicadas (mismo placa_id, multiples bloques fecha) ===")
cur.execute(
    """
    SELECT placa_id, COUNT(*) AS piezas,
           MIN(fecha_corte) AS min_f, MAX(fecha_corte) AS max_f,
           COUNT(DISTINCT DATE_TRUNC('minute', fecha_corte)) AS bloques
    FROM reporte_cortes
    WHERE TRIM(work_order) = %s AND placa_id IS NOT NULL
    GROUP BY placa_id
    HAVING COUNT(DISTINCT DATE_TRUNC('minute', fecha_corte)) > 1
       OR COUNT(*) > 50
    ORDER BY bloques DESC, piezas DESC
    LIMIT 30
    """,
    (WO,),
)
rows = cur.fetchall()
print(f"placas con posible duplicado: {len(rows)}")
for r in rows[:15]:
    print(dict(r))

print("\n=== pqart_wo ===")
cur.execute(
    """
    SELECT nombre_wo, COUNT(*) AS n, MIN(created_at) AS min_c, MAX(created_at) AS max_c
    FROM pqart_wo
    WHERE TRIM(nombre_wo) = %s OR TRIM(nombre_wo) ILIKE %s
    GROUP BY nombre_wo
    """,
    (WO, f"{WO}%"),
)
for r in cur.fetchall():
    print(dict(r))

print("\n=== material_requerido_ldg WO ===")
cur.execute(
    """
    SELECT orden_id, tipo_orden, COUNT(*) AS n, MIN(created_at) AS min_c, MAX(created_at) AS max_c
    FROM material_requerido_ldg
    WHERE tipo_orden = 'WO' AND TRIM(orden_id) ILIKE %s
    GROUP BY orden_id, tipo_orden
    """,
    (f"%{WO.replace('W.O. ', '')}%",),
)
for r in cur.fetchall():
    print(dict(r))

cur.close()
conn.close()
