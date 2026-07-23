"""Reparación post-export job 25349HEADIRON — MRL, VSM y verificación."""
from __future__ import annotations

import sys

ROOT = r"c:\Proyectos\New Arga Nesting Suite"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import psycopg2
from psycopg2.extras import RealDictCursor

import api_server
from lista_largos_material_requerido import asegurar_tabla_material_requerido_ldg
from modules.nesting_engine.api_client import avanzar_job_centralizado, trigger_pedido_po

JOB = "25349HEADIRON"
JOB_VSM = "25349TANK_HEADIRON"
WO = "W.O. 11 X9"
DB = dict(
    host="192.168.2.80",
    port="5433",
    database="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=12,
)


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    asegurar_tabla_material_requerido_ldg(DB)
    api_server.asegurar_tablas_lista_largos_operativas()

    conn = psycopg2.connect(**DB)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    _print_section("reporte_cortes / lista_largos")
    cur.execute(
        "SELECT COUNT(*) AS n FROM reporte_cortes WHERE TRIM(job) = %s",
        (JOB,),
    )
    print("reporte_cortes", dict(cur.fetchone() or {}))
    cur.execute(
        "SELECT COUNT(*) AS n FROM lista_largos_job WHERE TRIM(job) = %s",
        (JOB,),
    )
    print("lista_largos_job", dict(cur.fetchone() or {}))
    cur.execute(
        """
        SELECT perfil_estructural, material_grade, cantidad_total
        FROM lista_largos_job
        WHERE TRIM(job) = %s
        ORDER BY perfil_estructural
        """,
        (JOB,),
    )
    for row in cur.fetchall() or []:
        print("  largo", dict(row))

    _print_section("material_requerido_ldg ANTES")
    cur.execute(
        """
        SELECT material, codigo, cantidad, largo
        FROM material_requerido_ldg
        WHERE TRIM(orden_id) = %s AND tipo_orden = 'WO'
        ORDER BY material, largo
        """,
        (WO,),
    )
    antes = cur.fetchall() or []
    print(f"filas={len(antes)}")
    for row in antes:
        print(" ", dict(row))

    _print_section("Regenerar MRL")
    ok, msg = api_server._asegurar_material_requerido_orden(cur, WO, "WO")
    print(f"MRL {WO}: ok={ok} | {msg}")
    conn.commit()

    _print_section("material_requerido_ldg DESPUÉS")
    cur.execute(
        """
        SELECT material, codigo, cantidad, largo
        FROM material_requerido_ldg
        WHERE TRIM(orden_id) = %s AND tipo_orden = 'WO'
        ORDER BY material, largo
        """,
        (WO,),
    )
    despues = cur.fetchall() or []
    print(f"filas={len(despues)}")
    for row in despues:
        print(" ", dict(row))

    _print_section("Propagar por job")
    pedidos = api_server._propagar_material_requerido_por_job(DB, JOB, solo_work_order=WO)
    print("propagate_job", pedidos)

    cur.close()
    conn.close()

    _print_section("VSM avance (idempotente)")
    try:
        av = avanzar_job_centralizado(JOB)
        print("avanzar_job", av)
    except Exception as e:
        print("avanzar_job ERROR", e)

    _print_section("PEDIDO-PO (puede fallar en servidor)")
    for j in (JOB, JOB_VSM):
        try:
            po = trigger_pedido_po(j)
            print(f"pedido_po {j}:", po)
        except Exception as e:
            print(f"pedido_po {j} ERROR:", e)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
