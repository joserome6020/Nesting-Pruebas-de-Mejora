"""
Exporta anillos facetados del nesting como entidades DXF nativas (LINE / ARC / CIRCLE).
Evita polilíneas densas en barrenos y radios — requisito para PQart / Robot Plasma.
"""
from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]

_TAU = 2.0 * math.pi


def _as_point(pt) -> Optional[Point]:
    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
        try:
            return float(pt[0]), float(pt[1])
        except (TypeError, ValueError):
            return None
    return None


def normalize_ring(points: Iterable, *, closed: bool = True) -> List[Point]:
    out: List[Point] = []
    for raw in points or []:
        p = _as_point(raw)
        if p is None:
            continue
        if out and abs(out[-1][0] - p[0]) < 1e-6 and abs(out[-1][1] - p[1]) < 1e-6:
            continue
        out.append(p)
    if closed and len(out) > 2:
        if abs(out[0][0] - out[-1][0]) < 1e-6 and abs(out[0][1] - out[-1][1]) < 1e-6:
            out.pop()
    return out


def circle_centroid_mean(pts: Sequence[Point]) -> Optional[Tuple[float, float, float, float]]:
    """Centroide + radio medio: coincide con el anillo facetado del nesting (sin desplazar el contorno)."""
    n = len(pts)
    if n < 4:
        return None
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    radii = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]
    r = sum(radii) / n
    if r <= 1e-9:
        return None
    err_max = max(abs(ri - r) for ri in radii)
    return cx, cy, r, err_max


def circle_from_three_points(
    p0: Point, p1: Point, p2: Point
) -> Optional[Tuple[float, float, float]]:
    """Círculo que pasa exactamente por tres puntos (arcos anclados a vértices del nest)."""
    ax, ay = p0
    bx, by = p1
    cx, cy = p2
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-14:
        return None
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    if r <= 1e-9:
        return None
    return ux, uy, r


def fit_circle_least_squares(pts: Sequence[Point]) -> Optional[Tuple[float, float, float, float]]:
    """Ajuste algebraico; devuelve (cx, cy, r, err_max)."""
    if len(pts) < 4:
        return None
    try:
        import numpy as np

        arr = np.asarray(pts, dtype=float)
        x, y = arr[:, 0], arr[:, 1]
        x_m, y_m = float(np.mean(x)), float(np.mean(y))
        u, v = x - x_m, y - y_m
        Suu = float(np.sum(u * u))
        Svv = float(np.sum(v * v))
        Suv = float(np.sum(u * v))
        if abs(Suu * Svv - Suv * Suv) < 1e-22:
            return None
        Suuu = float(np.sum(u**3))
        Svvv = float(np.sum(v**3))
        Suvv = float(np.sum(u * v * v))
        Svuu = float(np.sum(v * u * u))
        A = np.array([[Suu, Suv], [Suv, Svv]])
        b = 0.5 * np.array([Suuu + Suvv, Svvv + Svuu])
        uc, vc = np.linalg.solve(A, b)
        cx, cy = float(uc + x_m), float(vc + y_m)
        r_sq = uc * uc + vc * vc + (Suu + Svv) / max(len(pts), 1)
        if r_sq <= 1e-18:
            return None
        r = float(math.sqrt(r_sq))
        err_max = float(np.max(np.abs(np.hypot(x - cx, y - cy) - r)))
        return cx, cy, r, err_max
    except Exception:
        return _fit_circle_kasa(pts)


def _fit_circle_kasa(pts: Sequence[Point]) -> Optional[Tuple[float, float, float, float]]:
    n = len(pts)
    if n < 4:
        return None
    sx = sy = sx2 = sy2 = sxy = sx3 = sy3 = sx2y = sxy2 = 0.0
    for x, y in pts:
        x2, y2 = x * x, y * y
        sx += x
        sy += y
        sx2 += x2
        sy2 += y2
        sxy += x * y
        sx3 += x2 * x
        sy3 += y2 * y
        sx2y += x2 * y
        sxy2 += x * y2
    c1 = n * sx2 - sx * sx
    c2 = n * sxy - sx * sy
    c3 = n * sy2 - sy * sy
    c4 = 0.5 * (n * (sx3 + sxy2) - (sx2 + sy2) * sx)
    c5 = 0.5 * (n * (sy3 + sx2y) - (sx2 + sy2) * sy)
    det = c1 * c3 - c2 * c2
    if abs(det) < 1e-18:
        return None
    cx = (c4 * c3 - c5 * c2) / det
    cy = (c1 * c5 - c2 * c4) / det
    r = math.sqrt(max(cx * cx + cy * cy + (sx2 + sy2 - 2 * sx * cx - 2 * sy * cy) / n, 0.0))
    if r <= 1e-9:
        return None
    err_max = max(abs(math.hypot(x - cx, y - cy) - r) for x, y in pts)
    return cx, cy, r, err_max


