"""
Pedido de compra — Material requerido LdG (Lista de Largos).

Persistencia en material_requerido_ldg. Solo STOCK (sin remanentes).
Se genera una sola vez por orden; no se recalcula si ya existe registro.
"""

from __future__ import annotations

import math
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

_MRL_SCHEMA_READY = False


def _resolver_db_config(db_config: dict | None = None) -> dict:
    if db_config:
        cfg = dict(db_config)
        if "database" not in cfg and cfg.get("dbname"):
            cfg["database"] = cfg["dbname"]
        return cfg
    return dict(DB_CONFIG)


def asegurar_tabla_material_requerido_ldg(db_config: dict | None = None):
    """DDL idempotente; corre una sola vez por proceso para no bloquear export."""
    global _MRL_SCHEMA_READY
    if _MRL_SCHEMA_READY:
        return

    conexion = None
    cursor = None
    cfg = _resolver_db_config(db_config)
    try:
        conexion = psycopg2.connect(**cfg)
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
            CREATE INDEX IF NOT EXISTS idx_mrl_orden_material_largo
            ON material_requerido_ldg (orden_id, tipo_orden, material, largo);
        """)
        # Índice único legado: impedía split por barra (cantidad>1). Se elimina si existe.
        cursor.execute("""
            DROP INDEX IF EXISTS uq_mrl_orden_material_largo;
        """)

        cursor.execute("""
            ALTER TABLE material_requerido_ldg
            ADD COLUMN IF NOT EXISTS kit_recibido BOOLEAN NOT NULL DEFAULT FALSE;
        """)
        cursor.execute("""
            ALTER TABLE material_requerido_ldg
            ADD COLUMN IF NOT EXISTS kit_recibido_por VARCHAR(120) NULL;
        """)
        cursor.execute("""
            ALTER TABLE material_requerido_ldg
            ADD COLUMN IF NOT EXISTS kit_recibido_fecha TIMESTAMP NULL;
        """)
        for col_sql in (
            "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS provider_handshake_at TIMESTAMP NULL",
            "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS provider_handshake_by VARCHAR(120) NULL",
            "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS almacen_received_at TIMESTAMP NULL",
            "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS almacen_received_by VARCHAR(120) NULL",
            "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS incoming_handshake_at TIMESTAMP NULL",
            "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS incoming_handshake_by VARCHAR(120) NULL",
            "ALTER TABLE material_requerido_ldg ADD COLUMN IF NOT EXISTS rechazado_incoming BOOLEAN NOT NULL DEFAULT FALSE",
        ):
            cursor.execute(col_sql)

        conexion.commit()
        _MRL_SCHEMA_READY = True
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


def _cantidad_barras_catalogo(total_pulgadas_plan: float, largo_catalogo_in: float) -> int:
    """Convierte pulgadas consumidas en el plan a barras estándar del catálogo Herinox."""
    total = max(0.0, float(total_pulgadas_plan or 0))
    largo_cat = max(0.0, float(largo_catalogo_in or 0))
    if total <= 0:
        return 1
    if largo_cat <= 0:
        return 1
    return max(1, math.ceil(total / largo_cat))


def agregar_filas_desde_plan(
    plan: Dict[str, Any],
    barras_excluidas_pedido: set[str] | None = None,
) -> List[Dict[str, Any]]:
    try:
        from catalogo_largos import (
            _cargar_placas_largos_desde_herinox,
            datos_material_requerido_pedido,
        )
    except ImportError:
        return []

    catalogo = _cargar_placas_largos_desde_herinox(solo_disponibles=False)
    excluir = barras_excluidas_pedido or set()
    largo_cat_por_material: Dict[str, float] = {}
    acumulado: Dict[str, Dict[str, Any]] = {}

    def _largo_cat_material(material: str) -> float:
        if material not in largo_cat_por_material:
            datos = datos_material_requerido_pedido(material, 1, catalogo=catalogo)
            largo_cat_por_material[material] = float(datos.get("largo") or 0)
        return largo_cat_por_material[material]

    for material, barras in _iter_barras_por_material(plan):
        for bar_idx, barra in enumerate(barras):
            bar_key = f"{material}::{bar_idx}"
            if bar_key in excluir:
                continue
            source = str(barra.get("source") or "STOCK").strip().upper()
            if source == "REMANENTE":
                continue
            if not (barra.get("cortes") or []):
                continue
            largo_stock = float(barra.get("largo_stock") or 0)
            if largo_stock <= 0:
                continue
            try:
                from interface.largos_nesting_service import _slots_comerciales_en_tira

                largo_cat = _largo_cat_material(material)
                n_slots = _slots_comerciales_en_tira(largo_stock, largo_cat)
            except Exception:
                n_slots = 1
            if material not in acumulado:
                acumulado[material] = {"material": material, "total_slots": 0}
            acumulado[material]["total_slots"] += max(1, n_slots)

    filas: List[Dict[str, Any]] = []
    for item in acumulado.values():
        datos_base = datos_material_requerido_pedido(
            item["material"], 1, catalogo=catalogo
        )
        largo_cat = float(datos_base.get("largo") or 0)
        cantidad = max(1, int(item.get("total_slots") or 1))
        datos = datos_material_requerido_pedido(
            item["material"], cantidad, catalogo=catalogo
        )
        filas.append(
            {
                "material": item["material"],
                "cantidad": cantidad,
                "codigo": datos.get("codigo") or "",
                "largo": float(datos.get("largo") or largo_cat),
                "costo": datos.get("costo"),
            }
        )
    filas.sort(key=lambda r: (r["material"], r.get("largo") or 0, r.get("codigo") or ""))
    return filas


def mrl_unit_key(material: str, largo: float, unit_idx: int) -> str:
    mat = str(material or "").strip()
    return f"{mat}::{round(float(largo or 0), 4)}::{int(unit_idx)}"


def expandir_pedido_mrl_unidades(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Desglosa el pedido MRL en unidades comerciales (1 fila = 1 barra a comprar)."""
    unidades: List[Dict[str, Any]] = []
    for fila in agregar_filas_desde_plan(plan):
        mat = str(fila.get("material") or "").strip()
        try:
            largo = float(fila.get("largo") or 0)
        except Exception:
            largo = 0.0
        cant = max(0, int(fila.get("cantidad") or 0))
        codigo = str(fila.get("codigo") or "").strip()
        costo_linea = fila.get("costo")
        for i in range(cant):
            unidades.append(
                {
                    "key": mrl_unit_key(mat, largo, i),
                    "material": mat,
                    "codigo": codigo,
                    "largo": largo,
                    "unit_idx": i + 1,
                    "cant_grupo": cant,
                    "costo": (
                        float(costo_linea) / cant
                        if costo_linea is not None and cant > 0
                        else None
                    ),
                    "nesting_key": None,
                }
            )
    return unidades


