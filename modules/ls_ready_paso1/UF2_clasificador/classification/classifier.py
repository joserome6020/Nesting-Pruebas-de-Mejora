# -*- coding: utf-8 -*-
"""
Orquestador: JSON crudo -> plan clasificado por piezas (CARLOS RPA).

Flujo:
  1. Filtrar cut_outer (placa + franjas borde)
  2. Crear piezas A..Z para cama B (mark der->izq en DXF)
  3. Asignar mark / cut_inner a cada pieza
  4. build_piece_plan() por pieza -> local_plan
  5. stitch_global_plan() -> plan.mark (X decreciente) / plan.cut (X creciente), exclusivo cama B/UF-2
"""
import copy
import json
import os
import sys
from datetime import datetime

_CARLOS_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
_CONFIG_DIR = os.path.join(_CARLOS_ROOT, "config")
if _CONFIG_DIR not in sys.path:
    sys.path.insert(0, _CONFIG_DIR)

import classification_config as cfg

from .plate_profile import load_runtime_config
from .assignment import assign_geometry_to_piece
from .contours import contour_dict_from_raw, extract_mark_stroke
from .geometry import filter_cut_outer_pieces
from .holes import classify_inner_contour
from .naming import index_to_piece_id, outer_name
from .ordering import sort_pieces_for_cut, sort_pieces_for_mark
from .piece_plan import build_piece_plan
from .plan import stitch_global_plan
from .ls_ready_v3 import apply_ls_ready_v3
from .nesting_normalization import normalize_nesting_geometry


PROGRAMMING_WIDTH_MM = 6096.0
PROGRAMMING_HEIGHT_MM = 2438.0
PROGRAMMING_PROFILE_KEY = "240X96"
PLATE_SOURCE_LAYERS = {"PLATE", "SHEET", "LAMINA", "LÁMINA", "PLACA", "BORDER", "MARCO_PLACA"}


def _new_piece_shell(idx, raw_outer):
    contour = contour_dict_from_raw(raw_outer, idx)
    bb = contour.get("bbox_dxf") or [0, 0, 0, 0]
    piece_id = index_to_piece_id(idx)
    return {
        "id": piece_id,
        "source_index": idx,
        "bbox_dxf": bb,
        "center_dxf": contour.get("center_dxf"),
        "max_x": float(bb[2]),
        "cut_outer": {
            "name": outer_name(piece_id),
            "contour": contour,
            "source_index": idx,
        },
        "hole_candidates": [],
        "inner_contours": [],
        "assignment_warnings": [],
    }


def _detected_sheet_snapshot(sheet):
    """Conserva el tamaño leído del DXF sin convertirlo en condición del flujo."""
    existing = sheet.get("detected_nesting")
    if isinstance(existing, dict) and existing:
        return copy.deepcopy(existing)

    source_layer = str(sheet.get("source_layer") or "").upper()
    source = "plate" if source_layer in PLATE_SOURCE_LAYERS else "bbox_estimate"
    return {
        "source": source,
        "source_layer": sheet.get("source_layer"),
        "dimensions_confirmed": source == "plate",
        "width_mm": float(sheet.get("width_mm") or 0.0),
        "height_mm": float(sheet.get("height_mm") or 0.0),
        "plate_size_in": sheet.get("plate_size_in"),
        "scene_size_key": sheet.get("scene_size_key"),
        "bbox_dxf": list(sheet.get("bbox") or []),
    }


