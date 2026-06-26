from fastapi import FastAPI, HTTPException, Response, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.extras import Json
import json
import os
import time
import io
import base64
import hashlib
from pathlib import Path
import re
import tempfile
from reporte_pdf_lista_largos import generar_pdf_lista_largos, build_remanentes_resultantes
from lista_largos_material_requerido import (
    asegurar_tabla_material_requerido_ldg,
    consultar_pedido,
    refrescar_pedido_herinox,
    sincronizar_pedido_desde_plan,
    existe_pedido,
    insertar_pedido_desde_plan_cursor,
    reconstruir_pedido_desde_plan,
)

app = FastAPI(title="API NestingPro")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "Content-Disposition",
        "X-Download-Filename",
        "X-Script-Source",
        "X-Scripts-Deleted",
        "X-Tabla-Origen",
        "X-Scripts-Deleted-From-Origin",
    ],
)

DB_CONFIG = {
    "host": os.getenv("NESTING_DB_HOST", "192.168.2.80"),
    "database": os.getenv("NESTING_DB_NAME", "nestingpro_db"),
    "user": os.getenv("NESTING_DB_USER", "postgres"),
    "password": os.getenv("NESTING_DB_PASSWORD", "nesting123"),
    "port": os.getenv("NESTING_DB_PORT", "5433"),
}

DB_TIMEZONE = os.getenv("DB_TIMEZONE", "America/Chihuahua")


def db_connect():
    conexion = psycopg2.connect(**DB_CONFIG)
    cursor = conexion.cursor()
    cursor.execute(f"SET TIME ZONE '{DB_TIMEZONE}';")
    cursor.close()
    return conexion

DXF_ROOT_DIR = os.getenv("DXF_ROOT_DIR", r"C:\NESTING_DXFS")
DXF_MAP_FILE = os.getenv("DXF_MAP_FILE", r"C:\NESTING_DXFS\dxf_map.json")

SHEET_METADATA_COLUMNS = {
    "sheet_uid",
    "sheet_code",
    "sheet_seq",
    "sheet_display_name",
    "plate_group_key",
    "placa_ancho_canonico_mm",
    "placa_largo_canonico_mm",
    "nest_instance_id",
    "source_nest_name",
    "is_rtz",
}

LISTA_LARGOS_STOCK_MAXIMO = 480.0
LISTA_LARGOS_STOCK_MINIMO = 240.0
LISTA_LARGOS_KERF = 0.25
LISTA_LARGOS_RECORTE_EXTREMO = 0.5
LISTA_LARGOS_REM_MINIMO = 15.0
LISTA_LARGOS_TIPO_REMANENTE_DEFAULT = "SOL"
LISTA_LARGOS_GRADE_DEFAULT = "A36"

LISTA_LARGOS_SECCION_MAP = {
    "pv2": "1",
    "cof": "1",
    "solera": "1",
}

# ========================================================
# HELPERS NUEVOS - SESIONES LISTA DE LARGOS
# ========================================================
def asegurar_tabla_lista_largos_sesiones():
    conexion = None
    cursor = None
    try:
        conexion = db_connect()
        cursor = conexion.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lista_largos_sesiones (
                id SERIAL PRIMARY KEY,
                orden_id VARCHAR(100) NOT NULL,
                tipo_orden VARCHAR(10) NOT NULL,
                operador VARCHAR(150) NOT NULL,
                bahia VARCHAR(100) NOT NULL,
                estado VARCHAR(20) NOT NULL DEFAULT 'activa',
                hora_inicio TIMESTAMP NOT NULL DEFAULT NOW(),
                hora_ultimo_movimiento TIMESTAMP NOT NULL DEFAULT NOW(),
                hora_fin TIMESTAMP NULL,
                piezas_totales INTEGER NOT NULL DEFAULT 0,
                piezas_completadas INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_lista_largos_tipo_orden CHECK (tipo_orden IN ('WO', 'SWO')),
                CONSTRAINT chk_lista_largos_piezas_totales CHECK (piezas_totales >= 0),
                CONSTRAINT chk_lista_largos_piezas_completadas CHECK (piezas_completadas >= 0)
            );
        """)

        cursor.execute("""
            ALTER TABLE lista_largos_sesiones
            DROP CONSTRAINT IF EXISTS chk_lista_largos_estado;
        """)

        cursor.execute("""
            ALTER TABLE lista_largos_sesiones
            ADD CONSTRAINT chk_lista_largos_estado
            CHECK (estado IN ('activa', 'cerrada', 'finalizada'));
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_lista_largos_sesiones_orden_tipo
            ON lista_largos_sesiones (orden_id, tipo_orden, hora_inicio DESC);
        """)

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_lista_largos_sesion_activa
            ON lista_largos_sesiones (orden_id, tipo_orden)
            WHERE estado = 'activa';
        """)

        conexion.commit()
    except Exception as e:
        if conexion:
            conexion.rollback()
        print(f"⚠️ No se pudo asegurar tabla lista_largos_sesiones: {e}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


@app.on_event("startup")
def startup_event():
    """
    Al arrancar la API, asegura la tabla nueva.
    """
    asegurar_tabla_lista_largos_sesiones()
    asegurar_tabla_material_requerido_ldg()


def normalizar_tipo_orden(tipo_orden: str) -> str:
    tipo = (tipo_orden or "").strip().upper()
    if tipo not in ("WO", "SWO"):
        raise HTTPException(status_code=422, detail="tipo_orden debe ser 'WO' o 'SWO'.")
    return tipo


def normalizar_orden_id(orden_id: str) -> str:
    orden = (orden_id or "").strip()
    if not orden:
        raise HTTPException(status_code=422, detail="orden_id es obligatorio.")
    return orden


def serializar_sesion(sesion):
    if not sesion:
        return None

    data = dict(sesion)

    for campo in [
        "hora_inicio",
        "hora_ultimo_movimiento",
        "hora_fin",
        "created_at",
        "updated_at",
    ]:
        valor = data.get(campo)
        if isinstance(valor, datetime):
            data[campo] = valor.isoformat()

    return data


def obtener_sesion_prioritaria(cursor, orden_id: str, tipo_orden: str):
    """
    Regresa primero una sesión activa si existe.
    Si no, regresa la última sesión registrada.
    """
    cursor.execute("""
        SELECT *
        FROM lista_largos_sesiones
        WHERE TRIM(orden_id) = %s AND tipo_orden = %s
        ORDER BY
            CASE WHEN estado = 'activa' THEN 0 ELSE 1 END,
            hora_inicio DESC
        LIMIT 1;
    """, (orden_id, tipo_orden))
    return cursor.fetchone()


def obtener_sesion_activa(cursor, orden_id: str, tipo_orden: str):
    cursor.execute("""
        SELECT *
        FROM lista_largos_sesiones
        WHERE TRIM(orden_id) = %s
          AND tipo_orden = %s
          AND estado = 'activa'
        ORDER BY hora_inicio DESC
        LIMIT 1;
    """, (orden_id, tipo_orden))
    return cursor.fetchone()

def obtener_ultima_sesion(cursor, orden_id: str, tipo_orden: str):
    cursor.execute("""
        SELECT *
        FROM lista_largos_sesiones
        WHERE TRIM(orden_id) = %s
          AND tipo_orden = %s
        ORDER BY hora_inicio DESC
        LIMIT 1;
    """, (orden_id, tipo_orden))
    return cursor.fetchone()

@app.get("/")
def leer_raiz():
    return {"mensaje": "Servidor de NestingPro en línea 🚀"}


@app.get("/api/jobs")
def obtener_lista_jobs():
    conexion = None
    try:
        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT DISTINCT job FROM reporte_cortes ORDER BY job;")
        jobs = cursor.fetchall()
        return {"jobs": [j["job"] for j in jobs if j["job"]]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conexion:
            cursor.close()
            conexion.close()


@app.get("/api/nesting/{nombre_job}")
def obtener_piezas_del_job(nombre_job: str):
    conexion = None
    try:
        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT * FROM reporte_cortes WHERE job = %s ORDER BY id ASC;",
            (nombre_job,)
        )
        piezas = cursor.fetchall()
        if not piezas:
            raise HTTPException(status_code=404, detail="No piezas")
        return {"piezas": piezas}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conexion:
            cursor.close()
            conexion.close()


def _obtener_columnas_reporte_cortes(cursor) -> set[str]:
    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'reporte_cortes'
    """)
    rows = cursor.fetchall()

    columnas = set()
    for row in rows:
        if isinstance(row, dict):
            valor = row.get("column_name")
        else:
            valor = row[0] if row else None

        if valor:
            columnas.add(valor)

    return columnas


def _sql_col_or_null(column_name: str, alias: Optional[str] = None, cast: str = "text", columnas_existentes: Optional[set[str]] = None) -> str:
    alias = alias or column_name
    columnas_existentes = columnas_existentes or set()
    if column_name in columnas_existentes:
        return f"{column_name} AS {alias}"
    return f"NULL::{cast} AS {alias}"

def _obtener_columnas_lista_largos(cursor) -> set[str]:
    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'lista_largos_job'
    """)
    rows = cursor.fetchall()

    columnas = set()
    for row in rows:
        if isinstance(row, dict):
            valor = row.get("column_name")
        else:
            valor = row[0] if row else None

        if valor:
            columnas.add(valor)

    return columnas


def _extraer_factor_wo(work_order: str) -> int:
    texto = str(work_order or "").strip()
    m = re.search(r"(?i)\bX\s*(\d+)\b", texto)
    if not m:
        return 1

    try:
        return max(1, int(m.group(1)))
    except Exception:
        return 1

def _normalizar_job_key(value: str) -> str:
    texto = str(value or "").strip()
    if not texto:
        return ""
    texto = re.sub(r"\s+", " ", texto)
    return texto.upper()

def _obtener_jobs_de_wo(cursor, wo_id: str) -> list[dict]:
    cursor.execute("""
        SELECT DISTINCT
            TRIM(job) AS job,
            TRIM(work_order) AS work_order
        FROM reporte_cortes
        WHERE TRIM(work_order) = %s
          AND job IS NOT NULL
          AND work_order IS NOT NULL
        ORDER BY TRIM(job), TRIM(work_order)
    """, (str(wo_id or "").strip(),))

    rows = cursor.fetchall() or []
    resultado = []

    for row in rows:
        job = str(row.get("job") or "").strip()
        work_order = str(row.get("work_order") or "").strip()
        if job and work_order:
            resultado.append({
                "job": job,
                "job_key": _normalizar_job_key(job),
                "work_order": work_order,
            })

    return resultado


def _obtener_jobs_de_swo(cursor, swo_id: str) -> list[dict]:
    cursor.execute("""
        SELECT DISTINCT
            TRIM(job) AS job,
            TRIM(work_order) AS work_order,
            TRIM(super_work_order) AS super_work_order
        FROM reporte_cortes
        WHERE TRIM(super_work_order) = %s
          AND job IS NOT NULL
          AND work_order IS NOT NULL
        ORDER BY TRIM(work_order), TRIM(job)
    """, (str(swo_id or "").strip(),))

    rows = cursor.fetchall() or []
    resultado = []

    for row in rows:
        job = str(row.get("job") or "").strip()
        work_order = str(row.get("work_order") or "").strip()
        super_work_order = str(row.get("super_work_order") or "").strip()
        if job and work_order:
            resultado.append({
                "job": job,
                "job_key": _normalizar_job_key(job),
                "work_order": work_order,
                "super_work_order": super_work_order,
            })

    return resultado


