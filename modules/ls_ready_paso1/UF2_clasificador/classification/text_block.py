# -*- coding: utf-8 -*-
"""Bloque de texto exclusivo cama B: inicio en menor X y menor Y en DXF."""
from .geometry import bbox_of_points, bbox_height, bbox_width


def _stroke_centers(text_strokes):
    centers = []
    for stroke in text_strokes or []:
        bb = stroke.get("bbox_dxf")
        if bb:
            centers.append((float((bb[0] + bb[2]) * 0.5), float((bb[1] + bb[3]) * 0.5)))
            continue
        pts = stroke.get("points") or []
        if pts:
            bb = bbox_of_points(pts)
            centers.append((float((bb[0] + bb[2]) * 0.5), float((bb[1] + bb[3]) * 0.5)))
    return centers


def _robust_span(values, lo=0.10, hi=0.90):
    vals = sorted(float(v) for v in (values or []))
    if not vals:
        return 0.0
    if len(vals) < 4:
        return max(vals) - min(vals)
    i_lo = int(lo * (len(vals) - 1))
    i_hi = int(hi * (len(vals) - 1))
    return max(vals[i_hi] - vals[i_lo], 0.0)


def text_cluster_bbox(text_strokes):
    points = []
    for stroke in text_strokes or []:
        for pt in stroke.get("points") or []:
            points.append(pt)
    return bbox_of_points(points)


def focus_text_cluster(text_strokes, cell_mm=80.0, min_fraction=0.45):
    """
    Conserva el subcluster 2D conectado principal de trazos de texto.

    Evita que trazos de figura en otras zonas de la pieza inclinen la
    orientacion (p. ej. texto horizontal abajo mezclado con trazos arriba).
    """
    strokes = list(text_strokes or [])
    if len(strokes) <= 4:
        return strokes

    centers = _stroke_centers(strokes)
    if len(centers) < 4:
        return strokes

    cells = {}
    for idx, (x, y) in enumerate(centers):
        key = (int(float(x) // float(cell_mm)), int(float(y) // float(cell_mm)))
        cells.setdefault(key, []).append(idx)

    if not cells:
        return strokes

    seed = max(cells, key=lambda k: len(cells[k]))
    selected = set()
    queue = [seed]
    while queue:
        cell = queue.pop(0)
        if cell in selected or cell not in cells:
            continue
        selected.add(cell)
        cx, cy = cell
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                neighbor = (cx + dx, cy + dy)
                if neighbor in cells and neighbor not in selected:
                    queue.append(neighbor)

    indices = sorted({idx for cell in selected for idx in cells[cell]})
    if len(indices) / float(len(centers)) >= min_fraction:
        return [strokes[idx] for idx in indices]
    return strokes


def robust_text_cluster_spans(text_strokes, lo=0.10, hi=0.90):
    """
    Span X/Y del cluster ignorando trazos sueltos (p. ej. lineas de figura
    mezcladas en el bloque de texto). Usa centros de trazo, no bbox global.
    """
    centers = _stroke_centers(text_strokes)
    if not centers:
        bb = text_cluster_bbox(text_strokes)
        if not bb:
            return 0.0, 0.0
        return max(bbox_width(bb), 0.0), max(bbox_height(bb), 0.0)

    xs = [c[0] for c in centers]
    ys = [c[1] for c in centers]
    return _robust_span(xs, lo, hi), _robust_span(ys, lo, hi)


def detect_text_orientation(
    text_strokes,
    ratio_threshold=1.2,
    percentile_lo=0.10,
    percentile_hi=0.90,
    cluster_cell_mm=80.0,
    cluster_min_fraction=0.45,
):
    """
    vertical | horizontal (nunca diagonal en nesting).
    Compara span robusto del subcluster principal (centros p10-p90).
    """
    focused = focus_text_cluster(
        text_strokes,
        cell_mm=cluster_cell_mm,
        min_fraction=cluster_min_fraction,
    )
    width, height = robust_text_cluster_spans(
        focused, lo=percentile_lo, hi=percentile_hi
    )
    if width <= 0.0 and height <= 0.0:
        return "horizontal", "min_x_min_y"

    width = max(width, 1e-6)
    height = max(height, 1e-6)
    if height / width >= float(ratio_threshold):
        return "vertical", "min_x_min_y"
    if width / height >= float(ratio_threshold):
        return "horizontal", "min_x_min_y"
    if height >= width:
        return "vertical", "min_x_min_y"
    return "horizontal", "min_x_min_y"


def _stroke_sort_key(stroke, orientation):
    pts = stroke.get("points") or []
    if not pts:
        c = stroke.get("center_dxf") or [0.0, 0.0]
        return float(c[1] if orientation == "vertical" else c[0])
    if orientation == "vertical":
        return max(float(p[1]) for p in pts)
    return min(float(p[0]) for p in pts)


def order_text_strokes(text_strokes, orientation):
    """
    Orden de trazos en coordenadas DXF para cama B, antes de convertir a UF-2:
      vertical   -> Y menor primero (ascending_y), para iniciar abajo y subir.
      horizontal -> X menor primero (ascending_x).
    """
    reverse = False
    return sorted(
        text_strokes or [],
        key=lambda s: _stroke_sort_key(s, orientation),
        reverse=reverse,
    )


def compute_text_start_point(ordered_text_strokes, orientation):
    """Punto de inicio del path de texto para cama B: menor X y menor Y en DXF."""
    best = None
    best_key = None
    for stroke in ordered_text_strokes or []:
        pts = stroke.get("points") or stroke.get("points_dxf") or []
        for pt in pts:
            key = (float(pt[0]), float(pt[1]))
            if best_key is None or key < best_key:
                best_key = key
                best = pt

    if best is None:
        if not ordered_text_strokes:
            return None
        c = ordered_text_strokes[0].get("center_dxf") or [0.0, 0.0]
        return [round(float(c[0]), 4), round(float(c[1]), 4)]
    return [round(float(best[0]), 4), round(float(best[1]), 4)]


def text_cluster_center_x(text_strokes):
    centers = _stroke_centers(text_strokes)
    if centers:
        xs = sorted(c[0] for c in centers)
        return xs[len(xs) // 2]
    bb = text_cluster_bbox(text_strokes)
    if not bb:
        return 0.0
    return (bb[0] + bb[2]) * 0.5
