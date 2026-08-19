# -*- coding: utf-8 -*-
"""Postproceso LS-ready v3.

Esta capa convierte el JSON clasificado en una fuente directa para el generador LS:
- Orden por columnas/filas.
- Entradas geométricas por operación.
- Geometría ya ordenada desde el punto de entrada.
- Preview UF-2, E1 y J2.
- Checkpoint de validación.

El generador LS no debe ordenar ni decidir geometría: solo leer estos valores,
convertir DXF/global -> UF-2 y escribir /MN + /POS.
"""
from __future__ import annotations

import copy
import json
import math
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .geometry import (
    bbox_of_points,
    clean_points,
    fit_circle_least_squares,
    point_in_polygon,
    polygon_area_abs,
    polygon_centroid,
)
from .optim_path_v4 import OptimPathResolverV4

Point = List[float]

E1_LIMITS_MM = {"min": -3670.0, "max": 3670.0}
FOLLOW_Y_E1_FORMULA = "E1_new = E1_previous - (Y_new - Y_previous)"
UF2_MARGIN_MM = 0.0
COLUMN_BAND_MM = 100.0
J2_WARNING_DEG = 55.0
J2_PHYSICAL_LIMIT_DEG = 65.0

# Tolerancias para convertir un barreno discretizado en dos arcos FANUC.
FANUC_ARC_MIN_POINTS = 8
FANUC_ARC_MIN_RADIUS_MM = 3.5
FANUC_ARC_MAX_RMS_ERROR_MM = 0.25
FANUC_ARC_MAX_RADIAL_ERROR_MM = 0.75
FANUC_ARC_MAX_ASPECT_ERROR = 0.02
FANUC_ARC_CUT_IN_MM = 3.0
# Parámetros de movimiento para barrenos circulares FANUC C+C.
# Para futuras pruebas, mantenerlos sincronizados con GeneratorConfig.
FANUC_HOLE_CUT_SPEED_MM_SEC = 35
FANUC_HOLE_ARC_TERMINATION = "CNT50"

# Perfil probado físicamente para barrenos pequeños. El límite es exclusivo:
# diámetro < 25 mm usa PTH; diámetro >= 25 mm conserva el perfil estándar.
FANUC_SMALL_HOLE_DIAMETER_LIMIT_MM = 25.0
FANUC_SMALL_HOLE_START_SPEED_MM_SEC = 10
FANUC_SMALL_HOLE_ARC1_SPEED_MM_SEC = 10
FANUC_SMALL_HOLE_ARC2_SPEED_MM_SEC = 7
FANUC_SMALL_HOLE_START_TERMINATION = "CNT40"
FANUC_SMALL_HOLE_ARC1_TERMINATION = "CNT60"
FANUC_SMALL_HOLE_ARC2_TERMINATION = "CNT40"
FANUC_SMALL_HOLE_USE_PTH = True
FANUC_HOLE_USE_COORD = False

FANUC_SLOT_ENDPOINT_TOL_MM = 0.6
FANUC_SLOT_RADIUS_TOL_MM = 0.15
FANUC_SLOT_SWEEP_TOL_DEG = 1.0
FANUC_SLOT_CUT_SPEED_MM_SEC = 35
FANUC_SLOT_TERMINATION = "CNT1"


def _hole_motion_profile_for_diameter(diameter_mm: float) -> Dict[str, Any]:
    is_small = round(float(diameter_mm), 4) < FANUC_SMALL_HOLE_DIAMETER_LIMIT_MM
    if is_small:
        return {
            "profile_id": "small_hole_under_25mm_pth",
            "diameter_class": "small",
            "line_to_start": {
                "speed_mm_sec": FANUC_SMALL_HOLE_START_SPEED_MM_SEC,
                "termination": FANUC_SMALL_HOLE_START_TERMINATION,
                "use_pth": FANUC_SMALL_HOLE_USE_PTH,
            },
            "arc_1": {
                "speed_mm_sec": FANUC_SMALL_HOLE_ARC1_SPEED_MM_SEC,
                "termination": FANUC_SMALL_HOLE_ARC1_TERMINATION,
                "use_pth": FANUC_SMALL_HOLE_USE_PTH,
            },
            "arc_2": {
                "speed_mm_sec": FANUC_SMALL_HOLE_ARC2_SPEED_MM_SEC,
                "termination": FANUC_SMALL_HOLE_ARC2_TERMINATION,
                "use_pth": FANUC_SMALL_HOLE_USE_PTH,
            },
        }
    return {
        "profile_id": "standard_hole_25mm_and_over",
        "diameter_class": "standard",
        "line_to_start": {
            "speed_mm_sec": FANUC_HOLE_CUT_SPEED_MM_SEC,
            "termination": "FINE",
            "use_pth": False,
        },
        "arc_1": {
            "speed_mm_sec": FANUC_HOLE_CUT_SPEED_MM_SEC,
            "termination": FANUC_HOLE_ARC_TERMINATION,
            "use_pth": False,
        },
        "arc_2": {
            "speed_mm_sec": FANUC_HOLE_CUT_SPEED_MM_SEC,
            "termination": "FINE",
            "use_pth": False,
        },
    }


def apply_ls_ready_v3(classified: Dict[str, Any], runtime_cfg=None, logs=None) -> Dict[str, Any]:
    """Devuelve una copia del JSON clasificado enriquecida para LS-ready v3."""
    logs = logs if logs is not None else []
    data = copy.deepcopy(classified or {})
    robot_programming = data.get("robot_programming") or {}
    motion_map = _load_motion_map(data)

    data["schema_version"] = "carlos_rpa.piece_plan.v3.uf2_b"
    data["coordinate_mapping_notes"] = _coordinate_mapping_notes()
    data["piece_order_policy"] = _piece_order_policy()
    data["operation_order_policy"] = _operation_order_policy()
    data["ls_generation_policy"] = _ls_generation_policy()
    data["motion_map"] = _slim_motion_map(motion_map)

    pieces = list(data.get("pieces") or [])
    piece_groups, piece_order_cut, piece_order_mark = _resolve_piece_groups_and_orders(pieces)
    piece_order_cut, nesting_cut = _apply_inner_nesting_cut_constraints(
        pieces, piece_order_cut, logs=logs
    )
    piece_groups["inner_nesting_cut_rule"] = nesting_cut
    data["piece_groups"] = piece_groups

    pieces_by_id = {str(p.get("id")): p for p in pieces}
    for order_mark, pid in enumerate(piece_order_mark, start=1):
        if pid in pieces_by_id:
            pieces_by_id[pid]["order_mark"] = order_mark
    for order_cut, pid in enumerate(piece_order_cut, start=1):
        if pid in pieces_by_id:
            pieces_by_id[pid]["order_cut"] = order_cut

    for piece in pieces:
        _enrich_piece_local_plan(piece, robot_programming, motion_map)

    plan = _stitch_plan_from_pieces(pieces_by_id, piece_order_mark, piece_order_cut)
    data["plan"] = plan
    data["track_flow"] = {
        "mark_direction": "right_to_left",
        "cut_direction": "left_to_right",
        "piece_order_mark": piece_order_mark,
        "piece_order_cut": piece_order_cut,
        "inner_nesting_cut_rule": nesting_cut,
    }
    data["summary"] = _build_summary(pieces, plan)
    data["pieces"] = [pieces_by_id[pid] for pid in piece_order_mark if pid in pieces_by_id]
    data["validation"] = _validate_ls_ready_v3(data)
    if logs is not None:
        logs.append(
            "ls_ready_v3: pieces={0} mark_steps={1} cut_steps={2} validation={3}".format(
                len(pieces), len(plan.get("mark") or []), len(plan.get("cut") or []), data["validation"].get("status")
            )
        )
    return data


def refresh_ls_ready_v3_metadata(data: Dict[str, Any], logs=None) -> Dict[str, Any]:
    """Actualiza metadatos consumibles por el generador sin reordenar geometría.

    Útil cuando la entrada ya era schema v3 y no conviene reaplicar todo el
    postproceso. No cambia geometría ni orden; solo normaliza mapa R3 y agrega
    hints LS/e1 para que el generador no tenga que inferir políticas.
    """
    logs = logs if logs is not None else []
    out = copy.deepcopy(data or {})
    out["ls_generation_policy"] = _ls_generation_policy()

    motion_map = out.get("motion_map") or {}
    meta = dict(motion_map.get("meta") or {})
    # Paquete exclusivo R3: un refresh solo es válido para un mapa R3 ya calculado.
    # No se convierten valores E1/J2 de R4 cambiando únicamente la etiqueta.
    meta["robot"] = "R3"
    motion_map["meta"] = meta
    out["motion_map"] = motion_map

    robot_programming = out.get("robot_programming") or {}

    for phase in ("mark", "cut", "all"):
        for step in ((out.get("plan") or {}).get(phase) or []):
            _refresh_hole_entry_contract_for_step(step, robot_programming)
            _refresh_slot_entry_contract_for_step(step, robot_programming)
            _attach_ls_generation(step)

    for piece in (out.get("pieces") or []):
        local = piece.get("local_plan") or {}
        for phase in ("mark", "cut"):
            for step in (local.get(phase) or []):
                _refresh_hole_entry_contract_for_step(step, robot_programming)
                _refresh_slot_entry_contract_for_step(step, robot_programming)
                _attach_ls_generation(step)

    out["validation"] = _validate_ls_ready_v3(out)
    logs.append("ls_ready_v3: refreshed_generator_metadata_without_reordering")
    return out


def _refresh_hole_entry_contract_for_step(step: Dict[str, Any], robot_programming: Dict[str, Any]) -> None:
    """Refresca entrada y contrato C+C de barrenos sin reordenar el plan."""
    if not isinstance(step, dict) or step.get("op") != "holes":
        return
    contours = ((step.get("geometry") or {}).get("ordered_contours_dxf") or [])
    for contour in contours:
        if isinstance(contour, dict):
            _apply_hole_arc_contract(contour, robot_programming)

# ---------------------------------------------------------------------------
# Políticas documentadas en el JSON
# ---------------------------------------------------------------------------


def _refresh_slot_entry_contract_for_step(step: Dict[str, Any], robot_programming: Dict[str, Any]) -> None:
    """Refresca el contrato LINE/ARC de ranuras sin reordenar el plan global."""
    if not isinstance(step, dict) or step.get("op") != "cut_inner":
        return
    contours = ((step.get("geometry") or {}).get("ordered_contours_dxf") or [])
    for contour in contours:
        if not isinstance(contour, dict):
            continue
        detection = contour.get("slot_detection") or {}
        if detection.get("eligible") is True or str(contour.get("shape_type") or "").lower() == "slot":
            _apply_slot_arc_contract(contour, robot_programming)


def _coordinate_mapping_notes() -> Dict[str, Any]:
    return {
        "dxf_origin": "lower_left_sheet_coordinates_normalized",
        "target_frame": "UF2",
        "bed": "B",
        "uf2_axis_meaning": {
            "x_uf2": "sheet short side, 0..2438 mm",
            "y_uf2": "sheet long side, 0..6096 mm",
        },
        "physical_to_dxf": {
            "left": "min_x",
            "right": "max_x",
            "bottom": "min_y",
            "top": "max_y",
        },
        "transform_reference": {
            "x_uf2": "y_dxf",
            "y_uf2": "sheet_width_mm - x_dxf",
            "margin_mm": 0.0,
            "note": "Normalizacion y entradas ya fueron resueltas en DXF antes de convertir a UF-2.",
        },
        "e1_follow_axis": "Y_UF2",
    }

def _piece_order_policy() -> Dict[str, Any]:
    return {
        "mode": "anchor_columns_rows",
        "coordinate_reference": "DXF_NORMALIZED",
        "column_grouping": "outer_entry_anchor_fixed_reference",
        "column_band_mm": COLUMN_BAND_MM,
        "column_reference": "first_anchor_in_column",
        "ignore_piece_bbox_for_column_membership": True,
        "cut_column_order": "left_to_right",
        "cut_row_order": "bottom_to_top",
        "mark_column_order": "right_to_left",
        "mark_row_order": "bottom_to_top",
        "mark_reverse_mode": "reverse_columns_only",
        "inner_nesting_cut_rule": {
            "enabled": True,
            "detection": "child_outer_centroid_inside_parent_cut_inner",
            "cut_constraint": "all_nested_pieces_before_container_piece",
            "purpose": "Evitar danar la pieza contenedora cortando primero las piezas chicas anidadas en su ventana interna.",
        },
        "dxf_sort_meaning": {
            "left_to_right": "x_ascending",
            "right_to_left": "x_descending",
            "bottom_to_top": "y_ascending",
        },
    }

def _operation_order_policy() -> Dict[str, Any]:
    return {
        "mark": {
            "mode": "by_entry_position",
            "coordinate_reference": "DXF_NORMALIZED",
            "direction": "right_to_left",
            "type_priority": "none",
            "items": ["mark_text", "mark_figure"],
            "dxf_sort": {"primary": "x_descending", "secondary": "y_ascending"},
        },
        "cut": {
            "mode": "type_priority_then_position",
            "coordinate_reference": "DXF_NORMALIZED_AND_UF2_PREVIEW",
            "type_priority": ["holes", "cut_inner", "cut_outer"],
            "holes": {
                "mode": "individual_paths",
                "physical_order": "x_priority_then_y_then_distance",
                "dxf_sort": {"primary": "x_ascending", "secondary": "y_ascending", "tertiary": "distance"},
                "e1_fixed_policy": "each_hole_uses_own_entry",
            },
            "cut_inner": {
                "mode": "individual_paths",
                "entry_strategy": "min_x_min_y_diagonal_2mm_x_2mm_y",
                "dxf_start": "min_x_min_y",
                "cut_in": "diagonal_2mm_x_2mm_y_inside_waste",
                "e1_fixed_policy": "each_inner_uses_own_entry",
            },
            "cut_outer": {
                "mode": "always_last",
                "dxf_start": "min_y_max_x",
            },
        },
    }

