# Motor de nesting C++ (Arga Suite)

Implementación nativa del empaquetado por hoja (`empaquetar_una_hoja_mc`) con **Clipper2** para operaciones geométricas. Todo el cálculo matemático del hot path corre en C++; Python solo orquesta.

## Alcance

| Componente | Motor |
|------------|-------|
| Empaque por hoja (hot path) | **C++** (`algorithm_cpp.pyd`) |
| Orquestación multi-placa, RTZ, costos, export | **Python** (`manager.py`) |

## Requisitos (Windows)

- Python 3.13/3.14
- [CMake](https://cmake.org/) 3.18+
- Visual Studio Build Tools (C++ / MSVC)
- Git (para descargar Clipper2 y pybind11 en el primer build)

## Compilar

Desde la raíz del repositorio:

```powershell
.\.venv\Scripts\Activate.ps1
powershell -ExecutionPolicy Bypass -File modules\nesting_engine\build_cpp_engine.ps1 -PythonExe ".\.venv\Scripts\python.exe"
```

Si no hay MSVC instalado:

```powershell
powershell -ExecutionPolicy Bypass -File modules\nesting_engine\build_cpp_engine.ps1 -PythonExe ".\.venv\Scripts\python.exe" -InstallMsvc
```

El script detecta Visual Studio (vswhere), usa generador `Visual Studio 17 2022` / `18 2026` con `-A x64`, o NMake si `cl` ya está en PATH.

Sin `algorithm_cpp.pyd` la aplicación no puede ejecutar nesting.

## Verificar

```powershell
py -3.14 -c "from modules.nesting_engine.algorithm_bridge import engine_name; print(engine_name())"
```

Debe imprimir `cpp_clipper2`.

## Perfil de optimización (Python)

Variables de entorno opcionales:

| Variable | Valores | Default |
|----------|---------|---------|
| `ARGA_NEST_MODE` | `first` (1 iter MC, sin refinado), `fast` (5), `standard` (15), `max` (30) | `first` |
| `ARGA_NEST_ITERATIONS` | 1–50 | (según modo) |

Modo `first` (default): 1 iteración MC por placa, sin refinado ni lookahead — primer acomodo del motor C++.

Modo `standard`: 15 iteraciones Monte Carlo por placa, compactación fina (C++), score de costo con lookahead de 2ª placa.
