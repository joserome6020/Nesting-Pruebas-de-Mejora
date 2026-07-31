"""Registro interno y auditable de equivalencias Herinox -> ContPAQ.

Este módulo nunca modifica Herinox ni ContPAQ.  La equivalencia vive en
``nestingpro_db`` y se copia como snapshot a cada MRL pendiente.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


ESTATUS_VALIDOS = {"PENDING", "VERIFIED", "REJECTED", "STALE"}


def _codigo(valor: object) -> str:
    return str(valor or "").strip().upper()


def _estandarizar_especificacion(valor: object) -> str:
    return " ".join(str(valor or "").strip().upper().split())


def asegurar_tabla_equivalencias(cursor) -> None:
    """Crea el registro ANS sin alterar catálogos externos."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS material_codigo_equivalencias (
            id BIGSERIAL PRIMARY KEY,
            herinox_codigo VARCHAR(64) NOT NULL,
            codigo_contpaq VARCHAR(64) NOT NULL,
            especificacion_normalizada TEXT NOT NULL DEFAULT '',
            estatus VARCHAR(16) NOT NULL DEFAULT 'PENDING',
            activo BOOLEAN NOT NULL DEFAULT TRUE,
            origen VARCHAR(80) NOT NULL DEFAULT 'MANUAL',
            nota TEXT NULL,
            verificado_por VARCHAR(120) NULL,
            verificado_at TIMESTAMP NULL,
            contpaq_catalog_seen_at TIMESTAMP NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            CONSTRAINT chk_equivalencia_estatus
                CHECK (estatus IN ('PENDING', 'VERIFIED', 'REJECTED', 'STALE'))
        )
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_equivalencia_herinox_activa
        ON material_codigo_equivalencias (UPPER(herinox_codigo))
        WHERE activo
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_equivalencia_contpaq_verificada
        ON material_codigo_equivalencias (UPPER(codigo_contpaq))
        WHERE activo AND estatus = 'VERIFIED'
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_equivalencia_estado
        ON material_codigo_equivalencias (estatus, activo, updated_at DESC)
        """
    )


def registrar_equivalencia(
    cursor,
    *,
    herinox_codigo: str,
    codigo_contpaq: str,
    especificacion: str = "",
    estatus: str = "PENDING",
    origen: str = "MANUAL",
    nota: str = "",
    verificado_por: str = "",
    reemplazar: bool = False,
) -> dict[str, Any]:
    """Registra una equivalencia sin permitir conflictos silenciosos."""
    herinox = _codigo(herinox_codigo)
    contpaq = _codigo(codigo_contpaq)
    estado = _codigo(estatus) or "PENDING"
    if not herinox or not contpaq:
        raise ValueError("herinox_codigo y codigo_contpaq son obligatorios.")
    if estado not in ESTATUS_VALIDOS:
        raise ValueError(f"Estatus de equivalencia inválido: {estado}.")

    cursor.execute(
        """
        SELECT id, herinox_codigo, codigo_contpaq, estatus
        FROM material_codigo_equivalencias
        WHERE activo AND UPPER(herinox_codigo) = UPPER(%s)
        """,
        (herinox,),
    )
    existente_herinox = cursor.fetchone()
    if existente_herinox:
        actual = dict(existente_herinox) if isinstance(existente_herinox, dict) else {
            "id": existente_herinox[0],
            "herinox_codigo": existente_herinox[1],
            "codigo_contpaq": existente_herinox[2],
            "estatus": existente_herinox[3],
        }
        if (
            _codigo(actual.get("codigo_contpaq")) == contpaq
            and _codigo(actual.get("estatus")) == estado
        ):
            return actual
        if not reemplazar:
            raise ValueError(
                f"{herinox} ya tiene equivalencia activa con "
                f"{actual.get('codigo_contpaq')} ({actual.get('estatus')})."
            )
        cursor.execute(
            """
            UPDATE material_codigo_equivalencias
            SET activo = FALSE, updated_at = NOW()
            WHERE id = %s
            """,
            (actual["id"],),
        )

    if estado == "VERIFIED":
        cursor.execute(
            """
            SELECT herinox_codigo
            FROM material_codigo_equivalencias
            WHERE activo
              AND estatus = 'VERIFIED'
              AND UPPER(codigo_contpaq) = UPPER(%s)
            """,
            (contpaq,),
        )
        conflicto = cursor.fetchone()
        if conflicto:
            codigo_conflicto = (
                conflicto.get("herinox_codigo")
                if isinstance(conflicto, dict)
                else conflicto[0]
            )
            if _codigo(codigo_conflicto) != herinox:
                raise ValueError(
                    f"El SKU ContPAQ {contpaq} ya está verificado para "
                    f"{codigo_conflicto}; revise la equivalencia."
                )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cursor.execute(
        """
        INSERT INTO material_codigo_equivalencias (
            herinox_codigo, codigo_contpaq, especificacion_normalizada,
            estatus, activo, origen, nota, verificado_por, verificado_at,
            contpaq_catalog_seen_at, created_at, updated_at
        )
        VALUES (%s, %s, %s, %s, TRUE, %s, %s, %s, %s, %s, NOW(), NOW())
        RETURNING id, herinox_codigo, codigo_contpaq, estatus, origen,
                  verificado_por, verificado_at, contpaq_catalog_seen_at
        """,
        (
            herinox,
            contpaq,
            _estandarizar_especificacion(especificacion),
            estado,
            str(origen or "MANUAL").strip().upper(),
            str(nota or "").strip() or None,
            str(verificado_por or "").strip() or None,
            now if estado == "VERIFIED" else None,
            now if estado == "VERIFIED" else None,
        ),
    )
    fila = cursor.fetchone()
    return dict(fila) if isinstance(fila, dict) else {
        "id": fila[0],
        "herinox_codigo": fila[1],
        "codigo_contpaq": fila[2],
        "estatus": fila[3],
        "origen": fila[4],
        "verificado_por": fila[5],
        "verificado_at": fila[6],
        "contpaq_catalog_seen_at": fila[7],
    }


# Equivalencias auditadas donde el SKU ContPAQi NO coincide con el código Herinox.
# Los códigos idénticos se resuelven en runtime vía catálogo ContPAQi (match directo).
_SEMILLAS_EQUIVALENCIA_DISTINTA: tuple[dict[str, str], ...] = (
    {
        "herinox_codigo": "HR164",
        "codigo_contpaq": "TUB010",
        "especificacion": "TUBO A36 CED 40 5.0 IN A 20 FT",
        "nota": "Verificado contra admProductos; SKU TUB010 con movimientos históricos.",
    },
    {
        "herinox_codigo": "HR166",
        "codigo_contpaq": "TUB017",
        "especificacion": "TUBO A36 CED 40 2.0 IN A 20 FT",
        "nota": "Alta ContPAQi TUB017 (CED 40 2.0 IN); Herinox mantiene HR166.",
    },
)


def sembrar_equivalencias_verificadas(cursor) -> None:
    """Siembra equivalencias distintas Herinox→ContPAQi sin pisar las ya activas."""
    asegurar_tabla_equivalencias(cursor)
    for semilla in _SEMILLAS_EQUIVALENCIA_DISTINTA:
        herinox = _codigo(semilla["herinox_codigo"])
        cursor.execute(
            """
            SELECT 1
            FROM material_codigo_equivalencias
            WHERE activo AND UPPER(herinox_codigo) = UPPER(%s)
            LIMIT 1
            """,
            (herinox,),
        )
        if cursor.fetchone():
            continue
        registrar_equivalencia(
            cursor,
            herinox_codigo=herinox,
            codigo_contpaq=semilla["codigo_contpaq"],
            especificacion=semilla.get("especificacion") or "",
            estatus="VERIFIED",
            origen="CATALOGO_CONTPAQ_AUDITADO",
            nota=semilla.get("nota") or "",
            verificado_por="ANS_MIGRATION",
        )


def resolver_equivalencia(cursor, herinox_codigo: str) -> dict[str, Any]:
    """Devuelve únicamente una equivalencia activa; nunca adivina un SKU."""
    herinox = _codigo(herinox_codigo)
    if not herinox:
        return {
            "herinox_codigo": "",
            "codigo_contpaq": None,
            "estatus": "PENDING",
            "mapping_id": None,
            "origen": None,
        }
    cursor.execute(
        """
        SELECT id, herinox_codigo, codigo_contpaq, estatus, origen,
               especificacion_normalizada, verificado_at, contpaq_catalog_seen_at
        FROM material_codigo_equivalencias
        WHERE activo AND UPPER(herinox_codigo) = UPPER(%s)
        ORDER BY id DESC
        LIMIT 1
        """,
        (herinox,),
    )
    fila = cursor.fetchone()
    if not fila:
        return {
            "herinox_codigo": herinox,
            "codigo_contpaq": None,
            "estatus": "PENDING",
            "mapping_id": None,
            "origen": None,
        }
    data = dict(fila) if isinstance(fila, dict) else {
        "mapping_id": fila[0],
        "herinox_codigo": fila[1],
        "codigo_contpaq": fila[2],
        "estatus": fila[3],
        "origen": fila[4],
        "especificacion_normalizada": fila[5],
        "verificado_at": fila[6],
        "contpaq_catalog_seen_at": fila[7],
    }
    data["mapping_id"] = data.pop("id", data.get("mapping_id"))
    data["herinox_codigo"] = _codigo(data.get("herinox_codigo"))
    data["codigo_contpaq"] = _codigo(data.get("codigo_contpaq")) or None
    data["estatus"] = _codigo(data.get("estatus")) or "PENDING"
    return data


def mapeo_es_verificado(mapeo: dict[str, Any] | None) -> bool:
    return bool(
        mapeo
        and _codigo(mapeo.get("estatus")) == "VERIFIED"
        and _codigo(mapeo.get("codigo_contpaq"))
    )


def registrar_matches_directos_catalogo(
    cursor,
    resultados_catalogo: dict[str, dict[str, Any]],
    *,
    verificado_por: str = "ANS_CATALOG_EXACT",
) -> dict[str, Any]:
    """
    Si ContPAQi tiene el mismo código que Herinox, registra VERIFIED 1:1.

    No inventa SKUs distintos: solo acepta ``codigo_herinox == codigo_contpaq``
    cuando el catálogo confirma ``existe=True``.
    """
    registradas = 0
    omitidas = 0
    errores: list[str] = []
    for codigo, resultado in (resultados_catalogo or {}).items():
        herinox = _codigo(codigo)
        if not herinox or not bool((resultado or {}).get("existe")):
            omitidas += 1
            continue
        mapeo = resolver_equivalencia(cursor, herinox)
        if mapeo_es_verificado(mapeo):
            omitidas += 1
            continue
        try:
            registrar_equivalencia(
                cursor,
                herinox_codigo=herinox,
                codigo_contpaq=herinox,
                especificacion=str((resultado or {}).get("descripcion") or ""),
                estatus="VERIFIED",
                origen="CONTPAQ_CATALOGO_EXACTO",
                nota="SKU Herinox idéntico confirmado en ContPAQi.",
                verificado_por=verificado_por,
                reemplazar=True,
            )
            registradas += 1
        except ValueError as exc:
            errores.append(f"{herinox}: {exc}")
            omitidas += 1
    return {
        "registradas": registradas,
        "omitidas": omitidas,
        "errores": errores,
    }


def resolver_codigo_contpaq(
    cursor,
    herinox_codigo: str,
    *,
    resultados_catalogo: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Resolución en dos pasos:

    1. Equivalencia VERIFIED registrada (p. ej. HR166→TUB017).
    2. Match directo: mismo código existe en ContPAQi (vía ``resultados_catalogo``).
    """
    mapeo = resolver_equivalencia(cursor, herinox_codigo)
    if mapeo_es_verificado(mapeo):
        return mapeo

    herinox = _codigo(herinox_codigo)
    producto = (resultados_catalogo or {}).get(herinox) or {}
    if herinox and bool(producto.get("existe")):
        registrar_matches_directos_catalogo(
            cursor,
            {herinox: producto},
        )
        mapeo = resolver_equivalencia(cursor, herinox)
    return mapeo


