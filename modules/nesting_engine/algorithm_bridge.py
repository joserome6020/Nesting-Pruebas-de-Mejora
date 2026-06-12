"""Puente Python → motor de nesting nativo (C++ / Cython fallback)."""
from __future__ import annotations

import copy
import os

_ENGINE_NAME = "unknown"
_empaquetar_native = None


def _rings_from_shapely_polygon(poly):
    if poly is None:
        return []
    exterior = list(poly.exterior.coords)
    rings = [exterior]
    for hole in getattr(poly, "interiors", []):
        rings.append(list(hole.coords))
    return rings


def _marks_ring_coords(coords):
    """Normaliza un trazo de marcaje para el motor nativo (mín. 2 vértices)."""
    ring = [(float(x), float(y)) for x, y in coords]
    if len(ring) < 2:
        return None
    # Compat: bindings C++ antiguos descartaban anillos con < 3 puntos.
    if len(ring) == 2:
        ring.append(ring[-1])
    return ring


def _marks_from_shapely(marks):
    if marks is None:
        return []
    try:
        if marks.is_empty:
            return []
    except Exception:
        return []
    out = []
    gtype = getattr(marks, "geom_type", "")
    if gtype == "LineString":
        ring = _marks_ring_coords(marks.coords)
        if ring:
            out.append(ring)
    elif gtype == "MultiLineString":
        for line in marks.geoms:
            ring = _marks_ring_coords(line.coords)
            if ring:
                out.append(ring)
    return out


def _piece_to_native(piece):
    poly = piece["poly"]
    marks = piece.get("marks")
    try:
        area = float(piece.get("area") or poly.area or 0.0)
    except Exception:
        area = 0.0
    return {
        "nombre": str(piece.get("nombre") or ""),
        "area": area,
        "calibre": str(piece.get("calibre") or ""),
        "material": str(piece.get("material") or ""),
        "rings": _rings_from_shapely_polygon(poly),
        "marks": _marks_from_shapely(marks),
    }


def _piece_from_native_rest(piece_native, original_lookup):
    key = str(piece_native.get("nombre") or "")
    src = original_lookup.get(key)
    if src is None:
        return None
    restored = copy.deepcopy(src)
    return restored


def _resolve_engine():
    global _ENGINE_NAME, _empaquetar_native
    if _empaquetar_native is not None:
        return _empaquetar_native, _ENGINE_NAME

    force = str(os.environ.get("ARGA_NESTING_ENGINE", "")).strip().lower()
    if force in {"cython", "python"}:
        from .algorithm import empaquetar_una_hoja_mc as fn

        _empaquetar_native = fn
        _ENGINE_NAME = "cython"
        return fn, _ENGINE_NAME

    try:
        from . import algorithm_cpp

        _empaquetar_native = algorithm_cpp.empaquetar_una_hoja_mc
        _ENGINE_NAME = getattr(algorithm_cpp, "ENGINE_NAME", "cpp")
        return _empaquetar_native, _ENGINE_NAME
    except ImportError:
        pass

    from .algorithm import empaquetar_una_hoja_mc as fn

    _empaquetar_native = fn
    _ENGINE_NAME = "cython"
    return fn, _ENGINE_NAME


def engine_name() -> str:
    _resolve_engine()
    return _ENGINE_NAME


def empaquetar_una_hoja_mc(
    piezas,
    w_placa,
    h_placa,
    kerf_override=0.2,
    margin_override=0.0,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_poly=None,
):
    fn, engine = _resolve_engine()

    if engine == "cython":
        return fn(
            piezas,
            w_placa,
            h_placa,
            kerf_override,
            margin_override,
            opt_override,
            corner_override,
            limite_poly=limite_poly,
        )

    native_piezas = [_piece_to_native(p) for p in (piezas or [])]
    limite_rings = None
    if limite_poly is not None:
        limite_rings = _rings_from_shapely_polygon(limite_poly)

    hoja_native, restos_native = fn(
        native_piezas,
        w_placa,
        h_placa,
        kerf_override,
        margin_override,
        opt_override,
        corner_override,
        limite_rings,
    )

    hoja = {
        "piezas": list(hoja_native.get("piezas", [])),
        "area_usada": float(hoja_native.get("area_usada", 0.0) or 0.0),
        "eficiencia": float(hoja_native.get("eficiencia", 0.0) or 0.0),
    }

    lookup = {}
    for p in piezas or []:
        lookup[str(p.get("nombre") or "")] = p

    restos = []
    for rn in restos_native or []:
        restored = _piece_from_native_rest(rn, lookup)
        if restored is not None:
            restos.append(restored)

    if not restos:
        placed = {str(x.get("nombre") or "") for x in hoja["piezas"]}
        for p in piezas or []:
            if str(p.get("nombre") or "") not in placed:
                restos.append(copy.deepcopy(p))

    return hoja, restos
