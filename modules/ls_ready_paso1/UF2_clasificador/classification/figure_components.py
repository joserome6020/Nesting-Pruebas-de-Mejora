# -*- coding: utf-8 -*-
"""Reconstrucción de figuras de marcaje desde entidades DXF abiertas.

Regla v3.3:
- Si el DXF ya trae una figura cerrada, se conserva como componente.
- Si el DXF trae la figura explotada en líneas abiertas, se reconstruye por
  conectividad de endpoints dentro de tolerancia.
- Cada componente de figura se entrega como UN stroke continuo para que el
  generador LS haga un solo ON/OFF por figura reconstruida, no por segmento.

No se une por cercanía general: la pertenencia a componente exige compartir
endpoint real. Cuando un componente conectado no tiene camino euleriano único
(por tener más de 2 nodos impares), se genera una ruta continua que puede
re-trazar aristas existentes para evitar crear líneas falsas entre partes no
conectadas.
"""
from __future__ import annotations

import math
from collections import defaultdict
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .geometry import bbox_center, bbox_of_points, dist2d, polyline_length

Point = Sequence[float]
Stroke = Dict[str, Any]
NodeKey = Tuple[int, int]


def _cfg_float(cfg: Any, name: str, default: float) -> float:
    try:
        return float(getattr(cfg, name, default))
    except Exception:
        return float(default)


def _cfg_bool(cfg: Any, name: str, default: bool) -> bool:
    try:
        return bool(getattr(cfg, name, default))
    except Exception:
        return bool(default)


def _cfg_int(cfg: Any, name: str, default: int) -> int:
    try:
        return int(getattr(cfg, name, default))
    except Exception:
        return int(default)


def _round_point(pt: Point) -> List[float]:
    return [round(float(pt[0]), 4), round(float(pt[1]), 4)]


def _point_key(pt: Point, tol: float) -> NodeKey:
    tol = max(float(tol), 1e-9)
    return (int(round(float(pt[0]) / tol)), int(round(float(pt[1]) / tol)))


def _stroke_points(stroke: Stroke) -> List[List[float]]:
    pts = stroke.get("points_dxf") or stroke.get("points") or []
    out: List[List[float]] = []
    for pt in pts:
        if pt is None or len(pt) < 2:
            continue
        out.append([float(pt[0]), float(pt[1])])
    return out


def _bbox_from_strokes(strokes: Iterable[Stroke]) -> List[float]:
    pts: List[Tuple[float, float]] = []
    for stroke in strokes or []:
        for p in _stroke_points(stroke):
            pts.append((float(p[0]), float(p[1])))
    if not pts:
        return [0.0, 0.0, 0.0, 0.0]
    bb = bbox_of_points(pts)
    return [float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])]


def _bbox_width(bb: Sequence[float]) -> float:
    return abs(float(bb[2]) - float(bb[0])) if bb and len(bb) >= 4 else 0.0


def _bbox_height(bb: Sequence[float]) -> float:
    return abs(float(bb[3]) - float(bb[1])) if bb and len(bb) >= 4 else 0.0


def _source_indices(strokes: Iterable[Stroke]) -> List[int]:
    out: List[int] = []
    for stroke in strokes or []:
        if stroke.get("source_indices"):
            for value in stroke.get("source_indices") or []:
                try:
                    out.append(int(value))
                except Exception:
                    pass
        elif stroke.get("source_index") is not None:
            try:
                out.append(int(stroke.get("source_index")))
            except Exception:
                pass
    # mantener orden, quitar duplicados
    seen = set()
    unique: List[int] = []
    for idx in out:
        if idx not in seen:
            seen.add(idx)
            unique.append(idx)
    return unique