def _swo_tiene_nesting_real(cursor, swo_id: str) -> bool:
    swo_limpia = str(swo_id or "").strip()
    if not swo_limpia:
        return False

    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pqart_swo p
            WHERE TRIM(COALESCE(p.nombre_swo, '')) = %s
            LIMIT 1
        ) AS ok
        """,
        (swo_limpia,),
    )
    row = cursor.fetchone() or {}
    return bool(row.get("ok"))


def _asegurar_swo_nesteada(cursor, swo_id: str, contexto: str = "consultar") -> None:
    if not _swo_tiene_nesting_real(cursor, swo_id):
        raise HTTPException(
            status_code=409,
            detail=(
                f"La SWO '{swo_id}' aún no está nesteada; "
                f"no se puede {contexto} hasta generar el nesting."
            ),
        )


def _obtener_lista_base_por_job(cursor, job: str) -> list[dict]:
    columnas = _obtener_columnas_lista_largos(cursor)

    if not columnas:
        return []

    job_key = _normalizar_job_key(job)

    expr_cantidad = "cantidad" if "cantidad" in columnas else "0::integer"
    expr_cantidad_base = "cantidad_base" if "cantidad_base" in columnas else expr_cantidad
    expr_cantidad_job = "cantidad_job" if "cantidad_job" in columnas else "1::integer"
    expr_cantidad_total = "cantidad_total" if "cantidad_total" in columnas else expr_cantidad

    if "job_key" in columnas:
        where_expr = "job_key = %s"
        where_value = job_key
    else:
        where_expr = "UPPER(REGEXP_REPLACE(BTRIM(job), '\\s+', ' ', 'g')) = %s"
        where_value = job_key

    query = f"""
        SELECT
            TRIM(job) AS job,
            source_csv_name,
            source_csv_path,
            nombre,
            clasificacion,
            largo_in,
            {expr_cantidad} AS cantidad,
            {expr_cantidad_base} AS cantidad_base,
            {expr_cantidad_job} AS cantidad_job,
            {expr_cantidad_total} AS cantidad_total,
            {_sql_col_or_null("proceso", columnas_existentes=columnas)}
        FROM public.lista_largos_job
        WHERE {where_expr}
        ORDER BY
            COALESCE(clasificacion, ''),
            COALESCE(nombre, ''),
            COALESCE(largo_in, 0),
            id
    """
    cursor.execute(query, (where_value,))
    return cursor.fetchall() or []


def _expandir_lista_para_wo(cursor, job: str, work_order: str) -> list[dict]:
    filas_base = _obtener_lista_base_por_job(cursor, job)
    factor_wo = _extraer_factor_wo(work_order)

    filas = []
    for row in filas_base:
        cantidad_base = safe_int(row.get("cantidad_base"), safe_int(row.get("cantidad"), 0))
        cantidad_job = safe_int(row.get("cantidad_job"), 1)
        cantidad_total_job = safe_int(row.get("cantidad_total"), cantidad_base * max(1, cantidad_job))
        cantidad_wo = cantidad_base * max(1, factor_wo)

        filas.append({
            "job": str(job or "").strip(),
            "work_order": str(work_order or "").strip(),
            "factor_wo": factor_wo,
            "source_csv_name": row.get("source_csv_name"),
            "source_csv_path": row.get("source_csv_path"),
            "nombre": row.get("nombre"),
            "clasificacion": row.get("clasificacion"),
            "largo_in": safe_float(row.get("largo_in"), 0.0),
            "cantidad": cantidad_wo,  # cantidad operativa para esa WO
            "cantidad_base": cantidad_base,
            "cantidad_job": cantidad_job,
            "cantidad_total_job": cantidad_total_job,
            "cantidad_wo": cantidad_wo,
            "proceso": str(row.get("proceso") or "").strip(),
        })

    return filas


def construir_lista_largos_wo(wo_id: str) -> dict:
    conexion = None
    cursor = None
    try:
        wo_limpia = str(wo_id or "").strip()

        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        jobs_wo = _obtener_jobs_de_wo(cursor, wo_limpia)
        if not jobs_wo:
            raise HTTPException(status_code=404, detail="WO no encontrada")

        rows = []
        jobs_unicos = []

        for item in jobs_wo:
            job = item["job"]
            work_order = item["work_order"]

            if job not in jobs_unicos:
                jobs_unicos.append(job)

            rows.extend(_expandir_lista_para_wo(cursor, job, work_order))

        if len(rows) > 0:
            asegurar_tablas_lista_largos_operativas()
            asegurar_tabla_material_requerido_ldg()
            try:
                _asegurar_material_requerido_orden(cursor, wo_limpia, "WO")
            except Exception as e_plan:
                print(f"[LISTA_LARGOS][WARN] Plan/pedido WO '{wo_limpia}': {e_plan}")
            conexion.commit()

        return {
            "tipo": "wo",
            "identificador": wo_limpia,
            "work_order": wo_limpia,
            "jobs": jobs_unicos,
            "factor_wo": _extraer_factor_wo(wo_limpia),
            "tiene_lista_largos": len(rows) > 0,
            "total_registros": len(rows),
            "rows": rows,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


def construir_lista_largos_swo(swo_id: str) -> dict:
    conexion = None
    cursor = None
    try:
        swo_limpia = str(swo_id or "").strip()

        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)
        _asegurar_swo_nesteada(cursor, swo_limpia, contexto="consultar lista de largos")

        jobs_swo = _obtener_jobs_de_swo(cursor, swo_limpia)
        if not jobs_swo:
            raise HTTPException(status_code=404, detail="SWO no encontrada")

        rows = []
        jobs_unicos = []
        work_orders_info = []

        vistos = set()
        for item in jobs_swo:
            job = item["job"]
            work_order = item["work_order"]

            if job not in jobs_unicos:
                jobs_unicos.append(job)

            clave = (job, work_order)
            if clave in vistos:
                continue
            vistos.add(clave)

            factor_wo = _extraer_factor_wo(work_order)
            work_orders_info.append({
                "job": job,
                "work_order": work_order,
                "factor_wo": factor_wo,
            })

            rows.extend(_expandir_lista_para_wo(cursor, job, work_order))

        if len(rows) > 0:
            asegurar_tablas_lista_largos_operativas()
            asegurar_tabla_material_requerido_ldg()
            try:
                _asegurar_material_requerido_orden(cursor, swo_limpia, "SWO")
            except Exception as e_plan:
                print(f"[LISTA_LARGOS][WARN] Plan/pedido SWO '{swo_limpia}': {e_plan}")
            conexion.commit()

        return {
            "tipo": "swo",
            "identificador": swo_limpia,
            "super_work_order": swo_limpia,
            "jobs": jobs_unicos,
            "work_orders": work_orders_info,
            "tiene_lista_largos": len(rows) > 0,
            "total_registros": len(rows),
            "rows": rows,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()

# ========================================================
# RUTA REPARADA: OBTENER PLACAS (Anti-Crash 500)
# ========================================================
@app.get("/api/placas")
def obtener_placas(wo: str):
    conexion = None
    try:
        wo_limpio = wo.strip()
        conexion = db_connect()
        cursor = conexion.cursor()

        # SQL simplificado y directo para evitar choques
        query = """
            SELECT DISTINCT
                placa_id,
                calibre,
                geometria->'limites_placa'->>'largo' AS largo_mm,
                geometria->'limites_placa'->>'ancho' AS ancho_mm
            FROM reporte_cortes
            WHERE (work_order = %s OR super_work_order = %s)
              AND (estatus = 'Pendiente' OR estatus = 'Pendiente SWO')
        """
        cursor.execute(query, (wo_limpio, wo_limpio))

        placas_pendientes = []
        for row in cursor.fetchall():
            try:
                largo = float(row[2]) if row[2] is not None else 0.0
            except Exception:
                largo = 0.0

            try:
                ancho = float(row[3]) if row[3] is not None else 0.0
            except Exception:
                ancho = 0.0

            placas_pendientes.append({
                "placa_id": str(row[0]) if row[0] else "N/A",
                "calibre": str(row[1]) if row[1] else "0",
                "largo_mm": largo,
                "ancho_mm": ancho
            })

        print(f"✅ [ÉXITO] Se enviaron {len(placas_pendientes)} placas a la tablet para la orden: {wo_limpio}")
        return placas_pendientes

    except Exception as e:
        import traceback
        print("🚨 [ERROR CRÍTICO EN /api/placas]:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conexion:
            cursor.close()
            conexion.close()


@app.get("/api/work_orders/ingenieria")
def obtener_wo_pendientes():
    conexion = None
    try:
        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)
        query = """
            SELECT work_order as id, MAX(job) as job, MAX(calibre) as calibre_completo, MAX(eficiencia) as eficiencia
            FROM reporte_cortes
            WHERE origen_nesting = 'INGENIERIA' AND super_work_order IS NULL
            GROUP BY work_order ORDER BY work_order DESC;
        """
        cursor.execute(query)
        wos_formateadas = []
        for r in cursor.fetchall():
            partes = str(r["calibre_completo"] or "").split("_", 1)
            wos_formateadas.append({
                "id": r["id"],
                "job": r["job"],
                "calibre": partes[0] if len(partes) > 0 else "N/A",
                "material": partes[1] if len(partes) > 1 else "DESCONOCIDO",
                "eficienciaOriginal": round(r["eficiencia"] or 0.0, 1)
            })
        return {"workOrders": wos_formateadas}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conexion:
            cursor.close()
            conexion.close()


@app.get("/api/work_orders/super")
def obtener_super_wos():
    conexion = None
    try:
        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)
        query = """
            WITH placas_swo AS (
                SELECT
                    TRIM(super_work_order) AS swo_id,
                    TRIM(work_order) AS work_order,
                    MAX(calibre) AS calibre_completo,
                    MAX(estatus) AS estatus,
                    COALESCE(NULLIF(TRIM(placa_id), ''), CONCAT('SIN_PLACA_', id::text)) AS placa_key,
                    MAX(COALESCE(eficiencia, 0)) AS eficiencia_placa
                FROM reporte_cortes
                WHERE super_work_order IS NOT NULL
                GROUP BY
                    TRIM(super_work_order),
                    TRIM(work_order),
                    COALESCE(NULLIF(TRIM(placa_id), ''), CONCAT('SIN_PLACA_', id::text))
            )
            SELECT
                swo_id AS id,
                COUNT(DISTINCT work_order) AS total_origenes,
                MAX(calibre_completo) AS calibre_completo,
                MAX(estatus) AS estatus,
                ROUND(AVG(eficiencia_placa)::numeric, 1) AS eficiencia
            FROM placas_swo
            GROUP BY swo_id
            ORDER BY swo_id DESC;
        """
        cursor.execute(query)
        swos_formateadas = []
        for r in cursor.fetchall():
            swo_id = str(r["id"] or "").strip()
            nesta = _swo_tiene_nesting_real(cursor, swo_id)
            partes = str(r["calibre_completo"] or "").split("_", 1)
            swos_formateadas.append({
                "id": swo_id,
                "origenes": [f"{r['total_origenes']} WOs Fusionadas"],
                "calibre": partes[0] if len(partes) > 0 else "MIXTO",
                "material": partes[1] if len(partes) > 1 else "MIXTO",
                "eficienciaMejorada": float(r.get("eficiencia") or 0.0) if nesta else 0.0,
                "estatus": r["estatus"],
                "nesteada": nesta,
            })
        return {"superWorkOrders": swos_formateadas}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conexion:
            cursor.close()
            conexion.close()


class SolicitudCombinacion(BaseModel):
    work_orders_ids: List[str]

class SesionListaLargosIniciar(BaseModel):
    orden_id: str
    tipo_orden: str
    operador: str
    bahia: str
    piezas_totales: int = 0


class SesionListaLargosAvance(BaseModel):
    orden_id: str
    tipo_orden: str
    piezas_completadas: int


class SesionListaLargosFinalizar(BaseModel):
    orden_id: str
    tipo_orden: str
    piezas_completadas: Optional[int] = None

class ListaLargosTogglePiezaPayload(BaseModel):
    orden_id: str
    tipo_orden: str
    operador: str
    bahia: str
    material: str
    barra_index: int
    pieza_index: int
    completada: bool

class ListaLargosIniciarPiezaPayload(BaseModel):
    orden_id: str
    tipo_orden: str
    operador: str
    bahia: str
    material: str
    barra_index: int
    pieza_index: int

class ListaLargosSobrantePayload(BaseModel):
    orden_id: str
    tipo_orden: str
    operador: str
    bahia: str
    material: str
    barra_index: int
    sobrante_real: Optional[float] = None


class MaterialRequeridoLdGSincronizar(BaseModel):
    orden_id: str
    tipo_orden: str
    plan: dict


class ListaLargosFinalizarPayload(BaseModel):
    orden_id: str
    tipo_orden: str
    operador: str
    bahia: str

@app.post("/api/work_orders/combinar")
def disparar_gatillo_combinacion(solicitud: SolicitudCombinacion):
    conexion = None
    try:
        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT COUNT(DISTINCT super_work_order) as total FROM reporte_cortes WHERE super_work_order IS NOT NULL")
        swo_count = cursor.fetchone()['total']
        nuevo_swo_id = f"SWO-{swo_count + 1:03d}"

        cursor.execute("""
            UPDATE reporte_cortes
            SET super_work_order = %s, estatus = 'Pendiente SWO'
            WHERE work_order = ANY(%s) AND super_work_order IS NULL
        """, (nuevo_swo_id, solicitud.work_orders_ids))
        piezas_agrupadas = cursor.rowcount

        pedido_msg = ""
        try:
            _, pedido_msg = _asegurar_material_requerido_orden(
                cursor, nuevo_swo_id, "SWO"
            )
        except Exception as e_ped:
            pedido_msg = str(e_ped)

        conexion.commit()
        mensaje = (
            f"¡Fusión Exitosa! Se agruparon {piezas_agrupadas} piezas bajo la {nuevo_swo_id}."
        )
        if pedido_msg:
            mensaje += f" Material requerido: {pedido_msg}"
        return {
            "estatus": "ok",
            "mensaje": mensaje,
            "swo_id": nuevo_swo_id,
        }
    except Exception as e:
        if conexion:
            conexion.rollback()
        return {"estatus": "error", "mensaje": f"Error del servidor: {str(e)}"}
    finally:
        if conexion:
            cursor.close()
            conexion.close()


@app.post("/api/reportes/guardar")
def guardar_reporte_dinamico(datos: dict):
    try:
        with open("ultimo_snapshot.json", "w", encoding="utf-8") as archivo:
            json.dump(datos, archivo, indent=4)
    except Exception:
        pass

    conexion = None
    try:
        conexion = db_connect()
        cursor = conexion.cursor()
        swo_id = datos.get("swo")
        snapshot_json = datos.get("snapshot")
        if not swo_id or not snapshot_json:
            raise HTTPException(status_code=400, detail="Faltan datos")

        query = """
            INSERT INTO reportes_dinamicos (super_work_order, datos_snapshot)
            VALUES (%s, %s)
            ON CONFLICT (super_work_order)
            DO UPDATE SET datos_snapshot = EXCLUDED.datos_snapshot, creado_el = CURRENT_TIMESTAMP;
        """
        cursor.execute(query, (swo_id, psycopg2.extras.Json(snapshot_json)))
        conexion.commit()
        return {"estatus": "ok", "mensaje": "Reporte guardado."}
    except Exception as e:
        if conexion:
            conexion.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conexion:
            cursor.close()
            conexion.close()


@app.post("/api/piezas/finalizar_lamina")
def finalizar_lamina(datos: dict):
    conexion = None
    try:
        conexion = db_connect()
        cursor = conexion.cursor()
        for pz in datos.get("resultados", []):
            cursor.execute(
                "UPDATE reporte_cortes SET estatus = %s WHERE id = %s",
                (pz['estatus'], pz['id'])
            )
        conexion.commit()
        return {"estatus": "ok", "mensaje": "¡Reporte guardado!"}
    except Exception as e:
        if conexion:
            conexion.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conexion:
            cursor.close()
            conexion.close()


# ========================================================
# RUTA 2 SOLUCIONADA: VALIDACIÓN DE MATERIAL (Crea la tabla si falta)
# ========================================================
class MaterialValido(BaseModel):
    largo: float
    ancho: float
    grosor: float


@app.post("/api/validar-material")
def validar_material(material: MaterialValido):
    conexion = None
    try:
        conexion = db_connect()
        cursor = conexion.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS material_actual (
                id_material SERIAL PRIMARY KEY,
                largo FLOAT,
                ancho FLOAT,
                grosor FLOAT,
                estatus TEXT,
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        query = """
            INSERT INTO material_actual (largo, ancho, grosor, estatus)
            VALUES (%s, %s, %s, 'validado')
            RETURNING id_material;
        """
        cursor.execute(query, (material.largo, material.ancho, material.grosor))
        id_generado = cursor.fetchone()[0]
        conexion.commit()
        return {"mensaje": "Material validado y guardado", "id_material": id_generado}
    except Exception as e:
        if conexion:
            conexion.rollback()
        print(f"🚨 [ERROR VALIDACION BD]: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conexion:
            cursor.close()
            conexion.close()


def _unlink_con_reintento(ruta: Path, intentos: int = 6) -> bool:
    """Borra un archivo; en Windows un archivo recién leído puede quedar un instante en uso."""
    for i in range(intentos):
        try:
            ruta.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if i < intentos - 1:
                time.sleep(0.05 * (i + 1))
    return False


class ScriptCorteSeleccionPayload(BaseModel):
    orden_id: str
    tipo_orden: str
    sheet_code: str
    bahia: str
    cama: str


def _resolver_tabla_pqart(tipo_orden: str) -> str:
    tipo = (tipo_orden or "").strip().upper()
    if tipo == "SWO":
        return "pqart_swo"
    if tipo == "WO":
        return "pqart_wo"
    raise HTTPException(status_code=422, detail="tipo_orden debe ser 'WO' o 'SWO'.")


def _resolver_columna_orden_pqart(tipo_orden: str) -> str:
    tipo = (tipo_orden or "").strip().upper()
    if tipo == "SWO":
        return "nombre_swo"
    if tipo == "WO":
        return "nombre_wo"
    raise HTTPException(status_code=422, detail="tipo_orden debe ser 'WO' o 'SWO'.")


@app.post("/api/corte/script-seleccion")
def seleccionar_script_corte(payload: ScriptCorteSeleccionPayload):
    conexion = None
    cursor = None
    try:
        orden_id = (payload.orden_id or "").strip()
        tipo_orden = (payload.tipo_orden or "").strip().upper()
        sheet_code = (payload.sheet_code or "").strip()
        bahia = (payload.bahia or "").strip()
        cama = (payload.cama or "").strip().upper()

        if not orden_id:
            raise HTTPException(status_code=422, detail="orden_id es obligatorio.")
        if not sheet_code:
            raise HTTPException(status_code=422, detail="sheet_code es obligatorio.")
        if bahia not in ("3", "4"):
            raise HTTPException(status_code=422, detail="bahia debe ser '3' o '4'.")
        if cama not in ("A", "B", "CL"):
            raise HTTPException(status_code=422, detail="cama debe ser 'A', 'B' o 'CL'.")

        tabla = _resolver_tabla_pqart(tipo_orden)
        columna_orden = _resolver_columna_orden_pqart(tipo_orden)

        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        query = f"""
            SELECT nombre_dxf, ruta, {columna_orden} AS nombre_orden, sheet_code
            FROM {tabla}
            WHERE TRIM(sheet_code) = %s
               OR TRIM({columna_orden}) = %s
            ORDER BY
                CASE WHEN TRIM(sheet_code) = %s THEN 0 ELSE 1 END,
                id DESC
            LIMIT 1;
        """
        cursor.execute(query, (sheet_code, orden_id, sheet_code))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No se encontró registro en {tabla} para orden '{orden_id}' o sheet_code '{sheet_code}'."
            )

        ruta_dxf = (row.get("ruta") or "").strip()
        nombre_dxf = (row.get("nombre_dxf") or "").strip()

        if not ruta_dxf:
            raise HTTPException(status_code=404, detail="El registro pqart no tiene ruta de archivo.")

        path_dxf = Path(ruta_dxf)
        base_dir = path_dxf.parent
        if not base_dir.exists():
            raise HTTPException(status_code=404, detail=f"No existe el directorio del nesting: {base_dir}")

        nombre_base = Path(nombre_dxf).stem if nombre_dxf else path_dxf.stem
        sufijo = "CL" if cama == "CL" else f"B{bahia}{cama}"
        nombre_script_objetivo = f"{nombre_base}{sufijo}"

        candidatos_objetivo = []
        for ext in (".tp", ".txt"):
            candidato = base_dir / f"{nombre_script_objetivo}{ext}"
            if candidato.is_file():
                candidatos_objetivo.append(candidato)

        if not candidatos_objetivo:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No se encontró el script esperado '{nombre_script_objetivo}.tp' "
                    f"o '{nombre_script_objetivo}.txt' en {base_dir}."
                )
            )

        script_objetivo = sorted(
            candidatos_objetivo,
            key=lambda p: 0 if p.suffix.lower() == ".tp" else 1
        )[0]

        variantes = []
        for ext in (".tp", ".txt"):
            variantes.extend([
                p for p in base_dir.glob(f"{nombre_base}*{ext}")
                if p.is_file()
            ])
        variantes = [
            p for p in variantes
            if p.is_file() and (
                p.name.startswith(f"{nombre_base}B") or p.name.startswith(f"{nombre_base}CL")
            )
        ]

        eliminados = []
        for archivo in variantes:
            if archivo.resolve() == script_objetivo.resolve():
                continue
            if _unlink_con_reintento(archivo):
                eliminados.append(str(archivo))

        # Leemos el script completo para poder eliminar también el archivo origen
        # y aún así enviar el binario al navegador con el nombre exacto.
        contenido_script = script_objetivo.read_bytes()
        nombre_descarga = script_objetivo.name

        ruta_script_origen = str(script_objetivo)
        if _unlink_con_reintento(script_objetivo):
            eliminados.append(ruta_script_origen)

        # Se entrega el binario a cada navegador (estación con robot). No se escribe en el disco del servidor.
        return Response(
            content=contenido_script,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{nombre_descarga}"',
                "X-Download-Filename": nombre_descarga,
                "X-Tabla-Origen": tabla,
                "X-Scripts-Deleted-From-Origin": str(len(eliminados)),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al preparar script de corte: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


@app.patch("/api/piezas/{pieza_id}/estatus")
def actualizar_estatus_instantaneo(pieza_id: int, payload: dict):
    conexion = None
    try:
        conexion = db_connect()
        cursor = conexion.cursor()
        cursor.execute(
            "UPDATE reporte_cortes SET estatus = %s WHERE id = %s",
            (payload.get("estatus"), pieza_id)
        )
        conexion.commit()
        return {"estatus": "ok"}
    except Exception as e:
        if conexion:
            conexion.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conexion:
            cursor.close()
            conexion.close()


# ========================================================
# RUTAS DE RADIOGRAFÍA (CON KPI DE AVANCE + DIBUJOS PDF)
# ========================================================

def safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default

def material_desde_calibre(calibre: str) -> str:
    text = str(calibre or "").strip()
    if "_" in text:
        return text.split("_", 1)[1].replace("_", " ").strip() or text
    return text or "N/A"

def geo_to_dict(geo_raw):
    if isinstance(geo_raw, dict):
        return geo_raw
    if isinstance(geo_raw, str):
        try:
            parsed = json.loads(geo_raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def extraer_sheet_meta(row: dict, geo_dict: dict) -> dict:
    geo_dict = geo_dict if isinstance(geo_dict, dict) else {}
    meta_geo = geo_dict.get("sheet_meta", {}) if isinstance(geo_dict.get("sheet_meta", {}), dict) else {}

    sheet_uid = (
        row.get("sheet_uid")
        or meta_geo.get("sheet_uid")
        or row.get("placa_id")
    )
    sheet_display_name = (
        row.get("sheet_display_name")
        or meta_geo.get("sheet_display_name")
        or row.get("placa_id")
    )
    plate_group_key = (
        row.get("plate_group_key")
        or meta_geo.get("plate_group_key")
        or sheet_uid
        or row.get("placa_id")
    )
    sheet_code = row.get("sheet_code") or meta_geo.get("sheet_code")
    sheet_seq = row.get("sheet_seq")
    if sheet_seq is None:
        sheet_seq = meta_geo.get("sheet_seq")

    placa_ancho_canonico_mm = row.get("placa_ancho_canonico_mm")
    if placa_ancho_canonico_mm is None:
        placa_ancho_canonico_mm = meta_geo.get("placa_ancho_canonico_mm")

    placa_largo_canonico_mm = row.get("placa_largo_canonico_mm")
    if placa_largo_canonico_mm is None:
        placa_largo_canonico_mm = meta_geo.get("placa_largo_canonico_mm")

    nest_instance_id = row.get("nest_instance_id") or meta_geo.get("nest_instance_id")
    source_nest_name = row.get("source_nest_name") or meta_geo.get("source_nest_name")
    is_rtz = row.get("is_rtz")
    if is_rtz is None:
        is_rtz = meta_geo.get("is_rtz", False)

    return {
        "sheet_uid": sheet_uid,
        "sheet_display_name": sheet_display_name,
        "plate_group_key": plate_group_key,
        "sheet_code": sheet_code,
        "sheet_seq": safe_int(sheet_seq, 0),
        "placa_ancho_canonico_mm": safe_float(placa_ancho_canonico_mm, 0.0),
        "placa_largo_canonico_mm": safe_float(placa_largo_canonico_mm, 0.0),
        "nest_instance_id": nest_instance_id,
        "source_nest_name": source_nest_name,
        "is_rtz": bool(is_rtz),
    }


def normalizar_puntos_simples(data):
    """
    Convierte una colección simple de puntos [{x,y}, ...] a lista válida.
    Si no tiene ese formato, regresa [].
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return []

    if isinstance(data, dict):
        data = data.get("puntos") or data.get("points") or data.get("path") or []

    if not isinstance(data, list):
        return []

    puntos_validos = []
    for pt in data:
        if isinstance(pt, dict):
            x = pt.get("x", pt.get("X"))
            y = pt.get("y", pt.get("Y"))
            if x is not None and y is not None:
                puntos_validos.append({
                    "x": safe_float(x, 0.0),
                    "y": safe_float(y, 0.0)
                })

    return puntos_validos


