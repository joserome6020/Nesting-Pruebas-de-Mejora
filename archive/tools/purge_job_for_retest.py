"""Purga trazable de un job ficticio antes de repetir una prueba completa.

Por defecto solo imprime un manifiesto.  ``--apply`` exige una confirmación
literal y también ``--delete-source`` para evitar eliminar por accidente una
carpeta de producción.  La utilidad opera únicamente sobre referencias exactas
al job, sus WO resueltas y su SWO confirmada en VSM.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config

VSM_BASE_URL = "http://192.168.2.80:8003"
CONFIRMATION_PREFIX = "DELETE-"

CHILD_FIRST_TABLES = (
    "lista_largos_eventos_pieza",
    "lista_largos_eventos_sobrante",
    "lista_largos_cortes",
    "lista_largos_sobrantes",
    "lista_largos_sobrante",
    "lista_largos_sesiones",
    "lista_largos_turnos",
)

TARGET_TABLES = (
    *CHILD_FIRST_TABLES,
    "lista_largos_remanentes",
    "lista_largos_planes",
    "lista_largos_plan",
    "material_requerido_ldg",
    "lista_largos_swo",
    "lista_largos_job",
    "pqart_wo",
    "pqart_swo",
    "erp_piezas_tracking",
    "erp_placas_tracking",
    "erp_work_orders",
    "erp_super_work_orders",
    "erp_jobs",
    "costos_prorrateo",
    "reportes_dinamicos",
    "diccionario_swo",
    "export_stage_checkpoints",
    "reporte_cortes",
    "jobs",
)

JOB_COLUMNS = {"job", "job_number", "job_name", "nombre_job", "titulo"}
WO_COLUMNS = {"work_order", "wo_name", "nombre_wo", "wo_number"}
SWO_COLUMNS = {
    "super_work_order",
    "nombre_swo",
    "swo_id",
    "swo",
    "prefijo_carpeta",
}
ORDER_COLUMNS = {"orden_id"}
CHECKPOINT_COLUMNS = {"scope_id"}
REMANENT_ORDER_COLUMNS = {"reservado_para_orden_id", "fuente_orden_id"}


def _db_config() -> dict[str, Any]:
    return {
        "host": getattr(config, "NESTING_DB_HOST", "192.168.2.80"),
        "database": getattr(config, "NESTING_DB_NAME", "nestingpro_db"),
        "user": getattr(config, "NESTING_DB_USER", "postgres"),
        "password": getattr(config, "NESTING_DB_PASSWORD", "nesting123"),
        "port": getattr(config, "NESTING_DB_PORT", "5433"),
        "connect_timeout": 15,
    }


def _vsm_db_config() -> dict[str, Any]:
    """Conexión al foldertree que sirve la instancia VSM productiva."""
    return {
        "host": os.getenv("VSM_DB_HOST", "192.168.2.80"),
        "port": os.getenv("VSM_DB_PORT", "5437"),
        "dbname": os.getenv("VSM_DB_NAME", "foldertree"),
        "user": os.getenv("VSM_DB_USER", "user"),
        "password": os.getenv("VSM_DB_PASSWORD", "password"),
        "connect_timeout": 15,
    }


def _http_json(method: str, path: str, *, timeout: int = 30) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(f"{VSM_BASE_URL}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            body = {"raw": raw}
        return error.code, body


def _table_exists(cursor, table: str) -> bool:
    cursor.execute("SELECT to_regclass(%s) AS reg", (f"public.{table}",))
    return bool((cursor.fetchone() or {}).get("reg"))


def _columns(cursor, table: str) -> set[str]:
    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    return {str(row["column_name"]) for row in (cursor.fetchall() or [])}


def _reference_conditions(
    columns: set[str],
    *,
    job: str,
    work_orders: list[str],
    swo: str,
) -> tuple[list[Any], list[Any]]:
    """Construye condiciones exactas sin interpolar valores del usuario."""
    clauses: list[Any] = []
    values: list[Any] = []

    for column in sorted(columns & JOB_COLUMNS):
        clauses.append(
            sql.SQL("BTRIM(COALESCE({}::text, '')) = %s").format(sql.Identifier(column))
        )
        values.append(job)
    for column in sorted(columns & WO_COLUMNS):
        if work_orders:
            clauses.append(
                sql.SQL("BTRIM(COALESCE({}::text, '')) = ANY(%s)").format(
                    sql.Identifier(column)
                )
            )
            values.append(work_orders)
    for column in sorted(columns & SWO_COLUMNS):
        clauses.append(
            sql.SQL("BTRIM(COALESCE({}::text, '')) = %s").format(sql.Identifier(column))
        )
        values.append(swo)
    for column in sorted(columns & ORDER_COLUMNS):
        order_ids = [*work_orders, swo]
        clauses.append(
            sql.SQL("BTRIM(COALESCE({}::text, '')) = ANY(%s)").format(
                sql.Identifier(column)
            )
        )
        values.append(order_ids)
    for column in sorted(columns & CHECKPOINT_COLUMNS):
        clauses.append(
            sql.SQL("BTRIM(COALESCE({}::text, '')) = ANY(%s)").format(
                sql.Identifier(column)
            )
        )
        values.append([job, swo])
    for column in sorted(columns & REMANENT_ORDER_COLUMNS):
        clauses.append(
            sql.SQL("BTRIM(COALESCE({}::text, '')) = ANY(%s)").format(
                sql.Identifier(column)
            )
        )
        values.append([*work_orders, swo])
    return clauses, values


def _count_references(
    cursor,
    table: str,
    *,
    job: str,
    work_orders: list[str],
    swo: str,
) -> int:
    if not _table_exists(cursor, table):
        return 0
    clauses, values = _reference_conditions(
        _columns(cursor, table), job=job, work_orders=work_orders, swo=swo
    )
    if not clauses:
        return 0
    query = sql.SQL("SELECT COUNT(*) AS n FROM {} WHERE ").format(sql.Identifier(table))
    query += sql.SQL(" OR ").join(clauses)
    cursor.execute(query, tuple(values))
    return int((cursor.fetchone() or {}).get("n") or 0)


def _delete_references(
    cursor,
    table: str,
    *,
    job: str,
    work_orders: list[str],
    swo: str,
) -> int:
    if not _table_exists(cursor, table):
        return 0
    clauses, values = _reference_conditions(
        _columns(cursor, table), job=job, work_orders=work_orders, swo=swo
    )
    if not clauses:
        return 0
    query = sql.SQL("DELETE FROM {} WHERE ").format(sql.Identifier(table))
    query += sql.SQL(" OR ").join(clauses)
    cursor.execute(query, tuple(values))
    return cursor.rowcount


def _erp_ids(
    cursor, table: str, id_column: str, name_column: str, values: list[str]
) -> list[Any]:
    """Obtiene las llaves ERP antes de borrar sus padres."""
    if not values or not _table_exists(cursor, table):
        return []
    columns = _columns(cursor, table)
    if id_column not in columns or name_column not in columns:
        return []
    query = sql.SQL(
        "SELECT DISTINCT {} AS id FROM {} "
        "WHERE BTRIM(COALESCE({}::text, '')) = ANY(%s)"
    ).format(
        sql.Identifier(id_column),
        sql.Identifier(table),
        sql.Identifier(name_column),
    )
    cursor.execute(query, (values,))
    return [row["id"] for row in (cursor.fetchall() or []) if row.get("id") is not None]


def _count_by_ids(cursor, table: str, column: str, values: list[Any]) -> int:
    if not values or not _table_exists(cursor, table) or column not in _columns(cursor, table):
        return 0
    query = sql.SQL("SELECT COUNT(*) AS n FROM {} WHERE {} = ANY(%s)").format(
        sql.Identifier(table), sql.Identifier(column)
    )
    cursor.execute(query, (values,))
    return int((cursor.fetchone() or {}).get("n") or 0)


def _delete_by_ids(cursor, table: str, column: str, values: list[Any]) -> int:
    if not values or not _table_exists(cursor, table) or column not in _columns(cursor, table):
        return 0
    query = sql.SQL("DELETE FROM {} WHERE {} = ANY(%s)").format(
        sql.Identifier(table), sql.Identifier(column)
    )
    cursor.execute(query, (values,))
    return cursor.rowcount


def _delete_remanentes(
    cursor, *, work_orders: list[str], swo: str
) -> dict[str, int]:
    table = "lista_largos_remanentes"
    if not _table_exists(cursor, table):
        return {"liberados": 0, "eliminados": 0}
    columns = _columns(cursor, table)
    order_ids = [*work_orders, swo]
    released = 0
    deleted = 0
    if {
        "reservado_para_orden_id",
        "reservado_para_tipo_orden",
        "status",
    }.issubset(columns):
        cursor.execute(
            """
            UPDATE lista_largos_remanentes
            SET status = 'DISPONIBLE',
                reservado_para_orden_id = NULL,
                reservado_para_tipo_orden = NULL,
                updated_at = NOW()
            WHERE BTRIM(COALESCE(reservado_para_orden_id, '')) = ANY(%s)
            """,
            (order_ids,),
        )
        released = cursor.rowcount
    if {"fuente_orden_id", "fuente_tipo_orden"}.issubset(columns):
        cursor.execute(
            """
            DELETE FROM lista_largos_remanentes
            WHERE BTRIM(COALESCE(fuente_orden_id, '')) = ANY(%s)
            """,
            (order_ids,),
        )
        deleted = cursor.rowcount
    return {"liberados": released, "eliminados": deleted}


def _get_vsm_state(job: str, swo: str) -> dict[str, Any]:
    job_path = f"/jobs/by-number/{urllib.parse.quote(job)}"
    job_status, job_data = _http_json("GET", job_path)
    job_detail_status = 0
    job_detail: dict[str, Any] = {}
    job_id = int((job_data or {}).get("id") or 0)
    if job_status == 200 and job_id > 0:
        job_detail_status, job_detail = _http_json("GET", f"/jobs/{job_id}")
        if job_detail_status == 200:
            job_data = {**job_data, **job_detail}
    member_status, member_data = _http_json(
        "GET", f"/nesting/swo/{urllib.parse.quote(swo)}/jobs"
    )
    return {
        "job_status": job_status,
        "job": job_data,
        "job_detail_status": job_detail_status,
        "swo_members_status": member_status,
        "swo_members": member_data,
    }


def _resolve_db_scope(
    cursor,
    job: str,
    swo: str,
    *,
    known_work_orders: list[str] | None = None,
) -> dict[str, Any]:
    if not _table_exists(cursor, "reporte_cortes"):
        raise RuntimeError("No existe reporte_cortes; no se puede comprobar alcance.")

    cursor.execute(
        """
        SELECT DISTINCT BTRIM(work_order) AS work_order
        FROM reporte_cortes
        WHERE BTRIM(job) = %s
          AND NULLIF(BTRIM(work_order), '') IS NOT NULL
        ORDER BY 1
        """,
        (job,),
    )
    work_orders = [
        str(row["work_order"]).strip()
        for row in (cursor.fetchall() or [])
        if str(row.get("work_order") or "").strip()
    ]

    cursor.execute(
        """
        SELECT DISTINCT BTRIM(job) AS job
        FROM reporte_cortes
        WHERE BTRIM(super_work_order) = %s
        ORDER BY 1
        """,
        (swo,),
    )
    swo_jobs = [
        str(row["job"]).strip()
        for row in (cursor.fetchall() or [])
        if str(row.get("job") or "").strip()
    ]
    unexpected = sorted(set(swo_jobs) - {job})
    if unexpected:
        raise RuntimeError(
            f"{swo} también contiene otros jobs ({unexpected}); se niega la purga."
        )
    if not work_orders and known_work_orders:
        work_orders = sorted({str(item).strip() for item in known_work_orders if str(item).strip()})
    if not work_orders:
        raise RuntimeError(f"No se encontraron WO para el job {job}.")
    return {
        "work_orders": work_orders,
        "swo_jobs": swo_jobs,
        "erp_work_order_ids": _erp_ids(
            cursor, "erp_work_orders", "id_wo", "nombre_wo", work_orders
        ),
        "erp_swo_ids": _erp_ids(
            cursor, "erp_super_work_orders", "id_swo", "nombre_swo", [swo]
        ),
    }


def _manifest(cursor, *, job: str, swo: str, scope: dict[str, Any]) -> dict[str, Any]:
    work_orders = scope["work_orders"]
    counts = {
        table: _count_references(
            cursor, table, job=job, work_orders=work_orders, swo=swo
        )
        for table in TARGET_TABLES
    }
    counts["erp_piezas_tracking"] = max(
        counts["erp_piezas_tracking"],
        _count_by_ids(
            cursor,
            "erp_piezas_tracking",
            "id_wo",
            scope["erp_work_order_ids"],
        ),
    )
    counts["erp_placas_tracking"] = max(
        counts["erp_placas_tracking"],
        _count_by_ids(
            cursor,
            "erp_placas_tracking",
            "id_swo",
            scope["erp_swo_ids"],
        ),
    )
    return {
        "job": job,
        "swo": swo,
        "work_orders": work_orders,
        "swo_jobs": scope["swo_jobs"],
        "table_counts": {name: count for name, count in counts.items() if count},
    }


def _history_path() -> Path:
    return Path(getattr(config, "DB_HISTORIAL", ROOT / "historial_jobs.json"))


def _drop_history_entries(value: Any, *, job: str, swo: str) -> tuple[Any, int]:
    """Elimina únicamente registros o claves que identifican este job/SWO."""
    target_keys = {"job", "job_number", "job_name", "nombre_job", "swo", "swo_id"}
    if isinstance(value, list):
        kept: list[Any] = []
        removed = 0
        for item in value:
            if isinstance(item, dict) and any(
                str(item.get(key) or "").strip() in {job, swo} for key in target_keys
            ):
                removed += 1
                continue
            rewritten, nested = _drop_history_entries(item, job=job, swo=swo)
            kept.append(rewritten)
            removed += nested
        return kept, removed
    if isinstance(value, dict):
        kept_dict: dict[str, Any] = {}
        removed = 0
        for key, item in value.items():
            if str(key).strip() in {job, swo}:
                removed += 1
                continue
            if isinstance(item, dict) and any(
                str(item.get(field) or "").strip() in {job, swo} for field in target_keys
            ):
                removed += 1
                continue
            rewritten, nested = _drop_history_entries(item, job=job, swo=swo)
            kept_dict[key] = rewritten
            removed += nested
        return kept_dict, removed
    return value, 0


def _purge_history(*, job: str, swo: str) -> int:
    path = _history_path()
    if not path.is_file():
        return 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    rewritten, removed = _drop_history_entries(raw, job=job, swo=swo)
    if removed:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(
            json.dumps(rewritten, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(path)
    return removed


def _source_dir(vsm_job: dict[str, Any], *, job: str) -> Path:
    product = str(vsm_job.get("product") or "").strip()
    client = str(vsm_job.get("client") or "").strip()
    if not product or not client:
        raise RuntimeError("VSM no devolvió producto/cliente para calcular la carpeta.")
    local_path = str(vsm_job.get("local_path") or "").replace("\\", "/")
    suffix = f"ARGA METALS CORPORATE SYSTEM/{product}/{client}/{job}"
    if not local_path.endswith(suffix):
        raise RuntimeError(
            f"Ruta VSM inesperada ({local_path!r}); se niega borrar carpeta."
        )
    source = Path(config.RUTA_SERVIDOR_RAIZ) / product / client / job
    source.resolve(strict=False)
    root = Path(config.RUTA_SERVIDOR_RAIZ)
    if source.parent != root / product / client:
        raise RuntimeError("La carpeta calculada sale de la raíz autorizada.")
    return source


def _purge_database(cursor, *, job: str, swo: str, scope: dict[str, Any]) -> dict[str, Any]:
    work_orders = scope["work_orders"]
    removed: dict[str, int] = {}
    for table in CHILD_FIRST_TABLES:
        count = _delete_references(
            cursor, table, job=job, work_orders=work_orders, swo=swo
        )
        if count:
            removed[table] = count

    remanentes = _delete_remanentes(cursor, work_orders=work_orders, swo=swo)
    if any(remanentes.values()):
        removed["lista_largos_remanentes"] = sum(remanentes.values())

    for table in TARGET_TABLES:
        if table in {*CHILD_FIRST_TABLES, "lista_largos_remanentes"}:
            continue
        if table == "erp_piezas_tracking":
            count = _delete_by_ids(
                cursor,
                table,
                "id_wo",
                scope["erp_work_order_ids"],
            )
        elif table == "erp_placas_tracking":
            count = _delete_by_ids(
                cursor,
                table,
                "id_swo",
                scope["erp_swo_ids"],
            )
        else:
            count = _delete_references(
                cursor, table, job=job, work_orders=work_orders, swo=swo
            )
        if count:
            removed[table] = count
    return {"removed": removed, "remanentes": remanentes}


def _purge_vsm_job_database(*, job_id: int, job: str) -> dict[str, int]:
    """Fallback para el DELETE VSM incompleto que deja claves foráneas hijas."""
    deleted: dict[str, int] = {}
    with psycopg2.connect(**_vsm_db_config()) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            if not _table_exists(cursor, "jobs"):
                raise RuntimeError("La base VSM no contiene la tabla jobs.")
            cursor.execute(
                "SELECT job_number FROM jobs WHERE id = %s FOR UPDATE", (job_id,)
            )
            row = cursor.fetchone()
            if not row or str(row.get("job_number") or "").strip() != job:
                raise RuntimeError(
                    f"El id VSM {job_id} no coincide exactamente con el job {job}."
                )

            statements = (
                (
                    "dossier_files",
                    """
                    DELETE FROM dossier_files
                    WHERE dossier_id IN (
                        SELECT id FROM dossiers WHERE job_id = %s
                    )
                    """,
                ),
                (
                    "defect_images",
                    """
                    DELETE FROM defect_images
                    WHERE defect_record_id IN (
                        SELECT id FROM defect_records WHERE job_id = %s
                    )
                    """,
                ),
                ("file_annotations", "DELETE FROM file_annotations WHERE job_id = %s"),
                ("file_reviews", "DELETE FROM file_reviews WHERE job_id = %s"),
                ("defect_records", "DELETE FROM defect_records WHERE job_id = %s"),
                ("dossiers", "DELETE FROM dossiers WHERE job_id = %s"),
                ("job_history", "DELETE FROM job_history WHERE job_id = %s"),
                ("work_orders", "DELETE FROM work_orders WHERE job_id = %s"),
                ("jobs", "DELETE FROM jobs WHERE id = %s"),
            )
            for table, statement in statements:
                if not _table_exists(cursor, table):
                    continue
                cursor.execute(statement, (job_id,))
                if cursor.rowcount:
                    deleted[table] = cursor.rowcount
            if deleted.get("jobs") != 1:
                raise RuntimeError(f"No se eliminó exactamente un job VSM: {deleted}")
        connection.commit()
    return deleted


def _delete_vsm(*, job_id: int, job: str, swo: str) -> dict[str, Any]:
    swo_status, swo_body = _http_json("DELETE", f"/nesting/swo/{urllib.parse.quote(swo)}")
    if not 200 <= swo_status < 300 and swo_status != 404:
        raise RuntimeError(f"No se pudo borrar {swo} en VSM: HTTP {swo_status} {swo_body}")

    job_status, job_body = _http_json("DELETE", f"/jobs/{job_id}")
    if job_status == 204:
        return {"swo_http": swo_status, "job_http": job_status, "mode": "api"}
    if job_status != 500:
        raise RuntimeError(
            f"No se pudo borrar job id {job_id} en VSM: HTTP {job_status} {job_body}"
        )

    fallback = _purge_vsm_job_database(job_id=job_id, job=job)
    return {
        "swo_http": swo_status,
        "job_http": job_status,
        "mode": "direct-db-fallback",
        "fallback_removed": fallback,
    }


def _verify_vsm_absent(job: str, swo: str) -> dict[str, int]:
    job_status, _ = _http_json(
        "GET", f"/jobs/by-number/{urllib.parse.quote(job)}"
    )
    swo_status, swo_body = _http_json(
        "GET", f"/nesting/swo/{urllib.parse.quote(swo)}/jobs"
    )
    swo_is_absent = swo_status == 404 or (
        swo_status == 200 and not (swo_body.get("jobs") or [])
    )
    if job_status != 404 or not swo_is_absent:
        raise RuntimeError(
            f"VSM conservó referencias: job HTTP {job_status}, SWO HTTP {swo_status}."
        )
    return {"job_http": job_status, "swo_http": swo_status}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Purga un job ficticio ANS/VSM para repetir la prueba desde cero."
    )
    parser.add_argument("--job", required=True, help="Job VSM exacto. Ej. 251008")
    parser.add_argument("--swo", required=True, help="SWO exacta. Ej. SWO-002")
    parser.add_argument("--expected-vsm-id", type=int, help="Id VSM esperado.")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reanuda una purga interrumpida tras borrar ANS o la SWO de VSM.",
    )
    parser.add_argument(
        "--known-wo",
        action="append",
        default=[],
        help="WO resuelta durante el dry-run; necesaria con --resume.",
    )
    parser.add_argument("--apply", action="store_true", help="Ejecuta los borrados.")
    parser.add_argument(
        "--delete-source",
        action="store_true",
        help="Autoriza eliminar la carpeta fuente del job en el servidor.",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help="Debe ser DELETE-<JOB> junto con --apply.",
    )
    args = parser.parse_args()
    job = str(args.job).strip()
    swo = str(args.swo).strip()
    expected_confirmation = f"{CONFIRMATION_PREFIX}{job}"
    if args.apply and args.confirm != expected_confirmation:
        parser.error(f"--apply exige --confirm {expected_confirmation}")
    if args.apply and not args.delete_source:
        parser.error("--apply exige --delete-source para este reinicio completo.")

    vsm = _get_vsm_state(job, swo)
    if vsm["job_status"] != 200:
        raise RuntimeError(f"VSM no encontró {job}: HTTP {vsm['job_status']} {vsm['job']}")
    if vsm["swo_members_status"] != 200:
        raise RuntimeError(
            f"VSM no encontró {swo}: HTTP {vsm['swo_members_status']} {vsm['swo_members']}"
        )
    vsm_job = vsm["job"]
    job_id = int(vsm_job.get("id") or 0)
    members = {
        str(item).strip()
        for item in (vsm["swo_members"].get("jobs") or [])
        if str(item).strip()
    }
    allowed_memberships = ({job}, set()) if args.resume else ({job},)
    if job_id <= 0 or members not in allowed_memberships:
        raise RuntimeError(
            f"VSM no es exclusivo: id={job_id}, miembros SWO={sorted(members)}."
        )
    if args.expected_vsm_id is not None and job_id != args.expected_vsm_id:
        raise RuntimeError(
            f"VSM devolvió id {job_id}; se esperaba {args.expected_vsm_id}."
        )

    with psycopg2.connect(**_db_config()) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            scope = _resolve_db_scope(
                cursor,
                job,
                swo,
                known_work_orders=args.known_wo if args.resume else None,
            )
            manifest = _manifest(cursor, job=job, swo=swo, scope=scope)
            manifest["vsm"] = {
                "job_id": job_id,
                "status": vsm_job.get("status"),
                "local_path": vsm_job.get("local_path"),
                "members": sorted(members),
            }
            source = _source_dir(vsm_job, job=job)
            manifest["source_dir"] = str(source)
            manifest["source_exists"] = source.exists()
            print(json.dumps({"mode": "dry-run" if not args.apply else "apply", **manifest},
                             ensure_ascii=False, indent=2, default=str))
            if not args.apply:
                return 0

            db_result = _purge_database(cursor, job=job, swo=swo, scope=scope)
            connection.commit()

    history_removed = _purge_history(job=job, swo=swo)
    vsm_deleted = _delete_vsm(job_id=job_id, job=job, swo=swo)
    if source.exists():
        shutil.rmtree(source)
    if source.exists():
        raise RuntimeError(f"La carpeta no se pudo eliminar: {source}")

    with psycopg2.connect(**_db_config()) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            residual = _manifest(cursor, job=job, swo=swo, scope=scope)
    vsm_verify = _verify_vsm_absent(job, swo)
    result = {
        "status": "OK",
        "db": db_result,
        "history_entries_removed": history_removed,
        "vsm": {**vsm_deleted, **vsm_verify},
        "source_deleted": str(source),
        "residual_table_counts": residual["table_counts"],
    }
    if residual["table_counts"]:
        raise RuntimeError(f"Quedaron referencias ANS: {result}")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
