# -*- coding: utf-8 -*-
"""
Ciclo por pieza: local_plan.mark + local_plan.cut.

MARK (der->izq dentro de la pieza):
  figuras con X mayor primero, bloque texto en su posicion X, resto de figuras.
CUT:
  barrenos -> inner -> contorno.
"""
from .figure_group import group_figure_strokes, merge_composite_figure_groups
from .figure_components import (
    build_mark_endpoint_components,
    flatten_component_strokes,
    filter_components_overlapping_text,
    merge_nearby_figure_components,
    reconstruct_figure_component_strokes,
    split_text_and_figure_components,
)
from .holes import group_holes_by_anchor_window
from .mark_split import split_text_and_figures
from .naming import grab_figure_name, grab_text_name, hole_group_name, inner_name, outer_name
from .ordering import sort_figure_strokes_der_to_izq
from .stroke_optimizer import connect_strokes_by_shared_endpoints, flatten_text_strokes_continuous
from .text_block import (
    compute_text_start_point,
    detect_text_orientation,
    focus_text_cluster,
    order_text_strokes,
    text_cluster_center_x,
)


def build_piece_mark_plan(piece_id, mark_strokes, cfg, logs=None):
    """Construye la fase MARK de una pieza.

    v3.3 reconstruye figuras por componentes sin romper la detección de texto:
    1) Primero separa texto/figura con la regla existente.
    2) Enfoca el bloque real de texto y devuelve sus outliers a figuras.
    3) Reconstruye figuras desde las líneas restantes por endpoints compartidos.

    Así se cubren ambos escenarios:
    - Figuras DXF cerradas: quedan como un componente.
    - Figuras DXF abiertas/explotadas: se reconstruyen por puntos compartidos.
    """
    use_components = bool(getattr(cfg, "FIGURE_COMPONENT_RECONSTRUCT", True))

    # La separación inicial se mantiene porque es la que mejor identifica el
    # bloque de texto cuando el lector DXF elimina micro-líneas o el texto queda
    # fragmentado en varias componentes.
    legacy_text_strokes, legacy_figure_strokes = split_text_and_figures(
        mark_strokes or [], cfg, logs=logs
    )

    text_strokes = list(legacy_text_strokes or [])
    figure_candidates = list(legacy_figure_strokes or [])

    if text_strokes:
        cell_mm = float(getattr(cfg, "TEXT_CLUSTER_CELL_MM", 80.0))
        min_frac = float(getattr(cfg, "TEXT_CLUSTER_MIN_FRACTION", 0.45))
        focused_text = focus_text_cluster(
            text_strokes,
            cell_mm=cell_mm,
            min_fraction=min_frac,
        )
        if len(focused_text) < len(text_strokes):
            focused_ids = {id(stroke) for stroke in focused_text}
            outliers = [s for s in text_strokes if id(s) not in focused_ids]
            figure_candidates.extend(outliers)
            if logs is not None:
                logs.append(
                    "piece {0} text_focus kept={1} moved_to_figures={2}".format(
                        piece_id, len(focused_text), len(outliers)
                    )
                )
            text_strokes = focused_text

    # Guardia anti-falso-texto: el texto real es compacto. Si el bloque "texto"
    # abarca una dimensión larga mayor al límite de texto real (p. ej. tapas
    # cortas de rectángulos/escuadras de MARK repartidas por toda la pieza), no
    # es texto: se manda a figuras (follow_y). Evita mark_text con E1 fijo sobre
    # un recorrido enorme en Y_UF2, que provoca "posición inalcanzable/límite".
    if text_strokes:
        max_text_long_dim = float(getattr(cfg, "TEXT_COMPONENT_MAX_LONG_DIM_MM", 700.0))
        xs0 = []
        ys0 = []
        for s in text_strokes:
            bb = s.get("bbox_dxf") or []
            if len(bb) >= 4:
                xs0.extend([float(bb[0]), float(bb[2])])
                ys0.extend([float(bb[1]), float(bb[3])])
        if xs0 and ys0:
            block_long_dim = max(max(xs0) - min(xs0), max(ys0) - min(ys0))
            if block_long_dim > max_text_long_dim + 1e-6:
                figure_candidates.extend(text_strokes)
                if logs is not None:
                    logs.append(
                        "piece {0} text_block_not_text long_dim={1:.1f} > limit={2:.1f} "
                        "moved_to_figures={3}".format(
                            piece_id, block_long_dim, max_text_long_dim, len(text_strokes)
                        )
                    )
                text_strokes = []

    if use_components:
        components = build_mark_endpoint_components(
            figure_candidates,
            cfg,
            logs=logs,
            piece_label="piece {0}".format(piece_id),
        )
        # Se llama al split de componentes solo para diagnóstico; aquí ya no se
        # usa para texto porque el texto se resolvió arriba por cluster.
        _, figure_components = split_text_and_figure_components(
            components,
            cfg,
            logs=logs,
            piece_label="piece {0}".format(piece_id),
        )
        figure_components = filter_components_overlapping_text(
            figure_components,
            text_strokes,
            cfg,
            logs=logs,
            piece_label="piece {0}".format(piece_id),
        )
        figure_components = merge_nearby_figure_components(
            figure_components,
            cfg,
            logs=logs,
            piece_label="piece {0}".format(piece_id),
        )
        figure_strokes = reconstruct_figure_component_strokes(
            figure_components,
            cfg,
            logs=logs,
            piece_label="piece {0}".format(piece_id),
        )
    else:
        figure_strokes = figure_candidates

    steps = []
    if not text_strokes and not figure_strokes:
        return steps, {}

    text_meta = {}
    if text_strokes:
        ratio = float(getattr(cfg, "TEXT_ORIENTATION_RATIO", 1.2))
        p_lo = float(getattr(cfg, "TEXT_ORIENTATION_PERCENTILE_LO", 0.10))
        p_hi = float(getattr(cfg, "TEXT_ORIENTATION_PERCENTILE_HI", 0.90))
        cell_mm = float(getattr(cfg, "TEXT_CLUSTER_CELL_MM", 80.0))
        min_frac = float(getattr(cfg, "TEXT_CLUSTER_MIN_FRACTION", 0.45))
        orientation, start_rule = detect_text_orientation(
            text_strokes,
            ratio_threshold=ratio,
            percentile_lo=p_lo,
            percentile_hi=p_hi,
            cluster_cell_mm=cell_mm,
            cluster_min_fraction=min_frac,
        )
        ordered_text = order_text_strokes(text_strokes, orientation)
        continuous_points = flatten_text_strokes_continuous(ordered_text, cfg)
        start_point = compute_text_start_point(ordered_text, orientation)
        if orientation == "vertical":
            stroke_order = "descending_y"
        else:
            stroke_order = "ascending_x"
        text_meta = {
            "orientation": orientation,
            "start_rule": start_rule,
            "start_point_dxf": start_point,
            "stroke_order": stroke_order,
            "stroke_count": len(ordered_text),
            "continuous_point_count": len(continuous_points),
            "laser_mode": getattr(cfg, "TEXT_LASER_MODE", "continuous"),
        }
        text_step = {
            "op": "mark_text",
            "path_name": grab_text_name(piece_id, 1),
            "strokes": ordered_text,
            "continuous_points_dxf": continuous_points,
            "laser_mode": getattr(cfg, "TEXT_LASER_MODE", "continuous"),
            "orientation": orientation,
            "start_rule": start_rule,
            "start_point_dxf": start_point,
            "stroke_order": stroke_order,
            "_sort_x": text_cluster_center_x(ordered_text),
        }
    else:
        text_step = None

    figure_items = []
    if figure_strokes:
        if use_components:
            figures_sorted = sort_figure_strokes_der_to_izq(figure_strokes)
            if bool(getattr(cfg, "FIGURE_ONE_STEP_PER_COMPONENT", True)):
                # Un path independiente por figura reconstruida (no se unen).
                for fig_idx, fig in enumerate(figures_sorted, start=1):
                    sort_x = float((fig.get("bbox_dxf") or [0, 0, 0, 0])[2])
                    figure_items.append(
                        {
                            "op": "mark_figure",
                            "path_name": grab_figure_name(piece_id, fig_idx),
                            "strokes": [fig],
                            "laser_mode": getattr(cfg, "FIGURE_LASER_MODE", "stroke"),
                            "original_stroke_count": int(fig.get("component_source_count") or fig.get("connected_source_count") or 1),
                            "connected_stroke_count": 1,
                            "figure_component_count": 1,
                            "reconstruction_mode": "endpoint_components",
                            "_sort_x": sort_x,
                        }
                    )
            else:
                sort_x = max(float((s.get("bbox_dxf") or [0, 0, 0, 0])[2]) for s in figures_sorted)
                figure_items.append(
                    {
                        "op": "mark_figure",
                        "path_name": grab_figure_name(piece_id, 1),
                        "strokes": figures_sorted,
                        "laser_mode": getattr(cfg, "FIGURE_LASER_MODE", "stroke"),
                        "original_stroke_count": sum(int(s.get("component_source_count") or s.get("connected_source_count") or 1) for s in figures_sorted),
                        "connected_stroke_count": len(figures_sorted),
                        "figure_component_count": len(figures_sorted),
                        "reconstruction_mode": "endpoint_components",
                        "_sort_x": sort_x,
                    }
                )
        else:
            figures_sorted = sort_figure_strokes_der_to_izq(figure_strokes)
            figure_groups = group_figure_strokes(
                figures_sorted,
                cfg,
                logs=logs,
                piece_label="piece {0}".format(piece_id),
            )
            figure_groups = merge_composite_figure_groups(
                figure_groups,
                cfg,
                logs=logs,
                piece_label="piece {0}".format(piece_id),
            )
            fig_idx = 0
            for group in figure_groups:
                connected_strokes = connect_strokes_by_shared_endpoints(
                    list(group["strokes"]),
                    cfg,
                    logs=logs,
                    label="piece {0} {1}".format(piece_id, grab_figure_name(piece_id, fig_idx + 1)),
                )
                fig_idx += 1
                figure_items.append(
                    {
                        "op": "mark_figure",
                        "path_name": grab_figure_name(piece_id, fig_idx),
                        "strokes": connected_strokes,
                        "laser_mode": getattr(cfg, "FIGURE_LASER_MODE", "stroke"),
                        "original_stroke_count": len(list(group["strokes"])),
                        "connected_stroke_count": len(connected_strokes),
                        "_sort_x": float(group["sort_value"]),
                    }
                )

    if text_step is None:
        ordered_items = figure_items
    elif not figure_items:
        ordered_items = [text_step]
    else:
        text_x = float(text_step["_sort_x"])
        right_figs = [f for f in figure_items if float(f["_sort_x"]) >= text_x]
        left_figs = [f for f in figure_items if float(f["_sort_x"]) < text_x]
        ordered_items = right_figs + [text_step] + left_figs
        if logs is not None:
            logs.append(
                "piece {0} mark layout: fig_r={1} text=1 fig_l={2}".format(
                    piece_id, len(right_figs), len(left_figs)
                )
            )

    for item in ordered_items:
        step = dict(item)
        step.pop("_sort_x", None)
        steps.append(step)

    return steps, text_meta

