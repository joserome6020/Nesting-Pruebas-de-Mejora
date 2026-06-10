import argparse
import os
import sys
from typing import Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

# USO DEL SCRIPT:
#
# Reset completo de una SWO:
# python Reset_consumo_lista_de_largos.py --orden "SWO-001" --tipo SWO --modo completo
#
# Reset completo de una WO:
# python Reset_consumo_lista_de_largos.py --orden "W.O. 9 X2" --tipo WO --modo completo
#
# Reset ligero de una SWO:
# python Reset_consumo_lista_de_largos.py --orden "SWO-001" --tipo SWO --modo ligero
#
# Reset ligero de una WO:
# python Reset_consumo_lista_de_largos.py --orden "W.O. 9 X2" --tipo WO --modo ligero
#
# Para ejecutar sin confirmación manual:
# python Reset_consumo_lista_de_largos.py --orden "SWO-001" --tipo SWO --modo completo --yes
#
# PARAMETROS:
# --orden  = WO o SWO a resetear
# --tipo   = WO o SWO
# --modo   = ligero | completo
# --yes    = ejecuta sin pedir confirmación
#
# MODO ligero:
# - borra sesiones
# - borra turnos
# - borra eventos de piezas
# - borra eventos de sobrantes
# - borra cortes
# - borra sobrantes
# - borra plan congelado
#
# MODO completo:
# - hace todo lo del modo ligero
# - libera remanentes reservados por esa orden
# - elimina remanentes creados por esa orden

DB_CONFIG = {
    "host": "localhost",
    "port": 5433,
    "dbname": "nestingpro_db",
    "user": "postgres",
    "password": "nesting123",
}

DB_TIMEZONE = os.getenv("DB_TIMEZONE", "America/Chihuahua")


def db_connect():
    conexion = psycopg2.connect(**DB_CONFIG)
    cursor = conexion.cursor()
    cursor.execute(f"SET TIME ZONE '{DB_TIMEZONE}';")
    cursor.close()
    return conexion


