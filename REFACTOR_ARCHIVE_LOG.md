# 📝 Registro de Refactorización (Fases 1–3 y correcciones)
**Fecha:** Julio 2026
**Propósito:** Limpieza de código heredado (Legacy) y modularización de la arquitectura del servidor API backend. 

Este archivo sirve como referencia técnica para *Cursor* y otros desarrolladores sobre los cambios estructurales aplicados al repositorio para reducir la deuda técnica y mejorar el mantenimiento.

---

## 🧹 Fase 1: Limpieza Profunda (Cleanup)
Se eliminaron archivos residuales, respaldos muertos y código obsoleto para reducir el peso y la confusión del repositorio.

1. **Eliminación de Respaldos (Backups):**
   - Se borró el directorio `_ref_oficial/` (un clon duplicado completo).
   - Se eliminaron las carpetas de respaldos antiguos como `modules/nesting_engine/_BACKUP_BRAIN/`.
   - Se borraron archivos terminados en `.bak` y `.prev`.
2. **Limpieza de Interfaz Antigua (CustomTkinter):**
   - Se borraron todos los archivos legacy dentro de `interface/` que pertenecían al framework viejo de CustomTkinter (ej. `tab_files.py`, `tab_nesting.py`, `nesting_canvas.py` original, etc.).
   - *Nota:* El motor lógico `interface/nesting_workspace.py` **se conservó** ya que es utilizado directamente por la nueva UI de PySide6 (`interface/qt/tabs/tab_nesting.py`).
3. **Limpieza de Directorio de Herramientas (`tools/`):**
   - Se auditaron y eliminaron más de 80 scripts de "one-shot" y benchmarks antiguos (`_audit_*.py`, `_bench_*.py`, `_test_*.py`) que solo hacían ruido.
4. **Basura Generada (Ignorada en Git):**
   - Se actualizaron las reglas del archivo `.gitignore` para omitir `*.arganest`, `*.pdf`, archivos de contexto `Contexto_*.txt`, logs generados y respaldos `.sql`.

---

## 🏗️ Fase 2: Modularización del `api_server.py` (Arquitectura API)
El archivo monolítico `api_server.py` (de +4,700 líneas y 136 funciones) era insostenible. Se realizó un refactor "Lift-and-Shift" **sin alterar la lógica de negocio** separando el servidor en módulos.

### Nueva Estructura:
- **`api_server.py`**: Fachada de entrada y compatibilidad. Registra los *routers*, expone la aplicación FastAPI y mantiene temporalmente el contrato de helpers heredados.
- **`.env` (NUEVO)**: Se extrajeron las contraseñas "hardcodeadas" de Postgres. Ahora se leen de forma segura (este archivo está excluido en git).

### Carpeta `api/` (Nueva Arquitectura):
1. **`api/database.py`**: Centraliza la conexión a PostgreSQL (psycopg2) consumiendo las variables del `.env`.
2. **`api/models.py`**: Contiene exclusivamente las clases/esquemas de **Pydantic** (`BaseModel`) usadas para validar *payloads*.
3. **`api/legacy_core.py`**: Agrupa más de 100 funciones "helpers", utilidades (ej. validaciones, formateos de diccionarios) y la inicialización base de FastAPI original y CORS. **Cualquier función sin la anotación `@app` terminó aquí.**
4. **`api/routers/`**: Se utilizó un extractor AST (Abstract Syntax Tree) para mover de forma matemáticamente exacta las 38 rutas de la API a sus dominios específicos:
   - `nesting.py`: Endpoints relacionados con trabajos de nesting y placas (`/api/jobs`, `/api/nesting`, `/api/placas`).
   - `work_orders.py`: Endpoints de WO y SWO (`/api/work_orders`, `/api/wo`, `/api/swo`).
   - `lista_largos.py`: Toda la lógica de sesiones de perfiles y cortes longitudinales (`/api/lista-largos`).
   - `produccion.py`: Endpoints para las tablets/interfaces de piso y máquinas (`/api/piezas`, `/api/validar-material`, `/api/corte`).
   - `reportes.py`: Endpoints de guardado dinámico de estado.

### 🛡️ Seguridad Post-Refactor
- **NO hubo pérdida de rutas:** Se validó de forma programática que las 38 rutas `/api/...` siguen registradas bajo los mismos paths.
- **NO hubo reescritura lógica:** Los cuerpos de las funciones se movieron 1:1, asegurando que el comportamiento de los cálculos de Nesting o SQL siga siendo idéntico. Todo importa sus dependencias resolviéndose en `api/legacy_core.py`.

