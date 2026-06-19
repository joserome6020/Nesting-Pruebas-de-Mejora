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

- **Desktop App (principal):** interfaz en **PySide6 (Qt)** para operación diaria (`interface/qt/`)
- **API Web (complementaria):** `FastAPI` para consulta/actualización de datos desde clientes web

> La interfaz antigua en `CustomTkinter` (`interface/main_window.py`, `tab_*.py` en raíz) se mantiene como legacy; no es la UI por defecto.

### Módulos clave y responsabilidad

- `main.py`: entrypoint de la app desktop.
- `interface/qt/main_window.py`: ventana principal Qt y montaje de pestañas.
- `interface/qt/tabs/tab_files.py`: ingestión de DXF y preparación inicial.
- `interface/qt/tabs/tab_parts.py`: revisión de piezas, datos de componentes y apoyo visual.
- `interface/qt/tabs/tab_sheets.py`: gestión de placas/materiales para nesting.
- `interface/qt/tabs/tab_nesting.py`: ejecución, orquestación y exportación del nesting.
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

## Requisitos del sistema

| Componente | Obligatorio | Uso |
|---|---|---|
| **Python 3.10+** (recomendado 3.12–3.14) | Sí | App, API y scripts |
| **Git** | Sí (para compilar motor C++) | Descarga Clipper2 al compilar `algorithm_cpp.pyd` |
| **PostgreSQL** | Sí en producción | WO/SWO, trazabilidad, sincronización Herinox |
| **FreeCAD 1.0** | Para DXF → STEP | `freecad_runner.py` |
| **Visual Studio Build Tools** (MSVC) | Para motor C++ | Compilar `algorithm_cpp.pyd` |
| Rutas de red / carpetas compartidas | Según planta | Archivos operativos y AutoDXF |

## Instalación desde cero (clon nuevo)

Copia y ejecuta en **PowerShell** desde la carpeta del repositorio.

### 1) Crear entorno virtual e instalar dependencias Python

```powershell
cd "C:\ruta\al\clone\New Arga Nesting Suite"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python tools\auto_setup_dependencies.py
```

El script `tools/auto_setup_dependencies.py` analiza el código y instala solo lo que falta. Es la forma recomendada (también la usa `run_nesting_escritorio.bat`).

**Solo ver qué instalaría (sin instalar):**

```powershell
python tools\auto_setup_dependencies.py --dry-run
```

### 2) Compilar motor de nesting C++ (obligatorio para calcular nesting)

`algorithm_cpp.pyd` no va en git; hay que generarlo en cada PC.

```powershell
.\.venv\Scripts\Activate.ps1
python tools\auto_setup_dependencies.py --include-optional
powershell -ExecutionPolicy Bypass -File modules\nesting_engine\build_cpp_engine.ps1 -PythonExe ".\.venv\Scripts\python.exe"
```

Requiere: **MSVC Build Tools**, **git** y conexión a internet (clona Clipper2).

### 3) Activar entorno (sesiones siguientes)

```powershell
cd "C:\ruta\al\clone\New Arga Nesting Suite"
.\.venv\Scripts\Activate.ps1
```

## Ejecución del proyecto

### App desktop

```powershell
python main.py
```

Si `python` del sistema falla por alias de Microsoft Store:

```powershell
.\.venv\Scripts\python.exe main.py
```

Abrir un workspace guardado al arrancar:

```powershell
.\.venv\Scripts\python.exe main.py "C:\ruta\trabajo.arganest"
```

### API (servidor local)

```powershell
.\.venv\Scripts\python.exe -m uvicorn api_server:app --reload --host 127.0.0.1 --port 8000
```

Si el puerto 8000 está ocupado (`WinError 10013`):

```powershell
netstat -ano | findstr :8000
```

### Lanzador todo-en-uno (API + app)

```powershell
.\run_nesting_escritorio.bat
```

Crea `venv` si no existe, instala dependencias y levanta API + interfaz.

## Dependencias Python (pip)

Lista canónica mantenida en `tools/auto_setup_dependencies.py`.

### Base (app Qt + API + nesting)

