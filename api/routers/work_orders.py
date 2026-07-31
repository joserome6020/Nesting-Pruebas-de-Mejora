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

@router.get("/api/work_orders/ingenieria")
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

@router.get("/api/work_orders/super")
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

@router.post("/api/work_orders/combinar")
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

@router.get("/api/swo/detalles")
def obtener_detalles_swo(swo_id: str):
    return construir_radiografia(swo_id, es_swo=True)

@router.get("/api/wo/detalles")
def obtener_detalles_wo(wo_id: str):
    return construir_radiografia(wo_id, es_swo=False)

@router.get("/api/wo/{wo_id:path}/detalles")
def obtener_detalles_wo_path(wo_id: str):
    return construir_radiografia(wo_id, es_swo=False)

@router.get("/api/swo/placas-operador")
def obtener_placas_operador_swo(swo_id: str):
    detalle = construir_radiografia(swo_id, es_swo=True)
    return construir_placas_operador(detalle)

@router.get("/api/wo/placas-operador")
def obtener_placas_operador_wo(wo_id: str):
    detalle = construir_radiografia(wo_id, es_swo=False)
    return construir_placas_operador(detalle)

@router.get("/api/wo/lista-largos")
def obtener_lista_largos_wo(wo_id: str):
    return construir_lista_largos_wo(wo_id)

@router.get("/api/wo/{wo_id:path}/lista-largos")
def obtener_lista_largos_wo_path(wo_id: str):
    return construir_lista_largos_wo(wo_id)

@router.get("/api/swo/lista-largos")
def obtener_lista_largos_swo(swo_id: str):
    return construir_lista_largos_swo(swo_id)

@router.get("/api/wo/lista-largos/pdf")
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

@router.get("/api/swo/lista-largos/pdf")
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

@router.get("/api/swo/placa-detalle")
def obtener_detalle_placa_swo(swo_id: str, placa_id: Optional[str] = None, sheet_uid: Optional[str] = None):
    return _obtener_detalle_placa_generico(swo_id, es_swo=True, placa_id=placa_id, sheet_uid=sheet_uid)

@router.get("/api/wo/placa-detalle")
def obtener_detalle_placa_wo(wo_id: str, placa_id: Optional[str] = None, sheet_uid: Optional[str] = None):
    return _obtener_detalle_placa_generico(wo_id, es_swo=False, placa_id=placa_id, sheet_uid=sheet_uid)

@router.get("/api/swo/pdf-produccion")
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

@router.get("/api/wo/pdf-produccion")
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