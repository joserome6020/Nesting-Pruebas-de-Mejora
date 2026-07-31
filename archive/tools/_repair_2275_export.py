"""Reparación post-export job 06-30-2275TANK25325 — MRL y verificación."""
from __future__ import annotations

import sys

ROOT = r"c:\Proyectos\New Arga Nesting Suite"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import psycopg2
from psycopg2.extras import RealDictCursor

import api_server
from lista_largos_material_requerido import asegurar_tabla_material_requerido_ldg

JOB = "06-30-2275TANK25325"
WO = "W.O. 9 X9"
DB = dict(
    host="192.168.2.80",
    port="5433",
    database="nestingpro_db",
    user="postgres",
    password="nesting123",
    connect_timeout=12,
)


def main() -> int:
    asegurar_tabla_material_requerido_ldg(DB)
    conn = psycopg2.connect(**DB)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    api_server.asegurar_tablas_lista_largos_operativas()

    ok, msg = api_server._asegurar_material_requerido_orden(cur, WO, "WO")
    print(f"MRL {WO}: ok={ok} | {msg}")
    conn.commit()

    cur.execute(
        """
        SELECT COUNT(*) AS n, COALESCE(SUM(cantidad), 0) AS qty
        FROM material_requerido_ldg
        WHERE TRIM(orden_id) = %s AND tipo_orden = 'WO'
        """,
        (WO,),
    )
    print("mrl_summary", dict(cur.fetchone() or {}))

    pedidos = api_server._propagar_material_requerido_por_job(DB, JOB)
    print("propagate_job", pedidos)
    conn.commit()

    cur.execute(
        "SELECT COUNT(*) AS n FROM lista_largos_job WHERE TRIM(job) = %s",
        (JOB,),
    )
    print("lista_largos_rows", dict(cur.fetchone() or {}))

    cur.execute(
        "SELECT COUNT(*) AS n FROM reporte_cortes WHERE TRIM(job) = %s",
        (JOB,),
    )
    print("reporte_cortes_rows", dict(cur.fetchone() or {}))

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