def actualizar_verificacion_catalogo(
    cursor,
    resultados_catalogo: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """Guarda el último avistamiento del catálogo sin tocar ContPAQ."""
    existentes = 0
    stale = 0
    for codigo, resultado in (resultados_catalogo or {}).items():
        sku = _codigo(codigo)
        if not sku:
            continue
        existe = bool(resultado.get("existe"))
        cursor.execute(
            """
            UPDATE material_codigo_equivalencias
            SET contpaq_catalog_seen_at = NOW(),
                estatus = CASE
                    WHEN %s THEN estatus
                    WHEN estatus = 'VERIFIED' THEN 'STALE'
                    ELSE estatus
                END,
                updated_at = NOW()
            WHERE activo AND UPPER(codigo_contpaq) = UPPER(%s)
            """,
            (existe, sku),
        )
        if existe:
            existentes += cursor.rowcount
        else:
            stale += cursor.rowcount
    return {"catalogo_encontrado": existentes, "catalogo_stale": stale}


def sincronizar_codigos_contpaq_mrl(
    cursor,
    *,
    orden_id: str | None = None,
    tipo_orden: str | None = None,
    resultados_catalogo: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Copia mappings a MRL que aún no tiene PO ni movimiento operativo.

    Resolución por fila:
    1. Equivalencia VERIFIED (códigos distintos, p. ej. HR166→TUB017).
    2. Match directo en ContPAQi (mismo código confirmado en ``resultados_catalogo``).

    No modifica snapshots comprometidos, aunque cambie una equivalencia.
    """
    filtros = [
        "po_estatus IS NULL",
        "NOT COALESCE(kit_recibido, FALSE)",
        "provider_handshake_at IS NULL",
        "almacen_received_at IS NULL",
        "incoming_handshake_at IS NULL",
        "NOT COALESCE(rechazado_incoming, FALSE)",
    ]
    valores: list[Any] = []
    if orden_id:
        filtros.append("BTRIM(orden_id) = %s")
        valores.append(str(orden_id).strip())
    if tipo_orden:
        filtros.append("tipo_orden = %s")
        valores.append(str(tipo_orden).strip().upper())
    cursor.execute(
        f"""
        SELECT id, codigo, codigo_herinox
        FROM material_requerido_ldg
        WHERE {' AND '.join(filtros)}
        ORDER BY id
        """,
        tuple(valores),
    )
    filas = cursor.fetchall() or []

    catalogo = dict(resultados_catalogo or {})
    pendientes_exactos: list[str] = []
    for fila in filas:
        data = dict(fila) if isinstance(fila, dict) else {
            "id": fila[0], "codigo": fila[1], "codigo_herinox": fila[2]
        }
        herinox = _codigo(data.get("codigo_herinox") or data.get("codigo"))
        if not herinox:
            continue
        if not mapeo_es_verificado(resolver_equivalencia(cursor, herinox)):
            pendientes_exactos.append(herinox)

    if pendientes_exactos:
        faltantes = [c for c in sorted(set(pendientes_exactos)) if c not in catalogo]
        if faltantes:
            try:
                from modules.nesting_engine.api_client import verificar_codigos_contpaq

                catalogo.update(verificar_codigos_contpaq(faltantes) or {})
            except Exception:
                pass
        if catalogo:
            registrar_matches_directos_catalogo(cursor, catalogo)

    resultado = {
        "actualizadas": 0,
        "verificadas": 0,
        "pendientes": 0,
        "filas": [],
        "catalogo_consultado": len(catalogo),
    }
    for fila in filas:
        data = dict(fila) if isinstance(fila, dict) else {
            "id": fila[0], "codigo": fila[1], "codigo_herinox": fila[2]
        }
        herinox = _codigo(data.get("codigo_herinox") or data.get("codigo"))
        mapeo = resolver_codigo_contpaq(
            cursor,
            herinox,
            resultados_catalogo=catalogo,
        )
        verificado = mapeo_es_verificado(mapeo)
        codigo_contpaq = mapeo.get("codigo_contpaq") if verificado else None
        estatus = "VERIFIED" if verificado else str(mapeo.get("estatus") or "PENDING")
        cursor.execute(
            """
            UPDATE material_requerido_ldg
            SET codigo_herinox = %s,
                codigo_contpaq = %s,
                codigo_contpaq_estatus = %s,
                codigo_equivalencia_id = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                herinox,
                codigo_contpaq,
                estatus,
                mapeo.get("mapping_id"),
                data["id"],
            ),
        )
        resultado["actualizadas"] += cursor.rowcount
        resultado["verificadas" if verificado else "pendientes"] += 1
        resultado["filas"].append(
            {
                "id": data["id"],
                "herinox_codigo": herinox,
                "codigo_contpaq": codigo_contpaq,
                "estatus": estatus,
                "origen": mapeo.get("origen"),
            }
        )
    return resultado