def _normalize_meta_for_ls(meta):
    """Separa tamaño detectado de la envolvente física de programación.

    Cualquier DXF puede entrar al flujo siempre que el nesting útil quepa dentro
    de 6096 x 2438 mm. El PLATE o bbox detectado queda únicamente informativo.
    """
    meta = dict(meta or {})
    sheet = dict(meta.get("sheet") or {})
    detected = _detected_sheet_snapshot(sheet)

    sheet["detected_nesting"] = detected
    sheet["dimensions_confirmed"] = bool(detected.get("dimensions_confirmed"))
    sheet["width_mm_original"] = detected.get("width_mm")
    sheet["height_mm_original"] = detected.get("height_mm")
    sheet["plate_size_in_original"] = detected.get("plate_size_in")
    sheet["scene_size_key_original"] = detected.get("scene_size_key")
    sheet["bbox_original_dxf"] = list(detected.get("bbox_dxf") or [])
    sheet["programming_width_mm"] = PROGRAMMING_WIDTH_MM
    sheet["programming_height_mm"] = PROGRAMMING_HEIGHT_MM
    sheet["programming_profile_key"] = PROGRAMMING_PROFILE_KEY
    sheet["size_role"] = "programming_envelope; detected_nesting is informational"

    # Estos campos siguen alimentando transformaciones y el perfil 240x96.
    sheet["width_mm"] = PROGRAMMING_WIDTH_MM
    sheet["height_mm"] = PROGRAMMING_HEIGHT_MM
    sheet["plate_size_in"] = "240x96"
    sheet["scene_size_key"] = PROGRAMMING_PROFILE_KEY
    bbox = list(sheet.get("bbox") or detected.get("bbox_dxf") or [])
    if len(bbox) == 4:
        bbox[2] = float(bbox[0]) + PROGRAMMING_WIDTH_MM
        bbox[3] = float(bbox[1]) + PROGRAMMING_HEIGHT_MM
        sheet["bbox"] = bbox
    meta["sheet"] = sheet

    scene = dict(meta.get("scene") or {})
    if scene.get("scene_size_key") and str(scene.get("scene_size_key")).upper().replace(" ", "") != PROGRAMMING_PROFILE_KEY:
        scene["scene_size_key_original"] = scene.get("scene_size_key")
    scene["scene_size_key"] = PROGRAMMING_PROFILE_KEY
    original_bed = scene.get("cama")
    if original_bed and str(original_bed).upper() != "B":
        scene["cama_original"] = original_bed
    original_line = scene.get("laser_line")
    if original_line and str(original_line).upper() != "L3":
        scene["laser_line_original"] = original_line
    scene["cama"] = "B"
    scene["laser_line"] = "L3"
    meta["scene"] = scene
    return meta


def _sheet_info_for_plate_filter(sheet_info):
    """Usa el PLATE real solo para excluir su contorno del conjunto de piezas."""
    sheet = dict(sheet_info or {})
    detected = sheet.get("detected_nesting") or {}
    if detected.get("source") != "plate":
        # Sin PLATE no se debe interpretar el bbox inferido como contorno de placa
        # ni como sobrante de borde; toda la geometría útil se conserva.
        return None
    filtered = dict(sheet)
    filtered["width_mm"] = float(detected.get("width_mm") or 0.0)
    filtered["height_mm"] = float(detected.get("height_mm") or 0.0)
    filtered["plate_size_in"] = detected.get("plate_size_in")
    filtered["scene_size_key"] = detected.get("scene_size_key")
    filtered["bbox"] = list(detected.get("bbox_dxf") or [])
    return filtered


def _build_nesting_size_info(meta, normalization):
    sheet = (meta or {}).get("sheet") or {}
    detected = sheet.get("detected_nesting") or {}
    useful_width = float((normalization or {}).get("useful_width_mm") or 0.0)
    useful_height = float((normalization or {}).get("useful_height_mm") or 0.0)
    source = detected.get("source")
    if source == "plate":
        reported_width = float(detected.get("width_mm") or 0.0)
        reported_height = float(detected.get("height_mm") or 0.0)
        reported_source = "plate"
    else:
        reported_width = useful_width
        reported_height = useful_height
        reported_source = "normalization_bbox"
    normalized_bbox = list((normalization or {}).get("normalized_useful_bbox_dxf") or [])
    fits = (
        useful_width <= PROGRAMMING_WIDTH_MM + 1e-6
        and useful_height <= PROGRAMMING_HEIGHT_MM + 1e-6
        and (
            len(normalized_bbox) != 4
            or (
                normalized_bbox[0] >= -1e-6
                and normalized_bbox[1] >= -1e-6
                and normalized_bbox[2] <= PROGRAMMING_WIDTH_MM + 1e-6
                and normalized_bbox[3] <= PROGRAMMING_HEIGHT_MM + 1e-6
            )
        )
    )
    return {
        "informational_only": True,
        "reported_source": reported_source,
        "width_mm": round(reported_width, 4),
        "height_mm": round(reported_height, 4),
        "detected_plate_size_in": detected.get("plate_size_in"),
        "detected_scene_size_key": detected.get("scene_size_key"),
        "dimensions_confirmed_by_plate": bool(detected.get("dimensions_confirmed")),
        "detected_bbox_dxf": list(detected.get("bbox_dxf") or []),
        "useful_bbox_width_mm": round(useful_width, 4),
        "useful_bbox_height_mm": round(useful_height, 4),
        "normalized_useful_bbox_dxf": normalized_bbox,
        "programming_envelope_width_mm": PROGRAMMING_WIDTH_MM,
        "programming_envelope_height_mm": PROGRAMMING_HEIGHT_MM,
        "fits_programming_envelope": bool(fits),
    }


def _robot_number_from_meta(meta):
    scene = (meta or {}).get("scene") or {}
    laser_line = str(scene.get("laser_line") or "").upper().strip()
    if laser_line.startswith("L") and laser_line[1:].isdigit():
        return int(laser_line[1:])
    return None


def _uframe_from_bed(_bed=None):
    """Clasificador exclusivo cama B: siempre UF-2."""
    return 2


