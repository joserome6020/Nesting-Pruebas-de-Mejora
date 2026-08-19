# -*- coding: utf-8 -*-
"""Utilidades geometricas para clasificacion de piezas."""
import math

POINT_EQUAL_TOL = 1e-6


def dist2d(a, b):
    return math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))


def points_equal(p1, p2, tol=POINT_EQUAL_TOL):
    return abs(float(p1[0]) - float(p2[0])) <= tol and abs(float(p1[1]) - float(p2[1])) <= tol


def clean_points(raw_points):
    out = []
    for p in raw_points or []:
        if not p or len(p) < 2:
            continue
        pt = (float(p[0]), float(p[1]))
        if out and points_equal(out[-1], pt):
            continue
        out.append(pt)
    return out


def bbox_of_points(points):
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def bbox_center(bb):
    return ((bb[0] + bb[2]) * 0.5, (bb[1] + bb[3]) * 0.5)


def bbox_width(bb):
    return max(0.0, bb[2] - bb[0])


def bbox_height(bb):
    return max(0.0, bb[3] - bb[1])


def polygon_centroid(points):
    pts = list(points or [])
    if not pts:
        return (0.0, 0.0)
    if len(pts) > 1 and points_equal(pts[0], pts[-1]):
        pts = pts[:-1]
    if not pts:
        return (0.0, 0.0)
    if len(pts) == 1:
        return (pts[0][0], pts[0][1])
    cx = sum(p[0] for p in pts) / float(len(pts))
    cy = sum(p[1] for p in pts) / float(len(pts))
    return (cx, cy)


def point_in_bbox(pt, bb, margin=0.0):
    x, y = float(pt[0]), float(pt[1])
    return (
        x >= bb[0] - margin
        and x <= bb[2] + margin
        and y >= bb[1] - margin
        and y <= bb[3] + margin
    )


def point_in_polygon(point, polygon):
    """Ray casting; polygon puede estar cerrado o abierto."""
    x, y = float(point[0]), float(point[1])
    pts = list(polygon or [])
    if len(pts) > 1 and points_equal(pts[0], pts[-1]):
        pts = pts[:-1]
    n = len(pts)
    if n < 3:
        return False

    inside = False
    j = n - 1
    i = 0
    while i < n:
        xi, yi = float(pts[i][0]), float(pts[i][1])
        xj, yj = float(pts[j][0]), float(pts[j][1])
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) if abs(yj - yi) > 1e-12 else 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
        i += 1
    return inside


def points_inside_ratio(point_list, polygon):
    pts = clean_points(point_list)
    if not pts:
        return 0.0
    hits = 0
    for p in pts:
        if point_in_polygon(p, polygon):
            hits += 1
    return hits / float(len(pts))


def polygon_area_abs(points):
    pts = list(points or [])
    if len(pts) > 1 and points_equal(pts[0], pts[-1]):
        pts = pts[:-1]
    if len(pts) < 3:
        return 0.0
    area = 0.0
    i = 0
    n = len(pts)
    while i < n:
        j = (i + 1) % n
        area += pts[i][0] * pts[j][1] - pts[j][0] * pts[i][1]
        i += 1
    return abs(area) * 0.5


def polyline_length(points):
    pts = clean_points(points)
    if len(pts) < 2:
        return 0.0
    total = 0.0
    i = 1
    while i < len(pts):
        total += dist2d(pts[i - 1], pts[i])
        i += 1
    return total


def polyline_perimeter(points):
    return polyline_length(points)


def unique_point_count(points):
    pts = clean_points(points)
    if len(pts) > 1 and points_equal(pts[0], pts[-1]):
        pts = pts[:-1]
    return len(pts)


def contour_span_mm(points):
    bb = bbox_of_points(clean_points(points))
    if not bb:
        return 0.0, 0.0
    return abs(bb[2] - bb[0]), abs(bb[3] - bb[1])


def safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def is_full_sheet_plate_contour(raw_contour, sheet_info, tol_mm=2.0):
    """Detecta contorno de placa completa (no pieza)."""
    if not sheet_info:
        return False
    points = clean_points(
        raw_contour.get("points", raw_contour) if isinstance(raw_contour, dict) else raw_contour
    )
    if len(points) < 4:
        return False
    sheet_w = safe_float(sheet_info.get("width_mm"))
    sheet_h = safe_float(sheet_info.get("height_mm"))
    if sheet_w <= 0.0 or sheet_h <= 0.0:
        return False
    span_w, span_h = contour_span_mm(points)
    if abs(span_w - sheet_w) > tol_mm or abs(span_h - sheet_h) > tol_mm:
        return False
    if isinstance(raw_contour, dict) and not bool(raw_contour.get("closed", True)):
        return False
    return True