def auditar_equivalencias_mrl(cursor, *, orden_id: str | None = None) -> list[dict[str, Any]]:
    """Vista de auditoría para pendientes, stale y snapshots comprometidos."""
    filtros = []
    valores: list[Any] = []
    if orden_id:
        filtros.append("BTRIM(m.orden_id) = %s")
        valores.append(str(orden_id).strip())
    where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    cursor.execute(
        f"""
        SELECT
            m.id,
            m.orden_id,
            m.tipo_orden,
            m.material,
            COALESCE(NULLIF(m.codigo_herinox, ''), m.codigo) AS herinox_codigo,
            m.codigo_contpaq,
            COALESCE(m.codigo_contpaq_estatus, 'PENDING') AS codigo_contpaq_estatus,
            m.po_estatus,
            m.kit_recibido,
            m.provider_handshake_at,
            m.almacen_received_at,
            e.origen AS equivalencia_origen,
            e.contpaq_catalog_seen_at
        FROM material_requerido_ldg m
        LEFT JOIN material_codigo_equivalencias e
          ON e.id = m.codigo_equivalencia_id
        {where}
        ORDER BY m.orden_id, m.id
        """,
        tuple(valores),
    )
    filas = cursor.fetchall() or []
    return [dict(fila) if isinstance(fila, dict) else {
        "id": fila[0],
        "orden_id": fila[1],
        "tipo_orden": fila[2],
        "material": fila[3],
        "herinox_codigo": fila[4],
        "codigo_contpaq": fila[5],
        "codigo_contpaq_estatus": fila[6],
        "po_estatus": fila[7],
        "kit_recibido": fila[8],
        "provider_handshake_at": fila[9],
        "almacen_received_at": fila[10],
        "equivalencia_origen": fila[11],
        "contpaq_catalog_seen_at": fila[12],
    } for fila in filas]
