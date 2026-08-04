# Contexto Global del Proyecto: ANS C++ (Arga Nesting Suite — Native Core)

> **IMPORTANTE:** Este árbol es la línea de evolución nativa en `C:\Proyectos\ANS C++`.  
> El ANS original vive en `C:\Proyectos\New Arga Nesting Suite` y no debe modificarse desde esta línea.  
> **Seguimiento vivo para agentes:** `AGENT_TRACKING.md` y `docs/cpp_migration/AGENT_TRACKING.md`  
> **Arquitectura del core:** `docs/cpp_migration/ARCHITECTURE.md`  
> **README de esta línea:** `README_ANS_CPP.md`

Este documento centraliza el estado actual del proyecto, la arquitectura del software y la visión a futuro del desarrollo del motor de nesting. **Cursor debe leer este documento para entender el contexto completo del proyecto antes de sugerir cambios arquitectónicos o de bajo nivel.**

## 1. Visión del Proyecto
El objetivo principal de "New Arga Nesting Suite" es desarrollar y gestionar un sistema de anidamiento (nesting) 2D irregular de última generación. Actualmente se utiliza un motor heredado ("Arga Lite"), pero el objetivo es construir o integrar un **motor de nesting en C++** que supere a las librerías open-source actuales (como libnest2d o Deepnest) en **velocidad y calidad**.

### Hipótesis para el Nuevo Motor C++ (Próximos pasos de desarrollo)
Tras una investigación exhaustiva, el nuevo motor C++ debería apuntar a resolver los cuellos de botella clásicos del cálculo de "No-Fit Polygons" (NFP) mediante las siguientes innovaciones:
1. **Aceleración GPU y Rasterización:** Reemplazar las matemáticas poligonales exactas en la CPU por operaciones lógicas de bits (píxeles/rasterización) en la tarjeta gráfica para detectar colisiones millones de veces más rápido.
2. **Simplificación Jerárquica (LOD):** Evaluar colisiones usando Bounding Boxes primero, luego Convex Hulls, luego polígonos simplificados, y finalmente el polígono exacto solo para los ajustes finales.
3. **Sembrado Heurístico con IA:** Entrenar un modelo de Machine Learning ligero para predecir el orden óptimo de las piezas a anidar, reduciendo las iteraciones del algoritmo genético (GA) de 10,000 a unas 100.
4. **Caché de NFP:** Pre-calcular y guardar en caché los NFP de piezas comunes/estándar de producción.

## 2. Arquitectura Actual (Python)

El proyecto acaba de pasar por un refactor masivo ("Lift-and-Shift") documentado en `REFACTOR_ARCHIVE_LOG.md` para reducir la deuda técnica de los monolitos heredados.

### A. Backend (Servidor API)
El backend está construido con **FastAPI** y se conecta a **PostgreSQL**.
- **`api_server.py`**: Actúa como fachada y punto de entrada para mantener retrocompatibilidad (delega atributos a `legacy_core`).
- **Directorio `api/`**:
  - `database.py`: Conexión a PostgreSQL (ahora segura con variables `.env`).
  - `models.py`: Esquemas de Pydantic.
  - `legacy_core.py`: Contiene +100 funciones "helpers" y utilidades heredadas.
  - `routers/`: Contiene 38 rutas divididas por dominio de negocio (`nesting.py`, `work_orders.py`, `lista_largos.py`, `produccion.py`, `reportes.py`).

### B. Frontend / UI (PySide6 / Qt)
La interfaz principal (anteriormente CustomTkinter, ahora PySide6) tiene su lógica de nesting modularizada mediante el **Patrón Mixin** para evitar monolitos de +8,000 líneas.
- **`tab_nesting.py`** (`interface/qt/tabs/`): Es el "pegamento" de la interfaz y delega la lógica pesada a sus mixins.
- **Mixins**:
  - `_mixin_nesting_calc.py`: Lógica principal de renesteo y comunicación con el motor C++.
  - `_mixin_export.py`: Generación de DXF de corte, STEP 3D, PDF y archivos `.arganest`.
  - `_mixin_plate_mgmt.py`: Gestión de placas en vivo, empaquetado y mermas RTZ.
  - `_mixin_transfer.py`: Transferencia de piezas entre órdenes y multilotes.
  - `_mixin_lote_edit.py`: Edición manual del lote activo.

## 3. Reglas Generales para Cursor
1. **Respetar la arquitectura modular:** No vuelvas a agregar métodos masivos a `tab_nesting.py` ni rutas directas a `api_server.py`. Utiliza los mixins y los routers correspondientes.
2. **Cuidado con `legacy_core.py`:** Las funciones allí son frágiles y se usan en toda la aplicación; cualquier cambio debe probarse exhaustivamente.
3. **C++ Development:** Al escribir código para el nuevo motor C++, prioriza la modularidad, el uso de OpenMP/CUDA si se aplican las hipótesis de GPU, y mantén el binding con Python (pybind11 o similar) limpio y documentado.
4. **Archivos generados:** Respeta el `.gitignore` y no hagas commit de respaldos locales, archivos de contexto, o `.env`.
