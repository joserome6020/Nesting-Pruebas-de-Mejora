# -*- coding: utf-8 -*-
"""
Genera documentación en PDF del esquema public de nestingpro_db.

Requisito: tools/schema_snapshot.json (UTF-8), generado con:
  python tools/dump_db_schema_for_pdf.py -o tools/schema_snapshot.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_JSON = Path(__file__).resolve().parent / "schema_snapshot.json"
OUT_PDF = ROOT / "docs" / "nestingpro_db_documentacion.pdf"

# Paleta
C_PRIMARY = colors.HexColor("#1e3a5f")
C_ACCENT = colors.HexColor("#2c5282")
C_BAND = colors.HexColor("#edf2f7")
C_BORDER = colors.HexColor("#cbd5e0")


def _ensure_font() -> str:
    arial = Path(r"C:\Windows\Fonts\arial.ttf")
    if arial.is_file():
        pdfmetrics.registerFont(TTFont("ArialUnicode", str(arial)))
        return "ArialUnicode"
    return "Helvetica"


# --- Dominios para índice y orden de lectura ---
DOMAIN_SECTIONS: list[tuple[str, str, list[str]]] = [
    (
        "A",
        "Nesting, historial de cortes y PQArt",
        ["reporte_cortes", "pqart_wo", "pqart_swo", "reportes_dinamicos"],
    ),
    (
        "B",
        "Costeo, trazabilidad SWO y tablas ERP",
        [
            "costos_prorrateo",
            "diccionario_swo",
            "erp_super_work_orders",
            "erp_jobs",
            "erp_work_orders",
            "erp_placas_tracking",
            "erp_piezas_tracking",
        ],
    ),
    (
        "C",
        "Lista de largos (importación, sesiones, taller)",
        [
            "lista_largos_job",
            "lista_largos_swo",
            "lista_largos_planes",
            "lista_largos_sesiones",
            "lista_largos_turnos",
            "lista_largos_cortes",
            "lista_largos_sobrantes",
            "lista_largos_remanentes",
            "lista_largos_eventos_pieza",
            "lista_largos_eventos_sobrante",
        ],
    ),
    ("D", "Material y utilidades API", ["material_actual"]),
    ("E", "Tablas legacy (sin escritura en este repositorio)", ["components", "sheets", "jobs"]),
]


TABLE_PURPOSE: dict[str, str] = {
    "reporte_cortes": (
        "Historial maestro de cada pieza colocada en el nesting: qué job/WO/SWO, en qué placa "
        "(identificadores sheet_*), calibre, eficiencia, estado operativo, geometría (polígonos/marcas) "
        "y rutas de exportación. Es la tabla que más consume la app y la API."
    ),
    "costos_prorrateo": (
        "Desglose financiero por placa y por WO: áreas asignadas, porcentaje sobre la placa, costo "
        "atribuido y parte del scrap. Los datos vienen del motor de nesting + CSV job_data + reglas de "
        "prorrateo en `generar_csv_compras`."
    ),
    "diccionario_swo": (
        "Traduce prefijos de carpeta (nombre corto de proyecto en red) a cliente, número de job y producto. "
        "Se usa cuando varias WO comparten una SWO y las piezas traen prefijo__nombre."
    ),
    "erp_super_work_orders": "Registro de SWO para enlazar placas y piezas en el modelo ERP interno.",
    "erp_jobs": (
        "Job de fabricación (cliente, producto, cantidades). Acumula SSTL/carbon desde CSV y costos MP "
        "actualizados después del costeo."
    ),
    "erp_work_orders": "WO hijas ligadas a un job, con cantidad por WO.",
    "erp_placas_tracking": "Una fila por código de placa nesteada dentro de una SWO y estados de proceso.",
    "erp_piezas_tracking": "Piezas requeridas por placa y WO (qty requerida/cortada/doblada).",
    "lista_largos_job": (
        "Copia normalizada del CSV «lista de largos» por job (parte, clasificación, largo en pulgadas, "
        "cantidades). Se repuebla al guardar un nesting no-SWO si existe la carpeta MATERIALS/LISTA LARGOS 2."
    ),
    "lista_largos_swo": (
        "Lista consolidada por SWO: cantidades por WO con factor XN y totales por job/SWO. "
        "Se regenera dentro de `guardar_tracking_erp` (llamado desde `generar_csv_compras` tras el costeo), "
        "no en el primer paso de solo `guardar_nesting_en_postgresql`."
    ),
    "lista_largos_sesiones": "Sesiones activas/cerradas de consumo de barras en taller (operador, bahía, conteos).",
    "lista_largos_turnos": "Turnos dentro de una sesión de lista de largos.",
    "lista_largos_eventos_pieza": "Log de eventos por pieza (acción, operador, bahía).",
    "lista_largos_eventos_sobrante": "Log de eventos sobre sobrantes medidos o retirados.",
    "lista_largos_planes": "Planes de corte serializados en JSON + totales por orden.",
    "lista_largos_cortes": "Estado por pieza dentro del plan (pendiente/en proceso/finalizada).",
    "lista_largos_sobrantes": "Longitud sobrante vigente por barra tras cortes.",
    "lista_largos_remanentes": "Inventario de remanentes disponibles o reservados para órdenes.",
    "pqart_wo": (
        "DXF generados por WO para PQArt/robot: ruta en disco, tipo de corte (láser/plasma), vínculos a sheet_*."
    ),
    "pqart_swo": "Igual que pqart_wo pero agrupado por nombre de SWO.",
    "reportes_dinamicos": "Snapshot JSON por SWO para cuadros o vistas construidas desde la API.",
    "material_actual": "Último material validado (largo/ancho/espesor) registrado vía API.",
    "components": "Esquema antiguo tipo BOM por componente en placa. No hay INSERT/UPDATE desde este repo.",
    "sheets": "Esquema antiguo de hojas por job. No hay SQL activo en este repo.",
    "jobs": "Esquema antiguo de jobs. No hay SQL activo en este repo.",
}


# Filas: módulo, operaciones (resumen), detalle orientado al negocio
TABLE_MODULE_OPS: dict[str, list[tuple[str, str, str]]] = {
    "reporte_cortes": [
        (
            "interface/tab_nesting.py",
            "Orquestación",
            "Tras exportar el nesting llama `guardar_nesting_en_postgresql` y antes/durante puede llamar "
            "`generar_csv_compras` (costeo).",
        ),
        (
            "interface/postgres_connector.py",
            "INSERT, DELETE",
            "`guardar_nesting_en_postgresql`: INSERT una fila por pieza nesteada (geometría JSON). "
            "Si es SWO (`nombre_job` empieza por SWO-), DELETE previo de filas `Pendiente%` de esa SWO.",
        ),
        (
            "interface/utils_nesting.py",
            "SELECT",
            "`obtener_siguiente_consecutivo`: lee WO existentes (`W.O.%`) para numeración de carpetas.",
        ),
        (
            "interface/tab_files.py",
            "SELECT",
            "Lista SWO/jobs disponibles desde historial.",
        ),
        (
            "api_server.py (FastAPI)",
            "SELECT, UPDATE",
            "Consultas por job/SWO, KPIs y actualización de estatus desde endpoints.",
        ),
        (
            "despachador_nocturno.py",
            "UPDATE, SELECT",
            "Marca piezas/listados como despachados según reglas nocturnas.",
        ),
    ],
    "costos_prorrateo": [
        (
            "interface/tab_nesting.py",
            "Orquestación",
            "Invoca `generar_csv_compras(...)` al cerrar escenarios de nesting (con `db_config`).",
        ),
        (
            "interface/utils_nesting.py",
            "INSERT",
            "`generar_csv_compras`: calcula áreas por placa/WO desde `resultados_motor`, lee `job_data_*.csv` "
            "y `diccionario_swo` / `erp_*` para cliente-producto-job; INSERT masivo en `costos_prorrateo`. "
            "Luego llama `guardar_tracking_erp`.",
        ),
        (
            "interface/postgres_connector.py",
            "SELECT",
            "`guardar_tracking_erp` y consolidaciones leen SUM(costo_asignado + costo_scrap).",
        ),
    ],
    "diccionario_swo": [
        (
            "interface/tab_files.py",
            "INSERT (alta)",
            "Cuando el usuario registra datos SWO/prefijo carpeta llama `registrar_diccionario_swo`.",
        ),
        (
            "interface/postgres_connector.py",
            "SELECT",
            "`guardar_nesting_en_postgresql`: resuelve `job_number` por prefijo de pieza en modo SWO.",
        ),
        (
            "interface/utils_nesting.py",
            "SELECT",
            "`generar_csv_compras` usa prefijo de pieza para cliente/job/producto.",
        ),
    ],
    "erp_super_work_orders": [
        (
            "interface/postgres_connector.py",
            "INSERT, SELECT",
            "`guardar_tracking_erp`: asegura fila SWO por nombre.",
        ),
    ],
    "erp_jobs": [
        (
            "interface/postgres_connector.py",
            "INSERT, UPDATE, SELECT",
            "`guardar_tracking_erp`: crea o actualiza job (cliente, producto, cantidad, SSTL/carbon desde CSV); "
            "actualiza costos MP y conteo WO.",
        ),
    ],
    "erp_work_orders": [
        (
            "interface/postgres_connector.py",
            "INSERT, UPDATE, SELECT",
            "`guardar_tracking_erp`: crear WO por nombre y qty; UPDATE qty si cambia.",
        ),
    ],
    "erp_placas_tracking": [
        (
            "interface/postgres_connector.py",
            "INSERT, SELECT",
            "`guardar_tracking_erp`: una entrada por `sheet_uid`/código de placa dentro de la SWO.",
        ),
    ],
    "erp_piezas_tracking": [
        (
            "interface/postgres_connector.py",
            "INSERT",
            "`guardar_tracking_erp`: piezas por placa/WO con cantidades requeridas.",
        ),
    ],
    "lista_largos_job": [
        (
            "interface/postgres_connector.py",
            "SELECT (dispara importación)",
            "Al guardar nesting **no SWO** con `ruta_exportacion`, llama `importar_lista_largos_job`.",
        ),
        (
            "modules/lista_largos_importer.py",
            "DDL mínimo, DELETE, INSERT, UPDATE",
            "Crea columnas índices si faltan; borra filas del job y reinserta desde CSV en MATERIALS/LISTA LARGOS 2.",
        ),
        (
            "interface/postgres_connector.py",
            "SELECT",
            "Fuente para regenerar `lista_largos_swo`.",
        ),
        (
            "api_server.py",
            "SELECT",
            "Construye respuestas lista de largos por WO/SWO.",
        ),
    ],
    "lista_largos_swo": [
        (
            "interface/utils_nesting.py → postgres_connector.py",
            "Orquestación",
            "`generar_csv_compras` termina llamando `guardar_tracking_erp` con las mismas filas que "
            "se insertaron en `costos_prorrateo`.",
        ),
        (
            "interface/postgres_connector.py",
            "CREATE TABLE IF NOT EXISTS, DELETE, INSERT",
            "`guardar_tracking_erp` → `_guardar_lista_largos_swo`: DELETE por SWO y repoblación desde "
            "`lista_largos_job` filtrado por jobs/WOs del costeo.",
        ),
    ],
    "lista_largos_sesiones": [
        ("api_server.py", "INSERT, UPDATE, DDL", "Tabla sesiones + índices; ciclo de vida de sesión activa."),
        ("Reset_consumo_lista_de_largos.py", "DELETE", "Limpieza masiva para pruebas o reset operativo."),
    ],
    "lista_largos_turnos": [
        ("api_server.py", "INSERT, UPDATE", "Turnos ligados a sesión."),
        ("Reset_consumo_lista_de_largos.py", "DELETE", ""),
    ],
    "lista_largos_eventos_pieza": [
        ("api_server.py", "INSERT", "Eventos de pieza."),
        ("Reset_consumo_lista_de_largos.py", "DELETE", ""),
    ],
    "lista_largos_eventos_sobrante": [
        ("api_server.py", "INSERT", "Eventos de sobrante."),
        ("Reset_consumo_lista_de_largos.py", "DELETE", ""),
    ],
    "lista_largos_planes": [
        ("api_server.py", "INSERT, UPDATE", "Persistencia del plan JSON."),
    ],
    "lista_largos_cortes": [
        ("api_server.py", "INSERT/UPSERT, UPDATE", "Estado de cada pieza del plan."),
        ("Reset_consumo_lista_de_largos.py", "DELETE", ""),
    ],
    "lista_largos_sobrantes": [
        ("api_server.py", "DELETE, INSERT", "Recalcula sobrantes por barra."),
    ],
    "lista_largos_remanentes": [
        ("api_server.py", "INSERT, UPDATE", "Stock remanentes."),
        ("Reset_consumo_lista_de_largos.py", "DELETE, UPDATE", ""),
        ("Eliminar_job_bd.py", "DELETE", "Eliminación ligada a limpieza de jobs."),
    ],
    "pqart_wo": [
        (
            "modules/nesting_engine/exporter.py",
            "Memoria (previo a BD)",
            "`_registrar_exportacion_pqart_hoja`: llena `pqart_exports` en cada hoja del resultado motor.",
        ),
        (
            "interface/postgres_connector.py",
            "DELETE, INSERT",
            "`_guardar_pqart_wo`: sustituye filas por WO según exports.",
        ),
        ("api_server.py", "SELECT", "Resuelve rutas PQArt por tipo orden."),
    ],
    "pqart_swo": [
        ("modules/nesting_engine/exporter.py", "Memoria", "Misma colección `pqart_exports`."),
        (
            "interface/postgres_connector.py",
            "DELETE, INSERT, UPDATE",
            "`_guardar_pqart_swo` + `_marcar_wos_como_procesadas_en_pqart` en pqart_wo.",
        ),
        ("api_server.py", "SELECT", ""),
    ],
    "reportes_dinamicos": [
        ("api_server.py", "INSERT ON CONFLICT / UPSERT", "Snapshot agregado por SWO."),
    ],
    "material_actual": [
        ("api_server.py", "CREATE TABLE IF NOT EXISTS, INSERT", "Registro desde flujo API/UI conectado al servidor."),
    ],
    "components": [
        ("—", "—", "No se encontraron referencias SQL en el repositorio ARGA-NESTING-SOFTWARE."),
    ],
    "sheets": [
        ("—", "—", "No se encontraron referencias SQL en el repositorio ARGA-NESTING-SOFTWARE."),
    ],
    "jobs": [
        ("—", "—", "No se encontraron referencias SQL en el repositorio ARGA-NESTING-SOFTWARE."),
    ],
}


COLUMN_DOC: dict[tuple[str, str], str] = {}
# Copia explícita de descripciones de columnas (mismas claves que versión anterior)
_RAW = r"""
reporte_cortes|id|PK. Identificador de registro.
reporte_cortes|job|Nombre de job o identificador SWO en UI.
reporte_cortes|work_order|WO asociada a la pieza en esta fila.
reporte_cortes|calibre|Grupo de material/espesor textual.
reporte_cortes|calibre_nominal|Calibre normalizado (pulgadas nominales).
reporte_cortes|placa_id|Etiqueta visible de placa (además de sheet_*).
reporte_cortes|eficiencia|Aprovechamiento de la hoja (% o fracción según motor).
reporte_cortes|item|Nombre de pieza/componente.
reporte_cortes|mark|Marca de trazabilidad en geometría.
reporte_cortes|clase|Clasificación interna (ej. A).
reporte_cortes|ancho|Bounding o ancho pieza en unidad histórica (mm).
reporte_cortes|largo|Largo pieza en unidad histórica (mm).
reporte_cortes|ancho_in|Ancho de placa en pulgadas (canónico).
reporte_cortes|largo_in|Largo de placa en pulgadas (canónico).
reporte_cortes|estatus|Pendiente, Pendiente SWO, despachado, etc.
reporte_cortes|geometria|JSON: exterior, agujeros, marcas, límites de placa, sheet_meta.
reporte_cortes|fecha_corte|Timestamp de inserción/último toque.
reporte_cortes|super_work_order|Texto SWO si aplica.
reporte_cortes|origen_nesting|Etiqueta de origen (ej. INGENIERIA).
reporte_cortes|estado_3d|PENDIENTE u otro estado STEP/3D.
reporte_cortes|ruta_exportacion|Carpeta raíz del export DXF/reporte.
reporte_cortes|sheet_uid|UID único por hoja nest.
reporte_cortes|sheet_code|Código corto de hoja.
reporte_cortes|sheet_seq|Número de hoja en la corrida.
reporte_cortes|sheet_display_name|Nombre para operador/PDF.
reporte_cortes|plate_group_key|Agrupa variantes de la misma placa lógica.
reporte_cortes|placa_ancho_canonico_mm|Ancho placa mm.
reporte_cortes|placa_largo_canonico_mm|Largo placa mm.
reporte_cortes|nest_instance_id|Instancia de nest.
reporte_cortes|source_nest_name|Carpeta/nest fuente.
reporte_cortes|is_rtz|True si es remanente/RTZ.
reporte_cortes|job_key|Clave normalizada para JOIN con lista_largos_job.
costos_prorrateo|id|PK.
costos_prorrateo|work_order|WO usada como clave de reparto (nombre visible).
costos_prorrateo|qty_lote|Multiplicador XN extraído del nombre de WO/SWO.
costos_prorrateo|plate_id|Id de placa (coherente con reporte interno por hoja).
costos_prorrateo|largo_in|Largo nominal placa (in).
costos_prorrateo|ancho_in|Ancho nominal placa (in).
costos_prorrateo|espesor|Texto calibre asignado.
costos_prorrateo|espesor_original|Texto antes de normalizar (si existe columna).
costos_prorrateo|precio_placa|Precio hoja desde catálogo/motor.
costos_prorrateo|area_total_in2|Área bruta placa (in²).
costos_prorrateo|cliente|Cliente imputado.
costos_prorrateo|producto|Producto imputado.
costos_prorrateo|job|Número/nombre job comercial.
costos_prorrateo|wo_origen|WO origen cuando SWO reparte entre varias.
costos_prorrateo|area_asignada_in2|Área consumida por ese grupo en la placa.
costos_prorrateo|porcentaje_area|% del área total de la placa.
costos_prorrateo|costo_asignado|Costo proporcional uso.
costos_prorrateo|scrap_in2|Área scrap imputada a ese grupo.
costos_prorrateo|costo_scrap|Costo monetario del scrap imputado.
costos_prorrateo|fecha_registro|Auto.
diccionario_swo|id|PK.
diccionario_swo|swo_id|Identificador SWO.
diccionario_swo|prefijo_carpeta|Prefijo en nombres de pieza/carpeta.
diccionario_swo|cliente|Cliente.
diccionario_swo|job_numero|Job ERP.
diccionario_swo|producto|Producto.
erp_super_work_orders|id_swo|PK.
erp_super_work_orders|nombre_swo|Nombre SWO.
erp_super_work_orders|fecha_creacion|Creación.
erp_jobs|id_job|PK.
erp_jobs|job_number|Número job.
erp_jobs|cliente|Cliente.
erp_jobs|producto|Producto.
erp_jobs|cantidad_total|Cantidad tanques/unidades desde CSV.
erp_jobs|fecha_registro|Registro.
erp_jobs|wo_generadas|Contador WO registradas.
erp_jobs|sstl|Peso SSTL desde job_data.
erp_jobs|carbon_steel|Peso carbon steel desde job_data.
erp_jobs|costo_mp_wo|Costo materia prima vista WO.
erp_jobs|costo_mp_swo|Costo MP acumulado SWO.
erp_work_orders|id_wo|PK.
erp_work_orders|id_job|FK erp_jobs.
erp_work_orders|nombre_wo|Nombre WO.
erp_work_orders|qty_wo|Cantidad WO.
erp_placas_tracking|id_placa|PK.
erp_placas_tracking|codigo_placa|UID placa nest.
erp_placas_tracking|qty_placas|Cantidad (típicamente 1 por registro).
erp_placas_tracking|herinox|Estado flujo Herinox.
erp_placas_tracking|granallado|Estado granallado.
erp_placas_tracking|corte|Estado corte.
erp_placas_tracking|id_swo|FK SWO.
erp_piezas_tracking|id_pieza|PK.
erp_piezas_tracking|id_placa|FK placa.
erp_piezas_tracking|id_wo|FK WO.
erp_piezas_tracking|nombre_componente|Nombre pieza.
erp_piezas_tracking|qty_requerida|Requerido nesting.
erp_piezas_tracking|qty_cortada|Avance corte (ERP).
erp_piezas_tracking|qty_doblada|Avance doblado (ERP).
lista_largos_job|id|PK.
lista_largos_job|job|Nombre job carpeta.
lista_largos_job|job_key|Clave normalizada.
lista_largos_job|source_csv_name|Archivo CSV origen.
lista_largos_job|source_csv_path|Ruta CSV.
lista_largos_job|nombre|Nombre línea lista largos.
lista_largos_job|clasificacion|Tipo parte.
lista_largos_job|largo_in|Longitud pulgadas.
lista_largos_job|cantidad|Cantidad operativa fila.
lista_largos_job|cantidad_base|Base antes de escalados.
lista_largos_job|cantidad_job|Cantidad por job.
lista_largos_job|cantidad_total|Total escalado.
lista_largos_job|row_hash|Integridad/dedup.
lista_largos_job|importado_el|Momento importación.
lista_largos_swo|id|PK.
lista_largos_swo|super_work_order|Nombre SWO.
lista_largos_swo|job|Job contenido.
lista_largos_swo|work_order|WO operativa.
lista_largos_swo|qty_lote|Factor lote nest.
lista_largos_swo|factor_wo|XN de WO.
lista_largos_swo|source_csv_name|Traza CSV job.
lista_largos_swo|source_csv_path|Ruta CSV.
lista_largos_swo|nombre|Ítem lista.
lista_largos_swo|clasificacion|Clasificación.
lista_largos_swo|largo_in|Pulgadas.
lista_largos_swo|cantidad|Cantidad fila SWO.
lista_largos_swo|cantidad_base|Base.
lista_largos_swo|cantidad_job|Por job.
lista_largos_swo|cantidad_total_job|Total job.
lista_largos_swo|cantidad_total_swo|Total SWO.
lista_largos_swo|creado_el|Regeneración tabla.
pqart_wo|id|PK.
pqart_wo|nombre_wo|WO dueña DXF.
pqart_wo|nombre_dxf|Nombre archivo.
pqart_wo|ruta|Path completo.
pqart_wo|tipo_corte|Laser/plasma/oxi según carpeta.
pqart_wo|procesado_con_swo|Marcador procesamiento SWO.
pqart_wo|ls|Estado sync PQArt.
pqart_wo|sheet_uid|UID hoja.
pqart_wo|sheet_code|Código hoja.
pqart_wo|sheet_display_name|Nombre visible.
pqart_wo|created_at|Creación.
pqart_wo|updated_at|Actualización.
pqart_wo|origen_proceso_pqart|Pendiente/Servidor/Local/Ambas.
pqart_swo|id|PK.
pqart_swo|nombre_swo|SWO dueña.
pqart_swo|nombre_dxf|Nombre archivo.
pqart_swo|ruta|Path completo.
pqart_swo|tipo_corte|Tipo proceso.
pqart_swo|ls|Estado sync.
pqart_swo|sheet_uid|UID hoja.
pqart_swo|sheet_code|Código.
pqart_swo|sheet_display_name|Nombre.
pqart_swo|created_at|Creación.
pqart_swo|updated_at|Actualización.
pqart_swo|origen_proceso_pqart|Origen proceso.
reportes_dinamicos|super_work_order|Clave SWO.
reportes_dinamicos|datos_snapshot|JSON métricas/vista.
reportes_dinamicos|creado_el|Timestamp snapshot.
material_actual|id_material|PK.
material_actual|largo|Largo material.
material_actual|ancho|Ancho material.
material_actual|grosor|Espesor.
material_actual|estatus|validado, etc.
material_actual|creado_el|Timestamp.
components|id_component|PK.
components|id_sheet|FK legacy.
components|item|Ítem.
components|clase|Clase.
components|marca|Marca.
components|geometria|JSON.
components|estatus_corte|Estado.
components|fecha_corte|Fecha.
sheets|id_sheet|PK.
sheets|id_job|FK legacy.
sheets|material|Material.
sheets|espesor|Espesor.
sheets|dimension_x|Dim.
sheets|dimension_y|Dim.
sheets|estatus|Estado.
jobs|id_job|PK legacy.
jobs|numero_job|Texto job.
jobs|fecha_creacion|Creación.
"""
for line in _RAW.strip().splitlines():
    parts = line.split("|", 2)
    if len(parts) == 3:
        t, c, d = parts
        COLUMN_DOC[(t.strip(), c.strip())] = d.strip()

LISTA_SHARED: dict[str, str] = {
    "orden_id": "ID de orden en taller (WO/SWO/JOB según tipo_orden).",
    "tipo_orden": "WO o SWO (restricción API).",
    "material": "Grado/material barra.",
    "barra_index": "Índice de barra en el plan.",
    "pieza_index": "Índice pieza en la barra.",
    "pieza_uid": "UID idempotente.",
    "accion": "Tipo de evento.",
    "operador": "Quién ejecutó.",
    "bahia": "Bahía física.",
    "fecha_evento": "Momento evento.",
    "fecha_update": "Última modificación.",
    "sesion_id": "FK lista_largos_sesiones.id.",
    "turno_id": "FK lista_largos_turnos.id.",
    "plan_hash": "Hash del JSON de plan.",
    "plan_json": "Definición del plan.",
    "total_piezas": "Piezas en plan.",
    "total_barras": "Barras en plan.",
    "estado": "Estado sesión/plan/turno.",
    "created_at": "Alta.",
    "updated_at": "Modificación.",
    "hora_inicio": "Inicio sesión.",
    "hora_ultimo_movimiento": "Último movimiento.",
    "hora_fin": "Fin sesión.",
    "piezas_totales": "Objetivo sesión.",
    "piezas_completadas": "Completadas sesión.",
    "hora_inicio_turno": "Inicio turno.",
    "hora_fin_turno": "Fin turno.",
    "piezas_completadas_inicio": "Contador al iniciar turno.",
    "piezas_completadas_fin": "Contador al cerrar turno.",
    "status": "Estado operativo.",
    "rem_uid": "PK remanente.",
    "rem_id": "ID negocio remanente.",
    "tipo": "Tipo remanente.",
    "seccion": "Detalle sección.",
    "largo_real": "Longitud útil.",
    "largo_id": "Catálogo largo estándar.",
    "material_grade": "Grado material.",
    "fuente_orden_id": "Orden que originó.",
    "fuente_tipo_orden": "Tipo orden fuente.",
    "reservado_para_orden_id": "Orden reserva.",
    "reservado_para_tipo_orden": "Tipo orden reserva.",
    "sobrante_real": "Longitud sobrante.",
}


def _fallback_column_doc(table: str, col: str, dtype: str) -> str:
    lc = col.lower()
    if lc == "id" and "int" in dtype:
        return "Clave primaria autogenerada (SERIAL)."
    if lc in ("created_at", "updated_at", "creado_el", "fecha_registro", "fecha_corte", "importado_el"):
        return "Marca temporal del sistema."
    if table.startswith("lista_largos_") and col in LISTA_SHARED:
        return LISTA_SHARED[col]
    if ("lista_largos_eventos_" in table) and col in LISTA_SHARED:
        return LISTA_SHARED[col]
    return f"Campo técnico «{col}» ({dtype}). Ver flujos del dominio «Lista de largos» o tabla relacionada."


def _ordered_schema(schema: list[dict]) -> list[dict]:
    by_name = {e["table"]: e for e in schema}
    ordered: list[dict] = []
    seen = set()
    for _, _, tables in DOMAIN_SECTIONS:
        for t in tables:
            if t in by_name and t not in seen:
                ordered.append(by_name[t])
                seen.add(t)
    for e in schema:
        if e["table"] not in seen:
            ordered.append(e)
            seen.add(e["table"])
    return ordered


def _styles(font: str):
    bs = getSampleStyleSheet()
    title = ParagraphStyle(
        "DocTitle",
        parent=bs["Title"],
        fontName=font,
        fontSize=22,
        leading=26,
        textColor=C_PRIMARY,
        spaceAfter=14,
    )
    subtitle = ParagraphStyle(
        "DocSub",
        parent=bs["Normal"],
        fontName=font,
        fontSize=11,
        leading=14,
        textColor=C_ACCENT,
    )
    h1 = ParagraphStyle(
        "SecH1",
        parent=bs["Heading1"],
        fontName=font,
        fontSize=14,
        leading=17,
        textColor=C_PRIMARY,
        spaceBefore=16,
        spaceAfter=10,
        outlineLevel=0,
    )
    h2 = ParagraphStyle(
        "SecH2",
        parent=bs["Heading2"],
        fontName=font,
        fontSize=11,
        leading=14,
        textColor=C_ACCENT,
        spaceBefore=12,
        spaceAfter=6,
        outlineLevel=1,
    )
    body = ParagraphStyle(
        "BodyEs",
        parent=bs["Normal"],
        fontName=font,
        fontSize=9.5,
        leading=12,
        alignment=TA_JUSTIFY,
    )
    body_compact = ParagraphStyle(
        "BodyCmp",
        parent=body,
        fontSize=8.5,
        leading=11,
    )
    tbl_hdr = ParagraphStyle(
        "TblHdr",
        parent=body,
        fontSize=8,
        textColor=colors.white,
        alignment=TA_JUSTIFY,
    )
    tbl_cell = ParagraphStyle(
        "TblCell",
        parent=body_compact,
        alignment=TA_JUSTIFY,
    )
    mono = ParagraphStyle(
        "MonoSmall",
        parent=body_compact,
        fontName="Courier",
    )
    return title, subtitle, h1, h2, body, body_compact, tbl_hdr, tbl_cell, mono


def _flow_intro(font: str, styles: tuple) -> list:
    title, subtitle, h1, h2, body, body_compact = styles[:6]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    story: list = [
        Spacer(1, 0.35 * inch),
        Paragraph(escape("ARGA Nesting Suite"), subtitle),
        Paragraph(escape("Base de datos PostgreSQL"), subtitle),
        Spacer(1, 0.2 * inch),
        Paragraph(escape("Manual de tablas — nestingpro_db · esquema public"), title),
        Spacer(1, 0.15 * inch),
        Paragraph(
            escape(
                f"Documento generado el {ts} a partir del volcado «schema_snapshot.json». "
                "Cada sección describe para qué sirve la tabla, qué parte del código escribe o lee datos "
                "y el significado de cada columna."
            ),
            body,
        ),
        Spacer(1, 0.12 * inch),
        HRFlowable(width="100%", thickness=1, color=C_BORDER, spaceBefore=6, spaceAfter=12),
        Paragraph(escape("<b>1. Cómo está organizado este PDF</b>"), h1),
        Paragraph(
            escape(
                "<b>Primero</b> encontrará la vista por dominios (nesting, ERP, lista de largos…) "
                "y un diagrama de flujo simplificado. "
                "<b>Después</b>, cada tabla en el mismo orden del índice, con tres bloques: "
                "propósito de negocio, tabla «quién toca la BD» y listado de campos."
            ),
            body,
        ),
        Paragraph(escape("<b>2. Piezas del sistema que hablan con PostgreSQL</b>"), h1),
    ]
    tbl_cell_style = ParagraphStyle(
        "TblCellFix",
        parent=body_compact,
        alignment=TA_JUSTIFY,
    )
    hdr_style = ParagraphStyle(
        "TblHdrFix",
        parent=body_compact,
        textColor=colors.white,
        fontName=font,
    )
    mono_style = ParagraphStyle("MonoFix", parent=body_compact, fontName="Courier")

    sys_tbl = Table(
        [
            [
                Paragraph(escape("<b>Componente</b>"), hdr_style),
                Paragraph(escape("<b>Rol respecto a la base</b>"), hdr_style),
            ],
            [
                Paragraph(escape("Escritorio — pestaña Nesting (tab_nesting.py)"), mono_style),
                Paragraph(
                    escape(
                        "Orquesta guardado BD tras nesting: `guardar_nesting_en_postgresql`, "
                        "`generar_csv_compras` (costeo + ERP)."
                    ),
                    tbl_cell_style,
                ),
            ],
            [
                Paragraph(escape("postgres_connector.py"), mono_style),
                Paragraph(
                    escape(
                        "`guardar_nesting_en_postgresql`: reporte_cortes, pqart_*, import lista_largos_job. "
                        "`guardar_tracking_erp`: tablas erp_* y lecturas para costos."
                    ),
                    tbl_cell_style,
                ),
            ],
            [
                Paragraph(escape("utils_nesting.py"), mono_style),
                Paragraph(
                    escape(
                        "`generar_csv_compras`: INSERT costos_prorrateo; usa diccionario_swo + erp_* en SELECT. "
                        "`obtener_siguiente_consecutivo`: SELECT reporte_cortes."
                    ),
                    tbl_cell_style,
                ),
            ],
            [
                Paragraph(escape("lista_largos_importer.py"), mono_style),
                Paragraph(
                    escape("DELETE + INSERT en lista_largos_job desde CSV en carpeta del job."),
                    tbl_cell_style,
                ),
            ],
            [
                Paragraph(escape("nesting_engine/exporter.py"), mono_style),
                Paragraph(
                    escape("Prepara pqart_exports en el dict del motor (sin SQL directo)."),
                    tbl_cell_style,
                ),
            ],
            [
                Paragraph(escape("api_server.py"), mono_style),
                Paragraph(
                    escape(
                        "CRUD lista de largos operativo, reportes dinámicos, material_actual, "
                        "consultas reporte_cortes / lista_largos_job."
                    ),
                    tbl_cell_style,
                ),
            ],
            [
                Paragraph(escape("despachador_nocturno.py"), mono_style),
                Paragraph(
                    escape("UPDATE / SELECT sobre reporte_cortes para despachos automáticos."),
                    tbl_cell_style,
                ),
            ],
            [
                Paragraph(escape("Scripts mantenimiento"), mono_style),
                Paragraph(
                    escape(
                        "Reset_consumo_lista_de_largos.py y Eliminar_job_bd.py: limpiezas en lista_largos_*."
                    ),
                    tbl_cell_style,
                ),
            ],
        ],
        colWidths=[2.0 * inch, 5.2 * inch],
    )
    sys_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
                ("GRID", (0, 0), (-1, -1), 0.5, C_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C_BAND]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(sys_tbl)
    story.append(Spacer(1, 0.14 * inch))

    story.append(Paragraph(escape("<b>3. Flujo resumido: desde «Nestear» hasta las tablas</b>"), h1))
    flow_rows = [
        [
            Paragraph(escape("<b>Paso</b>"), hdr_style),
            Paragraph(escape("<b>Tablas principales afectadas</b>"), hdr_style),
            Paragraph(escape("<b>Notas</b>"), hdr_style),
        ],
        [
            Paragraph(escape("1. Usuario nestea y exporta"), mono_style),
            Paragraph(escape("(sin BD todavía)"), tbl_cell_style),
            Paragraph(
                escape(
                    "Motor produce dict por calibre/hoja/pieza; exporter.py registra rutas PQArt."
                ),
                tbl_cell_style,
            ),
        ],
        [
            Paragraph(escape("2. Guardar en PostgreSQL"), mono_style),
            Paragraph(
                escape("reporte_cortes · pqart_wo / pqart_swo · lista_largos_job (si WO simple)"),
                tbl_cell_style,
            ),
            Paragraph(
                escape(
                    "`guardar_nesting_en_postgresql`. SWO borra pendientes previos de esa SWO en reporte_cortes."
                ),
                tbl_cell_style,
            ),
        ],
        [
            Paragraph(escape("3. Costeo y ERP"), mono_style),
            Paragraph(
                escape("costos_prorrateo · erp_* · lista_largos_swo (SWO)"),
                tbl_cell_style,
            ),
            Paragraph(
                escape(
                    "`generar_csv_compras` inserta costos y llama `guardar_tracking_erp`. "
                    "SWO también regenera lista_largos_swo."
                ),
                tbl_cell_style,
            ),
        ],
        [
            Paragraph(escape("4. Taller / tablets"), mono_style),
            Paragraph(
                escape("lista_largos_sesiones · turnos · cortes · remanentes · eventos"),
                tbl_cell_style,
            ),
            Paragraph(
                escape("Consumido principalmente por api_server.py (no por la GUI clásica)."),
                tbl_cell_style,
            ),
        ],
        [
            Paragraph(escape("5. Despacho nocturno"), mono_style),
            Paragraph(escape("reporte_cortes"), tbl_cell_style),
            Paragraph(
                escape("despachador_nocturno.py cambia estatus según reglas."),
                tbl_cell_style,
            ),
        ],
    ]
    flow_tbl = Table(flow_rows, colWidths=[1.35 * inch, 2.35 * inch, 3.5 * inch])
    flow_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), C_ACCENT),
                ("GRID", (0, 0), (-1, -1), 0.5, C_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C_BAND]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(flow_tbl)

    story.append(Spacer(1, 0.16 * inch))
    story.append(Paragraph(escape("<b>4. Índice de tablas por dominio</b>"), h1))
    idx_cells = [[Paragraph(escape("<b>Dominio</b>"), hdr_style), Paragraph(escape("<b>Tablas</b>"), hdr_style)]]
    for letter_id, title_dom, tables in DOMAIN_SECTIONS:
        idx_cells.append(
            [
                Paragraph(escape(f"{letter_id}. {title_dom}"), tbl_cell_style),
                Paragraph(escape(" · ".join(tables)), mono_style),
            ]
        )
    idx_tbl = Table(idx_cells, colWidths=[2.35 * inch, 4.85 * inch])
    idx_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
                ("GRID", (0, 0), (-1, -1), 0.5, C_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7fafc")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(idx_tbl)
    story.append(PageBreak())
    return story


def _table_detail_section(
    entry: dict,
    font: str,
    styles: tuple,
) -> list:
    _, _, _, h2, body, body_compact, tbl_hdr, tbl_cell, mono = styles
    tname = entry["table"]
    purpose = TABLE_PURPOSE.get(
        tname,
        "Tabla del esquema public documentada solo por columnas; amplíe el mapeo en código si aplica.",
    )
    ops = TABLE_MODULE_OPS.get(
        tname,
        [("Por determinar", "—", "Buscar el nombre de tabla en el repo con grep.")],
    )

    hdr_band = ParagraphStyle("Band", parent=h2, fontSize=11, textColor=colors.white, fontName=font)
    band_tbl = Table(
        [[Paragraph(escape(f"TABLA · {tname}"), hdr_band)]],
        colWidths=[7.15 * inch],
    )
    band_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), C_PRIMARY),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )

    op_rows = [
        [
            Paragraph(escape("<b>Módulo / archivo</b>"), tbl_hdr),
            Paragraph(escape("<b>Operaciones</b>"), tbl_hdr),
            Paragraph(escape("<b>Qué hace con esta tabla</b>"), tbl_hdr),
        ]
    ]
    for mod, operation, detail in ops:
        op_rows.append(
            [
                Paragraph(escape(mod), mono),
                Paragraph(escape(operation), tbl_cell),
                Paragraph(escape(detail), tbl_cell),
            ]
        )
    op_table = Table(op_rows, colWidths=[1.85 * inch, 1.05 * inch, 4.25 * inch])
    op_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), C_ACCENT),
                ("GRID", (0, 0), (-1, -1), 0.4, C_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C_BAND]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    col_rows = [
        [
            Paragraph(escape("<b>Columna</b>"), tbl_hdr),
            Paragraph(escape("<b>Tipo</b>"), tbl_hdr),
            Paragraph(escape("<b>Significado / contenido típico</b>"), tbl_hdr),
        ]
    ]
    for c in entry["columns"]:
        name = c["name"]
        dtype = c["data_type"]
        nullable = c["nullable"]
        default = c["default"] or ""
        tipo_line = dtype + (" · NULL permitido" if nullable == "YES" else " · NOT NULL")
        if default:
            tipo_line += "\nDefault: " + (default[:70] + "…" if len(default) > 70 else default)
        doc = COLUMN_DOC.get((tname, name)) or _fallback_column_doc(tname, name, dtype)
        col_rows.append(
            [
                Paragraph(escape(name), mono),
                Paragraph(escape(tipo_line), tbl_cell),
                Paragraph(escape(doc), tbl_cell),
            ]
        )

    col_table = Table(col_rows, colWidths=[1.35 * inch, 1.55 * inch, 4.25 * inch])
    col_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
                ("GRID", (0, 0), (-1, -1), 0.35, C_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )

    block = [
        KeepTogether([band_tbl, Spacer(1, 8)]),
        Paragraph(escape("<b>Para qué sirve (negocio)</b>"), h2),
        Paragraph(escape(purpose), body),
        Spacer(1, 8),
        Paragraph(escape("<b>Módulos del proyecto que escriben o leen esta tabla</b>"), h2),
        op_table,
        Spacer(1, 10),
        Paragraph(escape("<b>Columnas</b>"), h2),
        Paragraph(
            escape(
                "Cada columna describe un atributo del registro. Los valores concretos vienen del nesting, "
                "CSV job_data, importación lista de largos o captura en taller según la tabla."
            ),
            body_compact,
        ),
        Spacer(1, 6),
        col_table,
        Spacer(1, 0.14 * inch),
    ]
    return block


def _footer(canvas, doc, font_name: str):
    canvas.saveState()
    fn = font_name if font_name != "Helvetica" else "Helvetica"
    canvas.setFont(fn, 8)
    canvas.setFillColor(colors.HexColor("#718096"))
    w, _ = doc.pagesize
    num = canvas.getPageNumber()
    canvas.drawRightString(w - doc.rightMargin, doc.bottomMargin - 15, f"Página {num}")
    canvas.restoreState()


def build_story(schema: list[dict]) -> list:
    font = _ensure_font()
    styles = _styles(font)
    story = _flow_intro(font, styles)

    current_domain = ""
    _, _, _, h1, *_rest = styles
    for entry in _ordered_schema(schema):
        tname = entry["table"]
        dom = next((f"{lid}. {title}" for lid, title, tl in DOMAIN_SECTIONS if tname in tl), "Otros")
        if dom != current_domain:
            current_domain = dom
            story.append(Paragraph(escape(f"——— {dom} ———"), h1))
        story.extend(_table_detail_section(entry, font, styles))
        story.append(PageBreak())

    if story and isinstance(story[-1], PageBreak):
        story.pop()
    return story


def main() -> None:
    if not SCHEMA_JSON.is_file():
        print(f"No existe {SCHEMA_JSON}; ejecute dump_db_schema_for_pdf.py", file=sys.stderr)
        sys.exit(1)
    schema = json.loads(SCHEMA_JSON.read_text(encoding="utf-8"))
    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    font = _ensure_font()

    doc = SimpleDocTemplate(
        str(OUT_PDF),
        pagesize=letter,
        leftMargin=0.65 * inch,
        rightMargin=0.65 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.72 * inch,
        title="nestingpro_db — manual de tablas",
        author="ARGA Nesting Suite",
    )

    def on_pg(canvas, di):
        _footer(canvas, di, font)

    doc.build(build_story(schema), onFirstPage=on_pg, onLaterPages=on_pg)
    print(f"PDF escrito: {OUT_PDF}")


if __name__ == "__main__":
    main()
