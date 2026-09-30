"""Tope de área 456.954 in² (SP-792_1) para RTZ y ventanas.

- Pieza con área neta <= tope: se nestea como RTZ (``forzar_rtz``) salvo que el
  usuario la desmarque en PARTS (flag explícito False).
- Orificio interior ("ventana") de una pieza: solo admite piezas si su área es
  >= tope. Los menores son metal para el motor (colisión), pero se conservan
  en la geometría exportada/visible.
"""
from __future__ import annotations

from shapely.geometry import MultiPolygon, Polygon

# Área neta real de SP-792_1 (disco 24.25" con 16 barrenos).
AREA_TOPE_RTZ_IN2 = 456.954
IN2_MM2 = 645.16
_TOL_IN2 = 0.01

AREA_PIEZA_RTZ_MAX_MM2 = (AREA_TOPE_RTZ_IN2 + _TOL_IN2) * IN2_MM2
AREA_VENTANA_MIN_MM2 = (AREA_TOPE_RTZ_IN2 - _TOL_IN2) * IN2_MM2


def _es_cobre(material) -> bool:
    try:
        from interface.utils_nesting import es_material_cobre
    except Exception:
        return "COBRE" in str(material or "").upper()
    return bool(es_material_cobre(material))


def pieza_es_rtz_por_area(area_mm2, material=None) -> bool:
    """Cobre queda fuera: tiene su propio RTZCU."""
    if material is not None and _es_cobre(material):
        return False
    try:
        a = float(area_mm2 or 0.0)
    except (TypeError, ValueError):
        return False
    return 0.0 < a <= AREA_PIEZA_RTZ_MAX_MM2


def resolver_forzar_rtz(area_mm2, explicito: bool | None, material=None) -> bool:
    """Flag explícito del usuario (True/False) manda; sin flag decide el área."""
    if explicito is not None:
        return bool(explicito)
    return pieza_es_rtz_por_area(area_mm2, material)


def flag_forzar_explicito(flags_ruta: dict | None, clave_ruta, flags_nombre: dict | None, clave_nombre) -> bool | None:
    """True/False si el usuario marcó/desmarcó la pieza (ruta primero); None si nunca la tocó."""
    for flags, clave in ((flags_ruta, clave_ruta), (flags_nombre, clave_nombre)):
        if flags and clave and clave in flags and flags[clave] is not None:
            return bool(flags[clave])
    return None


def ventana_admite_piezas(area_mm2) -> bool:
    try:
        return float(area_mm2 or 0.0) >= AREA_VENTANA_MIN_MM2
    except (TypeError, ValueError):
        return False


def _solidificar_poligono(poly: Polygon) -> Polygon:
    grandes = [r for r in poly.interiors if ventana_admite_piezas(Polygon(r).area)]
    if len(grandes) == len(poly.interiors):
        return poly
    return Polygon(poly.exterior, grandes)


def solidificar_ventanas_chicas(geom):
    """Geometría de colisión: orificios < tope quedan como metal."""
    if geom is None or getattr(geom, "is_empty", True):
        return geom
    if isinstance(geom, Polygon):
        return _solidificar_poligono(geom)
    if isinstance(geom, MultiPolygon):
        return MultiPolygon([_solidificar_poligono(g) for g in geom.geoms])
    return geom
