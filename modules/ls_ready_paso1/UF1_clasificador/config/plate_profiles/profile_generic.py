# -*- coding: utf-8 -*-
"""Perfil generico minimo cuando no hay reglas especificas para la placa."""
PROFILE_KEY = "generic"
STATUS = "fallback"
DESCRIPTION = "Clasificacion basica sin reglas de marcaje/figuras avanzadas."

OVERRIDES = {
    "FIGURE_COMPOSITE_MERGE": False,
    "FIGURE_LINE_MIN_LENGTH_MM": 0.0,
    "PLATE_EDGE_STRIP_FILTER": True,
    "PLATE_EDGE_MAX_STRIP_THICKNESS_MM": 650.0,
}

FLOW = {
    "mark_direction": "right_to_left",
    "cut_direction_when_mark": "left_to_right",
    "cut_direction_when_no_mark": "left_to_right",
}
