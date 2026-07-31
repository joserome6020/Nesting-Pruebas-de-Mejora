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

@router.post("/api/reportes/guardar")
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