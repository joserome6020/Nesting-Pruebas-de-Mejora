"""Checkpoints persistentes para recuperar exportaciones sin repetir CAD."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import config


def _db_config(db_config: dict | None = None) -> dict:
    if db_config:
        return dict(db_config)
    return {
        "host": getattr(config, "NESTING_DB_HOST", "192.168.2.80"),
        "database": getattr(config, "NESTING_DB_NAME", "nestingpro_db"),
        "user": getattr(config, "NESTING_DB_USER", "postgres"),
        "password": getattr(config, "NESTING_DB_PASSWORD", "nesting123"),
        "port": getattr(config, "NESTING_DB_PORT", "5433"),
        "connect_timeout": int(getattr(config, "NESTING_DB_CONNECT_TIMEOUT", 5)),
    }


def nuevo_export_run_id() -> str:
    """Identificador correlacionable en logs y checkpoints."""
    return uuid.uuid4().hex


def asegurar_tabla_checkpoints_export(db_config: dict | None = None) -> None:
    import psycopg2

    with psycopg2.connect(**_db_config(db_config)) as conexion:
        with conexion.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.export_stage_checkpoints (
                    scope_id TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    run_id TEXT,
                    detail TEXT,
                    http_status INTEGER,
                    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (scope_id, scope_type, stage),
                    CONSTRAINT chk_export_stage_checkpoint_status
                        CHECK (status IN ('PENDING', 'OK', 'FAILED', 'WARNING'))
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_export_stage_checkpoint_scope
                ON public.export_stage_checkpoints (scope_id, scope_type, updated_at DESC)
                """
            )


def guardar_checkpoint_export(
    scope_id: str,
    scope_type: str,
    stage: str,
    *,
    status: str = "OK",
    run_id: str | None = None,
    detail: str = "",
    http_status: int | None = None,
    metadata: dict[str, Any] | None = None,
    db_config: dict | None = None,
) -> None:
    """Inserta o actualiza el último estado de una etapa de exportación."""
    import psycopg2
    from psycopg2.extras import Json

    scope = str(scope_id or "").strip()
    tipo = str(scope_type or "").strip().upper()
    etapa = str(stage or "").strip().upper()
    estado = str(status or "OK").strip().upper()
    if not scope or not tipo or not etapa:
        raise ValueError("scope_id, scope_type y stage son obligatorios.")
    if estado not in {"PENDING", "OK", "FAILED", "WARNING"}:
        raise ValueError(f"Estado de checkpoint inválido: {estado}")

    asegurar_tabla_checkpoints_export(db_config)
    with psycopg2.connect(**_db_config(db_config)) as conexion:
        with conexion.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.export_stage_checkpoints (
                    scope_id, scope_type, stage, status, run_id, detail,
                    http_status, metadata_json, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (scope_id, scope_type, stage)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    run_id = COALESCE(EXCLUDED.run_id, export_stage_checkpoints.run_id),
                    detail = EXCLUDED.detail,
                    http_status = EXCLUDED.http_status,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = NOW()
                """,
                (
                    scope,
                    tipo,
                    etapa,
                    estado,
                    str(run_id or "").strip() or None,
                    str(detail or ""),
                    int(http_status) if http_status is not None else None,
                    Json(metadata or {}),
                ),
            )


def checkpoint_export_ok(
    scope_id: str,
    scope_type: str,
    stage: str,
    *,
    db_config: dict | None = None,
) -> bool:
    """Indica si una etapa ya fue confirmada y puede omitirse al reanudar."""
    import psycopg2

    asegurar_tabla_checkpoints_export(db_config)
    with psycopg2.connect(**_db_config(db_config)) as conexion:
        with conexion.cursor() as cursor:
            cursor.execute(
                """
                SELECT status
                FROM public.export_stage_checkpoints
                WHERE scope_id = %s AND scope_type = %s AND stage = %s
                """,
                (
                    str(scope_id or "").strip(),
                    str(scope_type or "").strip().upper(),
                    str(stage or "").strip().upper(),
                ),
            )
            row = cursor.fetchone()
    return bool(row and str(row[0] or "").upper() == "OK")


def leer_checkpoints_export(
    scope_id: str,
    scope_type: str,
    *,
    db_config: dict | None = None,
) -> dict[str, dict[str, Any]]:
    """Snapshot de recuperación, útil para UI y auditorías sin efectos CAD."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    asegurar_tabla_checkpoints_export(db_config)
    with psycopg2.connect(**_db_config(db_config)) as conexion:
        with conexion.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT stage, status, run_id, detail, http_status, metadata_json,
                       created_at, updated_at
                FROM public.export_stage_checkpoints
                WHERE scope_id = %s AND scope_type = %s
                ORDER BY stage
                """,
                (str(scope_id or "").strip(), str(scope_type or "").strip().upper()),
            )
            rows = cursor.fetchall() or []
    return {
        str(row.get("stage") or ""): {
            **dict(row),
            "updated_at_iso": (
                row["updated_at"].replace(tzinfo=timezone.utc).isoformat()
                if isinstance(row.get("updated_at"), datetime)
                else None
            ),
        }
        for row in rows
    }
