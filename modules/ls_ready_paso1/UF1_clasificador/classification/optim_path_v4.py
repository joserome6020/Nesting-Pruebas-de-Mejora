# -*- coding: utf-8 -*-
"""Resolución de postura V4 para R3 / cama A / UF-1 / placa 240x96.

Responsabilidades:
- Clasificar cada path por dimensiones: max_y, usual_y o min_y_min_x.
- Seleccionar una postura inicial desde los nodos medidos.
- Trasladar E1 desde la columna canónica X=1524 mm.
- Usar la columna 0 como override medido cuando la traslación canónica rebasa
  el límite inferior del track.
- Calcular J2_preview y J2_postview por interpolación lineal por tramos.

El módulo NO cambia geometría, entradas ni orden de paths.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

E1_MIN_MM = -3670.0
E1_MAX_MM = 3670.0
J2_WARNING_DEG = 55.0
J2_PHYSICAL_LIMIT_DEG = 65.0
CANONICAL_X_DXF = 1524.0
NODE_STEP_Y_MM = 304.75


class OptimPathError(ValueError):
    """Error de resolución que vuelve el path no liberable."""


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _node_code(node_id: str) -> str:
    match = re.search(r"\(([^)]+)\)\s*$", str(node_id or ""))
    code = match.group(1).strip() if match else str(node_id or "").strip()
    return re.sub(r"1$", "", code)


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


@dataclass
class LoadedOptimMaps:
    column0_nodes: Dict[str, Dict[str, Any]]
    column1_nodes: Dict[str, Dict[str, Any]]
    progression0: Dict[str, Any]
    progression1: Dict[str, Any]
    source_files: Dict[str, str]


class OptimPathResolverV4:
    def __init__(self, config_dir: str):
        self.config_dir = os.path.normpath(config_dir)
        map0_path = os.path.join(self.config_dir, "Mediciones 240x96 A, mapa V4 COLUMNA 0.txt")
        map1_path = os.path.join(self.config_dir, "Mediciones 240x96 A, mapa V4 COLUMNA 1.txt")
        prog0_path = os.path.join(self.config_dir, "progreso_j2_columna_0.json")
        prog1_path = os.path.join(self.config_dir, "progreso_j2_columna_1.json")

        map0 = _load_json(map0_path)
        map1 = _load_json(map1_path)
        progression0 = _load_json(prog0_path)
        progression1 = _load_json(prog1_path)

        self.maps = LoadedOptimMaps(
            column0_nodes=self._index_nodes(map0),
            column1_nodes=self._index_nodes(map1),
            progression0=progression0,
            progression1=progression1,
            source_files={
                "column0_map": map0_path,
                "column1_map": map1_path,
                "column0_progression": prog0_path,
                "column1_progression": prog1_path,
            },
        )

    @staticmethod
    def _index_nodes(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for node in data.get("puntos exteriores") or []:
            if not isinstance(node, dict):
                continue
            code = _node_code(str(node.get("node_id") or ""))
            if code:
                copied = dict(node)
                copied["node_code"] = code
                out[code] = copied
        return out

    @staticmethod
    def classify_geometry(width_mm: float, height_mm: float) -> str:
        width = abs(float(width_mm))
        height = abs(float(height_mm))
        if width <= 800.0 and height <= 1000.0:
            return "min_y_min_x"
        if height >= 1700.0:
            return "max_y"
        return "usual_y"

    def resolve(
        self,
        *,
        profile: str,
        entry_x_dxf: float,
        entry_y_dxf: float,
        path_y_motion_limit_dxf: float,
        path_id: str = "",
    ) -> Dict[str, Any]:
        profile = str(profile or "").strip()
        entry_x = float(entry_x_dxf)
        entry_y = float(entry_y_dxf)
        limit_y = float(path_y_motion_limit_dxf)
        issues: List[str] = []

        if profile not in ("max_y", "usual_y", "min_y_min_x"):
            return self._invalid_result(
                profile, entry_x, entry_y, limit_y, "unsupported_profile", path_id
            )
        if limit_y > entry_y + 0.05:
            return self._invalid_result(
                profile,
                entry_x,
                entry_y,
                limit_y,
                "path_motion_direction_must_be_dxf_decreasing",
                path_id,
            )

        node_code = self._select_reference_node(profile, entry_y)
        if node_code is None:
            return self._invalid_result(
                profile, entry_x, entry_y, limit_y, "no_enabled_reference_node", path_id
            )

        canonical_node = self.maps.column1_nodes[node_code]
        canonical_profile = (canonical_node.get("profiles") or {}).get(profile) or {}
        canonical_e1 = canonical_profile.get("E1_mm")
        if not _is_number(canonical_e1):
            return self._invalid_result(
                profile, entry_x, entry_y, limit_y, "canonical_e1_missing", path_id
            )

        selected_column = 1
        selected_node = canonical_node
        selected_profile = canonical_profile
        selected_progression = self.maps.progression1
        e1_value = float(canonical_e1) + (entry_x - CANONICAL_X_DXF)
        e1_resolution = "canonical_column_1_translation"
        override_applied = False

        if not (E1_MIN_MM <= e1_value <= E1_MAX_MM):
            # Override medido de columna 0. Al desplazar desde X=0 hacia X real,
            # E1 cambia la misma cantidad positiva que X_DXF.
            override_node = self.maps.column0_nodes.get(node_code)
            override_profile = ((override_node or {}).get("profiles") or {}).get(profile) or {}
            override_e1 = override_profile.get("E1_mm")
            if override_node and override_profile.get("enabled") is True and _is_number(override_e1):
                candidate = float(override_e1) + entry_x
                if E1_MIN_MM <= candidate <= E1_MAX_MM:
                    selected_column = 0
                    selected_node = override_node
                    selected_profile = override_profile
                    selected_progression = self.maps.progression0
                    e1_value = candidate
                    e1_resolution = "column_0_measured_override_plus_x_translation"
                    override_applied = True
                    issues.append("canonical_e1_out_of_limits_column0_override_used")
                else:
                    return self._invalid_result(
                        profile,
                        entry_x,
                        entry_y,
                        limit_y,
                        "e1_out_of_limits_even_with_column0_override",
                        path_id,
                        calculated_e1=e1_value,
                        override_e1=candidate,
                    )
            else:
                return self._invalid_result(
                    profile,
                    entry_x,
                    entry_y,
                    limit_y,
                    "e1_out_of_limits_and_no_measured_override",
                    path_id,
                    calculated_e1=e1_value,
                )

        row_model = (
            ((selected_progression.get("profiles") or {}).get(profile) or {}).get(node_code)
        )
        if not isinstance(row_model, dict):
            return self._invalid_result(
                profile,
                entry_x,
                entry_y,
                limit_y,
                "j2_progression_row_missing",
                path_id,
                reference_node=node_code,
            )

        samples = list(row_model.get("samples") or [])
        preview = self._interpolate(samples, entry_y)
        at_limit = self._interpolate(samples, limit_y)
        if preview is None or at_limit is None:
            return self._invalid_result(
                profile,
                entry_x,
                entry_y,
                limit_y,
                "j2_interpolation_outside_measured_range",
                path_id,
                reference_node=node_code,
            )

        evaluated: List[Dict[str, float]] = [
            {"y_dxf": entry_y, "J2_deg": preview, "source": "entry_interpolation"},
            {"y_dxf": limit_y, "J2_deg": at_limit, "source": "motion_limit_interpolation"},
        ]
        lo, hi = sorted((limit_y, entry_y))
        for sample in samples:
            y = sample.get("y_dxf")
            j2 = sample.get("J2_deg")
            if _is_number(y) and _is_number(j2) and lo - 1e-6 <= float(y) <= hi + 1e-6:
                evaluated.append(
                    {
                        "y_dxf": float(y),
                        "J2_deg": float(j2),
                        "source": "measured_node",
                    }
                )

        j2_values = [float(item["J2_deg"]) for item in evaluated]
        j2_postview = max(j2_values)
        j2_min = min(j2_values)
        max_abs = max(abs(v) for v in j2_values)
        if max_abs > J2_PHYSICAL_LIMIT_DEG:
            status = "invalid"
            releaseable = False
            issues.append("j2_physical_limit_exceeded")
        elif max_abs > J2_WARNING_DEG:
            status = "warning"
            releaseable = True
            issues.append("j2_preventive_limit_exceeded")
        else:
            status = "safe"
            releaseable = True

        target = selected_profile.get("target_J2_deg")
        tolerance = selected_profile.get("tolerance_deg")
        measured_node_j2 = selected_profile.get("measured_node_J2_deg")
        target_error = None
        target_met = None
        if _is_number(target) and _is_number(measured_node_j2):
            target_error = abs(float(measured_node_j2) - float(target))
            if _is_number(tolerance):
                target_met = target_error <= float(tolerance) + 1e-9
                if not target_met:
                    issues.append("measured_node_j2_outside_preferred_target_tolerance")
                    if status == "safe":
                        status = "warning"

        e1_mode = "fixed" if profile == "min_y_min_x" else "follow_x"
        result = {
            "profile": profile,
            "path_id": path_id,
            "status": status,
            "releaseable": releaseable,
            "issues": issues,
            "entry_x_dxf": _round(entry_x),
            "entry_y_dxf": _round(entry_y),
            "path_y_motion_limit_dxf": _round(limit_y),
            "y_motion_direction_dxf": "decreasing",
            "y_advance_mm": _round(entry_y - limit_y),
            "reference_column": selected_column,
            "reference_node": node_code,
            "reference_node_id": selected_node.get("node_id"),
            "reference_node_x_dxf": _round(selected_node.get("x_dxf")),
            "reference_node_y_dxf": _round(selected_node.get("y_dxf")),
            "canonical_reference_x_dxf": CANONICAL_X_DXF,
            "e1_resolution": e1_resolution,
            "override_applied": override_applied,
            "E1_mode": e1_mode,
            "E1_selected_mm": _round(e1_value),
            "E1_limits_mm": {"min": E1_MIN_MM, "max": E1_MAX_MM},
            "target_J2_deg": _round(target),
            "tolerance_deg": _round(tolerance),
            "measured_node_J2_deg": _round(measured_node_j2),
            "target_error_deg": _round(target_error),
            "target_met": target_met,
            "J2_preview_deg": _round(preview),
            "J2_postview_deg": _round(j2_postview),
            "J2_min_deg": _round(j2_min),
            "J2_max_abs_deg": _round(max_abs),
            "J2_warning_deg": J2_WARNING_DEG,
            "J2_physical_limit_deg": J2_PHYSICAL_LIMIT_DEG,
            "prediction_method": "piecewise_linear_interpolation",
            "evaluated_samples": sorted(
                evaluated, key=lambda item: float(item["y_dxf"]), reverse=True
            ),
        }
        return result

    def resolve_frame_point(self, x_dxf: float, y_dxf: float, point_id: str) -> Dict[str, Any]:
        # El FRAME usa postura segura por punto. usual_y es la primera opción;
        # min_y_min_x actúa como respaldo cuando el nodo no ofrece usual_y.
        for profile in ("usual_y", "min_y_min_x"):
            result = self.resolve(
                profile=profile,
                entry_x_dxf=x_dxf,
                entry_y_dxf=y_dxf,
                path_y_motion_limit_dxf=y_dxf,
                path_id=point_id,
            )
            if result.get("status") != "invalid":
                result["frame_profile_selected"] = profile
                return result
        return result

    def _select_reference_node(self, profile: str, entry_y: float) -> Optional[str]:
        candidates: List[Tuple[float, str]] = []
        for code, node in self.maps.column1_nodes.items():
            p = ((node.get("profiles") or {}).get(profile) or {})
            y = node.get("y_dxf")
            if p.get("enabled") is True and _is_number(y) and float(y) + 0.05 >= entry_y:
                candidates.append((float(y), code))
        if not candidates:
            return None
        # Primer nodo disponible en la dirección opuesta al avance: y >= entry_y,
        # lo más cercano posible. Desde ese nodo la tabla progresa hacia Y menor.
        return min(candidates, key=lambda item: (item[0], item[1]))[1]

    @staticmethod
    def _interpolate(samples: Sequence[Dict[str, Any]], target_y: float) -> Optional[float]:
        pts = sorted(
            [
                (float(s["y_dxf"]), float(s["J2_deg"]))
                for s in samples
                if _is_number(s.get("y_dxf")) and _is_number(s.get("J2_deg"))
            ],
            key=lambda item: item[0],
        )
        if not pts:
            return None
        y = float(target_y)
        if y < pts[0][0] - 0.05 or y > pts[-1][0] + 0.05:
            return None
        for py, pj2 in pts:
            if abs(py - y) <= 0.05:
                return pj2
        for (y0, j0), (y1, j1) in zip(pts, pts[1:]):
            if y0 <= y <= y1:
                if abs(y1 - y0) < 1e-9:
                    return j0
                ratio = (y - y0) / (y1 - y0)
                return j0 + ratio * (j1 - j0)
        return None

    @staticmethod
    def _invalid_result(
        profile: str,
        entry_x: float,
        entry_y: float,
        limit_y: float,
        issue: str,
        path_id: str,
        **extra: Any,
    ) -> Dict[str, Any]:
        result = {
            "profile": profile,
            "path_id": path_id,
            "status": "invalid",
            "releaseable": False,
            "issues": [issue],
            "entry_x_dxf": _round(entry_x),
            "entry_y_dxf": _round(entry_y),
            "path_y_motion_limit_dxf": _round(limit_y),
            "y_motion_direction_dxf": "decreasing",
            "y_advance_mm": _round(entry_y - limit_y),
            "E1_mode": "fixed" if profile == "min_y_min_x" else "follow_x",
        }
        result.update(extra)
        return result

    def metadata(self) -> Dict[str, Any]:
        return {
            "map_id": "R3_A_UF1_240X96_OPTIM_PATH_V4",
            "robot": "R3",
            "bed": "A",
            "uf": 1,
            "sheet_width_mm": 6096.0,
            "sheet_height_mm": 2438.0,
            "canonical_column_x_dxf": CANONICAL_X_DXF,
            "node_step_y_mm": NODE_STEP_Y_MM,
            "e1_translation_formula": "E1_path = E1_reference + (entry_x_dxf - reference_x_dxf)",
            "e1_limits_mm": {"min": E1_MIN_MM, "max": E1_MAX_MM},
            "j2_prediction_method": "piecewise_linear_interpolation",
            "source_files": dict(self.maps.source_files),
        }
