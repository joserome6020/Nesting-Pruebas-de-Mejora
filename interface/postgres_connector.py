import os
import re
import psycopg2
import json

from modules.lista_largos_importer import importar_lista_largos_job
from modules.nesting_engine.efficiency_metrics import (
    allocar_nombre_rtz_sobrante_db,
    allocar_nombre_rtzc_sobrante_db,
    hoja_cuenta_como_rtz,
    hoja_es_sobrante_plasma_compensado,
    hoja_es_sobrante_sin_compra,
    hoja_excluida_de_rtz_sobrante,
    inicializar_contador_rtz_sobrante,
    inicializar_contador_rtzc_sobrante,
)
from modules.nesting_engine.sheet_numbering import iterar_grupos_nesting_ordenados

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


def _aplicar_env_db_config(db_config: dict | None) -> None:
    """Alinea NESTING_DB_* para que api_server/asegurar_tabla usen el mismo host que el export."""
    if not db_config:
        return
    mapping = (
        ("host", "NESTING_DB_HOST"),
        ("port", "NESTING_DB_PORT"),
        ("user", "NESTING_DB_USER"),
        ("password", "NESTING_DB_PASSWORD"),
    )
    for src, env_key in mapping:
        val = db_config.get(src)
        if val is not None and str(val).strip() != "":
            os.environ[env_key] = str(val)
    dbname = db_config.get("database") or db_config.get("dbname")
    if dbname:
        os.environ["NESTING_DB_NAME"] = str(dbname)


def _reportar_pedidos_material(logs: list | None, contexto: str) -> None:
    if not logs:
        print(
            f"[BD][LISTA_LARGOS][ERROR] {contexto}: "
            "no se propagó material_requerido_ldg (0 órdenes detectadas)."
        )
        return
    fallos = [p for p in logs if not p.get("ok")]
    for ped in logs:
        print(
            f"[BD][LISTA_LARGOS] Material requerido "
            f"{ped.get('tipo_orden')} '{ped.get('orden_id')}': "
            f"ok={ped.get('ok')} | {ped.get('mensaje')}"
        )
    if fallos:
        print(
            f"[BD][LISTA_LARGOS][ERROR] {contexto}: "
            f"{len(fallos)} orden(es) sin pedido — revisar lista_largos_job/plan."
        )


def _slug_token(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    return text.strip("_") or "NA"


def _obtener_columnas_reporte_cortes(cursor):
    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'reporte_cortes'
    """)
    return {row[0] for row in cursor.fetchall()}


def _resolver_placa_id_visible(hoja, contador_placas, *, contador_rtz=None, contador_rtzc=None, calibre=None, wo_name=None):
    if hoja_es_sobrante_sin_compra(hoja) and not hoja_excluida_de_rtz_sobrante(hoja):
        nombre_rtz = str(
            hoja.get("sheet_display_name") or hoja.get("placa_id") or ""
        ).strip()
        if nombre_rtz.upper().startswith("RTZC") or nombre_rtz.upper().startswith("RTZ"):
            return nombre_rtz
        if hoja_es_sobrante_plasma_compensado(hoja) and contador_rtzc is not None:
            return allocar_nombre_rtzc_sobrante_db(contador_rtzc, calibre, wo_name, hoja=hoja)
        if contador_rtz is not None:
            return allocar_nombre_rtz_sobrante_db(contador_rtz, calibre, wo_name, hoja=hoja)

    display_forzado = str(hoja.get("sheet_display_name") or "").strip()
    if display_forzado:
        return display_forzado

    codigo_base = str(hoja.get("placa_id", "SIN_ID")).strip() or "SIN_ID"

    if "RTZ" in codigo_base.upper():
        return codigo_base

    contador_placas[codigo_base] = contador_placas.get(codigo_base, 0) + 1
    return f"{codigo_base} P{contador_placas[codigo_base]}"


def _build_sheet_meta(
    *,
    hoja,
    grupo_calibre,
    sheet_seq_global,
    contador_placas,
    contador_rtz,
    contador_rtzc=None,
    nombre_job,
    nombre_wo,
    es_swo,
    ruta_exportacion,
):
    order_label = str(nombre_job if es_swo else nombre_wo).strip()
    thickness_name = str(grupo_calibre).split("_", 1)[0].strip() or "NA"
    es_sobrante_db = hoja_es_sobrante_sin_compra(hoja)

    display_name = _resolver_placa_id_visible(
        hoja,
        contador_placas,
        contador_rtz=contador_rtz,
        contador_rtzc=contador_rtzc,
        calibre=thickness_name,
        wo_name=order_label,
    )

    sheet_seq = int(hoja.get("sheet_seq") or sheet_seq_global)
    sheet_code = str(hoja.get("sheet_code") or f"{order_label}-H{sheet_seq}")

    placa_w = float(hoja.get("placa_w", 0.0) or 0.0)
    placa_h = float(hoja.get("placa_h", 0.0) or 0.0)

    source_nest_name = str(
        hoja.get("source_nest_name")
        or (os.path.basename(ruta_exportacion.rstrip("\\/")) if ruta_exportacion else "")
        or nombre_job
    ).strip()

    nest_instance_id = str(
        hoja.get("nest_instance_id")
        or f"{_slug_token(source_nest_name)}__{int(sheet_seq):03d}"
    )

    default_sheet_uid = (
        f"{_slug_token(order_label)}__"
        f"{_slug_token(thickness_name)}__"
        f"H{int(sheet_seq):03d}__"
        f"{_slug_token(display_name)}"
    )

    if es_sobrante_db:
        sheet_uid = default_sheet_uid
        sheet_display_name_db = display_name
    else:
        sheet_uid = str(hoja.get("sheet_uid") or default_sheet_uid)
        sheet_display_name_db = str(hoja.get("sheet_display_name") or display_name)

    meta = {
        "sheet_uid": sheet_uid,
        "sheet_code": sheet_code,
        "sheet_seq": sheet_seq,
        "sheet_display_name": sheet_display_name_db,
        "plate_group_key": str(hoja.get("plate_group_key") or sheet_uid),
        "placa_ancho_canonico_mm": round(float(hoja.get("placa_ancho_canonico_mm") or placa_w), 2),
        "placa_largo_canonico_mm": round(float(hoja.get("placa_largo_canonico_mm") or placa_h), 2),
        "nest_instance_id": nest_instance_id,
        "source_nest_name": source_nest_name,
        "is_rtz": hoja_cuenta_como_rtz(hoja)
        or (
            not hoja_excluida_de_rtz_sobrante(hoja)
            and str(display_name).upper().startswith("RTZ")
        ),
        "is_rtz_plasma_sobrante": bool(
            hoja.get("is_rtz_plasma_sobrante")
            or hoja_es_sobrante_plasma_compensado(hoja)
        ),
        "rtz_tipo": (
            str(hoja.get("rtz_tipo") or "COMPENSADO")
            if hoja_es_sobrante_plasma_compensado(hoja)
            else None
        ),
    }

    # Sobrante: metadata RTZ en BD; placa_id visible queda como RTZ en memoria.
    if es_sobrante_db:
        hoja.update(meta)
        hoja["placa_id"] = display_name
    else:
        hoja.update(meta)

    return meta

def _safe_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return default


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
    texto = re.sub(r"\s+", " ", texto)
    return texto.upper()

def _obtener_columnas_lista_largos_job(cursor):
    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'lista_largos_job'
    """)
    return {row[0] for row in cursor.fetchall()}


