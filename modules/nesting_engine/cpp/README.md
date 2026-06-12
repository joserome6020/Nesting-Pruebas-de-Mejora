# Motor de nesting C++ (Arga Suite)

Implementación nativa del empaquetado por hoja (`empaquetar_una_hoja_mc`), portada desde `algorithm.pyx` con **Clipper2** para operaciones geométricas.

## Alcance

| Componente | Motor |
|------------|-------|
| Empaque por hoja (hot path) | **C++** (`algorithm_cpp.pyd`) o fallback Cython |
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

Fuerza el motor legacy Cython:

```powershell
$env:ARGA_NESTING_ENGINE = "cython"
py -3.14 main.py
```

## Verificar

```powershell
py -3.14 -c "from modules.nesting_engine.algorithm_bridge import engine_name; print(engine_name())"
```

Debe imprimir `cpp_clipper2` si el `.pyd` está compilado.
