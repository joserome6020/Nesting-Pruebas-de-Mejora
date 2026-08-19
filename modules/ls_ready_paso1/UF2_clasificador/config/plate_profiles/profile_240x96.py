# -*- coding: utf-8 -*-
"""Reglas específicas — placa 240x96, cama B, R3, UF-2."""
PROFILE_KEY = "240X96"
PLATE_SIZE_IN = "240x96"
STATUS = "active"
DESCRIPTION = (
    "Placa 240x96 cama B: normalización activa, columnas por anchor de 100 mm, "
    "barrenos individuales, mapa E1/J2 por filas A/B/C/D/F y entrada LS-ready resuelta en DXF."
)

FLOW = {
    "mark_direction": "right_to_left",
    "cut_direction_when_mark": "left_to_right",
    "cut_direction_when_no_mark": "left_to_right",
    "mark_piece_order": "anchor_columns_right_to_left",
    "cut_piece_order_with_mark": "anchor_columns_left_to_right",
}

OVERRIDES = {
    # Se conservan las bases productivas del perfil 240x48.
    "FIGURE_LINE_X_TOL_MM": 40.0,
    "FIGURE_LINE_Y_TOL_MM": 40.0,
    "FIGURE_LINE_ORIENTATION_RATIO": 1.5,
    "FIGURE_LINE_MIN_LENGTH_MM": 400.0,
    "FIGURE_COMPOSITE_MERGE": True,
    "FIGURE_BBOX_PAD_MM": 2.0,
    "FIGURE_COMPOSITE_OVERLAP_MIN_MM": 30.0,
    "FIGURE_COMPOSITE_MAX_GAP_X_MM": 450.0,
    "FIGURE_COMPOSITE_MAX_GAP_Y_MM": 500.0,
    "TEXT_ORIENTATION_RATIO": 1.2,
    "TEXT_ORIENTATION_PERCENTILE_LO": 0.10,
    "TEXT_ORIENTATION_PERCENTILE_HI": 0.90,
    "PLATE_EDGE_STRIP_FILTER": True,
    "PLATE_EDGE_TOUCH_TOL_MM": 5.0,
    "PLATE_EDGE_MAX_STRIP_THICKNESS_MM": 650.0,
    # Remanente de esquina típico ~1081x1524; piezas reales mayores se conservan.
    "PLATE_CORNER_REMNANT_MAX_WIDTH_MM": 1200.0,
    "PLATE_CORNER_REMNANT_MAX_HEIGHT_MM": 1600.0,
    "TEXT_LASER_MODE": "continuous",
    "TEXT_CONTINUOUS_DROP_DUPLICATE_TOL_MM": 0.01,
    "FIGURE_LASER_MODE": "stroke",
    "FIGURE_STROKE_CONNECT_TOL_MM": 0.05,
    "FIGURE_COMPONENT_RECONSTRUCT": True,
    "FIGURE_COMPONENT_ENDPOINT_TOL_MM": 0.05,
    "FIGURE_COMPONENT_MICRO_GAP_MERGE_MM": 1.0,
    "FIGURE_COMPONENT_MICRO_GAP_MIN_OVERLAP_MM": 5.0,
    "TEXT_COMPONENT_MIN_STROKES": 80,
    "TEXT_COMPONENT_MAX_THICKNESS_MM": 90.0,
    "TEXT_COMPONENT_MAX_LONG_DIM_MM": 700.0,
    "TEXT_COMPONENT_EXCLUSION_PAD_MM": 2.0,
    "MIN_STROKE_LENGTH_MM": 0.8,

    # Reglas exclusivas 240x96.
    "LS_MARGIN_MM": 0.0,
    "NORMALIZE_NESTING": True,
    "NORMALIZATION_MARGIN_MM": 8.0,
    "COLUMN_BAND_MM": 100.0,
    "HOLE_GROUPING_MODE": "individual",
    "J2_WARNING_DEG": 55.0,
    "J2_PHYSICAL_LIMIT_DEG": 65.0,
}
