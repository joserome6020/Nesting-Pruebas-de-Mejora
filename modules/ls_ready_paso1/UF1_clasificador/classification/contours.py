# -*- coding: utf-8 -*-
import copy
"""Extraccion de contornos y trazos desde items del JSON del lector."""
from .geometry import (
    bbox_center,
    bbox_of_points,
    clean_points,
    polygon_centroid,
)


def extract_contour_info(raw_item):
    if isinstance(raw_item, dict):
        closed = bool(raw_item.get("closed", False))
        for key in ("points", "polyline", "vertices", "pts", "path"):
            if key in raw_item:
                return clean_points(raw_item.get(key)), closed
        return clean_points(raw_item), closed
    return clean_points(raw_item), False


def contour_dict_from_raw(raw_item, source_index):
    points, closed = extract_contour_info(raw_item)
    bb = bbox_of_points(points)
    center = bbox_center(bb) if bb else polygon_centroid(points)
    out = {
        "source_index": int(source_index),
        "points": points,
        "closed": bool(closed),
        "etype": str((raw_item or {}).get("etype", "")) if isinstance(raw_item, dict) else "",
        "center_dxf": [round(center[0], 4), round(center[1], 4)],
    }
    if bb:
        out["bbox_dxf"] = [
            round(bb[0], 4),
            round(bb[1], 4),
            round(bb[2], 4),
            round(bb[3], 4),
        ]
    if isinstance(raw_item, dict):
        native_segments = raw_item.get("native_segments")
        if isinstance(native_segments, list) and native_segments:
            out["native_segments"] = copy.deepcopy(native_segments)
            out["native_segment_count"] = len(native_segments)
            out["native_geometry_preserved"] = bool(raw_item.get("native_geometry_preserved", True))
        if raw_item.get("source_etypes") is not None:
            out["source_etypes"] = copy.deepcopy(raw_item.get("source_etypes"))
    return out


def extract_mark_stroke(raw_item, source_index, min_length_mm=0.8):
    points, closed = extract_contour_info(raw_item)
    if len(points) < 2:
        return None
    length = sum(
        (
            (points[i][0] - points[i - 1][0]) ** 2
            + (points[i][1] - points[i - 1][1]) ** 2
        )
        ** 0.5
        for i in range(1, len(points))
    )
    if length < float(min_length_mm):
        return None
    bb = bbox_of_points(points)
    if not bb:
        return None
    center = bbox_center(bb)
    return {
        "source_index": int(source_index),
        "points": points,
        "closed": bool(closed),
        "length_mm": round(length, 4),
        "bbox_dxf": [round(bb[0], 4), round(bb[1], 4), round(bb[2], 4), round(bb[3], 4)],
        "center_dxf": [round(center[0], 4), round(center[1], 4)],
    }
