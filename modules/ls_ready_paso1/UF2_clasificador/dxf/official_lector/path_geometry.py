# -*- coding: utf-8 -*-
"""
Geometria compartida para generadores de paths PQArt:
- Simplificacion de contornos a esquinas
- Entrada de corte perpendicular al primer tramo (lead-in) de 5 mm
"""
import math

POINT_EQUAL_TOL = 1e-6
COLLINEAR_TOL = 1e-3
DEFAULT_CUT_ENTRY_MM = 5.0
BOTTOM_EDGE_Y_TOL = 1.0


def safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def dist2d(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def points_equal(p1, p2, tol=POINT_EQUAL_TOL):
    return abs(p1[0] - p2[0]) <= tol and abs(p1[1] - p2[1]) <= tol


def triangle_area2(a, b, c):
    return abs(
        (b[0] - a[0]) * (c[1] - a[1]) -
        (b[1] - a[1]) * (c[0] - a[0])
    )


def point_between(a, b, c, tol=COLLINEAR_TOL):
    min_x = min(a[0], c[0]) - tol
    max_x = max(a[0], c[0]) + tol
    min_y = min(a[1], c[1]) - tol
    max_y = max(a[1], c[1]) + tol
    return (min_x <= b[0] <= max_x) and (min_y <= b[1] <= max_y)


def simplify_open_polyline_keep_corners(points, tol=COLLINEAR_TOL):
    if not points or len(points) <= 2:
        return list(points)

    out = [points[0]]
    i = 1
    while i < len(points) - 1:
        a = out[-1]
        b = points[i]
        c = points[i + 1]
        area2 = triangle_area2(a, b, c)
        if area2 <= tol and point_between(a, b, c, tol):
            i += 1
            continue
        out.append(b)
        i += 1
    out.append(points[-1])
    return out


def simplify_closed_polyline_keep_corners(points, tol=COLLINEAR_TOL):
    pts = list(points)
    if not pts:
        return []

    had_close = False
    if len(pts) >= 2 and points_equal(pts[0], pts[-1]):
        pts = pts[:-1]
        had_close = True

    if len(pts) <= 3:
        if had_close and pts and not points_equal(pts[0], pts[-1]):
            pts.append(pts[0])
        return pts

    changed = True
    while changed and len(pts) > 3:
        changed = False
        new_pts = []
        n = len(pts)
        i = 0
        while i < n:
            a = pts[(i - 1) % n]
            b = pts[i]
            c = pts[(i + 1) % n]
            area2 = triangle_area2(a, b, c)
            if area2 <= tol and point_between(a, b, c, tol):
                changed = True
            else:
                new_pts.append(b)
            i += 1
        pts = new_pts

    if pts and not points_equal(pts[0], pts[-1]):
        pts.append(pts[0])
    return pts


def simplify_contour_keep_corners(points, is_closed, tol=COLLINEAR_TOL):
    if is_closed:
        return simplify_closed_polyline_keep_corners(points, tol)
    return simplify_open_polyline_keep_corners(points, tol)


def rotate_closed_points(points, start_idx):
    pts = list(points)
    if pts and points_equal(pts[0], pts[-1]):
        pts = pts[:-1]
    if not pts:
        return []
    start_idx = int(start_idx) % len(pts)
    out = pts[start_idx:] + pts[:start_idx]
    out.append(out[0])
    return out


def bbox_center(points):
    if not points:
        return (0.0, 0.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return ((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5)


def contour_center(points):
    pts = list(points)
    if pts and points_equal(pts[0], pts[-1]):
        pts = pts[:-1]
    return bbox_center(pts) if pts else (0.0, 0.0)


def offset_from_center(pt, center, distance, inward=True):
    px, py = pt
    cx, cy = center
    if inward:
        dx = cx - px
        dy = cy - py
    else:
        dx = px - cx
        dy = py - cy

    length = math.hypot(dx, dy)
    if length < 1e-9:
        return pt

    scale = float(distance) / length
    return (px + dx * scale, py + dy * scale)


def start_point_sort_key(pt, bottom_y_is_min=True, e1_fn=None):
    x, y = pt
    y_key = y if bottom_y_is_min else -y
    e1 = 0.0
    if e1_fn is not None:
        try:
            e1 = float(e1_fn(x, y))
        except Exception:
            e1 = 0.0
    return (round(y_key, 6), round(e1, 6), round(x, 6))


def _is_ring_corner(pts, i, tol=COLLINEAR_TOL):
    n = len(pts)
    if n < 3:
        return True
    prev_pt = pts[(i - 1) % n]
    curr_pt = pts[i]
    next_pt = pts[(i + 1) % n]
    area2 = triangle_area2(prev_pt, curr_pt, next_pt)
    if area2 <= tol and point_between(prev_pt, curr_pt, next_pt, tol):
        return False
    return True


def choose_bottom_start_idx_for_closed(
    points,
    bottom_y_is_min=True,
    e1_fn=None,
    corners_only=False,
    tol=COLLINEAR_TOL,
):
    pts = _open_ring(points)
    if not pts:
        return 0

    bottom_y = _bottom_y_value(pts, bottom_y_is_min)

    best_i = None
    best_key = None
    i = 0
    while i < len(pts):
        if corners_only and not _is_ring_corner(pts, i, tol):
            i += 1
            continue
        if corners_only and not _y_on_bottom_edge(pts[i][1], bottom_y, bottom_y_is_min, tol):
            i += 1
            continue

        key = start_point_sort_key(pts[i], bottom_y_is_min, e1_fn)
        if best_key is None or key < best_key:
            best_key = key
            best_i = i
        i += 1

    if best_i is not None:
        return best_i

    best_i = 0
    best_key = start_point_sort_key(pts[0], bottom_y_is_min, e1_fn)
    i = 1
    while i < len(pts):
        key = start_point_sort_key(pts[i], bottom_y_is_min, e1_fn)
        if key < best_key:
            best_key = key
            best_i = i
        i += 1
    return best_i


def orient_open_contour_from_bottom(points, bottom_y_is_min=True, e1_fn=None):
    pts = list(points)
    if len(pts) < 2:
        return pts

    first_key = start_point_sort_key(pts[0], bottom_y_is_min, e1_fn)
    last_key = start_point_sort_key(pts[-1], bottom_y_is_min, e1_fn)
    if last_key < first_key:
        pts.reverse()
    return pts


def _second_contour_point(contour_start, contour_points):
    pts = list(contour_points or [])
    if len(pts) < 2:
        return None

    second = pts[1]
    if points_equal(contour_start, second) and len(pts) > 2:
        second = pts[2]
    if points_equal(contour_start, second):
        return None
    return second


def _open_ring(points):
    pts = list(points)
    if pts and points_equal(pts[0], pts[-1]):
        pts = pts[:-1]
    return pts


def _is_horizontal_segment(a, b, tol=COLLINEAR_TOL):
    return abs(b[1] - a[1]) <= tol


def _is_vertical_segment(a, b, tol=COLLINEAR_TOL):
    return abs(b[0] - a[0]) <= tol


def is_rectilinear_ring(points, tol=COLLINEAR_TOL):
    pts = _open_ring(points)
    if len(pts) < 4:
        return False

    n = len(pts)
    i = 0
    while i < n:
        a = pts[i]
        b = pts[(i + 1) % n]
        if not (
            _is_horizontal_segment(a, b, tol)
            or _is_vertical_segment(a, b, tol)
        ):
            return False
        i += 1
    return True


def _bottom_y_value(pts, bottom_y_is_min=True):
    ys = [p[1] for p in pts]
    return min(ys) if bottom_y_is_min else max(ys)


def _y_on_bottom_edge(y, bottom_y, bottom_y_is_min=True, tol=COLLINEAR_TOL):
    if bottom_y_is_min:
        return y <= bottom_y + tol
    return y >= bottom_y - tol


def _axis_aligned_entry_offset(start_x, start_y, dir_x, dir_y, dist, internal):
    """
    Desplaza entry_mm solo en X o solo en Y (nunca en diagonal).
    dir_x/dir_y apuntan hacia el interior del contorno.
    """
    if abs(dir_x) < 1e-9 and abs(dir_y) < 1e-9:
        return (start_x, start_y)

    sign_x = 1.0 if dir_x >= 0.0 else -1.0
    sign_y = 1.0 if dir_y >= 0.0 else -1.0
    if not internal:
        sign_x = -sign_x
        sign_y = -sign_y

    if abs(dir_x) >= abs(dir_y):
        return (start_x + sign_x * dist, start_y)
    return (start_x, start_y + sign_y * dist)


def _choose_rectilinear_corner_start_idx(
    pts,
    bottom_y_is_min=True,
    e1_fn=None,
    tol=COLLINEAR_TOL,
    require_horizontal_first=True,
):
    """
    Esquina real del borde mas bajo cuyo primer tramo de corte es horizontal.
    Ignora puntos intermedios colineales en el borde inferior.
    """
    bottom_y = _bottom_y_value(pts, bottom_y_is_min)
    best_idx = None
    best_key = None
    n = len(pts)

    i = 0
    while i < n:
        corner = pts[i]
        nxt = pts[(i + 1) % n]
        if not _is_ring_corner(pts, i, tol):
            i += 1
            continue
        if not _y_on_bottom_edge(corner[1], bottom_y, bottom_y_is_min, tol):
            i += 1
            continue
        if require_horizontal_first and not _is_horizontal_segment(corner, nxt, tol):
            i += 1
            continue

        key = start_point_sort_key(corner, bottom_y_is_min, e1_fn)
        if best_key is None or key < best_key:
            best_key = key
            best_idx = i
        i += 1

    return best_idx


def prepare_closed_contour_start(
    points,
    bottom_y_is_min=True,
    e1_fn=None,
    tol=COLLINEAR_TOL,
):
    """
    Reordena un contorno cerrado para un inicio de corte mas limpio.

    En contornos rectilineos (p. ej. rectangulos DXF) inicia en una esquina
    del borde horizontal mas bajo, con el primer tramo horizontal. En otros
    casos conserva el inicio en el punto mas bajo.
    """
    pts = _open_ring(points)
    if len(pts) < 3:
        return list(points)

    if is_rectilinear_ring(pts, tol):
        start_idx = _choose_rectilinear_corner_start_idx(
            pts,
            bottom_y_is_min=bottom_y_is_min,
            e1_fn=e1_fn,
            tol=tol,
            require_horizontal_first=True,
        )
        if start_idx is None:
            start_idx = _choose_rectilinear_corner_start_idx(
                pts,
                bottom_y_is_min=bottom_y_is_min,
                e1_fn=e1_fn,
                tol=tol,
                require_horizontal_first=False,
            )
        if start_idx is not None:
            return rotate_closed_points(points, start_idx)

    start_idx = choose_bottom_start_idx_for_closed(
        points,
        bottom_y_is_min=bottom_y_is_min,
        e1_fn=e1_fn,
        corners_only=is_rectilinear_ring(pts, tol),
        tol=tol,
    )
    return rotate_closed_points(points, start_idx)


def compute_cut_entry_xy(contour_start, contour_points, internal, entry_mm=DEFAULT_CUT_ENTRY_MM):
    """
    Punto de aproximacion para SAFE_IN / CUT_IN:
    - internal=True  -> entry_mm hacia el interior, perpendicular al primer tramo
    - internal=False -> entry_mm hacia el exterior, perpendicular al primer tramo

    El desplazamiento siempre queda estrictamente horizontal o vertical (nunca
    en diagonal). En bordes horizontales la entrada es vertical; en verticales,
    horizontal. Si no hay tramo usable, cae al offset radial hacia el centro.
    """
    start_x, start_y = contour_start
    second = _second_contour_point(contour_start, contour_points)
    dist = float(entry_mm)

    if second is None:
        center = contour_center(contour_points)
        cx, cy = center
        return _axis_aligned_entry_offset(
            start_x,
            start_y,
            cx - start_x,
            cy - start_y,
            dist,
            bool(internal),
        )

    dx = second[0] - start_x
    dy = second[1] - start_y
    length = math.hypot(dx, dy)
    if length < 1e-9:
        center = contour_center(contour_points)
        cx, cy = center
        return _axis_aligned_entry_offset(
            start_x,
            start_y,
            cx - start_x,
            cy - start_y,
            dist,
            bool(internal),
        )

    left_nx = -dy / length
    left_ny = dx / length

    cx, cy = contour_center(contour_points)
    to_center_x = cx - start_x
    to_center_y = cy - start_y
    if (left_nx * to_center_x + left_ny * to_center_y) >= 0.0:
        inward_nx, inward_ny = left_nx, left_ny
    else:
        inward_nx, inward_ny = -left_nx, -left_ny

    return _axis_aligned_entry_offset(
        start_x,
        start_y,
        inward_nx,
        inward_ny,
        dist,
        bool(internal),
    )


SHEET_PLATE_MATCH_TOL_MM = 8.0


def _open_ring_points(points):
    pts = list(points or [])
    if len(pts) >= 2 and points_equal(pts[0], pts[-1]):
        pts = pts[:-1]
    return pts


def contour_bbox_xy(points):
    pts = _open_ring_points(points)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def contour_span_mm(points):
    bb = contour_bbox_xy(points)
    if not bb:
        return 0.0, 0.0
    min_x, min_y, max_x, max_y = bb
    return abs(max_x - min_x), abs(max_y - min_y)


def _dims_match_sheet(span_w, span_h, sheet_w, sheet_h, tol_mm):
    pairs = (
        (span_w, span_h, sheet_w, sheet_h),
        (span_w, span_h, sheet_h, sheet_w),
    )
    for cw, ch, sw, sh in pairs:
        if abs(cw - sw) <= tol_mm and abs(ch - sh) <= tol_mm:
            return True
    return False


def normalize_contour_points(raw_contour):
    if isinstance(raw_contour, dict):
        pts = raw_contour.get("points", [])
    else:
        pts = raw_contour
    out = []
    for item in pts or []:
        if isinstance(item, dict):
            out.append((safe_float(item.get("x", 0.0)), safe_float(item.get("y", 0.0))))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append((safe_float(item[0]), safe_float(item[1])))
    return out


def is_full_sheet_plate_contour(raw_contour, sheet_info, tol_mm=SHEET_PLATE_MATCH_TOL_MM):
    """
    True si el contorno coincide con el perimetro completo de la placa (lamina).
    La placa de escena / layer PLATE no debe generarse como corte.
    """
    if not sheet_info:
        return False

    points = normalize_contour_points(raw_contour)
    if len(points) < 4:
        return False

    sheet_w = safe_float(sheet_info.get("width_mm"))
    sheet_h = safe_float(sheet_info.get("height_mm"))
    if sheet_w <= 0.0 or sheet_h <= 0.0:
        return False

    span_w, span_h = contour_span_mm(points)
    if not _dims_match_sheet(span_w, span_h, sheet_w, sheet_h, tol_mm):
        return False

    closed = True
    if isinstance(raw_contour, dict):
        closed = bool(raw_contour.get("closed", True))
    if not closed:
        return False

    if not is_rectilinear_ring(points, COLLINEAR_TOL):
        return False

    return True


def _sheet_bounds_from_info(sheet_info):
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
    max_strip_thickness_mm=650.0,
    min_edge_span_ratio=0.80,
):
    """
    True para sobrantes tipo franja pegados al borde de la placa.

    Ejemplo: pieza/sobrante D con todo el alto de 240x48, pegada a X=6096.
    No es la placa completa, pero tampoco debe pasar como pieza de corte.
    """
    bounds = _sheet_bounds_from_info(sheet_info)
    if not bounds:
        return False, ""

    points = normalize_contour_points(raw_contour)
    bb = contour_bbox_xy(points)
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
        return True, "vertical_right" if touches_right else "vertical_left"

    horizontal_strip = (
        (touches_bottom or touches_top)
        and height <= max_thick
        and width >= sheet_w * min_ratio
    )
    if horizontal_strip:
        return True, "horizontal_top" if touches_top else "horizontal_bottom"

    return False, ""


def filter_cut_outer_exclude_sheet_plate(cut_outer_raw, sheet_info, logs=None, tol_mm=SHEET_PLATE_MATCH_TOL_MM):
    """
    Quita contornos que representan la placa completa y sobrantes de borde.

    La placa completa y las franjas residuales pegadas al borde de la lamina no
    deben llegar al JSON como piezas.
    """
    if not cut_outer_raw:
        return []

    kept = []
    removed_plate = 0
    removed_edge = 0
    idx = 0
    while idx < len(cut_outer_raw):
        raw = cut_outer_raw[idx]
        if is_full_sheet_plate_contour(raw, sheet_info, tol_mm=tol_mm):
            removed_plate += 1
            if logs is not None:
                pts = normalize_contour_points(raw)
                span_w, span_h = contour_span_mm(pts)
                logs.append(
                    "PLATE_CONTOUR_SKIP idx={0} span={1:.3f}x{2:.3f}mm".format(
                        idx + 1, span_w, span_h
                    )
                )
        else:
            is_edge_strip, reason = is_plate_edge_strip_contour(raw, sheet_info)
            if is_edge_strip:
                removed_edge += 1
                if logs is not None:
                    pts = normalize_contour_points(raw)
                    span_w, span_h = contour_span_mm(pts)
                    logs.append(
                        "PLATE_EDGE_STRIP_SKIP idx={0} reason={1} span={2:.3f}x{3:.3f}mm".format(
                            idx + 1, reason, span_w, span_h
                        )
                    )
            else:
                kept.append(raw)
        idx += 1

    if logs is not None:
        logs.append(
            "PLATE_CONTOUR_FILTER kept={0} removed_plate={1} removed_edge_strips={2} tol_mm={3}".format(
                len(kept), removed_plate, removed_edge, tol_mm
            )
        )
    return kept


def _pqart_call_variants(fn, path_name=None):
    variants = [tuple()]
    if path_name:
        variants.extend([(path_name,), (str(path_name),)])
        try:
            variants.append((path_name.encode("ascii", "ignore"),))
        except Exception:
            pass
    return variants


def try_clear_pqart_path_points(eng, path_name, logs, call_str_fn=None):
    """
    Intenta vaciar la geometria de un path antes de reescribirlo.
    Necesario en copy-base: la plantilla PATH_BASE hereda el borde de placa.
    """
    tried = []

    bulk_methods = (
        "DeleteAllPathPoint",
        "DelAllPathPoint",
        "RemoveAllPathPoint",
        "ClearPathPoint",
        "ClearPath",
        "DeletePathPoint",
        "DeleteAllPoint",
        "ClearAllPathPoint",
        "DelPathAllPoint",
        "DeletePathAllPoint",
        "RemovePathAllPoint",
        "ClearPathAllPoint",
        "DeleteAllPathPoints",
        "DelAllPathPoints",
        "RemoveAllPathPoints",
        "ClearAllPoints",
        "DeleteAllPoints",
    )

    for method_name in bulk_methods:
        fn = getattr(eng, method_name, None)
        if not callable(fn):
            continue
        for args in _pqart_call_variants(fn, path_name):
            try:
                fn(*args)
                logs.append(
                    "PATH_CLEAR_OK path={0} method={1} argc={2}".format(
                        path_name, method_name, len(args)
                    )
                )
                return True
            except Exception as exc:
                tried.append("{0}{1}:{2}".format(method_name, args, exc))

        if call_str_fn is not None and path_name:
            try:
                call_str_fn(method_name, path_name)
                logs.append(
                    "PATH_CLEAR_OK path={0} method={1} via=call_str".format(
                        path_name, method_name
                    )
                )
                return True
            except Exception as exc:
                tried.append("{0}(call_str):{1}".format(method_name, exc))

    count_methods = (
        "GetPathPointCount",
        "GetPathPointNum",
        "GetPointCount",
        "GetPathPtCount",
        "PathPointCount",
        "GetPathPointSize",
        "GetPathPointNumber",
    )
    delete_methods = (
        "DeletePathPoint",
        "DelPathPoint",
        "RemovePathPoint",
        "DeletePathPointByIndex",
        "DelPathPointByIndex",
        "DeletePoint",
        "DelPoint",
        "RemovePoint",
        "DeletePathPointByNum",
        "DelPathPt",
        "DeletePathPt",
    )

    for count_name in count_methods:
        count_fn = getattr(eng, count_name, None)
        if not callable(count_fn):
            continue

        for delete_name in delete_methods:
            delete_fn = getattr(eng, delete_name, None)
            if not callable(delete_fn):
                continue

            removed = 0
            errors = 0
            while True:
                try:
                    count = int(count_fn())
                except Exception as exc:
                    errors += 1
                    tried.append("{0}():{1}".format(count_name, exc))
                    break

                if count <= 0:
                    break

                deleted = False
                for idx in (0, count - 1):
                    try:
                        delete_fn(int(idx))
                        deleted = True
                        removed += 1
                        break
                    except Exception:
                        pass

                if not deleted:
                    errors += 1
                    tried.append("{0}({1}) failed".format(delete_name, count))
                    break

            if removed > 0 and errors == 0:
                logs.append(
                    "PATH_CLEAR_OK path={0} method={1}+{2} removed={3}".format(
                        path_name, count_name, delete_name, removed
                    )
                )
                return True

    api_names = []
    try:
        api_names = sorted(
            n for n in dir(eng)
            if any(k in n.lower() for k in ("path", "point"))
            and any(k in n.lower() for k in ("del", "clear", "remove"))
        )
    except Exception:
        api_names = []

    if api_names:
        logs.append("PATH_CLEAR_API_HINT path={0} names={1}".format(path_name, ",".join(api_names)))

    logs.append(
        "PATH_CLEAR_SKIP path={0} tried={1}".format(
            path_name, tried if tried else "(none)"
        )
    )
    return False


def sheet_plate_contour_dxf(sheet_info, width_mm=None, height_mm=None):
    """Rectangulo de placa completa en coords DXF (origen esquina inferior-izquierda)."""
    if width_mm is None:
        width_mm = float((sheet_info or {}).get("width_mm") or 0.0)
    if height_mm is None:
        height_mm = float((sheet_info or {}).get("height_mm") or 0.0)
    w = float(width_mm)
    h = float(height_mm)
    if w <= 0.0 or h <= 0.0:
        raise ValueError(
            "sheet_plate_contour_dxf: width_mm/height_mm invalidos ({0}x{1})".format(w, h)
        )
    return {
        "points": [[0.0, 0.0], [w, 0.0], [w, h], [0.0, h]],
        "closed": True,
    }