def _ls_generation_policy() -> Dict[str, Any]:
    return {
        "generator_role": "consume_ls_ready_geometry_without_reordering",
        "classifier_scope": "exclusive_cama_B_UF2",
        "first_generated_point": "P[7] after fixed square B P[1]..P[6]",
        "line_count_meaning": "total_POS_points",
        "e1": {
            "limits_mm": dict(E1_LIMITS_MM),
            "allow_negative": True,
            "follow_y_formula": FOLLOW_Y_E1_FORMULA,
            "follow_y_reference": "previous_POS_point",
            "initial_follow_y_reference": {
                "point": "P[6] of fixed square B",
                "note": "Cama B arranca E1 desde el último punto de la escuadra B.",
            },
            "fixed_e1_operations": ["mark_text", "holes", "cut_inner"],
            "follow_y_operations": ["mark_figure", "cut_outer"],
        },
        "path_header": {
            "insert_once_before_each_path": ["UTOOL_NUM=1", "UFRAME_NUM=2"],
            "do_not_duplicate_as_previous_path_footer": True,
            "tooling_note": "No cambiar UTOOL/UT; solo User Frame pasa a UF-2.",
        },
        "geometry_contract": {
            "holes": {
                "generator_source": "plan.cut[].geometry.ordered_contours_dxf[].generator_path_dxf",
                "preferred_motion": "fanuc_two_arcs",
                "fallback_motion": "linear_points_fallback",
                "arc_sequence": "x_min -> y_max -> x_max -> y_min -> x_min",
                "motion_profiles": {
                    "diameter_under_25_mm": "10 CNT40 PTH -> 10 CNT60 PTH -> 7 CNT40 PTH",
                    "diameter_25_mm_and_over": "35 FINE -> 35 CNT50 -> 35 FINE",
                },
                "linear_motion_coord": False,
                "exit": "vertical_from_x_min_to_Z_plus_100_at_100mm_sec",
                "rule": "El clasificador decide C+C o respaldo por puntos; el generador no recalcula geometría ni entrada.",
            }
        },
        "final_home": {
            "required": True,
            "uses_new_final_POS_point": True,
            "joint_values": {"UF": 0, "UT": 1, "J1": 0.0, "J2": -0.0, "J3": 0.0, "J4": 0.0, "J5": -90.0, "J6": 0.0, "E1": 200.0},
        },
    }


# ---------------------------------------------------------------------------
# Motion map E1/J2
# ---------------------------------------------------------------------------


def _load_motion_map(data: Dict[str, Any]) -> Dict[str, Any]:
    root = _classifier_root(data)
    path = os.path.join(root, "config", "motion_maps", "R3_B_240X96_E1_J2_V1.json")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "map_id": "R3_B_240X96_E1_J2_V1",
        "meta": {"robot": "R3", "cama": "B", "placa": "240x96"},
        "selection": {},
        "interpolation": {"e1": "unavailable", "j2": "unavailable"},
        "points": [],
    }


def _classifier_root(data: Dict[str, Any]) -> str:
    source = data.get("source") or {}
    root = source.get("classifier_root")
    if root:
        return os.path.normpath(root)
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _slim_motion_map(motion_map: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "map_id": motion_map.get("map_id"),
        "meta": motion_map.get("meta") or {},
        "selection": motion_map.get("selection") or {},
        "interpolation": motion_map.get("interpolation") or {},
        "point_count": len(motion_map.get("points") or []),
        "points": motion_map.get("points") or [],
    }


def _motion_rows(motion_map: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    rows: Dict[str, List[Dict[str, Any]]] = {}
    for point in motion_map.get("points") or []:
        row = str(point.get("row") or "").upper()
        if not row:
            continue
        rows.setdefault(row, []).append(point)
    for row in rows:
        rows[row].sort(key=lambda p: float(p.get("x_dxf") or 0.0))
    return rows


def _motion_row_y(rows: Dict[str, List[Dict[str, Any]]], row: str) -> float:
    points = rows.get(row) or []
    if not points:
        raise ValueError("Motion map missing row {0}".format(row))
    return sum(float(p.get("y_dxf") or 0.0) for p in points) / float(len(points))


def _select_motion_row(y_dxf: float, motion_map: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    rows = _motion_rows(motion_map)
    for required in ("A", "B", "C", "D", "F"):
        if required not in rows:
            return "", []
    ya, yb, yc, yd, yf = [_motion_row_y(rows, r) for r in ("A", "B", "C", "D", "F")]
    boundaries = [
        (ya + 0.65 * (yb - ya), "A"),
        (yb + 0.35 * (yc - yb), "B"),
        (yc + 0.65 * (yd - yc), "C"),
        (yd + 0.35 * (yf - yd), "D"),
    ]
    selected = "F"
    for boundary, lower_row in boundaries:
        if float(y_dxf) < boundary:
            selected = lower_row
            break
    return selected, rows[selected]


def _select_motion_node(row_points: List[Dict[str, Any]], x_dxf: float) -> Tuple[Optional[Dict[str, Any]], bool]:
    if not row_points:
        return None, False
    candidates = [p for p in row_points if float(p.get("x_dxf") or 0.0) <= float(x_dxf) + 1e-9]
    node = max(candidates, key=lambda p: float(p.get("x_dxf") or 0.0)) if candidates else row_points[0]
    min_x = float(row_points[0].get("x_dxf") or 0.0)
    max_x = float(row_points[-1].get("x_dxf") or 0.0)
    return node, bool(float(x_dxf) < min_x - 1e-9 or float(x_dxf) > max_x + 1e-9)


def _motion_preview(pt_dxf: Optional[Sequence[float]], robot_programming: Dict[str, Any], motion_map: Dict[str, Any]) -> Dict[str, Any]:
    if not pt_dxf:
        return {
            "map_id": motion_map.get("map_id"),
            "coordinate_reference": "DXF_NORMALIZED",
            "e1_preview": None,
            "j2_preview": None,
            "e1_limits_mm": dict(E1_LIMITS_MM),
            "e1_within_limits": None,
            "used_for_correction": False,
            "v4_transition_ready": True,
        }
    x_dxf, y_dxf = float(pt_dxf[0]), float(pt_dxf[1])
    pt_uf2 = _to_uf2(pt_dxf, robot_programming)
    row, row_points = _select_motion_row(y_dxf, motion_map)
    node, extrapolated = _select_motion_node(row_points, x_dxf)
    if node is None:
        e1 = None
        j2 = None
        delta_x = None
        node_id = None
    else:
        delta_x = x_dxf - float(node.get("x_dxf") or 0.0)
        e1 = float(node.get("e1")) + delta_x if node.get("e1") is not None else None
        j2 = float(node.get("j2")) if node.get("j2") is not None else None
        node_id = node.get("id")

    j2_risk = j2 is not None and abs(j2) >= J2_WARNING_DEG
    j2_exceeded = j2 is not None and abs(j2) > J2_PHYSICAL_LIMIT_DEG
    fallback = None
    # C puede brincar a B o D según cercanía si en una futura medición llega a zona de riesgo.
    if row == "C" and j2_risk:
        rows = _motion_rows(motion_map)
        candidates = []
        for alt in ("B", "D"):
            if alt in rows:
                candidates.append((abs(y_dxf - _motion_row_y(rows, alt)), alt))
        if candidates:
            fallback = min(candidates)[1]

    return {
        "map_id": motion_map.get("map_id"),
        "coordinate_reference": "DXF_NORMALIZED",
        "sample_point_dxf": _round_point(pt_dxf),
        "sample_point_uf2_preview": _round_point(pt_uf2),
        "selected_row": row or None,
        "reference_node": node_id,
        "reference_x_dxf": _round_num(float(node.get("x_dxf"))) if node else None,
        "reference_y_dxf": _round_num(float(node.get("y_dxf"))) if node else None,
        "reference_e1_mm": _round_num(float(node.get("e1"))) if node and node.get("e1") is not None else None,
        "reference_j2_deg": _round_num(float(node.get("j2"))) if node and node.get("j2") is not None else None,
        "delta_x_mm": _round_num(delta_x),
        "e1_source": "selected_row_lower_x_node_plus_delta_x",
        "e1_formula": "E1_start = E1_node + (X_entry - X_node)",
        "e1_preview": _round_num(e1),
        "j2_source": "selected_reference_node",
        "j2_preview": _round_num(j2),
        "j2_warning_deg": J2_WARNING_DEG,
        "j2_physical_limit_deg": J2_PHYSICAL_LIMIT_DEG,
        "j2_risk_warning": bool(j2_risk),
        "j2_limit_exceeded": bool(j2_exceeded),
        "fallback_row_if_required": fallback,
        "extrapolated": bool(extrapolated),
        "extrapolation_allowed_inside_sheet": True,
        "e1_limits_mm": dict(E1_LIMITS_MM),
        "e1_within_limits": _e1_within_limits(e1),
        "requires_adjustment": bool(j2_exceeded or _e1_within_limits(e1) is False),
        "used_for_correction": False,
        "v4_transition_ready": True,
    }


def _e1_within_limits(e1: Optional[float]) -> Optional[bool]:
    if e1 is None:
        return None
    return E1_LIMITS_MM["min"] <= float(e1) <= E1_LIMITS_MM["max"]


_OPTIM_PATH_RESOLVER = None

def get_optim_path_resolver() -> OptimPathResolverV4:
    global _OPTIM_PATH_RESOLVER
    if _OPTIM_PATH_RESOLVER is None:
        config_dir = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "optim_path_v4")
        )
        _OPTIM_PATH_RESOLVER = OptimPathResolverV4(config_dir)
    return _OPTIM_PATH_RESOLVER


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(float(v))


def _resolve_optim_v3(
    resolver: OptimPathResolverV4,
    profile: str,
    entry: Optional[Sequence[float]],
    points: Sequence[Sequence[float]],
    path_id: Any,
) -> Dict[str, Any]:
    if not entry or not points:
        return {"profile": profile, "status": "invalid", "releaseable": False, "issues": ["optim_path_entry_or_geometry_missing"]}
    limit_y = max(float(p[1]) for p in points)
    primary = resolver.resolve(
        profile=profile,
        entry_x_dxf=float(entry[0]),
        entry_y_dxf=float(entry[1]),
        path_y_motion_limit_dxf=limit_y,
        path_id=str(path_id or ""),
    )
    primary["initial_profile"] = profile
    primary["final_profile"] = primary.get("profile")
    primary["fallback_applied"] = False

    fallback_profile = None
    if profile == "usual_y" and primary.get("status") in ("warning", "invalid"):
        fallback_profile = "max_y"
    elif profile == "min_y_min_x" and primary.get("status") == "invalid":
        fallback_profile = "usual_y"

    if fallback_profile:
        candidate = resolver.resolve(
            profile=fallback_profile,
            entry_x_dxf=float(entry[0]),
            entry_y_dxf=float(entry[1]),
            path_y_motion_limit_dxf=limit_y,
            path_id=str(path_id or ""),
        )
        rank = {"safe": 0, "warning": 1, "invalid": 2}
        primary_rank = rank.get(str(primary.get("status")), 3)
        candidate_rank = rank.get(str(candidate.get("status")), 3)
        primary_abs = float(primary.get("J2_max_abs_deg") or 9999.0)
        candidate_abs = float(candidate.get("J2_max_abs_deg") or 9999.0)
        if (candidate_rank, candidate_abs) < (primary_rank, primary_abs):
            candidate["initial_profile"] = profile
            candidate["final_profile"] = fallback_profile
            candidate["fallback_applied"] = True
            candidate["fallback_reason"] = (
                "usual_y_postview_requires_more_preventive_posture"
                if profile == "usual_y" else "e1_fixed_candidate_not_safe_dynamic_fallback"
            )
            return candidate
    return primary


