# CAMBIO — RANURAS FANUC LINE + ARC — R3 / CAMA B / UF-2 / 240X96

- El lector conserva simultáneamente `points` y `native_segments` para entidades DXF `LINE`, `ARC` y `CIRCLE`.
- Una ranura apta se reconoce como cadena cerrada de 2 `LINE` y 2 `ARC` semicirculares, con radios equivalentes y líneas paralelas.
- El clasificador resuelve orientación, punto inicial, cut-in y secuencia; el generador no reconstruye geometría.
- Política UF-2: inicio en menor X / menor Y DXF, cut-in diagonal `+2 mm X, +2 mm Y`, movimiento `follow_y`.
- Segmentos rectos se generan con `L`; arcos con `C`.
- Corte: 35 mm/sec, `CNT1` entre segmentos y `FINE` al cierre.
- Salida vertical: Z+100 a 100 mm/sec.
- Si la geometría nativa no cumple el contrato, se conserva el respaldo por puntos.
- Corrección adicional: `bbox_aspect_error = 0.0` se acepta como círculo exacto, por lo que los barrenos matemáticamente circulares conservan `C+C`.