def safe_int(value, default=0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def row_value(row, key, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def normalizar_tipo_orden(tipo: str) -> str:
    tipo_norm = (tipo or "").strip().upper()
    if tipo_norm not in ("WO", "SWO"):
        raise ValueError("tipo debe ser 'WO' o 'SWO'")
    return tipo_norm


def normalizar_orden_id(orden_id: str) -> str:
    orden = (orden_id or "").strip()
    if not orden:
        raise ValueError("orden_id es obligatorio")
    return orden


def table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SELECT to_regclass(%s) AS reg;", (table_name,))
    row = cursor.fetchone()
    return bool(row_value(row, "reg"))


def count_rows(cursor, query: str, params: tuple) -> int:
    cursor.execute(query, params)
    row = cursor.fetchone()
    return safe_int(row_value(row, "total"), 0)


def delete_from_first_existing_table(
    cursor,
    candidates: Tuple[str, ...],
    where_sql: str,
    params: tuple,
) -> Tuple[int, Optional[str]]:
    for table_name in candidates:
        if table_exists(cursor, table_name):
            cursor.execute(f"DELETE FROM {table_name} WHERE {where_sql};", params)
            return cursor.rowcount, table_name
    return 0, None


def obtener_conteos_previos(cursor, orden: str, tipo: str) -> dict:
    resumen = {}

    if table_exists(cursor, "lista_largos_sesiones"):
        resumen["sesiones_previas"] = count_rows(
            cursor,
            """
            SELECT COUNT(*) AS total
            FROM lista_largos_sesiones
            WHERE TRIM(orden_id) = %s
              AND tipo_orden = %s;
            """,
            (orden, tipo),
        )
    else:
        resumen["sesiones_previas"] = 0

    if table_exists(cursor, "lista_largos_turnos"):
        resumen["turnos_previos"] = count_rows(
            cursor,
            """
            SELECT COUNT(*) AS total
            FROM lista_largos_turnos
            WHERE TRIM(orden_id) = %s
              AND tipo_orden = %s;
            """,
            (orden, tipo),
        )
    else:
        resumen["turnos_previos"] = 0

    if table_exists(cursor, "lista_largos_eventos_pieza"):
        resumen["eventos_pieza_previos"] = count_rows(
            cursor,
            """
            SELECT COUNT(*) AS total
            FROM lista_largos_eventos_pieza
            WHERE TRIM(orden_id) = %s
              AND tipo_orden = %s;
            """,
            (orden, tipo),
        )
    else:
        resumen["eventos_pieza_previos"] = 0

    if table_exists(cursor, "lista_largos_eventos_sobrante"):
        resumen["eventos_sobrante_previos"] = count_rows(
            cursor,
            """
            SELECT COUNT(*) AS total
            FROM lista_largos_eventos_sobrante
            WHERE TRIM(orden_id) = %s
              AND tipo_orden = %s;
            """,
            (orden, tipo),
        )
    else:
        resumen["eventos_sobrante_previos"] = 0

    if table_exists(cursor, "lista_largos_cortes"):
        resumen["cortes_previos"] = count_rows(
            cursor,
            """
            SELECT COUNT(*) AS total
            FROM lista_largos_cortes
            WHERE TRIM(orden_id) = %s
              AND tipo_orden = %s;
            """,
            (orden, tipo),
        )
    else:
        resumen["cortes_previos"] = 0

    resumen["sobrantes_previos"] = 0
    resumen["tabla_sobrantes"] = None
    for tabla in ("lista_largos_sobrantes", "lista_largos_sobrante"):
        if table_exists(cursor, tabla):
            resumen["tabla_sobrantes"] = tabla
            resumen["sobrantes_previos"] = count_rows(
                cursor,
                f"""
                SELECT COUNT(*) AS total
                FROM {tabla}
                WHERE TRIM(orden_id) = %s
                  AND tipo_orden = %s;
                """,
                (orden, tipo),
            )
            break

    resumen["planes_previos"] = 0
    resumen["tabla_planes"] = None
    for tabla in ("lista_largos_planes", "lista_largos_plan"):
        if table_exists(cursor, tabla):
            resumen["tabla_planes"] = tabla
            resumen["planes_previos"] = count_rows(
                cursor,
                f"""
                SELECT COUNT(*) AS total
                FROM {tabla}
                WHERE TRIM(orden_id) = %s
                  AND tipo_orden = %s;
                """,
                (orden, tipo),
            )
            break

    if table_exists(cursor, "lista_largos_remanentes"):
        resumen["remanentes_creados_previos"] = count_rows(
            cursor,
            """
            SELECT COUNT(*) AS total
            FROM lista_largos_remanentes
            WHERE COALESCE(TRIM(fuente_orden_id), '') = %s
              AND fuente_tipo_orden = %s;
            """,
            (orden, tipo),
        )

        resumen["remanentes_reservados_previos"] = count_rows(
            cursor,
            """
            SELECT COUNT(*) AS total
            FROM lista_largos_remanentes
            WHERE COALESCE(TRIM(reservado_para_orden_id), '') = %s
              AND reservado_para_tipo_orden = %s
              AND NOT (
                    COALESCE(TRIM(fuente_orden_id), '') = %s
                AND fuente_tipo_orden = %s
              );
            """,
            (orden, tipo, orden, tipo),
        )
    else:
        resumen["remanentes_creados_previos"] = 0
        resumen["remanentes_reservados_previos"] = 0

    return resumen


def resetear_orden_lista_largos(orden: str, tipo: str, modo: str) -> dict:
    conexion = None
    cursor = None

    try:
        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        resumen_prev = obtener_conteos_previos(cursor, orden, tipo)

        sesiones_borradas = 0
        turnos_borrados = 0
        eventos_pieza_borrados = 0
        eventos_sobrante_borrados = 0
        cortes_borrados = 0
        sobrantes_borrados = 0
        planes_borrados = 0
        remanentes_liberados = 0
        remanentes_eliminados = 0

        # Primero borra auditoría fina
        if table_exists(cursor, "lista_largos_eventos_pieza"):
            cursor.execute(
                """
                DELETE FROM lista_largos_eventos_pieza
                WHERE TRIM(orden_id) = %s
                  AND tipo_orden = %s;
                """,
                (orden, tipo),
            )
            eventos_pieza_borrados = cursor.rowcount

        if table_exists(cursor, "lista_largos_eventos_sobrante"):
            cursor.execute(
                """
                DELETE FROM lista_largos_eventos_sobrante
                WHERE TRIM(orden_id) = %s
                  AND tipo_orden = %s;
                """,
                (orden, tipo),
            )
            eventos_sobrante_borrados = cursor.rowcount

        if table_exists(cursor, "lista_largos_turnos"):
            cursor.execute(
                """
                DELETE FROM lista_largos_turnos
                WHERE TRIM(orden_id) = %s
                  AND tipo_orden = %s;
                """,
                (orden, tipo),
            )
            turnos_borrados = cursor.rowcount

        # Luego estado operativo
        if table_exists(cursor, "lista_largos_cortes"):
            cursor.execute(
                """
                DELETE FROM lista_largos_cortes
                WHERE TRIM(orden_id) = %s
                  AND tipo_orden = %s;
                """,
                (orden, tipo),
            )
            cortes_borrados = cursor.rowcount

        sobrantes_borrados, tabla_sobrantes_borrada = delete_from_first_existing_table(
            cursor,
            ("lista_largos_sobrantes", "lista_largos_sobrante"),
            "TRIM(orden_id) = %s AND tipo_orden = %s",
            (orden, tipo),
        )

        planes_borrados, tabla_planes_borrada = delete_from_first_existing_table(
            cursor,
            ("lista_largos_planes", "lista_largos_plan"),
            "TRIM(orden_id) = %s AND tipo_orden = %s",
            (orden, tipo),
        )

        if table_exists(cursor, "lista_largos_sesiones"):
            cursor.execute(
                """
                DELETE FROM lista_largos_sesiones
                WHERE TRIM(orden_id) = %s
                  AND tipo_orden = %s;
                """,
                (orden, tipo),
            )
            sesiones_borradas = cursor.rowcount

        if modo == "completo" and table_exists(cursor, "lista_largos_remanentes"):
            cursor.execute(
                """
                UPDATE lista_largos_remanentes
                SET status = 'DISPONIBLE',
                    reservado_para_orden_id = NULL,
                    reservado_para_tipo_orden = NULL,
                    updated_at = NOW()
                WHERE COALESCE(TRIM(reservado_para_orden_id), '') = %s
                  AND reservado_para_tipo_orden = %s
                  AND NOT (
                        COALESCE(TRIM(fuente_orden_id), '') = %s
                    AND fuente_tipo_orden = %s
                  );
                """,
                (orden, tipo, orden, tipo),
            )
            remanentes_liberados = cursor.rowcount

            cursor.execute(
                """
                DELETE FROM lista_largos_remanentes
                WHERE COALESCE(TRIM(fuente_orden_id), '') = %s
                  AND fuente_tipo_orden = %s;
                """,
                (orden, tipo),
            )
            remanentes_eliminados = cursor.rowcount

        conexion.commit()

        return {
            "orden_id": orden,
            "tipo_orden": tipo,
            "modo": modo,
            "sesiones_previas": resumen_prev["sesiones_previas"],
            "sesiones_borradas": sesiones_borradas,
            "turnos_previos": resumen_prev["turnos_previos"],
            "turnos_borrados": turnos_borrados,
            "eventos_pieza_previos": resumen_prev["eventos_pieza_previos"],
            "eventos_pieza_borrados": eventos_pieza_borrados,
            "eventos_sobrante_previos": resumen_prev["eventos_sobrante_previos"],
            "eventos_sobrante_borrados": eventos_sobrante_borrados,
            "cortes_previos": resumen_prev["cortes_previos"],
            "cortes_borrados": cortes_borrados,
            "sobrantes_previos": resumen_prev["sobrantes_previos"],
            "sobrantes_borrados": sobrantes_borrados,
            "tabla_sobrantes": resumen_prev["tabla_sobrantes"] or tabla_sobrantes_borrada,
            "planes_previos": resumen_prev["planes_previos"],
            "planes_borrados": planes_borrados,
            "tabla_planes": resumen_prev["tabla_planes"] or tabla_planes_borrada,
            "remanentes_creados_previos": resumen_prev["remanentes_creados_previos"],
            "remanentes_reservados_previos": resumen_prev["remanentes_reservados_previos"],
            "remanentes_liberados": remanentes_liberados,
            "remanentes_eliminados": remanentes_eliminados,
        }

    except Exception:
        if conexion:
            conexion.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


def imprimir_resumen(resumen: dict):
    print("\n" + "=" * 72)
    print("RESET DE CONSUMO LISTA DE LARGOS")
    print("=" * 72)
    print(f"Orden:                    {resumen['orden_id']}")
    print(f"Tipo:                     {resumen['tipo_orden']}")
    print(f"Modo:                     {resumen['modo']}")
    print("-" * 72)
    print(f"Sesiones previas:         {resumen['sesiones_previas']}")
    print(f"Sesiones borradas:        {resumen['sesiones_borradas']}")
    print(f"Turnos previos:           {resumen['turnos_previos']}")
    print(f"Turnos borrados:          {resumen['turnos_borrados']}")
    print(f"Evt. pieza previos:       {resumen['eventos_pieza_previos']}")
    print(f"Evt. pieza borrados:      {resumen['eventos_pieza_borrados']}")
    print(f"Evt. sobrante previos:    {resumen['eventos_sobrante_previos']}")
    print(f"Evt. sobrante borrados:   {resumen['eventos_sobrante_borrados']}")
    print(f"Cortes previos:           {resumen['cortes_previos']}")
    print(f"Cortes borrados:          {resumen['cortes_borrados']}")
    print(f"Sobrantes previos:        {resumen['sobrantes_previos']}")
    print(f"Sobrantes borrados:       {resumen['sobrantes_borrados']}")
    print(f"Tabla sobrantes:          {resumen['tabla_sobrantes'] or '-'}")
    print(f"Planes previos:           {resumen['planes_previos']}")
    print(f"Planes borrados:          {resumen['planes_borrados']}")
    print(f"Tabla planes:             {resumen['tabla_planes'] or '-'}")
    print(f"Rem. creados previos:     {resumen['remanentes_creados_previos']}")
    print(f"Rem. reservados previos:  {resumen['remanentes_reservados_previos']}")
    print(f"Rem. liberados:           {resumen['remanentes_liberados']}")
    print(f"Rem. eliminados:          {resumen['remanentes_eliminados']}")
    print("=" * 72)
    print("La orden quedó lista para volver a probarse en lista de largos.")
    print("=" * 72 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Resetea el consumo de lista de largos de una WO/SWO para pruebas."
    )
    parser.add_argument(
        "--orden",
        required=True,
        help='WO o SWO a resetear. Ejemplo: "SWO-001" o "W.O. 9 X2"',
    )
    parser.add_argument(
        "--tipo",
        required=True,
        choices=["WO", "SWO", "wo", "swo"],
        help="Tipo de orden",
    )
    parser.add_argument(
        "--modo",
        default="completo",
        choices=["ligero", "completo"],
        help="ligero = limpia sesión/plan/consumo/auditoría; completo = además remanentes",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Ejecuta sin pedir confirmación",
    )

    args = parser.parse_args()

    try:
        orden = normalizar_orden_id(args.orden)
        tipo = normalizar_tipo_orden(args.tipo)
        modo = (args.modo or "completo").strip().lower()

        if not args.yes:
            print("\nVas a resetear esta orden de lista de largos:")
            print(f"  Orden: {orden}")
            print(f"  Tipo:  {tipo}")
            print(f"  Modo:  {modo}")
            confirm = input("\nEscribe SI para continuar: ").strip().upper()
            if confirm != "SI":
                print("Operación cancelada.")
                return

        resumen = resetear_orden_lista_largos(orden, tipo, modo)
        imprimir_resumen(resumen)

    except KeyboardInterrupt:
        print("\nOperación cancelada por el usuario.")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()