def _motion_preview_optim(
    pt_dxf: Optional[Sequence[float]],
    all_points_dxf: Optional[Sequence[Sequence[float]]],
    op_kind: str,
    robot_programming: Dict[str, Any],
    motion_map: Dict[str, Any],
) -> Dict[str, Any]:
    if not pt_dxf or not all_points_dxf:
        return _motion_preview(pt_dxf, robot_programming, motion_map)
    try:
        resolver = get_optim_path_resolver()
        pts = [p for p in all_points_dxf if isinstance(p, (list, tuple)) and len(p) >= 2]
        if not pts:
            return _motion_preview(pt_dxf, robot_programming, motion_map)

        bb = bbox_of_points(pts)
        width = float(bb[2] - bb[0])
        height = float(bb[3] - bb[1])

        if op_kind in ("mark_text", "holes", "single_hole"):
            profile = "min_y_min_x"
        else:
            profile = resolver.classify_geometry(width, height)

        entry_x = float(pt_dxf[0])
        entry_y = float(pt_dxf[1])

        optim = _resolve_optim_v3(
            resolver=resolver,
            profile=profile,
            entry=[entry_x, entry_y],
            points=pts,
            path_id=op_kind,
        )

        pt_uf2 = _to_uf2(pt_dxf, robot_programming)
        e1 = optim.get("E1_selected_mm")
        j2 = optim.get("J2_preview_deg")
        ref_node = optim.get("reference_node")

        j2_risk = j2 is not None and abs(j2) >= J2_WARNING_DEG
        j2_exceeded = j2 is not None and abs(j2) > J2_PHYSICAL_LIMIT_DEG

        return {
            "map_id": "R4_B_UF2_240X96_OPTIM_PATH_V4",
            "coordinate_reference": "DXF_NORMALIZED",
            "sample_point_dxf": _round_point(pt_dxf),
            "sample_point_uf2_preview": _round_point(pt_uf2),
            "selected_row": ref_node,
            "reference_node": ref_node,
            "reference_x_dxf": _round_num(float(optim.get("reference_x_dxf"))) if _is_num(optim.get("reference_x_dxf")) else None,
            "reference_y_dxf": _round_num(float(optim.get("reference_y_dxf"))) if _is_num(optim.get("reference_y_dxf")) else None,
            "reference_e1_mm": _round_num(float(optim.get("canonical_e1_mm"))) if _is_num(optim.get("canonical_e1_mm")) else None,
            "reference_j2_deg": _round_num(j2),
            "delta_x_mm": _round_num(entry_x - float(optim.get("reference_x_dxf") or 1524.0)),
            "e1_source": str(optim.get("e1_resolution") or "optim_path_v4_resolution"),
            "e1_mode": optim.get("E1_mode"),
            "e1_formula": "optim_path_v4_profile_" + str(profile),
            "e1_preview": _round_num(e1),
            "j2_source": "optim_path_v4_interpolation",
            "j2_preview": _round_num(j2),
            "j2_warning_deg": J2_WARNING_DEG,
            "j2_physical_limit_deg": J2_PHYSICAL_LIMIT_DEG,
            "j2_risk_warning": bool(j2_risk),
            "j2_limit_exceeded": bool(j2_exceeded),
            "fallback_row_if_required": None,
            "extrapolated": bool(optim.get("override_applied")),
            "extrapolation_allowed_inside_sheet": True,
            "e1_limits_mm": dict(E1_LIMITS_MM),
            "e1_within_limits": _e1_within_limits(e1),
            "requires_adjustment": bool(j2_exceeded or _e1_within_limits(e1) is False),
            "used_for_correction": False,
            "v4_transition_ready": True,
            "optim_path": optim,
        }
    except Exception as err:
        return _motion_preview(pt_dxf, robot_programming, motion_map)


# ---------------------------------------------------------------------------
# Orden de piezas
# ---------------------------------------------------------------------------


def _piece_outer_anchor(piece: Dict[str, Any]) -> Point:
    contour = ((piece.get("cut_outer") or {}).get("contour") or {})
    points = _contour_points(contour)
    anchor = _choose_point(points, "dxf_max_x_min_y")
    if anchor:
        return anchor
    bb = _bbox(piece)
    return [float(bb[2]), float(bb[1])]


def _resolve_piece_groups_and_orders(pieces: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str], List[str]]:
    pending = sorted(pieces, key=lambda p: (_piece_outer_anchor(p)[0], _piece_outer_anchor(p)[1], str(p.get("id"))))
    columns: List[Dict[str, Any]] = []
    while pending:
        first = pending.pop(0)
        reference_x = float(_piece_outer_anchor(first)[0])
        members = [first]
        keep = []
        for piece in pending:
            anchor = _piece_outer_anchor(piece)
            if abs(float(anchor[0]) - reference_x) <= COLUMN_BAND_MM + 1e-9:
                members.append(piece)
            else:
                keep.append(piece)
        pending = keep
        members.sort(key=lambda p: (_piece_outer_anchor(p)[1], _piece_outer_anchor(p)[0], str(p.get("id"))))
        columns.append({"reference_x": reference_x, "pieces": members})

    columns.sort(key=lambda c: c["reference_x"])
    out_cols = []
    cut_order: List[str] = []
    for idx, col in enumerate(columns, start=1):
        ids = [str(p.get("id")) for p in col["pieces"]]
        out_cols.append({
            "column_id": idx,
            "reference_anchor_x_dxf": _round_num(col["reference_x"]),
            "column_band_mm": COLUMN_BAND_MM,
            "piece_ids_bottom_to_top": ids,
            "anchors_dxf": [
                {"piece_id": str(p.get("id")), "anchor": _round_point(_piece_outer_anchor(p))}
                for p in col["pieces"]
            ],
        })
        cut_order.extend(ids)
    mark_order: List[str] = []
    for col in reversed(out_cols):
        mark_order.extend(col["piece_ids_bottom_to_top"])
    return {"columns": out_cols, "grouping": "outer_entry_anchor_fixed_100mm"}, cut_order, mark_order


def _piece_outer_points_raw(piece: Dict[str, Any]) -> List[Tuple[float, float]]:
    contour = ((piece.get("cut_outer") or {}).get("contour") or {})
    return clean_points(contour.get("points") or contour.get("points_dxf") or [])


def _piece_inner_polygons(piece: Dict[str, Any]) -> List[List[Tuple[float, float]]]:
    cut_inner = piece.get("cut_inner") or {}
    if isinstance(cut_inner, dict):
        contours = cut_inner.get("contours") or []
    elif isinstance(cut_inner, list):
        contours = cut_inner
    else:
        contours = []
    out: List[List[Tuple[float, float]]] = []
    for item in contours:
        if isinstance(item, dict):
            nested = item.get("contour") if isinstance(item.get("contour"), dict) else None
            pts = clean_points(
                item.get("points")
                or item.get("points_dxf")
                or ((nested or {}).get("points") if nested else None)
                or []
            )
        else:
            pts = clean_points(item)
        if len(pts) >= 3:
            out.append(pts)
    return out


