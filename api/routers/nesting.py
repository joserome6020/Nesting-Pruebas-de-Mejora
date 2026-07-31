from fastapi import APIRouter, HTTPException, Response, Query, BackgroundTasks, Body
from fastapi.responses import FileResponse, JSONResponse
from typing import List, Optional, Dict, Any
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor, Json
import json, os, time, io, base64, hashlib, re, tempfile
from pathlib import Path
from api.database import db_connect, DB_CONFIG, DB_TIMEZONE, DXF_ROOT_DIR, DXF_MAP_FILE
from api.models import *
from api.legacy_core import *

router = APIRouter()

@router.get("/api/jobs")
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

@router.get("/api/nesting/{nombre_job}")
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

@router.get("/api/placas")
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