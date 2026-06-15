#!/usr/bin/env python3
"""
Regenera material_requerido_ldg para WO/SWO que ya tienen lista_largos_job.

Flujo: lista de largos (medidas) -> plan de corte de barras -> pedido en BD.
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import psycopg2
from psycopg2.extras import RealDictCursor

from lista_largos_material_requerido import asegurar_tabla_material_requerido_ldg


def _db_config() -> dict:
    return {
        "host": os.getenv("NESTING_DB_HOST", "192.168.2.80"),
        "database": os.getenv("NESTING_DB_NAME", "nestingpro_db"),
        "user": os.getenv("NESTING_DB_USER", "postgres"),
        "password": os.getenv("NESTING_DB_PASSWORD", "nesting123"),
        "port": os.getenv("NESTING_DB_PORT", "5433"),
    }


def _asegurar_orden(cursor, orden_id: str, tipo_orden: str) -> tuple[bool, str]:
    import api_server

    api_server.asegurar_tablas_lista_largos_operativas()
    return api_server._asegurar_material_requerido_orden(cursor, orden_id, tipo_orden)


def _jobs_con_lista(cursor) -> list[str]:
    cursor.execute(
        """
        SELECT DISTINCT TRIM(job) AS job
        FROM public.lista_largos_job
        WHERE job IS NOT NULL AND TRIM(job) <> ''
        ORDER BY 1
        """
    )
    return [str(r["job"]).strip() for r in (cursor.fetchall() or []) if r.get("job")]


def _wos_de_job(cursor, job: str) -> list[str]:
    cursor.execute(
        """
        SELECT DISTINCT TRIM(work_order) AS work_order
        FROM reporte_cortes
        WHERE TRIM(job) = %s AND work_order IS NOT NULL
        ORDER BY 1
        """,
        (job.strip(),),
    )
    return [str(r["work_order"]).strip() for r in (cursor.fetchall() or []) if r.get("work_order")]


def _swos_de_job(cursor, job: str) -> list[str]:
    cursor.execute(
        """
        SELECT DISTINCT TRIM(super_work_order) AS swo
        FROM reporte_cortes
        WHERE TRIM(job) = %s
          AND super_work_order IS NOT NULL
          AND TRIM(super_work_order) <> ''
        ORDER BY 1
        """,
        (job.strip(),),
    )
    return [str(r["swo"]).strip() for r in (cursor.fetchall() or []) if r.get("swo")]


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill material_requerido_ldg")
    parser.add_argument("--job", action="append", default=[], help="Job(s) a procesar")
    parser.add_argument("--wo", action="append", default=[], help="WO específica(s)")
    parser.add_argument("--swo", action="append", default=[], help="SWO específica(s)")
    parser.add_argument(
        "--scan-jobs",
        action="store_true",
        help="Todos los jobs con filas en lista_largos_job",
    )
    args = parser.parse_args()

    asegurar_tabla_material_requerido_ldg()
    db = _db_config()
    logs: list[dict] = []

    conexion = psycopg2.connect(**db)
    cursor = conexion.cursor(cursor_factory=RealDictCursor)

    try:
        if args.job:
            import api_server

            for job in args.job:
                pedidos = api_server._propagar_material_requerido_por_job(db, job)
                logs.extend(pedidos or [])

        ordenes: list[tuple[str, str]] = []
        for wo in args.wo:
            ordenes.append((wo.strip(), "WO"))
        for swo in args.swo:
            ordenes.append((swo.strip(), "SWO"))

        if args.scan_jobs:
            for job in _jobs_con_lista(cursor):
                for wo in _wos_de_job(cursor, job):
                    ordenes.append((wo, "WO"))
                for swo in _swos_de_job(cursor, job):
                    ordenes.append((swo, "SWO"))

        vistos: set[tuple[str, str]] = set()
        for orden_id, tipo in ordenes:
            clave = (orden_id, tipo)
            if not orden_id or clave in vistos:
                continue
            vistos.add(clave)
            try:
                ok, msg = _asegurar_orden(cursor, orden_id, tipo)
                conexion.commit()
                logs.append(
                    {"orden_id": orden_id, "tipo_orden": tipo, "ok": ok, "mensaje": msg}
                )
            except Exception as e:
                conexion.rollback()
                logs.append(
                    {
                        "orden_id": orden_id,
                        "tipo_orden": tipo,
                        "ok": False,
                        "mensaje": str(e),
                    }
                )

        if not logs:
            print("Nada que procesar. Usa --job, --wo, --swo o --scan-jobs.")
            return 1

        ok_n = sum(1 for x in logs if x.get("ok"))
        print(f"Procesadas {len(logs)} orden(es); exitosas={ok_n}")
        for row in logs:
            mark = "OK" if row.get("ok") else "FAIL"
            print(
                f"  [{mark}] {row.get('tipo_orden')} {row.get('orden_id')}: "
                f"{row.get('mensaje')}"
            )
        return 0 if ok_n == len(logs) else 2
    finally:
        cursor.close()
        conexion.close()


if __name__ == "__main__":
    raise SystemExit(main())