def normalizar_coleccion_paths(data):
    """
    Convierte cualquiera de estos formatos a:
    [
      [ {x,y}, {x,y}, ... ],
      [ {x,y}, {x,y}, ... ]
    ]
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return []

    if not isinstance(data, list):
        return []

    if data and all(
        isinstance(pt, dict) and (("x" in pt or "X" in pt) and ("y" in pt or "Y" in pt))
        for pt in data
    ):
        puntos = normalizar_puntos_simples(data)
        return [puntos] if len(puntos) >= 2 else []

    paths = []
    for entry in data:
        if isinstance(entry, str):
            try:
                entry = json.loads(entry)
            except Exception:
                continue

        if isinstance(entry, list):
            puntos = normalizar_puntos_simples(entry)
            if len(puntos) >= 2:
                paths.append(puntos)

        elif isinstance(entry, dict):
            puntos = normalizar_puntos_simples(
                entry.get("puntos") or entry.get("points") or entry.get("path") or entry.get("coords")
            )
            if len(puntos) >= 2:
                paths.append(puntos)

    return paths


def first_path_collection(geo_dict, *keys):
    for key in keys:
        value = geo_dict.get(key)
        paths = normalizar_coleccion_paths(value)
        if paths:
            return paths
    return []


def limpiar_token_archivo(value: str) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace("\\", " ")
        .replace("/", " ")
        .replace("_", " ")
        .replace("-", " ")
    )


def cargar_mapa_dxf():
    try:
        if os.path.isfile(DXF_MAP_FILE):
            with open(DXF_MAP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"⚠️ No se pudo leer DXF_MAP_FILE: {e}")
    return {}


def resolver_dxf_para_placa(identificador: str, placa_id: str):
    """
    Prioridad:
    1) Mapa manual exacto
    2) Búsqueda automática por nombre dentro de DXF_ROOT_DIR
    """
    mapa = cargar_mapa_dxf()

    claves_prueba = [
        f"{identificador}|{placa_id}",
        placa_id,
        identificador,
    ]

    for key in claves_prueba:
        ruta = mapa.get(key)
        if ruta and os.path.isfile(ruta):
            return ruta

    root = Path(DXF_ROOT_DIR)
    if not root.exists():
        return None

    token_placa = limpiar_token_archivo(placa_id)
    token_id = limpiar_token_archivo(identificador)

    mejor = None
    mejor_score = -1

    for p in root.rglob("*.dxf"):
        nombre = limpiar_token_archivo(p.stem)

        score = 0
        if token_placa and token_placa in nombre:
            score += 100
        if token_id and token_id in nombre:
            score += 40
        if token_placa and nombre == token_placa:
            score += 200

        if score > mejor_score:
            mejor_score = score
            mejor = str(p)

    return mejor if mejor_score > 0 else None


def render_dxf_a_data_uri(dxf_path: str):
    """
    Renderiza el DXF final a PNG base64 para incrustarlo en el PDF.
    Requiere:
        pip install ezdxf matplotlib
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import ezdxf
        from ezdxf.addons.drawing import RenderContext, Frontend
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
        from ezdxf.addons.drawing.config import Configuration

        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()

        fig = plt.figure(figsize=(14, 5), dpi=160)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_aspect("equal")
        ax.axis("off")

        ctx = RenderContext(doc)
        backend = MatplotlibBackend(ax)
        Frontend(ctx, backend, config=Configuration()).draw_layout(msp, finalize=True)

        ax.margins(0.02)

        buf = io.BytesIO()
        fig.savefig(
            buf,
            format="png",
            dpi=160,
            bbox_inches="tight",
            pad_inches=0.08,
            facecolor="white"
        )
        plt.close(fig)

        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        print(f"⚠️ No se pudo renderizar DXF '{dxf_path}': {e}")
        return None