def sheet_bounds_from_info(sheet_info):
    """(min_x, min_y, max_x, max_y) de la placa en coordenadas DXF."""
    if not sheet_info:
        return None
    bbox = sheet_info.get("bbox")
    if bbox and len(bbox) >= 4:
        return (
            safe_float(bbox[0]),
            safe_float(bbox[1]),
            safe_float(bbox[2]),
            safe_float(bbox[3]),
        )
    ox = safe_float(sheet_info.get("origin_x_mm"))
    oy = safe_float(sheet_info.get("origin_y_mm"))
    w = safe_float(sheet_info.get("width_mm"))
    h = safe_float(sheet_info.get("height_mm"))
    if w <= 0.0 or h <= 0.0:
        return None
    return (ox, oy, ox + w, oy + h)


def is_plate_edge_strip_contour(
    raw_contour,
    sheet_info,
    edge_touch_tol_mm=5.0,
    max_strip_thickness_mm=400.0,
    min_edge_span_ratio=0.80,
    max_corner_remnant_width_mm=1200.0,
    max_corner_remnant_height_mm=1600.0,
):
    """
    Franja de borde de placa: toca un borde de la chapa y es alargada y fina
    (ej. franja derecha 204x1524 o franja inferior 5892x198 en H5).

    edge_touch_tol_mm=5.0 queda por debajo del offset tipico de ingenieria (~7.6 mm).
    """
    bounds = sheet_bounds_from_info(sheet_info)
    if not bounds:
        return False, ""

    points = clean_points(
        raw_contour.get("points", raw_contour) if isinstance(raw_contour, dict) else raw_contour
    )
    bb = bbox_of_points(points)
    if not bb:
        return False, ""

    s_min_x, s_min_y, s_max_x, s_max_y = bounds
    min_x, min_y, max_x, max_y = bb
    width = max(0.0, max_x - min_x)
    height = max(0.0, max_y - min_y)
    sheet_w = max(1.0, s_max_x - s_min_x)
    sheet_h = max(1.0, s_max_y - s_min_y)
    tol = float(edge_touch_tol_mm)
    max_thick = float(max_strip_thickness_mm)
    min_ratio = float(min_edge_span_ratio)

    touches_left = min_x <= s_min_x + tol
    touches_right = max_x >= s_max_x - tol
    touches_bottom = min_y <= s_min_y + tol
    touches_top = max_y >= s_max_y - tol

    vertical_strip = (
        (touches_left or touches_right)
        and width <= max_thick
        and height >= sheet_h * min_ratio
    )
    if vertical_strip:
        side = "right" if touches_right else "left"
        return True, "vertical_{0}".format(side)

    horizontal_strip = (
        (touches_bottom or touches_top)
        and height <= max_thick
        and width >= sheet_w * min_ratio
    )
    if horizontal_strip:
        side = "top" if touches_top else "bottom"
        return True, "horizontal_{0}".format(side)

    # Remanente de esquina: rectángulo axis-aligned que toca dos bordes
    # adyacentes de la placa (ej. sobrante 1081x1524 en X_max/Y_min).
    # Piezas reales grandes cerca del origen (p.ej. 1850x1692) NO deben
    # clasificarse como remanente: se limitan ancho/alto máximos.
    unique = []
    for pt in points:
        if not unique or not points_equal(unique[-1], pt):
            if unique and points_equal(unique[0], pt):
                continue
            unique.append(pt)
    is_axis_aligned_rect = (
        len(unique) == 4
        and len({round(p[0], 4) for p in unique}) == 2
        and len({round(p[1], 4) for p in unique}) == 2
    )
    corner_touches = sum(
        [touches_left, touches_right, touches_bottom, touches_top]
    )
    adjacent_corner = (
        (touches_left or touches_right) and (touches_bottom or touches_top)
    )
    within_remnant_size = (
        width <= float(max_corner_remnant_width_mm) + 1e-6
        and height <= float(max_corner_remnant_height_mm) + 1e-6
    )
    if (
        is_axis_aligned_rect
        and adjacent_corner
        and corner_touches >= 2
        and within_remnant_size
    ):
        return True, "corner_remnant_rectangle"

    return False, ""


