# -*- coding: utf-8 -*-
"""
Reglas de clasificacion y orden — placa 240x48 (scene_size_key 240X48).

Aplica a nesteos con tres piezas en fila (layout base), marcaje der->izq,
corte en vuelta izq->der cuando hay fase MARK, etc.
"""
PROFILE_KEY = "240X48"
PLATE_SIZE_IN = "240x48"
STATUS = "active"
DESCRIPTION = (
    "Placa 240x48: piezas A..Z por max_x der->izq; marcaje fig+texto; "
    "corte barrenos->inner->contorno; grupos barrenos 800x1300."
)

# Flujo documentado (referencia para carrusel / orquestador)
FLOW = {
    "mark_direction": "right_to_left",
    "cut_direction_when_mark": "left_to_right",
    "cut_direction_when_no_mark": "left_to_right",  # ver docs: candidato a C->B->A
    "mark_piece_order": "max_x_descending",
    "cut_piece_order_with_mark": "reverse_mark_order",
}

OVERRIDES = {
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
    "HOLE_GROUP_MAX_DX_MM": 800.0,
    "HOLE_GROUP_MAX_DY_MM": 1300.0,
    "PLATE_EDGE_STRIP_FILTER": True,
    "PLATE_EDGE_MAX_STRIP_THICKNESS_MM": 650.0,
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
}