def _build_robot_programming(meta, runtime_cfg):
    sheet = (meta or {}).get("sheet") or {}
    scene = (meta or {}).get("scene") or {}

    width = float(sheet.get("width_mm") or 0.0)
    height = float(sheet.get("height_mm") or 0.0)
    margin = float(getattr(runtime_cfg, "LS_MARGIN_MM", 3.0))
    bed = "B"
    uframe = 2

    return {
        "robot": 3,
        "bed": bed,
        "uframe": uframe,
        "utool": int(getattr(runtime_cfg, "DEFAULT_UTOOL_NUM", 1)),
        "target_frame": "UF2",
        "coord_source": "DXF_GLOBAL_SHEET_MM",
        "coord_stage": "before_uf2_transform",
        "sheet_width_mm": round(width, 4),
        "sheet_height_mm": round(height, 4),
        "margin_mm": margin,
        "sheet_width_transform_mm": round(width - margin, 4),
        "sheet_height_transform_mm": round(height, 4),
        "transform": {
            "x_uf2": "y_dxf + margin_mm",
            "y_uf2": "sheet_width_mm - x_dxf - margin_mm",
        },
        "notes": [
            "Clasificador exclusivo para robot R3 / cama B / UF-2.",
            "El JSON conserva geometria en DXF/global de placa.",
            "El generador LS UF-2 convierte con X_UF2 = Y_DXF + margin y Y_UF2 = sheet_width - X_DXF - margin.",
            "Para 240x96 el alto normalizado de programacion es 2438.0 mm.",
            "No cambiar herramienta: UTOOL/UT se conservan; solo cambia User Frame.",
        ],
    }


