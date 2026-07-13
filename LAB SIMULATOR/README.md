# LAB SIMULATOR

Copia experimental del cerebro de nesting. **El motor de produccion no se modifica.**

## Estructura

| Ruta | Contenido |
|------|-----------|
| `_backup_original/cpp/` | Snapshot del cerebro prod al crear el lab |
| `modules/nesting_engine/_BACKUP_BRAIN/cpp/` | Misma copia en el repo principal |
| `engine/cpp/packer_lab.cpp` | Cerebro experimental |
| `engine/bridge.py` | Carga `algorithm_cpp_lab.pyd` |
| `build_lab_engine.ps1` | Compila el modulo lab |

## Mejoras lab (v1)

1. **Anclas en concavidades** del perimetro DXF (huecos laterales).
2. **Modo interlock** para piezas repetidas (misma area + bbox): prioriza llenar huecos antes de apilar.
3. Campo `estrategia` en cada paso del timeline (`rectangular`, `interlock_repetida`, etc.).

## Compilar

```powershell
cd "LAB SIMULATOR"
powershell -ExecutionPolicy Bypass -File build_lab_engine.ps1
```

## Reproductor con motor lab

```powershell
cd "c:\Proyectos\New Arga Nesting Suite"
$env:ARGA_NEST_LAB = "1"
.\.venv\Scripts\python.exe tools\nest_sim_lab.py "_logs\sim_gene_prueba1\escenario.nestsim.json"
```

`tools\nest_sim_lab.py` activa `ARGA_NEST_LAB=1` automaticamente.

## Comparar prod vs lab

```powershell
# Produccion
$env:ARGA_NEST_LAB = "0"
python -c "... run_timeline_sim ... paso 2 ..."

# Lab
$env:ARGA_NEST_LAB = "1"
python -c "... run_timeline_sim ... paso 2 ..."
```
