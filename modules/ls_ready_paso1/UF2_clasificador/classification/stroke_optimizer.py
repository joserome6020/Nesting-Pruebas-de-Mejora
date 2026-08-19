# -*- coding: utf-8 -*-
"""Optimización de strokes para LS READY V3.

Reglas definidas para Generación LS desde JSON:
- Texto: láser continuo para todo el bloque de texto. Se conserva la lista de
  strokes originales, pero también se entrega una trayectoria continua
  `continuous_points_dxf` para que el generador LS haga un solo ON/OFF.
- Figuras: no usar láser continuo global. Solo unir minisegmentos que comparten
  endpoint real dentro de tolerancia, para formar strokes geométricos reales sin
  inventar líneas de unión.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .geometry import bbox_center, bbox_of_points, dist2d, polyline_length

Point = Sequence[float]
Stroke = Dict[str, Any]


def _tol(cfg, attr: str, default: float) -> float:
    try:
        return float(getattr(cfg, attr, default))
    except Exception:
        return float(default)


def _round_point(pt: Point) -> List[float]:
    return [round(float(pt[0]), 4), round(float(pt[1]), 4)]


def _point_key(pt: Point, tol: float) -> Tuple[int, int]:
    tol = max(float(tol), 1e-9)
    return (int(round(float(pt[0]) / tol)), int(round(float(pt[1]) / tol)))


def _stroke_points(stroke: Stroke) -> List[List[float]]:
    pts = stroke.get("points_dxf") or stroke.get("points") or []
    out: List[List[float]] = []
    for p in pts:
        if p is None or len(p) < 2:
            continue
        out.append([float(p[0]), float(p[1])])
    return out


def _copy_stroke_with_points(template: Stroke, points: List[Point], *, source_indices: Optional[List[int]] = None, connected_count: int = 1) -> Stroke:
    pts = [_round_point(p) for p in points]
    bb = bbox_of_points([(p[0], p[1]) for p in pts])
    c = bbox_center(bb) if bb else (0.0, 0.0)
    out = deepcopy(template or {})
    out["points_dxf"] = pts
    out["points"] = pts
    out["closed"] = bool(len(pts) > 2 and dist2d(pts[0], pts[-1]) <= 0.01)
    out["length_mm"] = round(polyline_length([(p[0], p[1]) for p in pts]), 4)
    if bb:
        out["bbox_dxf"] = [round(float(bb[0]), 4), round(float(bb[1]), 4), round(float(bb[2]), 4), round(float(bb[3]), 4)]
    out["center_dxf"] = [round(float(c[0]), 4), round(float(c[1]), 4)]
    if source_indices is not None:
        out["source_indices"] = [int(x) for x in source_indices]
        out["source_index"] = int(source_indices[0]) if source_indices else int(out.get("source_index", -1) or -1)
    out["connected_source_count"] = int(connected_count)
    return out


def flatten_text_strokes_continuous(text_strokes: Iterable[Stroke], cfg=None) -> List[List[float]]:
    """Devuelve una trayectoria continua para un bloque de texto.

    No inserta puntos seguros ni apaga láser entre strokes. Esa es la intención
    para texto: un solo ON/OFF por bloque, aunque existan pequeños trazos de
    unión entre strokes.
    """
    points: List[List[float]] = []
    drop_duplicate_tol = _tol(cfg, "TEXT_CONTINUOUS_DROP_DUPLICATE_TOL_MM", 0.01)
    for stroke in text_strokes or []:
        pts = _stroke_points(stroke)
        for pt in pts:
            if points and dist2d(points[-1], pt) <= drop_duplicate_tol:
                continue
            points.append(_round_point(pt))
    return points


def connect_strokes_by_shared_endpoints(strokes: Iterable[Stroke], cfg=None, *, logs=None, label: str = "") -> List[Stroke]:
    """Une strokes que comparten endpoint real.

    Es conservador: solo conecta cuando start/end coinciden dentro de tolerancia.
    No une por cercanía general ni por bbox, por lo que evita crear líneas falsas.
    """
    tol = _tol(cfg, "FIGURE_STROKE_CONNECT_TOL_MM", 0.05)
    remaining: List[Stroke] = []
    for s in strokes or []:
        pts = _stroke_points(s)
        if len(pts) >= 2:
            copied = deepcopy(s)
            copied["points_dxf"] = [_round_point(p) for p in pts]
            copied["points"] = [_round_point(p) for p in pts]
            remaining.append(copied)

    connected: List[Stroke] = []
    while remaining:
        current = remaining.pop(0)
        current_pts = _stroke_points(current)
        source_indices = []
        if current.get("source_indices"):
            source_indices.extend(int(x) for x in current.get("source_indices") or [])
        elif current.get("source_index") is not None:
            source_indices.append(int(current.get("source_index")))
        connected_count = int(current.get("connected_source_count") or 1)

        changed = True
        while changed and remaining:
            changed = False
            start_key = _point_key(current_pts[0], tol)
            end_key = _point_key(current_pts[-1], tol)

            best_idx = None
            best_mode = None
            for idx, candidate in enumerate(remaining):
                pts = _stroke_points(candidate)
                if len(pts) < 2:
                    continue
                c_start = _point_key(pts[0], tol)
                c_end = _point_key(pts[-1], tol)
                if end_key == c_start:
                    best_idx, best_mode = idx, "append"
                    break
                if end_key == c_end:
                    best_idx, best_mode = idx, "append_reversed"
                    break
                if start_key == c_end:
                    best_idx, best_mode = idx, "prepend"
                    break
                if start_key == c_start:
                    best_idx, best_mode = idx, "prepend_reversed"
                    break

            if best_idx is None:
                continue

            candidate = remaining.pop(best_idx)
            cand_pts = _stroke_points(candidate)
            if best_mode == "append":
                current_pts = current_pts + cand_pts[1:]
            elif best_mode == "append_reversed":
                rev = list(reversed(cand_pts))
                current_pts = current_pts + rev[1:]
            elif best_mode == "prepend":
                current_pts = cand_pts[:-1] + current_pts
            elif best_mode == "prepend_reversed":
                rev = list(reversed(cand_pts))
                current_pts = rev[:-1] + current_pts

            if candidate.get("source_indices"):
                source_indices.extend(int(x) for x in candidate.get("source_indices") or [])
            elif candidate.get("source_index") is not None:
                source_indices.append(int(candidate.get("source_index")))
            connected_count += int(candidate.get("connected_source_count") or 1)
            changed = True

        connected.append(_copy_stroke_with_points(current, current_pts, source_indices=source_indices, connected_count=connected_count))

    if logs is not None:
        logs.append(
            "{0}figure_stroke_connect in={1} out={2} tol={3}".format(
                (label + " ") if label else "",
                len(list(strokes or [])) if not isinstance(strokes, list) else len(strokes),
                len(connected),
                tol,
            )
        )
    return connected