def _asegurar_tabla_lista_largos_swo(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS public.lista_largos_swo (
            id SERIAL PRIMARY KEY,
            super_work_order TEXT NOT NULL,
            job TEXT NOT NULL,
            work_order TEXT NOT NULL,
            qty_lote INTEGER NOT NULL DEFAULT 1,
            factor_wo INTEGER NOT NULL DEFAULT 1,
            source_csv_name TEXT,
            source_csv_path TEXT,
            nombre TEXT NOT NULL,
            clasificacion TEXT,
            largo_in NUMERIC,
            cantidad INTEGER NOT NULL DEFAULT 0,
            cantidad_base INTEGER NOT NULL DEFAULT 0,
            cantidad_job INTEGER NOT NULL DEFAULT 1,
            cantidad_total_job INTEGER NOT NULL DEFAULT 0,
            cantidad_total_swo INTEGER NOT NULL DEFAULT 0,
            estatus TEXT DEFAULT 'PENDIENTE',
            avanzado_por TEXT,
            avanzado_fecha TIMESTAMP,
            creado_el TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Ensure columns exist for older databases
    cursor.execute("ALTER TABLE public.lista_largos_swo ADD COLUMN IF NOT EXISTS estatus TEXT DEFAULT 'PENDIENTE'")
    cursor.execute("ALTER TABLE public.lista_largos_swo ADD COLUMN IF NOT EXISTS avanzado_por TEXT")
    cursor.execute("ALTER TABLE public.lista_largos_swo ADD COLUMN IF NOT EXISTS avanzado_fecha TIMESTAMP")

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_lista_largos_swo_swo
        ON public.lista_largos_swo (super_work_order)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_lista_largos_swo_job_wo
        ON public.lista_largos_swo (job, work_order)
    """)


def _guardar_lista_largos_swo(cursor, nombre_swo, datos_para_db):
    """
    Genera la lista de largos consolidada de una SWO tomando como base
    la tabla lista_largos_job.

    Regla correcta:
    - lista_largos_job ya trae el total del job completo por cantidad de tanques.
    - Para SWO se debe usar la cantidad operativa de cada WO origen.
    - Esa cantidad operativa debe respetar el multiplicador de la WO (X2, X3, etc.).
    - Si qty_lote viene bien, se respeta.
    - Si qty_lote viene en 1 pero la WO dice X2/X3, se toma el mayor.
    """
    columnas = _obtener_columnas_lista_largos_job(cursor)
    if not columnas:
        print("[ERP][LISTA_LARGOS][WARN] No existe lista_largos_job o no tiene columnas disponibles.")
        return 0
    expr_cantidad = "cantidad" if "cantidad" in columnas else "0::integer"
    expr_cantidad_base = "cantidad_base" if "cantidad_base" in columnas else expr_cantidad
    expr_cantidad_job = "cantidad_job" if "cantidad_job" in columnas else "1::integer"
    expr_cantidad_total = "cantidad_total" if "cantidad_total" in columnas else expr_cantidad

    if "job_key" in columnas:
        where_expr = "(job_key = %s OR UPPER(REGEXP_REPLACE(BTRIM(job), '\\s+', ' ', 'g')) = %s)"
        usa_job_key = True
    else:
        where_expr = "UPPER(REGEXP_REPLACE(BTRIM(job), '\\s+', ' ', 'g')) = %s"
        usa_job_key = False

    query_base = f"""
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
            {expr_cantidad_total} AS cantidad_total
        FROM public.lista_largos_job
        WHERE {where_expr}
        ORDER BY
            COALESCE(clasificacion, ''),
            COALESCE(nombre, ''),
            COALESCE(largo_in, 0),
            id
    """

    # Rehacer completa la SWO actual para no dejar basura de corridas previas
    cursor.execute(
        "DELETE FROM public.lista_largos_swo WHERE super_work_order = %s",
        (nombre_swo,)
    )

    # Dedupe por (job, wo_orig), conservando la mejor cantidad operativa
    wos_unicas = {}

    for fila in (datos_para_db or []):
        try:
            (
                swo_m, qty_lote, sheet_uid, largo, ancho, espesor, precio, area_t,
                cliente, producto, job, wo_orig, area_asig, pct_a, costo_a, scrap, costo_s
            ) = fila
        except Exception:
            continue

        job = str(job or "").strip()
        wo_orig = str(wo_orig or "").strip()

        if not job or not wo_orig:
            continue

        qty_lote_int = max(1, _safe_int(qty_lote, 1))
        factor_wo = max(1, _extraer_factor_wo(wo_orig))

        # Esta es la clave del arreglo:
        # usamos el mayor entre qty_lote y el factor real detectado en la WO.
        # Así no subcontamos cuando qty_lote viene en 1 pero la WO es X2/X3.
        qty_wo_efectiva = max(qty_lote_int, factor_wo)

        clave = (job, wo_orig)

        if clave not in wos_unicas:
            wos_unicas[clave] = {
                "qty_lote_original": qty_lote_int,
                "factor_wo": factor_wo,
                "qty_wo_efectiva": qty_wo_efectiva,
            }
        else:
            anterior = wos_unicas[clave]
            anterior["qty_lote_original"] = max(anterior["qty_lote_original"], qty_lote_int)
            anterior["factor_wo"] = max(anterior["factor_wo"], factor_wo)
            anterior["qty_wo_efectiva"] = max(anterior["qty_wo_efectiva"], qty_wo_efectiva)

    total_insertados = 0

    for (job, wo_orig), meta in wos_unicas.items():
        job_key = _normalizar_job_key(job)

        if usa_job_key:
            cursor.execute(query_base, (job_key, job_key))
        else:
            cursor.execute(query_base, (job_key,))

        filas_base = cursor.fetchall() or []

        if not filas_base:
            print(f"[ERP][LISTA_LARGOS][WARN] El job '{job}' no tiene registros en lista_largos_job.")
            continue

        qty_lote_original = meta["qty_lote_original"]
        factor_wo = meta["factor_wo"]
        qty_wo_efectiva = meta["qty_wo_efectiva"]

        if qty_lote_original != factor_wo:
            print(
                f"[ERP][LISTA_LARGOS][WARN] WO '{wo_orig}' -> "
                f"qty_lote={qty_lote_original}, factor_texto={factor_wo}. "
                f"Se usará qty_wo_efectiva={qty_wo_efectiva}."
            )

        for row in filas_base:
            source_csv_name = row[1]
            source_csv_path = row[2]
            nombre = row[3]
            clasificacion = row[4]
            largo_in = row[5]

            cantidad_fallback = _safe_int(row[6], 0)
            cantidad_base = _safe_int(row[7], cantidad_fallback)
            cantidad_job = max(1, _safe_int(row[8], 1))
            cantidad_total_job = _safe_int(row[9], cantidad_base * cantidad_job)

            # Correcto para SWO:
            # usar la cantidad operativa de la WO origen, no el total del job.
            cantidad_total_swo = cantidad_base * qty_wo_efectiva

            cursor.execute("""
                INSERT INTO public.lista_largos_swo (
                    super_work_order,
                    job,
                    work_order,
                    qty_lote,
                    factor_wo,
                    source_csv_name,
                    source_csv_path,
                    nombre,
                    clasificacion,
                    largo_in,
                    cantidad,
                    cantidad_base,
                    cantidad_job,
                    cantidad_total_job,
                    cantidad_total_swo
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
            """, (
                nombre_swo,
                job,
                wo_orig,
                qty_wo_efectiva,   # guardamos la cantidad efectiva usada
                factor_wo,
                source_csv_name,
                source_csv_path,
                nombre,
                clasificacion,
                largo_in,
                cantidad_total_swo,
                cantidad_base,
                cantidad_job,
                cantidad_total_job,
                cantidad_total_swo,
            ))

            total_insertados += 1

    print(
        f"[ERP][LISTA_LARGOS] SWO '{nombre_swo}' -> "
        f"{total_insertados} registros guardados en lista_largos_swo."
    )
    return total_insertados

def _asegurar_tablas_pqart(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS public.pqart_wo (
            id SERIAL PRIMARY KEY,
            nombre_wo TEXT NOT NULL,
            nombre_dxf TEXT NOT NULL,
            ruta TEXT NOT NULL,
            tipo_corte VARCHAR(20) NOT NULL,
            procesado_con_swo VARCHAR(20) NOT NULL DEFAULT 'NO procesado',
            ls TEXT NOT NULL DEFAULT 'Pendiente',
            sheet_uid TEXT,
            sheet_code TEXT,
            sheet_display_name TEXT,
            origen_proceso_pqart VARCHAR(20) NOT NULL DEFAULT 'Pendiente',
            CONSTRAINT chk_pqart_wo_origen_proceso
                CHECK (origen_proceso_pqart IN ('Pendiente', 'Servidor', 'Local', 'Ambas')),
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT chk_pqart_wo_tipo_corte
                CHECK (tipo_corte IN ('CamaLaser', 'RobotLaser', 'Plasma')),
            CONSTRAINT chk_pqart_wo_procesado_swo
                CHECK (procesado_con_swo IN ('Procesado', 'NO procesado'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS public.pqart_swo (
            id SERIAL PRIMARY KEY,
            nombre_swo TEXT NOT NULL,
            nombre_dxf TEXT NOT NULL,
            ruta TEXT NOT NULL,
            tipo_corte VARCHAR(20) NOT NULL,
            ls TEXT NOT NULL DEFAULT 'Pendiente',
            sheet_uid TEXT,
            sheet_code TEXT,
            sheet_display_name TEXT,
            origen_proceso_pqart VARCHAR(20) NOT NULL DEFAULT 'Pendiente',
            CONSTRAINT chk_pqart_swo_origen_proceso
                CHECK (origen_proceso_pqart IN ('Pendiente', 'Servidor', 'Local', 'Ambas')),
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT chk_pqart_swo_tipo_corte
                CHECK (tipo_corte IN ('CamaLaser', 'RobotLaser', 'Plasma'))
        )
    """)

    cursor.execute("""
        ALTER TABLE public.pqart_wo
        ADD COLUMN IF NOT EXISTS origen_proceso_pqart VARCHAR(20) NOT NULL DEFAULT 'Pendiente'
    """)

    cursor.execute("""
        ALTER TABLE public.pqart_swo
        ADD COLUMN IF NOT EXISTS origen_proceso_pqart VARCHAR(20) NOT NULL DEFAULT 'Pendiente'
    """)

    cursor.execute("""
        ALTER TABLE public.pqart_wo
        DROP CONSTRAINT IF EXISTS chk_pqart_wo_origen_proceso
    """)
    cursor.execute("""
        ALTER TABLE public.pqart_wo
        ADD CONSTRAINT chk_pqart_wo_origen_proceso
        CHECK (origen_proceso_pqart IN ('Pendiente', 'Servidor', 'Local', 'Ambas'))
    """)

    cursor.execute("""
        ALTER TABLE public.pqart_swo
        DROP CONSTRAINT IF EXISTS chk_pqart_swo_origen_proceso
    """)
    cursor.execute("""
        ALTER TABLE public.pqart_swo
        ADD CONSTRAINT chk_pqart_swo_origen_proceso
        CHECK (origen_proceso_pqart IN ('Pendiente', 'Servidor', 'Local', 'Ambas'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pqart_wo_origen_proceso
        ON public.pqart_wo (origen_proceso_pqart)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pqart_swo_origen_proceso
        ON public.pqart_swo (origen_proceso_pqart)
    """)

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_pqart_wo_ruta
        ON public.pqart_wo (ruta)
    """)

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_pqart_swo_ruta
        ON public.pqart_swo (ruta)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pqart_wo_nombre_wo
        ON public.pqart_wo (nombre_wo)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pqart_swo_nombre_swo
        ON public.pqart_swo (nombre_swo)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pqart_wo_procesado_swo
        ON public.pqart_wo (procesado_con_swo)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pqart_wo_ls
        ON public.pqart_wo (ls)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pqart_swo_ls
        ON public.pqart_swo (ls)
    """)


def _iterar_exports_pqart(resultados_motor):
    for grupo_calibre, datos_grupo in (resultados_motor or {}).items():
        if not isinstance(datos_grupo, dict) or "error" in datos_grupo:
            continue

        for hoja in (datos_grupo.get("hojas", []) or []):
            for export_row in (hoja.get("pqart_exports", []) or []):
                ruta = os.path.normpath(str(export_row.get("ruta") or "").strip())
                if not ruta:
                    continue

                yield {
                    "nombre_dxf": str(export_row.get("nombre_dxf") or os.path.basename(ruta)).strip(),
                    "ruta": ruta,
                    "tipo_corte": str(export_row.get("tipo_corte") or "").strip(),
                    "sheet_uid": str(export_row.get("sheet_uid") or "").strip() or None,
                    "sheet_code": str(export_row.get("sheet_code") or "").strip() or None,
                    "sheet_display_name": str(export_row.get("sheet_display_name") or "").strip() or None,
                }


def _guardar_pqart_wo(cursor, nombre_wo, resultados_motor):
    exports = list(_iterar_exports_pqart(resultados_motor))

    cursor.execute("""
        DELETE FROM public.pqart_wo
        WHERE nombre_wo = %s
    """, (nombre_wo,))

    total = 0
    for row in exports:
        cursor.execute("""
            INSERT INTO public.pqart_wo (
                nombre_wo,
                nombre_dxf,
                ruta,
                tipo_corte,
                procesado_con_swo,
                ls,
                sheet_uid,
                sheet_code,
                sheet_display_name,
                origen_proceso_pqart,
                created_at,
                updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """, (
            nombre_wo,
            row["nombre_dxf"],
            row["ruta"],
            row["tipo_corte"],
            "NO procesado",
            "Pendiente",
            row["sheet_uid"],
            row["sheet_code"],
            row["sheet_display_name"],
            "Pendiente",
        ))
        total += 1

    print(f"[PQART][WO] WO '{nombre_wo}' -> {total} DXF(s) registrados.")
    return total


def _guardar_pqart_swo(cursor, nombre_swo, resultados_motor):
    exports = list(_iterar_exports_pqart(resultados_motor))

    cursor.execute("""
        DELETE FROM public.pqart_swo
        WHERE nombre_swo = %s
    """, (nombre_swo,))

    total = 0
    for row in exports:
        cursor.execute("""
            INSERT INTO public.pqart_swo (
                nombre_swo,
                nombre_dxf,
                ruta,
                tipo_corte,
                ls,
                sheet_uid,
                sheet_code,
                sheet_display_name,
                origen_proceso_pqart,
                created_at,
                updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """, (
            nombre_swo,
            row["nombre_dxf"],
            row["ruta"],
            row["tipo_corte"],
            "Pendiente",
            row["sheet_uid"],
            row["sheet_code"],
            row["sheet_display_name"],
            "Pendiente",
        ))
        total += 1

    print(f"[PQART][SWO] SWO '{nombre_swo}' -> {total} DXF(s) registrados.")
    return total


def _marcar_wos_como_procesadas_en_pqart(cursor, wos_origen):
    wos_limpias = sorted({
        str(wo or "").strip()
        for wo in (wos_origen or set())
        if str(wo or "").strip()
    })

    if not wos_limpias:
        return 0

    cursor.execute("""
        UPDATE public.pqart_wo
        SET procesado_con_swo = 'Procesado',
            updated_at = CURRENT_TIMESTAMP
        WHERE nombre_wo = ANY(%s)
    """, (wos_limpias,))

    print(f"[PQART][WO] {cursor.rowcount} fila(s) marcadas como Procesado por SWO.")
    return cursor.rowcount

THICKNESS_TO_NOMINAL = {
    0.0590: "16",
    0.0750: "14",
    0.1050: "12",
    0.1180: "11",
    0.1875: "3/16",
    0.1880: "3/16",
    0.2500: "1/4",
    0.3125: "5/16",
    0.3130: "5/16",
    0.3750: "3/8",
    0.5000: "1/2",
    0.6250: "5/8",
    0.7500: "3/4",
    1.0000: "1",
    1.2500: "1 1/4",
}

def _mm_to_in(valor_mm, decimales=3):
    try:
        return round(float(valor_mm) / 25.4, decimales)
    except Exception:
        return 0.0

def _extraer_base_calibre(calibre_texto):
    """
    De '0.105_CARBONO' -> '0.105'
    De '16_CARBONO' -> '16'
    """
    base = str(calibre_texto or "").strip().split("_", 1)[0].strip().upper()
    base = (
        base.replace("CAL.", "")
            .replace("CAL", "")
            .replace("GA.", "")
            .replace("GA", "")
            .strip()
    )
    return base

def _normalizar_calibre_nominal(calibre_texto):
    """
    Regresa un valor global/comercial limpio:
    0.059 -> 16
    0.105 -> 12
    0.118 -> 11
    0.188 -> 3/16
    0.25  -> 1/4
    etc.
    Si ya viene nominal (10, 11, 14, 16, 18), lo respeta.
    """
    base = _extraer_base_calibre(calibre_texto)

    if not base:
        return None

    # Si ya viene nominal entero, se respeta tal cual
    if re.fullmatch(r"\d+", base):
        valor_entero = int(base)
        if valor_entero >= 7:
            return str(valor_entero)

    # Si ya viene fracción nominal, se respeta
    if "/" in base:
        return base

    # Si viene decimal, se normaliza
    try:
        valor = round(float(base), 4)
    except Exception:
        return base

    # Match exacto o casi exacto
    for espesor, nominal in THICKNESS_TO_NOMINAL.items():
        if abs(valor - espesor) <= 0.003:
            return nominal

    # Fallback: aproximación al más cercano
    espesor_mas_cercano = min(
        THICKNESS_TO_NOMINAL.keys(),
        key=lambda x: abs(x - valor)
    )

    if abs(valor - espesor_mas_cercano) <= 0.03:
        return THICKNESS_TO_NOMINAL[espesor_mas_cercano]

    # Último fallback: guardar el decimal limpio
    return str(round(valor, 3))

# CAMBIO 1: Se agregó ruta_exportacion="" a los parámetros de la función
def guardar_nesting_en_postgresql(nombre_job, nombre_wo, resultados_motor, db_config, estado_3d="PENDIENTE", ruta_exportacion="", limpiar_previos=True):
    print(f"\n[BD] Iniciando guardado ESTRUCTURADO para el Job: {nombre_job}...")
    
    try:
        conexion = psycopg2.connect(**db_config)
        cursor = conexion.cursor()
        piezas_guardadas = 0
        global_sheet_counter = 0
        contador_rtz_sobrante = inicializar_contador_rtz_sobrante(resultados_motor)
        contador_rtzc_sobrante = inicializar_contador_rtzc_sobrante(resultados_motor)

        print("[PQART][DEBUG] Resumen de hojas antes de guardar:")
        for grupo_calibre, datos_grupo in (resultados_motor or {}).items():
            if not isinstance(datos_grupo, dict):
                print(f"  grupo={grupo_calibre} -> datos_grupo inválido: {type(datos_grupo)}")
                continue

            if "error" in datos_grupo:
                print(f"  grupo={grupo_calibre} -> grupo con error, se omite debug de hojas")
                continue

            for i, hoja in enumerate((datos_grupo.get("hojas", []) or []), start=1):
                exports = hoja.get("pqart_exports", []) or []
                print(f"  grupo={grupo_calibre} hoja={i} exports={len(exports)}")

        print("[DEBUG] Executing _obtener_columnas_reporte_cortes...")
        wos_origen_swo = set()

        columnas_existentes = _obtener_columnas_reporte_cortes(cursor)
        usar_columnas_sheet = SHEET_METADATA_COLUMNS.issubset(columnas_existentes)
        print("[DEBUG] Done _obtener_columnas_reporte_cortes.")

        # =======================================================
        # MAGIA SWO: Detectamos si es una Súper Orden
        # =======================================================
        es_swo = "S.W.O" in nombre_job.upper() or "SWO" in nombre_job.upper()
        job_original = nombre_job

        if es_swo:
            print("[DEBUG] Executing SELECT job FROM reporte_cortes...")
            cursor.execute("SELECT job FROM reporte_cortes WHERE super_work_order = %s LIMIT 1", (nombre_job,))
            res_job = cursor.fetchone()
            if res_job and res_job[0]:
                job_original = res_job[0]
            print("[DEBUG] Done SELECT job.")

            if limpiar_previos:
                print("[DEBUG] Executing DELETE FROM reporte_cortes...")
                cursor.execute(
                    "DELETE FROM reporte_cortes WHERE super_work_order = %s AND estatus ILIKE 'Pendiente%%'",
                    (nombre_job,)
                )
                print("[BD] S.W.O. Detectada: Se limpiaron los registros individuales previos para insertar el layout maestro.")


        for grupo_calibre, datos_grupo in iterar_grupos_nesting_ordenados(resultados_motor):
            if 'error' in datos_grupo:
                continue

            contador_placas = {}

            for indice, hoja in enumerate(datos_grupo.get('hojas', []), start=1):
                global_sheet_counter = int(hoja.get("sheet_seq") or (global_sheet_counter + 1))
                if not hoja.get("sheet_seq"):
                    hoja["sheet_seq"] = global_sheet_counter

                eficiencia = hoja.get('eficiencia', 0.0)
                placa_w = float(hoja.get('placa_w', 0.0) or 0.0)
                placa_h = float(hoja.get('placa_h', 0.0) or 0.0)

                sheet_meta = _build_sheet_meta(
                    hoja=hoja,
                    grupo_calibre=grupo_calibre,
                    sheet_seq_global=global_sheet_counter,
                    contador_placas=contador_placas,
                    contador_rtz=contador_rtz_sobrante,
                    contador_rtzc=contador_rtzc_sobrante,
                    nombre_job=nombre_job,
                    nombre_wo=nombre_wo,
                    es_swo=es_swo,
                    ruta_exportacion=ruta_exportacion,
                )

                placa_id_final = sheet_meta["sheet_display_name"]

                for pieza in hoja.get('piezas', []):
                    item_name_crudo = pieza['nombre']
                    es_pieza_especial = (
                        item_name_crudo.startswith("REMANENTE")
                        or item_name_crudo.startswith("RETAZO")
                        or item_name_crudo.startswith("TATUAJE")
                        or item_name_crudo.startswith("REF__")
                    )

                    if es_pieza_especial:
                        continue

                    exterior = [{"x": round(pt[0], 2), "y": round(pt[1], 2)} for pt in pieza['poligonos'][0]] if pieza.get('poligonos') else []

                    agujeros = []
                    if len(pieza.get('poligonos', [])) > 1:
                        for agujero in pieza['poligonos'][1:]:
                            agujeros.append(
                                [{"x": round(pt[0], 2), "y": round(pt[1], 2)} for pt in agujero]
                            )

                    lineas_marcas = []
                    for marca in pieza.get('marcas', []):
                        lineas_marcas.append(
                            [{"x": round(pt[0], 2), "y": round(pt[1], 2)} for pt in marca]
                        )

                    geometria_payload = {
                        "exterior": exterior,
                        "agujeros": agujeros,
                        "marcas": lineas_marcas,
                        "limites_placa": {
                            "ancho": round(placa_w, 2),
                            "largo": round(placa_h, 2),
                        },
                        "sheet_meta": {
                            "sheet_uid": sheet_meta["sheet_uid"],
                            "sheet_code": sheet_meta["sheet_code"],
                            "sheet_seq": sheet_meta["sheet_seq"],
                            "sheet_display_name": sheet_meta["sheet_display_name"],
                            "plate_group_key": sheet_meta["plate_group_key"],
                            "placa_ancho_canonico_mm": sheet_meta["placa_ancho_canonico_mm"],
                            "placa_largo_canonico_mm": sheet_meta["placa_largo_canonico_mm"],
                            "nest_instance_id": sheet_meta["nest_instance_id"],
                            "source_nest_name": sheet_meta["source_nest_name"],
                            "is_rtz": sheet_meta["is_rtz"],
                            "is_rtz_plasma_sobrante": sheet_meta.get("is_rtz_plasma_sobrante", False),
                            "rtz_tipo": sheet_meta.get("rtz_tipo"),
                        }
                    }
                    geometria_json = json.dumps(geometria_payload, ensure_ascii=False)

                    if exterior:
                        xs = [p['x'] for p in exterior]
                        ys = [p['y'] for p in exterior]
                        ancho = round(max(xs) - min(xs), 2)
                        largo = round(max(ys) - min(ys), 2)
                    else:
                        ancho, largo = 0.0, 0.0

                    ancho_in = _mm_to_in(sheet_meta.get("placa_ancho_canonico_mm", 0.0))
                    largo_in = _mm_to_in(sheet_meta.get("placa_largo_canonico_mm", 0.0))
                    calibre_nominal = _normalizar_calibre_nominal(grupo_calibre)

                    # =======================================================
                    # IDENTIDAD COMERCIAL / OPERATIVA ACTUAL
                    # =======================================================
                    if es_swo:
                        swo_insert = nombre_job
                        estatus_insert = "Pendiente SWO"

                        if "__" in item_name_crudo:
                            wo_original_pieza = item_name_crudo.split("__")[0].strip()
                            item_name_limpio = item_name_crudo.split("__")[1].strip()
                            wos_origen_swo.add(wo_original_pieza)

                            cursor.execute(
                                "SELECT job_numero FROM diccionario_swo WHERE prefijo_carpeta ILIKE %s ORDER BY id DESC LIMIT 1",
                                (wo_original_pieza,)
                            )
                            reg_job = cursor.fetchone()

                            if not reg_job:
                                cursor.execute("""
                                    SELECT j.job_number
                                    FROM erp_work_orders w
                                    JOIN erp_jobs j ON w.id_job = j.id_job
                                    WHERE w.nombre_wo ILIKE %s
                                    ORDER BY w.id_wo DESC LIMIT 1
                                """, (wo_original_pieza,))
                                reg_job = cursor.fetchone()

                            job_insert = reg_job[0] if reg_job else job_original
                            work_order_insert = wo_original_pieza
                        else:
                            job_insert = job_original
                            work_order_insert = f"{nombre_job} (PIEZAS_BASE)"
                            item_name_limpio = item_name_crudo
                    else:
                        job_insert = nombre_job
                        work_order_insert = nombre_wo
                        swo_insert = None
                        estatus_insert = "Pendiente"
                        item_name_limpio = (
                            item_name_crudo.replace("_PLASMA", "")
                            .replace("_TAB", "")
                            .replace("_OXI", "")
                            .strip()
                        )

                    if usar_columnas_sheet:
                        query = """
                            INSERT INTO reporte_cortes
                            (
                                job, work_order, calibre, calibre_nominal, placa_id, eficiencia, item, mark, clase,
                                ancho, largo, ancho_in, largo_in, estatus, geometria, super_work_order, origen_nesting,
                                estado_3d, ruta_exportacion,
                                sheet_uid, sheet_code, sheet_seq, sheet_display_name, plate_group_key,
                                placa_ancho_canonico_mm, placa_largo_canonico_mm,
                                nest_instance_id, source_nest_name, is_rtz
                            )
                            VALUES
                            (
                                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s::jsonb, %s, %s,
                                %s, %s,
                                %s, %s, %s, %s, %s,
                                %s, %s,
                                %s, %s, %s
                            )
                        """
                        valores = (
                            job_insert, work_order_insert, grupo_calibre, calibre_nominal, placa_id_final, eficiencia,
                            item_name_limpio, "M-01", "A",
                            ancho, largo, ancho_in, largo_in, estatus_insert, geometria_json, swo_insert, "INGENIERIA",
                            estado_3d, ruta_exportacion,
                            sheet_meta["sheet_uid"],
                            sheet_meta["sheet_code"],
                            sheet_meta["sheet_seq"],
                            sheet_meta["sheet_display_name"],
                            sheet_meta["plate_group_key"],
                            sheet_meta["placa_ancho_canonico_mm"],
                            sheet_meta["placa_largo_canonico_mm"],
                            sheet_meta["nest_instance_id"],
                            sheet_meta["source_nest_name"],
                            sheet_meta["is_rtz"],
                        )
                    else:
                        # Fallback seguro: no rompe si aún no corres la migración SQL
                        query = """
                            INSERT INTO reporte_cortes
                            (
                                job, work_order, calibre, calibre_nominal, placa_id, eficiencia, item, mark, clase,
                                ancho, largo, ancho_in, largo_in, estatus, geometria, super_work_order, origen_nesting,
                                estado_3d, ruta_exportacion
                            )
                            VALUES
                            (
                                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s::jsonb, %s, %s,
                                %s, %s
                            )
                        """
                        valores = (
                            job_insert, work_order_insert, grupo_calibre, calibre_nominal, placa_id_final, eficiencia,
                            item_name_limpio, "M-01", "A",
                            ancho, largo, ancho_in, largo_in, estatus_insert, geometria_json, swo_insert, "INGENIERIA",
                            estado_3d, ruta_exportacion
                        )

                    cursor.execute(query, valores)
                    piezas_guardadas += 1

        if es_swo:
            _guardar_pqart_swo(cursor, nombre_job, resultados_motor)
            _marcar_wos_como_procesadas_en_pqart(cursor, wos_origen_swo)
        else:
            _guardar_pqart_wo(cursor, nombre_wo, resultados_motor)

        conexion.commit()
        print(f"[BD] ¡ÉXITO! Se guardaron {piezas_guardadas} piezas. Estado 3D: {estado_3d}. Ruta inyectada.")

        # =======================================================
        # IMPORTACIÓN NO BLOQUEANTE DE LISTA DE LARGOS POR JOB
        # =======================================================
        print(
            f"[DEBUG][LISTA_LARGOS] es_swo={es_swo} | "
            f"job_original={job_original} | "
            f"ruta_exportacion={ruta_exportacion!r}"
        )

        if not es_swo and ruta_exportacion:
            _aplicar_env_db_config(db_config)
            try:
                print("[DEBUG][LISTA_LARGOS] Entrando a importar_lista_largos_job(...)")
                resultado_largos = importar_lista_largos_job(
                    job=job_original,
                    ruta_exportacion=ruta_exportacion,
                    db_config=db_config,
                    work_order_alcance=str(nombre_wo or "").strip() or None,
                    propagar_material=False,
                )
                print(f"[BD][LISTA_LARGOS] Resultado importación: {resultado_largos}")
                pedidos = resultado_largos.get("pedidos_material")
                if pedidos:
                    _reportar_pedidos_material(
                        pedidos,
                        f"import job='{job_original}'",
                    )
            except Exception as e:
                print(
                    f"[BD][LISTA_LARGOS][WARN] No se pudo importar la lista de largos "
                    f"del job '{job_original}': {e}"
                )

            # material_requerido_ldg se aplica desde NESTEO DE LARGOS al exportar
            # (plan calculado con el nesting + exclusiones elegidas por el usuario).
            print(
                f"[BD][LISTA_LARGOS] Sin propagación automática a material_requerido_ldg "
                f"(job='{job_original}' wo='{nombre_wo}')."
            )
        elif es_swo and db_config:
            print(
                f"[BD][LISTA_LARGOS] SWO '{nombre_job}': material_requerido_ldg "
                "se aplica al exportar desde el plan de NESTEO DE LARGOS."
            )
        else:
            print(
                f"[DEBUG][LISTA_LARGOS] SKIP importación | "
                f"es_swo={es_swo} | ruta_exportacion={ruta_exportacion!r} | "
                f"db_config={'set' if db_config else 'none'}"
            )

        return True, piezas_guardadas

    except Exception as error:
        print(f"\n[BD] ❌ ERROR CRÍTICO AL GUARDAR EN POSTGRESQL:")
        import traceback
        traceback.print_exc()
        if 'conexion' in locals():
            conexion.rollback()
        return False, repr(error)

    finally:
        if 'conexion' in locals() and conexion:
            cursor.close()
            conexion.close()

def guardar_tracking_erp(nombre_swo, datos_para_db, piezas_detalladas_db, db_config, datos_financieros=None):
    """
    Inyecta información en las Tablas Maestras.
    Fase 1: Guarda pesos crudos.
    Fase 5: Clasifica costos en cubetas WO y SWO (Sin cálculos de MSR).
    """
    if datos_financieros is None: datos_financieros = {}
    cantidad_job = datos_financieros.get("cantidad", 1)
    sstl_val = datos_financieros.get("sstl", 0.0)
    carbon_val = datos_financieros.get("carbon", 0.0)

    print(f"\n[ERP] Iniciando inyección de datos para: {nombre_swo}...")
    try:
        conexion = psycopg2.connect(**db_config)
        cursor = conexion.cursor()
        jobs_procesados = {}     
        wos_procesadas = {}      
        placas_procesadas = {}   

        cursor.execute("SELECT id_swo FROM erp_super_work_orders WHERE nombre_swo = %s", (nombre_swo,))
        res_swo = cursor.fetchone()
        id_swo = res_swo[0] if res_swo else None
        if not id_swo:
            cursor.execute("INSERT INTO erp_super_work_orders (nombre_swo) VALUES (%s) RETURNING id_swo", (nombre_swo,))
            id_swo = cursor.fetchone()[0]

        for fila in datos_para_db:
            (swo_m, qty_lote, sheet_uid, largo, ancho, espesor, precio, area_t, cliente, producto, job, wo_orig, area_asig, pct_a, costo_a, scrap, costo_s) = fila
            
            # --- FASE 1: JOBS + PESOS DEL EXCEL ---
            clave_job = f"{cliente}_{producto}_{job}"
            if clave_job not in jobs_procesados:
                cursor.execute("SELECT id_job FROM erp_jobs WHERE job_number = %s AND cliente = %s", (job, cliente))
                res_job = cursor.fetchone()
                if res_job: 
                    id_job = res_job[0]
                    cursor.execute("""
                        UPDATE erp_jobs SET cantidad_total = %s, sstl = %s, carbon_steel = %s 
                        WHERE id_job = %s
                    """, (cantidad_job, sstl_val, carbon_val, id_job))
                else:
                    cursor.execute("""
                        INSERT INTO erp_jobs (job_number, cliente, producto, cantidad_total, sstl, carbon_steel)
                        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id_job
                    """, (job, cliente, producto, cantidad_job, sstl_val, carbon_val))
                    id_job = cursor.fetchone()[0]
                jobs_procesados[clave_job] = {"id_job": id_job, "job_number": job}
            else: id_job = jobs_procesados[clave_job]["id_job"]

            # --- FASE 2: WORK ORDERS ---
            qty_wo_real = max(1, _safe_int(qty_lote, 1))
            clave_wo = (id_job, wo_orig)

            if clave_wo not in wos_procesadas:
                cursor.execute(
                    "SELECT id_wo FROM erp_work_orders WHERE id_job = %s AND nombre_wo = %s",
                    (id_job, wo_orig)
                )
                res_wo = cursor.fetchone()
                id_wo = res_wo[0] if res_wo else None

                if id_wo:
                    cursor.execute(
                        "UPDATE erp_work_orders SET qty_wo = %s WHERE id_wo = %s",
                        (qty_wo_real, id_wo)
                    )
                else:
                    cursor.execute("""
                        INSERT INTO erp_work_orders (id_job, nombre_wo, qty_wo)
                        VALUES (%s, %s, %s)
                        RETURNING id_wo
                    """, (id_job, wo_orig, qty_wo_real))
                    id_wo = cursor.fetchone()[0]

                wos_procesadas[clave_wo] = id_wo
            else:
                id_wo = wos_procesadas[clave_wo]

            # --- FASE 3: PLACAS ---
            if sheet_uid not in placas_procesadas:
                cursor.execute("SELECT id_placa FROM erp_placas_tracking WHERE id_swo = %s AND codigo_placa = %s", (id_swo, sheet_uid))
                res_placa = cursor.fetchone()
                id_placa = res_placa[0] if res_placa else None
                if not id_placa:
                    cursor.execute("INSERT INTO erp_placas_tracking (id_swo, codigo_placa, qty_placas) VALUES (%s, %s, %s) RETURNING id_placa", (id_swo, sheet_uid, 1))
                    id_placa = cursor.fetchone()[0]
                placas_procesadas[sheet_uid] = id_placa

            # --- FASE 4: PIEZAS ---
            dict_wo_en_placa = piezas_detalladas_db.get(sheet_uid, {}).get(wo_orig, {})
            if dict_wo_en_placa:
                for nombre_real_pieza, cantidad in dict_wo_en_placa.items():
                    cursor.execute("INSERT INTO erp_piezas_tracking (id_placa, id_wo, nombre_componente, qty_requerida) VALUES (%s, %s, %s, %s)", (placas_procesadas[sheet_uid], id_wo, nombre_real_pieza, cantidad))
                piezas_detalladas_db[sheet_uid][wo_orig] = {} 

        # =====================================================================
        # FASE 4.5: LISTA DE LARGOS CONSOLIDADA DE LA SWO
        # =====================================================================
        _guardar_lista_largos_swo(cursor, nombre_swo, datos_para_db)

        # =====================================================================
        # FASE 5: CLASIFICACIÓN FINANCIERA (SUMA POR CUBETAS)
        # =====================================================================
        for clave, data_job in jobs_procesados.items():
            j_id = data_job["id_job"]; j_num = data_job["job_number"]

            cursor.execute("""
                SELECT SUM(costo_asignado + costo_scrap) FROM costos_prorrateo 
                WHERE job = %s AND work_order NOT ILIKE '%%S.W.O%%' AND work_order NOT ILIKE '%%SWO%%'
            """, (j_num,))
            costo_wo = float(cursor.fetchone()[0] or 0.0)

            cursor.execute("""
                SELECT SUM(costo_asignado + costo_scrap) FROM costos_prorrateo 
                WHERE job = %s AND (work_order ILIKE '%%S.W.O%%' OR work_order ILIKE '%%SWO%%')
            """, (j_num,))
            costo_swo = float(cursor.fetchone()[0] or 0.0)

            cursor.execute("SELECT COUNT(DISTINCT id_wo) FROM erp_work_orders WHERE id_job = %s", (j_id,))
            wo_count = cursor.fetchone()[0]

            cursor.execute("""
                UPDATE erp_jobs SET costo_mp_wo = %s, costo_mp_swo = %s, wo_generadas = %s 
                WHERE id_job = %s
            """, (costo_wo, costo_swo, wo_count, j_id))

            print(f"✅ [FINANZAS] Job {j_num} -> WO: ${costo_wo} | SWO: ${costo_swo} | WOs: {wo_count}")

        conexion.commit()
        return True
    except Exception as error:
        print(f"\n[ERP] ❌ ERROR: {error}")
        if 'conexion' in locals(): conexion.rollback()
        return False
    finally:
        if 'conexion' in locals() and conexion: cursor.close(); conexion.close()

# =========================================================================
# DICCIONARIO DE TRAZABILIDAD PARA S.W.O.
# =========================================================================
def registrar_diccionario_swo(swo_id, prefijo, cliente, job, producto, db_config):
    """
    Registra el ADN comercial de una carpeta en la base de datos 
    ANTES de que el motor de Nesting mezcle las piezas.
    """
    try:
        import psycopg2
        conexion = psycopg2.connect(**db_config)
        cursor = conexion.cursor()
        
        # 1. Verificamos si ya existe para no duplicar si le das clic dos veces
        cursor.execute("""
            SELECT id FROM diccionario_swo 
            WHERE swo_id = %s AND prefijo_carpeta = %s
        """, (swo_id, prefijo))
        
        # 2. Si no existe, lo insertamos
        if not cursor.fetchone():
            query = """
                INSERT INTO diccionario_swo (swo_id, prefijo_carpeta, cliente, job_numero, producto)
                VALUES (%s, %s, %s, %s, %s)
            """
            cursor.execute(query, (swo_id, prefijo, cliente, job, producto))
            conexion.commit()
            print(f"[TRACKING] Registrado en diccionario: {prefijo} -> Cliente: {cliente} | Job: {job}")
            
    except Exception as e:
        print(f"⚠️ [ERROR TRACKING] No se pudo registrar en diccionario SWO: {e}")
    finally:
        if 'conexion' in locals() and conexion:
            cursor.close()
            conexion.close()