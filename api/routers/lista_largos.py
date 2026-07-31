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

@router.get("/api/lista-largos/sesion")
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

@router.post("/api/lista-largos/sesion/iniciar")
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

@router.post("/api/lista-largos/sesion/avance")
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

@router.post("/api/lista-largos/sesion/finalizar")
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

@router.get("/api/lista-largos/plan")
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

@router.get("/api/lista-largos/material-requerido")
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

@router.post("/api/lista-largos/material-requerido/refrescar-herinox")
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

@router.post("/api/lista-largos/material-requerido/sincronizar")
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

@router.post("/api/lista-largos/pieza/iniciar")
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

@router.patch("/api/lista-largos/pieza")
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

@router.post("/api/lista-largos/sobrante")
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

@router.get("/api/lista-largos/remanentes-stock")
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

@router.post("/api/lista-largos/finalizar")
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