def build_mark_endpoint_components(strokes: Iterable[Stroke], cfg: Any, logs=None, piece_label: str = "") -> List[Dict[str, Any]]:
    """Agrupa strokes por endpoints compartidos.

    Cada entidad se usa una sola vez; no existe unión por bbox ni por cercanía
    entre segmentos que no compartan endpoint.
    """
    tol = _cfg_float(cfg, "FIGURE_COMPONENT_ENDPOINT_TOL_MM", _cfg_float(cfg, "FIGURE_STROKE_CONNECT_TOL_MM", 0.05))
    valid: List[Stroke] = []
    for stroke in strokes or []:
        pts = _stroke_points(stroke)
        if len(pts) >= 2:
            copied = deepcopy(stroke)
            copied["points"] = [_round_point(p) for p in pts]
            copied["points_dxf"] = [_round_point(p) for p in pts]
            valid.append(copied)

    endpoint_to_indices: Dict[NodeKey, List[int]] = defaultdict(list)
    for idx, stroke in enumerate(valid):
        pts = _stroke_points(stroke)
        endpoint_to_indices[_point_key(pts[0], tol)].append(idx)
        endpoint_to_indices[_point_key(pts[-1], tol)].append(idx)

    visited = [False] * len(valid)
    components: List[Dict[str, Any]] = []
    for idx in range(len(valid)):
        if visited[idx]:
            continue
        stack = [idx]
        visited[idx] = True
        comp_indices: List[int] = []
        while stack:
            current = stack.pop()
            comp_indices.append(current)
            pts = _stroke_points(valid[current])
            for pt in (pts[0], pts[-1]):
                for nxt in endpoint_to_indices.get(_point_key(pt, tol), []):
                    if not visited[nxt]:
                        visited[nxt] = True
                        stack.append(nxt)
        comp_strokes = [valid[i] for i in comp_indices]
        bb = _bbox_from_strokes(comp_strokes)
        components.append(
            {
                "component_index": len(components) + 1,
                "strokes": comp_strokes,
                "bbox_dxf": [round(v, 4) for v in bb],
                "stroke_count": len(comp_strokes),
                "source_indices": _source_indices(comp_strokes),
                "endpoint_tol_mm": tol,
            }
        )

    components.sort(key=lambda c: (-float(c["bbox_dxf"][2]), float(c["bbox_dxf"][1]), -float(c["bbox_dxf"][3])))
    for idx, comp in enumerate(components, start=1):
        comp["component_index"] = idx

    if logs is not None:
        logs.append(
            "{0}mark_endpoint_components strokes={1} components={2} tol={3}".format(
                (piece_label + " ") if piece_label else "",
                len(valid),
                len(components),
                tol,
            )
        )
    return components