def _tol_for_radius(r: float) -> float:
    return max(0.045, float(r) * 0.007)


def _angles_unwrapped(pts: Sequence[Point], cx: float, cy: float) -> List[float]:
    raw = [math.atan2(y - cy, x - cx) for x, y in pts]
    out = [raw[0]]
    for a in raw[1:]:
        v = a
        while v < out[-1] - 0.02:
            v += _TAU
        while v > out[-1] + _TAU - 0.02:
            v -= _TAU
        out.append(v)
    return out


def _is_monotonic_arc(pts: Sequence[Point], cx: float, cy: float, r: float, tol: float) -> bool:
    if len(pts) < 3:
        return False
    for x, y in pts:
        if abs(math.hypot(x - cx, y - cy) - r) > tol:
            return False
    ang = _angles_unwrapped(pts, cx, cy)
    sweep = ang[-1] - ang[0]
    if abs(sweep) < math.radians(1.5):
        return False
    dtheta = sweep / max(len(pts) - 1, 1)
    for i in range(1, len(ang)):
        step = ang[i] - ang[i - 1]
        if abs(step) < 1e-9:
            continue
        if step * sweep < 0:
            return False
        if abs(step - dtheta) > math.radians(12.0):
            return False
    return True


def _try_full_circle(pts: Sequence[Point], *, relaxed: bool = False) -> Optional[Tuple[float, float, float]]:
    min_pts = 4 if relaxed else 6
    if len(pts) < min_pts:
        return None
    fit = circle_centroid_mean(pts) or fit_circle_least_squares(pts)
    if not fit:
        return None
    cx, cy, r, err = fit
    if r < 0.25:
        return None
    tol = _tol_for_radius(r) * (2.5 if relaxed else 1.0)
    if err > tol:
        return None
    angles = sorted(math.atan2(y - cy, x - cx) for x, y in pts)
    if len(angles) < min_pts:
        return None
    gaps = []
    for i in range(len(angles) - 1):
        gaps.append(angles[i + 1] - angles[i])
    gaps.append(angles[0] + _TAU - angles[-1])
    max_gap = math.radians(90.0 if relaxed else 45.0)
    if max(gaps) > max_gap:
        return None
    return cx, cy, r


def _fit_arc_segment(pts: Sequence[Point]) -> Optional[Tuple[str, float, float, float, bool, Point, Point]]:
    if len(pts) < 3:
        return None
    p0, p_end = pts[0], pts[-1]
    p_mid = pts[len(pts) // 2]
    circ3 = circle_from_three_points(p0, p_mid, p_end)
    if circ3:
        cx, cy, r = circ3
        err = max(abs(math.hypot(x - cx, y - cy) - r) for x, y in pts)
    else:
        fit = fit_circle_least_squares(pts)
        if not fit:
            return None
        cx, cy, r, err = fit
    if r < 0.2:
        return None
    tol = _tol_for_radius(r)
    if err > tol:
        return None
    if not _is_monotonic_arc(pts, cx, cy, r, tol):
        return None

    ang = _angles_unwrapped(pts, cx, cy)
    sweep = ang[-1] - ang[0]
    if abs(sweep) >= math.radians(350.0):
        return ("circle", cx, cy, r, True, pts[0], pts[-1])
    if abs(sweep) < math.radians(2.0):
        return None
    ccw = sweep > 0
    return ("arc", cx, cy, r, ccw, pts[0], pts[-1])


def _add_line(msp, p1: Point, p2: Point, layer: str) -> None:
    if math.hypot(p2[0] - p1[0], p2[1] - p1[1]) < 1e-6:
        return
    msp.add_line(p1, p2, dxfattribs={"layer": layer})


def _add_arc(msp, cx: float, cy: float, r: float, p_start: Point, p_end: Point, ccw: bool, layer: str) -> None:
    if not ccw:
        p_start, p_end = p_end, p_start
    sa = math.degrees(math.atan2(p_start[1] - cy, p_start[0] - cx))
    ea = math.degrees(math.atan2(p_end[1] - cy, p_end[0] - cx))
    while ea < sa - 1e-6:
        ea += 360.0
    msp.add_arc((cx, cy), r, sa, ea, dxfattribs={"layer": layer})


def _point_line_dist(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    ln2 = dx * dx + dy * dy
    if ln2 < 1e-18:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / ln2))
    qx = x1 + t * dx
    qy = y1 + t * dy
    return math.hypot(px - qx, py - qy)


