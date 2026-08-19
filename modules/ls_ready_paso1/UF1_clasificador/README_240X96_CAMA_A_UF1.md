# Clasificador LS-ready R3 — 240x96 / cama A / UF-1

Versión exclusiva para robot R3 derivada del flujo estable de R4.

## Conservado
- Normalización hacia MAX_X/MAX_Y con margen de 20 mm.
- Banda de columnas de 100 mm.
- Marcaje de derecha a izquierda y corte de izquierda a derecha.
- Figuras: inicio en esquina DXF `(X_max, Y_max)` (arranque físico abajo) con recorrido `Y_max → Y_min`.
- Entradas geométricas ya resueltas en DXF antes de UF-1.
- Barrenos individuales con E1 fijo.
- Transformación `X_UF1=6096-X_DXF`, `Y_UF1=2438-Y_DXF`.

## Cambio R3
- `robot_programming.robot=3`.
- Escena predeterminada L3.
- Mapa `R3_A_240X96_E1_J2_V1.json` con las 25 mediciones suministradas.
- Política y validación exclusivas R3.

Un JSON clasificado previamente para R4 se reconstruye desde `legacy/raw` antes de usar el mapa R3. Si no contiene esa geometría, el flujo lo rechaza para evitar reutilizar previews E1/J2 de otro robot.
