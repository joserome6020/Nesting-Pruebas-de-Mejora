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

@router.post("/api/piezas/finalizar_lamina")
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

@router.post("/api/validar-material")
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

@router.post("/api/corte/script-seleccion")
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

@router.patch("/api/piezas/{pieza_id}/estatus")
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