def filter_cut_outer_pieces(
    cut_outer_raw,
    sheet_info,
    tol_mm=2.0,
    logs=None,
    filter_edge_strips=True,
    edge_touch_tol_mm=5.0,
    max_strip_thickness_mm=400.0,
    min_edge_span_ratio=0.80,
    max_corner_remnant_width_mm=1200.0,
    max_corner_remnant_height_mm=1600.0,
):
    kept = []
    removed_plate = 0
    removed_edge = 0
    for idx, raw in enumerate(cut_outer_raw or []):
        if is_full_sheet_plate_contour(raw, sheet_info, tol_mm=tol_mm):
            removed_plate += 1
            if logs is not None:
                logs.append("PLATE_SKIP full_sheet cut_outer index={0}".format(idx))
            continue
        if filter_edge_strips:
            is_strip, reason = is_plate_edge_strip_contour(
                raw,
                sheet_info,
                edge_touch_tol_mm=edge_touch_tol_mm,
                max_strip_thickness_mm=max_strip_thickness_mm,
                min_edge_span_ratio=min_edge_span_ratio,
                max_corner_remnant_width_mm=max_corner_remnant_width_mm,
                max_corner_remnant_height_mm=max_corner_remnant_height_mm,
            )
            if is_strip:
                removed_edge += 1
                if logs is not None:
                    logs.append(
                        "PLATE_EDGE_SKIP index={0} reason={1}".format(idx, reason)
                    )
                continue
        kept.append(raw)
    if logs is not None:
        logs.append(
            "cut_outer kept={0} removed_plate={1} removed_edge_strips={2}".format(
                len(kept), removed_plate, removed_edge
            )
        )
    return kept

def _solve_3x3(matrix, vector, eps=1e-12):
    """Resuelve un sistema 3x3 con eliminación Gauss-Jordan y pivoteo."""
    a = [
        [float(matrix[row][col]) for col in range(3)] + [float(vector[row])]
        for row in range(3)
    ]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda row: abs(a[row][col]))
        if abs(a[pivot][col]) <= eps:
            return None
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
        div = a[col][col]
        a[col] = [value / div for value in a[col]]
        for row in range(3):
            if row == col:
                continue
            factor = a[row][col]
            if abs(factor) <= eps:
                continue
            a[row] = [a[row][idx] - factor * a[col][idx] for idx in range(4)]
    return (a[0][3], a[1][3], a[2][3])

def fit_circle_least_squares(points):
    """Ajusta un círculo por mínimos cuadrados y devuelve métricas de auditoría.

    El método usa la forma algebraica x²+y²+D*x+E*y+F=0. La geometría
    original no se modifica. El consumidor decide con sus tolerancias si el
    ajuste es suficientemente circular para emitir instrucciones FANUC C.
    """
    pts = clean_points(points)
    if len(pts) > 1 and points_equal(pts[0], pts[-1]):
        pts = pts[:-1]
    if len(pts) < 3:
        return {
            "valid": False,
            "reason": "fewer_than_3_unique_points",
            "point_count": len(pts),
        }

    n = float(len(pts))
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    sxx = sum(p[0] * p[0] for p in pts)
    syy = sum(p[1] * p[1] for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    z = [p[0] * p[0] + p[1] * p[1] for p in pts]
    sz = sum(z)
    sxz = sum(p[0] * zz for p, zz in zip(pts, z))
    syz = sum(p[1] * zz for p, zz in zip(pts, z))

    solution = _solve_3x3(
        [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, n]],
        [-sxz, -syz, -sz],
    )
    if solution is None:
        return {
            "valid": False,
            "reason": "singular_circle_fit",
            "point_count": len(pts),
        }

    d_coef, e_coef, f_coef = solution
    cx = -0.5 * d_coef
    cy = -0.5 * e_coef
    radius_sq = cx * cx + cy * cy - f_coef
    if radius_sq <= 0.0 or not math.isfinite(radius_sq):
        return {
            "valid": False,
            "reason": "invalid_radius_squared",
            "point_count": len(pts),
        }

    radius = math.sqrt(radius_sq)
    distances = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]
    errors = [distance - radius for distance in distances]
    rms_error = math.sqrt(sum(error * error for error in errors) / n)
    max_error = max(abs(error) for error in errors)
    bb = bbox_of_points(pts)
    width = (bb[2] - bb[0]) if bb else 0.0
    height = (bb[3] - bb[1]) if bb else 0.0
    aspect = width / height if height > 1e-12 else 0.0
    aspect_error = abs(width - height) / max(width, height, 1e-12)

    return {
        "valid": True,
        "reason": "ok",
        "point_count": len(pts),
        "center": [cx, cy],
        "radius": radius,
        "rms_error_mm": rms_error,
        "max_error_mm": max_error,
        "max_error_ratio": max_error / radius if radius > 1e-12 else None,
        "bbox_width_mm": width,
        "bbox_height_mm": height,
        "bbox_aspect": aspect,
        "bbox_aspect_error": aspect_error,
    }