| Paquete pip | Uso principal |
|---|---|
| `PySide6` | Interfaz gráfica Qt |
| `pillow` | Imágenes y thumbnails DXF |
| `psycopg2-binary` | PostgreSQL |
| `fastapi` | API REST |
| `uvicorn` | Servidor ASGI |
| `pydantic` | Modelos API |
| `reportlab` | PDFs de producción |
| `ezdxf` | Lectura/escritura DXF |
| `matplotlib` | Visualización 2D |
| `numpy` | Cálculo numérico |
| `shapely` | Geometría y nesting |
| `pandas` | Tablas e integración Herinox |
| `openpyxl` | Excel `.xlsx` |
| `xlrd` | Excel `.xls` legacy |

**Instalación manual equivalente (si no usas el script):**

```powershell
python -m pip install PySide6 pillow psycopg2-binary fastapi uvicorn pydantic reportlab ezdxf matplotlib numpy shapely pandas openpyxl xlrd
```

### Legacy Tk (solo si usas la UI antigua)

```powershell
python tools\auto_setup_dependencies.py --include-legacy-tk
```

Instala además: `customtkinter`

### Build / motor C++ / empaquetado EXE

```powershell
python tools\auto_setup_dependencies.py --include-optional
```

Instala además: `pyinstaller`, `setuptools`, `wheel`, `pybind11`, `cmake`

## Compilar ejecutable (.exe) para otras PCs

Script principal: `tools/build_arga_exe.py`  
Atajo Windows: `tools\build_arga_exe.bat`

### Primera vez en una PC nueva

```powershell
cd "C:\ruta\al\clone\New Arga Nesting Suite"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python tools\build_arga_exe.py --install-msvc
```

`--install-msvc` instala **Visual Studio 2022 Build Tools** (C++) vía winget si no hay compilador. Solo hace falta una vez por PC.

### Compilaciones siguientes

```powershell
.\.venv\Scripts\Activate.ps1
python tools\build_arga_exe.py
```

O doble clic en `tools\build_arga_exe.bat`.

### Opciones útiles

| Flag | Efecto |
|---|---|
| `--install-msvc` | Instala MSVC con winget si falta |
| `--force-cpp` | Recompila `algorithm_cpp.pyd` |
| `--skip-cpp` | No compila C++ (requiere `.pyd` existente) |
| `--skip-deps` | No actualiza paquetes pip |
| `--allow-no-cpp` | Empaqueta sin motor (nesting no funcionará) |
| `--onedir` | Carpeta en lugar de un solo `.exe` |

### Salida

- `dist\ArgaNestingSuite.exe` — ejecutable
- `dist\modules\Plates.xlsx` — inventario de placas
- `dist\arga_build_manifest.json` — manifiesto del build

Copia la carpeta `dist\` completa a otras PCs de planta.

### Requisitos del build

- Python 3.10+ con venv activo
- **MSVC** (Visual Studio Build Tools 2022, workload C++)
- **Git** (clona Clipper2 al compilar el motor C++)
- Conexión a internet (pip + Clipper2)

El script detecta Visual Studio automáticamente y elige el generador CMake correcto (ya no usa NMake sin compilador).

## Integraciones externas y supuestos

Este sistema depende de:

- conexión activa a **PostgreSQL** con el esquema esperado
- rutas de red/compartidas para archivos operativos
- **FreeCAD** instalado en ruta válida (por ejemplo `C:\Program Files\FreeCAD 1.0\bin\FreeCAD.exe`)
- **`algorithm_cpp.pyd`** compilado localmente para el cálculo de nesting

Si cambian rutas, credenciales o esquema DB, deben ajustarse parámetros en `config.py` y archivos de configuración del proyecto.

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
- **Faltan paquetes pip:** `python tools\auto_setup_dependencies.py`
- **Motor C++ no disponible al hacer nesting:** compilar con `modules\nesting_engine\build_cpp_engine.ps1` (ver sección de instalación).
- **API no levanta:** validar venv activa + `uvicorn` instalado + puerto libre.
- **Fallo DXF -> STEP:** validar instalación/ruta de FreeCAD y carpetas de entrada/salida.
- **Errores de BD:** validar credenciales, host/puerto y tablas esperadas.
- **Diferencias de contexto para IA:** regenerar con `scripts/generate_project_context.py`.

## Nota de mantenimiento

Este proyecto tiene alta integración entre UI, motor de nesting, exportación CAD y persistencia. Antes de cambios grandes:

- validar impacto en `tab_nesting.py`, `manager.py` y `reporte_pdf_nesting.py`
- correr pruebas funcionales sobre un WO/SWO real de ejemplo
- confirmar que exportación DXF/STEP y PDF siguen consistentes
