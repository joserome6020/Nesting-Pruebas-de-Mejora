"""Audita / limpia MRL y lista_largos para empatar captura Nesteo (solo Pedir)."""
from __future__ import annotations

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

# Captura: Pedir = estos códigos; No = HR164, TUB007, TUB010, TUB017
KEEP_CODIGOS = ("ANG022", "ANG035", "CAN015", "PTR016", "RED027", "SLC051")
DROP_CODIGOS = ("HR164", "TUB007", "TUB010", "TUB017")
WO_ORDEN = "W.O. 1 X1"


def q(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()


def main() -> int:
    do_delete = "--delete" in sys.argv
    conn = psycopg2.connect(**CFG)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=RealDictCursor)

    print("=== Tablas lista_largos* ===")
    for r in q(
        cur,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name ILIKE 'lista_largos%%'
        ORDER BY 1
        """,
    ):
        print(" ", r["table_name"])

    print("\n=== material_requerido_ldg (todas las órdenes) ===")
    for r in q(
        cur,
        """
        SELECT orden_id, tipo_orden, COUNT(*) AS n
        FROM material_requerido_ldg
        GROUP BY 1, 2
        ORDER BY 1, 2
        """,
    ):
        print(" ", dict(r))

    print(f"\n=== MRL detalle orden ~ {WO_ORDEN!r} ===")
    rows = q(
        cur,
        """
        SELECT id, orden_id, tipo_orden, codigo, material, largo, cantidad
        FROM material_requerido_ldg
        WHERE TRIM(orden_id) ILIKE %s
           OR TRIM(orden_id) ILIKE %s
        ORDER BY tipo_orden, orden_id, codigo, id
        """,
        ("%W.O. 1%X1%", "%WO%1%X1%"),
    )
    for r in rows:
        flag = "KEEP" if str(r["codigo"] or "").strip().upper() in KEEP_CODIGOS else (
            "DROP" if str(r["codigo"] or "").strip().upper() in DROP_CODIGOS else "?"
        )
        print(f"  [{flag}] {dict(r)}")

    print("\n=== MRL con códigos DROP en cualquier orden ===")
    drop_rows = q(
        cur,
        """
        SELECT id, orden_id, tipo_orden, codigo, material, largo, cantidad
        FROM material_requerido_ldg
        WHERE UPPER(TRIM(codigo)) = ANY(%s)
        ORDER BY tipo_orden, orden_id, codigo, id
        """,
        (list(DROP_CODIGOS),),
    )
    for r in drop_rows:
        print(" ", dict(r))
    print(f"Total DROP candidatos MRL: {len(drop_rows)}")

    print("\n=== SWO ligadas a W.O. 1 / job X1 ===")
    # erp_super_work_orders / diccionario / reporte
    for sql, label in [
        (
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name='erp_super_work_orders'
            ORDER BY ordinal_position
            """,
            "cols erp_super_work_orders",
        ),
        (
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name='lista_largos_swo'
            ORDER BY ordinal_position
            LIMIT 30
            """,
            "cols lista_largos_swo",
        ),
        (
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name='lista_largos_job'
            ORDER BY ordinal_position
            LIMIT 30
            """,
            "cols lista_largos_job",
        ),
    ]:
        print(f"\n-- {label}")
        try:
            for r in q(cur, sql):
                print(" ", dict(r))
        except Exception as e:
            conn.rollback()
            print("  ERR", e)

    # Jobs / WOs relacionados
    print("\n=== lista_largos_job jobs que parecen WO1/X1 ===")
    try:
        for r in q(
            cur,
            """
            SELECT DISTINCT job, job_key, COUNT(*) AS n
            FROM lista_largos_job
            WHERE TRIM(job) ILIKE %s OR TRIM(job) ILIKE %s OR TRIM(job_key) ILIKE %s
            GROUP BY 1, 2
            ORDER BY 1
            """,
            ("%W.O. 1%", "%X1%", "%wo%1%x1%"),
        ):
            print(" ", dict(r))
    except Exception as e:
        conn.rollback()
        print(" ERR", e)

    print("\n=== lista_largos_job códigos DROP (por nombre/código en nombre) ===")
    try:
        for r in q(
            cur,
            """
            SELECT id, job, nombre, clasificacion, largo_in, cantidad, cantidad_total
            FROM lista_largos_job
            WHERE UPPER(nombre) LIKE ANY(%s)
               OR UPPER(COALESCE(clasificacion,'')) LIKE ANY(%s)
            ORDER BY job, nombre
            LIMIT 80
            """,
            (
                [f"%{c}%" for c in DROP_CODIGOS],
                [f"%{c}%" for c in DROP_CODIGOS],
            ),
        ):
            print(" ", dict(r))
    except Exception as e:
        conn.rollback()
        print(" ERR", e)

    print("\n=== lista_largos_swo con códigos DROP ===")
    try:
        for r in q(
            cur,
            """
            SELECT id, super_work_order, job, work_order, nombre, clasificacion, largo_in, cantidad
            FROM lista_largos_swo
            WHERE UPPER(nombre) LIKE ANY(%s)
            ORDER BY super_work_order, job, nombre
            LIMIT 80
            """,
            ([f"%{c}%" for c in DROP_CODIGOS],),
        ):
            print(" ", dict(r))
    except Exception as e:
        conn.rollback()
        print(" ERR", e)

    print("\n=== MRL SWO que contienen DROP o misma WO ===")
    try:
        for r in q(
            cur,
            """
            SELECT id, orden_id, tipo_orden, codigo, material, largo, cantidad
            FROM material_requerido_ldg
            WHERE tipo_orden = 'SWO'
              AND (
                UPPER(TRIM(codigo)) = ANY(%s)
                OR TRIM(orden_id) IN (
                    SELECT DISTINCT TRIM(super_work_order)
                    FROM lista_largos_swo
                    WHERE TRIM(work_order) ILIKE %s OR TRIM(job) ILIKE %s
                )
              )
            ORDER BY orden_id, codigo
            """,
            (list(DROP_CODIGOS), "%W.O. 1%X1%", "%X1%"),
        ):
            print(" ", dict(r))
    except Exception as e:
        conn.rollback()
        print(" ERR", e)

    if not do_delete:
        print("\nDry-run OK. Ejecute con --delete para borrar solo MRL (códigos DROP).")
        cur.close()
        conn.close()
        return 0

    # Solo pedido comercial (MRL). NO borrar lista_largos_job/swo: ahí viven
    # las piezas de corte que las SWO heredan de la WO/job.
    print("\n=== DELETE material_requerido_ldg (WO/SWO) códigos DROP ===")
    cur.execute(
        """
        DELETE FROM material_requerido_ldg
        WHERE UPPER(TRIM(codigo)) = ANY(%s)
        RETURNING id, orden_id, tipo_orden, codigo, largo, cantidad
        """,
        (list(DROP_CODIGOS),),
    )
    deleted_mrl = cur.fetchall()
    print(f"MRL deleted: {len(deleted_mrl)}")
    for r in deleted_mrl:
        print(" ", dict(r))

    print("\n=== Verificación post-delete MRL W.O. 1 X1 ===")
    for r in q(
        cur,
        """
        SELECT id, orden_id, tipo_orden, codigo, material, largo, cantidad
        FROM material_requerido_ldg
        WHERE TRIM(orden_id) = %s OR TRIM(orden_id) ILIKE %s
        ORDER BY codigo, id
        """,
        (WO_ORDEN, "%W.O. 1%X1%"),
    ):
        print(" ", dict(r))

    print("\n=== Resumen agregado esperado (captura Pedir) ===")
    for r in q(
        cur,
        """
        SELECT
          CASE
            WHEN UPPER(material) LIKE '%%ANGULO%%' OR UPPER(codigo) LIKE 'ANG%%' THEN 'Ángulo'
            WHEN UPPER(material) LIKE '%%CANAL%%' OR UPPER(codigo) LIKE 'CAN%%' THEN 'Canal'
            WHEN UPPER(material) LIKE '%%PTR%%' OR UPPER(codigo) LIKE 'PTR%%' THEN 'PTR'
            WHEN UPPER(material) LIKE '%%VARILLA%%' OR UPPER(codigo) LIKE 'RED%%' THEN 'Varilla'
            WHEN UPPER(material) LIKE '%%SOLERA%%' OR UPPER(codigo) LIKE 'SLC%%' THEN 'Solera'
            ELSE COALESCE(codigo, material)
          END AS material_tipo,
          codigo,
          largo,
          cantidad
        FROM material_requerido_ldg
        WHERE TRIM(orden_id) = %s AND tipo_orden = 'WO'
        ORDER BY codigo
        """,
        (WO_ORDEN,),
    ):
        print(" ", dict(r))

    print("\n=== Códigos DROP restantes en MRL ===")
    left = q(
        cur,
        """
        SELECT orden_id, tipo_orden, codigo, COUNT(*) n
        FROM material_requerido_ldg
        WHERE UPPER(TRIM(codigo)) = ANY(%s)
        GROUP BY 1,2,3
        """,
        (list(DROP_CODIGOS),),
    )
    print(" remaining:", left or "NONE")

    conn.commit()
    print("\nCOMMIT OK")
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
