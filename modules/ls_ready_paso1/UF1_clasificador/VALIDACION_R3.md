# Validación R3 / cama A / UF-1 / 240x96

## Mapa
- JSON válido.
- 25 nodos únicos: A0..A4, B0..B4, C0..C4, D0..D4 y F0..F4.
- Metadata: `R3_A_240X96_E1_J2_V1` / robot `R3`.
- Se conservaron exactamente las mediciones suministradas, incluidas las excepciones C0 y F0.

## Clasificador
- Pruebas sintéticas: OK.
- Prueba completa principal: 3 piezas, 6 pasos de grabado, 45 pasos de corte y 51 pasos totales.
- Orden de grabado y corte conservado según metodología de cama A.
- Validación LS-ready: OK, 0 errores y 0 advertencias.
- JSON R4 con geometría `legacy/raw`: reconstruido correctamente con mapa R3.
- JSON R4 sin posibilidad de reconstrucción segura: se rechaza.

## Generador
- Perfil: `R3_A_UF1_240X96_V1`.
- Prueba completa principal: 51 operaciones detectadas, 51 generadas, 0 omitidas.
- LINE_COUNT: 5824 posiciones.
- Líneas `/MN`: 6290.
- Eventos de límite E1: 0.
- Issues: 0.
- Un JSON R4 fue rechazado con `INVALID_ROBOT_PROFILE` y no produjo archivo LS.

## Escuadra
La comparación automática confirmó que las primeras 13 líneas `/MN` y P[1]..P[6] son idénticas a la base R4, conforme a la confirmación del usuario.

## Auditoría adicional
En otro nesting de 4 piezas el cálculo de un punto del contorno exterior A alcanzó E1=-3696.510 mm. El resguardo heredado del generador lo limitó a -3670.000 mm y lo registró en `e1_limit_events`. El mapa no fue alterado para ocultar este comportamiento.