def _max_collinear_span(pts: List[Point], i: int, n: int) -> int:
    j = (i + 1) % n
    if j == i:
        return 1
    x1, y1 = pts[i]
    x2, y2 = pts[j]
    chord = math.hypot(x2 - x1, y2 - y1)
    if chord < 1e-6:
        return 1
    tol = max(0.08, chord * 0.002)
    span = 1
    for k in range(2, n + 1):
        jj = (i + k) % n
        if jj == i:
            break
        if _point_line_dist(pts[jj][0], pts[jj][1], x1, y1, x2, y2) > tol:
            break
        span = k
    return span


def _longest_arc_from(pts: List[Point], i: int, n: int) -> Tuple[int, Optional[Tuple]]:
    best_len = 1
    best_arc = None
    max_span = min(n - 1, 240)
    for span in range(2, max_span + 1):
        seg = [pts[(i + k) % n] for k in range(span + 1)]
        arc = _fit_arc_segment(seg)
        if arc:
            best_len = span
            best_arc = arc
        elif span > 4 and best_arc is not None:
            break
    return best_len, best_arc


def export_ring_native(
    msp,
    points: Iterable,
    layer: str,
    *,
    closed: bool = True,
    prefer_circle: bool = False,
) -> bool:
    """
    Dibuja un anillo como LINE / ARC / CIRCLE nativos.
    Devuelve True si se exportó algo.
    prefer_circle: para barrenos; acepta ajuste más tolerante a CIRCLE único.
    """
    pts = normalize_ring(points, closed=closed)
    if len(pts) < 2:
        return False

    if closed and len(pts) >= (4 if prefer_circle else 6):
        circ = _try_full_circle(pts, relaxed=prefer_circle)
        if circ:
            msp.add_circle((circ[0], circ[1]), circ[2], dxfattribs={"layer": layer})
            return True

    n = len(pts)
    if closed:
        i = 0
        guard = 0
        while guard < n * 4:
            guard += 1
            span, arc = _longest_arc_from(pts, i, n)
            if arc and span >= 2:
                kind = arc[0]
                if kind == "circle":
                    msp.add_circle((arc[1], arc[2]), arc[3], dxfattribs={"layer": layer})
                    return True
                _add_arc(msp, arc[1], arc[2], arc[3], arc[5], arc[6], arc[4], layer)
                i = (i + span) % n
                if i == 0:
                    break
                continue
            line_span = _max_collinear_span(pts, i, n)
            j = (i + line_span) % n
            _add_line(msp, pts[i], pts[j], layer)
            i = j
            if i == 0:
                break
        return guard > 0

    i = 0
    while i < n - 1:
        best_arc = None
        arc_span = 1
        for cand in range(2, min(n - i, 200)):
            seg = pts[i : i + cand]
            arc = _fit_arc_segment(seg)
            if arc:
                arc_span = cand - 1
                best_arc = arc
            elif cand > 3 and best_arc:
                break
        if best_arc and arc_span >= 2:
            if best_arc[0] == "circle":
                msp.add_circle((best_arc[1], best_arc[2]), best_arc[3], dxfattribs={"layer": layer})
                return True
            _add_arc(msp, best_arc[1], best_arc[2], best_arc[3], best_arc[5], best_arc[6], best_arc[4], layer)
            i += arc_span
            continue
        tol_run = 1
        if i + 2 < len(pts):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            tol = max(0.08, math.hypot(x2 - x1, y2 - y1) * 0.002)
            for k in range(i + 2, len(pts)):
                if _point_line_dist(pts[k][0], pts[k][1], x1, y1, x2, y2) > tol:
                    break
                tol_run = k - i
        j = i + tol_run
        _add_line(msp, pts[i], pts[j], layer)
        i = j
    return True
