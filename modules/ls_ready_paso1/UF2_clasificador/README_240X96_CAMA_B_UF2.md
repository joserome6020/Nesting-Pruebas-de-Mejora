# CLASIFICADOR LS-READY V3 — 240X96 CAMA B / R3 / UF-2

Este paquete traslada al robot **R3** la metodología productiva ya validada para placa 240x96 en cama B.
La geometría, normalización, orden de piezas, entradas de corte y transformación a UF-2 se conservan.
El cambio funcional principal es el mapa de postura E1/J2 y la identidad del robot objetivo.

## Configuración fija

- Robot objetivo: `R3`.
- Cama: `B`.
- User Frame: `UF-2`.
- Placa: `6096 x 2438 mm`.
- Normalización: activa hacia `X_DXF máximo / Y_DXF mínimo`, margen `20 mm`.
- Columnas: anchor fijo con banda de `100 mm`.
- Transformación: `X_UF2 = Y_DXF`, `Y_UF2 = 6096 - X_DXF`.
- Barrenos: paths individuales con `E1_fixed`, orden X/Y/distancia.
- Corte interno: entrada diagonal `+2 mm X_DXF / +2 mm Y_DXF`.
- Corte exterior: inicio mayor X / menor Y y entrada en retal.

## Mapa R3

Archivo activo:

`config/motion_maps/R3_B_240X96_E1_J2_V1.json`

Contiene 25 nodos en la misma malla 5x5 de filas `A/B/C/D/F` y columnas `0..4`.
Las mediciones se conservaron tal como fueron entregadas, incluyendo las excepciones de borde y saturación.

## Ejecución

```bat
run_selector_dxf.bat
```

O desde terminal:

```bash
python run_ls_ready_flow.py entrada.dxf
python run_ls_ready_flow.py entrada.json
```

## Pruebas

```bash
python tests/test_240x96_rules.py
python tests/ls_ready_v3_smoke_test.py
```

## Nota sobre el generador

El generador R4 no debe utilizarse directamente para producir un programa destinado a R3. Aunque la conversión DXF→UF-2 es igual, ese generador valida `robot=4` y contiene una plantilla fija P[1]..P[6] con una postura joint P[2] propia de R4. Se requiere una variante R3 con su plantilla física confirmada.
