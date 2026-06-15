"""
Pedido de compra — Material requerido LdG (Lista de Largos).

Persistencia en material_requerido_ldg. Solo STOCK (sin remanentes).
Se genera una sola vez por orden; no se recalcula si ya existe registro.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import os

import psycopg2
from psycopg2.extras import RealDictCursor

DB_CONFIG = {
    "host": os.getenv("NESTING_DB_HOST", "localhost"),
    "database": os.getenv("NESTING_DB_NAME", "nestingpro_db"),
    "user": os.getenv("NESTING_DB_USER", "postgres"),
    "password": os.getenv("NESTING_DB_PASSWORD", "nesting123"),
    "port": os.getenv("NESTING_DB_PORT", "5433"),
}


def asegurar_tabla_material_requerido_ldg():
    conexion = None
    cursor = None
    try:
        conexion = psycopg2.connect(**DB_CONFIG)
        cursor = conexion.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS material_requerido_ldg (
                id SERIAL PRIMARY KEY,
                orden_id VARCHAR(100) NOT NULL,
                tipo_orden VARCHAR(10) NOT NULL,
                material VARCHAR(255) NOT NULL,
                codigo VARCHAR(100) NOT NULL DEFAULT '',
                largo NUMERIC(12, 2) NOT NULL DEFAULT 0,
                cantidad INTEGER NOT NULL DEFAULT 0,
                costo NUMERIC(14, 4) NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_mrl_tipo_orden CHECK (tipo_orden IN ('WO', 'SWO')),
                CONSTRAINT chk_mrl_cantidad CHECK (cantidad >= 0)
            );
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_mrl_orden_tipo
            ON material_requerido_ldg (orden_id, tipo_orden);
        """)

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_mrl_orden_material_largo
            ON material_requerido_ldg (orden_id, tipo_orden, material, largo);
        """)

        conexion.commit()
    except Exception as e:
        if conexion:
            conexion.rollback()
        print(f"⚠️ No se pudo asegurar tabla material_requerido_ldg: {e}")
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


def _serializar_fila(fila: Optional[dict]) -> Optional[dict]:
    if not fila:
        return None
    data = dict(fila)
    for campo in ("created_at", "updated_at"):
        valor = data.get(campo)
        if isinstance(valor, datetime):
            data[campo] = valor.isoformat()
    if data.get("largo") is not None:
        data["largo"] = float(data["largo"])
    if data.get("costo") is not None:
        data["costo"] = float(data["costo"])
    return data


def _serializar_filas(filas: List[dict]) -> List[dict]:
    return [_serializar_fila(dict(f)) for f in filas if f]


def _iter_barras_por_material(plan: Dict[str, Any]):
    """Acepta plan con 'materiales' (vista) o 'data' (json guardado)."""
    if plan.get("materiales"):
        for material_data in plan.get("materiales") or []:
            material = str(material_data.get("material") or "").strip() or "SIN CLASIFICACION"
            yield material, material_data.get("barras") or []
        return

    data = plan.get("data") or {}
    for material, barras in data.items():
        yield str(material or "").strip() or "SIN CLASIFICACION", barras or []


def agregar_filas_desde_plan(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        from catalogo_largos import (
            _cargar_placas_largos_desde_herinox,
            datos_material_requerido_pedido,
        )
    except ImportError:
        return []

    catalogo = _cargar_placas_largos_desde_herinox(solo_disponibles=False)
    acumulado: Dict[str, Dict[str, Any]] = {}

    for material, barras in _iter_barras_por_material(plan):
        for barra in barras:
            source = str(barra.get("source") or "STOCK").strip().upper()
            if source == "REMANENTE":
                continue
            if material not in acumulado:
                acumulado[material] = {"material": material, "cantidad": 0}
            acumulado[material]["cantidad"] += 1

    filas: List[Dict[str, Any]] = []
    for item in acumulado.values():
        datos = datos_material_requerido_pedido(
            item["material"], item["cantidad"], catalogo=catalogo
        )
        filas.append(
            {
                "material": item["material"],
                "cantidad": item["cantidad"],
                "codigo": datos.get("codigo") or "",
                "largo": float(datos.get("largo") or 0),
                "costo": datos.get("costo"),
            }
        )
    filas.sort(key=lambda r: (r["material"], r.get("codigo") or ""))
    return filas


def obtener_filas_pedido(cursor, orden_id: str, tipo_orden: str) -> List[dict]:
    cursor.execute(
        """
        SELECT *
        FROM material_requerido_ldg
        WHERE TRIM(orden_id) = %s AND tipo_orden = %s
        ORDER BY material ASC, largo ASC;
        """,
        (orden_id, tipo_orden),
    )
    return cursor.fetchall() or []


def existe_pedido(cursor, orden_id: str, tipo_orden: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM material_requerido_ldg
        WHERE TRIM(orden_id) = %s AND tipo_orden = %s
        LIMIT 1;
        """,
        (orden_id, tipo_orden),
    )
    return cursor.fetchone() is not None