def build_piece_cut_plan(piece_id, hole_candidates, inner_contours, cut_outer, cfg):
    grouping_mode = str(getattr(cfg, "HOLE_GROUPING_MODE", "grouped") or "grouped").lower()
    if grouping_mode == "individual":
        # 240x96: no se comparte E1 entre barrenos. Cada barreno se convierte
        # en un step independiente y conserva su propio E1_fixed.
        ordered_holes = sorted(
            list(hole_candidates or []),
            key=lambda h: (
                float((h.get("center_dxf") or [0.0, 0.0])[0]),
                float((h.get("center_dxf") or [0.0, 0.0])[1]),
                int(h.get("source_index", -1)),
            ),
        )
        groups = [[hole] for hole in ordered_holes]
    else:
        groups = group_holes_by_anchor_window(
            hole_candidates or [],
            cfg.HOLE_GROUP_MAX_DX_MM,
            cfg.HOLE_GROUP_MAX_DY_MM,
        )

    hole_groups = []
    for gidx, group in enumerate(groups, start=1):
        anchor = group[0]["center_dxf"]
        hole_groups.append(
            {
                "name": hole_group_name(piece_id, gidx),
                "group_index": gidx,
                "grouping_mode": grouping_mode,
                "anchor_dxf": [round(anchor[0], 4), round(anchor[1], 4)],
                "holes": group,
            }
        )

    cut_inner = None
    if inner_contours:
        cut_inner = {
            "name": inner_name(piece_id),
            "contours": inner_contours,
        }

    steps = []
    for group in hole_groups:
        steps.append(
            {
                "op": "holes",
                "path_name": group["name"],
                "group_index": int(group["group_index"]),
                "grouping_mode": grouping_mode,
                "holes": group["holes"],
            }
        )

    if cut_inner and cut_inner.get("contours"):
        steps.append(
            {
                "op": "cut_inner",
                "path_name": cut_inner["name"],
                "contours": cut_inner["contours"],
            }
        )

    if cut_outer:
        steps.append(
            {
                "op": "cut_outer",
                "path_name": outer_name(piece_id),
                "contour_source_index": int(cut_outer.get("source_index", -1)),
                "contour": cut_outer.get("contour") or {},
            }
        )

    return {
        "hole_groups": hole_groups,
        "cut_inner": cut_inner,
        "cut_steps": steps,
    }


