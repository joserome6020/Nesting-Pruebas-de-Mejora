import argparse
import os
import sys
from typing import Iterable

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor


DB_CONFIG = {
    "host": "localhost",
    "port": 5433,
    "dbname": "nestingpro_db",
    "user": "postgres",
    "password": "nesting123",
}

DB_TIMEZONE = os.getenv("DB_TIMEZONE", "America/Chihuahua")
SCHEMA = "public"

# Columnas típicas donde puede existir el nombre del job.
GENERIC_JOB_COLUMNS = ("job", "titulo", "job_name", "nombre_job")


def db_connect():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(f"SET TIME ZONE '{DB_TIMEZONE}';")
    cur.close()
    return conn


def table_exists(cur, table_name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS reg;", (f"{SCHEMA}.{table_name}",))
    row = cur.fetchone()
    return bool(row["reg"] if row else None)


def get_columns(cur, table_name: str) -> list[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position;
        """,
        (SCHEMA, table_name),
    )
    return [r["column_name"] for r in cur.fetchall()]


def count_where_equal(cur, table_name: str, column_name: str, value: str) -> int:
    query = sql.SQL("SELECT COUNT(*) AS total FROM {}.{} WHERE {} = %s").format(
        sql.Identifier(SCHEMA),
        sql.Identifier(table_name),
        sql.Identifier(column_name),
    )
    cur.execute(query, (value,))
    row = cur.fetchone()
    return int(row["total"] or 0)


def delete_where_equal(cur, table_name: str, column_name: str, value: str) -> int:
    query = sql.SQL("DELETE FROM {}.{} WHERE {} = %s").format(
        sql.Identifier(SCHEMA),
        sql.Identifier(table_name),
        sql.Identifier(column_name),
    )
    cur.execute(query, (value,))
    return cur.rowcount


def fetch_distinct_values(cur, table_name: str, column_name: str, where_col: str, where_val: str) -> list[str]:
    query = sql.SQL(
        """
        SELECT DISTINCT {}
        FROM {}.{}
        WHERE {} = %s
          AND {} IS NOT NULL
          AND TRIM({}::text) <> ''
        ORDER BY {};
        """
    ).format(
        sql.Identifier(column_name),
        sql.Identifier(SCHEMA),
        sql.Identifier(table_name),
        sql.Identifier(where_col),
        sql.Identifier(column_name),
        sql.Identifier(column_name),
        sql.Identifier(column_name),
    )
    cur.execute(query, (where_val,))
    return [str(r[column_name]).strip() for r in cur.fetchall()]


def delete_for_orders(cur, table_name: str, order_ids: Iterable[str], tipo_orden: str) -> int:
    order_ids = [o for o in order_ids if o]
    if not order_ids or not table_exists(cur, table_name):
        return 0

    query = sql.SQL(
        """
        DELETE FROM {}.{}
        WHERE tipo_orden = %s
          AND TRIM(orden_id) = ANY(%s);
        """
    ).format(sql.Identifier(SCHEMA), sql.Identifier(table_name))
    cur.execute(query, (tipo_orden, order_ids))
    return cur.rowcount


def delete_remanentes_for_orders(cur, order_ids: Iterable[str], tipo_orden: str) -> dict:
    order_ids = [o for o in order_ids if o]
    if not order_ids or not table_exists(cur, "lista_largos_remanentes"):
        return {"eliminados": 0, "liberados": 0}

    # Liberar remanentes reservados para esas órdenes
    cur.execute(
        """
        UPDATE public.lista_largos_remanentes
        SET status = 'DISPONIBLE',
            reservado_para_orden_id = NULL,
            reservado_para_tipo_orden = NULL,
            updated_at = NOW()
        WHERE reservado_para_tipo_orden = %s
          AND TRIM(COALESCE(reservado_para_orden_id, '')) = ANY(%s)
          AND NOT (
                fuente_tipo_orden = %s
            AND TRIM(COALESCE(fuente_orden_id, '')) = ANY(%s)
          );
        """,
        (tipo_orden, order_ids, tipo_orden, order_ids),
    )
    liberados = cur.rowcount

    # Eliminar remanentes creados por esas órdenes
    cur.execute(
        """
        DELETE FROM public.lista_largos_remanentes
        WHERE fuente_tipo_orden = %s
          AND TRIM(COALESCE(fuente_orden_id, '')) = ANY(%s);
        """,
        (tipo_orden, order_ids),
    )
    eliminados = cur.rowcount

    return {"eliminados": eliminados, "liberados": liberados}


def preview_job(cur, job_name: str):
    print("\n" + "=" * 80)
    print(f"PREVIEW DEL JOB: {job_name}")
    print("=" * 80)

    wos = []
    swos = []

    if table_exists(cur, "lista_largos_job"):
        cols = get_columns(cur, "lista_largos_job")
        if "job" in cols and "work_order" in cols:
            wos = fetch_distinct_values(cur, "lista_largos_job", "work_order", "job", job_name)

    if table_exists(cur, "lista_largos_swo"):
        cols = get_columns(cur, "lista_largos_swo")
        if "job" in cols and "super_work_order" in cols:
            swos = fetch_distinct_values(cur, "lista_largos_swo", "super_work_order", "job", job_name)

    print(f"WO detectadas:  {wos if wos else 'ninguna'}")
    print(f"SWO detectadas: {swos if swos else 'ninguna'}")
    print("-" * 80)

    # Preview genérico por tablas
    cur.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_type = 'BASE TABLE'
        ORDER BY table_name;
        """,
        (SCHEMA,),
    )
    tables = [r["table_name"] for r in cur.fetchall()]

    found_any = False
    for table_name in tables:
        cols = set(get_columns(cur, table_name))
        for col in GENERIC_JOB_COLUMNS:
            if col in cols:
                total = count_where_equal(cur, table_name, col, job_name)
                if total > 0:
                    found_any = True
                    print(f"{table_name}.{col}: {total} fila(s)")
    if not found_any:
        print("No se encontraron coincidencias exactas del nombre del job en columnas genéricas.")

    print("=" * 80 + "\n")


def eliminar_job(cur, job_name: str) -> dict:
    resumen = {
        "job": job_name,
        "wos": [],
        "swos": [],
        "borrados_por_tabla": {},
        "remanentes_wo": {"eliminados": 0, "liberados": 0},
        "remanentes_swo": {"eliminados": 0, "liberados": 0},
    }

    # 1) Descubrir WO y SWO ligadas al job desde las tablas base de lista de largos
    if table_exists(cur, "lista_largos_job"):
        cols = get_columns(cur, "lista_largos_job")
        if "job" in cols and "work_order" in cols:
            resumen["wos"] = fetch_distinct_values(cur, "lista_largos_job", "work_order", "job", job_name)

    if table_exists(cur, "lista_largos_swo"):
        cols = get_columns(cur, "lista_largos_swo")
        if "job" in cols and "super_work_order" in cols:
            resumen["swos"] = fetch_distinct_values(cur, "lista_largos_swo", "super_work_order", "job", job_name)

    # 2) Limpiar estado operativo de lista de largos para WO
    for table_name in ("lista_largos_sesiones", "lista_largos_cortes", "lista_largos_sobrantes", "lista_largos_sobrante", "lista_largos_planes", "lista_largos_plan"):
        borradas = delete_for_orders(cur, table_name, resumen["wos"], "WO")
        if borradas:
            resumen["borrados_por_tabla"][f"{table_name} [WO]"] = borradas

    # 3) Limpiar estado operativo de lista de largos para SWO
    for table_name in ("lista_largos_sesiones", "lista_largos_cortes", "lista_largos_sobrantes", "lista_largos_sobrante", "lista_largos_planes", "lista_largos_plan"):
        borradas = delete_for_orders(cur, table_name, resumen["swos"], "SWO")
        if borradas:
            resumen["borrados_por_tabla"][f"{table_name} [SWO]"] = borradas

    # 4) Limpiar remanentes ligados a esas WO/SWO
    resumen["remanentes_wo"] = delete_remanentes_for_orders(cur, resumen["wos"], "WO")
    resumen["remanentes_swo"] = delete_remanentes_for_orders(cur, resumen["swos"], "SWO")

    # 5) Eliminar filas base de lista de largos del job
    if table_exists(cur, "lista_largos_job"):
        borradas = delete_where_equal(cur, "lista_largos_job", "job", job_name)
        if borradas:
            resumen["borrados_por_tabla"]["lista_largos_job.job"] = borradas

    if table_exists(cur, "lista_largos_swo"):
        borradas = delete_where_equal(cur, "lista_largos_swo", "job", job_name)
        if borradas:
            resumen["borrados_por_tabla"]["lista_largos_swo.job"] = borradas

    # 6) Eliminar coincidencias exactas del job en otras tablas del sistema
    cur.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_type = 'BASE TABLE'
        ORDER BY table_name;
        """,
        (SCHEMA,),
    )
    tables = [r["table_name"] for r in cur.fetchall()]

    # Evitar doble borrado en tablas ya tratadas arriba
    skip_tables = {
        "lista_largos_job",
        "lista_largos_swo",
        "lista_largos_sesiones",
        "lista_largos_cortes",
        "lista_largos_sobrantes",
        "lista_largos_sobrante",
        "lista_largos_planes",
        "lista_largos_plan",
        "lista_largos_remanentes",
    }

    for table_name in tables:
        if table_name in skip_tables:
            continue

        cols = set(get_columns(cur, table_name))
        for col in GENERIC_JOB_COLUMNS:
            if col in cols:
                borradas = delete_where_equal(cur, table_name, col, job_name)
                if borradas:
                    resumen["borrados_por_tabla"][f"{table_name}.{col}"] = borradas

    return resumen


def imprimir_resumen(resumen: dict):
    print("\n" + "=" * 80)
    print("ELIMINACION DE JOB EN BASE DE DATOS")
    print("=" * 80)
    print(f"Job:  {resumen['job']}")
    print(f"WOs detectadas:  {resumen['wos'] if resumen['wos'] else 'ninguna'}")
    print(f"SWOs detectadas: {resumen['swos'] if resumen['swos'] else 'ninguna'}")
    print("-" * 80)

    if resumen["borrados_por_tabla"]:
        for tabla, total in resumen["borrados_por_tabla"].items():
            print(f"{tabla:<45} {total}")
    else:
        print("No se borraron filas en tablas principales.")

    print("-" * 80)
    print(
        f"Remanentes WO liberados/eliminados: "
        f"{resumen['remanentes_wo']['liberados']} / {resumen['remanentes_wo']['eliminados']}"
    )
    print(
        f"Remanentes SWO liberados/eliminados: "
        f"{resumen['remanentes_swo']['liberados']} / {resumen['remanentes_swo']['eliminados']}"
    )
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Elimina de la BD toda la información referida a un job específico."
    )
    parser.add_argument("--job", required=True, help='Nombre exacto del job. Ej: "4000 KVA DE PRUEBA"')
    parser.add_argument("--preview", action="store_true", help="Solo muestra qué encontraría, no borra nada")
    parser.add_argument("--yes", action="store_true", help="Ejecuta sin pedir confirmación")
    args = parser.parse_args()

    job_name = (args.job or "").strip()
    if not job_name:
        print("ERROR: debes indicar --job")
        sys.exit(1)

    conn = None
    cur = None

    try:
        conn = db_connect()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        if args.preview:
            preview_job(cur, job_name)
            return

        if not args.yes:
            print("\nVas a eliminar de la BD toda la información asociada a este job:")
            print(f"  Job: {job_name}")
            confirm = input('\nEscribe ELIMINAR para continuar: ').strip().upper()
            if confirm != "ELIMINAR":
                print("Operación cancelada.")
                return

        resumen = eliminar_job(cur, job_name)
        conn.commit()
        imprimir_resumen(resumen)

    except KeyboardInterrupt:
        if conn:
            conn.rollback()
        print("\nOperación cancelada por el usuario.")
        sys.exit(1)
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"\nERROR: {e}")
        sys.exit(1)
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


if __name__ == "__main__":
    main()