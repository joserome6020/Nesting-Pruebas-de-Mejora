"""Colocación de marcaje stick sin solapes con geometría existente."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from modules.dxf_mark.stick_font import (
    build_stick_strokes,
    rotate_strokes,
    text_bbox,
    translate_strokes,
)


@dataclass
class PolyData:
    points: list[tuple[float, float]]
    area: float
    centroid: tuple[float, float]
    bbox: tuple[float, float, float, float]


def polygon_area(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    a = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) * 0.5


def polygon_centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    if len(points) < 3:
        xs = [p[0] for p in points] or [0.0]
        ys = [p[1] for p in points] or [0.0]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    a = 0.0
    cx = 0.0
    cy = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(a) < 1e-18:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return sum(xs) / n, sum(ys) / n
    a *= 0.5
    return cx / (6.0 * a), cy / (6.0 * a)


def poly_bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) + 1e-18) + xi):
            inside = not inside
        j = i
    return inside


def segments_from_points(
    points: list[tuple[float, float]], closed: bool
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    if len(points) < 2:
        return segs
    n = len(points)
    last = n if closed else n - 1
    for i in range(last):
        a = points[i]
        b = points[(i + 1) % n]
        if a != b:
            segs.append((a, b))
    return segs


def orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])


def on_segment(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]) -> bool:
    return (
        min(a[0], b[0]) - 1e-9 <= p[0] <= max(a[0], b[0]) + 1e-9
        and min(a[1], b[1]) - 1e-9 <= p[1] <= max(a[1], b[1]) + 1e-9
    )


def segments_intersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    o1 = orientation(a1, a2, b1)
    o2 = orientation(a1, a2, b2)
    o3 = orientation(b1, b2, a1)
    o4 = orientation(b1, b2, a2)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return True
    if abs(o1) < 1e-12 and on_segment(a1, a2, b1):
        return True
    if abs(o2) < 1e-12 and on_segment(a1, a2, b2):
        return True
    if abs(o3) < 1e-12 and on_segment(b1, b2, a1):
        return True
    if abs(o4) < 1e-12 and on_segment(b1, b2, a2):
        return True
    return False


def _seg_point_dist2(
    a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]
) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    len2 = dx * dx + dy * dy
    if len2 < 1e-18:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len2))
    qx, qy = ax + t * dx, ay + t * dy
    return (px - qx) ** 2 + (py - qy) ** 2


def _seg_seg_dist2(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> float:
    if segments_intersect(a1, a2, b1, b2):
        return 0.0
    return min(
        _seg_point_dist2(a1, a2, b1),
        _seg_point_dist2(a1, a2, b2),
        _seg_point_dist2(b1, b2, a1),
        _seg_point_dist2(b1, b2, a2),
    )


def make_poly_data(points: list[tuple[float, float]]) -> PolyData | None:
    if len(points) < 3:
        return None
    clean = list(points)
    if clean[0] == clean[-1] and len(clean) > 3:
        clean = clean[:-1]
    if len(clean) < 3:
        return None
    area = polygon_area(clean)
    if area < 1e-12:
        return None
    return PolyData(
        points=clean,
        area=area,
        centroid=polygon_centroid(clean),
        bbox=poly_bbox(clean),
    )


def strokes_segments(
    strokes: list[list[tuple[float, float]]],
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for stroke in strokes:
        segs.extend(segments_from_points(stroke, closed=False))
    return segs


def mark_fits(
    strokes: list[list[tuple[float, float]]],
    outer: PolyData,
    holes: Iterable[PolyData],
    obstacle_segments: list[tuple[tuple[float, float], tuple[float, float]]],
    clearance: float,
) -> bool:
    """Texto dentro del outer, fuera de agujeros, sin cruzar ni acercarse a obstáculos."""
    pts = [p for s in strokes for p in s]
    if not pts:
        return False
    for p in pts:
        if not point_in_polygon(p, outer.points):
            return False
        for h in holes:
            if point_in_polygon(p, h.points):
                return False

    mark_segs = strokes_segments(strokes)
    clr2 = float(clearance) ** 2
    for ms in mark_segs:
        for obs in obstacle_segments:
            if _seg_seg_dist2(ms[0], ms[1], obs[0], obs[1]) < clr2:
                return False
    return True


def _build_seg_index(
    segs: list[tuple[tuple[float, float], tuple[float, float]]], cell: float
) -> dict[tuple[int, int], list]:
    """Cuadrícula espacial de segmentos para consultas rápidas por bbox."""
    grid: dict[tuple[int, int], list] = {}
    inv = 1.0 / cell
    for seg in segs:
        (x1, y1), (x2, y2) = seg
        ix0 = int(min(x1, x2) * inv)
        ix1 = int(max(x1, x2) * inv)
        iy0 = int(min(y1, y2) * inv)
        iy1 = int(max(y1, y2) * inv)
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                grid.setdefault((ix, iy), []).append(seg)
    return grid


def _query_segs(
    grid: dict[tuple[int, int], list],
    cell: float,
    bbox: tuple[float, float, float, float],
) -> list:
    x0, y0, x1, y1 = bbox
    inv = 1.0 / cell
    out: list = []
    seen: set[int] = set()
    for ix in range(int(x0 * inv), int(x1 * inv) + 1):
        for iy in range(int(y0 * inv), int(y1 * inv) + 1):
            for seg in grid.get((ix, iy), ()):
                sid = id(seg)
                if sid not in seen:
                    seen.add(sid)
                    out.append(seg)
    return out


def _prepared_region(outer: PolyData, holes: list[PolyData]):
    """Región shapely preparada (outer - holes) para containment rápido."""
    try:
        from shapely.geometry import Polygon
        from shapely.prepared import prep

        poly = Polygon(
            outer.points,
            [list(h.points) for h in holes if len(h.points) >= 3],
        )
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            return None
        return prep(poly)
    except Exception:
        return None


def _strokes_contained(
    placed: list[list[tuple[float, float]]],
    prepared_region,
    outer: PolyData,
    holes: list[PolyData],
) -> bool:
    if prepared_region is not None:
        try:
            from shapely.geometry import MultiLineString

            return bool(prepared_region.covers(MultiLineString(placed)))
        except Exception:
            pass
    # Fallback puro Python: puntos dentro del outer y fuera de agujeros.
    for stroke in placed:
        for p in stroke:
            if not point_in_polygon(p, outer.points):
                return False
            for h in holes:
                if point_in_polygon(p, h.points):
                    return False
    return True


def _eval_clearance_score(
    placed: list[list[tuple[float, float]]],
    bbox: tuple[float, float, float, float],
    grid: dict[tuple[int, int], list],
    cell: float,
    clearance: float,
    score_cap: float,
) -> float | None:
    """None si viola el clearance; si no, distancia mínima a obstáculos (cap)."""
    x0, y0, x1, y1 = bbox
    nearby = _query_segs(
        grid, cell, (x0 - score_cap, y0 - score_cap, x1 + score_cap, y1 + score_cap)
    )
    if not nearby:
        return score_cap
    clr2 = clearance * clearance
    best2 = score_cap * score_cap
    for a, b in strokes_segments(placed):
        for obs in nearby:
            d2 = _seg_seg_dist2(a, b, obs[0], obs[1])
            if d2 < clr2:
                return None
            if d2 < best2:
                best2 = d2
    return math.sqrt(best2)


def find_mark_position(
    text: str,
    height: float,
    outer: PolyData,
    holes: list[PolyData],
    obstacle_segments: list[tuple[tuple[float, float], tuple[float, float]]],
    *,
    clearance: float,
    angle_deg: float = 0.0,
    search_rings: int = 20,
    width_factor: float = 0.72,
    spacing_factor: float = 0.18,
    max_candidates: int = 1400,
) -> list[list[tuple[float, float]]] | None:
    """
    Busca la MEJOR posición para el texto: válida (dentro de la pieza, fuera de
    agujeros, lejos de toda línea) y con máxima separación a obstáculos,
    prefiriendo posiciones centradas. angle_deg rota el texto (0=horizontal,
    90=vertical a lo largo de +Y). Devuelve strokes trasladados o None.
    """
    base0 = build_stick_strokes(
        text, (0.0, 0.0), height, width_factor=width_factor, spacing_factor=spacing_factor
    )
    if not base0:
        return None
    base = rotate_strokes(base0, angle_deg, origin=(0.0, 0.0))
    bb = text_bbox(base)
    if not bb:
        return None
    bx0, by0, bx1, by1 = bb
    tw, th = bx1 - bx0, by1 - by0
    if tw <= 0 or th <= 0:
        return None
    bcx, bcy = (bx0 + bx1) * 0.5, (by0 + by1) * 0.5

    cx, cy = outer.centroid
    ox0, oy0, ox1, oy1 = outer.bbox
    clr = float(clearance)
    score_cap = max(clr * 4.0, 1e-9)
    cell = max(th, tw * 0.15, clr * 2.0, 1e-6)
    grid = _build_seg_index(obstacle_segments, cell)
    prepared_region = _prepared_region(outer, holes)

    # Candidatos: centroide + cuadrícula del bbox + anillos alrededor del centroide.
    candidates: list[tuple[float, float]] = [(cx, cy)]
    x_lo = ox0 + tw * 0.5 + clr
    x_hi = ox1 - tw * 0.5 - clr
    y_lo = oy0 + th * 0.5 + clr
    y_hi = oy1 - th * 0.5 - clr
    if x_hi >= x_lo and y_hi >= y_lo:
        step_x = max(tw * 0.35, min(tw, th) * 0.8, 1e-6)
        step_y = max(th * 0.35, min(tw, th) * 0.8, 1e-6)
        nx = min(28, max(1, int((x_hi - x_lo) / step_x) + 1))
        ny = min(28, max(1, int((y_hi - y_lo) / step_y) + 1))
        for i in range(nx + 1):
            gx = x_lo + (x_hi - x_lo) * (i / nx) if nx > 0 else (x_lo + x_hi) * 0.5
            for j in range(ny + 1):
                gy = y_lo + (y_hi - y_lo) * (j / ny) if ny > 0 else (y_lo + y_hi) * 0.5
                candidates.append((gx, gy))

    span = max(ox1 - ox0, oy1 - oy0, 1.0)
    rings = max(2, int(search_rings))
    for ring in range(1, rings + 1):
        r = (ring / rings) * span * 0.48
        n_ang = max(8, ring * 4)
        for k in range(n_ang):
            ang = (2.0 * math.pi * k) / n_ang
            candidates.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))

    # Ordena por cercanía al centroide (preferencia centrado) y dedupe grueso.
    dedup_cell = max(min(tw, th) * 0.5, 1e-6)
    seen: set[tuple[int, int]] = set()
    ordered: list[tuple[float, float]] = []
    for p in sorted(candidates, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2):
        key = (int(p[0] / dedup_cell), int(p[1] / dedup_cell))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(p)
        if len(ordered) >= max_candidates:
            break

    best: list[list[tuple[float, float]]] | None = None
    best_score = -1.0
    for ax, ay in ordered:
        dx = ax - bcx
        dy = ay - bcy
        px0, py0, px1, py1 = bx0 + dx, by0 + dy, bx1 + dx, by1 + dy
        # Filtro rápido: bbox del texto dentro del bbox del outer (condición necesaria).
        if px0 < ox0 + clr or py0 < oy0 + clr or px1 > ox1 - clr or py1 > oy1 - clr:
            continue
        placed = translate_strokes(base, dx, dy)
        if not _strokes_contained(placed, prepared_region, outer, holes):
            continue
        score = _eval_clearance_score(
            placed, (px0, py0, px1, py1), grid, cell, clr, score_cap
        )
        if score is None:
            continue
        if score > best_score + 1e-9:
            best_score = score
            best = placed
            if best_score >= score_cap * 0.999:
                break
    return best