def build_piece_plan(piece, mark_strokes, cfg, logs=None):
    """Entrada: dict pieza base + trazos mark asignados. Salida: pieza con local_plan."""
    piece_id = piece["id"]
    mark_steps, text_meta = build_piece_mark_plan(
        piece_id, mark_strokes, cfg, logs=logs
    )

    hole_candidates = list(piece.get("hole_candidates") or [])
    inner_contours = list(piece.get("inner_contours") or [])
    cut_result = build_piece_cut_plan(
        piece_id,
        hole_candidates,
        inner_contours,
        piece.get("cut_outer"),
        cfg,
    )

    local_plan = {
        "mark": _slim_local_mark_steps(mark_steps),
        "cut": _slim_local_cut_steps(cut_result["cut_steps"], cfg),
    }

    out = dict(piece)
    out.pop("hole_candidates", None)
    out.pop("inner_contours", None)
    out["hole_groups"] = cut_result["hole_groups"]
    out["cut_inner"] = cut_result["cut_inner"]
    out["mark_text"] = text_meta if text_meta else None
    out["local_plan"] = local_plan
    out["flags"] = {
        "has_mark_text": bool(text_meta),
        "has_mark_figures": any(s["op"] == "mark_figure" for s in mark_steps),
        "has_holes": bool(cut_result["hole_groups"]),
        "has_cut_inner": bool(cut_result["cut_inner"]),
        "has_cut_outer": bool(piece.get("cut_outer")),
    }
    return out


