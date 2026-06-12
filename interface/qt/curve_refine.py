"""Refinado opcional de anillos facetados para visualización CAD (mm)."""
from __future__ import annotations

import math
import os
from typing import Iterable, Sequence

DISPLAY_CURVE_TOL_MM = 0.06
# Desactivado por defecto: el refinado agresivo generaba arcos falsos en rectas.
_ENABLED = os.getenv("ARGA_DISPLAY_CURVE_REFINE", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def refine_enabled() -> bool:
    return _ENABLED


def _as_xy(pt) -> tuple[float, float] | None:
    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
        return float(pt[0]), float(pt[1])
    if isinstance(pt, dict):
        x, y = pt.get("x", pt.get("X")), pt.get("y", pt.get("Y"))
        if x is not None and y is not None:
            return float(x), float(y)
    return None


def _ring_points(ring: Iterable) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for pt in ring or []:
        xy = _as_xy(pt)
        if xy is not None:
            out.append(xy)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def _fit_circle_3p(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
) -> tuple[tuple[float, float], float] | None:
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    d = 2.0 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return None
    ux = (
        (x1 * x1 + y1 * y1) * (y2 - y3)
        + (x2 * x2 + y2 * y2) * (y3 - y1)
        + (x3 * x3 + y3 * y3) * (y1 - y2)
    ) / d
    uy = (
        (x1 * x1 + y1 * y1) * (x3 - x2)
        + (x2 * x2 + y2 * y2) * (x1 - x3)
        + (x3 * x3 + y3 * y3) * (x2 - x1)
    ) / d
    r = math.hypot(x1 - ux, y1 - uy)
    if r < 1e-9:
        return None
    return (ux, uy), r


def _circle_fit_error(
    pts: Sequence[tuple[float, float]],
    center: tuple[float, float],
    radius: float,
) -> float:
    cx, cy = center
    return max(abs(math.hypot(x - cx, y - cy) - radius) for x, y in pts)


def _chord(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _densify_arc_short(
    p_start: tuple[float, float],
    p_end: tuple[float, float],
    center: tuple[float, float],
    radius: float,
    sagitta_mm: float,
    ccw: bool,
) -> list[tuple[float, float]]:
    chord = _chord(p_start, p_end)
    if chord < 1e-6:
        return []
    # Radio enorme ≈ arista recta; no inventar arcos.
    if radius > max(chord * 8.0, 500.0):
        return []

    cx, cy = center
    a1 = math.atan2(p_start[1] - cy, p_start[0] - cx)
    a2 = math.atan2(p_end[1] - cy, p_end[0] - cx)
    da = a2 - a1
    while da <= -math.pi:
        da += 2.0 * math.pi
    while da > math.pi:
        da -= 2.0 * math.pi
    if not ccw and da > 0:
        da -= 2.0 * math.pi
    if ccw and da < 0:
        da += 2.0 * math.pi

    # Nunca un arco > 90° por arista (evita "anillos" falsos)
    if abs(da) > math.radians(90.0):
        return []

    arc_len = abs(da) * radius
    step = max(0.12, math.sqrt(max(8.0 * radius * sagitta_mm, 1e-9)))
    n = min(64, max(1, int(math.ceil(arc_len / step))))
    out: list[tuple[float, float]] = []
    for i in range(1, n):
        t = i / n
        a = a1 + da * t
        out.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return out


def refine_ring(ring: Iterable, sagitta_mm: float = DISPLAY_CURVE_TOL_MM) -> list[tuple[float, float]]:
    """Devuelve anillo densificado solo si ARGA_DISPLAY_CURVE_REFINE=1."""
    pts = _ring_points(ring)
    if not _ENABLED or len(pts) < 4:
        return pts

    tol = max(0.02, float(sagitta_mm))
    out: list[tuple[float, float]] = [pts[0]]

    for i in range(len(pts)):
        p0 = pts[i]
        p1 = pts[(i + 1) % len(pts)]
        p2 = pts[(i + 2) % len(pts)]
        chord = _chord(p0, p1)
        if chord < 0.5:
            continue

        fit = _fit_circle_3p(p0, p1, p2)
        if fit is None:
            out.append(p1)
            continue
        center, radius = fit
        if radius < 0.2 or _circle_fit_error((p0, p1, p2), center, radius) > tol * 2.0:
            out.append(p1)
            continue

        # Solo arista curva real (no esquina de rectángulo)
        sagitta = radius - math.sqrt(max(radius * radius - (chord * 0.5) ** 2, 0.0))
        if sagitta < tol * 0.5:
            out.append(p1)
            continue

        v1x, v1y = p1[0] - p0[0], p1[1] - p0[1]
        v2x, v2y = p2[0] - p1[0], p2[1] - p1[1]
        cross = v1x * v2y - v1y * v2x
        ccw = cross > 0
        dense = _densify_arc_short(p0, p1, center, radius, tol, ccw)
        if dense:
            out.extend(dense)
        if not out or out[-1] != p1:
            out.append(p1)

    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out if len(out) >= 3 else pts
