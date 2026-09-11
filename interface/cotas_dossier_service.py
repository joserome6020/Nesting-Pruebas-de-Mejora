"""Tabla `cotas_dossier` en nestingpro_db — evidencias/fotos del dossier de cotas.

Solo esquema (CREATE IF NOT EXISTS). La escritura/lectura la hará el proyecto
COTAS ABIGAIL al integrar spoteos / typ+ / FYP.
"""
from __future__ import annotations

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


def asegurar_tabla_cotas_dossier(db_config: dict | None = None) -> None:
    """Crea `public.cotas_dossier` e índices si aún no existen."""
    import psycopg2

    with psycopg2.connect(**_db_config(db_config)) as conexion:
        with conexion.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.cotas_dossier (
                    id                  SERIAL PRIMARY KEY,
                    cliente             TEXT NOT NULL,
                    producto            TEXT NOT NULL DEFAULT '',
                    job                 TEXT NOT NULL,
                    type                TEXT NOT NULL DEFAULT '',
                    cantidad_spoteos    INTEGER NOT NULL DEFAULT 1
                        CHECK (cantidad_spoteos >= 0),
                    nombre_archivo      TEXT NOT NULL DEFAULT '',
                    ruta                TEXT NOT NULL DEFAULT '',
                    created_at          TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                ALTER TABLE public.cotas_dossier
                    ADD COLUMN IF NOT EXISTS cliente TEXT NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS producto TEXT NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS job TEXT NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS type TEXT NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS cantidad_spoteos INTEGER NOT NULL DEFAULT 1,
                    ADD COLUMN IF NOT EXISTS nombre_archivo TEXT NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS ruta TEXT NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW()
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cotas_dossier_job
                ON public.cotas_dossier (job)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cotas_dossier_cliente_producto
                ON public.cotas_dossier (cliente, producto)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cotas_dossier_type
                ON public.cotas_dossier (type)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cotas_dossier_job_type
                ON public.cotas_dossier (job, type)
                """
            )
