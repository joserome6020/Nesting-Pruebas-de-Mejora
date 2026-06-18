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

```powershell
cd "c:\Proyectos\ANS Pruebas de mejora\modules\nesting_engine"
.\build_cpp_engine.ps1
```

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
| `ARGA_NEST_MODE` | `fast` (5 iter), `standard` (15), `max` (30) | `standard` |
| `ARGA_NEST_ITERATIONS` | 1–50 | (según modo) |

Modo `standard`: 15 iteraciones Monte Carlo por placa, compactación fina (C++), score de costo con lookahead de 2ª placa.