---

## 🧩 Fase 3: Desacoplamiento de `tab_nesting.py` (Front-End)
El archivo `interface/qt/tabs/tab_nesting.py` era el "Objeto Dios" de la interfaz, con 8,414 líneas y 252 métodos que mezclaban renderizado, cálculo de nesting y exportación.

### Estrategia: Patrón Mixin
Para evitar romper dependencias internas (miles de llamadas cruzadas con `self.` y `self.app.`), se utilizó un patrón de herencia Mixin. Se extrajeron **202 métodos** a 5 módulos lógicos dedicados, conservando intacto el comportamiento interno:

1. **`_mixin_export.py`** (~700 líneas): Todos los métodos para generar DXF de corte, STEP 3D, PDF y archivos `.arganest`.
2. **`_mixin_nesting_calc.py`** (~3,300 líneas): La lógica principal (pesada) de renesteo, llamadas al motor en C++, hilos de procesamiento y compensaciones de plasma.
3. **`_mixin_plate_mgmt.py`** (~1,900 líneas): Gestión del inventario en vivo de placas, empaquetado, manejo de mermas RTZ y actualizaciones del panel lateral.
4. **`_mixin_transfer.py`** (~800 líneas): Todo el código relacionado con mover piezas entre órdenes (Work Orders / SWO) y manejo de `multilotes`.
5. **`_mixin_lote_edit.py`** (~650 líneas): Acciones de edición libre del lote activo (agregar piezas sueltas, parsear nombres de DXFs para cantidades).

### Archivo Central Resultante
- **`tab_nesting.py`**: Quedó reducido a ~810 líneas (50 métodos). Ahora actúa únicamente como "pegamento" de estos 5 Mixins y concentra el ciclo de vida principal del widget (constructor `__init__`, guardado de workspace y Helpers UI básicos).

### 🛡️ Seguridad Post-Refactor
- **Script AST Estricto:** La extracción de código no se hizo a mano; un analizador de sintaxis (AST) cortó cada función y decorador byte a byte para garantizar que la reubicación a los mixins sea matemáticamente perfecta.
- La aplicación sigue arrancando e importando la clase `TabNesting` completa (con todos sus 252 métodos intactos heredados).

---

## ✅ Corrección posterior: contrato de compatibilidad de `api_server.py`
**Fecha:** 27 de julio de 2026
**Motivo:** Corrección preventiva posterior a la Fase 2, identificada durante la revisión integral tras la Fase 3.

### Hallazgo
La fachada nueva de `api_server.py` registraba correctamente los routers, pero dejó de exponer helpers que sí formaban parte del contrato del servidor monolítico. Esto rompía imports activos de:

- `interface/largos_nesting_service.py`: `_extraer_factor_wo`, `_obtener_lista_base_por_job`, `_expandir_lista_para_wo`, `_ll_generar_plan_desde_payload` y `_ll_obtener_o_generar_plan`.
- `modules/lista_largos_importer.py`: `_propagar_material_requerido_por_job`.

El código de esos helpers seguía intacto en `api/legacy_core.py`; el problema era exclusivamente de exposición desde la fachada.

### Corrección aplicada
- `api_server.py` ahora delega atributos no locales a `api.legacy_core` mediante `__getattr__`.
- `__dir__` incluye los nombres delegados para conservar la experiencia de inspección y depuración.
- `__all__` conserva la semántica de `from api_server import *` para los nombres públicos del servidor previo.
- No se alteró lógica de negocio, SQL, payloads, rutas ni comportamiento de los helpers.

### Verificación realizada
- Los seis imports activos anteriores resuelven al mismo objeto definido en `api.legacy_core`.
- `from api_server import *` vuelve a exponer helpers públicos heredados.
- `api_server.py`, `interface/largos_nesting_service.py` y `modules/lista_largos_importer.py` compilan sin errores.
- La aplicación conserva sus 38 rutas `/api/...` registradas.

### Seguimiento
Esta fachada es una capa de compatibilidad temporal, no el destino final. En una fase posterior, los consumidores deberán importar servicios de dominio explícitos; la delegación solo debe eliminarse después de confirmar que no queden referencias activas a helpers de `api_server`.
