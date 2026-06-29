"""Geometría de visualización 1:1 desde DXF fuente (mantiene acomodo del nest)."""
from __future__ import annotations

import os
from typing import Any

from shapely import affinity

from .geometry_parser import (
    poligonos_desde_shapely,
    reconstruir_poly_seguro,
    recuperar_geometria_robusta,
)

_DXF_LOCAL_CACHE: dict[tuple[str, float], tuple] = {}


def _cargar_poly_local_dxf(ruta: str):
    try:
        mtime = os.path.getmtime(ruta)
    except OSError:
        return None

    key = (ruta, mtime)
    cached = _DXF_LOCAL_CACHE.get(key)
    if cached is not None:
        return cached

    poly, marks = recuperar_geometria_robusta(ruta)
    if poly is None or getattr(poly, "is_empty", True):
        return None

    minx, miny, _, _ = poly.bounds
    poly_local = affinity.translate(poly, -minx, -miny)
    marks_local = marks
    if marks is not None and not getattr(marks, "is_empty", True):
        marks_local = affinity.translate(marks, -minx, -miny)

    cached = (poly_local, marks_local, float(minx), float(miny))
    _DXF_LOCAL_CACHE[key] = cached
    if len(_DXF_LOCAL_CACHE) > 256:
        _DXF_LOCAL_CACHE.pop(next(iter(_DXF_LOCAL_CACHE)))
    return cached


def _inferir_transformacion(p_orig: dict, pieza: dict):
    from .manager import _inferir_transformacion_desde_resultado

    return _inferir_transformacion_desde_resultado(p_orig, pieza)


def _origen_rotacion(poly_local):
    from .manager import _origen_rotacion_pieza

    return _origen_rotacion_pieza(poly_local)


def poligonos_display_desde_dxf(pieza: dict) -> list | None:
    """
    Reconstruye anillos de la pieza colocada usando el DXF fuente (tolerancia CAD)
    y el acomodo actual (polígonos del nest). No altera pieza.
    """
    ruta = str(pieza.get("ruta") or "").strip()
    if not ruta or not os.path.isfile(ruta):
        return None

    nested = pieza.get("poligonos") or []
    if not nested or not nested[0] or len(nested[0]) < 3:
        return None

    loaded = _cargar_poly_local_dxf(ruta)
    if loaded is None:
        return None
    poly_local, marks_local, orig_minx, orig_miny = loaded

    p_orig = {
        "poly": poly_local,
        "poly_exact": poly_local,
        "marks": marks_local,
        "marks_exact": marks_local,
        "orig_minx": orig_minx,
        "orig_miny": orig_miny,
    }
    transform = _inferir_transformacion(p_orig, pieza)
    if not transform:
        final_poly = reconstruir_poly_seguro(nested)
        if final_poly is None or final_poly.is_empty:
            return None
        fnminx, fnminy, _, _ = final_poly.bounds
        placed = affinity.translate(poly_local, fnminx, fnminy)
        return poligonos_desde_shapely(placed)

    rot_origin = _origen_rotacion(poly_local)
    rotated = affinity.rotate(
        poly_local,
        float(transform.get("rot_deg", 0) or 0),
        origin=rot_origin,
    )
    placed = affinity.translate(
        rotated,
        float(transform.get("shift_x", 0) or 0),
        float(transform.get("shift_y", 0) or 0),
    )
    return poligonos_desde_shapely(placed)


def _es_pieza_virtual_nombre(nom: str) -> bool:
    n = str(nom or "")
    return n.startswith(
        ("REF__", "TATUAJE__", "RETAZO_GUILLOTINA__", "CU_CORTE__", "REMANENTE__")
    )


def completar_transform_export_pieza(pieza: dict) -> bool:
    """
    Infiera rotación y traslación desde DXF + polígonos colocados (export 1:1).
    Necesario cuando el nest viene de .arganest sin metadata de transformación.
    """
    if not isinstance(pieza, dict) or _es_pieza_virtual_nombre(pieza.get("nombre")):
        return False

    ruta = str(pieza.get("ruta") or "").strip()
    if not ruta or not os.path.isfile(ruta):
        return False

    nested = pieza.get("poligonos") or []
    if not nested or not nested[0] or len(nested[0]) < 3:
        return False

    loaded = _cargar_poly_local_dxf(ruta)
    if loaded is None:
        return False
    poly_local, marks_local, orig_minx, orig_miny = loaded

    p_orig = {
        "poly": poly_local,
        "poly_exact": poly_local,
        "marks": marks_local,
        "marks_exact": marks_local,
        "orig_minx": orig_minx,
        "orig_miny": orig_miny,
    }
    transform = _inferir_transformacion(p_orig, pieza)
    if not transform:
        return False

    rot_origin = _origen_rotacion(poly_local)
    pieza["orig_minx"] = float(orig_minx)
    pieza["orig_miny"] = float(orig_miny)
    pieza["rot_deg"] = float(transform.get("rot_deg", 0.0) or 0.0)
    pieza["shift_x"] = float(transform.get("shift_x", 0.0) or 0.0)
    pieza["shift_y"] = float(transform.get("shift_y", 0.0) or 0.0)
    pieza["rot_origin_cx"] = float(rot_origin[0])
    pieza["rot_origin_cy"] = float(rot_origin[1])
    return True


def completar_transform_export_hoja(hoja: dict) -> int:
    if not isinstance(hoja, dict):
        return 0
    n = 0
    for pz in hoja.get("piezas") or []:
        if completar_transform_export_pieza(pz):
            n += 1
    return n


def refrescar_poligonos_display_pieza(pieza: dict) -> bool:
    """Sustituye poligonos en memoria por versión fiel al DXF (misma posición)."""
    if not isinstance(pieza, dict):
        return False
    if _es_pieza_virtual_nombre(pieza.get("nombre")):
        return False

    if not completar_transform_export_pieza(pieza):
        return False

    pols = poligonos_display_desde_dxf(pieza)
    if not pols:
        return False

    pieza["poligonos"] = pols
    pieza.pop("_poly_cache", None)
    pieza.pop("_bounds_cache", None)
    return True


def refrescar_poligonos_display_hoja(hoja: dict) -> int:
    if not isinstance(hoja, dict):
        return 0
    n = 0
    for pz in hoja.get("piezas") or []:
        if refrescar_poligonos_display_pieza(pz):
            n += 1
    return n


def refrescar_poligonos_display_resultados(resultados: dict) -> int:
    """Una sola pasada sobre todas las hojas (carga nest / fin de nesting)."""
    if not isinstance(resultados, dict):
        return 0
    total = 0
    for info in resultados.values():
        if not isinstance(info, dict):
            continue
        for hoja in info.get("hojas") or []:
            total += refrescar_poligonos_display_hoja(hoja)
    return total


def refrescar_poligonos_display_multilote(multilote) -> int:
    if not isinstance(multilote, list):
        return 0
    total = 0
    for lote in multilote:
        if not isinstance(lote, dict):
            continue
        data = lote.get("data")
        if isinstance(data, dict):
            total += refrescar_poligonos_display_resultados(data)
    return total
