import psycopg2
from psycopg2.extras import RealDictCursor

cfg = dict(host="192.168.2.80", port=5433, dbname="nestingpro_db", user="postgres", password="nesting123")
conn = psycopg2.connect(**cfg)
cur = conn.cursor(cursor_factory=RealDictCursor)

patterns = ["W.O. 1%", "WO 1%", "SWO%"]

for label, sql in [
    ("work_orders en reporte_cortes", """
        SELECT TRIM(work_order) AS wo, TRIM(super_work_order) AS swo, COUNT(*) n,
               MIN(fecha_corte) min_f, MAX(fecha_corte) max_f
        FROM reporte_cortes
        WHERE work_order ILIKE 'W.O. 1%' OR work_order ILIKE 'WO 1%'
        GROUP BY 1,2 ORDER BY min_f
    """),
    ("jobs", """
        SELECT DISTINCT TRIM(job) AS job, TRIM(work_order) AS wo, COUNT(*) n
        FROM reporte_cortes
        WHERE work_order ILIKE 'W.O. 1%' OR job ILIKE '%62174%'
        GROUP BY 1,2
    """),
    ("placas por wo (sheet_display)", """
        SELECT TRIM(work_order) AS wo, COALESCE(sheet_display_name, sheet_code, placa_id) AS placa,
               COUNT(*) piezas, MIN(fecha_corte) min_f, MAX(fecha_corte) max_f,
               STRING_AGG(DISTINCT placa_id::text, ', ') AS placa_ids
        FROM reporte_cortes
        WHERE work_order ILIKE 'W.O. 1%'
        GROUP BY 1,2
        ORDER BY 1,2
    """),
    ("placa_id repetido en misma wo", """
        SELECT TRIM(work_order) AS wo, placa_id, COUNT(*) AS veces,
               COUNT(DISTINCT item) AS items_distintos,
               MIN(fecha_corte) min_f, MAX(fecha_corte) max_f
        FROM reporte_cortes
        WHERE work_order ILIKE 'W.O. 1%'
        GROUP BY 1,2
        HAVING COUNT(*) > 1
        ORDER BY veces DESC
        LIMIT 25
    """),
    ("todos los bloques fecha job 62174", """
        SELECT DATE_TRUNC('second', fecha_corte) AS t, work_order, COUNT(*) n,
               COUNT(DISTINCT placa_id) placas
        FROM reporte_cortes
        WHERE job ILIKE '%62174%' OR work_order ILIKE 'W.O. 1%'
        GROUP BY 1,2 ORDER BY t
    """),
    ("SWO relacionadas", """
        SELECT super_work_order, work_order, COUNT(*) n, MIN(fecha_corte) min_f
        FROM reporte_cortes
        WHERE super_work_order IS NOT NULL AND super_work_order != ''
           OR work_order ILIKE 'W.O. 1%'
        GROUP BY 1,2
        HAVING super_work_order IS NOT NULL
        ORDER BY min_f
    """),
    ("costos_prorrateo", """
        SELECT work_order, COUNT(*) n, MIN(fecha_registro) min_f, MAX(fecha_registro) max_f
        FROM costos_prorrateo
        WHERE work_order ILIKE 'W.O. 1%'
        GROUP BY 1
    """),
    ("erp placas", """
        SELECT e.nombre_swo, w.nombre_wo, COUNT(*) n
        FROM erp_work_orders w
        JOIN erp_super_work_orders e ON e.id_swo = w.id_job
        LIMIT 5
    """),
]:
    print(f"\n=== {label} ===")
    try:
        cur.execute(sql)
        rows = cur.fetchall()
        print(f"filas: {len(rows)}")
        for r in rows[:40]:
            print(dict(r))
    except Exception as e:
        conn.rollback()
        print("ERR", e)

cur.close()
conn.close()