def _detect_pieces_nested_in_cut_inners(pieces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detecta piezas cuyo centroide cae dentro de un cut_inner de otra pieza."""
    infos = []
    for piece in pieces or []:
        pid = str(piece.get("id") or "")
        if not pid:
            continue
        outer = _piece_outer_points_raw(piece)
        if len(outer) < 3:
            continue
        infos.append(
            {
                "id": pid,
                "outer": outer,
                "centroid": polygon_centroid(outer),
                "inners": _piece_inner_polygons(piece),
                "area": polygon_area_abs(outer),
            }
        )

    relations: List[Dict[str, Any]] = []
    for child in infos:
        candidates = []
        for parent in infos:
            if child["id"] == parent["id"] or not parent["inners"]:
                continue
            if not point_in_polygon(child["centroid"], parent["outer"]):
                continue
            matched_inner = None
            for inner in parent["inners"]:
                if point_in_polygon(child["centroid"], inner):
                    matched_inner = inner
                    break
            if matched_inner is None:
                continue
            candidates.append(
                (
                    float(parent["area"]),
                    parent["id"],
                    polygon_area_abs(matched_inner),
                )
            )
        if not candidates:
            continue
        # Contenedor inmediato: menor area de pieza padre.
        candidates.sort(key=lambda row: (row[0], row[2], row[1]))
        parent_id = candidates[0][1]
        relations.append(
            {
                "child_piece_id": child["id"],
                "parent_piece_id": parent_id,
                "rule": "child_outer_centroid_inside_parent_cut_inner",
            }
        )
    return relations


def _apply_inner_nesting_cut_constraints(
    pieces: List[Dict[str, Any]],
    cut_order: List[str],
    logs=None,
) -> Tuple[List[str], Dict[str, Any]]:
    """Garantiza que piezas anidadas en un cut_inner se corten antes que su contenedora."""
    relations = _detect_pieces_nested_in_cut_inners(pieces)
    info: Dict[str, Any] = {
        "enabled": True,
        "relations": relations,
        "reordered": False,
        "original_cut_order": list(cut_order),
        "final_cut_order": list(cut_order),
    }
    if not relations or not cut_order:
        return list(cut_order), info

    prerequisites = {pid: set() for pid in cut_order}
    for rel in relations:
        child = str(rel["child_piece_id"])
        parent = str(rel["parent_piece_id"])
        if parent not in prerequisites:
            prerequisites[parent] = set()
        if child in prerequisites or child in cut_order:
            prerequisites[parent].add(child)

    # Cierre transitivo simple: si A->B y B->C, A antes de C.
    changed = True
    while changed:
        changed = False
        for pid, deps in list(prerequisites.items()):
            extra = set()
            for dep in deps:
                extra |= prerequisites.get(dep, set())
            if not extra.issubset(deps):
                prerequisites[pid] = deps | extra
                changed = True

    remaining = list(cut_order)
    placed = set()
    result: List[str] = []
    while remaining:
        moved = False
        for idx, pid in enumerate(remaining):
            deps = prerequisites.get(pid, set())
            if deps.issubset(placed):
                result.append(pid)
                placed.add(pid)
                remaining.pop(idx)
                moved = True
                break
        if not moved:
            # Ciclo o dependencia incompleta: conservar remanente en orden original.
            result.extend(remaining)
            info["warning"] = "inner_nesting_cut_rule_partial_fallback"
            break

    info["reordered"] = result != list(cut_order)
    info["final_cut_order"] = list(result)
    if logs is not None:
        logs.append(
            "inner_nesting_cut_rule: relations={0} reordered={1}".format(
                len(relations), info["reordered"]
            )
        )
        for rel in relations:
            logs.append(
                "inner_nesting_cut_rule: {0} before {1}".format(
                    rel["child_piece_id"], rel["parent_piece_id"]
                )
            )
    return result, info


def _ranges_overlap(a0: float, a1: float, b0: float, b1: float, tol: float = 0.0) -> bool:
    return max(a0, b0) <= min(a1, b1) + tol


# ---------------------------------------------------------------------------
# Enriquecimiento por operación
# ---------------------------------------------------------------------------


def _e1_fixed_from_motion(mm: Dict[str, Any]) -> Optional[float]:
    for key in ("e1_value", "group_e1_fixed", "e1_fixed"):
        val = mm.get(key)
        if val is not None:
            return val
    anchor = mm.get("motion_anchor") or {}
    val = anchor.get("e1_preview")
    return val if val is not None else None


def _j2_from_motion(mm: Dict[str, Any]) -> Optional[float]:
    for key in ("j2_preview_at_entry", "j2_preview", "j2_preview_at_anchor"):
        val = mm.get(key)
        if val is not None:
            return val
    anchor = mm.get("motion_anchor") or {}
    val = anchor.get("j2_preview")
    return val if val is not None else None


def _attach_ls_generation(step: Dict[str, Any]) -> Dict[str, Any]:
    mm = step.get("motion_mapping") or {}
    e1_mode = str(mm.get("e1_mode") or "follow_y")
    fixed_modes = {"fixed", "fixed_from_group_anchor"}
    use_fixed = e1_mode in fixed_modes
    fixed_value = _e1_fixed_from_motion(mm) if use_fixed else None
    j2_preview = _j2_from_motion(mm)

    if use_fixed:
        e1_block = {
            "mode": "fixed",
            "use_e1_fixed": True,
            "e1_fixed_mm": _round_num(fixed_value),
            "source": "motion_map_anchor",
            "limits_mm": dict(E1_LIMITS_MM),
            "within_limits": _e1_within_limits(fixed_value),
            "allow_negative": True,
        }
    else:
        e1_block = {
            "mode": "follow_y",
            "use_e1_fixed": False,
            "e1_fixed_mm": None,
            "source": "incremental_y_from_previous_POS_point",
            "formula": FOLLOW_Y_E1_FORMULA,
            "limits_mm": dict(E1_LIMITS_MM),
            "allow_negative": True,
            "preview_at_entry_mm": mm.get("e1_preview_at_entry", mm.get("e1_preview")),
        }

    step["ls_generation"] = {
        "ready_for_generator": True,
        "operation": step.get("op"),
        "path_name": step.get("path_name"),
        "e1": e1_block,
        "audit": {
            "map_id": mm.get("map_id"),
            "selected_row": mm.get("selected_row"),
            "reference_node": mm.get("reference_node"),
            "delta_x_mm": mm.get("delta_x_mm"),
            "j2_preview_deg": _round_num(j2_preview),
            "j2_source": mm.get("j2_source") or "motion_anchor",
            "j2_risk_warning": mm.get("j2_risk_warning"),
            "j2_limit_exceeded": mm.get("j2_limit_exceeded"),
            "extrapolated": mm.get("extrapolated"),
            "requires_adjustment": mm.get("requires_adjustment"),
            "motion_anchor": mm.get("motion_anchor"),
        },
        "generator_must_not_reorder_geometry": True,
    }
    return step


def _enrich_piece_local_plan(piece: Dict[str, Any], robot_programming: Dict[str, Any], motion_map: Dict[str, Any]) -> None:
    local = piece.get("local_plan") or {}
    mark = list(local.get("mark") or [])
    cut = list(local.get("cut") or [])
    mark = [_enrich_mark_step(step, robot_programming, motion_map) for step in mark]
    mark.sort(key=lambda s: _mark_sort_key(s))
    cut = [_enrich_cut_step(step, robot_programming, motion_map) for step in cut]
    cut = _sort_cut_steps(cut)
    piece["local_plan"] = {"mark": mark, "cut": cut}


def _enrich_mark_step(step: Dict[str, Any], robot_programming: Dict[str, Any], motion_map: Dict[str, Any]) -> Dict[str, Any]:
    step = copy.deepcopy(step)
    geom = step.get("geometry") or {}
    strokes = list(geom.get("strokes") or step.get("strokes") or [])
    if step.get("op") == "mark_text":
        strategy = "mark_text_dxf_min_x_min_y"
        target = "dxf_min_x_min_y"
        ordered, start = _order_strokes_by_target(strokes, target)
        continuous_points = geom.get("continuous_points_dxf") or step.get("continuous_points_dxf") or []
        if continuous_points:
            # Cama B: el texto debe iniciar en menor X y menor Y.
            # Recalculamos el bloque continuo a partir de los strokes ya orientados por target.
            continuous_points = _flatten_ordered_strokes_points(ordered)
            if continuous_points:
                start = continuous_points[0]
        policy_id = "R3_B_240X96_MARK_TEXT_ENTRY_V1"
        e1_mode = "fixed"
    else:
        # Cama B: figura inicia abajo-derecha = punto del path más cercano a
        # (X_max, Y_min) del bbox. No altera coordenadas; solo el índice de inicio.
        strategy = "mark_figure_dxf_bottom_right"
        ordered, start = _order_figure_bottom_right(
            strokes, prefer_next="dxf_increasing_y"
        )
        continuous_points = []
        policy_id = "R3_B_240X96_MARK_FIGURE_ENTRY_V1"
        e1_mode = "follow_y"

    uf2 = _to_uf2(start, robot_programming) if start else None
    step["entry"] = {
        "strategy": strategy,
        "policy_id": policy_id,
        "coordinate_stage": "dxf_global_before_uf_transform",
        "requires_cut_in": False,
        "start_point_dxf": _round_point(start) if start else None,
        "start_point_uf2_preview": _round_point(uf2) if uf2 else None,
    }
    geometry_type = "text_continuous" if step.get("op") == "mark_text" and step.get("laser_mode") == "continuous" else "strokes"
    step["geometry"] = {
        "type": geometry_type,
        "source_strokes_dxf": strokes,
        "ordered_strokes_dxf": ordered,
        "ordered_by": strategy,
        "was_reordered": True,
    }
    if step.get("op") == "mark_text" and continuous_points:
        step["geometry"]["continuous_points_dxf"] = [_round_point(p) for p in continuous_points]
        step["geometry"]["continuous_point_count"] = len(continuous_points)
        step["path_rules"] = {
            "must_close": False,
            "is_open_path": True,
            "allow_inserted_start_point": False,
            "laser_continuous_for_group": True,
        }
    else:
        step["path_rules"] = {"must_close": False, "is_open_path": True, "allow_inserted_start_point": False}
    preview = _motion_preview_optim(start, continuous_points or [p for s in ordered for p in s], str(step.get("op") or "mark"), robot_programming, motion_map)
    step["motion_mapping"] = {
        **preview,
        "e1_mode": e1_mode,
        "e1_scope": "text_block" if e1_mode == "fixed" else "path",
        "e1_value": preview.get("e1_preview") if e1_mode == "fixed" else None,
        "e1_preview_at_entry": preview.get("e1_preview"),
        "j2_preview_at_entry": preview.get("j2_preview"),
        "motion_anchor": {
            "source": "entry_start_point",
            "start_point_dxf": _round_point(start) if start else None,
            "start_point_uf2_preview": _round_point(uf2) if uf2 else None,
            "e1_preview": preview.get("e1_preview"),
            "j2_preview": preview.get("j2_preview"),
        },
    }
    _attach_ls_generation(step)
    step["order_key"] = {
        "phase": "mark",
        "coordinate_reference": "DXF_GLOBAL",
        "sort_mode": "by_entry_position_right_to_left_x_desc",
        "sort_x_dxf": _round_num(float(start[0])) if start else None,
        "sort_y_dxf": _round_num(float(start[1])) if start else None,
        "sort_x_uf2_preview": _round_num(uf2[0]) if uf2 else None,
        "sort_y_uf2_preview": _round_num(uf2[1]) if uf2 else None,
    }
    return step


def _enrich_cut_step(step: Dict[str, Any], robot_programming: Dict[str, Any], motion_map: Dict[str, Any]) -> Dict[str, Any]:
    op = step.get("op")
    if op == "holes":
        return _enrich_holes_step(step, robot_programming, motion_map)
    if op == "cut_inner":
        return _enrich_cut_inner_step(step, robot_programming, motion_map)
    if op == "cut_outer":
        return _enrich_cut_outer_step(step, robot_programming, motion_map)
    return step


def _enrich_holes_step(step: Dict[str, Any], robot_programming: Dict[str, Any], motion_map: Dict[str, Any]) -> Dict[str, Any]:
    step = copy.deepcopy(step)
    contours = list((step.get("geometry") or {}).get("contours") or [])
    enriched = [_enrich_single_hole(c, idx, robot_programming, motion_map) for idx, c in enumerate(contours, start=1)]
    # 240x96: prioridad 1 X_DXF, prioridad 2 Y_DXF, prioridad 3 distancia.
    enriched.sort(key=lambda c: (
        float((c.get("order_key") or {}).get("primary_x_dxf") or 0.0),
        float((c.get("order_key") or {}).get("secondary_y_dxf") or 0.0),
        float((c.get("order_key") or {}).get("tertiary_distance_mm") or 0.0),
    ))
    for idx, contour in enumerate(enriched, start=1):
        contour["hole_id"] = "H{0:02d}".format(idx)
        contour["order_index"] = idx
        mm = contour.get("motion_mapping") or {}
        contour["motion_mapping"] = {
            **mm,
            "e1_mode": "fixed",
            "e1_scope": "single_hole",
            "e1_value": mm.get("e1_preview"),
            "individual_e1_fixed": True,
        }
        contour["op"] = "hole_contour"
        _attach_ls_generation(contour)

    first = enriched[0] if enriched else None
    step["entry"] = copy.deepcopy(first.get("entry")) if first else _empty_cut_entry("holes")
    step["geometry"] = {
        "type": "closed_contours",
        "source_contours_dxf": contours,
        "ordered_contours_dxf": enriched,
        "ordered_by": "holes_x_then_y_then_distance_240x96",
        "was_reordered": True,
    }
    step["hole_order_policy"] = _operation_order_policy()["cut"]["holes"]
    first_mm = (first.get("motion_mapping") or {}) if first else {}
    step["motion_mapping"] = {
        **first_mm,
        "e1_mode": "fixed",
        "e1_scope": "single_hole",
        "e1_value": first_mm.get("e1_preview"),
        "individual_hole_path": True,
        "grouping_disabled": True,
        "motion_anchor": {
            "source": "own_hole_entry",
            "hole_id": first.get("hole_id") if first else None,
            "start_point_dxf": (first.get("entry") or {}).get("start_point_dxf") if first else None,
            "e1_preview": first_mm.get("e1_preview"),
            "j2_preview": first_mm.get("j2_preview"),
        } if first else None,
    }
    _attach_ls_generation(step)
    step["order_key"] = {
        "phase": "cut",
        "type_priority": 1,
        "sort_mode": "hole_x_then_y_then_distance",
        "primary_x_dxf": (first.get("order_key") or {}).get("primary_x_dxf") if first else None,
        "secondary_y_dxf": (first.get("order_key") or {}).get("secondary_y_dxf") if first else None,
    }
    step["path_rules"] = {"must_close": True, "close_to_start": True, "is_open_path": False, "allow_inserted_start_point": True}
    return step

def _round_fit_value(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return _round_num(float(value))
    return value

def _apply_hole_arc_contract(c: Dict[str, Any], robot_programming: Dict[str, Any]) -> Optional[Point]:
    """Construye el contrato LS-ready de un barreno.

    Si el ajuste circular cumple tolerancias, entrega cuatro puntos cardinales
    para dos instrucciones FANUC C. En caso contrario conserva el contorno
    original como respaldo lineal.
    """
    points = _contour_points(c)
    fallback_start = _choose_point(points, "dxf_min_x_min_y")
    fallback_ordered = _order_closed_points_from_start(
        points, fallback_start, prefer_next="continue_original"
    )

    fit = fit_circle_least_squares(points)
    radius = float(fit.get("radius") or 0.0) if fit.get("valid") else 0.0
    eligible = bool(
        fit.get("valid")
        and int(fit.get("point_count") or 0) >= FANUC_ARC_MIN_POINTS
        and radius >= FANUC_ARC_MIN_RADIUS_MM
        and float(fit.get("rms_error_mm") if fit.get("rms_error_mm") is not None else 1e9) <= FANUC_ARC_MAX_RMS_ERROR_MM
        and float(fit.get("max_error_mm") if fit.get("max_error_mm") is not None else 1e9) <= FANUC_ARC_MAX_RADIAL_ERROR_MM
        and float(fit.get("bbox_aspect_error") if fit.get("bbox_aspect_error") is not None else 1e9) <= FANUC_ARC_MAX_ASPECT_ERROR
        and radius > FANUC_ARC_CUT_IN_MM
    )

    diameter_mm = radius * 2.0 if radius > 0.0 else 0.0
    motion_profile = _hole_motion_profile_for_diameter(diameter_mm)

    if eligible:
        cx, cy = [float(v) for v in fit["center"]]
        x_min = [cx - radius, cy]
        y_max = [cx, cy + radius]
        x_max = [cx + radius, cy]
        y_min = [cx, cy - radius]
        start = x_min
    else:
        start = fallback_start
        x_min = y_max = x_max = y_min = None

    cut_in = [start[0] + FANUC_ARC_CUT_IN_MM, start[1]] if start else None
    start_uf2 = _to_uf2(start, robot_programming) if start else None
    cut_in_uf2 = _to_uf2(cut_in, robot_programming) if cut_in else None
    cut_in_inside = _point_in_polygon(cut_in, points) if cut_in else None

    fit_audit = {
        "valid": bool(fit.get("valid")),
        "eligible_for_fanuc_two_arcs": eligible,
        "reason": "within_tolerance" if eligible else str(fit.get("reason") or "outside_tolerance"),
        "source_etype": c.get("etype"),
        "point_count": int(fit.get("point_count") or 0),
        "center_dxf": _round_point(fit.get("center")) if fit.get("center") else None,
        "radius_mm": _round_fit_value(fit.get("radius")),
        "rms_error_mm": _round_fit_value(fit.get("rms_error_mm")),
        "max_error_mm": _round_fit_value(fit.get("max_error_mm")),
        "max_error_ratio": _round_fit_value(fit.get("max_error_ratio")),
        "bbox_width_mm": _round_fit_value(fit.get("bbox_width_mm")),
        "bbox_height_mm": _round_fit_value(fit.get("bbox_height_mm")),
        "bbox_aspect_error": _round_fit_value(fit.get("bbox_aspect_error")),
        "tolerances": {
            "min_points": FANUC_ARC_MIN_POINTS,
            "min_radius_mm": FANUC_ARC_MIN_RADIUS_MM,
            "max_rms_error_mm": FANUC_ARC_MAX_RMS_ERROR_MM,
            "max_radial_error_mm": FANUC_ARC_MAX_RADIAL_ERROR_MM,
            "max_aspect_error": FANUC_ARC_MAX_ASPECT_ERROR,
        },
    }

    c["entry"] = {
        "strategy": "hole_dxf_min_x_cut_in_plus_x",
        "policy_id": "R3_B_240X96_HOLE_ENTRY_ARC_V2",
        "coordinate_stage": "dxf_global_before_uf_transform",
        "requires_cut_in": True,
        "cut_in_mm": FANUC_ARC_CUT_IN_MM,
        "start_point_dxf": _round_point(start) if start else None,
        "cut_in_point_dxf": _round_point(cut_in) if cut_in else None,
        "first_move_vector_dxf": [-1.0, 0.0],
        "first_move_vector_uf2": [0.0, 1.0],
        "start_point_uf2_preview": _round_point(start_uf2) if start_uf2 else None,
        "cut_in_point_uf2_preview": _round_point(cut_in_uf2) if cut_in_uf2 else None,
        "cut_in_validation": {
            "inside_hole_polygon": cut_in_inside,
            "validation_method": "point_in_polygon_dxf",
            "warning_if_false": "Hole cut-in is not inside hole/waste polygon; check lead-in sign before LS generation.",
        },
    }
    c["ordered_points_dxf"] = fallback_ordered
    c["circle_fit"] = fit_audit

    if eligible:
        c["center_dxf"] = _round_point(fit["center"])
        c["radius_mm"] = _round_num(radius)
        c["generator_path_dxf"] = {
            "type": "fanuc_two_arcs",
            "motion_type": "fanuc_two_arcs",
            "coordinate_stage": "dxf_global_before_uf_transform",
            "generator_must_not_calculate_cut_in": True,
            "generator_must_not_fit_circle": True,
            "pre_laser_point_dxf": _round_point(cut_in),
            "laser_on_point_dxf": _round_point(cut_in),
            "first_cut_point_dxf": _round_point(x_min),
            "circle_center_dxf": _round_point(fit["center"]),
            "circle_radius_mm": _round_num(radius),
            "diameter_mm": _round_num(diameter_mm),
            "small_hole_diameter_limit_mm": FANUC_SMALL_HOLE_DIAMETER_LIMIT_MM,
            "motion_profile_id": motion_profile["profile_id"],
            "diameter_class": motion_profile["diameter_class"],
            "linear_motion_coord": FANUC_HOLE_USE_COORD,
            "line_to_start": copy.deepcopy(motion_profile["line_to_start"]),
            "cardinal_points_dxf": {
                "x_min": _round_point(x_min),
                "y_max": _round_point(y_max),
                "x_max": _round_point(x_max),
                "y_min": _round_point(y_min),
            },
            "arc_1": {
                "from": "x_min",
                "via_point_dxf": _round_point(y_max),
                "end_point_dxf": _round_point(x_max),
                "speed_mm_sec": motion_profile["arc_1"]["speed_mm_sec"],
                "termination": motion_profile["arc_1"]["termination"],
                "use_pth": motion_profile["arc_1"]["use_pth"],
            },
            "arc_2": {
                "from": "x_max",
                "via_point_dxf": _round_point(y_min),
                "end_point_dxf": _round_point(x_min),
                "speed_mm_sec": motion_profile["arc_2"]["speed_mm_sec"],
                "termination": motion_profile["arc_2"]["termination"],
                "use_pth": motion_profile["arc_2"]["use_pth"],
            },
            "exit": {
                "from_point_dxf": _round_point(x_min),
                "vertical_lift_mm": 100.0,
                "speed_mm_sec": 100,
                "termination": "FINE",
            },
            "fallback": {
                "type": "linear_points_fallback",
                "cut_points_dxf": fallback_ordered,
                "reason_if_used": "circle_fit_not_eligible_or_generator_contract_invalid",
            },
            "must_close": True,
            "close_to_start": True,
            "sequence": [
                "move_safe_to_pre_laser_point",
                "move_z_cut_to_pre_laser_point",
                "laser_on",
                "line_to_x_min_{0}_{1}{2}".format(
                    motion_profile["line_to_start"]["speed_mm_sec"],
                    motion_profile["line_to_start"]["termination"],
                    "_PTH" if motion_profile["line_to_start"]["use_pth"] else "",
                ),
                "arc_x_min_via_y_max_to_x_max_{0}_{1}{2}".format(
                    motion_profile["arc_1"]["speed_mm_sec"],
                    motion_profile["arc_1"]["termination"],
                    "_PTH" if motion_profile["arc_1"]["use_pth"] else "",
                ),
                "arc_x_max_via_y_min_to_x_min_{0}_{1}{2}".format(
                    motion_profile["arc_2"]["speed_mm_sec"],
                    motion_profile["arc_2"]["termination"],
                    "_PTH" if motion_profile["arc_2"]["use_pth"] else "",
                ),
                "laser_off",
                "vertical_exit_Z_plus_100_at_100mm_sec",
            ],
        }
    else:
        c["generator_path_dxf"] = {
            "type": "linear_points_fallback",
            "motion_type": "linear_points_fallback",
            "coordinate_stage": "dxf_global_before_uf_transform",
            "generator_must_not_calculate_cut_in": True,
            "pre_laser_point_dxf": _round_point(cut_in) if cut_in else None,
            "laser_on_point_dxf": _round_point(cut_in) if cut_in else None,
            "first_cut_point_dxf": _round_point(start) if start else None,
            "cut_points_dxf": fallback_ordered,
            "must_close": True,
            "close_to_start": True,
            "fallback_reason": "circle_fit_outside_tolerance",
            "circle_fit": fit_audit,
            "sequence": [
                "move_safe_to_pre_laser_point",
                "move_z_cut_to_pre_laser_point",
                "laser_on",
                "cut_to_first_cut_point",
                "follow_cut_points_dxf",
                "close_to_start_if_needed",
                "laser_off",
                "vertical_exit_Z_plus_100_at_100mm_sec",
            ],
        }
    return start

def _enrich_single_hole(contour: Dict[str, Any], idx: int, robot_programming: Dict[str, Any], motion_map: Dict[str, Any]) -> Dict[str, Any]:
    c = copy.deepcopy(contour)
    start = _apply_hole_arc_contract(c, robot_programming)
    points = _contour_points(c)
    center = c.get("center_dxf") or _center_of_points(points)
    posture_sample = [start[0], min(p[1] for p in points)] if (start and points) else start
    c["motion_mapping"] = _motion_preview(posture_sample, robot_programming, motion_map)
    c["order_key"] = {
        "coordinate_reference": "DXF_NORMALIZED",
        "sort_mode": "x_then_y_then_distance",
        "primary_x_dxf": _round_num(float(start[0])) if start else None,
        "secondary_y_dxf": _round_num(float(start[1])) if start else None,
        "tertiary_distance_mm": 0.0,
        "center_dxf": _round_point(center) if center else None,
    }
    c["hole_id"] = "H{0:02d}".format(idx)
    return c

def _enrich_cut_inner_step(step: Dict[str, Any], robot_programming: Dict[str, Any], motion_map: Dict[str, Any]) -> Dict[str, Any]:
    step = copy.deepcopy(step)
    contours = list((step.get("geometry") or {}).get("contours") or [])
    enriched = []
    for idx, c in enumerate(contours, start=1):
        enriched.append(_enrich_inner_contour(c, idx, robot_programming, motion_map))
    enriched.sort(key=lambda c: (float(c.get("entry", {}).get("start_point_dxf", [0, 0])[0]), float(c.get("entry", {}).get("start_point_dxf", [0, 0])[1])))
    for idx, contour in enumerate(enriched, start=1):
        contour["inner_id"] = "I{0:02d}".format(idx)
        contour["order_index"] = idx
        mm = contour.get("motion_mapping") or {}
        c_e1_mode = mm.get("e1_mode") or "fixed"
        contour["motion_mapping"] = {
            **mm,
            "e1_mode": c_e1_mode,
            "e1_scope": "single_inner" if c_e1_mode == "fixed" else "path",
            "e1_value": mm.get("e1_preview") if c_e1_mode == "fixed" else None,
            "individual_e1_fixed": c_e1_mode == "fixed",
        }
        contour["op"] = "inner_contour"
        _attach_ls_generation(contour)
    first = enriched[0] if enriched else None
    step["entry"] = copy.deepcopy(first.get("entry")) if first else _empty_cut_entry("cut_inner")
    step["geometry"] = {
        "type": "closed_contours",
        "source_contours_dxf": contours,
        "ordered_contours_dxf": enriched,
        "ordered_by": "inner_by_entry_position",
        "was_reordered": True,
    }
    first_mm = (first.get("motion_mapping") or {}) if first else {}
    s_e1_mode = first_mm.get("e1_mode") or "fixed"
    step["motion_mapping"] = {
        **first_mm,
        "e1_mode": s_e1_mode,
        "e1_scope": "single_inner" if s_e1_mode == "fixed" else "path",
        "e1_value": first_mm.get("e1_preview") if s_e1_mode == "fixed" else None,
        "individual_inner_path": s_e1_mode == "fixed",
        "e1_preview_at_entry": first_mm.get("e1_preview"),
        "j2_preview_at_entry": first_mm.get("j2_preview"),
        "motion_anchor": {
            "source": "own_inner_entry",
            "inner_id": first.get("inner_id") if first else None,
            "start_point_dxf": (first.get("entry") or {}).get("start_point_dxf") if first else None,
            "e1_preview": first_mm.get("e1_preview"),
            "j2_preview": first_mm.get("j2_preview"),
        } if first else None,
    }
    _attach_ls_generation(step)
    step["order_key"] = {"phase": "cut", "type_priority": 2, "sort_mode": "cut_inner_by_x_then_y", "primary_x_dxf": (step.get("entry") or {}).get("start_point_dxf", [None, None])[0], "secondary_y_dxf": (step.get("entry") or {}).get("start_point_dxf", [None, None])[1]}
    step["path_rules"] = {"must_close": True, "close_to_start": True, "is_open_path": False, "allow_inserted_start_point": True}
    return step


def _native_point(segment: Dict[str, Any], key: str) -> Optional[Point]:
    value = (segment or {}).get(key)
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return [float(value[0]), float(value[1])]
    return None


def _native_segment_copy(segment: Dict[str, Any]) -> Dict[str, Any]:
    return copy.deepcopy(segment or {})


def _reverse_native_segment(segment: Dict[str, Any]) -> Dict[str, Any]:
    seg = _native_segment_copy(segment)
    seg["start_dxf"], seg["end_dxf"] = seg.get("end_dxf"), seg.get("start_dxf")
    if str(seg.get("type") or "").lower() == "arc":
        seg["start_angle_deg"], seg["end_angle_deg"] = seg.get("end_angle_deg"), seg.get("start_angle_deg")
        seg["direction"] = "cw" if str(seg.get("direction") or "ccw").lower() == "ccw" else "ccw"
    return seg


def _reverse_native_chain(segments: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_reverse_native_segment(seg) for seg in reversed(list(segments or []))]


def _angle_of_point_deg(point: Sequence[float], center: Sequence[float]) -> float:
    return math.degrees(math.atan2(float(point[1]) - float(center[1]), float(point[0]) - float(center[0]))) % 360.0


def _directed_angle_delta_deg(start_deg: float, end_deg: float, direction: str) -> float:
    if str(direction or "ccw").lower() == "cw":
        return (float(start_deg) - float(end_deg)) % 360.0
    return (float(end_deg) - float(start_deg)) % 360.0


def _advance_angle_deg(start_deg: float, delta_deg: float, direction: str) -> float:
    if str(direction or "ccw").lower() == "cw":
        return (float(start_deg) - float(delta_deg)) % 360.0
    return (float(start_deg) + float(delta_deg)) % 360.0


def _arc_point(center: Sequence[float], radius: float, angle_deg: float) -> Point:
    angle = math.radians(float(angle_deg))
    return [float(center[0]) + float(radius) * math.cos(angle), float(center[1]) + float(radius) * math.sin(angle)]


def _arc_sweep(segment: Dict[str, Any]) -> float:
    value = segment.get("sweep_deg")
    if value is not None:
        return abs(float(value))
    return _directed_angle_delta_deg(
        float(segment.get("start_angle_deg") or 0.0),
        float(segment.get("end_angle_deg") or 0.0),
        str(segment.get("direction") or "ccw"),
    )


def _arc_contains_angle(segment: Dict[str, Any], angle_deg: float, tol_deg: float = 1e-4) -> bool:
    start = float(segment.get("start_angle_deg") or 0.0) % 360.0
    direction = str(segment.get("direction") or "ccw")
    delta = _directed_angle_delta_deg(start, float(angle_deg) % 360.0, direction)
    return delta <= _arc_sweep(segment) + tol_deg


def _segment_contains_point(segment: Dict[str, Any], point: Sequence[float], tol: float = 0.02) -> bool:
    seg_type = str(segment.get("type") or "").lower()
    start = _native_point(segment, "start_dxf")
    end = _native_point(segment, "end_dxf")
    if start and _same_point(start, point, tol=tol):
        return True
    if end and _same_point(end, point, tol=tol):
        return True
    if seg_type == "line" and start and end:
        length = _dist(start, end)
        return abs((_dist(start, point) + _dist(point, end)) - length) <= tol
    if seg_type == "arc":
        center = _native_point(segment, "center_dxf")
        radius = float(segment.get("radius_mm") or 0.0)
        if not center or radius <= 0.0 or abs(_dist(center, point) - radius) > tol:
            return False
        return _arc_contains_angle(segment, _angle_of_point_deg(point, center), tol_deg=0.05)
    return False


def _slot_start_candidates(segments: Sequence[Dict[str, Any]]) -> List[Point]:
    candidates: List[Point] = []
    for segment in segments or []:
        for key in ("start_dxf", "end_dxf"):
            point = _native_point(segment, key)
            if point is not None:
                candidates.append(point)
        if str(segment.get("type") or "").lower() == "arc":
            center = _native_point(segment, "center_dxf")
            radius = float(segment.get("radius_mm") or 0.0)
            if center and radius > 0.0:
                for angle in (0.0, 90.0, 180.0, 270.0):
                    if _arc_contains_angle(segment, angle):
                        candidates.append(_arc_point(center, radius, angle))
    unique: List[Point] = []
    for point in candidates:
        if not any(_same_point(point, old, tol=0.01) for old in unique):
            unique.append(point)
    return unique


def _make_oriented_arc(
    source: Dict[str, Any], start_point: Sequence[float], end_point: Sequence[float], sweep_deg: float
) -> Dict[str, Any]:
    seg = _native_segment_copy(source)
    center = _native_point(seg, "center_dxf")
    direction = str(seg.get("direction") or "ccw")
    seg["start_dxf"] = _round_point(start_point)
    seg["end_dxf"] = _round_point(end_point)
    seg["start_angle_deg"] = round(_angle_of_point_deg(start_point, center), 6)
    seg["end_angle_deg"] = round(_angle_of_point_deg(end_point, center), 6)
    seg["direction"] = direction
    seg["sweep_deg"] = round(float(sweep_deg), 6)
    return seg


def _split_native_segment_at_point(
    segment: Dict[str, Any], point: Sequence[float]
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Devuelve (before, after) siguiendo la orientación del segmento."""
    start = _native_point(segment, "start_dxf")
    end = _native_point(segment, "end_dxf")
    if start is None or end is None:
        return None, _native_segment_copy(segment)
    if _same_point(point, start, tol=0.02):
        return None, _native_segment_copy(segment)
    if _same_point(point, end, tol=0.02):
        return _native_segment_copy(segment), None

    seg_type = str(segment.get("type") or "").lower()
    if seg_type == "line":
        before = _native_segment_copy(segment)
        before["end_dxf"] = _round_point(point)
        after = _native_segment_copy(segment)
        after["start_dxf"] = _round_point(point)
        return before, after
    if seg_type == "arc":
        center = _native_point(segment, "center_dxf")
        direction = str(segment.get("direction") or "ccw")
        start_angle = _angle_of_point_deg(start, center)
        point_angle = _angle_of_point_deg(point, center)
        end_angle = _angle_of_point_deg(end, center)
        before_sweep = _directed_angle_delta_deg(start_angle, point_angle, direction)
        after_sweep = _directed_angle_delta_deg(point_angle, end_angle, direction)
        return (
            _make_oriented_arc(segment, start, point, before_sweep),
            _make_oriented_arc(segment, point, end, after_sweep),
        )
    return None, _native_segment_copy(segment)


def _rotate_native_chain_at_start(
    segments: Sequence[Dict[str, Any]], start: Sequence[float]
) -> List[Dict[str, Any]]:
    chain = [_native_segment_copy(seg) for seg in (segments or [])]
    # Preferir un segmento que ya comience exactamente en el punto.
    for idx, segment in enumerate(chain):
        seg_start = _native_point(segment, "start_dxf")
        if seg_start and _same_point(seg_start, start, tol=0.02):
            return chain[idx:] + chain[:idx]
    # Luego buscar un punto interior o el final de un segmento.
    for idx, segment in enumerate(chain):
        if not _segment_contains_point(segment, start):
            continue
        before, after = _split_native_segment_at_point(segment, start)
        out: List[Dict[str, Any]] = []
        if after is not None:
            out.append(after)
        out.extend(chain[idx + 1 :])
        out.extend(chain[:idx])
        if before is not None:
            out.append(before)
        return out
    return []


def _first_segment_direction(chain: Sequence[Dict[str, Any]]) -> Tuple[float, float]:
    if not chain:
        return (0.0, 0.0)
    segment = chain[0]
    start = _native_point(segment, "start_dxf")
    end = _native_point(segment, "end_dxf")
    if start is None or end is None:
        return (0.0, 0.0)
    if str(segment.get("type") or "").lower() == "line":
        vx, vy = end[0] - start[0], end[1] - start[1]
    else:
        center = _native_point(segment, "center_dxf")
        rx, ry = start[0] - center[0], start[1] - center[1]
        if str(segment.get("direction") or "ccw").lower() == "cw":
            vx, vy = ry, -rx
        else:
            vx, vy = -ry, rx
    mag = math.hypot(vx, vy)
    return (vx / mag, vy / mag) if mag > 1e-9 else (0.0, 0.0)


def _ordered_slot_native_chain(segments: Sequence[Dict[str, Any]], start: Sequence[float]) -> List[Dict[str, Any]]:
    forward = _rotate_native_chain_at_start(segments, start)
    reverse = _rotate_native_chain_at_start(_reverse_native_chain(segments), start)
    desired = (0.0, 1.0)
    score_f = sum(a * b for a, b in zip(_first_segment_direction(forward), desired))
    score_r = sum(a * b for a, b in zip(_first_segment_direction(reverse), desired))
    return forward if score_f >= score_r else reverse


def _split_arc_for_fanuc(segment: Dict[str, Any], max_sweep_deg: float = 180.0) -> List[Dict[str, Any]]:
    sweep = _arc_sweep(segment)
    if sweep <= max_sweep_deg + 1e-6:
        return [_native_segment_copy(segment)]
    center = _native_point(segment, "center_dxf")
    radius = float(segment.get("radius_mm") or 0.0)
    direction = str(segment.get("direction") or "ccw")
    start_angle = _angle_of_point_deg(_native_point(segment, "start_dxf"), center)
    count = max(2, int(math.ceil(sweep / max_sweep_deg)))
    part_sweep = sweep / count
    out = []
    current_angle = start_angle
    current_point = _native_point(segment, "start_dxf")
    for idx in range(count):
        end_angle = _advance_angle_deg(current_angle, part_sweep, direction)
        end_point = _native_point(segment, "end_dxf") if idx == count - 1 else _arc_point(center, radius, end_angle)
        out.append(_make_oriented_arc(segment, current_point, end_point, part_sweep))
        current_angle = end_angle
        current_point = end_point
    return out


def _slot_motion_segments(chain: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    expanded: List[Dict[str, Any]] = []
    for segment in chain or []:
        if str(segment.get("type") or "").lower() == "arc":
            expanded.extend(_split_arc_for_fanuc(segment))
        else:
            expanded.append(_native_segment_copy(segment))

    motions: List[Dict[str, Any]] = []
    for idx, segment in enumerate(expanded):
        seg_type = str(segment.get("type") or "").lower()
        termination = "FINE" if idx == len(expanded) - 1 else FANUC_SLOT_TERMINATION
        motion = {
            "type": seg_type,
            "start_point_dxf": _round_point(_native_point(segment, "start_dxf")),
            "end_point_dxf": _round_point(_native_point(segment, "end_dxf")),
            "speed_mm_sec": FANUC_SLOT_CUT_SPEED_MM_SEC,
            "termination": termination,
        }
        if seg_type == "arc":
            center = _native_point(segment, "center_dxf")
            radius = float(segment.get("radius_mm") or 0.0)
            direction = str(segment.get("direction") or "ccw")
            start_angle = _angle_of_point_deg(motion["start_point_dxf"], center)
            sweep = _arc_sweep(segment)
            via_angle = _advance_angle_deg(start_angle, sweep / 2.0, direction)
            motion.update({
                "center_dxf": _round_point(center),
                "radius_mm": _round_num(radius),
                "direction": direction,
                "sweep_deg": _round_num(sweep),
                "via_point_dxf": _round_point(_arc_point(center, radius, via_angle)),
            })
        motions.append(motion)
    return motions


def _apply_slot_arc_contract(
    c: Dict[str, Any], robot_programming: Dict[str, Any]
) -> Optional[Point]:
    detection = c.get("slot_detection") or {}
    segments = list(c.get("native_segments") or [])
    if not detection.get("eligible") or len(segments) != 4:
        return None

    candidates = _slot_start_candidates(segments)
    start = min(candidates, key=lambda p: (float(p[0]), float(p[1]))) if candidates else None
    if start is None:
        return None
    chain = _ordered_slot_native_chain(segments, start)
    motions = _slot_motion_segments(chain)
    if not chain or not motions:
        return None
    if not _same_point(motions[-1]["end_point_dxf"], start, tol=0.05):
        return None

    points = _contour_points(c)
    fallback_ordered = _order_closed_points_from_start(points, start, prefer_next="dxf_increasing_y")
    cut_in = _diagonal_cut_in_point_dxf(start, dx_mm=2.0, dy_mm=2.0)
    start_uf2 = _to_uf2(start, robot_programming)
    cut_in_uf2 = _to_uf2(cut_in, robot_programming)
    cut_in_inside = _point_in_polygon(cut_in, points)

    c["entry"] = {
        "strategy": "inner_slot_min_x_min_y_diagonal_plus2_x_plus2_y",
        "policy_id": "R3_B_240X96_SLOT_ENTRY_ARC_V1",
        "coordinate_stage": "dxf_global_before_uf_transform",
        "requires_cut_in": True,
        "cut_in_mm": _round_num(math.hypot(2.0, 2.0)),
        "cut_in_type": "diagonal",
        "cut_in_dx_mm": 2.0,
        "cut_in_dy_mm": 2.0,
        "cut_in_angle_deg": 45.0,
        "start_point_dxf": _round_point(start),
        "cut_in_point_dxf": _round_point(cut_in),
        "first_move_vector_dxf": [-0.7071, -0.7071],
        "first_move_vector_uf2": [-0.7071, 0.7071],
        "start_point_uf2_preview": _round_point(start_uf2),
        "cut_in_point_uf2_preview": _round_point(cut_in_uf2),
        "cut_in_validation": {
            "inside_waste_polygon": cut_in_inside,
            "validation_method": "point_in_polygon_dxf",
            "warning_if_false": "Review slot cut entry before LS generation.",
        },
    }
    c["ordered_points_dxf"] = fallback_ordered
    c["shape_type"] = "slot"
    c["generator_path_dxf"] = {
        "type": "fanuc_mixed_segments",
        "motion_type": "fanuc_mixed_segments",
        "shape_type": "slot",
        "coordinate_stage": "dxf_global_before_uf_transform",
        "generator_must_not_reconstruct_geometry": True,
        "generator_must_not_calculate_cut_in": True,
        "pre_laser_point_dxf": _round_point(cut_in),
        "laser_on_point_dxf": _round_point(cut_in),
        "first_cut_point_dxf": _round_point(start),
        "segments": motions,
        "segment_count": len(motions),
        "native_source_segment_count": len(segments),
        "termination_policy": "CNT1_between_segments_FINE_at_close",
        "exit": {
            "from_point_dxf": _round_point(start),
            "vertical_lift_mm": 100.0,
            "speed_mm_sec": 100,
            "termination": "FINE",
        },
        "fallback": {
            "type": "linear_points_fallback",
            "cut_points_dxf": fallback_ordered,
            "reason_if_used": "native_slot_contract_invalid_or_generator_contract_invalid",
        },
        "must_close": True,
        "close_to_start": True,
    }
    return start


def _enrich_inner_contour(contour: Dict[str, Any], idx: int, robot_programming: Dict[str, Any], motion_map: Dict[str, Any]) -> Dict[str, Any]:
    c = copy.deepcopy(contour)
    points = _contour_points(c)
    start = _apply_slot_arc_contract(c, robot_programming)
    if start is None:
        # Cama B: entrada diagonal interna desde menor X y menor Y.
        # El cut-in se coloca +2 mm en X y +2 mm en Y respecto al punto de inicio.
        start = _choose_point(points, "dxf_min_x_min_y")
        ordered = _order_closed_points_from_start(points, start, prefer_next="dxf_decreasing_x")
        cut_in = _diagonal_cut_in_point_dxf(start, dx_mm=2.0, dy_mm=2.0) if start else None
        start_uf2 = _to_uf2(start, robot_programming) if start else None
        cut_in_uf2 = _to_uf2(cut_in, robot_programming) if cut_in else None
        cut_in_inside = _point_in_polygon(cut_in, points) if cut_in else None
        c["entry"] = {
            "strategy": "inner_min_x_min_y_diagonal_2mm_x_2mm_y",
            "policy_id": "R3_B_240X96_INNER_CUT_ENTRY_V1",
            "coordinate_stage": "dxf_global_before_uf_transform",
            "requires_cut_in": True,
            "cut_in_mm": _round_num(math.hypot(2.0, 2.0)),
            "cut_in_type": "diagonal",
            "cut_in_dx_mm": 2.0,
            "cut_in_dy_mm": 2.0,
            "cut_in_angle_deg": 45.0,
            "start_point_dxf": _round_point(start) if start else None,
            "cut_in_point_dxf": _round_point(cut_in) if cut_in else None,
            "first_move_vector_dxf": [-0.7071, -0.7071],
            "first_move_vector_uf2": [-0.7071, 0.7071],
            "start_point_uf2_preview": _round_point(start_uf2) if start_uf2 else None,
            "cut_in_point_uf2_preview": _round_point(cut_in_uf2) if cut_in_uf2 else None,
            "cut_in_validation": {
                "inside_waste_polygon": cut_in_inside,
                "validation_method": "point_in_polygon_dxf",
                "warning_if_false": "Review inner cut entry before LS generation.",
            },
        }
        c["ordered_points_dxf"] = ordered
    posture_sample = [start[0], min(p[1] for p in points)] if (start and points) else start
    preview = _motion_preview_optim(posture_sample, points, "inner_contour", robot_programming, motion_map)
    c_e1_mode = preview.get("e1_mode") or "fixed"
    c["motion_mapping"] = {
        **preview,
        "e1_mode": c_e1_mode,
        "e1_scope": "single_inner" if c_e1_mode == "fixed" else "path",
        "e1_value": preview.get("e1_preview") if c_e1_mode == "fixed" else None,
        "individual_e1_fixed": c_e1_mode == "fixed",
    }
    c["inner_id"] = "I{0:02d}".format(idx)
    return c


def _enrich_cut_outer_step(step: Dict[str, Any], robot_programming: Dict[str, Any], motion_map: Dict[str, Any]) -> Dict[str, Any]:
    step = copy.deepcopy(step)
    contour = copy.deepcopy((step.get("geometry") or {}).get("contour") or {})
    points = _contour_points(contour)
    start = _choose_point(points, "dxf_min_y_max_x")
    ordered = _order_closed_points_from_start(points, start, prefer_next="dxf_decreasing_x")
    # Cama B: corte exterior inicia en mayor X y menor Y; cut-in se deja -3 mm en Y_DXF
    # para que el pierce quede en el retal (por debajo del borde inferior de la pieza).
    cut_in = [start[0], start[1] - 3.0] if start else None
    start_uf2 = _to_uf2(start, robot_programming) if start else None
    cut_in_uf2 = _to_uf2(cut_in, robot_programming) if cut_in else None
    entry = {
        "strategy": "outer_dxf_min_y_max_x",
        "policy_id": "R3_B_240X96_OUTER_CUT_ENTRY_V2",
        "coordinate_stage": "dxf_global_before_uf_transform",
        "requires_cut_in": True,
        "cut_in_mm": 3.0,
        "start_point_dxf": _round_point(start) if start else None,
        "cut_in_point_dxf": _round_point(cut_in) if cut_in else None,
        # cut_in -> start avanza +Y_DXF; en UF-2 eso es +X_UF2 (X_UF2 = Y_DXF).
        "first_move_vector_dxf": [0.0, 1.0],
        "first_move_vector_uf2": [1.0, 0.0],
        "start_point_uf2_preview": _round_point(start_uf2) if start_uf2 else None,
        "cut_in_point_uf2_preview": _round_point(cut_in_uf2) if cut_in_uf2 else None,
    }
    contour["ordered_points_dxf"] = ordered
    step["entry"] = entry
    step["geometry"] = {
        "type": "closed_contour",
        "source_points_dxf": points,
        "ordered_points_dxf": ordered,
        "start_point_dxf": _round_point(start) if start else None,
        "contour": contour,
        "closed": True,
        "was_reordered": True,
        "ordered_by": "outer_dxf_min_y_max_x_then_decreasing_x",
    }
    preview = _motion_preview_optim(start, points, "cut_outer", robot_programming, motion_map)
    step["motion_mapping"] = {**preview, "e1_mode": "follow_y", "e1_preview_at_entry": preview.get("e1_preview"), "j2_preview_at_entry": preview.get("j2_preview")}
    _attach_ls_generation(step)
    step["order_key"] = {"phase": "cut", "type_priority": 3, "sort_mode": "cut_outer_always_last"}
    step["path_rules"] = {"must_close": True, "close_to_start": True, "is_open_path": False, "allow_inserted_start_point": True}
    return step


# ---------------------------------------------------------------------------
# Orden/stitch/validación
# ---------------------------------------------------------------------------


def _mark_sort_key(step: Dict[str, Any]) -> Tuple[float, float, str]:
    # Cama B MARK: derecha -> izquierda físico, en DXF = X descendente.
    ok = step.get("order_key") or {}
    x = ok.get("sort_x_dxf")
    y = ok.get("sort_y_dxf")
    return (-float(x if x is not None else 0.0), float(y if y is not None else 0.0), str(step.get("path_name") or ""))


def _sort_cut_steps(cut: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key(step: Dict[str, Any]) -> Tuple[int, float, float, str]:
        op = step.get("op")
        pri = {"holes": 1, "cut_inner": 2, "cut_outer": 3}.get(str(op), 9)
        ok = step.get("order_key") or {}
        if op == "holes":
            x = ok.get("primary_x_dxf")
            y = ok.get("secondary_y_dxf")
        else:
            x = ok.get("primary_x_dxf", ok.get("secondary_x_dxf"))
            y = ok.get("secondary_y_dxf", ok.get("primary_y_dxf"))
        return (pri, float(x if x is not None else 0.0), float(y if y is not None else 0.0), str(step.get("path_name") or ""))
    return sorted(cut, key=key)

def _stitch_plan_from_pieces(pieces_by_id: Dict[str, Dict[str, Any]], piece_order_mark: List[str], piece_order_cut: List[str]) -> Dict[str, Any]:
    mark_steps: List[Dict[str, Any]] = []
    cut_steps: List[Dict[str, Any]] = []
    for piece_id in piece_order_mark:
        piece = pieces_by_id.get(piece_id) or {}
        for step in ((piece.get("local_plan") or {}).get("mark") or []):
            g = copy.deepcopy(step)
            g["step"] = len(mark_steps) + 1
            g["phase"] = "mark"
            g["piece_id"] = piece_id
            mark_steps.append(g)
    for piece_id in piece_order_cut:
        piece = pieces_by_id.get(piece_id) or {}
        for step in ((piece.get("local_plan") or {}).get("cut") or []):
            g = copy.deepcopy(step)
            g["step"] = len(cut_steps) + 1
            g["phase"] = "cut"
            g["piece_id"] = piece_id
            cut_steps.append(g)
    return {"mark": mark_steps, "cut": cut_steps, "all": mark_steps + cut_steps}


def _build_summary(pieces: List[Dict[str, Any]], plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "piece_count": len(pieces),
        "pieces_with_mark_text": sum(1 for p in pieces if (p.get("flags") or {}).get("has_mark_text")),
        "pieces_with_mark_figures": sum(1 for p in pieces if (p.get("flags") or {}).get("has_mark_figures")),
        "pieces_with_holes": sum(1 for p in pieces if (p.get("flags") or {}).get("has_holes")),
        "pieces_with_cut_inner": sum(1 for p in pieces if (p.get("flags") or {}).get("has_cut_inner")),
        "hole_fanuc_two_arcs_count": sum(
            1
            for step in (plan.get("cut") or [])
            if step.get("op") == "holes"
            for contour in ((step.get("geometry") or {}).get("ordered_contours_dxf") or [])
            if str(((contour.get("generator_path_dxf") or {}).get("motion_type") or "")) == "fanuc_two_arcs"
        ),
        "hole_linear_fallback_count": sum(
            1
            for step in (plan.get("cut") or [])
            if step.get("op") == "holes"
            for contour in ((step.get("geometry") or {}).get("ordered_contours_dxf") or [])
            if str(((contour.get("generator_path_dxf") or {}).get("motion_type") or "")) == "linear_points_fallback"
        ),
        "slot_fanuc_mixed_segments_count": sum(
            1
            for step in (plan.get("cut") or [])
            if step.get("op") == "cut_inner"
            for contour in ((step.get("geometry") or {}).get("ordered_contours_dxf") or [])
            if str(((contour.get("generator_path_dxf") or {}).get("motion_type") or "")) == "fanuc_mixed_segments"
        ),
        "slot_linear_fallback_count": sum(
            1
            for step in (plan.get("cut") or [])
            if step.get("op") == "cut_inner"
            for contour in ((step.get("geometry") or {}).get("ordered_contours_dxf") or [])
            if (str(contour.get("shape_type") or "").lower() == "slot"
                and str(((contour.get("generator_path_dxf") or {}).get("motion_type") or "")) != "fanuc_mixed_segments")
        ),
        "mark_step_count": len(plan.get("mark") or []),
        "cut_step_count": len(plan.get("cut") or []),
        "all_step_count": len(plan.get("all") or []),
    }


def _validate_ls_ready_v3(data: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    rp = data.get("robot_programming") or {}
    width = float(rp.get("sheet_width_transform_mm") or rp.get("sheet_width_mm") or 0.0)
    height = float(rp.get("sheet_height_transform_mm") or rp.get("sheet_height_mm") or 0.0)
    for phase in ("mark", "cut"):
        for step in (data.get("plan") or {}).get(phase) or []:
            label = "{0}:{1}:{2}".format(phase, step.get("piece_id"), step.get("path_name"))
            if not step.get("geometry"):
                errors.append(label + " missing geometry")
            entry = step.get("entry") or {}
            if not entry:
                errors.append(label + " missing entry")
                continue
            if phase == "cut" and entry.get("requires_cut_in") and not entry.get("cut_in_point_dxf"):
                errors.append(label + " missing cut_in_point_dxf")
            if phase == "mark" and entry.get("requires_cut_in"):
                errors.append(label + " mark should not require cut_in")
            for key in ("start_point_dxf", "cut_in_point_dxf"):
                pt = entry.get(key)
                if pt and len(pt) >= 2:
                    x, y = float(pt[0]), float(pt[1])
                    if width and not (-10.0 <= x <= width + 10.0):
                        warnings.append(label + " {0} x outside transform area: {1}".format(key, round(x, 4)))
                    if height and not (-10.0 <= y <= height + 10.0):
                        warnings.append(label + " {0} y outside transform area: {1}".format(key, round(y, 4)))
            if step.get("op") == "holes":
                for contour in ((step.get("geometry") or {}).get("ordered_contours_dxf") or []):
                    cin = ((contour.get("entry") or {}).get("cut_in_validation") or {}).get("inside_hole_polygon")
                    if cin is False:
                        warnings.append(label + " hole cut_in_point_dxf not inside hole contour; review before LS generation")
                    gp = contour.get("generator_path_dxf") or {}
                    if gp.get("generator_must_not_calculate_cut_in") is not True:
                        warnings.append(label + " missing generator_path_dxf contract for hole; generator may fallback to legacy cut-in")
                    motion_type = str(gp.get("motion_type") or gp.get("type") or "")
                    if motion_type == "fanuc_two_arcs":
                        cardinal = gp.get("cardinal_points_dxf") or {}
                        if not all(cardinal.get(key) for key in ("x_min", "y_max", "x_max", "y_min")):
                            errors.append(label + " fanuc_two_arcs missing cardinal points")
                        diameter = float(gp.get("diameter_mm") or (2.0 * float(gp.get("circle_radius_mm") or 0.0)))
                        expected_profile = _hole_motion_profile_for_diameter(diameter)
                        if gp.get("motion_profile_id") != expected_profile["profile_id"]:
                            errors.append(label + " hole motion_profile_id does not match diameter")
                        if gp.get("linear_motion_coord") is not FANUC_HOLE_USE_COORD:
                            errors.append(label + " hole linear_motion_coord must be false")
                        line_cfg = gp.get("line_to_start") or {}
                        for key in ("speed_mm_sec", "termination", "use_pth"):
                            if line_cfg.get(key) != expected_profile["line_to_start"][key]:
                                errors.append(label + " hole line_to_start {0} does not match profile".format(key))
                        for arc_key in ("arc_1", "arc_2"):
                            actual_arc = gp.get(arc_key) or {}
                            for key in ("speed_mm_sec", "termination", "use_pth"):
                                if actual_arc.get(key) != expected_profile[arc_key][key]:
                                    errors.append(label + " {0} {1} does not match hole profile".format(arc_key, key))
                    elif motion_type != "linear_points_fallback":
                        errors.append(label + " unsupported hole motion_type: " + motion_type)
            if step.get("op") == "cut_inner":
                for contour_index, contour in enumerate(((step.get("geometry") or {}).get("ordered_contours_dxf") or []), start=1):
                    contour_label = label + ":contour{0}".format(contour_index)
                    cin = ((contour.get("entry") or {}).get("cut_in_validation") or {}).get("inside_waste_polygon")
                    if cin is False:
                        warnings.append(contour_label + " diagonal cut_in_point_dxf not inside inner contour; review before LS generation")
                    gp = contour.get("generator_path_dxf") or {}
                    motion_type = str(gp.get("motion_type") or gp.get("type") or "")
                    slot_expected = ((contour.get("slot_detection") or {}).get("eligible") is True
                                     or str(contour.get("shape_type") or "").lower() == "slot")
                    if motion_type == "fanuc_mixed_segments":
                        if gp.get("generator_must_not_reconstruct_geometry") is not True:
                            errors.append(contour_label + " mixed slot contract must forbid geometry reconstruction")
                        if gp.get("generator_must_not_calculate_cut_in") is not True:
                            errors.append(contour_label + " mixed slot contract must forbid generator cut-in calculation")
                        first = gp.get("first_cut_point_dxf")
                        segments = gp.get("segments") if isinstance(gp.get("segments"), list) else []
                        if not first or not segments:
                            errors.append(contour_label + " fanuc_mixed_segments missing first point or segments")
                        else:
                            previous_end = first
                            for seg_index, segment in enumerate(segments, start=1):
                                if not isinstance(segment, dict):
                                    errors.append(contour_label + " invalid mixed segment {0}".format(seg_index))
                                    continue
                                seg_type = str(segment.get("type") or "").lower()
                                start_pt = segment.get("start_point_dxf")
                                end_pt = segment.get("end_point_dxf")
                                if seg_type not in ("line", "arc"):
                                    errors.append(contour_label + " unsupported mixed segment type: " + seg_type)
                                if not start_pt or not end_pt:
                                    errors.append(contour_label + " mixed segment {0} missing start/end".format(seg_index))
                                    continue
                                if not _same_point(start_pt, previous_end, tol=0.05):
                                    errors.append(contour_label + " mixed segment chain discontinuity at {0}".format(seg_index))
                                expected_term = "FINE" if seg_index == len(segments) else FANUC_SLOT_TERMINATION
                                if str(segment.get("termination") or "") != expected_term:
                                    errors.append(contour_label + " mixed segment {0} termination must be {1}".format(seg_index, expected_term))
                                if seg_type == "arc":
                                    via = segment.get("via_point_dxf")
                                    center = segment.get("center_dxf")
                                    try:
                                        radius = float(segment.get("radius_mm") or 0.0)
                                        sweep = float(segment.get("sweep_deg") or 0.0)
                                    except (TypeError, ValueError):
                                        radius, sweep = 0.0, 0.0
                                    if not via or not center or radius <= 0.0 or not (0.0 < sweep <= 180.001):
                                        errors.append(contour_label + " mixed arc {0} has invalid via/center/radius/sweep".format(seg_index))
                                previous_end = end_pt
                            if not _same_point(previous_end, first, tol=0.05):
                                errors.append(contour_label + " mixed slot path does not close at first_cut_point_dxf")
                            if int(gp.get("segment_count") or -1) != len(segments):
                                errors.append(contour_label + " mixed slot segment_count mismatch")
                    elif slot_expected:
                        errors.append(contour_label + " eligible slot missing fanuc_mixed_segments contract")
            if step.get("op") == "cut_outer":
                rules = step.get("path_rules") or {}
                if not rules.get("must_close"):
                    errors.append(label + " cut_outer must_close false")
    motion_meta = ((data.get("motion_map") or {}).get("meta") or {})
    motion_robot = str(motion_meta.get("robot") or "").upper()
    if motion_robot != "R3":
        errors.append("motion_map meta.robot must be R3; received {0}".format(motion_robot or "empty"))
    if int(rp.get("robot") or 0) != 3:
        errors.append("robot_programming.robot must be 3 for this classifier")

    for phase in ("mark", "cut"):
        for step in (data.get("plan") or {}).get(phase) or []:
            label = "{0}:{1}:{2}".format(phase, step.get("piece_id"), step.get("path_name"))
            lsgen = step.get("ls_generation") or {}
            e1 = lsgen.get("e1") or {}
            if not lsgen:
                warnings.append(label + " missing ls_generation hints for generator")
            if e1.get("use_e1_fixed") is True:
                if e1.get("e1_fixed_mm") is None:
                    errors.append(label + " fixed E1 mode without e1_fixed_mm")
                elif _e1_within_limits(float(e1.get("e1_fixed_mm"))) is False:
                    errors.append(label + " e1_fixed_mm outside robot limits")

    nesting = ((data.get("track_flow") or {}).get("inner_nesting_cut_rule")
               or ((data.get("piece_groups") or {}).get("inner_nesting_cut_rule"))
               or {})
    cut_order = list(((data.get("track_flow") or {}).get("piece_order_cut") or []))
    cut_index = {pid: idx for idx, pid in enumerate(cut_order)}
    for rel in nesting.get("relations") or []:
        child = str(rel.get("child_piece_id") or "")
        parent = str(rel.get("parent_piece_id") or "")
        if child not in cut_index or parent not in cut_index:
            errors.append(
                "inner_nesting_cut_rule missing pieces in cut order: {0} before {1}".format(child, parent)
            )
            continue
        if cut_index[child] >= cut_index[parent]:
            errors.append(
                "inner_nesting_cut_rule violated: {0} must be cut before container {1}".format(child, parent)
            )

    return {"status": "ok" if not errors else "error", "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------
# Geometría helpers
# ---------------------------------------------------------------------------


def _contour_points(contour: Dict[str, Any]) -> List[Point]:
    pts = (contour or {}).get("points_dxf") or (contour or {}).get("points") or (contour or {}).get("ordered_points_dxf") or []
    return [_round_point(p) for p in pts if p is not None and len(p) >= 2]

def _order_strokes_by_target(strokes: List[Dict[str, Any]], target: str) -> Tuple[List[Dict[str, Any]], Optional[Point]]:
    remaining = [copy.deepcopy(s) for s in strokes]
    if not remaining:
        return [], None
    endpoints = []
    for i, s in enumerate(remaining):
        pts = list(s.get("points_dxf") or [])
        if not pts:
            continue
        endpoints.append((i, 0, pts[0]))
        endpoints.append((i, -1, pts[-1]))
    if not endpoints:
        return remaining, None
    chosen_i, chosen_end, start = sorted(endpoints, key=lambda e: _target_sort_key(e[2], target))[0]
    ordered: List[Dict[str, Any]] = []
    current = start
    # Orientar primer stroke.
    first = remaining.pop(chosen_i)
    first = _orient_stroke_to_start(first, chosen_end)
    ordered.append(first)
    pts = first.get("points_dxf") or []
    current = pts[-1] if pts else current
    # Greedy por endpoint más cercano.
    while remaining:
        best = None
        for i, s in enumerate(remaining):
            pts = s.get("points_dxf") or []
            if not pts:
                continue
            d0 = _dist(current, pts[0])
            d1 = _dist(current, pts[-1])
            cand = (min(d0, d1), i, 0 if d0 <= d1 else -1)
            if best is None or cand < best:
                best = cand
        if best is None:
            ordered.extend(remaining)
            break
        _, i, end = best
        s = remaining.pop(i)
        s = _orient_stroke_to_start(s, end)
        ordered.append(s)
        pts = s.get("points_dxf") or []
        if pts:
            current = pts[-1]
    return ordered, _round_point(start)


def _rotate_figure_strokes_to_target(
    strokes: List[Dict[str, Any]],
    target: str = "dxf_min_y_max_x",
    prefer_next: str = "dxf_increasing_y",
) -> Tuple[List[Dict[str, Any]], Optional[Point]]:
    """Rota figure strokes (sobre todo paths cerrados reconstruidos) al punto verdadero del target.

    `_order_strokes_by_target` solo mira endpoints; en un polyline cerrado first==last
    esa elección no fuerza Y_min. Aquí se rota sobre todos los puntos del stroke.
    No altera coordenadas: solo cambia el índice de inicio y el sentido de recorrido.
    """
    if not strokes:
        return [], None
    out = [copy.deepcopy(s) for s in strokes]
    first = out[0]
    pts = list(first.get("points_dxf") or [])
    if len(pts) < 2:
        start = pts[0] if pts else None
        return out, _round_point(start) if start else None

    closed = _same_point(pts[0], pts[-1])
    body = pts[:-1] if closed and len(pts) > 1 else list(pts)
    start = sorted(body, key=lambda p: _target_sort_key(p, target))[0]
    return _set_figure_stroke_start(out, body, start, closed, prefer_next)


def _order_figure_bottom_right(
    strokes: List[Dict[str, Any]],
    prefer_next: str = "dxf_increasing_y",
) -> Tuple[List[Dict[str, Any]], Optional[Point]]:
    """Inicia la figura en el punto más cercano a la esquina inferior-derecha (X_max, Y_min).

    Así se fuerza arranque abajo y se conserva el lado de X máxima cuando el remate
    de X_max absoluto está arriba (p.ej. GrabC).
    """
    if not strokes:
        return [], None
    ordered, _ = _order_strokes_by_target(strokes, "dxf_max_x_min_y")
    out = [copy.deepcopy(s) for s in ordered]
    first = out[0]
    pts = list(first.get("points_dxf") or [])
    if len(pts) < 2:
        start = pts[0] if pts else None
        return out, _round_point(start) if start else None

    closed = _same_point(pts[0], pts[-1])
    body = pts[:-1] if closed and len(pts) > 1 else list(pts)
    start = min(body, key=lambda p: (round(float(p[1]), 2), -round(float(p[0]), 2)))
    return _set_figure_stroke_start(out, body, start, closed, prefer_next)


def _set_figure_stroke_start(
    strokes: List[Dict[str, Any]],
    body: List[Point],
    start: Sequence[float],
    closed: bool,
    prefer_next: str,
) -> Tuple[List[Dict[str, Any]], Optional[Point]]:
    idx = min(range(len(body)), key=lambda i: _dist(body[i], start))
    forward = body[idx:] + body[:idx]
    reverse = [body[idx]] + list(reversed(body[:idx])) + list(reversed(body[idx + 1 :]))
    if prefer_next == "dxf_increasing_y":
        chosen = _choose_direction_with_next_vector(forward, reverse, desired=(0.0, 1.0))
    elif prefer_next == "dxf_decreasing_y":
        chosen = _choose_direction_with_next_vector(forward, reverse, desired=(0.0, -1.0))
    else:
        chosen = forward
    if closed and chosen and not _same_point(chosen[0], chosen[-1]):
        chosen = chosen + [chosen[0]]
    strokes[0]["points_dxf"] = [_round_point(p) for p in chosen]
    return strokes, _round_point(chosen[0]) if chosen else None


def _orient_stroke_to_start(stroke: Dict[str, Any], chosen_end: int) -> Dict[str, Any]:
    stroke = copy.deepcopy(stroke)
    pts = list(stroke.get("points_dxf") or [])
    if chosen_end == -1:
        pts = list(reversed(pts))
    stroke["points_dxf"] = [_round_point(p) for p in pts]
    return stroke


def _target_sort_key(pt: Sequence[float], target: str) -> Tuple[float, float]:
    x, y = float(pt[0]), float(pt[1])
    if target == "dxf_max_x_max_y":
        return (-x, -y)
    if target == "dxf_max_x_min_y":
        return (-x, y)
    if target == "dxf_min_x_max_y":
        return (x, -y)
    if target == "dxf_min_x_min_y":
        return (x, y)
    if target == "dxf_min_y_max_x":
        # Primero Y mínima (abajo), luego X máxima (derecha).
        return (y, -x)
    return (x, y)


def _choose_point(points: Sequence[Sequence[float]], mode: str) -> Optional[Point]:
    pts = [_round_point(p) for p in points or []]
    if not pts:
        return None
    if mode == "dxf_min_x":
        return min(pts, key=lambda p: (float(p[0]), float(p[1])))
    if mode == "dxf_min_x_min_y":
        return min(pts, key=lambda p: (float(p[0]), float(p[1])))
    if mode == "dxf_min_x_max_y":
        return min(pts, key=lambda p: (float(p[0]), -float(p[1])))
    if mode == "dxf_max_x_min_y":
        return max(pts, key=lambda p: (float(p[0]), -float(p[1])))
    if mode == "dxf_max_x_max_y":
        return max(pts, key=lambda p: (float(p[0]), float(p[1])))
    if mode == "dxf_min_y_max_x":
        return min(pts, key=lambda p: (float(p[1]), -float(p[0])))
    return pts[0]


def _order_closed_points_from_start(points: Sequence[Sequence[float]], start: Optional[Sequence[float]], prefer_next: str = "continue_original") -> List[Point]:
    pts = [_round_point(p) for p in points or []]
    if not pts or start is None:
        return pts
    # Remover cierre duplicado para rotar limpio.
    if len(pts) > 1 and _same_point(pts[0], pts[-1]):
        pts = pts[:-1]
    idx = min(range(len(pts)), key=lambda i: _dist(pts[i], start))
    forward = pts[idx:] + pts[:idx]
    reverse = [pts[idx]] + list(reversed(pts[:idx])) + list(reversed(pts[idx + 1 :]))
    chosen = forward
    if prefer_next == "dxf_decreasing_y":
        chosen = _choose_direction_with_next_vector(forward, reverse, desired=(0.0, -1.0))
    elif prefer_next == "dxf_increasing_y":
        chosen = _choose_direction_with_next_vector(forward, reverse, desired=(0.0, 1.0))
    elif prefer_next == "dxf_decreasing_x":
        chosen = _choose_direction_with_next_vector(forward, reverse, desired=(-1.0, 0.0))
    elif prefer_next == "dxf_increasing_x":
        chosen = _choose_direction_with_next_vector(forward, reverse, desired=(1.0, 0.0))
    if chosen and not _same_point(chosen[0], chosen[-1]):
        chosen = chosen + [chosen[0]]
    return [_round_point(p) for p in chosen]


def _choose_direction_with_next_vector(a: List[Point], b: List[Point], desired: Tuple[float, float]) -> List[Point]:
    def score(seq: List[Point]) -> float:
        if len(seq) < 2:
            return -999.0
        vx = float(seq[1][0]) - float(seq[0][0])
        vy = float(seq[1][1]) - float(seq[0][1])
        mag = math.hypot(vx, vy)
        if mag < 1e-9:
            return -999.0
        return (vx / mag) * desired[0] + (vy / mag) * desired[1]
    return a if score(a) >= score(b) else b


def _diagonal_cut_in_point_dxf(start: Optional[Sequence[float]], dx_mm: float = 2.0, dy_mm: float = 2.0) -> Optional[Point]:
    if start is None:
        return None
    return [_round_num(float(start[0]) + float(dx_mm)), _round_num(float(start[1]) + float(dy_mm))]


def _flatten_ordered_strokes_points(strokes: Sequence[Dict[str, Any]]) -> List[Point]:
    points: List[Point] = []
    for stroke in strokes or []:
        for pt in (stroke.get("points_dxf") or stroke.get("points") or []):
            if pt is None or len(pt) < 2:
                continue
            rp = _round_point(pt)
            if points and _same_point(points[-1], rp, tol=0.01):
                continue
            points.append(rp)
    return points


def _point_in_polygon(pt: Optional[Sequence[float]], polygon: Sequence[Sequence[float]]) -> Optional[bool]:
    if pt is None:
        return None
    pts = [_round_point(p) for p in polygon or []]
    if len(pts) < 3:
        return None
    if _same_point(pts[0], pts[-1]):
        pts = pts[:-1]
    x, y = float(pt[0]), float(pt[1])
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = float(pts[i][0]), float(pts[i][1])
        xj, yj = float(pts[j][0]), float(pts[j][1])
        # Punto sobre borde: aceptarlo como válido.
        if _point_on_segment((x, y), (xi, yi), (xj, yj)):
            return True
        intersects = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi)
        if intersects:
            inside = not inside
        j = i
    return inside


def _point_on_segment(pt: Tuple[float, float], a: Tuple[float, float], b: Tuple[float, float], tol: float = 1e-6) -> bool:
    px, py = pt
    ax, ay = a
    bx, by = b
    cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
    if abs(cross) > tol:
        return False
    dot = (px - ax) * (bx - ax) + (py - ay) * (by - ay)
    if dot < -tol:
        return False
    sq_len = (bx - ax) ** 2 + (by - ay) ** 2
    if dot - sq_len > tol:
        return False
    return True


def _empty_cut_entry(strategy: str) -> Dict[str, Any]:
    return {
        "strategy": strategy,
        "coordinate_stage": "dxf_global_before_uf_transform",
        "requires_cut_in": True,
        "cut_in_mm": 3.0,
        "start_point_dxf": None,
        "cut_in_point_dxf": None,
    }


def _to_uf2(pt: Sequence[float], robot_programming: Dict[str, Any]) -> Point:
    """DXF global -> UF-2 cama B.

    X_UF2 = Y_DXF
    Y_UF2 = sheet_width - X_DXF

    El perfil 240x96 fija margin_mm=0; normalización y entradas ya vienen resueltas.
    """
    width = float(robot_programming.get("sheet_width_mm") or 0.0)
    margin = float(robot_programming.get("margin_mm") if robot_programming.get("margin_mm") is not None else UF2_MARGIN_MM)
    return [_round_num(float(pt[1]) + margin), _round_num(width - float(pt[0]) - margin)]


def _bbox(piece: Dict[str, Any]) -> List[float]:
    bb = piece.get("bbox_dxf") or [0.0, 0.0, 0.0, 0.0]
    return [float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])]


def _center_of_points(points: Sequence[Sequence[float]]) -> Optional[Point]:
    pts = list(points or [])
    if not pts:
        return None
    return [sum(float(p[0]) for p in pts) / len(pts), sum(float(p[1]) for p in pts) / len(pts)]


def _same_point(a: Sequence[float], b: Sequence[float], tol: float = 1e-4) -> bool:
    return abs(float(a[0]) - float(b[0])) <= tol and abs(float(a[1]) - float(b[1])) <= tol


def _dist(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _round_point(pt: Optional[Sequence[float]]) -> Optional[Point]:
    if pt is None:
        return None
    return [_round_num(float(pt[0])), _round_num(float(pt[1]))]


def _round_num(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return round(float(v), 4)
