"""
Exportación plasma compensada.
Usa los mismos polígonos del nest (mm de placa) y el mismo dibujado que láser.
"""
from __future__ import annotations

import math
import os
from typing import List, Tuple

import ezdxf
from shapely.geometry import MultiPolygon, Polygon

from modules.dxf_native_curves import export_ring_native
from modules.nesting_engine.geometry_parser import ESCALA_DXF, _clasificar_capa
from modules.plasma_compensator import _buffer_polygon_points, _entity_points_xy

Point = Tuple[float, float]

# Contorno exterior válido debe superar este radio (mm); evita círculos basura del fallback viejo.
_MIN_OUTER_SPAN_MM = 8.0


def _sanitize_ring_coords(ring, decimals=4):
    from modules.nesting_engine.exporter import _sanitize_ring_coords as sanitize

    return sanitize(ring, decimals=decimals)


def _rings_from_poligonos(pols: list) -> tuple[list, list]:
    """Extrae outer + holes desde poligonos del nest; reconstruye si hace falta."""
    from modules.nesting_engine.geometry_parser import reconstruir_poly_seguro

    pols = list(pols or [])
    if not pols:
        return [], []

    outer = list(pols[0] or [])
    holes = [list(h or []) for h in pols[1:] if h]

    if len(outer) >= 3:
        return outer, holes

    poly = reconstruir_poly_seguro(pols)
    if poly is None or poly.is_empty:
        return outer, holes

    try:
        if not poly.is_valid:
            fixed = poly.buffer(0)
            if fixed is not None and not fixed.is_empty:
                if isinstance(fixed, MultiPolygon):
                    poly = max(fixed.geoms, key=lambda g: float(g.area))
                else:
                    poly = fixed
    except Exception:
        pass

    if poly is None or poly.is_empty:
        return outer, holes

    try:
        outer = list(poly.exterior.coords)
        holes = [list(i.coords) for i in poly.interiors]
    except Exception:
        pass
    return outer, holes


def build_plasma_profile_from_nested(pols: list, *, offset_mm: float = 0.0, already_compensated: bool = False):
    """Perfil plasma desde poligonos del nest (mm de placa)."""
    outer_raw, holes_raw = _rings_from_poligonos(pols)
    if already_compensated:
        from modules.nesting_engine.exporter import _clean_profile_for_production

        return _clean_profile_for_production(outer_raw, holes_raw)
    if offset_mm <= 0:
        return sanitize_plasma_profile(outer_raw, holes_raw)

    try:
        if len(outer_raw) < 3:
            return sanitize_plasma_profile(outer_raw, holes_raw)
        outer_poly = Polygon(outer_raw, holes_raw if holes_raw else None)
        if outer_poly.is_empty:
            return sanitize_plasma_profile(outer_raw, holes_raw)
        outer_buf = outer_poly.buffer(float(offset_mm), join_style=2)
        if outer_buf.is_empty:
            return sanitize_plasma_profile(outer_raw, holes_raw)
        if isinstance(outer_buf, MultiPolygon):
            outer_buf = max(outer_buf.geoms, key=lambda g: float(g.area))
        plasma_outer = list(outer_buf.exterior.coords)
        plasma_holes = []
        for h in holes_raw:
            try:
                hp = Polygon(h)
                if hp.is_empty:
                    continue
                hc = hp.buffer(-float(offset_mm), join_style=2)
                if hc.is_empty:
                    continue
                if isinstance(hc, MultiPolygon):
                    hc = max(hc.geoms, key=lambda g: float(g.area))
                plasma_holes.append(list(hc.exterior.coords))
            except Exception:
                continue
        return sanitize_plasma_profile(plasma_outer, plasma_holes)
    except Exception:
        return sanitize_plasma_profile(outer_raw, holes_raw)


def sanitize_plasma_profile(outer, holes):
    """Limpieza suave; nunca descarta un contorno con vértices válidos."""
    outer_s = _sanitize_ring_coords(outer or [])
    holes_s = []
    for h in holes or []:
        hh = _sanitize_ring_coords(h)
        if len(hh) >= 3:
            holes_s.append(hh)

    if len(outer_s) >= 3:
        return outer_s, holes_s

    raw = []
    for pt in outer or []:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            try:
                raw.append((float(pt[0]), float(pt[1])))
            except Exception:
                pass
    if len(raw) >= 3:
        return raw, holes_s
    return outer_s, holes_s


