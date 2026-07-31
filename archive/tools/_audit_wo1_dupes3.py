import psycopg2
from psycopg2.extras import RealDictCursor

cfg = dict(host="192.168.2.80", port=5433, dbname="nestingpro_db", user="postgres", password="nesting123")
conn = psycopg2.connect(**cfg)
cur = conn.cursor(cursor_factory=RealDictCursor)

queries = {
    "reporte_cortes all wo": """
        SELECT TRIM(work_order) wo, TRIM(super_work_order) swo, TRIM(job) job,
               COUNT(*) n, MIN(fecha_corte) min_f, MAX(fecha_corte) max_f
        FROM reporte_cortes GROUP BY 1,2,3 ORDER BY min_f
    """,
    "SWO-001 placas": """
        SELECT COALESCE(sheet_display_name, placa_id) placa, placa_id,
               COUNT(*) piezas, MIN(fecha_corte) min_f, MAX(fecha_corte) max_f
        FROM reporte_cortes WHERE TRIM(super_work_order) = 'SWO-001'
        GROUP BY 1,2 ORDER BY 1
    """,
    "nest_instance / source": """
        SELECT nest_instance_id, source_nest_name, COUNT(*) n,
               MIN(fecha_corte) min_f, MAX(fecha_corte) max_f
        FROM reporte_cortes
        WHERE work_order ILIKE 'W.O. 1%' OR super_work_order = 'SWO-001'
        GROUP BY 1,2 ORDER BY min_f
    """,
    "costos_prorrateo por placa": """
        SELECT plate_id, COUNT(*) n, MIN(fecha_registro) min_f, MAX(fecha_registro) max_f
        FROM costos_prorrateo WHERE work_order ILIKE 'W.O. 1%'
        GROUP BY 1 ORDER BY n DESC
    """,
    "costos bloques fecha": """
        SELECT DATE_TRUNC('minute', fecha_registro) t, COUNT(*) n
        FROM costos_prorrateo WHERE work_order ILIKE 'W.O. 1%'
        GROUP BY 1 ORDER BY 1
    """,
    "erp_placas_tracking": """
        SELECT codigo_placa, id_swo, COUNT(*) n FROM erp_placas_tracking
        GROUP BY 1,2 ORDER BY n DESC LIMIT 30
    """,
    "erp_super_work_orders": "SELECT * FROM erp_super_work_orders",
    "erp_work_orders": "SELECT * FROM erp_work_orders",
    "pqart_swo": """
        SELECT nombre_swo, COUNT(*) n, MIN(created_at) min_c, MAX(created_at) max_c
        FROM pqart_swo GROUP BY 1
    """,
    "material_requerido": """
        SELECT orden_id, tipo_orden, COUNT(*) n, MIN(created_at) min_c, MAX(created_at) max_c
        FROM material_requerido_ldg GROUP BY 1,2 ORDER BY min_c
    """,
    "lista_largos_job": """
        SELECT job, COUNT(*) n, MIN(importado_el) min_i, MAX(importado_el) max_i
        FROM lista_largos_job GROUP BY 1
    """,
    "sheet_uid dup cross wo": """
        SELECT sheet_uid, COUNT(DISTINCT work_order) wos, COUNT(*) piezas,
               STRING_AGG(DISTINCT work_order, ', ') wos_list,
               MIN(fecha_corte) min_f, MAX(fecha_corte) max_f
        FROM reporte_cortes
        WHERE sheet_uid IS NOT NULL AND sheet_uid != ''
        GROUP BY sheet_uid
        HAVING COUNT(DISTINCT work_order) > 1 OR COUNT(*) > 100
        ORDER BY piezas DESC LIMIT 20
    """,
}

for title, sql in queries.items():
    print(f"\n=== {title} ===")
    try:
        cur.execute(sql)
        rows = cur.fetchall()
        print(f"count: {len(rows)}")
        for r in rows:
            print(dict(r))
    except Exception as e:
        conn.rollback()
        print("ERR", e)

cur.close()
conn.close()