def enriquecer_pedido_herinox_cursor(
    cursor,
    orden_id: str,
    tipo_orden: str,
    forzar_costo: bool = False,
) -> int:
    """Completa codigo/costo en filas ya insertadas usando catálogo Herinox."""
    try:
        from catalogo_largos import (
            _cargar_placas_largos_desde_herinox,
            datos_material_requerido_pedido,
            extraer_codigo_herinox_combo,
        )
    except ImportError:
        return 0

    catalogo = _cargar_placas_largos_desde_herinox(solo_disponibles=False)
    if not catalogo:
        return 0

    filas = obtener_filas_pedido(cursor, orden_id, tipo_orden)
    actualizadas = 0

    for fila in filas:
        material_txt = str(fila.get("material") or "").strip()
        cantidad = int(fila.get("cantidad") or 1)
        codigo_esperado = extraer_codigo_herinox_combo(material_txt)
        codigo_actual = str(fila.get("codigo") or "").strip()
        costo_actual = fila.get("costo")
        largo_actual = float(fila.get("largo") or 0)
        tiene_costo = costo_actual is not None and float(costo_actual or 0) > 0

        datos = datos_material_requerido_pedido(
            material_txt, cantidad, catalogo=catalogo
        )
        nuevo_codigo = str(datos.get("codigo") or codigo_esperado or "").strip()
        nuevo_largo = float(datos.get("largo") or 0)
        nuevo_costo = datos.get("costo")

        if not nuevo_codigo and nuevo_costo is None and nuevo_largo <= 0:
            continue

        if (
            not forzar_costo
            and tiene_costo
            and codigo_actual == nuevo_codigo
            and codigo_actual == codigo_esperado
            and nuevo_costo is not None
            and (nuevo_largo <= 0 or abs(largo_actual - nuevo_largo) < 0.01)
        ):
            continue

        cursor.execute(
            """
            UPDATE material_requerido_ldg
            SET codigo = %s,
                largo = CASE WHEN %s > 0 THEN %s ELSE largo END,
                costo = %s,
                updated_at = NOW()
            WHERE id = %s;
            """,
            (
                nuevo_codigo,
                nuevo_largo,
                nuevo_largo,
                nuevo_costo,
                fila["id"],
            ),
        )
        actualizadas += 1

    return actualizadas


def refrescar_pedido_herinox(orden_id: str, tipo_orden: str) -> Dict[str, Any]:
    asegurar_tabla_material_requerido_ldg()

    conexion = None
    cursor = None
    try:
        conexion = psycopg2.connect(**DB_CONFIG)
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        if not existe_pedido(cursor, orden_id, tipo_orden):
            return {
                "estatus": "ok",
                "existe": False,
                "actualizadas": 0,
                "mensaje": "No hay pedido registrado para esta orden.",
                "filas": [],
            }

        actualizadas = enriquecer_pedido_herinox_cursor(
            cursor, orden_id, tipo_orden, forzar_costo=True
        )
        conexion.commit()
        filas = obtener_filas_pedido(cursor, orden_id, tipo_orden)

        return {
            "estatus": "ok",
            "existe": True,
            "actualizadas": actualizadas,
            "mensaje": f"Pedido actualizado desde Herinox ({actualizadas} filas).",
            "filas": _serializar_filas(filas),
        }
    except Exception:
        if conexion:
            conexion.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