def agregar_filas_desde_unidades_mrl(unidades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reagrupa unidades MRL supervivientes para INSERT en material_requerido_ldg."""
    if not unidades:
        return []
    try:
        from catalogo_largos import (
            _cargar_placas_largos_desde_herinox,
            datos_material_requerido_pedido,
        )
    except ImportError:
        return []

    catalogo = _cargar_placas_largos_desde_herinox(solo_disponibles=False)
    conteo: Dict[tuple[str, float, str], int] = {}
    for u in unidades:
        mat = str(u.get("material") or "").strip()
        try:
            largo = float(u.get("largo") or 0)
        except Exception:
            largo = 0.0
        cod = str(u.get("codigo") or "").strip()
        k = (mat, largo, cod)
        conteo[k] = conteo.get(k, 0) + 1

    filas: List[Dict[str, Any]] = []
    for (mat, largo, cod), cant in sorted(conteo.items(), key=lambda x: (x[0][0], x[0][1], x[0][2])):
        datos = datos_material_requerido_pedido(mat, cant, catalogo=catalogo)
        filas.append(
            {
                "material": mat,
                "cantidad": cant,
                "codigo": datos.get("codigo") or cod,
                "largo": float(datos.get("largo") or largo),
                "costo": datos.get("costo"),
            }
        )
    return filas


def previsualizar_pedido_mrl_unidades(
    plan: Dict[str, Any],
    unidades_excluidas: set[str] | None = None,
) -> List[Dict[str, Any]]:
    excl = unidades_excluidas or set()
    todas = expandir_pedido_mrl_unidades(plan)
    return [u for u in todas if u["key"] not in excl]


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
            material_txt,
            cantidad,
            catalogo=catalogo,
        )
        nuevo_codigo = str(datos.get("codigo") or codigo_esperado or "").strip()
        nuevo_costo = datos.get("costo")

        if not nuevo_codigo and nuevo_costo is None:
            continue

        if (
            not forzar_costo
            and tiene_costo
            and codigo_actual == nuevo_codigo
            and codigo_actual == codigo_esperado
            and nuevo_costo is not None
        ):
            continue

        cursor.execute(
            """
            UPDATE material_requerido_ldg
            SET codigo = %s,
                costo = %s,
                updated_at = NOW()
            WHERE id = %s;
            """,
            (
                nuevo_codigo,
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
    barras_excluidas_pedido: set[str] | None = None,
    unidades_excluidas_mrl: set[str] | None = None,
) -> Tuple[bool, str]:
    cursor.execute(
        """
        DELETE FROM material_requerido_ldg
        WHERE TRIM(orden_id) = %s AND tipo_orden = %s
        """,
        (str(orden_id or "").strip(), tipo_orden),
    )
    if unidades_excluidas_mrl is not None:
        todas = expandir_pedido_mrl_unidades(plan)
        excl = unidades_excluidas_mrl or set()
        kept = [u for u in todas if u["key"] not in excl]
        nuevas = agregar_filas_desde_unidades_mrl(kept)
    else:
        nuevas = agregar_filas_desde_plan(plan, barras_excluidas_pedido=barras_excluidas_pedido)
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
