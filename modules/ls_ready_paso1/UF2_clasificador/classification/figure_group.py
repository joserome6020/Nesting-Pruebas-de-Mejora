# -*- coding: utf-8 -*-
"""
Agrupa trazos de figura que pertenecen a la misma linea fisica de marcaje.

Vertical   -> cluster por center X (pares duplicados en DXF)
Horizontal -> cluster por center Y
"""
from .geometry import bbox_height, bbox_width


def _stroke_center(stroke):
    return stroke.get("center_dxf") or [0.0, 0.0]


def stroke_line_orientation(stroke, ratio_threshold=1.5):
    bb = stroke.get("bbox_dxf") or [0.0, 0.0, 0.0, 0.0]
    width = max(bbox_width(bb), 1e-6)
    height = max(bbox_height(bb), 1e-6)
    if height / width >= float(ratio_threshold):
        return "vertical"
    if width / height >= float(ratio_threshold):
        return "horizontal"
    return "other"


def _axis_value(stroke, orientation):
    cx, cy = _stroke_center(stroke)
    if orientation == "horizontal":
        return float(cy)
    return float(cx)


def _group_tolerance(orientation, cfg):
    if orientation == "horizontal":
        return float(getattr(cfg, "FIGURE_LINE_Y_TOL_MM", 40.0))
    return float(getattr(cfg, "FIGURE_LINE_X_TOL_MM", 40.0))


def _group_sort_value(group):
    """Der->izq: vertical por X desc; horizontal por Y desc (ajustable)."""
    orient = group.get("orientation") or "vertical"
    strokes = group.get("strokes") or []
    if not strokes:
        return 0.0
    if orient == "horizontal":
        return max(float(_stroke_center(s)[1]) for s in strokes)
    return max(float(_stroke_center(s)[0]) for s in strokes)


def _order_strokes_within_group(strokes, orientation):
    if orientation == "horizontal":
        return sorted(strokes, key=lambda s: float(_stroke_center(s)[0]))
    return sorted(strokes, key=lambda s: float(_stroke_center(s)[1]))


def group_figure_strokes(figure_strokes, cfg, logs=None, piece_label=""):
    """
    Devuelve lista de grupos:
      { orientation, strokes, sort_value, axis_value }
    """
    min_length = float(getattr(cfg, "FIGURE_LINE_MIN_LENGTH_MM", 0.0) or 0.0)
    orient_ratio = float(getattr(cfg, "FIGURE_LINE_ORIENTATION_RATIO", 1.5))

    groups = []
    skipped_short = 0

    for stroke in figure_strokes or []:
        length = float(stroke.get("length_mm") or 0.0)
        if min_length > 0.0 and length < min_length:
            skipped_short += 1
            continue

        orientation = stroke_line_orientation(stroke, ratio_threshold=orient_ratio)
        axis_value = _axis_value(stroke, orientation)
        tol = _group_tolerance(orientation, cfg)

        placed = False
        for group in groups:
            if group["orientation"] != orientation:
                continue
            if abs(float(group["axis_value"]) - axis_value) <= tol:
                group["strokes"].append(stroke)
                n = len(group["strokes"])
                prev = float(group["axis_value"])
                group["axis_value"] = (prev * (n - 1) + axis_value) / float(n)
                group["sort_value"] = _group_sort_value(group)
                placed = True
                break

        if not placed:
            groups.append(
                {
                    "orientation": orientation,
                    "strokes": [stroke],
                    "axis_value": axis_value,
                    "sort_value": _group_sort_value(
                        {"orientation": orientation, "strokes": [stroke]}
                    ),
                }
            )

    for group in groups:
        group["strokes"] = _order_strokes_within_group(
            group["strokes"], group["orientation"]
        )
        group["sort_value"] = _group_sort_value(group)

    groups.sort(
        key=lambda g: (
            -float(g.get("sort_value") or 0.0),
            str(g.get("orientation") or ""),
        )
    )

    if logs is not None:
        logs.append(
            "{0}figure_groups in={1} groups={2} skipped_short={3}".format(
                (piece_label + " ") if piece_label else "",
                len(figure_strokes or []),
                len(groups),
                skipped_short,
            )
        )

    return groups


