"""Tope de área 456.954 in² (SP-792_1).

- Pieza con área neta <= tope: reservada a RTZ. No va al cuerpo de la madre;
  solo entra al mini-nest de retazos (clamp cama láser 120x60", mínimo 20",
  >= 2 piezas por RTZ). Si la madre es "cama láser sin mini nest" va directo
  en la placa. Lo que no cupo en retazos se anida en placa de cama 120x60.
- Orificio interior ("ventana") de una pieza: solo admite piezas si su área es
  >= tope. Los menores son metal para el motor (colisión), pero se conservan en
  la geometría exportada/visible.
"""
from __future__ import annotations

from shapely.geometry import MultiPolygon, Polygon

# Área neta real de SP-792_1 (disco 24.25" con 16 barrenos).
AREA_TOPE_RTZ_IN2 = 456.954
IN2_MM2 = 645.16
_TOL_IN2 = 0.01

AREA_PIEZA_RTZ_MAX_MM2 = (AREA_TOPE_RTZ_IN2 + _TOL_IN2) * IN2_MM2
AREA_VENTANA_MIN_MM2 = (AREA_TOPE_RTZ_IN2 - _TOL_IN2) * IN2_MM2


RTZ_MIN_PIEZAS = 2
CAMA_LASER_LARGO_MM = 120.0 * 25.4
CAMA_LASER_ANCHO_MM = 60.0 * 25.4
_TOL_FORMATO_MM = 13.0


def pieza_reservada_rtz(pieza: dict) -> bool:
    try:
        return 0.0 < float(pieza.get("area") or 0.0) <= AREA_PIEZA_RTZ_MAX_MM2
    except (TypeError, ValueError, AttributeError):
        return False


def placas_cama_laser(placas: list) -> list:
    """Formatos para piezas RTZ sobrantes: 120x60 exacto; si no hay, <= 120x60."""
    def _dims(p):
        w, h = float(p.get("w") or 0.0), float(p.get("h") or 0.0)
        return max(w, h), min(w, h)

    exactas, menores = [], []
    for p in placas or []:
        largo, ancho = _dims(p)
        if largo <= CAMA_LASER_LARGO_MM + _TOL_FORMATO_MM and ancho <= CAMA_LASER_ANCHO_MM + _TOL_FORMATO_MM:
            menores.append(p)
            if largo >= CAMA_LASER_LARGO_MM - _TOL_FORMATO_MM and ancho >= CAMA_LASER_ANCHO_MM - _TOL_FORMATO_MM:
                exactas.append(p)
    return exactas or menores


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