def _round_point(pt):
    return [round(float(pt[0]), 4), round(float(pt[1]), 4)]


def _round_points(points):
    return [_round_point(pt) for pt in (points or [])]


def _safe_int(value, default=-1):
    try:
        return int(value)
    except Exception:
        return int(default)


def _copy_if_present(src, dst, keys):
    for key in keys:
        if key in src and src.get(key) is not None:
            dst[key] = src.get(key)



def _flatten_stroke_source_indices(strokes):
    out = []
    seen = set()
    for stroke in strokes or []:
        values = stroke.get("source_indices") if isinstance(stroke, dict) else None
        if values:
            candidates = values
        else:
            candidates = [stroke.get("source_index")] if isinstance(stroke, dict) else []
        for value in candidates or []:
            idx = _safe_int(value)
            if idx == -1:
                continue
            if idx not in seen:
                seen.add(idx)
                out.append(idx)
    return out

def _serialize_geometry_entity(entity):
    """
    Copia geometria real en coordenadas DXF/globales de placa.

    Esto deja el plan listo para LS: el generador ya no necesita buscar
    indices dentro de legacy para reconstruir puntos.
    """
    entity = entity or {}
    out = {
        "source_index": _safe_int(entity.get("source_index")),
        "points_dxf": _round_points(entity.get("points") or []),
        "closed": bool(entity.get("closed", False)),
    }
    _copy_if_present(
        entity,
        out,
        (
            "etype",
            "bbox_dxf",
            "center_dxf",
            "length_mm",
            "assignment_method",
            "inner_kind",
            "circularity",
            "aspect",
            "source_indices",
            "connected_source_count",
            "component_source_count",
            "figure_component_index",
            "component_kind",
            "traversal_method",
            "may_retrace_existing_edges",
            "odd_endpoint_count",
            "endpoint_tol_mm",
            "native_segments",
            "native_segment_count",
            "native_geometry_preserved",
            "source_etypes",
            "slot_detection",
        ),
    )
    return out


