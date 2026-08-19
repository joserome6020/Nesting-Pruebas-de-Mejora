# Barrenos FANUC con dos arcos C

- Inicio DXF: `x_min`.
- Cut-in: `x_min + 3 mm`, hacia el centro/desperdicio.
- Primer arco: `x_min -> y_max -> x_max`, `CNT50`.
- Segundo arco: `x_max -> y_min -> x_min`, `FINE`.
- E1 fijo en todo el barreno.
- Salida vertical desde `x_min` a `Z+100 mm`, `100 mm/sec FINE`.
- Si el ajuste circular no cumple tolerancia, se conserva el contorno por puntos.
