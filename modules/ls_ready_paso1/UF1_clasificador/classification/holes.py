# -*- coding: utf-8 -*-
"""Deteccion de barrenos y agrupacion BarrA / BarrA2."""
import math

from .geometry import (
    bbox_of_points,
    dist2d,
    polygon_area_abs,
    polyline_perimeter,
    unique_point_count,
)



def _native_point(segment, key):
    value = (segment or {}).get(key)
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return (float(value[0]), float(value[1]))
    return None


def _line_length(segment):
    start = _native_point(segment, "start_dxf")
    end = _native_point(segment, "end_dxf")
    return dist2d(start, end) if start and end else 0.0


def classify_native_slot(contour_dict, endpoint_tol=0.6, radius_tol=0.15, sweep_tol_deg=1.0):
    """Reconoce una ranura nativa como 2 LINE + 2 ARC semicirculares.

    La orientación es libre; no se supone horizontal ni vertical. La cadena ya
    debe venir orientada/cerrada desde el stitch del lector DXF.
    """
    segments = list(contour_dict.get("native_segments") or [])
    types = [str(seg.get("type") or "").lower() for seg in segments]
    lines = [seg for seg in segments if str(seg.get("type") or "").lower() == "line"]
    arcs = [seg for seg in segments if str(seg.get("type") or "").lower() == "arc"]
    audit = {
        "eligible": False,
        "shape_type": "slot",
        "segment_count": len(segments),
        "line_count": len(lines),
        "arc_count": len(arcs),
        "source_types": types,
    }
    if len(segments) != 4 or len(lines) != 2 or len(arcs) != 2:
        audit["reason"] = "requires_exactly_2_lines_2_arcs"
        return False, audit

    sweeps = [abs(float(seg.get("sweep_deg") or 0.0)) for seg in arcs]
    radii = [float(seg.get("radius_mm") or 0.0) for seg in arcs]
    line_lengths = [_line_length(seg) for seg in lines]
    audit.update({
        "arc_sweeps_deg": [round(v, 6) for v in sweeps],
        "arc_radii_mm": [round(v, 6) for v in radii],
        "line_lengths_mm": [round(v, 6) for v in line_lengths],
    })
    if any(abs(sweep - 180.0) > sweep_tol_deg for sweep in sweeps):
        audit["reason"] = "arc_sweep_not_semicircle"
        return False, audit
    if min(radii) <= 0.0 or abs(radii[0] - radii[1]) > radius_tol:
        audit["reason"] = "arc_radii_mismatch"
        return False, audit
    if min(line_lengths) <= 0.0 or abs(line_lengths[0] - line_lengths[1]) > endpoint_tol:
        audit["reason"] = "line_lengths_mismatch"
        return False, audit

    # Continuidad de la cadena orientada, incluido el cierre.
    gaps = []
    for idx, segment in enumerate(segments):
        end = _native_point(segment, "end_dxf")
        next_start = _native_point(segments[(idx + 1) % len(segments)], "start_dxf")
        gaps.append(dist2d(end, next_start) if end and next_start else 1e9)
    audit["chain_gaps_mm"] = [round(v, 6) for v in gaps]
    if max(gaps) > endpoint_tol:
        audit["reason"] = "native_chain_not_closed"
        return False, audit

    # Las dos líneas deben ser paralelas (pueden venir en sentidos opuestos).
    vectors = []
    for line in lines:
        a = _native_point(line, "start_dxf")
        b = _native_point(line, "end_dxf")
        vectors.append((b[0] - a[0], b[1] - a[1]))
    cross = abs(vectors[0][0] * vectors[1][1] - vectors[0][1] * vectors[1][0])
    denom = max(line_lengths[0] * line_lengths[1], 1e-9)
    parallel_error = cross / denom
    audit["line_parallel_error"] = round(parallel_error, 8)
    if parallel_error > 1e-3:
        audit["reason"] = "slot_lines_not_parallel"
        return False, audit

    audit["eligible"] = True
    audit["reason"] = "native_2_lines_2_semicircle_arcs"
    audit["radius_mm"] = round(sum(radii) / 2.0, 6)
    return True, audit


def classify_inner_contour(contour_dict, cfg):
    is_slot, slot_audit = classify_native_slot(contour_dict)
    contour_dict["slot_detection"] = slot_audit
    if is_slot:
        return "ranura", 0.0, 0.0

    points = contour_dict.get("points") or []
    if not contour_dict.get("closed") or len(points) < cfg.MIN_INNER_POINTS:
        return "open_or_short", 0.0, 0.0

    area = polygon_area_abs(points)
    if area < cfg.MIN_INNER_AREA:
        return "too_small", 0.0, 0.0

    bb = bbox_of_points(points)
    if not bb:
        return "cuadro", 0.0, 0.0

    width = max(abs(bb[2] - bb[0]), 1e-9)
    height = max(abs(bb[3] - bb[1]), 1e-9)
    aspect = width / float(height)
    elongation = max(width, height) / max(min(width, height), 1e-9)
    perimeter = polyline_perimeter(points)
    points_count = unique_point_count(points)

    circularity = 0.0
    if perimeter > 1e-9:
        circularity = (4.0 * 3.141592653589793 * area) / (perimeter * perimeter)

    looks_round = (
        points_count >= cfg.MIN_BARRENO_POINTS
        and aspect >= cfg.BARRENO_ASPECT_MIN
        and aspect <= cfg.BARRENO_ASPECT_MAX
        and circularity >= cfg.BARRENO_CIRCULARITY_MIN
    )
    looks_oval = (
        points_count >= cfg.MIN_OVAL_POINTS
        and elongation >= cfg.OVAL_ELONGATION_MIN
        and elongation <= cfg.OVAL_ELONGATION_MAX
        and circularity >= cfg.OVAL_CIRCULARITY_MIN
    )

    if looks_round or looks_oval:
        return "barreno", circularity, aspect
    return "cuadro", circularity, aspect


def sort_holes_bottom_to_top_left_to_right(holes):
    """Cama A: prioridad X ascendente y Y descendente en DXF."""
    return sorted(
        holes,
        key=lambda h: (
            float(h["center_dxf"][0]),
            -float(h["center_dxf"][1]),
        ),
    )


def group_holes_by_anchor_window(holes, max_dx, max_dy):
    """
    Agrupa barrenos: ventana 500x800 anclada al primer barreno del grupo.
    Solo distancia al ancla (no cadena B->C).
    """
    remaining = sort_holes_bottom_to_top_left_to_right(holes)
    groups = []
    while remaining:
        anchor = remaining.pop(0)
        ax, ay = anchor["center_dxf"]
        group = [anchor]
        next_remaining = []
        for hole in remaining:
            hx, hy = hole["center_dxf"]
            if abs(hx - ax) <= float(max_dx) and abs(hy - ay) <= float(max_dy):
                group.append(hole)
            else:
                next_remaining.append(hole)
        group = sort_holes_bottom_to_top_left_to_right(group)
        groups.append(group)
        remaining = next_remaining
    return groups
