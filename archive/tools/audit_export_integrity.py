"""Auditoría de solo lectura para exportaciones, MRL y checkpoints."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from interface.largos_nesting_service import _conexion_bd


def _rows(cursor, query: str, params=()):
    cursor.execute(query, params)
    return [dict(row) for row in (cursor.fetchall() or [])]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", help="Limita el reporte a un Job concreto.")
    args = parser.parse_args()
    filtro = str(args.job or "").strip()

    conexion, cursor_factory = _conexion_bd()
    cursor = conexion.cursor(cursor_factory=cursor_factory)
    try:
        where_job = "WHERE (%s = '' OR BTRIM(job) = %s)"
        params = (filtro, filtro)
        reporte = {
            "job": filtro or "TODOS",
            "duplicados_reporte_cortes": _rows(
                cursor,
                f"""
                SELECT BTRIM(job) AS job, BTRIM(work_order) AS work_order,
                       NULLIF(BTRIM(super_work_order), '') AS swo,
                       placa_id, item, COUNT(*) AS filas
                FROM reporte_cortes
                {where_job}
                -- Dos piezas iguales pueden compartir placa e item de forma
                -- legítima. La geometría serializada identifica una inserción
                -- realmente repetida sin señalar falsos duplicados.
                GROUP BY 1, 2, 3, 4, 5, geometria
                HAVING COUNT(*) > 1
                ORDER BY filas DESC, job, work_order
                """,
                params,
            ),
            "mrl_wo_duplicado_con_swo": _rows(
                cursor,
                f"""
                WITH relaciones AS (
                    SELECT DISTINCT BTRIM(job) AS job, BTRIM(work_order) AS wo,
                           BTRIM(super_work_order) AS swo
                    FROM reporte_cortes
                    WHERE NULLIF(BTRIM(super_work_order), '') IS NOT NULL
                      AND (%s = '' OR BTRIM(job) = %s)
                ),
                mrl_wo AS (
                    SELECT BTRIM(orden_id) AS wo, COUNT(*) AS filas,
                           SUM(cantidad) AS barras
                    FROM material_requerido_ldg
                    WHERE tipo_orden = 'WO'
                    GROUP BY 1
                ),
                mrl_swo AS (
                    SELECT BTRIM(orden_id) AS swo, COUNT(*) AS filas,
                           SUM(cantidad) AS barras
                    FROM material_requerido_ldg
                    WHERE tipo_orden = 'SWO'
                    GROUP BY 1
                )
                SELECT r.job, r.wo, r.swo, mw.filas AS wo_filas,
                       mw.barras AS wo_barras, ms.filas AS swo_filas,
                       ms.barras AS swo_barras
                FROM relaciones r
                JOIN mrl_wo mw ON mw.wo = r.wo
                JOIN mrl_swo ms ON ms.swo = r.swo
                ORDER BY r.job, r.wo
                """,
                params,
            ),
            "pqart_rutas_duplicadas": _rows(
                cursor,
                """
                WITH ordenes AS (
                    SELECT DISTINCT BTRIM(job) AS job, BTRIM(work_order) AS wo,
                           NULLIF(BTRIM(super_work_order), '') AS swo
                    FROM reporte_cortes
                    WHERE (%s = '' OR BTRIM(job) = %s)
                ),
                pq AS (
                    SELECT o.job, 'WO' AS tipo, p.nombre_wo AS orden_id,
                           LOWER(p.ruta) AS ruta, COUNT(*) AS filas
                    FROM pqart_wo p JOIN ordenes o ON BTRIM(p.nombre_wo) = o.wo
                    GROUP BY o.job, p.nombre_wo, LOWER(p.ruta)
                    HAVING COUNT(*) > 1
                    UNION ALL
                    SELECT o.job, 'SWO' AS tipo, p.nombre_swo AS orden_id,
                           LOWER(p.ruta) AS ruta, COUNT(*) AS filas
                    FROM pqart_swo p JOIN ordenes o ON BTRIM(p.nombre_swo) = o.swo
                    WHERE o.swo IS NOT NULL
                    GROUP BY o.job, p.nombre_swo, LOWER(p.ruta)
                    HAVING COUNT(*) > 1
                )
                SELECT * FROM pq ORDER BY job, tipo, orden_id, ruta
                """,
                params,
            ),
            "operacion_largos_protegida": _rows(
                cursor,
                """
                WITH ordenes AS (
                    SELECT DISTINCT BTRIM(job) AS job, BTRIM(work_order) AS wo,
                           NULLIF(BTRIM(super_work_order), '') AS swo
                    FROM reporte_cortes
                    WHERE (%s = '' OR BTRIM(job) = %s)
                )
                SELECT DISTINCT o.job, BTRIM(m.orden_id) AS orden_id,
                       m.tipo_orden
                FROM material_requerido_ldg m
                JOIN ordenes o ON (
                    (m.tipo_orden = 'WO' AND BTRIM(m.orden_id) = o.wo)
                    OR (m.tipo_orden = 'SWO' AND BTRIM(m.orden_id) = o.swo)
                )
                WHERE COALESCE(m.kit_recibido, FALSE)
                   OR m.provider_handshake_at IS NOT NULL
                   OR m.almacen_received_at IS NOT NULL
                   OR m.incoming_handshake_at IS NOT NULL
                   OR COALESCE(m.rechazado_incoming, FALSE)
                ORDER BY o.job, m.tipo_orden, orden_id
                """,
                params,
            ),
            "sesiones_largos_activas": _rows(
                cursor,
                """
                WITH ordenes AS (
                    SELECT DISTINCT BTRIM(job) AS job, BTRIM(work_order) AS wo,
                           NULLIF(BTRIM(super_work_order), '') AS swo
                    FROM reporte_cortes
                    WHERE (%s = '' OR BTRIM(job) = %s)
                )
                SELECT DISTINCT o.job, s.orden_id, s.tipo_orden, s.operador,
                       s.bahia, s.estado, s.hora_ultimo_movimiento
                FROM lista_largos_sesiones s
                JOIN ordenes o ON (
                    (s.tipo_orden = 'WO' AND BTRIM(s.orden_id) = o.wo)
                    OR (s.tipo_orden = 'SWO' AND BTRIM(s.orden_id) = o.swo)
                )
                WHERE s.estado = 'activa'
                ORDER BY o.job, s.hora_ultimo_movimiento DESC
                """,
                params,
            ),
            "checkpoints_pendientes": [],
        }
        cursor.execute("SELECT to_regclass('public.export_stage_checkpoints') AS tabla")
        if (cursor.fetchone() or {}).get("tabla"):
            reporte["checkpoints_pendientes"] = _rows(
                cursor,
                """
                SELECT scope_id, scope_type, stage, status, detail, http_status,
                       updated_at
                FROM export_stage_checkpoints
                WHERE status <> 'OK'
                  AND (%s = '' OR scope_id = %s)
                ORDER BY updated_at DESC
                """,
                params,
            )

        print(json.dumps(reporte, ensure_ascii=False, indent=2, default=str))
        return 1 if (
            reporte["duplicados_reporte_cortes"]
            or reporte["mrl_wo_duplicado_con_swo"]
            or reporte["pqart_rutas_duplicadas"]
            or reporte["checkpoints_pendientes"]
        ) else 0
    finally:
        cursor.close()
        conexion.close()


if __name__ == "__main__":
    raise SystemExit(main())
