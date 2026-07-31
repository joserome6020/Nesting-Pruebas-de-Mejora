import psycopg2
from psycopg2.extras import RealDictCursor

cfg = dict(host="192.168.2.80", port=5433, dbname="nestingpro_db", user="postgres", password="nesting123")
conn = psycopg2.connect(**cfg)
cur = conn.cursor(cursor_factory=RealDictCursor)

for title, sql in [
    ("costos detalle por bloque", """
        SELECT id, plate_id, fecha_registro, wo_origen, job, costo_asignado
        FROM costos_prorrateo WHERE work_order ILIKE 'W.O. 1%'
        ORDER BY plate_id, fecha_registro, id
    """),
    ("erp_piezas_tracking", """
        SELECT COUNT(*) n FROM erp_piezas_tracking
    """),
    ("erp_piezas por placa", """
        SELECT p.codigo_placa, COUNT(*) n
        FROM erp_piezas_tracking pt
        JOIN erp_placas_tracking p ON p.id_placa = pt.id_placa
        GROUP BY 1 ORDER BY n DESC LIMIT 25
    """),
    ("pqart_wo detalle", """
        SELECT id, nombre_wo, nombre_dxf, sheet_display_name, created_at
        FROM pqart_wo WHERE nombre_wo ILIKE 'W.O. 1%'
        ORDER BY created_at, id
    """),
    ("lista_largos_swo", "SELECT COUNT(*) n FROM lista_largos_swo"),
    ("diccionario_swo", "SELECT * FROM diccionario_swo"),
    ("reportes_dinamicos", "SELECT * FROM reportes_dinamicos"),
]:
    print(f"\n=== {title} ===")
    cur.execute(sql)
    rows = cur.fetchall()
    print(f"count: {len(rows)}")
    for r in rows[:50]:
        print(dict(r))

cur.close()
conn.close()
