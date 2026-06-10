# ARGA-NESTING-SOFTWARE

Suite interna de Grupo Arga para nesting industrial: toma DXF de producción, procesa geometría, optimiza acomodo de piezas en placas, exporta DXF/STEP y genera reportes operativos (PDF + datos en PostgreSQL + API).

## Objetivo del sistema

Este proyecto existe para estandarizar el flujo completo de corte de placas metálicas:

- importar archivos técnicos (DXF) desde fuentes operativas
- limpiar y normalizar capas/entidades
- ejecutar el motor de nesting por calibre/material
- exportar resultados para corte (DXF) y modelado (STEP)
- reportar resultados para producción y trazabilidad
- sincronizar estado de operación con base de datos y API

## Arquitectura general

El sistema está compuesto por 2 superficies principales:

- **Desktop App (principal):** interfaz en `CustomTkinter` para operación diaria
- **API Web (complementaria):** `FastAPI` para consulta/actualización de datos desde clientes web

### Módulos clave y responsabilidad

- `main.py`: entrypoint de la app desktop.
- `interface/main_window.py`: ventana principal y montaje de pestañas.
- `interface/tab_files.py`: ingestión de DXF y preparación inicial.
- `tab_parts.py`: revisión de piezas, datos de componentes y apoyo visual.
- `tab_sheets.py`: gestión de placas/materiales para nesting.
- `interface/tab_nesting.py`: ejecución, orquestación y exportación del nesting.
- `modules/nesting_engine/manager.py`: núcleo de cálculo/estrategia de nesting.
- `modules/nesting_engine/exporter.py`: exportación de layouts/artefactos de salida.
- `modules/nest_exporter.py`: lógica adicional de exportación geométrica.
- `modules/processed_layers.py`: normalización/limpieza de capas DXF.
- `reporte_pdf_nesting.py`: reporte PDF de producción.
- `interface/postgres_connector.py`: integración y persistencia PostgreSQL.
- `api_server.py`: endpoints operativos para WO/SWO, piezas, placas y lista de largos.
- `freecad_runner.py`: puente para conversión DXF -> STEP con FreeCAD.

## Flujo operativo end-to-end

1. El operador inicia la app (`main.py`).
2. Se cargan DXF y metadatos de trabajo.
3. Se procesa geometría y capas para consistencia de corte.
4. Se seleccionan placas/material/calibre para el trabajo.
5. El motor de nesting calcula acomodos válidos.
6. Se exportan resultados:
   - DXF de producción
   - STEP (si aplica vía FreeCAD)
   - PDF de reporte
7. Se registra avance/resultados en BD y API.

## Ejecución del proyecto

### 1) Entorno recomendado

- Python 3.x
- PostgreSQL
- FreeCAD 1.0
- Git

### 2) Activar entorno virtual (Windows PowerShell)

```powershell
.\.venv\Scripts\Activate.ps1
```

### 3) Ejecutar app desktop

```powershell
python main.py
```

Si `python` del sistema falla por alias de Microsoft Store, usa:

```powershell
.\.venv\Scripts\python.exe main.py
```

### 4) Ejecutar API

```powershell
uvicorn api_server:app --reload
```

## Dependencias principales

Instalación base (referencial):

```bash
pip install customtkinter pillow psycopg2-binary fastapi uvicorn pydantic reportlab ezdxf matplotlib numpy shapely pandas PySide6
```

## Integraciones externas y supuestos

Este sistema depende de:

- conexión activa a PostgreSQL con esquema esperado
- rutas de red/compartidas para archivos operativos
- FreeCAD instalado en ruta válida (por ejemplo `C:\Program Files\FreeCAD 1.0\bin\FreeCAD.exe`)

Si cambian rutas, credenciales o esquema DB, deben ajustarse parámetros/configuración del proyecto.

## Archivos de entrada y salida relevantes

Entradas frecuentes:

- DXF de producción
- inventarios/listas en Excel o CSV (según flujo)
- datos WO/SWO en base de datos

Salidas frecuentes:

- DXF exportados para corte
- STEP exportados vía FreeCAD
- PDF de producción (`reporte_pdf_nesting.py`)
- registros y trazabilidad en PostgreSQL

## API (resumen rápido)

La API expone endpoints para:

- jobs y work orders (`WO`/`SWO`)
- detalles de placa y piezas
- lista de largos (plan/sesión/progreso/finalización)
- descarga de reportes PDF

Entry point API: `api_server.py`

## Contexto automático del proyecto (IA + documentación viva)

Este repositorio genera contexto técnico para humanos e IA:

- `PROJECT_CONTEXT.md`: resumen técnico legible
- `project_context.json`: estructura completa machine-readable
- `PROJECT_CODE_LISTING.txt`: volcado exacto de archivos con encabezado por ruta

### Regenerar manualmente

```bash
python scripts/generate_project_context.py
```

### Auto-actualización en commit (recomendado)

Instala hook local una sola vez:

```bash
python scripts/install_context_hook.py
```

Luego, cada `git commit` regenera y agrega automáticamente:

- `PROJECT_CONTEXT.md`
- `project_context.json`
- `PROJECT_CODE_LISTING.txt`

## Troubleshooting rápido

- **`python` no encontrado en Windows:** usar `.\.venv\Scripts\python.exe`.
- **API no levanta:** validar venv activa + `uvicorn` instalado + puerto libre.
- **Fallo DXF -> STEP:** validar instalación/ruta de FreeCAD y carpetas de entrada/salida.
- **Errores de BD:** validar credenciales, host/puerto y tablas esperadas.
- **Diferencias de contexto para IA:** regenerar con `scripts/generate_project_context.py`.

## Nota de mantenimiento

Este proyecto tiene alta integración entre UI, motor de nesting, exportación CAD y persistencia. Antes de cambios grandes:

- validar impacto en `tab_nesting.py`, `manager.py` y `reporte_pdf_nesting.py`
- correr pruebas funcionales sobre un WO/SWO real de ejemplo
- confirmar que exportación DXF/STEP y PDF siguen consistentes
