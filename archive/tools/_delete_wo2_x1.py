"""Auditar y borrar W.O. 2 X1 (export accidental)."""
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
WO = "W.O. 2 X1"


def audit(cur):
    print(f"=== Registros para {WO} ===\n")
    checks = [
        (
            "reporte_cortes (work_order)",
            "SELECT job, work_order, COUNT(*) AS n FROM reporte_cortes WHERE TRIM(work_order) = %s GROUP BY 1,2",
            (WO,),
        ),
        (
            "reporte_cortes (job)",
            "SELECT job, work_order, COUNT(*) AS n FROM reporte_cortes WHERE TRIM(job) = %s GROUP BY 1,2",
            (WO,),
        ),
        (
            "pqart_wo",
            "SELECT nombre_wo, COUNT(*) AS n FROM pqart_wo WHERE TRIM(nombre_wo) = %s GROUP BY 1",
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
        (
            "lista_largos_job",
            "SELECT id, job, work_order FROM lista_largos_job WHERE TRIM(work_order) = %s OR TRIM(job) = %s",
            (WO, WO),
        ),
        (
            "costos_prorrateo",
            "SELECT COUNT(*) AS n FROM costos_prorrateo WHERE TRIM(work_order) = %s OR TRIM(orden_id) = %s",
            (WO, WO),
        ),
        (
            "erp_jobs",
            "SELECT job_number, COUNT(*) AS n FROM erp_jobs WHERE job_number ILIKE %s GROUP BY 1",
            (f"%{WO}%",),
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
            deleted[label] = deleted.get(label, 0) + n
        return n

    # Hijos lista_largos por orden WO
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

    # lista_largos_job y hijos por job_id
    try:
        cur.execute(
            """
            SELECT id FROM lista_largos_job
            WHERE TRIM(work_order) = %s OR TRIM(job) = %s
            """,
            (wo, wo),
        )
        job_ids = [r["id"] for r in cur.fetchall()]
        if job_ids:
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
                        f"{tbl}_by_job_id",
                        f"DELETE FROM {tbl} WHERE job_id = ANY(%s)",
                        (job_ids,),
                    )
                except Exception:
                    pass
            run("lista_largos_job", "DELETE FROM lista_largos_job WHERE id = ANY(%s)", (job_ids,))
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
        "DELETE FROM reporte_cortes WHERE TRIM(work_order) = %s OR TRIM(job) = %s",
        (wo, wo),
    )
    run(
        "pqart_wo",
        "DELETE FROM pqart_wo WHERE TRIM(nombre_wo) = %s OR ruta ILIKE %s",
        (wo, f"%{wo.replace('.', '%')}%"),
    )
    try:
        run(
            "costos_prorrateo",
            "DELETE FROM costos_prorrateo WHERE TRIM(work_order) = %s OR TRIM(orden_id) = %s",
            (wo, wo),
        )
    except Exception:
        pass
    try:
        run("lista_largos_swo", "DELETE FROM lista_largos_swo WHERE job ILIKE %s", (f"%{wo}%",))
    except Exception:
        pass

    for tbl, col in (
        ("erp_piezas_tracking", "wo_name"),
        ("erp_placas_tracking", "wo_name"),
        ("erp_work_orders", "nombre_wo"),
        ("erp_jobs", "job_number"),
        ("erp_super_work_orders", "nombre_swo"),
    ):
        try:
            run(tbl, f"DELETE FROM {tbl} WHERE TRIM({col}) = %s OR {col} ILIKE %s", (wo, f"%{wo}%"))
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