def construir_radiografia(identificador: str, es_swo: bool):
    conexion = None
    cursor = None
    try:
        identificador_limpio = identificador.strip()
        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)
        if es_swo:
            _asegurar_swo_nesteada(cursor, identificador_limpio, contexto="consultar radiografía")

        columnas_existentes = _obtener_columnas_reporte_cortes(cursor)
        campo_where = "super_work_order" if es_swo else "work_order"

        # Semántica PDF:
        # - nombre_orden = job/proyecto cuando es WO
        # - work_order_label = WO actual
        # - en SWO conservamos el identificador actual para ambos
        nombre_orden_pdf = identificador_limpio
        work_order_label_pdf = identificador_limpio

        distinct_plate_expr = "COALESCE(sheet_uid, placa_id)" if "sheet_uid" in columnas_existentes else "placa_id"
        display_name_expr = "COALESCE(sheet_display_name, placa_id)" if "sheet_display_name" in columnas_existentes else "placa_id"
        order_expr = "COALESCE(sheet_seq, 999999), COALESCE(sheet_display_name, placa_id), id" if "sheet_seq" in columnas_existentes else "placa_id, id"

        # 1) TOTALES
        cursor.execute(
            f"""
            SELECT
                COUNT(id) as tp,
                COUNT(DISTINCT {distinct_plate_expr}) as tpl
            FROM reporte_cortes
            WHERE TRIM({campo_where}) = %s
            """,
            (identificador_limpio,)
        )
        totales = cursor.fetchone()

        print(f"🔍 [TEST BD] Orden: {identificador_limpio} | Piezas reales en BD: {totales['tp'] if totales else 0}")

        if not totales or totales["tp"] == 0:
            return {
                "nombre": identificador_limpio,
                "nombre_orden": nombre_orden_pdf,
                "work_order_label": work_order_label_pdf,
                "total_piezas": 0,
                "total_placas": 0,
                "detalles_tabla": [],
                "placas": []
            }

        # 2) TABLA KPI
        query_tabla = f"""
            SELECT
                COALESCE(SPLIT_PART(calibre, '_', 1), 'N/A') AS thk,
                COALESCE(SPLIT_PART({display_name_expr}, ' ', 1), 'PENDIENTE') AS code,
                COUNT(id) AS total_del_grupo,
                COUNT(id) FILTER (WHERE estatus IN ('Cortado', 'Defectuoso')) AS procesadas_del_grupo,
                COUNT(DISTINCT {distinct_plate_expr}) AS qty
            FROM reporte_cortes
            WHERE TRIM({campo_where}) = %s
              AND placa_id IS NOT NULL
            GROUP BY SPLIT_PART(calibre, '_', 1), SPLIT_PART({display_name_expr}, ' ', 1)
            ORDER BY thk, code
        """
        cursor.execute(query_tabla, (identificador_limpio,))

        detalles_tabla = []
        for f in cursor.fetchall():
            pct_avance = round((f["procesadas_del_grupo"] / f["total_del_grupo"]) * 100, 1) if f["total_del_grupo"] > 0 else 0
            detalles_tabla.append({
                "thk": f["thk"],
                "code": f["code"],
                "num_comp": f["total_del_grupo"],
                "qty": f["qty"],
                "porcentaje": pct_avance
            })

        # 3) GEOMETRÍA Y DATOS PARA PDF
        query_geo = f"""
            SELECT
                id,
                job,
                item,
                calibre,
                placa_id,
                estatus,
                geometria,
                {_sql_col_or_null("sheet_uid", "sheet_uid", "text", columnas_existentes)},
                {_sql_col_or_null("sheet_code", "sheet_code", "text", columnas_existentes)},
                {_sql_col_or_null("sheet_seq", "sheet_seq", "integer", columnas_existentes)},
                {_sql_col_or_null("sheet_display_name", "sheet_display_name", "text", columnas_existentes)},
                {_sql_col_or_null("plate_group_key", "plate_group_key", "text", columnas_existentes)},
                {_sql_col_or_null("placa_ancho_canonico_mm", "placa_ancho_canonico_mm", "numeric", columnas_existentes)},
                {_sql_col_or_null("placa_largo_canonico_mm", "placa_largo_canonico_mm", "numeric", columnas_existentes)},
                {_sql_col_or_null("nest_instance_id", "nest_instance_id", "text", columnas_existentes)},
                {_sql_col_or_null("source_nest_name", "source_nest_name", "text", columnas_existentes)},
                {_sql_col_or_null("is_rtz", "is_rtz", "boolean", columnas_existentes)}
            FROM reporte_cortes
            WHERE TRIM({campo_where}) = %s
              AND placa_id IS NOT NULL
            ORDER BY {order_expr}
        """
        cursor.execute(query_geo, (identificador_limpio,))
        piezas_db = cursor.fetchall()

        if not es_swo and piezas_db:
            primer_job = str(piezas_db[0].get("job") or "").strip()
            if primer_job:
                nombre_orden_pdf = primer_job
            work_order_label_pdf = identificador_limpio

        placas_dict = {}

        for p in piezas_db:
            geo_pieza = geo_to_dict(p["geometria"])
            sheet_meta = extraer_sheet_meta(p, geo_pieza)

            pl_group = str(sheet_meta["plate_group_key"] or sheet_meta["sheet_uid"] or p["placa_id"])
            pl_id_visible = str(sheet_meta["sheet_display_name"] or p["placa_id"])

            limites = geo_pieza.get("limites_placa", {}) if isinstance(geo_pieza.get("limites_placa", {}), dict) else {}

            # Exterior / contorno
            coordenadas = (
                normalizar_puntos_simples(geo_pieza.get("puntos")) or
                normalizar_puntos_simples(geo_pieza.get("exterior")) or
                normalizar_puntos_simples(geo_pieza.get("outer")) or
                normalizar_puntos_simples(geo_pieza.get("contorno")) or
                []
            )

            # Cortes internos
            interiores = first_path_collection(
                geo_pieza,
                "interiores",
                "interior",
                "agujeros",
                "holes",
                "internos",
                "inner",
                "cut_inner",
                "cortes_internos"
            )

            # Marcaje
            marcaje = first_path_collection(
                geo_pieza,
                "marcaje",
                "marcajes",
                "lineasMarcaje",
                "lineas_marcaje",
                "marking",
                "markings",
                "marcas"
            )

            if pl_group not in placas_dict:
                material_real = material_desde_calibre(p.get("calibre"))

                largo_placa = sheet_meta["placa_largo_canonico_mm"]
                ancho_placa = sheet_meta["placa_ancho_canonico_mm"]

                if not largo_placa:
                    largo_placa = safe_float(limites.get("largo"), 0.0)
                if not ancho_placa:
                    ancho_placa = safe_float(limites.get("ancho"), 0.0)

                dxf_path = resolver_dxf_para_placa(identificador_limpio, pl_id_visible)
                dxf_preview = render_dxf_a_data_uri(dxf_path) if dxf_path else None

                placas_dict[pl_group] = {
                    "id": pl_id_visible,
                    "sheet_uid": sheet_meta["sheet_uid"],
                    "plate_group_key": pl_group,
                    "sheet_code": sheet_meta["sheet_code"],
                    "sheet_seq": sheet_meta["sheet_seq"],
                    "calibre": p["calibre"],
                    "material": material_real,
                    "largoPlaca": largo_placa,
                    "anchoPlaca": ancho_placa,
                    "dxfPreviewDataUri": dxf_preview,
                    "dxfFileName": os.path.basename(dxf_path) if dxf_path else None,
                    "piezas": []
                }

            # Si el primer registro vino sin canónicos y otro sí los trae, rellenamos
            if not placas_dict[pl_group]["largoPlaca"]:
                placas_dict[pl_group]["largoPlaca"] = sheet_meta["placa_largo_canonico_mm"] or safe_float(limites.get("largo"), 0.0)
            if not placas_dict[pl_group]["anchoPlaca"]:
                placas_dict[pl_group]["anchoPlaca"] = sheet_meta["placa_ancho_canonico_mm"] or safe_float(limites.get("ancho"), 0.0)
            if not placas_dict[pl_group].get("sheet_code") and sheet_meta["sheet_code"]:
                placas_dict[pl_group]["sheet_code"] = sheet_meta["sheet_code"]
            if not placas_dict[pl_group].get("sheet_seq") and sheet_meta["sheet_seq"]:
                placas_dict[pl_group]["sheet_seq"] = sheet_meta["sheet_seq"]

            placas_dict[pl_group]["piezas"].append({
                "id": str(p["id"]),
                "job": p.get("job") or "-",
                "mark": p.get("item") or "S/M",
                "item": p.get("item") or "-",
                "estatus": p.get("estatus") or "-",
                "sheet_uid": sheet_meta["sheet_uid"],
                "sheet_code": sheet_meta["sheet_code"],
                "geometria": {
                    "puntos": coordenadas,
                    "interiores": interiores,
                    "marcaje": marcaje
                }
            })

        placas_ordenadas = sorted(
            placas_dict.values(),
            key=lambda pl: (
                safe_int(pl.get("sheet_seq"), 999999),
                str(pl.get("sheet_code") or ""),
                str(pl.get("id") or "")
            )
        )

        return {
            "nombre": identificador_limpio,
            "nombre_orden": nombre_orden_pdf,
            "work_order_label": work_order_label_pdf,
            "total_piezas": totales["tp"],
            "total_placas": totales["tpl"],
            "detalles_tabla": detalles_tabla,
            "placas": placas_ordenadas
        }

    except Exception as e:
        print(f"🚨 ERROR EN RADIOGRAFÍA: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


def _extraer_grosor_in(calibre: str) -> float:
    texto = str(calibre or "")
    match = re.search(r"[\d.]+", texto)
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except Exception:
        return 0.0


def _resumen_estado_placa(piezas: list[dict]) -> dict:
    piezas = piezas or []

    pendientes = 0
    cortadas = 0
    defectuosas = 0

    for pieza in piezas:
        est = str((pieza or {}).get("estatus") or "").strip().upper()
        if est in ("PENDIENTE", "PENDIENTE SWO"):
            pendientes += 1
        elif est == "CORTADO":
            cortadas += 1
        elif est == "DEFECTUOSO":
            defectuosas += 1

    total = len(piezas)

    if total == 0:
        estado_operador = "vacia"
    elif pendientes == 0:
        estado_operador = "consumida"
    elif pendientes < total:
        estado_operador = "en_proceso"
    else:
        estado_operador = "pendiente"

    return {
        "piezas_total": total,
        "piezas_pendientes": pendientes,
        "piezas_cortadas": cortadas,
        "piezas_defectuosas": defectuosas,
        "estado_operador": estado_operador,
    }


def construir_placas_operador(detalle: dict) -> dict:
    nombre = str((detalle or {}).get("nombre") or "").strip()
    placas = (detalle or {}).get("placas") or []

    placas_operador = []

    for idx, placa in enumerate(placas, start=1):
        calibre = str(placa.get("calibre") or "")
        largo_mm = safe_float(placa.get("largoPlaca"), 0.0)
        ancho_mm = safe_float(placa.get("anchoPlaca"), 0.0)
        piezas = placa.get("piezas") or []

        resumen = _resumen_estado_placa(piezas)

        orden_consumo = safe_int(placa.get("sheet_seq"), idx)
        sheet_code = str(placa.get("sheet_code") or f"{nombre}-H{orden_consumo}")

        placas_operador.append({
            "placa_id": str(placa.get("id") or "").strip(),
            "sheet_uid": str(placa.get("sheet_uid") or "").strip(),
            "plate_group_key": str(placa.get("plate_group_key") or "").strip(),
            "calibre": calibre,
            "material": str(placa.get("material") or "").strip(),
            "largo_mm": largo_mm,
            "ancho_mm": ancho_mm,
            "largo_in": round(largo_mm / 25.4, 3) if largo_mm else 0.0,
            "ancho_in": round(ancho_mm / 25.4, 3) if ancho_mm else 0.0,
            "grosor_in": _extraer_grosor_in(calibre),
            "orden_consumo": orden_consumo,
            "sheet_code": sheet_code,
            "piezas": piezas,
            **resumen,
        })

    placas_operador.sort(
        key=lambda p: (
            safe_int(p.get("orden_consumo"), 999999),
            str(p.get("sheet_code") or ""),
            str(p.get("placa_id") or "")
        )
    )

    return {
        "nombre": nombre,
        "placas": placas_operador,
    }


@app.get("/api/swo/detalles")
def obtener_detalles_swo(swo_id: str):
    return construir_radiografia(swo_id, es_swo=True)


@app.get("/api/wo/detalles")
def obtener_detalles_wo(wo_id: str):
    return construir_radiografia(wo_id, es_swo=False)


@app.get("/api/wo/{wo_id:path}/detalles")
def obtener_detalles_wo_path(wo_id: str):
    return construir_radiografia(wo_id, es_swo=False)


@app.get("/api/swo/placas-operador")
def obtener_placas_operador_swo(swo_id: str):
    detalle = construir_radiografia(swo_id, es_swo=True)
    return construir_placas_operador(detalle)


@app.get("/api/wo/placas-operador")
def obtener_placas_operador_wo(wo_id: str):
    detalle = construir_radiografia(wo_id, es_swo=False)
    return construir_placas_operador(detalle)

@app.get("/api/wo/lista-largos")
def obtener_lista_largos_wo(wo_id: str):
    return construir_lista_largos_wo(wo_id)


@app.get("/api/wo/{wo_id:path}/lista-largos")
def obtener_lista_largos_wo_path(wo_id: str):
    return construir_lista_largos_wo(wo_id)


@app.get("/api/swo/lista-largos")
def obtener_lista_largos_swo(swo_id: str):
    return construir_lista_largos_swo(swo_id)

# ========================================================
# RUTAS NUEVAS - SESIONES OPERATIVAS LISTA DE LARGOS
# ========================================================
@app.get("/api/lista-largos/sesion")
def obtener_sesion_lista_largos(
    orden_id: str = Query(..., description="Identificador de la WO o SWO"),
    tipo_orden: str = Query(..., description="WO o SWO"),
):
    asegurar_tabla_lista_largos_sesiones()

    conexion = None
    cursor = None
    try:
        orden = normalizar_orden_id(orden_id)
        tipo = normalizar_tipo_orden(tipo_orden)

        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        sesion = obtener_sesion_prioritaria(cursor, orden, tipo)

        return {
            "estatus": "ok",
            "sesion": serializar_sesion(sesion)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al consultar sesión: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


@app.post("/api/lista-largos/sesion/iniciar")
def iniciar_sesion_lista_largos(payload: SesionListaLargosIniciar):
    asegurar_tabla_lista_largos_sesiones()
    asegurar_tablas_lista_largos_operativas()

    conexion = None
    cursor = None
    try:
        orden = normalizar_orden_id(payload.orden_id)
        tipo = normalizar_tipo_orden(payload.tipo_orden)
        operador = (payload.operador or "").strip()
        bahia = (payload.bahia or "").strip()
        piezas_totales = max(0, int(payload.piezas_totales or 0))

        if not operador:
            raise HTTPException(status_code=422, detail="operador es obligatorio.")
        if not bahia:
            raise HTTPException(status_code=422, detail="bahia es obligatoria.")

        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        plan_row = _ll_cargar_plan_row(cursor, orden, tipo)
        if plan_row and _ll_plan_ya_finalizado(plan_row):
            raise HTTPException(
                status_code=409,
                detail=f"La orden {orden} ya está finalizada y no puede retomarse."
            )

        sesion_activa = obtener_sesion_activa(cursor, orden, tipo)
        if sesion_activa:
            bahia_activa = (sesion_activa.get("bahia") or "").strip().lower()
            operador_activo = (sesion_activa.get("operador") or "").strip().lower()

            if bahia_activa != bahia.lower():
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"La orden {orden} ya está activa en la bahía "
                        f"{sesion_activa.get('bahia')} con operador "
                        f"{sesion_activa.get('operador')}."
                    )
                )

            if operador_activo == operador.lower():
                return {
                    "estatus": "ok",
                    "mensaje": "La sesión ya estaba activa para esta orden.",
                    "sesion": serializar_sesion(sesion_activa)
                }

            raise HTTPException(
                status_code=409,
                detail=(
                    f"La orden {orden} ya tiene un turno activo con "
                    f"{sesion_activa.get('operador')} en la bahía "
                    f"{sesion_activa.get('bahia')}."
                )
            )
        
        # Antes de arrancar realmente el corte, se recalcula el plan una última vez
        # contra el stock vivo de remanentes y AHÍ sí se reservan.
        plan_json, plan_row = _ll_obtener_o_generar_plan(cursor, orden, tipo, reservar=True)

        if plan_row and _ll_plan_ya_finalizado(plan_row):
            raise HTTPException(
                status_code=409,
                detail=f"La orden {orden} ya está finalizada y no puede retomarse."
            )

        piezas_plan = safe_int(plan_json.get("total_piezas"), 0)
        if piezas_plan > 0:
            piezas_totales = max(piezas_totales, piezas_plan)

        ultima_sesion = obtener_ultima_sesion(cursor, orden, tipo)

        if ultima_sesion and str(ultima_sesion.get("estado") or "").lower() != "finalizada":
            bahia_bloqueada = (ultima_sesion.get("bahia") or "").strip()
            if bahia_bloqueada and bahia_bloqueada.lower() != bahia.lower():
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"La orden {orden} quedó asignada a la bahía "
                        f"{bahia_bloqueada}. Debe retomarse en esa misma bahía."
                    )
                )

        # Si existe una sesión cerrada previa, se reactiva la MISMA sesión
        # para conservar la hora_inicio original del corte.
        if ultima_sesion and str(ultima_sesion.get("estado") or "").lower() == "cerrada":
            piezas_totales_base = max(
                piezas_totales,
                safe_int(ultima_sesion.get("piezas_totales"), 0)
            )
            piezas_completadas_base = safe_int(ultima_sesion.get("piezas_completadas"), 0)

            cursor.execute("""
                UPDATE lista_largos_sesiones
                SET operador = %s,
                    bahia = %s,
                    estado = 'activa',
                    hora_fin = NULL,
                    hora_ultimo_movimiento = NOW(),
                    piezas_totales = %s,
                    piezas_completadas = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *;
            """, (
                operador,
                bahia,
                piezas_totales_base,
                piezas_completadas_base,
                ultima_sesion["id"],
            ))

            sesion_reanudada = cursor.fetchone()

            _ll_crear_turno(
                cursor,
                sesion_reanudada["id"],
                orden,
                tipo,
                operador,
                bahia,
                piezas_completadas_base
            )

            conexion.commit()

            return {
                "estatus": "ok",
                "mensaje": "Corte reanudado correctamente.",
                "sesion": serializar_sesion(sesion_reanudada)
            }

        # Si no había sesión previa reutilizable, se crea una nueva.
        piezas_totales_base = piezas_totales
        piezas_completadas_base = 0

        if ultima_sesion:
            piezas_totales_base = max(
                piezas_totales_base,
                safe_int(ultima_sesion.get("piezas_totales"), 0)
            )
            piezas_completadas_base = safe_int(ultima_sesion.get("piezas_completadas"), 0)

        cursor.execute("""
            INSERT INTO lista_largos_sesiones (
                orden_id,
                tipo_orden,
                operador,
                bahia,
                estado,
                hora_inicio,
                hora_ultimo_movimiento,
                piezas_totales,
                piezas_completadas,
                created_at,
                updated_at
            )
            VALUES (
                %s, %s, %s, %s,
                'activa',
                NOW(),
                NOW(),
                %s,
                %s,
                NOW(),
                NOW()
            )
            RETURNING *;
        """, (
            orden,
            tipo,
            operador,
            bahia,
            piezas_totales_base,
            piezas_completadas_base,
        ))

        nueva_sesion = cursor.fetchone()

        _ll_crear_turno(
            cursor,
            nueva_sesion["id"],
            orden,
            tipo,
            operador,
            bahia,
            piezas_completadas_base
        )

        conexion.commit()

        return {
            "estatus": "ok",
            "mensaje": "Sesión iniciada correctamente.",
            "sesion": serializar_sesion(nueva_sesion)
        }
    

    except HTTPException:
        if conexion:
            conexion.rollback()
        raise
    except Exception as e:
        if conexion:
            conexion.rollback()
        raise HTTPException(status_code=500, detail=f"Error al iniciar sesión: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


@app.post("/api/lista-largos/sesion/avance")
def actualizar_avance_lista_largos(payload: SesionListaLargosAvance):
    asegurar_tabla_lista_largos_sesiones()

    conexion = None
    cursor = None
    try:
        orden = normalizar_orden_id(payload.orden_id)
        tipo = normalizar_tipo_orden(payload.tipo_orden)
        piezas_completadas = max(0, int(payload.piezas_completadas or 0))

        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        sesion_activa = obtener_sesion_activa(cursor, orden, tipo)

        if not sesion_activa:
            raise HTTPException(status_code=404, detail="No existe una sesión activa para esa orden.")

        piezas_totales = int(sesion_activa.get("piezas_totales") or 0)
        if piezas_totales > 0:
            piezas_completadas = min(piezas_completadas, piezas_totales)

        cursor.execute("""
            UPDATE lista_largos_sesiones
            SET piezas_completadas = %s,
                hora_ultimo_movimiento = NOW(),
                updated_at = NOW()
            WHERE id = %s
            RETURNING *;
        """, (piezas_completadas, sesion_activa["id"]))

        sesion_actualizada = cursor.fetchone()


        conexion.commit()

        return {
            "estatus": "ok",
            "mensaje": "Avance actualizado correctamente.",
            "sesion": serializar_sesion(sesion_actualizada)
        }
    except HTTPException:
        if conexion:
            conexion.rollback()
        raise
    except Exception as e:
        if conexion:
            conexion.rollback()
        raise HTTPException(status_code=500, detail=f"Error al actualizar avance: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


@app.post("/api/lista-largos/sesion/finalizar")
def finalizar_sesion_lista_largos(payload: SesionListaLargosFinalizar):
    asegurar_tabla_lista_largos_sesiones()
    asegurar_tablas_lista_largos_operativas()

    conexion = None
    cursor = None
    try:
        orden = normalizar_orden_id(payload.orden_id)
        tipo = normalizar_tipo_orden(payload.tipo_orden)

        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        sesion_activa = obtener_sesion_activa(cursor, orden, tipo)
        if not sesion_activa:
            raise HTTPException(status_code=404, detail="No existe una sesión activa para cerrar turno.")

        pieza_en_proceso = _ll_pieza_en_proceso(cursor, orden, tipo)
        if pieza_en_proceso:
            raise HTTPException(
                status_code=409,
                detail=(
                    "No se puede pausar el corte mientras exista una pieza en proceso. "
                    f"Material: {pieza_en_proceso.get('material')}, "
                    f"Barra: {pieza_en_proceso.get('barra_index')}, "
                    f"Pieza: {pieza_en_proceso.get('pieza_index')}."
                )
            )

        piezas_totales = int(sesion_activa.get("piezas_totales") or 0)

        if payload.piezas_completadas is None:
            piezas_completadas = int(sesion_activa.get("piezas_completadas") or 0)
        else:
            piezas_completadas = max(0, int(payload.piezas_completadas))

        if piezas_totales > 0:
            piezas_completadas = min(piezas_completadas, piezas_totales)

        cursor.execute("""
            UPDATE lista_largos_sesiones
            SET estado = 'cerrada',
                piezas_completadas = %s,
                hora_ultimo_movimiento = NOW(),
                hora_fin = NULL,
                updated_at = NOW()
            WHERE id = %s
            RETURNING *;
        """, (piezas_completadas, sesion_activa["id"]))

        sesion_cerrada = cursor.fetchone()

        _ll_cerrar_turno_activo(
            cursor,
            orden,
            tipo,
            "pausado",
            piezas_completadas
        )

        conexion.commit()

        return {
            "estatus": "ok",
            "mensaje": "Corte pausado correctamente.",
            "sesion": serializar_sesion(sesion_cerrada)
        }

    except HTTPException:
        if conexion:
            conexion.rollback()
        raise
    except Exception as e:
        if conexion:
            conexion.rollback()
        raise HTTPException(status_code=500, detail=f"Error al cerrar turno: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()

# ========================================================
# HELPERS NUEVOS - PLAN CONGELADO / CORTES / SOBRANTES / REMANENTES
# ========================================================
def asegurar_tablas_lista_largos_operativas():
    conexion = None
    cursor = None
    try:
        conexion = db_connect()
        cursor = conexion.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lista_largos_planes (
                id SERIAL PRIMARY KEY,
                orden_id VARCHAR(100) NOT NULL,
                tipo_orden VARCHAR(10) NOT NULL,
                plan_hash TEXT,
                plan_json JSONB NOT NULL,
                total_piezas INTEGER NOT NULL DEFAULT 0,
                total_barras INTEGER NOT NULL DEFAULT 0,
                estado VARCHAR(20) NOT NULL DEFAULT 'activo',
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_ll_plan_tipo CHECK (tipo_orden IN ('WO', 'SWO')),
                CONSTRAINT chk_ll_plan_estado CHECK (estado IN ('activo', 'finalizado')),
                CONSTRAINT uq_ll_plan UNIQUE (orden_id, tipo_orden)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lista_largos_cortes (
                orden_id VARCHAR(100) NOT NULL,
                tipo_orden VARCHAR(10) NOT NULL,
                material VARCHAR(120) NOT NULL,
                barra_index INTEGER NOT NULL,
                pieza_index INTEGER NOT NULL,
                pieza_uid VARCHAR(200) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
                fecha_update TIMESTAMP NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_ll_cortes_tipo CHECK (tipo_orden IN ('WO', 'SWO')),
                CONSTRAINT chk_ll_cortes_status CHECK (status IN ('PENDIENTE', 'COMPLETADA')),
                PRIMARY KEY (orden_id, tipo_orden, material, barra_index, pieza_index)
            );
        """)

        cursor.execute("""
            ALTER TABLE lista_largos_cortes
            DROP CONSTRAINT IF EXISTS chk_ll_cortes_status;
        """)

        cursor.execute("""
            ALTER TABLE lista_largos_cortes
            ADD CONSTRAINT chk_ll_cortes_status
            CHECK (status IN ('PENDIENTE', 'EN_PROCESO', 'COMPLETADA'));
        """)

        cursor.execute("""
            ALTER TABLE lista_largos_cortes
            ADD COLUMN IF NOT EXISTS hora_inicio_corte TIMESTAMP NULL;
        """)

        cursor.execute("""
            ALTER TABLE lista_largos_cortes
            ADD COLUMN IF NOT EXISTS hora_fin_corte TIMESTAMP NULL;
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lista_largos_sobrantes (
                orden_id VARCHAR(100) NOT NULL,
                tipo_orden VARCHAR(10) NOT NULL,
                material VARCHAR(120) NOT NULL,
                barra_index INTEGER NOT NULL,
                sobrante_real NUMERIC(12,4) NOT NULL,
                fecha_update TIMESTAMP NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_ll_sobrantes_tipo CHECK (tipo_orden IN ('WO', 'SWO')),
                PRIMARY KEY (orden_id, tipo_orden, material, barra_index)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lista_largos_remanentes (
                rem_uid SERIAL PRIMARY KEY,
                rem_id VARCHAR(120) NOT NULL UNIQUE,
                tipo VARCHAR(40) NOT NULL,
                seccion VARCHAR(20) NOT NULL,
                largo_real NUMERIC(12,4) NOT NULL,
                largo_id INTEGER NOT NULL,
                material_grade VARCHAR(40) NOT NULL DEFAULT 'A36',
                material VARCHAR(120) NOT NULL,
                fuente_orden_id VARCHAR(100),
                fuente_tipo_orden VARCHAR(10),
                barra_index INTEGER,
                status VARCHAR(20) NOT NULL DEFAULT 'DISPONIBLE',
                reservado_para_orden_id VARCHAR(100),
                reservado_para_tipo_orden VARCHAR(10),
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_ll_rem_fuente_tipo CHECK (
                    fuente_tipo_orden IS NULL OR fuente_tipo_orden IN ('WO', 'SWO')
                ),
                CONSTRAINT chk_ll_rem_reserva_tipo CHECK (
                    reservado_para_tipo_orden IS NULL OR reservado_para_tipo_orden IN ('WO', 'SWO')
                ),
                CONSTRAINT chk_ll_rem_status CHECK (
                    status IN ('DISPONIBLE', 'RESERVADO', 'USADO', 'DESCARTADO')
                )
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lista_largos_turnos (
                id SERIAL PRIMARY KEY,
                sesion_id INTEGER NOT NULL,
                orden_id VARCHAR(100) NOT NULL,
                tipo_orden VARCHAR(10) NOT NULL,
                operador VARCHAR(150) NOT NULL,
                bahia VARCHAR(100) NOT NULL,
                estado VARCHAR(20) NOT NULL DEFAULT 'activo',
                hora_inicio_turno TIMESTAMP NOT NULL DEFAULT NOW(),
                hora_fin_turno TIMESTAMP NULL,
                piezas_completadas_inicio INTEGER NOT NULL DEFAULT 0,
                piezas_completadas_fin INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_ll_turnos_tipo CHECK (tipo_orden IN ('WO', 'SWO')),
                CONSTRAINT chk_ll_turnos_estado CHECK (estado IN ('activo', 'pausado', 'finalizado'))
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lista_largos_eventos_pieza (
                id SERIAL PRIMARY KEY,
                turno_id INTEGER NOT NULL,
                sesion_id INTEGER NOT NULL,
                orden_id VARCHAR(100) NOT NULL,
                tipo_orden VARCHAR(10) NOT NULL,
                material VARCHAR(120) NOT NULL,
                barra_index INTEGER NOT NULL,
                pieza_index INTEGER NOT NULL,
                pieza_uid VARCHAR(200) NOT NULL,
                accion VARCHAR(20) NOT NULL,
                operador VARCHAR(150) NOT NULL,
                bahia VARCHAR(100) NOT NULL,
                fecha_evento TIMESTAMP NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_ll_evt_pieza_tipo CHECK (tipo_orden IN ('WO', 'SWO')),
                CONSTRAINT chk_ll_evt_pieza_accion CHECK (accion IN ('COMPLETADA', 'REABIERTA'))
            );
        """)

        cursor.execute("""
            ALTER TABLE lista_largos_eventos_pieza
            DROP CONSTRAINT IF EXISTS chk_ll_evt_pieza_accion;
        """)

        cursor.execute("""
            ALTER TABLE lista_largos_eventos_pieza
            ADD CONSTRAINT chk_ll_evt_pieza_accion
            CHECK (accion IN ('INICIO_CORTE', 'COMPLETADA', 'REABIERTA'));
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lista_largos_eventos_sobrante (
                id SERIAL PRIMARY KEY,
                turno_id INTEGER NOT NULL,
                sesion_id INTEGER NOT NULL,
                orden_id VARCHAR(100) NOT NULL,
                tipo_orden VARCHAR(10) NOT NULL,
                material VARCHAR(120) NOT NULL,
                barra_index INTEGER NOT NULL,
                accion VARCHAR(20) NOT NULL,
                sobrante_real NUMERIC(12,4) NULL,
                operador VARCHAR(150) NOT NULL,
                bahia VARCHAR(100) NOT NULL,
                fecha_evento TIMESTAMP NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_ll_evt_sobrante_tipo CHECK (tipo_orden IN ('WO', 'SWO')),
                CONSTRAINT chk_ll_evt_sobrante_accion CHECK (accion IN ('CAPTURADO', 'ELIMINADO'))
            );
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ll_turnos_orden_tipo
            ON lista_largos_turnos (orden_id, tipo_orden, hora_inicio_turno DESC);
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ll_turnos_sesion
            ON lista_largos_turnos (sesion_id, hora_inicio_turno DESC);
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ll_evt_pieza_orden_tipo
            ON lista_largos_eventos_pieza (orden_id, tipo_orden, fecha_evento DESC);
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ll_evt_sobrante_orden_tipo
            ON lista_largos_eventos_sobrante (orden_id, tipo_orden, fecha_evento DESC);
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ll_planes_orden_tipo
            ON lista_largos_planes (orden_id, tipo_orden);
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ll_cortes_orden_tipo
            ON lista_largos_cortes (orden_id, tipo_orden);
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ll_sobrantes_orden_tipo
            ON lista_largos_sobrantes (orden_id, tipo_orden);
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ll_remanentes_material_status
            ON lista_largos_remanentes (material, status, largo_real DESC);
        """)

        conexion.commit()
    except Exception as e:
        if conexion:
            conexion.rollback()
        print(f"⚠️ No se pudieron asegurar tablas operativas de lista de largos: {e}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


@app.on_event("startup")
def startup_event_lista_largos_operativas():
    asegurar_tablas_lista_largos_operativas()


def _ll_parse_plan_json(raw_plan):
    if isinstance(raw_plan, dict):
        return raw_plan
    if isinstance(raw_plan, str):
        try:
            parsed = json.loads(raw_plan)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _ll_hash_payload(payload: dict) -> str:
    try:
        serial = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha1(serial.encode("utf-8", errors="ignore")).hexdigest()
    except Exception:
        return ""


def _ll_material_key(value: str) -> str:
    return str(value or "").strip() or "SIN_CLASIFICACION"


def _ll_rows_para_orden(cursor, orden_id: str, tipo_orden: str) -> tuple[list[dict], list[str]]:
    """
    Filas de lista_largos_job expandidas por WO/SWO.
    No llama a construir_lista_largos_* (evita recursión con _ll_obtener_o_generar_plan).
    """
    orden = str(orden_id or "").strip()
    tipo = str(tipo_orden or "").strip().upper()
    if tipo == "SWO":
        jobs = _obtener_jobs_de_swo(cursor, orden)
    else:
        jobs = _obtener_jobs_de_wo(cursor, orden)

    rows: list[dict] = []
    jobs_unicos: list[str] = []
    vistos: set[tuple[str, str]] = set()

    for item in jobs:
        job = str(item.get("job") or "").strip()
        work_order = str(item.get("work_order") or "").strip()
        if not job or not work_order:
            continue
        if job not in jobs_unicos:
            jobs_unicos.append(job)
        clave = (job, work_order)
        if clave in vistos:
            continue
        vistos.add(clave)
        rows.extend(_expandir_lista_para_wo(cursor, job, work_order))

    return rows, jobs_unicos


def _asegurar_material_requerido_orden(
    cursor,
    orden_id: str,
    tipo_orden: str,
) -> tuple[bool, str]:
    """Genera material_requerido_ldg desde lista + plan (sin abrir estación)."""
    orden = str(orden_id or "").strip()
    tipo = str(tipo_orden or "").strip().upper()
    if tipo not in ("WO", "SWO"):
        return False, "tipo_orden debe ser WO o SWO"

    asegurar_tabla_material_requerido_ldg()
    rows, _ = _ll_rows_para_orden(cursor, orden, tipo)
    if not rows:
        return (
            False,
            f"No hay lista de largos importada para {tipo} {orden}.",
        )

    plan_json, _ = _ll_obtener_o_generar_plan(
        cursor, orden, tipo, reservar=False
    )
    return reconstruir_pedido_desde_plan(cursor, orden, tipo, plan_json)


def _propagar_material_requerido_tras_jobs(cursor, jobs: list[dict]) -> list[dict]:
    wos: set[str] = set()
    swos: set[str] = set()
    logs: list[dict] = []

    for item in jobs or []:
        wo = str(item.get("work_order") or "").strip()
        swo = str(item.get("super_work_order") or "").strip()
        if wo:
            wos.add(wo)
        if swo:
            swos.add(swo)

    for wo in sorted(wos):
        try:
            ok, msg = _asegurar_material_requerido_orden(cursor, wo, "WO")
            logs.append(
                {"orden_id": wo, "tipo_orden": "WO", "ok": ok, "mensaje": msg}
            )
        except Exception as e:
            logs.append(
                {
                    "orden_id": wo,
                    "tipo_orden": "WO",
                    "ok": False,
                    "mensaje": str(e),
                }
            )

    for swo in sorted(swos):
        try:
            ok, msg = _asegurar_material_requerido_orden(cursor, swo, "SWO")
            logs.append(
                {"orden_id": swo, "tipo_orden": "SWO", "ok": ok, "mensaje": msg}
            )
        except Exception as e:
            logs.append(
                {
                    "orden_id": swo,
                    "tipo_orden": "SWO",
                    "ok": False,
                    "mensaje": str(e),
                }
            )

    return logs


def _propagar_material_requerido_por_job(
    db_config: dict,
    job: str,
    solo_work_order: str | None = None,
) -> list[dict]:
    job_limpio = str(job or "").strip()
    if not job_limpio:
        return []

    wo_filtro = str(solo_work_order or "").strip()

    conexion = None
    cursor = None
    try:
        if db_config:
            conexion = psycopg2.connect(**db_config)
            tz_cur = conexion.cursor()
            tz_cur.execute(f"SET TIME ZONE '{DB_TIMEZONE}';")
            tz_cur.close()
        else:
            conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            SELECT DISTINCT TRIM(work_order) AS work_order,
                   NULLIF(TRIM(super_work_order), '') AS super_work_order
            FROM reporte_cortes
            WHERE TRIM(job) = %s
              AND work_order IS NOT NULL
            """,
            (job_limpio,),
        )
        jobs: list[dict] = []
        for row in cursor.fetchall() or []:
            wo = str(row.get("work_order") or "").strip()
            if not wo:
                continue
            if wo_filtro and wo != wo_filtro:
                continue
            jobs.append(
                {
                    "job": job_limpio,
                    "work_order": wo,
                    "super_work_order": str(row.get("super_work_order") or "").strip(),
                }
            )
        if not jobs:
            return []
        logs = _propagar_material_requerido_tras_jobs(cursor, jobs)
        conexion.commit()
        return logs
    except Exception:
        if conexion:
            conexion.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


def _ll_source_payload(cursor, orden_id: str, tipo_orden: str) -> dict:
    orden = str(orden_id or "").strip()
    tipo = str(tipo_orden or "").strip().upper()
    rows, jobs_unicos = _ll_rows_para_orden(cursor, orden, tipo)

    if tipo == "SWO":
        return {
            "tipo": "swo",
            "identificador": orden,
            "super_work_order": orden,
            "jobs": jobs_unicos,
            "rows": rows,
        }

    return {
        "tipo": "wo",
        "identificador": orden,
        "work_order": orden,
        "jobs": jobs_unicos,
        "factor_wo": _extraer_factor_wo(orden),
        "rows": rows,
    }


def _ll_expandir_rows_a_piezas(rows: list[dict]) -> list[dict]:
    piezas = []

    for row in rows or []:
        cantidad = safe_int(row.get("cantidad"), 0)
        largo = safe_float(row.get("largo_in", row.get("largo", 0.0)), 0.0)
        nombre = str(row.get("nombre") or row.get("componente") or "").strip() or "SIN_NOMBRE"
        material = _ll_material_key(row.get("clasificacion") or row.get("material"))
        origen_id = str(row.get("work_order") or row.get("origen_wo") or "").strip()
        origen_lbl = origen_id or "-"
        job = str(row.get("job") or "").strip()

        if cantidad <= 0 or largo <= 0:
            continue

        for _ in range(cantidad):
            piezas.append({
                "nombre": nombre,
                "material": material,
                "largo": float(largo),
                "origen_id": origen_id,
                "origen_lbl": origen_lbl,
                "job": job,
            })

    return piezas


def _ll_listar_remanentes_para_material(cursor, material: str, orden_id: str, tipo_orden: str) -> list[dict]:
    cursor.execute("""
        SELECT *
        FROM lista_largos_remanentes
        WHERE material = %s
          AND (
                status = 'DISPONIBLE'
                OR (
                    status = 'RESERVADO'
                    AND reservado_para_orden_id = %s
                    AND reservado_para_tipo_orden = %s
                )
          )
        ORDER BY largo_real DESC, created_at ASC;
    """, (material, orden_id, tipo_orden))
    return cursor.fetchall() or []


def _ll_reservar_remanentes(cursor, rem_uids: list[int], orden_id: str, tipo_orden: str):
    if not rem_uids:
        return

    cursor.execute("""
        UPDATE lista_largos_remanentes
        SET status = 'RESERVADO',
            reservado_para_orden_id = %s,
            reservado_para_tipo_orden = %s,
            updated_at = NOW()
        WHERE rem_uid = ANY(%s);
    """, (orden_id, tipo_orden, rem_uids))

def _ll_largo_requerido_pieza(pieza: dict) -> float:
    largo_p = safe_float(pieza.get("largo"), 0.0)
    if largo_p <= 0:
        return 0.0
    return largo_p + LISTA_LARGOS_KERF

def _ll_largo_util_bruto(largo_stock: float) -> float:
    largo_stock = safe_float(largo_stock, 0.0)
    if largo_stock <= 0:
        return 0.0
    return max(0.0, largo_stock - (LISTA_LARGOS_RECORTE_EXTREMO * 2))

def _ll_crear_barra_desde_remanente(rem_row: dict) -> Optional[dict]:
    largo_real = safe_float(rem_row.get("largo_real"), 0.0)
    util = _ll_largo_util_bruto(largo_real)
    if util <= 0:
        return None

    return {
        "source": "REMANENTE",
        "largo_stock": float(largo_real),
        "remanente_calc": float(util),
        "remanente_real": None,
        "remanente_show": float(util),
        "rem_uid": safe_int(rem_row.get("rem_uid"), 0),
        "rem_id": rem_row.get("rem_id"),
        "cortes": [],
    }

def _ll_crear_barra_stock(largo_stock: float) -> dict:
    util = _ll_largo_util_bruto(largo_stock)
    return {
        "source": "STOCK",
        "largo_stock": float(largo_stock),
        "remanente_calc": float(util),
        "remanente_real": None,
        "remanente_show": float(util),
        "rem_uid": None,
        "rem_id": None,
        "cortes": [],
    }

def _ll_aplicar_piezas_a_barra(barra: dict, piezas_asignadas: list[dict]) -> dict:
    rem_calc = safe_float(barra.get("remanente_calc"), 0.0)

    for pieza in piezas_asignadas:
        largo_p = safe_float(pieza.get("largo"), 0.0)
        requerido = _ll_largo_requerido_pieza(pieza)
        barra["cortes"].append({
            "nombre": pieza.get("nombre"),
            "largo": largo_p,
            "origen_id": pieza.get("origen_id"),
            "origen_lbl": pieza.get("origen_lbl"),
            "job": pieza.get("job"),
        })
        rem_calc -= requerido

    barra["remanente_calc"] = max(0.0, rem_calc)
    barra["remanente_show"] = barra["remanente_calc"]
    return barra

def _ll_mejor_subset_para_capacidad(
    piezas: list[dict],
    capacidad: float,
    max_candidatas: int = 18
) -> list[int]:
    """
    Regresa índices relativos dentro de 'piezas' del mejor subconjunto que cabe
    en 'capacidad'. Busca maximizar largo consumido y, en empate, cantidad de piezas.

    max_candidatas limita el tamaño del problema para mantener velocidad.
    """
    if capacidad <= 0 or not piezas:
        return []

    candidatas = []
    for idx, pieza in enumerate(piezas):
        requerido = _ll_largo_requerido_pieza(pieza)
        if requerido > 0 and requerido <= capacidad:
            candidatas.append((idx, pieza, requerido))

    if not candidatas:
        return []

    candidatas.sort(key=lambda x: x[2], reverse=True)
    candidatas = candidatas[:max_candidatas]

    mejores_indices: list[int] = []
    mejor_usado = 0.0
    mejor_count = 0

    sufijos = [0.0] * (len(candidatas) + 1)
    for i in range(len(candidatas) - 1, -1, -1):
        sufijos[i] = sufijos[i + 1] + candidatas[i][2]

    def backtrack(pos: int, usados: list[int], usado_total: float):
        nonlocal mejores_indices, mejor_usado, mejor_count

        if usado_total > capacidad:
            return

        if usado_total > mejor_usado + 1e-9:
            mejor_usado = usado_total
            mejor_count = len(usados)
            mejores_indices = list(usados)
        elif abs(usado_total - mejor_usado) <= 1e-9 and len(usados) > mejor_count:
            mejor_count = len(usados)
            mejores_indices = list(usados)

        if pos >= len(candidatas):
            return

        # poda optimista
        if usado_total + sufijos[pos] < mejor_usado - 1e-9:
            return

        idx_real, _, requerido = candidatas[pos]

        # incluir
        if usado_total + requerido <= capacidad + 1e-9:
            usados.append(idx_real)
            backtrack(pos + 1, usados, usado_total + requerido)
            usados.pop()

        # excluir
        backtrack(pos + 1, usados, usado_total)

    backtrack(0, [], 0.0)
    return sorted(mejores_indices)

def _ll_consumir_remanentes_material(
    rems_rows: list[dict],
    piezas_material: list[dict]
) -> tuple[list[dict], list[dict], set[int]]:
    """
    Usa remanentes primero. Devuelve:
    - barras creadas desde remanentes con cortes
    - piezas que no lograron asignarse
    - rem_uids utilizados
    """
    pendientes = list(piezas_material)
    barras = []
    remanentes_usados = set()

    rems_ordenados = sorted(
        rems_rows or [],
        key=lambda r: safe_float(r.get("largo_real"), 0.0),
        reverse=True
    )

    for rem in rems_ordenados:
        barra = _ll_crear_barra_desde_remanente(rem)
        if not barra:
            continue

        capacidad = safe_float(barra.get("remanente_calc"), 0.0)
        idxs = _ll_mejor_subset_para_capacidad(pendientes, capacidad)

        if not idxs:
            continue

        piezas_asignadas = [pendientes[i] for i in idxs]
        barra = _ll_aplicar_piezas_a_barra(barra, piezas_asignadas)
        barras.append(barra)

        rem_uid = safe_int(barra.get("rem_uid"), 0)
        if rem_uid > 0:
            remanentes_usados.add(rem_uid)

        idxs_set = set(idxs)
        pendientes = [pieza for i, pieza in enumerate(pendientes) if i not in idxs_set]

        if not pendientes:
            break

    return barras, pendientes, remanentes_usados

def _ll_resolver_pendientes_con_stock(piezas_pendientes: list[dict]) -> list[dict]:
    barras = []
    pendientes = list(sorted(
        piezas_pendientes,
        key=lambda p: safe_float(p.get("largo"), 0.0),
        reverse=True
    ))

    def try_place_on_existing(pieza: dict) -> bool:
        requerido = _ll_largo_requerido_pieza(pieza)
        mejor_idx = -1
        menor_desperdicio = float("inf")

        for i, barra in enumerate(barras):
            rem_calc = safe_float(barra.get("remanente_calc"), 0.0)
            if rem_calc >= requerido:
                desperdicio = rem_calc - requerido
                if desperdicio < menor_desperdicio:
                    menor_desperdicio = desperdicio
                    mejor_idx = i

        if mejor_idx == -1:
            return False

        _ll_aplicar_piezas_a_barra(barras[mejor_idx], [pieza])
        return True

    def subset_stats(piezas_base: list[dict], idxs: list[int]) -> tuple[float, int]:
        usado = sum(_ll_largo_requerido_pieza(piezas_base[i]) for i in idxs)
        return usado, len(idxs)

    while pendientes:
        # Primero intenta meter piezas en barras de stock ya abiertas
        progreso = True
        while progreso and pendientes:
            progreso = False
            nuevas_pendientes = []

            for pieza in pendientes:
                if try_place_on_existing(pieza):
                    progreso = True
                else:
                    nuevas_pendientes.append(pieza)

            pendientes = nuevas_pendientes

        if not pendientes:
            break

        util_240 = _ll_largo_util_bruto(LISTA_LARGOS_STOCK_MINIMO)
        util_480 = _ll_largo_util_bruto(LISTA_LARGOS_STOCK_MAXIMO)

        idxs_240 = _ll_mejor_subset_para_capacidad(pendientes, util_240)
        idxs_480 = _ll_mejor_subset_para_capacidad(pendientes, util_480)

        if not idxs_240 and not idxs_480:
            break

        usado_240, count_240 = subset_stats(pendientes, idxs_240) if idxs_240 else (0.0, 0)
        usado_480, count_480 = subset_stats(pendientes, idxs_480) if idxs_480 else (0.0, 0)

        usar_480 = False
        if usado_480 > usado_240 + 1e-9:
            usar_480 = True
        elif abs(usado_480 - usado_240) <= 1e-9 and count_480 > count_240:
            usar_480 = True

        if usar_480 and idxs_480:
            idxs_elegidos = idxs_480
            barra = _ll_crear_barra_stock(LISTA_LARGOS_STOCK_MAXIMO)
        else:
            if idxs_240:
                idxs_elegidos = idxs_240
                barra = _ll_crear_barra_stock(LISTA_LARGOS_STOCK_MINIMO)
            else:
                idxs_elegidos = idxs_480
                barra = _ll_crear_barra_stock(LISTA_LARGOS_STOCK_MAXIMO)

        piezas_asignadas = [pendientes[i] for i in idxs_elegidos]
        _ll_aplicar_piezas_a_barra(barra, piezas_asignadas)
        barras.append(barra)

        idxs_set = set(idxs_elegidos)
        pendientes = [pieza for i, pieza in enumerate(pendientes) if i not in idxs_set]

    return barras

def _ll_generar_plan_desde_payload(
    cursor,
    orden_id: str,
    tipo_orden: str,
    payload: dict,
    *,
    usar_remanentes: bool = True,
) -> tuple[dict, list[int]]:
    rows = (payload or {}).get("rows") or []
    piezas = _ll_expandir_rows_a_piezas(rows)

    if not piezas:
        return {
            "orden_id": orden_id,
            "tipo_orden": tipo_orden,
            "data": {},
            "total_piezas": 0,
            "total_barras": 0,
        }, []

    materiales: dict[str, list[dict]] = {}
    for pieza in piezas:
        material = pieza["material"]
        materiales.setdefault(material, []).append(pieza)

    data = {}
    remanentes_usados = set()

    for material in sorted(materiales.keys()):
        piezas_material = list(materiales[material])
        piezas_material.sort(key=lambda p: safe_float(p.get("largo"), 0.0), reverse=True)

        barras_rem: list[dict] = []
        pendientes = piezas_material
        rems_usados_mat: set[int] = set()
        if usar_remanentes:
            rems_rows = _ll_listar_remanentes_para_material(cursor, material, orden_id, tipo_orden)
            barras_rem, pendientes, rems_usados_mat = _ll_consumir_remanentes_material(
                rems_rows,
                piezas_material,
            )

        barras_stock = _ll_resolver_pendientes_con_stock(pendientes)

        barras_finales = barras_rem + barras_stock

        # Quitar barras sin piezas (remanentes vacíos o stock huérfano).
        barras_finales = [
            b
            for b in barras_finales
            if len(b.get("cortes") or []) > 0
        ]

        data[material] = barras_finales
        remanentes_usados.update(rems_usados_mat)

    total_barras = sum(len(v) for v in data.values())
    total_piezas = sum(
        len((b.get("cortes") or []))
        for barras in data.values()
        for b in barras
    )

    return {
        "orden_id": orden_id,
        "tipo_orden": tipo_orden,
        "data": data,
        "total_piezas": int(total_piezas),
        "total_barras": int(total_barras),
    }, sorted(remanentes_usados)


def _ll_cargar_plan_row(cursor, orden_id: str, tipo_orden: str):
    cursor.execute("""
        SELECT *
        FROM lista_largos_planes
        WHERE orden_id = %s AND tipo_orden = %s
        LIMIT 1;
    """, (orden_id, tipo_orden))
    return cursor.fetchone()


def _ll_guardar_plan(cursor, orden_id: str, tipo_orden: str, plan_hash: str, plan_json: dict):
    total_piezas = safe_int(plan_json.get("total_piezas"), 0)
    total_barras = safe_int(plan_json.get("total_barras"), 0)

    cursor.execute("""
        INSERT INTO lista_largos_planes (
            orden_id,
            tipo_orden,
            plan_hash,
            plan_json,
            total_piezas,
            total_barras,
            estado,
            created_at,
            updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            'activo',
            NOW(),
            NOW()
        )
        ON CONFLICT (orden_id, tipo_orden)
        DO UPDATE SET
            plan_hash = EXCLUDED.plan_hash,
            plan_json = EXCLUDED.plan_json,
            total_piezas = EXCLUDED.total_piezas,
            total_barras = EXCLUDED.total_barras,
            updated_at = NOW()
        RETURNING *;
    """, (orden_id, tipo_orden, plan_hash, Json(plan_json), total_piezas, total_barras))

    return cursor.fetchone()

def _ll_hay_sesion_registrada(cursor, orden_id: str, tipo_orden: str) -> bool:
    cursor.execute("""
        SELECT EXISTS(
            SELECT 1
            FROM lista_largos_sesiones
            WHERE TRIM(orden_id) = %s
              AND tipo_orden = %s
        ) AS existe;
    """, (orden_id, tipo_orden))

    row = cursor.fetchone() or {}
    return bool(row.get("existe"))


def _ll_hay_operacion_registrada(cursor, orden_id: str, tipo_orden: str) -> bool:
    cursor.execute("""
        SELECT
            EXISTS(
                SELECT 1
                FROM lista_largos_cortes
                WHERE orden_id = %s
                  AND tipo_orden = %s
            ) AS hay_cortes,
            EXISTS(
                SELECT 1
                FROM lista_largos_sobrantes
                WHERE orden_id = %s
                  AND tipo_orden = %s
            ) AS hay_sobrantes;
    """, (orden_id, tipo_orden, orden_id, tipo_orden))

    row = cursor.fetchone() or {}
    return bool(row.get("hay_cortes")) or bool(row.get("hay_sobrantes"))


def _ll_plan_puede_regenerarse(cursor, orden_id: str, tipo_orden: str, plan_row: Optional[dict] = None) -> bool:
    if plan_row and _ll_plan_ya_finalizado(plan_row):
        return False

    # Si ya hubo sesión, el plan ya se considera arrancado.
    if _ll_hay_sesion_registrada(cursor, orden_id, tipo_orden):
        return False

    # Si ya hubo cortes o sobrantes, tampoco debe cambiar.
    if _ll_hay_operacion_registrada(cursor, orden_id, tipo_orden):
        return False

    return True


def _ll_liberar_reservas_de_orden(cursor, orden_id: str, tipo_orden: str):
    cursor.execute("""
        UPDATE lista_largos_remanentes
        SET status = 'DISPONIBLE',
            reservado_para_orden_id = NULL,
            reservado_para_tipo_orden = NULL,
            updated_at = NOW()
        WHERE reservado_para_orden_id = %s
          AND reservado_para_tipo_orden = %s
          AND status = 'RESERVADO';
    """, (orden_id, tipo_orden))

def _ll_obtener_o_generar_plan(
    cursor,
    orden_id: str,
    tipo_orden: str,
    reservar: bool = False
) -> tuple[dict, dict]:
    existente = _ll_cargar_plan_row(cursor, orden_id, tipo_orden)

    # Si ya existe un plan y la orden ya arrancó operativamente,
    # se respeta tal cual.
    if existente and not _ll_plan_puede_regenerarse(cursor, orden_id, tipo_orden, existente):
        return _ll_parse_plan_json(existente.get("plan_json")), existente

    # Si la orden sigue virgen, liberamos cualquier reserva vieja
    # que haya quedado colgada por pruebas previas o lógica anterior.
    if _ll_plan_puede_regenerarse(cursor, orden_id, tipo_orden, existente):
        _ll_liberar_reservas_de_orden(cursor, orden_id, tipo_orden)

    payload = _ll_source_payload(cursor, orden_id, tipo_orden)
    plan_hash = _ll_hash_payload(payload)
    plan_json, remanentes_reservar = _ll_generar_plan_desde_payload(cursor, orden_id, tipo_orden, payload)

    # OJO:
    # Solo se reservan remanentes cuando realmente se va a congelar el plan.
    if reservar and remanentes_reservar:
        _ll_reservar_remanentes(cursor, remanentes_reservar, orden_id, tipo_orden)

    saved_row = _ll_guardar_plan(cursor, orden_id, tipo_orden, plan_hash, plan_json)
    return plan_json, saved_row


def _ll_cortes_completados_set(cursor, orden_id: str, tipo_orden: str) -> set[tuple[str, int, int]]:
    cursor.execute("""
        SELECT material, barra_index, pieza_index
        FROM lista_largos_cortes
        WHERE orden_id = %s
          AND tipo_orden = %s
          AND status = 'COMPLETADA';
    """, (orden_id, tipo_orden))

    out = set()
    for row in cursor.fetchall() or []:
        out.add((
            str(row.get("material") or ""),
            safe_int(row.get("barra_index"), 0),
            safe_int(row.get("pieza_index"), 0),
        ))
    return out

def _ll_cortes_map(cursor, orden_id: str, tipo_orden: str) -> dict[tuple[str, int, int], dict]:
    cursor.execute("""
        SELECT material, barra_index, pieza_index, status, hora_inicio_corte, hora_fin_corte
        FROM lista_largos_cortes
        WHERE orden_id = %s
          AND tipo_orden = %s;
    """, (orden_id, tipo_orden))

    out = {}
    for row in cursor.fetchall() or []:
        key = (
            str(row.get("material") or ""),
            safe_int(row.get("barra_index"), 0),
            safe_int(row.get("pieza_index"), 0),
        )
        out[key] = {
            "status": str(row.get("status") or "PENDIENTE").upper(),
            "hora_inicio_corte": row.get("hora_inicio_corte"),
            "hora_fin_corte": row.get("hora_fin_corte"),
        }
    return out

def _ll_sobrantes_map(cursor, orden_id: str, tipo_orden: str) -> dict[tuple[str, int], float]:
    cursor.execute("""
        SELECT material, barra_index, sobrante_real
        FROM lista_largos_sobrantes
        WHERE orden_id = %s
          AND tipo_orden = %s;
    """, (orden_id, tipo_orden))

    out = {}
    for row in cursor.fetchall() or []:
        key = (
            str(row.get("material") or ""),
            safe_int(row.get("barra_index"), 0),
        )
        out[key] = safe_float(row.get("sobrante_real"), 0.0)

    return out


def _ll_annotar_plan(cursor, orden_id: str, tipo_orden: str, plan_json: dict) -> dict:
    plan = json.loads(json.dumps(plan_json or {}, ensure_ascii=False, default=str))

    cortes_map = _ll_cortes_map(cursor, orden_id, tipo_orden)
    sobrantes_guardados = _ll_sobrantes_map(cursor, orden_id, tipo_orden)

    total_piezas = 0
    piezas_completadas = 0
    faltan_sobrantes = []
    materiales_view = []

    data = plan.get("data") or {}

    for material, barras in data.items():
        barras = barras or []

        for barra_index, barra in enumerate(barras, start=1):
            barra["barra_index"] = barra_index

            rem_calc = safe_float(barra.get("remanente_calc", barra.get("remanente_show", 0.0)), 0.0)
            barra["remanente_calc"] = rem_calc

            rem_real = sobrantes_guardados.get((str(material), int(barra_index)))
            barra["remanente_real"] = rem_real
            barra["remanente_show"] = rem_real if rem_real is not None else rem_calc

            requiere_real = rem_calc > LISTA_LARGOS_REM_MINIMO
            barra["requiere_sobrante_real"] = requiere_real

            if requiere_real and rem_real is None:
                faltan_sobrantes.append({
                    "material": str(material),
                    "barra_index": int(barra_index),
                    "source": str(barra.get("source") or "STOCK"),
                    "remanente_calc": float(rem_calc),
                    "rem_id": barra.get("rem_id"),
                    "rem_uid": barra.get("rem_uid"),
                })

            cortes = barra.get("cortes") or []
            for pieza_index, pieza in enumerate(cortes, start=1):
                total_piezas += 1

                corte_info = cortes_map.get((str(material), int(barra_index), int(pieza_index)), {})
                status_corte = str(corte_info.get("status") or "PENDIENTE").upper()

                hora_inicio = corte_info.get("hora_inicio_corte")
                hora_fin = corte_info.get("hora_fin_corte")

                if isinstance(hora_inicio, datetime):
                    hora_inicio_iso = hora_inicio.isoformat()
                else:
                    hora_inicio_iso = None

                if isinstance(hora_fin, datetime):
                    hora_fin_iso = hora_fin.isoformat()
                else:
                    hora_fin_iso = None

                segundos_corte = 0
                if isinstance(hora_inicio, datetime) and isinstance(hora_fin, datetime):
                    segundos_corte = max(0, int((hora_fin - hora_inicio).total_seconds()))

                done = status_corte == "COMPLETADA"
                if done:
                    piezas_completadas += 1

                pieza["pieza_index"] = pieza_index
                pieza["pieza_uid"] = f"{orden_id}|{tipo_orden}|{material}|{barra_index}|{pieza_index}"
                pieza["done"] = done
                pieza["status_corte"] = status_corte
                pieza["en_proceso"] = status_corte == "EN_PROCESO"
                pieza["hora_inicio_corte"] = hora_inicio_iso
                pieza["hora_fin_corte"] = hora_fin_iso
                pieza["segundos_corte"] = segundos_corte

        materiales_view.append({
            "material": str(material),
            "barras": barras,
        })

    plan["orden_id"] = orden_id
    plan["tipo_orden"] = tipo_orden
    plan["total_piezas"] = total_piezas
    plan["total_barras"] = sum(len((barras or [])) for barras in data.values())
    plan["materiales"] = materiales_view
    plan["resumen"] = {
        "total_piezas": int(total_piezas),
        "piezas_completadas": int(piezas_completadas),
        "piezas_pendientes": int(max(0, total_piezas - piezas_completadas)),
        "total_barras": int(plan["total_barras"]),
        "faltan_sobrantes_count": len(faltan_sobrantes),
    }
    plan["faltan_sobrantes"] = faltan_sobrantes

    return plan


def _ll_sync_sesion_activa(cursor, orden_id: str, tipo_orden: str, total_piezas: int, piezas_completadas: int):
    cursor.execute("""
        UPDATE lista_largos_sesiones
        SET piezas_totales = %s,
            piezas_completadas = %s,
            hora_ultimo_movimiento = NOW(),
            updated_at = NOW()
        WHERE orden_id = %s
          AND tipo_orden = %s
          AND estado = 'activa';
    """, (total_piezas, piezas_completadas, orden_id, tipo_orden))


def _ll_buscar_barra(plan: dict, material: str, barra_index: int) -> Optional[dict]:
    data = (plan or {}).get("data") or {}
    barras = data.get(material) or []
    if barra_index < 1 or barra_index > len(barras):
        return None
    return barras[barra_index - 1]


def _ll_buscar_pieza(plan: dict, material: str, barra_index: int, pieza_index: int) -> Optional[dict]:
    barra = _ll_buscar_barra(plan, material, barra_index)
    if not barra:
        return None

    cortes = barra.get("cortes") or []
    if pieza_index < 1 or pieza_index > len(cortes):
        return None

    return cortes[pieza_index - 1]

def _ll_corte_actual(cursor, orden_id: str, tipo_orden: str, material: str, barra_index: int, pieza_index: int):
    cursor.execute("""
        SELECT *
        FROM lista_largos_cortes
        WHERE orden_id = %s
          AND tipo_orden = %s
          AND material = %s
          AND barra_index = %s
          AND pieza_index = %s
        LIMIT 1;
    """, (orden_id, tipo_orden, material, barra_index, pieza_index))
    return cursor.fetchone()

def _ll_pieza_en_proceso(cursor, orden_id: str, tipo_orden: str, excluir: Optional[tuple[str, int, int]] = None):
    where_extra = ""
    params = [orden_id, tipo_orden]

    if excluir:
        where_extra = """
          AND NOT (
                material = %s
            AND barra_index = %s
            AND pieza_index = %s
          )
        """
        params.extend([excluir[0], excluir[1], excluir[2]])

    cursor.execute(f"""
        SELECT *
        FROM lista_largos_cortes
        WHERE orden_id = %s
          AND tipo_orden = %s
          AND status = 'EN_PROCESO'
          {where_extra}
        ORDER BY hora_inicio_corte ASC
        LIMIT 1;
    """, tuple(params))

    return cursor.fetchone()

def _ll_id_base(tipo: str, seccion: str, largo_id: int, grade: str) -> str:
    tipo = (tipo or LISTA_LARGOS_TIPO_REMANENTE_DEFAULT).strip().upper()
    seccion = "".join(ch for ch in (seccion or "1") if ch.isdigit()) or "1"
    grade = "".join(ch for ch in (grade or LISTA_LARGOS_GRADE_DEFAULT) if ch.isalnum()).upper()
    return f"{tipo}{int(seccion)}{int(largo_id)}{grade}"


def _ll_generar_rem_id(cursor, tipo: str, seccion: str, largo_id: int, grade: str) -> str:
    base = _ll_id_base(tipo, seccion, largo_id, grade)

    cursor.execute("""
        SELECT rem_id
        FROM lista_largos_remanentes
        WHERE rem_id LIKE %s
        ORDER BY rem_id ASC;
    """, (base + "%",))

    existing = [str(r.get("rem_id") or "") for r in cursor.fetchall() or []]

    if base not in existing:
        return base

    mx = 1
    for rid in existing:
        if rid.startswith(base + "-"):
            try:
                n = int(rid.split("-")[-1])
                mx = max(mx, n)
            except Exception:
                pass

    return f"{base}-{mx + 1:02d}"


def _ll_material_to_seccion(material: str) -> str:
    return LISTA_LARGOS_SECCION_MAP.get(str(material or "").strip().lower(), "1")


def _ll_plan_ya_finalizado(plan_row: dict) -> bool:
    return str(plan_row.get("estado") or "").strip().lower() == "finalizado"

def _ll_validar_sesion_operativa(
    cursor,
    orden_id: str,
    tipo_orden: str,
    operador: Optional[str] = None,
    bahia: Optional[str] = None,
):
    sesion = obtener_sesion_activa(cursor, orden_id, tipo_orden)

    if not sesion:
        raise HTTPException(
            status_code=409,
            detail="No existe una sesión activa para operar esta orden."
        )

    operador_req = (operador or "").strip().lower()
    operador_activo = str(sesion.get("operador") or "").strip().lower()

    if operador_req and operador_req != operador_activo:
        raise HTTPException(
            status_code=409,
            detail=(
                f"La orden {orden_id} está activa con el operador "
                f"{sesion.get('operador')} y no admite cambios desde otro operador."
            )
        )

    bahia_req = (bahia or "").strip().lower()
    bahia_activa = str(sesion.get("bahia") or "").strip().lower()

    if bahia_req and bahia_req != bahia_activa:
        raise HTTPException(
            status_code=409,
            detail=(
                f"La orden {orden_id} está activa en la bahía "
                f"{sesion.get('bahia')} y no admite cambios desde otra bahía."
            )
        )

    return sesion

def _ll_obtener_turno_activo(cursor, orden_id: str, tipo_orden: str):
    cursor.execute("""
        SELECT *
        FROM lista_largos_turnos
        WHERE orden_id = %s
          AND tipo_orden = %s
          AND estado = 'activo'
        ORDER BY hora_inicio_turno DESC
        LIMIT 1;
    """, (orden_id, tipo_orden))
    return cursor.fetchone()

def _ll_crear_turno(cursor, sesion_id: int, orden_id: str, tipo_orden: str, operador: str, bahia: str, piezas_inicio: int):
    cursor.execute("""
        INSERT INTO lista_largos_turnos (
            sesion_id,
            orden_id,
            tipo_orden,
            operador,
            bahia,
            estado,
            hora_inicio_turno,
            hora_fin_turno,
            piezas_completadas_inicio,
            piezas_completadas_fin,
            created_at,
            updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s,
            'activo',
            NOW(),
            NULL,
            %s,
            %s,
            NOW(),
            NOW()
        )
        RETURNING *;
    """, (
        sesion_id,
        orden_id,
        tipo_orden,
        operador,
        bahia,
        piezas_inicio,
        piezas_inicio,
    ))
    return cursor.fetchone()

def _ll_cerrar_turno_activo(cursor, orden_id: str, tipo_orden: str, estado_final: str, piezas_fin: int):
    cursor.execute("""
        UPDATE lista_largos_turnos
        SET estado = %s,
            hora_fin_turno = NOW(),
            piezas_completadas_fin = %s,
            updated_at = NOW()
        WHERE id = (
            SELECT id
            FROM lista_largos_turnos
            WHERE orden_id = %s
              AND tipo_orden = %s
              AND estado = 'activo'
            ORDER BY hora_inicio_turno DESC
            LIMIT 1
        )
        RETURNING *;
    """, (estado_final, piezas_fin, orden_id, tipo_orden))
    return cursor.fetchone()

def _ll_registrar_evento_pieza(
    cursor,
    turno_id: int,
    sesion_id: int,
    orden_id: str,
    tipo_orden: str,
    material: str,
    barra_index: int,
    pieza_index: int,
    pieza_uid: str,
    accion: str,
    operador: str,
    bahia: str,
):
    cursor.execute("""
        INSERT INTO lista_largos_eventos_pieza (
            turno_id, sesion_id, orden_id, tipo_orden,
            material, barra_index, pieza_index, pieza_uid,
            accion, operador, bahia, fecha_evento
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW());
    """, (
        turno_id, sesion_id, orden_id, tipo_orden,
        material, barra_index, pieza_index, pieza_uid,
        accion, operador, bahia
    ))

def _ll_registrar_evento_sobrante(
    cursor,
    turno_id: int,
    sesion_id: int,
    orden_id: str,
    tipo_orden: str,
    material: str,
    barra_index: int,
    accion: str,
    sobrante_real,
    operador: str,
    bahia: str,
):
    cursor.execute("""
        INSERT INTO lista_largos_eventos_sobrante (
            turno_id, sesion_id, orden_id, tipo_orden,
            material, barra_index, accion, sobrante_real,
            operador, bahia, fecha_evento
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW());
    """, (
        turno_id, sesion_id, orden_id, tipo_orden,
        material, barra_index, accion, sobrante_real,
        operador, bahia
    ))

# ========================================================
# RUTAS NUEVAS - PLAN CONGELADO / PIEZAS / SOBRANTES / REMANENTES / FINALIZAR
# ========================================================
@app.get("/api/lista-largos/plan")
def obtener_plan_lista_largos(
    orden_id: str = Query(...),
    tipo_orden: str = Query(...)
):
    asegurar_tablas_lista_largos_operativas()

    conexion = None
    cursor = None
    try:
        orden = normalizar_orden_id(orden_id)
        tipo = normalizar_tipo_orden(tipo_orden)

        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        plan_json, plan_row = _ll_obtener_o_generar_plan(cursor, orden, tipo, reservar=False)
        plan_view = _ll_annotar_plan(cursor, orden, tipo, plan_json)

        if not existe_pedido(cursor, orden, tipo):
            insertar_pedido_desde_plan_cursor(cursor, orden, tipo, plan_view)

        conexion.commit()

        return {
            "estatus": "ok",
            "plan": plan_view,
            "plan_estado": plan_row.get("estado") if plan_row else "activo",
        }
    except HTTPException:
        if conexion:
            conexion.rollback()
        raise
    except Exception as e:
        if conexion:
            conexion.rollback()
        raise HTTPException(status_code=500, detail=f"Error al obtener plan de lista de largos: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


@app.get("/api/lista-largos/material-requerido")
def obtener_material_requerido_ldg(
    orden_id: str = Query(..., description="Identificador de la WO o SWO"),
    tipo_orden: str = Query(..., description="WO o SWO"),
):
    try:
        orden = normalizar_orden_id(orden_id)
        tipo = normalizar_tipo_orden(tipo_orden)
        return consultar_pedido(orden, tipo)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al consultar material requerido LdG: {str(e)}",
        )


@app.post("/api/lista-largos/material-requerido/refrescar-herinox")
def refrescar_material_requerido_herinox(
    orden_id: str = Query(..., description="Identificador de la WO o SWO"),
    tipo_orden: str = Query(..., description="WO o SWO"),
):
    try:
        orden = normalizar_orden_id(orden_id)
        tipo = normalizar_tipo_orden(tipo_orden)
        return refrescar_pedido_herinox(orden, tipo)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al refrescar precios Herinox: {str(e)}",
        )


@app.post("/api/lista-largos/material-requerido/sincronizar")
def sincronizar_material_requerido_ldg_endpoint(payload: MaterialRequeridoLdGSincronizar):
    try:
        orden = normalizar_orden_id(payload.orden_id)
        tipo = normalizar_tipo_orden(payload.tipo_orden)

        if not payload.plan:
            raise HTTPException(
                status_code=422,
                detail="plan es obligatorio para la primera generación.",
            )

        return sincronizar_pedido_desde_plan(orden, tipo, payload.plan)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al sincronizar material requerido LdG: {str(e)}",
        )


@app.post("/api/lista-largos/pieza/iniciar")
def iniciar_pieza_lista_largos(payload: ListaLargosIniciarPiezaPayload):
    asegurar_tablas_lista_largos_operativas()

    conexion = None
    cursor = None
    try:
        orden = normalizar_orden_id(payload.orden_id)
        tipo = normalizar_tipo_orden(payload.tipo_orden)
        material = _ll_material_key(payload.material)
        barra_index = max(1, int(payload.barra_index))
        pieza_index = max(1, int(payload.pieza_index))
        operador = (payload.operador or "").strip()
        bahia = (payload.bahia or "").strip()

        if not operador:
            raise HTTPException(status_code=422, detail="operador es obligatorio.")
        if not bahia:
            raise HTTPException(status_code=422, detail="bahia es obligatoria.")

        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        sesion = _ll_validar_sesion_operativa(
            cursor,
            orden,
            tipo,
            operador=operador,
            bahia=bahia,
        )

        turno = _ll_obtener_turno_activo(cursor, orden, tipo)
        if not turno:
            raise HTTPException(status_code=409, detail="No existe un turno activo para iniciar pieza.")

        plan_json, plan_row = _ll_obtener_o_generar_plan(cursor, orden, tipo)
        if plan_row and _ll_plan_ya_finalizado(plan_row):
            raise HTTPException(status_code=409, detail="El plan ya está finalizado y no admite más cambios.")

        pieza = _ll_buscar_pieza(plan_json, material, barra_index, pieza_index)
        if not pieza:
            raise HTTPException(status_code=404, detail="No se encontró la pieza indicada en el plan.")

        pieza_uid = f"{orden}|{tipo}|{material}|{barra_index}|{pieza_index}"
        corte_actual = _ll_corte_actual(cursor, orden, tipo, material, barra_index, pieza_index)
        status_actual = str((corte_actual or {}).get("status") or "").upper()
        otra_pieza_en_proceso = _ll_pieza_en_proceso(
            cursor,
            orden,
            tipo,
            excluir=(material, barra_index, pieza_index)
        )

        if otra_pieza_en_proceso:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Ya existe otra pieza en proceso. "
                    f"Material: {otra_pieza_en_proceso.get('material')}, "
                    f"Barra: {otra_pieza_en_proceso.get('barra_index')}, "
                    f"Pieza: {otra_pieza_en_proceso.get('pieza_index')}."
                )
            )

        if status_actual == "COMPLETADA":
            raise HTTPException(status_code=409, detail="La pieza ya está completada.")

        if status_actual == "EN_PROCESO":
            plan_view = _ll_annotar_plan(cursor, orden, tipo, plan_json)
            conexion.commit()
            return {
                "estatus": "ok",
                "mensaje": "La pieza ya estaba en proceso.",
                "plan": plan_view,
                "pieza_uid": pieza_uid,
            }

        cursor.execute("""
            INSERT INTO lista_largos_cortes (
                orden_id, tipo_orden, material, barra_index, pieza_index, pieza_uid,
                status, fecha_update, hora_inicio_corte, hora_fin_corte
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'EN_PROCESO', NOW(), NOW(), NULL)
            ON CONFLICT (orden_id, tipo_orden, material, barra_index, pieza_index)
            DO UPDATE SET
                pieza_uid = EXCLUDED.pieza_uid,
                status = 'EN_PROCESO',
                fecha_update = NOW(),
                hora_inicio_corte = COALESCE(lista_largos_cortes.hora_inicio_corte, NOW()),
                hora_fin_corte = NULL;
        """, (orden, tipo, material, barra_index, pieza_index, pieza_uid))

        _ll_registrar_evento_pieza(
            cursor,
            turno_id=turno["id"],
            sesion_id=sesion["id"],
            orden_id=orden,
            tipo_orden=tipo,
            material=material,
            barra_index=barra_index,
            pieza_index=pieza_index,
            pieza_uid=pieza_uid,
            accion="INICIO_CORTE",
            operador=operador,
            bahia=bahia,
        )

        plan_view = _ll_annotar_plan(cursor, orden, tipo, plan_json)
        _ll_sync_sesion_activa(
            cursor,
            orden,
            tipo,
            safe_int(plan_view.get("resumen", {}).get("total_piezas"), 0),
            safe_int(plan_view.get("resumen", {}).get("piezas_completadas"), 0),
        )

        conexion.commit()

        return {
            "estatus": "ok",
            "mensaje": "Pieza iniciada correctamente.",
            "plan": plan_view,
            "pieza_uid": pieza_uid,
        }

    except HTTPException:
        if conexion:
            conexion.rollback()
        raise
    except Exception as e:
        if conexion:
            conexion.rollback()
        raise HTTPException(status_code=500, detail=f"Error al iniciar pieza: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()

@app.patch("/api/lista-largos/pieza")
def toggle_pieza_lista_largos(payload: ListaLargosTogglePiezaPayload):
    asegurar_tablas_lista_largos_operativas()

    conexion = None
    cursor = None
    try:
        orden = normalizar_orden_id(payload.orden_id)
        tipo = normalizar_tipo_orden(payload.tipo_orden)
        material = _ll_material_key(payload.material)
        barra_index = max(1, int(payload.barra_index))
        pieza_index = max(1, int(payload.pieza_index))
        operador = (payload.operador or "").strip()
        bahia = (payload.bahia or "").strip()

        if not operador:
            raise HTTPException(status_code=422, detail="operador es obligatorio.")
        if not bahia:
            raise HTTPException(status_code=422, detail="bahia es obligatoria.")

        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        sesion = _ll_validar_sesion_operativa(
            cursor,
            orden,
            tipo,
            operador=operador,
            bahia=bahia,
        )

        plan_json, plan_row = _ll_obtener_o_generar_plan(cursor, orden, tipo)

        if plan_row and _ll_plan_ya_finalizado(plan_row):
            raise HTTPException(status_code=409, detail="El plan ya está finalizado y no admite más cambios.")

        pieza = _ll_buscar_pieza(plan_json, material, barra_index, pieza_index)
        if not pieza:
            raise HTTPException(status_code=404, detail="No se encontró la pieza indicada en el plan.")

        pieza_uid = f"{orden}|{tipo}|{material}|{barra_index}|{pieza_index}"

        corte_actual = _ll_corte_actual(cursor, orden, tipo, material, barra_index, pieza_index)
        status_actual = str((corte_actual or {}).get("status") or "").upper()

        if payload.completada:
            if not corte_actual or status_actual == "PENDIENTE":
                raise HTTPException(
                    status_code=409,
                    detail="La pieza debe iniciarse antes de marcarse como completada."
                )

            if status_actual == "COMPLETADA":
                raise HTTPException(
                    status_code=409,
                    detail="La pieza ya estaba completada."
                )

            cursor.execute("""
                UPDATE lista_largos_cortes
                SET status = 'COMPLETADA',
                    fecha_update = NOW(),
                    hora_fin_corte = NOW()
                WHERE orden_id = %s
                AND tipo_orden = %s
                AND material = %s
                AND barra_index = %s
                AND pieza_index = %s
                AND status = 'EN_PROCESO'
                RETURNING *;
            """, (orden, tipo, material, barra_index, pieza_index))

            corte_cerrado = cursor.fetchone()
            if not corte_cerrado:
                raise HTTPException(
                    status_code=409,
                    detail="La pieza no estaba en proceso al momento de completarse."
                )
        else:
            if not corte_actual or status_actual == "PENDIENTE":
                raise HTTPException(
                    status_code=409,
                    detail="La pieza no estaba iniciada ni completada."
                )

            cursor.execute("""
                UPDATE lista_largos_cortes
                SET status = 'PENDIENTE',
                    fecha_update = NOW(),
                    hora_inicio_corte = NULL,
                    hora_fin_corte = NULL
                WHERE orden_id = %s
                  AND tipo_orden = %s
                  AND material = %s
                  AND barra_index = %s
                  AND pieza_index = %s;
            """, (orden, tipo, material, barra_index, pieza_index))

        turno = _ll_obtener_turno_activo(cursor, orden, tipo)
        if turno:
            _ll_registrar_evento_pieza(
                cursor,
                turno_id=turno["id"],
                sesion_id=sesion["id"],
                orden_id=orden,
                tipo_orden=tipo,
                material=material,
                barra_index=barra_index,
                pieza_index=pieza_index,
                pieza_uid=pieza_uid,
                accion="COMPLETADA" if payload.completada else "REABIERTA",
                operador=operador,
                bahia=bahia,
            )

        plan_view = _ll_annotar_plan(cursor, orden, tipo, plan_json)
        _ll_sync_sesion_activa(
            cursor,
            orden,
            tipo,
            safe_int(plan_view.get("resumen", {}).get("total_piezas"), 0),
            safe_int(plan_view.get("resumen", {}).get("piezas_completadas"), 0),
        )

        conexion.commit()

        return {
            "estatus": "ok",
            "plan": plan_view,
            "pieza_uid": pieza_uid,
        }
    except HTTPException:
        if conexion:
            conexion.rollback()
        raise
    except Exception as e:
        if conexion:
            conexion.rollback()
        raise HTTPException(status_code=500, detail=f"Error al actualizar pieza: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


@app.post("/api/lista-largos/sobrante")
def guardar_sobrante_lista_largos(payload: ListaLargosSobrantePayload):
    asegurar_tablas_lista_largos_operativas()

    conexion = None
    cursor = None
    try:
        orden = normalizar_orden_id(payload.orden_id)
        tipo = normalizar_tipo_orden(payload.tipo_orden)
        material = _ll_material_key(payload.material)
        barra_index = max(1, int(payload.barra_index))
        operador = (payload.operador or "").strip()
        bahia = (payload.bahia or "").strip()

        if not operador:
            raise HTTPException(status_code=422, detail="operador es obligatorio.")
        if not bahia:
            raise HTTPException(status_code=422, detail="bahia es obligatoria.")

        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        sesion = _ll_validar_sesion_operativa(
            cursor,
            orden,
            tipo,
            operador=operador,
            bahia=bahia,
        )

        plan_json, plan_row = _ll_obtener_o_generar_plan(cursor, orden, tipo)

        if plan_row and _ll_plan_ya_finalizado(plan_row):
            raise HTTPException(status_code=409, detail="El plan ya está finalizado y no admite más cambios.")

        barra = _ll_buscar_barra(plan_json, material, barra_index)
        if not barra:
            raise HTTPException(status_code=404, detail="No se encontró la barra indicada en el plan.")

        if payload.sobrante_real is None:
            cursor.execute("""
                DELETE FROM lista_largos_sobrantes
                WHERE orden_id = %s
                  AND tipo_orden = %s
                  AND material = %s
                  AND barra_index = %s;
            """, (orden, tipo, material, barra_index))
        else:
            sobrante_real = max(0.0, float(payload.sobrante_real))

            cursor.execute("""
                INSERT INTO lista_largos_sobrantes (
                    orden_id, tipo_orden, material, barra_index, sobrante_real, fecha_update
                )
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (orden_id, tipo_orden, material, barra_index)
                DO UPDATE SET
                    sobrante_real = EXCLUDED.sobrante_real,
                    fecha_update = NOW();
            """, (orden, tipo, material, barra_index, sobrante_real))
        
        turno = _ll_obtener_turno_activo(cursor, orden, tipo)
        if turno:
            _ll_registrar_evento_sobrante(
                cursor,
                turno_id=turno["id"],
                sesion_id=sesion["id"],
                orden_id=orden,
                tipo_orden=tipo,
                material=material,
                barra_index=barra_index,
                accion="CAPTURADO" if payload.sobrante_real is not None else "ELIMINADO",
                sobrante_real=payload.sobrante_real,
                operador=operador,
                bahia=bahia,
            )

        plan_view = _ll_annotar_plan(cursor, orden, tipo, plan_json)
        _ll_sync_sesion_activa(
            cursor,
            orden,
            tipo,
            safe_int(plan_view.get("resumen", {}).get("total_piezas"), 0),
            safe_int(plan_view.get("resumen", {}).get("piezas_completadas"), 0),
        )
        conexion.commit()

        return {
            "estatus": "ok",
            "plan": plan_view,
        }
    except HTTPException:
        if conexion:
            conexion.rollback()
        raise
    except Exception as e:
        if conexion:
            conexion.rollback()
        raise HTTPException(status_code=500, detail=f"Error al guardar sobrante: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


@app.get("/api/lista-largos/remanentes-stock")
def listar_remanentes_stock_lista_largos(material: Optional[str] = None):
    asegurar_tablas_lista_largos_operativas()

    conexion = None
    cursor = None
    try:
        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        if material:
            cursor.execute("""
                SELECT *
                FROM lista_largos_remanentes
                WHERE status = 'DISPONIBLE'
                  AND material = %s
                ORDER BY largo_real DESC, created_at ASC;
            """, (_ll_material_key(material),))
        else:
            cursor.execute("""
                SELECT *
                FROM lista_largos_remanentes
                WHERE status = 'DISPONIBLE'
                ORDER BY material ASC, largo_real DESC, created_at ASC;
            """)

        rows = cursor.fetchall() or []

        return {
            "estatus": "ok",
            "remanentes": rows,
            "total": len(rows),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al listar remanentes: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


@app.post("/api/lista-largos/finalizar")
def finalizar_plan_lista_largos(payload: ListaLargosFinalizarPayload):
    asegurar_tablas_lista_largos_operativas()

    conexion = None
    cursor = None
    try:
        orden = normalizar_orden_id(payload.orden_id)
        tipo = normalizar_tipo_orden(payload.tipo_orden)
        operador = (payload.operador or "").strip()
        bahia = (payload.bahia or "").strip()

        if not operador:
            raise HTTPException(status_code=422, detail="operador es obligatorio.")
        if not bahia:
            raise HTTPException(status_code=422, detail="bahia es obligatoria.")

        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)
        _ll_validar_sesion_operativa(
            cursor,
            orden,
            tipo,
            operador=operador,
            bahia=bahia,
        )

        plan_json, plan_row = _ll_obtener_o_generar_plan(cursor, orden, tipo)
        pieza_en_proceso = _ll_pieza_en_proceso(cursor, orden, tipo)
        if pieza_en_proceso:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "NO SE PUEDE FINALIZAR: existe una pieza en proceso.",
                    "pieza_en_proceso": {
                        "material": pieza_en_proceso.get("material"),
                        "barra_index": pieza_en_proceso.get("barra_index"),
                        "pieza_index": pieza_en_proceso.get("pieza_index"),
                        "hora_inicio_corte": (
                            pieza_en_proceso.get("hora_inicio_corte").isoformat()
                            if isinstance(pieza_en_proceso.get("hora_inicio_corte"), datetime)
                            else pieza_en_proceso.get("hora_inicio_corte")
                        ),
                    }
                }
            )

        if plan_row and _ll_plan_ya_finalizado(plan_row):
            raise HTTPException(status_code=409, detail="El plan ya fue finalizado previamente.")

        plan_view = _ll_annotar_plan(cursor, orden, tipo, plan_json)
        resumen = plan_view.get("resumen", {}) or {}

        if safe_int(resumen.get("piezas_pendientes"), 0) > 0:
            faltantes = []

            for material_data in plan_view.get("materiales") or []:
                material = material_data.get("material")
                for barra in material_data.get("barras") or []:
                    for pieza in barra.get("cortes") or []:
                        if not bool(pieza.get("done")):
                            faltantes.append({
                                "material": material,
                                "barra_index": barra.get("barra_index"),
                                "pieza_index": pieza.get("pieza_index"),
                                "nombre": pieza.get("nombre"),
                                "origen_id": pieza.get("origen_id"),
                            })

            raise HTTPException(
                status_code=400,
                detail={
                    "error": "NO SE PUEDE FINALIZAR: faltan piezas por marcar como completadas.",
                    "count": len(faltantes),
                    "faltantes": faltantes[:25],
                }
            )

        faltan_sobrantes = plan_view.get("faltan_sobrantes") or []
        if faltan_sobrantes:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "NO SE PUEDE FINALIZAR: faltan sobrantes reales requeridos.",
                    "count": len(faltan_sobrantes),
                    "faltantes": faltan_sobrantes,
                }
            )

        remanentes_creados = []
        remanentes_actualizados = []

        for material_data in plan_view.get("materiales") or []:
            material = material_data.get("material")

            for barra in material_data.get("barras") or []:
                source = str(barra.get("source") or "STOCK").upper()
                barra_index = safe_int(barra.get("barra_index"), 0)
                rem_calc = safe_float(barra.get("remanente_calc"), 0.0)
                rem_real = barra.get("remanente_real")
                rem_final = safe_float(rem_real, rem_calc) if rem_real is not None else rem_calc

                if source == "REMANENTE":
                    rem_uid = safe_int(barra.get("rem_uid"), 0)
                    if rem_uid <= 0:
                        continue

                    nuevo_status = "DISPONIBLE" if rem_final > LISTA_LARGOS_REM_MINIMO else "DESCARTADO"

                    cursor.execute("""
                        UPDATE lista_largos_remanentes
                        SET status = %s,
                            largo_real = %s,
                            largo_id = %s,
                            reservado_para_orden_id = NULL,
                            reservado_para_tipo_orden = NULL,
                            updated_at = NOW()
                        WHERE rem_uid = %s
                        RETURNING rem_uid, rem_id, largo_real, status;
                    """, (nuevo_status, rem_final, int(round(rem_final)), rem_uid))

                    row = cursor.fetchone()
                    if row:
                        remanentes_actualizados.append(dict(row))

                elif source == "STOCK":
                    if rem_final <= LISTA_LARGOS_REM_MINIMO:
                        continue

                    seccion = _ll_material_to_seccion(material)
                    largo_id = int(round(rem_final))
                    rem_id = _ll_generar_rem_id(
                        cursor,
                        LISTA_LARGOS_TIPO_REMANENTE_DEFAULT,
                        seccion,
                        largo_id,
                        LISTA_LARGOS_GRADE_DEFAULT
                    )

                    cursor.execute("""
                        INSERT INTO lista_largos_remanentes (
                            rem_id,
                            tipo,
                            seccion,
                            largo_real,
                            largo_id,
                            material_grade,
                            material,
                            fuente_orden_id,
                            fuente_tipo_orden,
                            barra_index,
                            status,
                            reservado_para_orden_id,
                            reservado_para_tipo_orden,
                            created_at,
                            updated_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, 'DISPONIBLE',
                            NULL, NULL,
                            NOW(), NOW()
                        )
                        RETURNING rem_uid, rem_id, material, largo_real, barra_index;
                    """, (
                        rem_id,
                        LISTA_LARGOS_TIPO_REMANENTE_DEFAULT,
                        seccion,
                        rem_final,
                        largo_id,
                        LISTA_LARGOS_GRADE_DEFAULT,
                        material,
                        orden,
                        tipo,
                        barra_index,
                    ))

                    row = cursor.fetchone()
                    if row:
                        remanentes_creados.append(dict(row))

        cursor.execute("""
            UPDATE lista_largos_planes
            SET estado = 'finalizado',
                updated_at = NOW()
            WHERE orden_id = %s
            AND tipo_orden = %s;
        """, (orden, tipo))

        cursor.execute("""
            UPDATE lista_largos_sesiones
            SET estado = 'finalizada',
                piezas_totales = %s,
                piezas_completadas = %s,
                hora_ultimo_movimiento = NOW(),
                hora_fin = NOW(),
                updated_at = NOW()
            WHERE orden_id = %s
            AND tipo_orden = %s
            AND estado IN ('activa', 'cerrada');
        """, (
            safe_int(resumen.get("total_piezas"), 0),
            safe_int(resumen.get("piezas_completadas"), 0),
            orden,
            tipo,
        ))

        _ll_cerrar_turno_activo(
            cursor,
            orden,
            tipo,
            "finalizado",
            safe_int(resumen.get("piezas_completadas"), 0)
        )

        conexion.commit()

        return {
            "estatus": "ok",
            "mensaje": "Plan de lista de largos finalizado correctamente.",
            "remanentes_creados": remanentes_creados,
            "remanentes_actualizados": remanentes_actualizados,
            "resumen": resumen,
        }
    except HTTPException:
        if conexion:
            conexion.rollback()
        raise
    except Exception as e:
        if conexion:
            conexion.rollback()
        raise HTTPException(status_code=500, detail=f"Error al finalizar plan de lista de largos: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()

def _ll_pdf_data_nesteo_from_plan(plan_view: dict) -> dict:
    data_nesteo = {}

    for material_data in (plan_view or {}).get("materiales", []) or []:
        material = str(material_data.get("material") or "").strip() or "SIN_CLASIFICACION"
        barras_out = []

        for barra in material_data.get("barras", []) or []:
            rem_show = barra.get("remanente_real")
            if rem_show is None:
                rem_show = barra.get("remanente_show", barra.get("remanente_calc", 0.0))

            barras_out.append({
                "source": str(barra.get("source") or "STOCK").upper(),
                "largo_stock": safe_float(barra.get("largo_stock"), 0.0),
                "remanente_calc": safe_float(barra.get("remanente_calc"), 0.0),
                "remanente_real": barra.get("remanente_real"),
                "remanente_show": safe_float(rem_show, 0.0),
                "rem_uid": barra.get("rem_uid"),
                "rem_id": barra.get("rem_id"),
                "barra_index": safe_int(barra.get("barra_index"), 0),
                "requiere_sobrante_real": bool(barra.get("requiere_sobrante_real")),
                "cortes": [
                    {
                        "nombre": str(p.get("nombre") or ""),
                        "largo": safe_float(p.get("largo"), 0.0),
                        "origen_id": str(p.get("origen_id") or ""),
                        "origen_lbl": str(p.get("origen_lbl") or ""),
                        "job": str(p.get("job") or ""),
                        "done": bool(p.get("done")),
                    }
                    for p in (barra.get("cortes") or [])
                ],
            })

        data_nesteo[material] = barras_out

    return data_nesteo


def _ll_pdf_rem_id_map_resultantes(cursor, orden_id: str, tipo_orden: str) -> dict:
    cursor.execute("""
        SELECT material, barra_index, rem_id
        FROM lista_largos_remanentes
        WHERE fuente_orden_id = %s
          AND fuente_tipo_orden = %s
        ORDER BY created_at ASC;
    """, (orden_id, tipo_orden))

    out = {}
    for row in cursor.fetchall() or []:
        key = (
            str(row.get("material") or "").strip(),
            safe_int(row.get("barra_index"), 0),
        )
        out[key] = str(row.get("rem_id") or "").strip() or None

    return out


def _ll_construir_snapshot_pdf(cursor, orden_id: str, tipo_orden: str) -> dict:
    plan_json, plan_row = _ll_obtener_o_generar_plan(cursor, orden_id, tipo_orden)
    plan_view = _ll_annotar_plan(cursor, orden_id, tipo_orden, plan_json)
    sesion = obtener_sesion_prioritaria(cursor, orden_id, tipo_orden)

    data_nesteo = _ll_pdf_data_nesteo_from_plan(plan_view)
    rem_id_map = _ll_pdf_rem_id_map_resultantes(cursor, orden_id, tipo_orden)
    remanentes_resultantes = build_remanentes_resultantes(data_nesteo, rem_id_map=rem_id_map)

    return {
        "titulo_job": orden_id,
        "orden_id": orden_id,
        "tipo_orden": tipo_orden,
        "estado_plan": (plan_row.get("estado") if plan_row else "activo"),
        "fecha_emision": _db_now_iso(cursor),
        "operador": (sesion.get("operador") if sesion else None),
        "bahia": (sesion.get("bahia") if sesion else None),
        "hora_inicio": (sesion.get("hora_inicio") if sesion else None),
        "hora_fin": (sesion.get("hora_fin") if sesion else None),
        "plan": plan_view,
        "data_nesteo": data_nesteo,
        "remanentes_resultantes": remanentes_resultantes,
    }

def _db_now_iso(cursor) -> Optional[str]:
    cursor.execute("SELECT NOW() AS ahora;")
    row = cursor.fetchone()
    if not row:
        return None

    valor = row.get("ahora") if isinstance(row, dict) else None
    if isinstance(valor, datetime):
        return valor.isoformat()

    return str(valor) if valor else None

@app.get("/api/wo/lista-largos/pdf")
def descargar_pdf_lista_largos_wo(wo_id: str):
    asegurar_tablas_lista_largos_operativas()

    conexion = None
    cursor = None
    try:
        orden = normalizar_orden_id(wo_id)
        tipo = "WO"

        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        snapshot = _ll_construir_snapshot_pdf(cursor, orden, tipo)
        pdf_buffer = generar_pdf_lista_largos(snapshot)

        filename = f'Lista_Largos_{orden.replace("/", "-")}.pdf'
        return Response(
            content=pdf_buffer.getvalue(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al generar PDF de lista de largos: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


@app.get("/api/swo/lista-largos/pdf")
def descargar_pdf_lista_largos_swo(swo_id: str):
    asegurar_tablas_lista_largos_operativas()

    conexion = None
    cursor = None
    try:
        orden = normalizar_orden_id(swo_id)
        tipo = "SWO"

        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)
        _asegurar_swo_nesteada(cursor, orden, contexto="descargar PDF de lista de largos")

        snapshot = _ll_construir_snapshot_pdf(cursor, orden, tipo)
        pdf_buffer = generar_pdf_lista_largos(snapshot)

        filename = f'Lista_Largos_{orden.replace("/", "-")}.pdf'
        return Response(
            content=pdf_buffer.getvalue(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al generar PDF de lista de largos: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()

def _obtener_detalle_placa_generico(orden_id: str, es_swo: bool, placa_id: Optional[str] = None, sheet_uid: Optional[str] = None):
    conexion = None
    cursor = None
    try:
        orden_limpia = (orden_id or "").strip()
        placa_limpia = (placa_id or "").strip()
        sheet_uid_limpio = (sheet_uid or "").strip()
        campo_where = "super_work_order" if es_swo else "work_order"

        conexion = db_connect()
        cursor = conexion.cursor(cursor_factory=RealDictCursor)
        if es_swo:
            _asegurar_swo_nesteada(cursor, orden_limpia, contexto="consultar vista de operador")

        columnas_existentes = _obtener_columnas_reporte_cortes(cursor)
        usar_sheet_uid = "sheet_uid" in columnas_existentes and bool(sheet_uid_limpio)

        if usar_sheet_uid:
            cursor.execute(
                f"""
                SELECT *
                FROM reporte_cortes
                WHERE TRIM({campo_where}) = %s
                  AND TRIM(sheet_uid) = %s
                ORDER BY id ASC;
                """,
                (orden_limpia, sheet_uid_limpio)
            )
        else:
            if not placa_limpia:
                raise HTTPException(status_code=400, detail="Falta placa_id o sheet_uid")
            cursor.execute(
                f"""
                SELECT *
                FROM reporte_cortes
                WHERE TRIM({campo_where}) = %s
                  AND TRIM(placa_id) = %s
                ORDER BY id ASC;
                """,
                (orden_limpia, placa_limpia)
            )

        piezas = cursor.fetchall()

        return {
            "nombre": orden_limpia,
            "placa_id": placa_limpia,
            "sheet_uid": sheet_uid_limpio if usar_sheet_uid else None,
            "piezas": piezas
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


@app.get("/api/swo/placa-detalle")
def obtener_detalle_placa_swo(swo_id: str, placa_id: Optional[str] = None, sheet_uid: Optional[str] = None):
    return _obtener_detalle_placa_generico(swo_id, es_swo=True, placa_id=placa_id, sheet_uid=sheet_uid)


@app.get("/api/wo/placa-detalle")
def obtener_detalle_placa_wo(wo_id: str, placa_id: Optional[str] = None, sheet_uid: Optional[str] = None):
    return _obtener_detalle_placa_generico(wo_id, es_swo=False, placa_id=placa_id, sheet_uid=sheet_uid)


@app.get("/api/swo/pdf-produccion")
def descargar_pdf_produccion_swo(swo_id: str):
    from reporte_pdf_nesting import exportar_pdf_radiografia_web

    detalle = construir_radiografia(swo_id, es_swo=True)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    ruta_pdf = tmp.name
    tmp.close()

    try:
        exportar_pdf_radiografia_web(
            detalle_radiografia=detalle,
            ruta_pdf=ruta_pdf,
            nombre_orden=detalle.get("nombre_orden") or detalle.get("nombre") or swo_id,
            work_order_label=detalle.get("work_order_label") or detalle.get("nombre") or swo_id,
        )

        with open(ruta_pdf, "rb") as f:
            data = f.read()

        nombre_pdf = (detalle.get("nombre_orden") or detalle.get("nombre") or swo_id).strip()
        wo_pdf = (detalle.get("work_order_label") or detalle.get("nombre") or swo_id).strip()

        nombre_pdf = nombre_pdf.replace("/", "-").replace("\\", "-")
        wo_pdf = wo_pdf.replace("/", "-").replace("\\", "-")

        filename = f"Nesting_Reporte_{nombre_pdf}-{wo_pdf}.pdf"
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    finally:
        try:
            os.remove(ruta_pdf)
        except Exception:
            pass


@app.get("/api/wo/pdf-produccion")
def descargar_pdf_produccion_wo(wo_id: str):
    from reporte_pdf_nesting import exportar_pdf_radiografia_web

    detalle = construir_radiografia(wo_id, es_swo=False)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    ruta_pdf = tmp.name
    tmp.close()

    try:
        exportar_pdf_radiografia_web(
            detalle_radiografia=detalle,
            ruta_pdf=ruta_pdf,
            nombre_orden=detalle.get("nombre_orden") or detalle.get("nombre") or wo_id,
            work_order_label=detalle.get("work_order_label") or wo_id,
        )

        with open(ruta_pdf, "rb") as f:
            data = f.read()

        nombre_pdf = (detalle.get("nombre_orden") or detalle.get("nombre") or wo_id).strip()
        wo_pdf = (detalle.get("work_order_label") or wo_id).strip()

        nombre_pdf = nombre_pdf.replace("/", "-").replace("\\", "-")
        wo_pdf = wo_pdf.replace("/", "-").replace("\\", "-")

        filename = f"Nesting_Reporte_{nombre_pdf}-{wo_pdf}.pdf"
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    finally:
        try:
            os.remove(ruta_pdf)
        except Exception:
            pass