def _resolve_plasma_profile(p: dict) -> tuple[list, list]:
    from modules.nesting_engine.exporter import _clean_profile_for_production

    outer = list(p.get("outer") or p.get("outer_poly") or [])
    holes = [list(h) for h in (p.get("holes") or p.get("inner") or []) if h]

    if len(outer) < 3:
        pols = list(p.get("nested_poligonos") or [])
        if not pols:
            pols = [outer] + holes if outer else []
        outer, holes = _rings_from_poligonos(pols)

    if len(outer) < 3:
        pols = list(p.get("nested_poligonos") or [])
        outer, holes = build_plasma_profile_from_nested(
            pols,
            offset_mm=float(p.get("plasma_offset_mm") or 0.0),
            already_compensated=bool(p.get("compensated")),
        )

    if len(outer) >= 3 or holes:
        outer, holes = _clean_profile_for_production(outer, holes)
    return outer, holes


def _outer_entities_span_mm(entities) -> float:
    xs: list[float] = []
    ys: list[float] = []
    for ent in entities or []:
        try:
            if ent.dxftype() == "LINE":
                xs.extend([float(ent.dxf.start.x), float(ent.dxf.end.x)])
                ys.extend([float(ent.dxf.start.y), float(ent.dxf.end.y)])
            elif ent.dxftype() == "ARC":
                r = float(ent.dxf.radius)
                cx, cy = float(ent.dxf.center.x), float(ent.dxf.center.y)
                xs.extend([cx - r, cx + r])
                ys.extend([cy - r, cy + r])
            elif ent.dxftype() == "CIRCLE":
                r = float(ent.dxf.radius)
                cx, cy = float(ent.dxf.center.x), float(ent.dxf.center.y)
                xs.extend([cx - r, cx + r])
                ys.extend([cy - r, cy + r])
        except Exception:
            continue
    if not xs:
        return 0.0
    return max(max(xs) - min(xs), max(ys) - min(ys))


def export_plasma_placement(msp, p: dict, *, draw_holes: bool = True, draw_marks: bool = True) -> bool:
    """
    Exporta plasma desde polígonos del nest (mm de placa), igual que láser.
    No usa AutoDXF fuente: el fallback anterior colapsaba piezas a círculos basura.
    """
    from modules.nest_exporter import _export_placed_geometry, _msp_count, _msp_snapshot

    outer, holes = _resolve_plasma_profile(p)
    if len(outer) < 3:
        return False

    p_export = dict(p)
    p_export["outer"] = outer
    p_export["holes"] = holes
    p_export["use_native_curves"] = False
    p_export.pop("ruta", None)
    p_export.pop("compensated_plasma_source", None)

    count_before = _msp_count(msp)
    if not _export_placed_geometry(msp, p_export, draw_holes=draw_holes, draw_marks=draw_marks):
        return False

    new_entities = _msp_snapshot(msp)[count_before:]
    outer_ents = [
        e for e in new_entities
        if str(getattr(e.dxf, "layer", "") or "") == "CUT_OUTER"
    ]
    if not outer_ents:
        return False

    span = _outer_entities_span_mm(outer_ents)
    if span < _MIN_OUTER_SPAN_MM:
        return False

    only_tiny_circles = all(
        e.dxftype() == "CIRCLE" and float(e.dxf.radius) < _MIN_OUTER_SPAN_MM * 0.5
        for e in outer_ents
    )
    return not only_tiny_circles


def _offset_inches(offset_mm: float) -> float:
    return float(offset_mm) / float(ESCALA_DXF)


def _compensate_circle(entity, offset_mm: float, *, outward: bool):
    if entity.dxftype() != "CIRCLE":
        return None
    off_in = _offset_inches(offset_mm)
    sign = 1.0 if outward else -1.0
    r_new = float(entity.dxf.radius) + sign * off_in
    if r_new <= 1e-9:
        return None
    out = entity.copy()
    out.dxf.radius = r_new
    return out


def _compensate_arc(entity, offset_mm: float, *, outward: bool):
    if entity.dxftype() != "ARC":
        return None
    off_in = _offset_inches(offset_mm)
    sign = 1.0 if outward else -1.0
    r_new = float(entity.dxf.radius) + sign * off_in
    if r_new <= 1e-9:
        return None
    out = entity.copy()
    out.dxf.radius = r_new
    return out


def _compensate_line(entity, offset_mm: float, *, outward: bool):
    if entity.dxftype() != "LINE":
        return None
    off_in = _offset_inches(offset_mm)
    sign = 1.0 if outward else -1.0
    s = entity.dxf.start
    e = entity.dxf.end
    x1, y1 = float(s.x), float(s.y)
    x2, y2 = float(e.x), float(e.y)
    dx, dy = x2 - x1, y2 - y1
    ln = math.hypot(dx, dy)
    if ln < 1e-12:
        return None
    nx, ny = -dy / ln, dx / ln
    d = sign * off_in
    out = entity.copy()
    out.dxf.start = (x1 + nx * d, y1 + ny * d, float(getattr(s, "z", 0) or 0))
    out.dxf.end = (x2 + nx * d, y2 + ny * d, float(getattr(e, "z", 0) or 0))
    return out


