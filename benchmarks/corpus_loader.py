"""Carga de escenarios de corpus (S0/S1) con geometría embebida (sin red)."""
from __future__ import annotations

import json
import os
from typing import Any

from shapely.geometry import Polygon

IN_TO_MM = 25.4


def benchmarks_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def corpus_dir() -> str:
    return os.path.join(benchmarks_root(), "corpus")


def real_corpus_dir() -> str:
    return os.path.join(benchmarks_root(), "corpus_real")


def baselines_dir() -> str:
    return os.path.join(benchmarks_root(), "baselines")


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_scenarios() -> list[str]:
    out: list[str] = []
    for name in sorted(os.listdir(corpus_dir())):
        if name.lower().endswith(".json"):
            out.append(os.path.splitext(name)[0])
    root = real_corpus_dir()
    if os.path.isdir(root):
        for case_dir in sorted(os.listdir(root)):
            case_path = os.path.join(root, case_dir)
            if not os.path.isdir(case_path):
                continue
            for filename in ("scenario.json", "scenario.nestsim.json"):
                candidate = os.path.join(case_path, filename)
                if os.path.isfile(candidate):
                    data = load_json(candidate)
                    scenario_id = str(data.get("scenario") or case_dir)
                    if scenario_id not in out:
                        out.append(scenario_id)
    return out


def scenario_path(scenario_id: str) -> str:
    embedded = os.path.join(corpus_dir(), f"{scenario_id}.json")
    if os.path.isfile(embedded):
        return embedded
    root = real_corpus_dir()
    if os.path.isdir(root):
        for case_dir in os.listdir(root):
            case_path = os.path.join(root, case_dir)
            for filename in ("scenario.json", "scenario.nestsim.json"):
                candidate = os.path.join(case_path, filename)
                if not os.path.isfile(candidate):
                    continue
                data = load_json(candidate)
                if str(data.get("scenario") or case_dir) == scenario_id:
                    return candidate
    raise FileNotFoundError(f"Escenario no encontrado: {scenario_id}")


def _ring_to_tuples(ring: list) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for pt in ring or []:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            pts.append((float(pt[0]), float(pt[1])))
    return pts


def piece_from_spec(spec: dict, *, index: int = 0) -> dict:
    """Convierte spec de corpus a pieza pack (Shapely poly, mm)."""
    rings = spec.get("rings")
    if not rings:
        # atajos rectángulo w_in/h_in o w_mm/h_mm
        if "w_in" in spec or "h_in" in spec:
            w = float(spec.get("w_in") or 0) * IN_TO_MM
            h = float(spec.get("h_in") or 0) * IN_TO_MM
        else:
            w = float(spec.get("w_mm") or 0)
            h = float(spec.get("h_mm") or 0)
        rings = [[(0.0, 0.0), (w, 0.0), (w, h), (0.0, h), (0.0, 0.0)]]
        hole = spec.get("hole_in")
        if hole and len(hole) >= 4:
            # hole_in = [x,y,w,h] en pulgadas relativo al origen
            x, y, hw, hh = [float(v) * IN_TO_MM for v in hole[:4]]
            rings.append(
                [(x, y), (x + hw, y), (x + hw, y + hh), (x, y + hh), (x, y)]
            )

    outer = _ring_to_tuples(rings[0])
    holes = [_ring_to_tuples(r) for r in rings[1:]]
    poly = Polygon(outer, holes or None)
    if not poly.is_valid:
        poly = poly.buffer(0)
    base = str(spec.get("nombre") or f"P{index}")
    qty_idx = int(spec.get("_qty_idx") or 0)
    nombre = base if qty_idx <= 0 else f"{base}#{qty_idx}"
    out = {
        "nombre": nombre,
        "poly": poly,
        "area": float(spec.get("area") or poly.area),
        "calibre": str(spec.get("calibre") or "BENCH"),
        "material": str(spec.get("material") or "A36"),
        "marks": None,
    }
    if spec.get("grain_locked"):
        out["grain_locked"] = True
    if spec.get("allowed_rotations") is not None:
        out["allowed_rotations"] = spec.get("allowed_rotations")
    return out


def expand_pieces(data: dict) -> list[dict]:
    piezas: list[dict] = []
    for i, spec in enumerate(data.get("pieces") or []):
        qty = max(1, int(spec.get("qty") or 1))
        for q in range(qty):
            s = dict(spec)
            s["_qty_idx"] = q if qty > 1 else 0
            piezas.append(piece_from_spec(s, index=i))
    return piezas


def _params_from_data(data: dict, scenario_id: str) -> dict[str, Any]:
    return {
        "scenario": str(data.get("scenario") or scenario_id),
        "plate_w_in": float(data.get("plate_w_in") or 48),
        "plate_h_in": float(data.get("plate_h_in") or 48),
        "kerf_in": float(data.get("kerf_in") or 0.25),
        "margin_in": float(data.get("margin_in") or 0.15),
        "corner": str(data.get("corner") or "INFERIOR IZQUIERDA"),
        "opt": str(data.get("opt") or "OPTIMIZAR LARGO Y ANCHO"),
        "nest_mode": str(data.get("nest_mode") or "standard"),
        "mc_iterations": int(data.get("mc_iterations") or 1),
        "notes": str(data.get("notes") or ""),
        "require_full_place": bool(data.get("require_full_place", True)),
        "level": str(data.get("level") or ""),
        "source_kind": str(data.get("source_kind") or "embedded"),
    }


def _expand_local_nestsim(data: dict, scenario_file: str) -> list[dict]:
    """Carga DXF desde rutas relativas del snapshot local."""
    from modules.nesting_engine.sim_lab import SimPieceEntry, build_pieces_from_entries

    base_dir = os.path.dirname(scenario_file)
    entries: list[SimPieceEntry] = []
    for piece in data.get("pieces") or []:
        if not isinstance(piece, dict):
            continue
        route = str(piece.get("ruta") or "")
        if not os.path.isabs(route):
            route = os.path.normpath(os.path.join(base_dir, route))
        entries.append(
            SimPieceEntry(
                ruta=route,
                qty=max(1, int(piece.get("qty") or 1)),
                nombre=str(piece.get("nombre") or ""),
            )
        )
    pieces, errors = build_pieces_from_entries(entries)
    if errors:
        raise ValueError("No se pudo cargar snapshot DXF local: " + " | ".join(errors))
    if not pieces:
        raise ValueError("Snapshot DXF local no produjo piezas.")
    return pieces


def load_scenario(scenario_id: str) -> tuple[dict[str, Any], list[dict]]:
    path = scenario_path(scenario_id)
    data = load_json(path)
    params = _params_from_data(data, scenario_id)
    if str(data.get("source_kind") or "") == "nestsim_snapshot":
        return params, _expand_local_nestsim(data, path)
    return params, expand_pieces(data)


def pieces_to_native(piezas: list[dict]) -> list[dict]:
    from modules.nesting_engine.algorithm_bridge import _piece_to_native

    return [_piece_to_native(p) for p in piezas]
