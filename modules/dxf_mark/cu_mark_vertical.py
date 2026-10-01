"""MARK vertical al inicio de piezas normales de cobre (CNC Busbar Punching).

La punzonadora graba el ``Model`` (``M100``) en vertical, al principio de la
pieza y antes de los barrenos. Este MARK es el que se ve en PARTS, en el nest y
en el DXF de la barra, centrado en la misma X que el golpe ``M100`` del CSV
(``cu_punch_tooling.x_grabado_mm``) para que las coordenadas cuadren.
"""
from __future__ import annotations

from typing import Any

from shapely.geometry import MultiLineString, Polygon

from modules.dxf_mark.inject import (
    DEFAULT_CLEARANCE_IN,
    MAX_MARK_HEIGHT_IN,
    MIN_MARK_HEIGHT_IN,
    STICK_VISIBLE_HEIGHT_FACTOR,
)
from modules.dxf_mark.stick_font import (
    build_stick_strokes,
    normalize_mark_text,
    rotate_strokes,
    text_bbox,
    translate_strokes,
)

_TOL_RECT_MM = 0.05


def es_rectangulo_eje(poly: Polygon | None) -> bool:
    """Contorno exterior de 4 vértices alineado a ejes (pieza normal de solera)."""
    if poly is None or poly.is_empty or not isinstance(poly, Polygon):
        return False
    pts = list(poly.exterior.simplify(_TOL_RECT_MM).coords)[:-1]
    if len(pts) != 4:
        return False
    for i in range(4):
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % 4]
        if abs(x1 - x2) > _TOL_RECT_MM and abs(y1 - y2) > _TOL_RECT_MM:
            return False
    return True


def strokes_mark_cu_vertical(
    bounds_mm: tuple[float, float, float, float],
    holes_minx_mm: list[float],
    texto: str,
    *,
    grabado: dict[str, Any] | None = None,
) -> list[list[tuple[float, float]]]:
    """Trazos (mm) del texto vertical: centrado en ``x_grabado_mm`` y empezando en
    ``y_grabado_mm`` (mismo punto que el golpe ``M100`` del CSV), leyendo hacia arriba."""
    from modules.nesting_engine.cu_punch_tooling import x_grabado_mm, y_grabado_mm

    text = normalize_mark_text(texto)
    if not text:
        return []
    minx, miny, maxx, maxy = (float(v) for v in bounds_mm)
    largo, ancho = maxx - minx, maxy - miny
    if largo <= 0 or ancho <= 0:
        return []
    borde = (min(holes_minx_mm) - minx) if holes_minx_mm else None
    x_rel = x_grabado_mm(largo, borde, grabado)
    clear = DEFAULT_CLEARANCE_IN * 25.4
    franja = 2.0 * x_rel
    h_vis = min(MAX_MARK_HEIGHT_IN * 25.4, franja - 2.0 * clear)
    h_vis = max(h_vis, MIN_MARK_HEIGHT_IN * 25.4)
    height = h_vis / STICK_VISIBLE_HEIGHT_FACTOR
    strokes = build_stick_strokes(text, (0.0, 0.0), height)
    bb = text_bbox(strokes)
    if not bb:
        return []
    y_rel = y_grabado_mm(ancho, grabado)
    usable = max(ancho - y_rel - clear, 1e-6)
    tw = bb[2] - bb[0]
    if tw > usable:
        strokes = build_stick_strokes(text, (0.0, 0.0), height * usable / tw)
    strokes = rotate_strokes(strokes, 90.0)
    bb = text_bbox(strokes)
    cx_t = (bb[0] + bb[2]) / 2.0
    return translate_strokes(strokes, minx + x_rel - cx_t, miny + y_rel - bb[1])


def mark_cu_vertical_para_poly(
    poly: Polygon,
    texto: str,
    *,
    grabado: dict[str, Any] | None = None,
) -> MultiLineString:
    """MARK vertical para una pieza (coords de ``poly`` en mm); vacío si no aplica."""
    if not es_rectangulo_eje(poly):
        return MultiLineString()
    holes = [float(Polygon(r).bounds[0]) for r in poly.interiors]
    strokes = strokes_mark_cu_vertical(poly.bounds, holes, texto, grabado=grabado)
    lines = [s for s in strokes if len(s) >= 2]
    return MultiLineString(lines) if lines else MultiLineString()


def aplica_mark_cu_vertical(material: str, *, especial: bool = False) -> bool:
    """Cobre normal en modo punzonadora (no especial, no modo forzado DXF+STEP)."""
    try:
        from interface.utils_nesting import es_material_cobre

        if not es_material_cobre(material):
            return False
    except Exception:
        if "CU" not in str(material or "").upper():
            return False
    if especial:
        return False
    try:
        from modules.nesting_engine.nest_runtime_prefs import is_cu_force_dxf_step_enabled

        if is_cu_force_dxf_step_enabled():
            return False
    except Exception:
        pass
    return True