def classify_json_data(raw_data, source_path="", logs=None):
    logs = logs if logs is not None else []
    data = copy.deepcopy(raw_data or {})
    meta = _normalize_meta_for_ls(data.get("meta") or {})
    sheet_info = meta.get("sheet")
    plate_filter_sheet_info = _sheet_info_for_plate_filter(sheet_info)

    runtime_cfg, plate_profile = load_runtime_config(meta, logs=logs)
    flow = plate_profile.get("flow") or {}

    cut_outer_raw = filter_cut_outer_pieces(
        data.get("cut_outer") or [],
        plate_filter_sheet_info,
        tol_mm=runtime_cfg.SHEET_PLATE_MATCH_TOL_MM,
        logs=logs,
        filter_edge_strips=bool(getattr(runtime_cfg, "PLATE_EDGE_STRIP_FILTER", True)),
        edge_touch_tol_mm=float(getattr(runtime_cfg, "PLATE_EDGE_TOUCH_TOL_MM", 5.0)),
        max_strip_thickness_mm=float(
            getattr(runtime_cfg, "PLATE_EDGE_MAX_STRIP_THICKNESS_MM", 400.0)
        ),
        min_edge_span_ratio=float(getattr(runtime_cfg, "PLATE_EDGE_MIN_SPAN_RATIO", 0.80)),
        max_corner_remnant_width_mm=float(
            getattr(runtime_cfg, "PLATE_CORNER_REMNANT_MAX_WIDTH_MM", 1200.0)
        ),
        max_corner_remnant_height_mm=float(
            getattr(runtime_cfg, "PLATE_CORNER_REMNANT_MAX_HEIGHT_MM", 1600.0)
        ),
    )

    raw_inner = data.get("cut_inner") or []
    raw_mark = data.get("mark") or []
    if bool(getattr(runtime_cfg, "NORMALIZE_NESTING", False)):
        cut_outer_raw, raw_inner, raw_mark, normalization = normalize_nesting_geometry(
            cut_outer_raw,
            raw_inner,
            raw_mark,
            sheet_info,
            margin_mm=float(getattr(runtime_cfg, "NORMALIZATION_MARGIN_MM", 10.0)),
        )
        logs.append(
            "nesting_normalization: dx={0} dy={1} margin={2}".format(
                normalization.get("dx_mm"), normalization.get("dy_mm"), normalization.get("margin_mm")
            )
        )
    else:
        normalization = {
            "enabled": False,
            "always_evaluate": False,
            "dx_mm": 0.0,
            "dy_mm": 0.0,
        }

    pieces = [_new_piece_shell(idx, raw) for idx, raw in enumerate(cut_outer_raw)]

    strokes_by_piece = {p["id"]: [] for p in pieces}
    for midx, raw_item in enumerate(raw_mark):
        stroke = extract_mark_stroke(
            raw_item, midx, min_length_mm=runtime_cfg.MIN_STROKE_LENGTH_MM
        )
        if not stroke:
            continue
        pidx, method, warns = assign_geometry_to_piece(
            stroke["points"],
            pieces,
            margin_mm=runtime_cfg.PLATE_ASSIGN_MARGIN_MM,
            majority_ratio=runtime_cfg.POINTS_MAJORITY_RATIO,
        )
        stroke["assignment_method"] = method
        pieces[pidx]["assignment_warnings"].extend(warns)
        strokes_by_piece[pieces[pidx]["id"]].append(stroke)

    for iidx, raw_item in enumerate(raw_inner):
        contour = contour_dict_from_raw(raw_item, iidx)
        if len(contour.get("points") or []) < 2:
            continue
        pidx, method, warns = assign_geometry_to_piece(
            contour["points"],
            pieces,
            margin_mm=runtime_cfg.PLATE_ASSIGN_MARGIN_MM,
            majority_ratio=runtime_cfg.POINTS_MAJORITY_RATIO,
        )
        contour["assignment_method"] = method
        pieces[pidx]["assignment_warnings"].extend(warns)
        kind, circ, aspect = classify_inner_contour(contour, runtime_cfg)
        contour["inner_kind"] = kind
        contour["circularity"] = round(circ, 4)
        contour["aspect"] = round(aspect, 4)
        if kind == "barreno":
            pieces[pidx]["hole_candidates"].append(contour)
        else:
            pieces[pidx]["inner_contours"].append(contour)

    built_pieces = []
    for piece in pieces:
        built = build_piece_plan(
            piece,
            strokes_by_piece.get(piece["id"], []),
            runtime_cfg,
            logs=logs,
        )
        built_pieces.append(built)

    pieces_mark = sort_pieces_for_mark(built_pieces)
    piece_order_mark = [p["id"] for p in pieces_mark]
    piece_order_cut = [p["id"] for p in sort_pieces_for_cut(pieces_mark)]

    pieces_by_id = {p["id"]: p for p in built_pieces}
    for order_mark, piece_id in enumerate(piece_order_mark, start=1):
        pieces_by_id[piece_id]["order_mark"] = order_mark
    for order_cut, piece_id in enumerate(piece_order_cut, start=1):
        pieces_by_id[piece_id]["order_cut"] = order_cut

    plan = stitch_global_plan(pieces_by_id, piece_order_mark, piece_order_cut)
    pieces_out = [pieces_by_id[pid] for pid in piece_order_mark]

    summary = {
        "piece_count": len(pieces_out),
        "pieces_with_mark_text": sum(
            1 for p in pieces_out if p.get("flags", {}).get("has_mark_text")
        ),
        "pieces_with_mark_figures": sum(
            1 for p in pieces_out if p.get("flags", {}).get("has_mark_figures")
        ),
        "pieces_with_holes": sum(
            1 for p in pieces_out if p.get("flags", {}).get("has_holes")
        ),
        "pieces_with_cut_inner": sum(
            1 for p in pieces_out if p.get("flags", {}).get("has_cut_inner")
        ),
        "mark_step_count": len(plan["mark"]),
        "cut_step_count": len(plan["cut"]),
    }

    result = {
        "schema_version": cfg.SCHEMA_VERSION,
        "source": {
            "json_path": os.path.normpath(source_path) if source_path else "",
            "classified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "classifier_root": _CARLOS_ROOT,
        },
        "meta": meta,
        "plate_profile": plate_profile,
        "robot_programming": _build_robot_programming(meta, runtime_cfg),
        "normalization": normalization,
        "nesting_size": _build_nesting_size_info(meta, normalization),
        "summary": summary,
        "track_flow": {
            "mark_direction": flow.get("mark_direction", "right_to_left"),
            "cut_direction": flow.get("cut_direction_when_mark", "left_to_right"),
            "piece_order_mark": piece_order_mark,
            "piece_order_cut": piece_order_cut,
        },
        "pieces": pieces_out,
        "plan": plan,
        "legacy": {
            "cut_outer": cut_outer_raw,
            "cut_inner": raw_inner,
            "mark": raw_mark,
        },
        "classification_log": logs,
    }

    return apply_ls_ready_v3(result, runtime_cfg=runtime_cfg, logs=logs)


def classify_json_file(json_path, output_path=None, logs=None):
    json_path = os.path.normpath(json_path)
    with open(json_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    classified = classify_json_data(raw, source_path=json_path, logs=logs)

    if output_path is None:
        os.makedirs(cfg.CLASSIFIED_JSON_DIR, exist_ok=True)
        base = os.path.splitext(os.path.basename(json_path))[0]
        output_path = os.path.join(cfg.CLASSIFIED_JSON_DIR, base + "_classified.json")
    else:
        os.makedirs(os.path.dirname(os.path.normpath(output_path)), exist_ok=True)

    tmp = output_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(classified, f, indent=2, ensure_ascii=False)
    os.replace(tmp, output_path)
    classified["source"]["classified_json_path"] = os.path.normpath(output_path)
    return classified