def _cut_entry_policy(cfg):
    return {
        "strategy": "cut_in",
        "requires_cut_in": True,
        "cut_in_mm": float(getattr(cfg, "CUT_IN_MM", 3.0)),
        "preferred_first_move": "bottom_to_top",
        "coordinate_stage": "dxf_global_before_uf_transform",
    }


def _mark_entry_policy(step):
    entry = {
        "strategy": "direct_on_path",
        "requires_cut_in": False,
        "coordinate_stage": "dxf_global_before_uf_transform",
    }
    if step.get("start_point_dxf") is not None:
        entry["start_point_dxf"] = step.get("start_point_dxf")
    if step.get("start_rule") is not None:
        entry["start_rule"] = step.get("start_rule")
    if step.get("stroke_order") is not None:
        entry["stroke_order"] = step.get("stroke_order")
    return entry


def _slim_local_mark_steps(steps):
    slim = []
    for step in steps or []:
        strokes = list(step.get("strokes") or [])
        item = {
            "op": step["op"],
            "path_name": step["path_name"],
            "laser_mode": step.get("laser_mode") or "engrave",
            "stroke_source_indices": _flatten_stroke_source_indices(strokes),
            "geometry": {
                "type": "text_continuous" if step["op"] == "mark_text" and (step.get("laser_mode") == "continuous") else "strokes",
                "strokes": [_serialize_geometry_entity(s) for s in strokes],
            },
            "entry": _mark_entry_policy(step),
        }
        if step["op"] == "mark_text" and step.get("continuous_points_dxf"):
            item["geometry"]["continuous_points_dxf"] = _round_points(step.get("continuous_points_dxf") or [])
            item["geometry"]["continuous_point_count"] = len(item["geometry"]["continuous_points_dxf"])
        if step["op"] == "mark_text":
            for key in (
                "orientation",
                "start_rule",
                "start_point_dxf",
                "stroke_order",
                "continuous_points_dxf",
            ):
                if key in step:
                    item[key] = step[key]
        elif step["op"] == "mark_figure":
            item["stroke_count"] = len(strokes)
            item["source_segment_count"] = len(item["stroke_source_indices"])
            item["original_stroke_count"] = int(step.get("original_stroke_count") or item["source_segment_count"] or item["stroke_count"])
            item["connected_stroke_count"] = int(step.get("connected_stroke_count") or item["stroke_count"])
            for key in ("figure_component_count", "reconstruction_mode"):
                if key in step:
                    item[key] = step[key]
        slim.append(item)
    return slim


def _slim_local_cut_steps(steps, cfg):
    slim = []
    for step in steps or []:
        item = {
            "op": step["op"],
            "path_name": step["path_name"],
            "laser_mode": "cut",
            "entry": _cut_entry_policy(cfg),
        }
        if step["op"] == "holes":
            holes = list(step.get("holes") or [])
            item["group_index"] = int(step.get("group_index") or 1)
            item["hole_source_indices"] = [
                _safe_int(h.get("source_index")) for h in holes
            ]
            item["geometry"] = {
                "type": "closed_contours",
                "contours": [_serialize_geometry_entity(h) for h in holes],
            }
        elif step["op"] == "cut_inner":
            contours = list(step.get("contours") or [])
            item["contour_source_indices"] = [
                _safe_int(c.get("source_index")) for c in contours
            ]
            item["geometry"] = {
                "type": "closed_contours",
                "contours": [_serialize_geometry_entity(c) for c in contours],
            }
        elif step["op"] == "cut_outer":
            contour = step.get("contour") or {}
            item["contour_source_index"] = int(step.get("contour_source_index", -1))
            item["geometry"] = {
                "type": "closed_contour",
                "contour": _serialize_geometry_entity(contour),
                "must_close": True,
            }
        slim.append(item)
    return slim
