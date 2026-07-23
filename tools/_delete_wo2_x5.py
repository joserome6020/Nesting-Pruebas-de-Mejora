"""Auditar y borrar W.O. 2 X5 (export de prueba accidental)."""
import sys
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
WO = "W.O. 2 X5"


def audit(cur):
    print(f"=== Registros para {WO} ===\n")
    checks = [
        (
            "reporte_cortes",
            "SELECT job, work_order, COUNT(*) FROM reporte_cortes WHERE TRIM(work_order) = %s GROUP BY 1,2",
            (WO,),
        ),
        (
            "pqart_wo",
            "SELECT nombre_wo, COUNT(*) FROM pqart_wo WHERE TRIM(nombre_wo) = %s GROUP BY 1",
            (WO,),
        ),
        (
            "material_requerido_ldg",
            "SELECT COUNT(*) AS n FROM material_requerido_ldg WHERE TRIM(orden_id) = %s AND tipo_orden = 'WO'",
            (WO,),
        ),
        (
            "lista_largos_planes",
            "SELECT COUNT(*) AS n FROM lista_largos_planes WHERE TRIM(orden_id) = %s AND tipo_orden = 'WO'",
            (WO,),
        ),
    ]
    for label, sql, params in checks:
        try:
            cur.execute(sql, params)
            rows = cur.fetchall()
            print(f"{label}: {rows if rows else '(vacío)'}")
        except Exception as e:
            print(f"{label}: SKIP ({e})")


def delete_wo(cur):
    deleted = {}
    wo = WO

    def run(label, sql, params=()):
        cur.execute(sql, params)
        n = cur.rowcount
        if n:
            deleted[label] = n
        return n

    # Hijos lista_largos por orden
    for tbl in (
        "lista_largos_cortes",
        "lista_largos_eventos_pieza",
        "lista_largos_eventos_sobrante",
        "lista_largos_sobrantes",
        "lista_largos_sesiones",
        "lista_largos_turnos",
    ):
        try:
            run(
                tbl,
                f"DELETE FROM {tbl} WHERE TRIM(orden_id) = %s AND tipo_orden = 'WO'",
                (wo,),
            )
        except Exception:
            pass

    try:
        run(
            "lista_largos_remanentes",
            """
            DELETE FROM lista_largos_remanentes
            WHERE reservado_para_orden_id = %s AND reservado_para_tipo_orden = 'WO'
            """,
            (wo,),
        )
    except Exception:
        pass

    run(
        "lista_largos_planes",
        "DELETE FROM lista_largos_planes WHERE TRIM(orden_id) = %s AND tipo_orden = 'WO'",
        (wo,),
    )
    run(
        "material_requerido_ldg",
        "DELETE FROM material_requerido_ldg WHERE TRIM(orden_id) = %s AND tipo_orden = 'WO'",
        (wo,),
    )
    run(
        "reporte_cortes",
        "DELETE FROM reporte_cortes WHERE TRIM(work_order) = %s",
        (wo,),
    )
    run(
        "pqart_wo",
        "DELETE FROM pqart_wo WHERE TRIM(nombre_wo) = %s",
        (wo,),
    )

    for tbl, col in (
        ("erp_piezas_tracking", "wo_name"),
        ("erp_placas_tracking", "wo_name"),
        ("erp_work_orders", "nombre_wo"),
    ):
        try:
            run(tbl, f"DELETE FROM {tbl} WHERE TRIM({col}) = %s", (wo,))
        except Exception:
            pass

    return deleted


def main():
    do_delete = "--delete" in sys.argv
    conn = psycopg2.connect(**CFG)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=RealDictCursor)
    audit(cur)
    if do_delete:
        print("\n=== BORRANDO ===")
        deleted = delete_wo(cur)
        print("Eliminados:", deleted or "(nada)")
        print("\n=== DESPUÉS ===")
        audit(cur)
    else:
        print("\n(Pase --delete para borrar)")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
