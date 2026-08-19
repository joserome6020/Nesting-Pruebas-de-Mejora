# -*- coding: utf-8 -*-
"""Normalización del nesting 240x96 antes de resolver entradas y mapa E1/J2."""
from __future__ import annotations

import copy

from .contours import extract_contour_info
from .geometry import bbox_of_points

_POINT_KEYS = ("points", "polyline", "vertices", "pts", "path")


def _translate_point(point, dx, dy):
    if isinstance(point, dict):
        out = copy.deepcopy(point)
        if "x" in out or "y" in out:
            out["x"] = float(out.get("x", 0.0)) + dx
            out["y"] = float(out.get("y", 0.0)) + dy
        elif "X" in out or "Y" in out:
            out["X"] = float(out.get("X", 0.0)) + dx
            out["Y"] = float(out.get("Y", 0.0)) + dy
        return out
    out = list(point)
    out[0] = float(out[0]) + dx
    out[1] = float(out[1]) + dy
    return out



def _translate_native_segment(segment, dx, dy):
    out = copy.deepcopy(segment or {})
    for key in ("start_dxf", "end_dxf", "center_dxf", "via_point_dxf"):
        value = out.get(key)
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            out[key] = [float(value[0]) + dx, float(value[1]) + dy]
    return out

def _translate_raw_item(item, dx, dy):
    if isinstance(item, dict):
        out = copy.deepcopy(item)
        for key in _POINT_KEYS:
            if key in out and isinstance(out[key], list):
                out[key] = [_translate_point(p, dx, dy) for p in out[key]]
                break
        if isinstance(out.get("native_segments"), list):
            out["native_segments"] = [
                _translate_native_segment(segment, dx, dy)
                for segment in out["native_segments"]
            ]
        return out
    if isinstance(item, list):
        return [_translate_point(p, dx, dy) for p in item]
    return copy.deepcopy(item)


def normalize_nesting_geometry(cut_outer, cut_inner, mark, sheet_info, margin_mm=10.0):
    """Mueve toda la geometría útil hacia X máximo y Y mínimo.

    `cut_outer` ya debe venir filtrado sin el contorno de placa. El mismo dx/dy
    se aplica a piezas, cortes internos y marcajes. El contorno de placa no se
    usa para calcular el área útil.
    """
    sheet = sheet_info or {}
    width = float(sheet.get("width_mm") or 0.0)
    height = float(sheet.get("height_mm") or 0.0)
    margin = float(margin_mm)
    if width <= 0.0 or height <= 0.0:
        raise ValueError("No hay dimensiones válidas de placa para normalizar.")

    useful_points = []
    for item in cut_outer or []:
        points, _closed = extract_contour_info(item)
        useful_points.extend(points)
    if not useful_points:
        raise ValueError("No hay piezas cut_outer útiles para normalizar el nesting.")

    bb = bbox_of_points(useful_points)
    min_x, min_y, max_x, max_y = [float(v) for v in bb]
    useful_width = max_x - min_x
    useful_height = max_y - min_y

    # Validar primero si el área útil excede físicamente las dimensiones de la placa
    if useful_width > width + 1e-6 or useful_height > height + 1e-6:
        raise ValueError(
            "El área útil {:.3f}x{:.3f} mm excede físicamente las dimensiones de la placa {:.3f}x{:.3f} mm.".format(
                useful_width, useful_height, width, height
            )
        )

    # Margen adaptable: si el margen solicitado no cabe, se reduce al valor máximo disponible
    max_allowable_margin_x = max(0.0, width - useful_width)
    max_allowable_margin_y = max(0.0, height - useful_height)

    effective_margin_x = min(margin, max_allowable_margin_x)
    effective_margin_y = min(margin, max_allowable_margin_y)

    margin_adjusted = (
        abs(effective_margin_x - margin) > 1e-4 or abs(effective_margin_y - margin) > 1e-4
    )

    dx = width - effective_margin_x - max_x
    dy = effective_margin_y - min_y
    new_min_x = min_x + dx
    new_max_y = max_y + dy
    if new_min_x < -1e-6 or new_max_y > height + 1e-6:
        raise ValueError(
            "Tras anclar a max-X/min-Y con margen adaptativo ({:.3f}, {:.3f}) mm, el nesting sale de la placa "
            "(min_x={:.3f}, max_y={:.3f}; placa {:.3f}x{:.3f}).".format(
                effective_margin_x, effective_margin_y, new_min_x, new_max_y, width, height
            )
        )

    translated_outer = [_translate_raw_item(item, dx, dy) for item in (cut_outer or [])]
    translated_inner = [_translate_raw_item(item, dx, dy) for item in (cut_inner or [])]
    translated_mark = [_translate_raw_item(item, dx, dy) for item in (mark or [])]

    return translated_outer, translated_inner, translated_mark, {
        "enabled": True,
        "always_evaluate": True,
        "coordinate_stage": "DXF_BEFORE_ENTRY_AND_E1_MAP",
        "direction": "MAX_X_MIN_Y",
        "target_frame": "UF2",
        "margin_mm": round(margin, 4),
        "effective_margin_x_mm": round(effective_margin_x, 4),
        "effective_margin_y_mm": round(effective_margin_y, 4),
        "margin_adjusted": margin_adjusted,
        "dx_mm": round(dx, 4),
        "dy_mm": round(dy, 4),
        "original_useful_bbox_dxf": [round(v, 4) for v in (min_x, min_y, max_x, max_y)],
        "normalized_useful_bbox_dxf": [
            round(min_x + dx, 4), round(min_y + dy, 4),
            round(max_x + dx, 4), round(max_y + dy, 4),
        ],
        "useful_width_mm": round(useful_width, 4),
        "useful_height_mm": round(useful_height, 4),
        "plate_contour_used_for_bbox": False,
        "inverse_transform": {
            "x_original": "x_normalized - dx_mm",
            "y_original": "y_normalized - dy_mm",
        },
    }
