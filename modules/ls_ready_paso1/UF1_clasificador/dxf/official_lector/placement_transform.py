# -*- coding: utf-8 -*-
"""Helpers neutrales del lector DXF para el flujo 240x96 cama A.

El lector conserva coordenadas DXF/globales. La normalización, las entradas y
la conversión a UF-1 pertenecen exclusivamente al clasificador LS-ready.
"""
JSON_COORD_DECIMALS = 3
ORIGIN_TOLERANCE_MM = 1.0
BBOX_DIM_TOLERANCE_MM = 3.0


def _contour_area(contour):
    pts = contour.get("points") if isinstance(contour, dict) else contour
    if not pts or len(pts) < 3:
        return 0.0
    area = 0.0
    for i in range(len(pts)):
        x1, y1 = float(pts[i][0]), float(pts[i][1])
        x2, y2 = float(pts[(i + 1) % len(pts)][0]), float(pts[(i + 1) % len(pts)][1])
        area += x1 * y2 - x2 * y1
    return abs(area) * 0.5


def select_primary_plate_contour(plate_contours):
    if not plate_contours:
        return None, 0
    if len(plate_contours) == 1:
        return plate_contours[0], 1
    return max(plate_contours, key=_contour_area), len(plate_contours)


def build_meta_placement(sheet_info, ref_bbox, plate_contour_count=0):
    warnings = []
    if plate_contour_count > 1:
        warnings.append("Varios contornos PLATE ({0}); se usa el de mayor área".format(plate_contour_count))
    if not sheet_info:
        warnings.append("No hay PLATE/sheet válido en el DXF")
        return {"placement_ok": False, "placement_warnings": warnings, "coordinate_stage": "DXF_GLOBAL_ONLY"}
    bbox = list(sheet_info.get("bbox") or [0.0, 0.0, 0.0, 0.0])
    min_x, min_y, max_x, max_y = [float(v) for v in bbox]
    width = float(sheet_info.get("width_mm") or 0.0)
    height = float(sheet_info.get("height_mm") or 0.0)
    origin_ok = max(abs(min_x), abs(min_y)) <= ORIGIN_TOLERANCE_MM
    dims_ok = abs((max_x-min_x)-width) <= BBOX_DIM_TOLERANCE_MM and abs((max_y-min_y)-height) <= BBOX_DIM_TOLERANCE_MM
    if not origin_ok:
        warnings.append("Esq1 PLATE no está en (0,0); el clasificador normalizará la geometría útil.")
    if not dims_ok:
        warnings.append("El bbox de PLATE difiere de width/height; revisar dimensiones detectadas.")
    return {
        "placement_ok": bool(origin_ok and dims_ok),
        "placement_warnings": warnings,
        "coordinate_stage": "DXF_GLOBAL_ONLY",
        "target_classifier": "240X96_CAMA_A_UF1",
        "uf_conversion_applied": False,
        "normalization_applied": False,
        "ref_bbox": ref_bbox,
        "sheet_width_mm": width,
        "sheet_height_mm": height,
        "note": "El lector no convierte a UF. El clasificador normaliza a MAX_X/MAX_Y con 20 mm y genera previews UF-1.",
    }


def round_contour_coords(contours):
    out = []
    for contour in contours or []:
        item = dict(contour)
        rounded = []
        for point in contour.get("points") or []:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                rounded.append([round(float(point[0]), JSON_COORD_DECIMALS), round(float(point[1]), JSON_COORD_DECIMALS)])
            else:
                rounded.append(point)
        item["points"] = rounded
        out.append(item)
    return out