def insertar_pedido_desde_plan_cursor(
    cursor,
    orden_id: str,
    tipo_orden: str,
    plan: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Inserta filas si no existen. Retorna (generado, mensaje).
    Usa el cursor de la transacción activa del api_server.
    """
    if existe_pedido(cursor, orden_id, tipo_orden):
        return False, "El pedido de compra ya estaba registrado para esta orden."

    nuevas = agregar_filas_desde_plan(plan)
    if not nuevas:
        return False, "No hay barras de stock en el plan para generar el pedido."

    for fila in nuevas:
        cursor.execute(
            """
            INSERT INTO material_requerido_ldg (
                orden_id, tipo_orden, material, codigo, largo, cantidad, costo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s);
            """,
            (
                orden_id,
                tipo_orden,
                fila["material"],
                fila.get("codigo") or "",
                fila.get("largo") or 0,
                fila["cantidad"],
                fila.get("costo"),
            ),
        )

    enriquecer_pedido_herinox_cursor(cursor, orden_id, tipo_orden, forzar_costo=True)
    return True, "Pedido de compra generado correctamente."


def reconstruir_pedido_desde_plan(
    cursor,
    orden_id: str,
    tipo_orden: str,
    plan: Dict[str, Any],
) -> Tuple[bool, str]:
    cursor.execute(
        """
        DELETE FROM material_requerido_ldg
        WHERE TRIM(orden_id) = %s AND tipo_orden = %s
        """,
        (str(orden_id or "").strip(), tipo_orden),
    )
    nuevas = agregar_filas_desde_plan(plan)
    if not nuevas:
        return False, "No hay barras de stock en el plan para generar el pedido."

    for fila in nuevas:
        cursor.execute(
            """
            INSERT INTO material_requerido_ldg (
                orden_id, tipo_orden, material, codigo, largo, cantidad, costo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s);
            """,
            (
                orden_id,
                tipo_orden,
                fila["material"],
                fila.get("codigo") or "",
                fila.get("largo") or 0,
                fila["cantidad"],
                fila.get("costo"),
            ),
        )

    enriquecer_pedido_herinox_cursor(cursor, orden_id, tipo_orden, forzar_costo=True)
    return True, "Pedido de compra regenerado desde el plan."


def sincronizar_pedido_desde_plan(
    orden_id: str,
    tipo_orden: str,
    plan: Dict[str, Any],
) -> Dict[str, Any]:
    asegurar_tabla_material_requerido_ldg()

    conexion = None
    cursor = None
    try:
        conexion = psycopg2.connect(**DB_CONFIG)
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        generado_previo = existe_pedido(cursor, orden_id, tipo_orden)
        generado, mensaje = reconstruir_pedido_desde_plan(
            cursor, orden_id, tipo_orden, plan
        )
        conexion.commit()
        filas = obtener_filas_pedido(cursor, orden_id, tipo_orden)

        return {
            "estatus": "ok",
            "generado": generado and not generado_previo,
            "regenerado": generado,
            "mensaje": mensaje,
            "filas": _serializar_filas(filas),
        }
    except Exception:
        if conexion:
            conexion.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


def consultar_pedido(orden_id: str, tipo_orden: str) -> Dict[str, Any]:
    asegurar_tabla_material_requerido_ldg()

    conexion = None
    cursor = None
    try:
        conexion = psycopg2.connect(**DB_CONFIG)
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        filas = obtener_filas_pedido(cursor, orden_id, tipo_orden)
        if filas and any(float(f.get("costo") or 0) <= 0 for f in filas):
            enriquecer_pedido_herinox_cursor(cursor, orden_id, tipo_orden)
            conexion.commit()
            filas = obtener_filas_pedido(cursor, orden_id, tipo_orden)

        return {
            "estatus": "ok",
            "orden_id": orden_id,
            "tipo_orden": tipo_orden,
            "existe": len(filas) > 0,
            "filas": _serializar_filas(filas),
        }
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()