def split_text_and_figure_components(components: Iterable[Dict[str, Any]], cfg: Any, logs=None, piece_label: str = "") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Separa componentes de texto y de figura.

    Para el flujo actual, el texto vectorizado suele formar un componente con
    muchos strokes pequeños. Las figuras reales tienen pocos strokes, incluso
    cuando vienen explotadas en líneas.
    """
    text_min_strokes = _cfg_int(cfg, "TEXT_COMPONENT_MIN_STROKES", 80)
    text_max_thickness = _cfg_float(cfg, "TEXT_COMPONENT_MAX_THICKNESS_MM", 90.0)
    text_max_long_dim = _cfg_float(cfg, "TEXT_COMPONENT_MAX_LONG_DIM_MM", 700.0)

    text_components: List[Dict[str, Any]] = []
    figure_components: List[Dict[str, Any]] = []

    for comp in components or []:
        bb = comp.get("bbox_dxf") or _bbox_from_strokes(comp.get("strokes") or [])
        width = _bbox_width(bb)
        height = _bbox_height(bb)
        min_dim = min(width, height)
        max_dim = max(width, height)
        stroke_count = int(comp.get("stroke_count") or len(comp.get("strokes") or []))
        is_text = (
            stroke_count >= text_min_strokes
            and min_dim <= text_max_thickness
            and max_dim <= text_max_long_dim
        )
        comp["component_kind"] = "text" if is_text else "figure"
        comp["component_classification"] = {
            "method": "stroke_count_and_bbox",
            "stroke_count": stroke_count,
            "bbox_width": round(width, 4),
            "bbox_height": round(height, 4),
            "text_min_strokes": text_min_strokes,
            "text_max_thickness_mm": text_max_thickness,
            "text_max_long_dim_mm": text_max_long_dim,
        }
        if is_text:
            text_components.append(comp)
        else:
            figure_components.append(comp)

    if logs is not None:
        logs.append(
            "{0}component_split text_components={1} figure_components={2}".format(
                (piece_label + " ") if piece_label else "",
                len(text_components),
                len(figure_components),
            )
        )
    return text_components, figure_components


def flatten_component_strokes(components: Iterable[Dict[str, Any]]) -> List[Stroke]:
    out: List[Stroke] = []
    for comp in components or []:
        out.extend(list(comp.get("strokes") or []))
    return out


def _node_sort_value(node_key: NodeKey, node_coords: Dict[NodeKey, List[float]]):
    pt = node_coords.get(node_key) or [0.0, 0.0]
    # Entrada de figura cama B: preferir esquina inferior-derecha (X_max, Y_min).
    # Se usa con max(...): score = x - y  (mayor X y menor Y ganan).
    return (float(pt[0]) - float(pt[1]), float(pt[0]), -float(pt[1]))


def _build_graph_edges(strokes: Iterable[Stroke], tol: float):
    node_coords: Dict[NodeKey, List[float]] = {}
    edges: List[Tuple[NodeKey, NodeKey]] = []
    for stroke in strokes or []:
        pts = _stroke_points(stroke)
        if len(pts) < 2:
            continue
        for p in pts:
            k = _point_key(p, tol)
            node_coords.setdefault(k, _round_point(p))
        for a, b in zip(pts, pts[1:]):
            ka = _point_key(a, tol)
            kb = _point_key(b, tol)
            if ka == kb:
                continue
            edges.append((ka, kb))
    return node_coords, edges


def _choose_start_node(nodes: Iterable[NodeKey], node_coords: Dict[NodeKey, List[float]], odd_nodes: List[NodeKey] | None = None) -> NodeKey:
    candidates = odd_nodes if odd_nodes else list(nodes)
    if not candidates:
        raise ValueError("No nodes available")
    return max(candidates, key=lambda n: _node_sort_value(n, node_coords))


def _euler_node_path(node_coords: Dict[NodeKey, List[float]], edges: List[Tuple[NodeKey, NodeKey]]) -> List[NodeKey]:
    adjacency: Dict[NodeKey, List[Tuple[int, NodeKey]]] = defaultdict(list)
    degree: Dict[NodeKey, int] = defaultdict(int)
    for eid, (a, b) in enumerate(edges):
        adjacency[a].append((eid, b))
        adjacency[b].append((eid, a))
        degree[a] += 1
        degree[b] += 1
    odd = [n for n, d in degree.items() if d % 2 == 1]
    start = _choose_start_node(degree.keys(), node_coords, odd if len(odd) == 2 else None)

    used = set()
    stack = [start]
    path: List[NodeKey] = []
    while stack:
        u = stack[-1]
        while adjacency[u] and adjacency[u][-1][0] in used:
            adjacency[u].pop()
        if adjacency[u]:
            eid, v = adjacency[u].pop()
            if eid in used:
                continue
            used.add(eid)
            stack.append(v)
        else:
            path.append(stack.pop())
    path.reverse()
    return path


def _dfs_backtrack_node_path(node_coords: Dict[NodeKey, List[float]], edges: List[Tuple[NodeKey, NodeKey]]) -> List[NodeKey]:
    adjacency: Dict[NodeKey, List[Tuple[int, NodeKey]]] = defaultdict(list)
    for eid, (a, b) in enumerate(edges):
        adjacency[a].append((eid, b))
        adjacency[b].append((eid, a))
    for node in adjacency:
        adjacency[node].sort(key=lambda item: _node_sort_value(item[1], node_coords), reverse=True)
    start = _choose_start_node(adjacency.keys(), node_coords)
    used = set()
    route = [start]

    def dfs(u: NodeKey):
        for eid, v in list(adjacency[u]):
            if eid in used:
                continue
            used.add(eid)
            route.append(v)
            dfs(v)
            route.append(u)  # regresar por la misma arista: retrazo real, no línea falsa

    dfs(start)
    # Si el último punto repite innecesariamente, se deja; mantiene trayectoria continua y cerrada.
    return route


def _node_path_to_points(path: Iterable[NodeKey], node_coords: Dict[NodeKey, List[float]]) -> List[List[float]]:
    points: List[List[float]] = []
    for node in path or []:
        pt = node_coords.get(node)
        if not pt:
            continue
        if points and dist2d(points[-1], pt) <= 1e-9:
            continue
        points.append(_round_point(pt))
    return points


def component_to_continuous_stroke(component: Dict[str, Any], cfg: Any, component_number: int = 1) -> Stroke:
    """Convierte un componente de figura en un solo stroke continuo."""
    tol = _cfg_float(cfg, "FIGURE_COMPONENT_ENDPOINT_TOL_MM", _cfg_float(cfg, "FIGURE_STROKE_CONNECT_TOL_MM", 0.05))
    strokes = list(component.get("strokes") or [])
    source_indices = _source_indices(strokes)

    node_coords, edges = _build_graph_edges(strokes, tol)
    degree: Dict[NodeKey, int] = defaultdict(int)
    for a, b in edges:
        degree[a] += 1
        degree[b] += 1
    odd_count = sum(1 for d in degree.values() if d % 2 == 1)

    traversal_method = "direct"
    may_retrace = False
    if not edges:
        points: List[List[float]] = []
    elif odd_count in (0, 2):
        node_path = _euler_node_path(node_coords, edges)
        points = _node_path_to_points(node_path, node_coords)
        traversal_method = "euler_trail"
    else:
        node_path = _dfs_backtrack_node_path(node_coords, edges)
        points = _node_path_to_points(node_path, node_coords)
        traversal_method = "dfs_backtrack_existing_edges"
        may_retrace = True

    if len(points) < 2:
        # Respaldo conservador: usar puntos originales en orden de entrada.
        points = []
        for stroke in strokes:
            for pt in _stroke_points(stroke):
                if points and dist2d(points[-1], pt) <= 1e-9:
                    continue
                points.append(_round_point(pt))
        traversal_method = "fallback_original_order"

    bb = bbox_of_points([(p[0], p[1]) for p in points]) if points else _bbox_from_strokes(strokes)
    center = bbox_center(bb) if bb else (0.0, 0.0)
    closed = bool(len(points) > 2 and dist2d(points[0], points[-1]) <= tol)
    out: Stroke = {
        "source_index": int(source_indices[0]) if source_indices else -1,
        "source_indices": source_indices,
        "points": [_round_point(p) for p in points],
        "points_dxf": [_round_point(p) for p in points],
        "closed": closed,
        "etype": "RECONSTRUCTED_FIGURE_COMPONENT",
        "bbox_dxf": [round(float(bb[0]), 4), round(float(bb[1]), 4), round(float(bb[2]), 4), round(float(bb[3]), 4)] if bb else [0.0, 0.0, 0.0, 0.0],
        "center_dxf": [round(float(center[0]), 4), round(float(center[1]), 4)],
        "length_mm": round(polyline_length([(p[0], p[1]) for p in points]), 4),
        "connected_source_count": len(source_indices) if source_indices else len(strokes),
        "component_source_count": len(strokes),
        "figure_component_index": int(component_number),
        "component_kind": "figure",
        "traversal_method": traversal_method,
        "may_retrace_existing_edges": may_retrace,
        "odd_endpoint_count": int(odd_count),
        "endpoint_tol_mm": tol,
    }
    return out



def filter_components_overlapping_text(components: Iterable[Dict[str, Any]], text_strokes: Iterable[Stroke], cfg: Any, logs=None, piece_label: str = "") -> List[Dict[str, Any]]:
    """Quita componentes candidatos a figura que realmente pertenecen al texto.

    En algunos DXF el texto trae una línea vertical larga de unión que la regla
    inicial manda a figuras por longitud. Si esa línea cae dentro del bbox del
    texto enfocado, se devuelve al bloque de texto/queda fuera de figuras para
    no crear una figura falsa adicional.
    """
    comps = list(components or [])
    text_strokes = list(text_strokes or [])
    if not comps or not text_strokes:
        return comps

    text_bb = _bbox_from_strokes(text_strokes)
    pad = _cfg_float(cfg, "TEXT_COMPONENT_EXCLUSION_PAD_MM", 2.0)
    padded = [text_bb[0] - pad, text_bb[1] - pad, text_bb[2] + pad, text_bb[3] + pad]

    kept: List[Dict[str, Any]] = []
    removed = 0
    for comp in comps:
        bb = comp.get("bbox_dxf") or _bbox_from_strokes(comp.get("strokes") or [])
        inside = (
            float(bb[0]) >= padded[0]
            and float(bb[1]) >= padded[1]
            and float(bb[2]) <= padded[2]
            and float(bb[3]) <= padded[3]
        )
        if inside:
            removed += 1
            continue
        kept.append(comp)

    if logs is not None and removed:
        logs.append(
            "{0}figure_component_text_overlap_removed={1} pad={2}".format(
                (piece_label + " ") if piece_label else "",
                removed,
                pad,
            )
        )
    return kept


def _bbox_gap_and_overlap(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float, float]:
    if float(a[2]) < float(b[0]):
        gap_x = float(b[0]) - float(a[2])
    elif float(b[2]) < float(a[0]):
        gap_x = float(a[0]) - float(b[2])
    else:
        gap_x = 0.0
    if float(a[3]) < float(b[1]):
        gap_y = float(b[1]) - float(a[3])
    elif float(b[3]) < float(a[1]):
        gap_y = float(a[1]) - float(b[3])
    else:
        gap_y = 0.0
    overlap_x = max(0.0, min(float(a[2]), float(b[2])) - max(float(a[0]), float(b[0])))
    overlap_y = max(0.0, min(float(a[3]), float(b[3])) - max(float(a[1]), float(b[1])))
    return gap_x, gap_y, overlap_x, overlap_y


def merge_nearby_figure_components(components: Iterable[Dict[str, Any]], cfg: Any, logs=None, piece_label: str = "") -> List[Dict[str, Any]]:
    """Une componentes separados por micro-gaps.

    Esta regla existe para recuperar segmentos de figura que quedaron separados
    porque se filtraron líneas muy pequeñas del DXF. No es un bridge visual
    general: solo aplica cuando el gap es menor a ~1 mm y los bboxes se solapan
    claramente en el eje perpendicular.
    """
    max_gap = _cfg_float(cfg, "FIGURE_COMPONENT_MICRO_GAP_MERGE_MM", 1.0)
    min_overlap = _cfg_float(cfg, "FIGURE_COMPONENT_MICRO_GAP_MIN_OVERLAP_MM", 5.0)
    working = [deepcopy(c) for c in (components or [])]
    if len(working) <= 1 or max_gap <= 0:
        return working

    merged_count = 0
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(working):
            j = i + 1
            while j < len(working):
                a = working[i].get("bbox_dxf") or _bbox_from_strokes(working[i].get("strokes") or [])
                b = working[j].get("bbox_dxf") or _bbox_from_strokes(working[j].get("strokes") or [])
                gap_x, gap_y, ox, oy = _bbox_gap_and_overlap(a, b)
                should_merge = (
                    (gap_x <= max_gap and oy >= min_overlap)
                    or (gap_y <= max_gap and ox >= min_overlap)
                )
                if should_merge:
                    strokes = list(working[i].get("strokes") or []) + list(working[j].get("strokes") or [])
                    bb = _bbox_from_strokes(strokes)
                    src = _source_indices(strokes)
                    working[i] = {
                        **working[i],
                        "strokes": strokes,
                        "bbox_dxf": [round(v, 4) for v in bb],
                        "stroke_count": len(strokes),
                        "source_indices": src,
                        "merged_by_micro_gap": True,
                    }
                    working.pop(j)
                    merged_count += 1
                    changed = True
                    continue
                j += 1
            i += 1

    working.sort(key=lambda c: (-float((c.get("bbox_dxf") or [0, 0, 0, 0])[2]), float((c.get("bbox_dxf") or [0, 0, 0, 0])[1])))
    for idx, comp in enumerate(working, start=1):
        comp["component_index"] = idx

    if logs is not None and merged_count:
        logs.append(
            "{0}figure_component_micro_gap_merged={1} remaining={2} max_gap={3}".format(
                (piece_label + " ") if piece_label else "",
                merged_count,
                len(working),
                max_gap,
            )
        )
    return working

def reconstruct_figure_component_strokes(figure_components: Iterable[Dict[str, Any]], cfg: Any, logs=None, piece_label: str = "") -> List[Stroke]:
    """Reconstruye todos los subcomponentes realmente conectados sin perder geometría.

    `merge_nearby_figure_components()` puede agrupar componentes separados por un
    micro-gap para mantenerlos dentro del mismo bloque lógico de grabado. Ese
    agrupamiento NO crea una arista geométrica entre ellos. La implementación
    anterior iniciaba DFS en un solo subgrafo y descartaba silenciosamente los
    demás, aunque sus source_index siguieran apareciendo en el JSON.

    Antes de generar strokes continuos se vuelve a separar cada grupo lógico por
    conectividad real de endpoints. El generador conserva estos strokes dentro del
    mismo figure_group y apaga el láser durante el traslado entre ellos, evitando
    tanto pérdida de segmentos como líneas falsas.
    """
    logical_components = list(figure_components or [])
    actual_components: List[Dict[str, Any]] = []
    split_groups = 0

    for logical in logical_components:
        connected = build_mark_endpoint_components(
            logical.get("strokes") or [],
            cfg,
            logs=None,
            piece_label="",
        )
        if connected:
            if len(connected) > 1:
                split_groups += 1
            for sub in connected:
                sub["logical_component_index"] = logical.get("component_index")
                sub["preserved_after_micro_gap_group"] = len(connected) > 1
                actual_components.append(sub)
        else:
            actual_components.append(logical)

    # Orden der->izq por max_x, y luego de abajo/arriba según DXF para estabilidad.
    actual_components.sort(
        key=lambda c: (
            -float((c.get("bbox_dxf") or [0, 0, 0, 0])[2]),
            float((c.get("bbox_dxf") or [0, 0, 0, 0])[1]),
        )
    )

    out: List[Stroke] = []
    retrace_count = 0
    for idx, comp in enumerate(actual_components, start=1):
        stroke = component_to_continuous_stroke(comp, cfg, component_number=idx)
        out.append(stroke)
        if stroke.get("may_retrace_existing_edges"):
            retrace_count += 1

    if logs is not None:
        logs.append(
            "{0}figure_component_reconstruct logical_components={1} actual_components={2} "
            "split_groups={3} output_strokes={4} retrace_components={5}".format(
                (piece_label + " ") if piece_label else "",
                len(logical_components),
                len(actual_components),
                split_groups,
                len(out),
                retrace_count,
            )
        )
    return out