def _compensate_closed_polyline(entity, offset_mm: float, *, outward: bool):
    if entity.dxftype() not in ("LWPOLYLINE", "POLYLINE"):
        return None
    pts = _entity_points_xy(entity)
    if not pts:
        return None
    off_in = _offset_inches(offset_mm)
    sign = 1.0 if outward else -1.0
    return _buffer_polygon_points(pts, sign * off_in)


def export_compensated_plasma_from_source(
    msp,
    doc,
    p: dict,
    *,
    draw_holes: bool = True,
    draw_marks: bool = True,
) -> dict:
    """Respaldo legacy: compensa AutoDXF fuente. No usar en export compensado del nest."""
    from modules.nest_exporter import (
        _build_placement_matrix,
        _import_layers_from_source,
        _placement_origin_mm,
        _write_native_entity,
    )

    ruta = str(p.get("ruta") or "").strip()
    offset_mm = float(p.get("plasma_offset_mm") or 0.0)
    stats = {"ok": False, "outer": 0, "inner": 0}
    if not ruta or not os.path.isfile(ruta) or offset_mm <= 0:
        return stats

    try:
        part_doc = ezdxf.readfile(ruta)
    except Exception:
        return stats

    m = _build_placement_matrix(p)
    layers_used: set[str] = set()
    ox, oy = _placement_origin_mm(p)
    rot = math.radians(float(p.get("rot_deg", 0.0) or 0.0))
    sx = float(p.get("shift_x", 0.0) or 0.0)
    sy = float(p.get("shift_y", 0.0) or 0.0)
    rcx = float(p.get("rot_origin_cx", 0.0) or 0.0)
    rcy = float(p.get("rot_origin_cy", 0.0) or 0.0)

    def _rot_pt(x: float, y: float) -> Point:
        if abs(rot) < 1e-12:
            return x, y
        dx, dy = x - rcx, y - rcy
        c, s = math.cos(rot), math.sin(rot)
        return rcx + dx * c - dy * s, rcy + dx * s + dy * c

    def _transform_ring_to_plate(ring: List[Point]) -> List[Point]:
        out: List[Point] = []
        for raw in ring:
            if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                continue
            x = float(raw[0]) * ESCALA_DXF - ox
            y = float(raw[1]) * ESCALA_DXF - oy
            x, y = _rot_pt(x, y)
            out.append((x + sx, y + sy))
        return out

    for entity in part_doc.modelspace():
        if entity.dxftype() not in (
            "LINE",
            "LWPOLYLINE",
            "POLYLINE",
            "ARC",
            "CIRCLE",
            "ELLIPSE",
            "SPLINE",
        ):
            continue

        clase = _clasificar_capa(str(entity.dxf.layer))
        if clase is None:
            continue
        if clase == "mark" and not draw_marks:
            continue
        if clase == "inner" and not draw_holes:
            continue
        if clase not in ("outer", "inner", "mark"):
            continue

        outward = clase == "outer"
        layer = "CUT_OUTER" if clase == "outer" else ("CUT_INNER" if clase == "inner" else "MARK")
        typ = entity.dxftype()

        compensated = None
        poly_rings = None
        if typ == "CIRCLE":
            compensated = _compensate_circle(entity, offset_mm, outward=outward)
        elif typ == "ARC":
            compensated = _compensate_arc(entity, offset_mm, outward=outward)
        elif typ == "LINE" and clase in ("outer", "inner"):
            compensated = _compensate_line(entity, offset_mm, outward=outward)
        elif typ in ("LWPOLYLINE", "POLYLINE"):
            poly_rings = _compensate_closed_polyline(entity, offset_mm, outward=outward)

        if compensated is not None:
            try:
                new_e = compensated.copy()
                if not new_e.transform(m):
                    continue
                n = _write_native_entity(msp, new_e, layer)
                if n > 0:
                    stats["ok"] = True
                    if clase == "outer":
                        stats["outer"] += n
                    elif clase == "inner":
                        stats["inner"] += n
                    layers_used.add(layer)
            except Exception:
                continue
            continue

        if poly_rings:
            for ring in poly_rings:
                ring_mm = _transform_ring_to_plate(ring)
                if export_ring_native(
                    msp,
                    ring_mm,
                    layer,
                    closed=True,
                    prefer_circle=(clase == "inner"),
                ):
                    stats["ok"] = True
                    if clase == "outer":
                        stats["outer"] += 1
                    elif clase == "inner":
                        stats["inner"] += 1
                    layers_used.add(layer)

    if doc is not None and layers_used:
        _import_layers_from_source(part_doc, doc, layers_used)

    return stats
