# -*- coding: utf-8 -*-
"""
DXF -> JSON crudo compatible con classification.classifier / LS READY V3.

Este módulo no reemplaza al lector DXF histórico si ya lo tienes; su objetivo es
crear un puente directo para pruebas: DXF -> raw_json -> LS-ready v3.

Dependencia:
    pip install ezdxf
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import dxf_layer_config as layer_cfg

Point = Tuple[float, float]


@dataclass
class RawPath:
    points: List[Point]
    closed: bool
    etype: str
    layer: str

    def to_item(self) -> dict:
        return {
            "points": [[round(x, 3), round(y, 3)] for x, y in self.points],
            "closed": bool(self.closed),
            "etype": self.etype,
            "layer": self.layer,
        }


def _norm_layer(name: str) -> str:
    out = str(name or "").upper().strip()
    for ch in (" ", "-", "_", "."):
        out = out.replace(ch, "")
    return out


def _alias_set(values: Iterable[str]) -> set:
    return {_norm_layer(v) for v in values}


_PLATE = _alias_set(layer_cfg.PLATE_LAYER_ALIASES)
_CUT_OUTER = _alias_set(layer_cfg.CUT_OUTER_LAYER_ALIASES)
_CUT_INNER = _alias_set(layer_cfg.CUT_INNER_LAYER_ALIASES)
_MARK = _alias_set(layer_cfg.MARK_LAYER_ALIASES)


def classify_layer(layer_name: str) -> Optional[str]:
    key = _norm_layer(layer_name)
    if key in _PLATE:
        return "plate"
    if key in _CUT_INNER:
        return "cut_inner"
    if key in _MARK:
        return "mark"
    if key in _CUT_OUTER:
        return "cut_outer"
    return None


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _close_points(points: List[Point], tol: float = 1e-6) -> List[Point]:
    if len(points) >= 2 and _dist(points[0], points[-1]) > tol:
        points.append(points[0])
    return points


def _bbox(points: Sequence[Point]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _area(points: Sequence[Point]) -> float:
    if len(points) < 3:
        return 0.0
    s = 0.0
    pts = list(points)
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _point_in_poly(pt: Point, poly: Sequence[Point]) -> bool:
    x, y = pt
    inside = False
    pts = list(poly)
    if len(pts) < 3:
        return False
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        denom = (yj - yi) if abs(yj - yi) > 1e-12 else 1e-12
        intersect = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / denom + xi)
        if intersect:
            inside = not inside
        j = i
    return inside


def _center_from_bbox(points: Sequence[Point]) -> Point:
    min_x, min_y, max_x, max_y = _bbox(points)
    return (min_x + max_x) / 2.0, (min_y + max_y) / 2.0


def _arc_points(center: Point, radius: float, start_deg: float, end_deg: float, segments: int) -> List[Point]:
    if segments < 2:
        segments = 2
    sweep = end_deg - start_deg
    if sweep <= 0:
        sweep += 360.0
    pts = []
    for i in range(segments + 1):
        deg = start_deg + sweep * (i / segments)
        rad = math.radians(deg)
        pts.append((center[0] + radius * math.cos(rad), center[1] + radius * math.sin(rad)))
    return pts


def _circle_points(center: Point, radius: float, segments: int) -> List[Point]:
    pts = []
    for i in range(segments):
        rad = math.tau * i / segments
        pts.append((center[0] + radius * math.cos(rad), center[1] + radius * math.sin(rad)))
    pts.append(pts[0])
    return pts


def _bulge_segment_points(p1: Point, p2: Point, bulge: float) -> List[Point]:
    """Devuelve puntos intermedios de un segmento LWPOLYLINE con bulge."""
    if abs(bulge) < 1e-12:
        return [p1, p2]

    x1, y1 = p1
    x2, y2 = p2
    chord = math.hypot(x2 - x1, y2 - y1)
    if chord < 1e-12:
        return [p1]

    theta = 4.0 * math.atan(bulge)
    radius = abs(chord / (2.0 * math.sin(theta / 2.0)))
    mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    dx, dy = (x2 - x1) / chord, (y2 - y1) / chord
    # Normal izquierda del segmento. El signo del bulge define el lado del centro.
    nx, ny = -dy, dx
    h_sq = max(radius * radius - (chord / 2.0) ** 2, 0.0)
    h = math.sqrt(h_sq)
    sign = 1.0 if bulge > 0 else -1.0
    cx, cy = mx + sign * nx * h, my + sign * ny * h

    a1 = math.atan2(y1 - cy, x1 - cx)
    a2 = math.atan2(y2 - cy, x2 - cx)
    if bulge > 0 and a2 <= a1:
        a2 += math.tau
    elif bulge < 0 and a2 >= a1:
        a2 -= math.tau

    sweep = a2 - a1
    segs = max(2, int(abs(math.degrees(sweep)) / 180.0 * layer_cfg.BULGE_SEGMENTS_PER_180_DEG))
    pts = []
    for i in range(segs + 1):
        a = a1 + sweep * (i / segs)
        pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return pts


def _append_no_duplicate(target: List[Point], pts: Sequence[Point], tol: float = 1e-7) -> None:
    for p in pts:
        if target and _dist(target[-1], p) <= tol:
            continue
        target.append(p)


def _entity_to_path(entity) -> Optional[RawPath]:
    etype = entity.dxftype()
    layer = getattr(entity.dxf, "layer", "")

    if etype == "LINE":
        a = entity.dxf.start
        b = entity.dxf.end
        return RawPath(points=[(float(a.x), float(a.y)), (float(b.x), float(b.y))], closed=False, etype=etype, layer=layer)

    if etype == "CIRCLE":
        c = entity.dxf.center
        pts = _circle_points((float(c.x), float(c.y)), float(entity.dxf.radius), int(layer_cfg.CIRCLE_SEGMENTS))
        return RawPath(points=pts, closed=True, etype=etype, layer=layer)

    if etype == "ARC":
        c = entity.dxf.center
        pts = _arc_points(
            (float(c.x), float(c.y)),
            float(entity.dxf.radius),
            float(entity.dxf.start_angle),
            float(entity.dxf.end_angle),
            int(layer_cfg.ARC_SEGMENTS),
        )
        return RawPath(points=pts, closed=False, etype=etype, layer=layer)

    if etype == "LWPOLYLINE":
        raw = list(entity.get_points("xyb"))
        if not raw:
            return None
        closed = bool(entity.closed)
        pts: List[Point] = []
        n = len(raw)
        last_index = n if closed else n - 1
        for i in range(last_index):
            x1, y1, bulge = raw[i]
            x2, y2, _ = raw[(i + 1) % n]
            seg_pts = _bulge_segment_points((float(x1), float(y1)), (float(x2), float(y2)), float(bulge or 0.0))
            _append_no_duplicate(pts, seg_pts)
        if not closed and n == 1:
            x, y, _ = raw[0]
            pts = [(float(x), float(y))]
        if closed:
            _close_points(pts)
        return RawPath(points=pts, closed=closed, etype=etype, layer=layer)

    if etype == "POLYLINE":
        pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
        closed = bool(entity.is_closed)
        if closed:
            _close_points(pts)
        return RawPath(points=pts, closed=closed, etype=etype, layer=layer)

    return None


def _infer_sheet_from_plate_paths(plate_paths: Sequence[RawPath]) -> Optional[dict]:
    if not plate_paths:
        return None
    # Usa el contorno PLATE de mayor área como placa.
    path = max(plate_paths, key=lambda p: _area(p.points))
    min_x, min_y, max_x, max_y = _bbox(path.points)
    width = max_x - min_x
    height = max_y - min_y
    return _sheet_meta_from_bbox((min_x, min_y, max_x, max_y), path.layer, width, height)


def _match_known_sheet(width: float, height: float) -> Tuple[str, str]:
    for w, h, plate_size, scene_key in layer_cfg.KNOWN_SHEET_SIZES_MM:
        if abs(width - w) <= layer_cfg.SHEET_SIZE_TOL_MM and abs(height - h) <= layer_cfg.SHEET_SIZE_TOL_MM:
            return plate_size, scene_key
        if abs(width - h) <= layer_cfg.SHEET_SIZE_TOL_MM and abs(height - w) <= layer_cfg.SHEET_SIZE_TOL_MM:
            return plate_size, scene_key
    return "", ""


def _sheet_meta_from_bbox(bbox: Tuple[float, float, float, float], source_layer: str, width: float, height: float) -> dict:
    min_x, min_y, max_x, max_y = bbox
    plate_size, scene_key = _match_known_sheet(width, height)
    width_in = width / 25.4 if width else 0.0
    height_in = height / 25.4 if height else 0.0
    if not plate_size and width and height:
        plate_size = f"{round(width_in):.0f}x{round(height_in):.0f}"
        scene_key = plate_size.upper()
    return {
        "source_layer": source_layer,
        "width_mm": round(width, 4),
        "height_mm": round(height, 4),
        "width_in": round(width_in, 4),
        "height_in": round(height_in, 4),
        "plate_size_in": plate_size,
        "scene_size_key": scene_key,
        "origin_x_mm": round(min_x, 4),
        "origin_y_mm": round(min_y, 4),
        "bbox": [round(min_x, 4), round(min_y, 4), round(max_x, 4), round(max_y, 4)],
    }


def _infer_sheet_from_all(paths: Sequence[RawPath]) -> Optional[dict]:
    closed = [p for p in paths if p.closed and len(p.points) >= 4]
    candidates = []
    for p in closed:
        min_x, min_y, max_x, max_y = _bbox(p.points)
        width = max_x - min_x
        height = max_y - min_y
        plate_size, scene_key = _match_known_sheet(width, height)
        if plate_size:
            candidates.append((_area(p.points), p, width, height, plate_size, scene_key))
    if candidates:
        _, p, width, height, _, _ = max(candidates, key=lambda x: x[0])
        min_x, min_y, max_x, max_y = _bbox(p.points)
        return _sheet_meta_from_bbox((min_x, min_y, max_x, max_y), p.layer, width, height)

    # Fallback: bbox global de toda la geometría. Esto permite clasificar, pero debe validarse.
    all_pts = [pt for p in paths for pt in p.points]
    if not all_pts:
        return None
    min_x, min_y, max_x, max_y = _bbox(all_pts)
    return _sheet_meta_from_bbox((min_x, min_y, max_x, max_y), "INFERRED_GLOBAL_BBOX", max_x - min_x, max_y - min_y)


def _classify_unknown_paths(paths: Sequence[RawPath]) -> Tuple[List[RawPath], List[RawPath], List[RawPath]]:
    """Clasifica paths sin layer reconocido en cut_outer/cut_inner/mark."""
    open_paths = [p for p in paths if not p.closed or len(p.points) < 4]
    closed_paths = [p for p in paths if p.closed and _area(p.points) >= layer_cfg.MIN_CLOSED_AREA_MM2]

    cut_outer: List[RawPath] = []
    cut_inner: List[RawPath] = []

    # Primero identifica contornos contenidos dentro de otro contorno más grande.
    closed_sorted = sorted(closed_paths, key=lambda p: _area(p.points), reverse=True)
    for path in closed_sorted:
        center = _center_from_bbox(path.points)
        contained = False
        for bigger in closed_sorted:
            if bigger is path:
                continue
            if _area(bigger.points) <= _area(path.points):
                continue
            if _point_in_poly(center, bigger.points):
                contained = True
                break
        if contained:
            cut_inner.append(path)
        else:
            cut_outer.append(path)

    return cut_outer, cut_inner, open_paths


def convert_dxf_to_raw_json(dxf_path: str, output_path: Optional[str] = None) -> dict:
    try:
        import ezdxf  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "No se encontró la librería ezdxf. Instálala con: pip install ezdxf"
        ) from exc

    dxf_path = os.path.normpath(dxf_path)
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    cut_outer: List[RawPath] = []
    cut_inner: List[RawPath] = []
    mark: List[RawPath] = []
    plate_paths: List[RawPath] = []
    unknown: List[RawPath] = []
    ignored: List[dict] = []

    for entity in msp:
        path = _entity_to_path(entity)
        if path is None:
            ignored.append({
                "etype": entity.dxftype(),
                "layer": getattr(entity.dxf, "layer", ""),
                "reason": "entity_not_supported_or_text_not_exploded",
            })
            continue
        category = classify_layer(path.layer)
        if category == "plate":
            plate_paths.append(path)
        elif category == "cut_outer":
            cut_outer.append(path)
        elif category == "cut_inner":
            cut_inner.append(path)
        elif category == "mark":
            mark.append(path)
        else:
            unknown.append(path)

    inferred_outer, inferred_inner, inferred_mark = _classify_unknown_paths(unknown)
    cut_outer.extend(inferred_outer)
    cut_inner.extend(inferred_inner)
    mark.extend(inferred_mark)

    sheet = _infer_sheet_from_plate_paths(plate_paths)
    if sheet is None:
        sheet = _infer_sheet_from_all([*plate_paths, *cut_outer, *cut_inner, *mark])

    scene_size_key = (sheet or {}).get("scene_size_key") or ""
    height_mm = (sheet or {}).get("height_mm") or 0.0
    scene = {
        "scene_size_key": scene_size_key,
        "scene_width_code_in": int(round(height_mm / 25.4)) if height_mm else None,
        "laser_line": "L3",
        "cama": "B",
        "source_layer": (sheet or {}).get("source_layer", ""),
    }

    raw = {
        "meta": {
            "source_dxf": dxf_path,
            "scale": 1.0,
            "inner_accept_margin": 50.0,
            "join_tol": 0.5,
            "circle_segments": int(layer_cfg.CIRCLE_SEGMENTS),
            "arc_segments": int(layer_cfg.ARC_SEGMENTS),
            "curve_flatten_tol": 1.0,
            "filter_cut_inner": False,
            "ref_bbox_mode": "CUT_OUTER_ONLY",
            "use_only_largest_cut_outer": True,
            "stitch_mark": False,
            "auto_close_min_points": 3,
            "json_coord_decimals": 3,
            "sheet": sheet or {},
            "scene": scene,
            "ignored_entities": ignored,
            "dxf_reader": {
                "module": "dxf.dxf_to_raw_json",
                "layer_mode": "layer_aliases_plus_geometry_fallback",
                "unknown_paths": len(unknown),
                "plate_paths": len(plate_paths),
            },
        },
        "cut_outer": [p.to_item() for p in cut_outer],
        "cut_inner": [p.to_item() for p in cut_inner],
        "mark": [p.to_item() for p in mark],
    }

    if output_path:
        output_path = os.path.normpath(output_path)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)

    return raw
