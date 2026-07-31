"""Reconstruye MRL SWO desde su demanda canónica y valida cobertura total."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api.legacy_core import _ll_obtener_o_generar_plan, _ll_rows_para_orden
from interface.largos_nesting_service import _conexion_bd
from interface.postgres_connector import _asegurar_tabla_lista_largos_swo, _guardar_lista_largos_swo
from lista_largos_material_requerido import reconstruir_pedido_desde_plan


def _movimiento_operativo(cursor, swo: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS(
            SELECT 1 FROM material_requerido_ldg
            WHERE BTRIM(orden_id) = %s AND tipo_orden = 'SWO'
              AND (
                COALESCE(kit_recibido, FALSE)
                OR provider_handshake_at IS NOT NULL
                OR almacen_received_at IS NOT NULL
                OR incoming_handshake_at IS NOT NULL
                OR COALESCE(rechazado_incoming, FALSE)
              )
        ) AS operativo
        """,
        (swo,),
    )
    row = cursor.fetchone() or {}
    return bool(row.get("operativo")) if isinstance(row, dict) else bool(row[0])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--swo", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    swo = str(args.swo or "").strip()

    conexion, cursor_factory = _conexion_bd()
    cursor = conexion.cursor(cursor_factory=cursor_factory)
    try:
        if _movimiento_operativo(cursor, swo):
            print("BLOQUEADO: la MRL SWO ya tiene movimiento operativo.")
            return 2

        rows, jobs = _ll_rows_para_orden(cursor, swo, "SWO")
        demanda = sum(int(row.get("cantidad") or 0) for row in rows)
        plan, _row_plan = _ll_obtener_o_generar_plan(cursor, swo, "SWO", reservar=False)
        plan = dict(plan or {})
        piezas_plan = int(plan.get("total_piezas") or 0)
        barras_plan = int(plan.get("total_barras") or 0)
        print(
            f"SWO={swo} jobs={jobs} filas={len(rows)} "
            f"demanda={demanda} plan_piezas={piezas_plan} plan_barras={barras_plan}"
        )
        if not rows or demanda <= 0 or piezas_plan != demanda or barras_plan <= 0:
            print("BLOQUEADO: el plan no cubre exactamente la demanda canónica.")
            return 3

        if not args.apply:
            print("Simulación correcta; no se modificó MRL ni el plan.")
            return 0

        ok, mensaje = reconstruir_pedido_desde_plan(cursor, swo, "SWO", plan)
        if not ok:
            print(f"BLOQUEADO: {mensaje}")
            return 4

        # La lista visible de VSM se deriva de la misma fuente, pero no debe
        # ser requisito para calcular MRL. La reconstruimos ahora que CAD/PG
        # ya fueron validados, evitando que aparezca antes de una exportación.
        # _guardar_lista_largos_swo es código legado que consume filas
        # posicionales; se usa un cursor estándar para no mezclarlo con el
        # RealDictCursor de las validaciones anteriores.
        cursor_tracking = conexion.cursor()
        try:
            cursor_tracking.execute(
                """
                SELECT DISTINCT BTRIM(job) AS job, BTRIM(work_order) AS work_order
                FROM reporte_cortes
                WHERE BTRIM(super_work_order) = %s
                  AND BTRIM(job) <> ''
                  AND BTRIM(work_order) <> ''
                ORDER BY 1, 2
                """,
                (swo,),
            )
            datos_tracking = []
            for job, wo in cursor_tracking.fetchall() or []:
                job, wo = str(job).strip(), str(wo).strip()
                if not job or not wo:
                    continue
                match = re.search(r"X\s*(\d+)", wo.upper())
                factor = max(1, int(match.group(1))) if match else 1
                datos_tracking.append(
                    (swo, factor, "", 0, 0, 0, 0, 0, "", "", job, wo, 0, 0, 0, 0, 0)
                )
            _asegurar_tabla_lista_largos_swo(cursor_tracking)
            filas_lista = _guardar_lista_largos_swo(cursor_tracking, swo, datos_tracking)
        finally:
            cursor_tracking.close()
        conexion.commit()
        print(f"MRL regenerada: {mensaje}; lista SWO reconstruida={filas_lista} filas.")
        return 0
    except Exception:
        conexion.rollback()
        raise
    finally:
        cursor.close()
        conexion.close()


if __name__ == "__main__":
    raise SystemExit(main())
