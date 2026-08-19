# -*- coding: utf-8 -*-
"""
Configuración del lector DXF para generar el JSON crudo que consume LS READY V3.

IMPORTANTE:
- Si el DXF ya trae layers claros, agrega aquí los nombres reales.
- Si un layer no coincide, el lector usa una clasificación geométrica de respaldo:
  contornos cerrados exteriores -> cut_outer, contornos cerrados contenidos -> cut_inner,
  trazos abiertos -> mark.
"""

# Comparación insensible a mayúsculas, espacios, guiones y guiones bajos.
PLATE_LAYER_ALIASES = {
    "PLATE", "SHEET", "LAMINA", "LÁMINA", "PLACA", "BORDER", "MARCO_PLACA",
}

CUT_OUTER_LAYER_ALIASES = {
    "CUT_OUTER", "OUTER", "CONTOUR", "CONTOURS", "CONTORNO", "CONTORNOS",
    "CORTE_EXTERIOR", "CORTE_EXT", "CUT", "CORTE",
}

CUT_INNER_LAYER_ALIASES = {
    "CUT_INNER", "INNER", "HOLE", "HOLES", "BARRENO", "BARRENOS",
    "CORTE_INTERIOR", "CORTE_INT", "CUT_HOLES",
}

MARK_LAYER_ALIASES = {
    "MARK", "MARKING", "GRABADO", "MARCAJE", "TEXT", "TEXTOS", "TEXTO",
    "ENGRAVE", "ETCH", "SCORE",
}

# Segmentación para convertir círculos/arcos a puntos.
CIRCLE_SEGMENTS = 72
ARC_SEGMENTS = 36
BULGE_SEGMENTS_PER_180_DEG = 18

# Si no existe layer PLATE, se intenta inferir la placa por dimensiones conocidas.
KNOWN_SHEET_SIZES_MM = [
    (6096.0, 1219.0, "240x48", "240X48"),
    (6096.0, 1219.2, "240x48", "240X48"),
    (6096.0, 1524.0, "240x60", "240X60"),
    (6096.0, 2438.0, "240x96", "240X96"),
    (6096.0, 2438.4, "240x96", "240X96"),
]
SHEET_SIZE_TOL_MM = 5.0

# Clasificación de respaldo: contornos muy pequeños cerrados normalmente son cut_inner,
# pero el método principal es por contención geométrica dentro de un cut_outer.
MIN_CLOSED_AREA_MM2 = 0.01
