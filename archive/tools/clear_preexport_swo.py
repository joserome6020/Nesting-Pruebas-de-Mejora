"""Limpia solo el snapshot técnico pre-export de una SWO, con guardas."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from interface.largos_nesting_service import _conexion_bd


def _scalar(cursor, query: str, params: tuple) -> int:
    cursor.execute(query, params)
    row = cursor.fetchone()
    return int((row or {}).get("n") or 0) if isinstance(row, dict) else int(row[0] or 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--swo", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    swo = str(args.swo or "").strip()

    conexion, cursor_factory = _conexion_bd()
    cursor = conexion.cursor(cursor_factory=cursor_factory)
    try:
        counts = {
            "lista_largos_swo": _scalar(
                cursor,
                "SELECT COUNT(*) AS n FROM lista_largos_swo WHERE BTRIM(super_work_order) = %s",
                (swo,),
            ),
            "plan_swo": _scalar(
                cursor,
                "SELECT COUNT(*) AS n FROM lista_largos_planes WHERE BTRIM(orden_id) = %s AND tipo_orden = 'SWO'",
                (swo,),
            ),
            "mrl_swo": _scalar(
                cursor,
                "SELECT COUNT(*) AS n FROM material_requerido_ldg WHERE BTRIM(orden_id) = %s AND tipo_orden = 'SWO'",
                (swo,),
            ),
            "pqart_swo": _scalar(
                cursor,
                "SELECT COUNT(*) AS n FROM pqart_swo WHERE BTRIM(nombre_swo) = %s",
                (swo,),
            ),
            "reporte_swo": _scalar(
                cursor,
                "SELECT COUNT(*) AS n FROM reporte_cortes WHERE BTRIM(super_work_order) = %s",
                (swo,),
            ),
        }
        blockers = {
            "sesion_activa": _scalar(
                cursor,
                "SELECT COUNT(*) AS n FROM lista_largos_sesiones WHERE BTRIM(orden_id) = %s AND tipo_orden = 'SWO' AND estado = 'activa'",
                (swo,),
            ),
            "cortes_operativos": _scalar(
                cursor,
                "SELECT COUNT(*) AS n FROM lista_largos_cortes WHERE BTRIM(orden_id) = %s AND tipo_orden = 'SWO'",
                (swo,),
            ),
            "mrl_operativo": _scalar(
                cursor,
                """
                SELECT COUNT(*) AS n FROM material_requerido_ldg
                WHERE BTRIM(orden_id) = %s AND tipo_orden = 'SWO'
                  AND (
                      COALESCE(kit_recibido, FALSE)
                      OR provider_handshake_at IS NOT NULL
                      OR almacen_received_at IS NOT NULL
                      OR incoming_handshake_at IS NOT NULL
                      OR COALESCE(rechazado_incoming, FALSE)
                  )
                """,
                (swo,),
            ),
            "pqart_no_pendiente": _scalar(
                cursor,
                "SELECT COUNT(*) AS n FROM pqart_swo WHERE BTRIM(nombre_swo) = %s AND COALESCE(ls, '') NOT ILIKE 'Pendiente%%'",
                (swo,),
            ),
            "reporte_no_pendiente": _scalar(
                cursor,
                "SELECT COUNT(*) AS n FROM reporte_cortes WHERE BTRIM(super_work_order) = %s AND COALESCE(estatus, '') NOT ILIKE 'Pendiente%%'",
                (swo,),
            ),
        }
        print("Snapshot:", counts)
        print("Bloqueos:", blockers)
        if any(blockers.values()):
            print("No se modificó nada: la SWO tiene estado operativo o congelado.")
            return 2
        if not args.apply:
            print("Simulación: la limpieza sería segura; no se modificó nada.")
            return 0

        deletes = (
            ("pqart_swo", "DELETE FROM pqart_swo WHERE BTRIM(nombre_swo) = %s"),
            ("reporte_cortes", "DELETE FROM reporte_cortes WHERE BTRIM(super_work_order) = %s"),
            ("material_requerido_ldg", "DELETE FROM material_requerido_ldg WHERE BTRIM(orden_id) = %s AND tipo_orden = 'SWO'"),
            ("lista_largos_planes", "DELETE FROM lista_largos_planes WHERE BTRIM(orden_id) = %s AND tipo_orden = 'SWO'"),
            ("lista_largos_swo", "DELETE FROM lista_largos_swo WHERE BTRIM(super_work_order) = %s"),
        )
        removed = {}
        for name, query in deletes:
            cursor.execute(query, (swo,))
            removed[name] = cursor.rowcount
        conexion.commit()
        print("Limpieza aplicada:", removed)
        return 0
    except Exception:
        conexion.rollback()
        raise
    finally:
        cursor.close()
        conexion.close()


if __name__ == "__main__":
    raise SystemExit(main())