def _group_bbox(strokes, pad_mm=0.0):
    xs = []
    ys = []
    for stroke in strokes or []:
        bb = stroke.get("bbox_dxf")
        if bb:
            xs.extend([bb[0], bb[2]])
            ys.extend([bb[1], bb[3]])
        for pt in stroke.get("points") or []:
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
    if not xs:
        return None
    pad = float(pad_mm or 0.0)
    return (
        min(xs) - pad,
        min(ys) - pad,
        max(xs) + pad,
        max(ys) + pad,
    )


def _bbox_overlap(a, b):
    ox = min(a[2], b[2]) - max(a[0], b[0])
    oy = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, ox), max(0.0, oy)


def _bbox_gap(a, b):
    if a[2] < b[0]:
        gx = b[0] - a[2]
    elif b[2] < a[0]:
        gx = a[0] - b[2]
    else:
        gx = 0.0
    if a[3] < b[1]:
        gy = b[1] - a[3]
    elif b[3] < a[1]:
        gy = a[1] - b[3]
    else:
        gy = 0.0
    return gx, gy


def _composite_groups_linked(bb1, bb2, cfg):
    ox, oy = _bbox_overlap(bb1, bb2)
    gx, gy = _bbox_gap(bb1, bb2)
    overlap_min = float(getattr(cfg, "FIGURE_COMPOSITE_OVERLAP_MIN_MM", 30.0))
    max_gx = float(getattr(cfg, "FIGURE_COMPOSITE_MAX_GAP_X_MM", 450.0))
    max_gy = float(getattr(cfg, "FIGURE_COMPOSITE_MAX_GAP_Y_MM", 500.0))
    if ox >= overlap_min and gy <= max_gy:
        return True
    if oy >= overlap_min and gx <= max_gx:
        return True
    return False


def _merge_group_dicts(group_a, group_b):
    strokes = list(group_a.get("strokes") or []) + list(group_b.get("strokes") or [])
    orientation = group_a.get("orientation") or group_b.get("orientation") or "other"
    return {
        "orientation": orientation,
        "strokes": strokes,
        "axis_value": float(group_a.get("axis_value") or 0.0),
        "sort_value": _group_sort_value(
            {"orientation": orientation, "strokes": strokes}
        ),
        "composite": True,
    }


def merge_composite_figure_groups(groups, cfg, logs=None, piece_label=""):
    """
    Une grupos de lineas que forman una figura compuesta (ej. grid de cuadros en C).
    No une lineas paralelas sueltas separadas en X (A/B conservan 3 paths).
    """
    if not bool(getattr(cfg, "FIGURE_COMPOSITE_MERGE", True)):
        return list(groups or [])

    pad = float(getattr(cfg, "FIGURE_BBOX_PAD_MM", 2.0))
    working = [dict(g) for g in (groups or [])]
    if len(working) <= 1:
        return working

    merged_any = True
    while merged_any:
        merged_any = False
        bboxes = [_group_bbox(g.get("strokes"), pad_mm=pad) for g in working]
        i = 0
        while i < len(working):
            j = i + 1
            while j < len(working):
                if not bboxes[i] or not bboxes[j]:
                    j += 1
                    continue
                if _composite_groups_linked(bboxes[i], bboxes[j], cfg):
                    working[i] = _merge_group_dicts(working[i], working[j])
                    working.pop(j)
                    bboxes.pop(j)
                    bboxes[i] = _group_bbox(working[i].get("strokes"), pad_mm=pad)
                    merged_any = True
                else:
                    j += 1
            i += 1

    for group in working:
        orient = group.get("orientation") or "vertical"
        group["strokes"] = _order_strokes_within_group(group.get("strokes") or [], orient)
        group["sort_value"] = _group_sort_value(group)

    working.sort(
        key=lambda g: (
            -float(g.get("sort_value") or 0.0),
            str(g.get("orientation") or ""),
        )
    )

    if logs is not None and len(working) != len(groups or []):
        logs.append(
            "{0}figure_composite merged {1} line groups -> {2} figure path(s)".format(
                (piece_label + " ") if piece_label else "",
                len(groups or []),
                len(working),
            )
        )

    return working
