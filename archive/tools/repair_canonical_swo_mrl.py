"""Elimina únicamente MRL WO duplicado cuando la SWO canónica está intacta."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from interface.largos_nesting_service import _conexion_bd


def _candidatos(cursor, job: str) -> list[dict]:
    cursor.execute(
        """
        WITH relaciones AS (
            SELECT DISTINCT BTRIM(job) AS job, BTRIM(work_order) AS wo,
                   BTRIM(super_work_order) AS swo
            FROM reporte_cortes
            WHERE NULLIF(BTRIM(super_work_order), '') IS NOT NULL
              AND (%s = '' OR BTRIM(job) = %s)
        ),
        sesiones_activas AS (
            SELECT DISTINCT BTRIM(orden_id) AS wo
            FROM lista_largos_sesiones
            WHERE tipo_orden = 'WO' AND estado = 'activa'
        )
        SELECT r.job, r.wo, r.swo,
               COUNT(DISTINCT mwo.id) AS wo_filas,
               COUNT(DISTINCT mswo.id) AS swo_filas,
               BOOL_OR(
                    COALESCE(mwo.kit_recibido, FALSE)
                    OR mwo.provider_handshake_at IS NOT NULL
                    OR mwo.almacen_received_at IS NOT NULL
                    OR mwo.incoming_handshake_at IS NOT NULL
                    OR COALESCE(mwo.rechazado_incoming, FALSE)
               ) AS wo_operativo,
               BOOL_OR(sa.wo IS NOT NULL) AS wo_sesion_activa
        FROM relaciones r
        JOIN material_requerido_ldg mwo
          ON BTRIM(mwo.orden_id) = r.wo AND mwo.tipo_orden = 'WO'
        JOIN material_requerido_ldg mswo
          ON BTRIM(mswo.orden_id) = r.swo AND mswo.tipo_orden = 'SWO'
        LEFT JOIN sesiones_activas sa ON sa.wo = r.wo
        GROUP BY r.job, r.wo, r.swo
        HAVING COUNT(DISTINCT mwo.id) > 0 AND COUNT(DISTINCT mswo.id) > 0
        ORDER BY r.job, r.wo
        """,
        (job, job),
    )
    return [dict(row) for row in (cursor.fetchall() or [])]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", default="", help="Job a reparar; vacío procesa todos.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica la reparación. Sin este flag solo muestra candidatos.",
    )
    args = parser.parse_args()
    job = str(args.job or "").strip()

    conexion, cursor_factory = _conexion_bd()
    cursor = conexion.cursor(cursor_factory=cursor_factory)
    try:
        candidatos = _candidatos(cursor, job)
        seguros = [
            row
            for row in candidatos
            if not bool(row.get("wo_operativo")) and not bool(row.get("wo_sesion_activa"))
        ]
        bloqueados = [
            row
            for row in candidatos
            if bool(row.get("wo_operativo")) or bool(row.get("wo_sesion_activa"))
        ]
        print(f"Candidatos: {len(candidatos)} | seguros: {len(seguros)} | bloqueados: {len(bloqueados)}")
        for row in bloqueados:
            motivo = (
                "sesión de largos activa"
                if bool(row.get("wo_sesion_activa"))
                else "movimiento operativo"
            )
            print(f"BLOQUEADO ({motivo}): {row['job']} {row['wo']} -> {row['swo']}")
        for row in seguros:
            print(f"SEGURO: {row['job']} {row['wo']} -> {row['swo']} ({row['wo_filas']} filas WO)")

        if not args.apply:
            print("Modo simulación; no se modificó información.")
            return 0

        eliminadas_mrl = eliminados_planes = 0
        for row in seguros:
            wo = str(row["wo"])
            cursor.execute(
                """
                DELETE FROM material_requerido_ldg
                WHERE BTRIM(orden_id) = %s AND tipo_orden = 'WO'
                """,
                (wo,),
            )
            eliminadas_mrl += cursor.rowcount

            cursor.execute(
                """
                DELETE FROM lista_largos_planes p
                WHERE BTRIM(p.orden_id) = %s
                  AND p.tipo_orden = 'WO'
                  AND NOT EXISTS (
                      SELECT 1 FROM lista_largos_sesiones s
                      WHERE BTRIM(s.orden_id) = p.orden_id
                        AND s.tipo_orden = p.tipo_orden
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM lista_largos_cortes c
                      WHERE BTRIM(c.orden_id) = p.orden_id
                        AND c.tipo_orden = p.tipo_orden
                  )
                """,
                (wo,),
            )
            eliminados_planes += cursor.rowcount
        conexion.commit()
        print(
            f"Reparación aplicada: MRL WO eliminada={eliminadas_mrl}, "
            f"planes WO huérfanos eliminados={eliminados_planes}."
        )
        return 0
    except Exception:
        conexion.rollback()
        raise
    finally:
        cursor.close()
        conexion.close()


if __name__ == "__main__":
    raise SystemExit(main())
