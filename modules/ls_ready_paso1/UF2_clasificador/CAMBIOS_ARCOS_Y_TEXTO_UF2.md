# R3 240x96 Cama B / UF-2 — arcos FANUC y guardia de texto

## Barrenos

- El clasificador ajusta cada contorno de barreno a un círculo por mínimos cuadrados.
- Si cumple tolerancias, entrega `generator_path_dxf.motion_type = fanuc_two_arcs`.
- Secuencia: `x_min -> y_max -> x_max -> y_min -> x_min`.
- Primer arco: `CNT50`; segundo arco: `FINE`.
- Entrada: `x_min + 3 mm` en DXF, hacia el desperdicio interno.
- Salida: elevación vertical desde `x_min` a `Z+100` a `100 mm/sec`.
- Todo el barreno conserva E1 fijo.
- Si no cumple tolerancias, conserva `linear_points_fallback` con los puntos originales.

## Texto

La base recibida ya incluía el guardia `TEXT_COMPONENT_MAX_LONG_DIM_MM = 700 mm` en `piece_plan.py`:

- Texto real compacto permanece como `mark_text` con E1 fijo.
- Bloques dispersos mayores a 700 mm se reasignan a `mark_figure` y usan `follow_y`.

## Archivos principales modificados

- `classification/geometry.py`
- `classification/ls_ready_v3.py`
- `ls_generator/config.py`
- `ls_generator/ls_writer.py`
- `ls_generator/motion_builder.py`
- `ls_generator/validators.py`
