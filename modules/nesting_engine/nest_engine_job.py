"""Serialización de jobs de motor (Lite/Force/APEX/…) para nest remoto Spark."""
from __future__ import annotations

import json
from typing import Any

from modules.nesting_engine.engines.types import PackSheetRequest, PackSheetResult


def _jsonable(value: Any) -> Any:
    """Convierte estructuras de piezas a JSON-safe (tuples → list, etc.)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "tolist"):
        try:
            return _jsonable(value.tolist())
        except Exception:
            pass
    if hasattr(value, "geom_type") and hasattr(value, "exterior"):
        return None
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _rings_from_piece(piece: dict[str, Any]) -> list[list[list[float]]]:
    rings = piece.get("poligonos") or piece.get("rings")
    if isinstance(rings, list) and rings:
        out: list[list[list[float]]] = []
        for ring in rings:
            pts: list[list[float]] = []
            for pt in ring or []:
                if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    pts.append([float(pt[0]), float(pt[1])])
            if len(pts) >= 3:
                out.append(pts)
        if out:
            return out
    poly = piece.get("poly_exact") or piece.get("poly")
    if poly is not None and hasattr(poly, "exterior"):
        try:
            from modules.nesting_engine.algorithm_bridge import _rings_from_shapely_polygon

            return _jsonable(_rings_from_shapely_polygon(poly))
        except Exception:
            try:
                exterior = [[float(x), float(y)] for x, y in poly.exterior.coords]
                holes = [
                    [[float(x), float(y)] for x, y in hole.coords]
                    for hole in getattr(poly, "interiors", [])
                ]
                return [exterior, *holes]
            except Exception:
                return []
    return []


def piece_to_wire(piece: Any) -> dict[str, Any]:
    """Quita geometría viva y garantiza poligonos JSON."""
    if not isinstance(piece, dict):
        return {"nombre": str(piece)}
    skip = {"poly", "poly_exact", "marks", "cancel_checker"}
    out = {str(k): _jsonable(v) for k, v in piece.items() if k not in skip}
    rings = _rings_from_piece(piece)
    if rings:
        out["poligonos"] = rings
        out.setdefault("rings", rings)
    if out.get("area") is None and piece.get("poly") is not None:
        try:
            out["area"] = float(piece["poly"].area)
        except Exception:
            pass
    return out


def hydrate_piece(piece: dict[str, Any]) -> dict[str, Any]:
    """Reconstruye Shapely ``poly`` desde poligonos/rings tras JSON."""
    out = dict(piece or {})
    poly = out.get("poly")
    needs = poly is None or not hasattr(poly, "geom_type")
    if needs:
        rings = out.get("poligonos") or out.get("rings") or []
        if rings:
            try:
                from shapely.geometry import Polygon

                exterior = [(float(p[0]), float(p[1])) for p in rings[0]]
                holes = [
                    [(float(p[0]), float(p[1])) for p in ring]
                    for ring in rings[1:]
                    if ring
                ]
                geom = Polygon(exterior, holes)
                if not geom.is_empty:
                    if not geom.is_valid:
                        geom = geom.buffer(0)
                    out["poly"] = geom
                    out["poly_exact"] = geom
                    out.setdefault("area", float(geom.area))
            except Exception as ex:
                out["_hydrate_error"] = str(ex)
    return out


def hydrate_limite_poly(value: Any) -> Any:
    if value is None or hasattr(value, "geom_type"):
        return value
    if isinstance(value, list) and value:
        try:
            from shapely.geometry import Polygon

            if isinstance(value[0], (list, tuple)) and isinstance(value[0][0], (list, tuple)):
                exterior = [(float(p[0]), float(p[1])) for p in value[0]]
                holes = [[(float(p[0]), float(p[1])) for p in ring] for ring in value[1:]]
                return Polygon(exterior, holes)
            return Polygon([(float(p[0]), float(p[1])) for p in value])
        except Exception:
            return None
    return None


def pack_request_to_dict(request: PackSheetRequest, *, engine_id: str) -> dict[str, Any]:
    limite = request.limite_poly
    limite_wire: Any = None
    if limite is not None and hasattr(limite, "geom_type"):
        try:
            from modules.nesting_engine.algorithm_bridge import _rings_from_shapely_polygon

            limite_wire = _jsonable(_rings_from_shapely_polygon(limite))
        except Exception:
            limite_wire = None
    else:
        limite_wire = _jsonable(limite)

    return {
        "engine_id": str(engine_id),
        "w_placa": float(request.w_placa),
        "h_placa": float(request.h_placa),
        "kerf_override": float(request.kerf_override),
        "margin_override": float(request.margin_override),
        "opt_override": str(request.opt_override),
        "corner_override": str(request.corner_override),
        "limite_poly": limite_wire,
        "mc_iterations": None if request.mc_iterations is None else int(request.mc_iterations),
        "piezas": [piece_to_wire(p) for p in (request.piezas or [])],
    }


def pack_result_from_dict(data: dict[str, Any]) -> PackSheetResult:
    if not isinstance(data, dict):
        raise ValueError("pack_engine result must be object")
    runtime = data.get("runtime")
    restos_raw = list(data.get("restos") or [])
    restos = [hydrate_piece(p) if isinstance(p, dict) else p for p in restos_raw]
    return PackSheetResult(
        hoja=dict(data.get("hoja") or {}),
        restos=restos,
        engine_id=str(data.get("engine_id") or ""),
        elapsed_s=float(data.get("elapsed_s") or 0.0),
        error=data.get("error"),
        runtime=dict(runtime) if isinstance(runtime, dict) else None,
    )
