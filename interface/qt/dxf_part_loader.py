"""Carga DXF de pieza → métricas + snap para visor Qt (render vía ezdxf PyQtBackend)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import ezdxf
import numpy as np
from ezdxf import path

from interface.qt.dxf_part_geometry import (
    build_snap_context,
    capa_relevante_visual,
    centroid_2d,
    clasificar_contornos_cerrados,
    dxf_arc_ccw_sweep_rad,
    es_cut_layer,
    es_inner_layer,
    es_mark_layer,
    es_outer_layer,
    poly_area_2d,
    rol_capa_pieza,
    rotar_punto,
)
from interface.qt.dxf_qt_renderer import rotate_modelspace
# Serializa el parseo ezdxf contra el audit thread. Ver modules/dxf_thread_lock.py.
from modules.dxf_thread_lock import EZDXF_LOCK


@dataclass
class DxfPartModel:
    factor_conversion: float = 25.4
    render_all_layers: bool = False
    centro_pieza: tuple[float, float] = (0.0, 0.0)
    fit_rect: tuple[float, float, float, float] | None = None
    min_x_raw: float = 0.0
    max_x_raw: float = 0.0
    min_y_raw: float = 0.0
    max_y_raw: float = 0.0
    perimetro_total: float = 0.0
    area_neta: float = 0.0
    piece_span: float = 1.0
    snap_ctx: object = None
    doc: object = None
    msp: object = None
    # Contornos OUTER en coords de escena (unidades DXF), para overlay plasma.
    outer_rings: list = field(default_factory=list)


def _insunits_factor(doc) -> float:
    insunits = int(doc.header.get("$INSUNITS", 0) or 0)
    if insunits == 1:
        return 1.0
    if insunits == 4:
        return 25.4
    if insunits == 5:
        return 2.54
    if insunits == 2:
        return 12.0
    return 25.4


def _agregar_shapes_desde_line_arc(
    entidades: list,
    rol: str,
    shapes_cerrados: list,
    rot: int,
    cx: float,
    cy: float,
) -> None:
    """Encadena LINE+ARC de una capa CUT en anillos cerrados y los agrega a shapes_cerrados.

    Este es el puente entre DXF Inventor (cortes hechos de LINE/ARC sueltos) y
    la clasificación outer/inner que alimenta ``outer_rings`` — sin este puente,
    el visor no puede pintar el énfasis plasma sobre perfiles Inventor.
    """
    if not entidades:
        return
    try:
        from modules.plasma_dxf_export import (
            _flatten_entity_group_inches,
            _group_connected_cut_entities,
        )
    except Exception:
        return
    try:
        grupos = _group_connected_cut_entities(entidades, tol=0.05)
    except Exception:
        grupos = []
    for grupo in grupos or []:
        try:
            pts = _flatten_entity_group_inches(grupo, flat_in=0.01)
        except Exception:
            continue
        if not pts or len(pts) < 3:
            continue
        # Solo aceptamos anillos genuinamente cerrados (< 0.05" gap tras densificar).
        if math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) > 0.05:
            continue
        pts_ring = [(float(x), float(y)) for x, y in pts]
        if rot:
            pts_ring = [rotar_punto(x, y, cx, cy, rot) for (x, y) in pts_ring]
        area_abs = abs(poly_area_2d(pts_ring))
        if area_abs <= 1e-9:
            continue
        shapes_cerrados.append(
            {
                "kind": "poly",
                "pts": pts_ring,
                "area": area_abs,
                "rol": rol,
            }
        )


def load_dxf_part(ruta_dxf: str, rotacion_vista_deg: int = 0) -> DxfPartModel | None:
    """Carga y parsea el DXF bajo `EZDXF_LOCK` (evita race con audit thread)."""
    with EZDXF_LOCK:
        return _load_dxf_part_impl(ruta_dxf, rotacion_vista_deg)


def _load_dxf_part_impl(ruta_dxf: str, rotacion_vista_deg: int = 0) -> DxfPartModel | None:
    try:
        doc = ezdxf.readfile(ruta_dxf)
    except Exception:
        return None

    model = DxfPartModel()
    model.doc = doc
    model.factor_conversion = _insunits_factor(doc)
    msp = doc.modelspace()
    model.msp = msp
    entities = list(msp)

    has_relevant = any(
        capa_relevante_visual(str(e.dxf.layer), False) for e in entities
    )
    model.render_all_layers = not has_relevant

    perimetro_total = 0.0
    area_neta = 0.0
    contornos = []
    all_points_raw = []
    circulos_raw = []
    # DXF exportados por Inventor (IV_OUTER_PROFILE, IV_INTERIOR_PROFILES) entregan
    # el contorno como decenas de LINE+ARC sueltos. Guardamos las entidades para
    # encadenarlas después en anillos cerrados: si no lo hacemos, `outer_rings`
    # queda vacío y el énfasis plasma (rojo + "+X"") no se pinta.
    outer_line_arc_raw: list = []
    inner_line_arc_raw: list = []
    distancia_suavizado = 0.05 * (model.factor_conversion / 25.4)

    for entity in entities:
        layer = entity.dxf.layer.upper()
        if not capa_relevante_visual(layer, model.render_all_layers):
            continue
        typ = entity.dxftype()

        if typ == "CIRCLE":
            try:
                c = entity.dxf.center
                r = float(entity.dxf.radius)
                if r <= 0:
                    continue
                cx, cy = float(c.x), float(c.y)
                circulos_raw.append((layer, cx, cy, r))
                all_points_raw.extend(
                    [(cx - r, cy - r), (cx + r, cy - r), (cx - r, cy + r), (cx + r, cy + r)]
                )
                if es_cut_layer(layer) or model.render_all_layers:
                    perimetro_total += 2.0 * math.pi * r
                    if es_outer_layer(layer):
                        area_neta += math.pi * r * r
                    elif es_inner_layer(layer):
                        area_neta -= math.pi * r * r
            except Exception:
                pass
            continue

        if typ == "ARC":
            try:
                c = entity.dxf.center
                r = float(entity.dxf.radius)
                sa = float(entity.dxf.start_angle)
                ea = float(entity.dxf.end_angle)
                if r <= 0:
                    continue
                cx, cy = float(c.x), float(c.y)
                t0, sweep = dxf_arc_ccw_sweep_rad(sa, ea)
                if es_cut_layer(layer) or model.render_all_layers:
                    perimetro_total += r * sweep
                n = max(8, int(math.degrees(sweep) / 4) + 1)
                for i in range(n + 1):
                    u = t0 + sweep * (i / max(1, n))
                    all_points_raw.append((cx + r * math.cos(u), cy + r * math.sin(u)))
                contornos.append(("ARC", layer, cx, cy, r, sa, ea))
                if es_outer_layer(layer):
                    outer_line_arc_raw.append(entity)
                elif es_inner_layer(layer):
                    inner_line_arc_raw.append(entity)
            except Exception:
                pass
            continue

        if typ == "LINE" and (es_outer_layer(layer) or es_inner_layer(layer)):
            try:
                s = entity.dxf.start
                e = entity.dxf.end
                p0 = (float(s.x), float(s.y))
                p1 = (float(e.x), float(e.y))
                all_points_raw.extend([p0, p1])
                if es_cut_layer(layer) or model.render_all_layers:
                    perimetro_total += math.hypot(p1[0] - p0[0], p1[1] - p0[1])
                # Guardamos la entidad para encadenar el anillo cerrado después.
                if es_outer_layer(layer):
                    outer_line_arc_raw.append(entity)
                else:
                    inner_line_arc_raw.append(entity)
            except Exception:
                pass
            continue

        try:
            p = path.make_path(entity)
            vertices = list(p.flattening(distance=distancia_suavizado))
            v2d = [(v[0], v[1]) for v in vertices]
            if len(v2d) < 2:
                continue
            if es_cut_layer(layer) or model.render_all_layers:
                for i in range(len(v2d) - 1):
                    perimetro_total += math.hypot(
                        v2d[i + 1][0] - v2d[i][0], v2d[i + 1][1] - v2d[i][1]
                    )
            all_points_raw.extend(v2d)
            contornos.append(("POLY", layer, v2d, bool(p.is_closed)))
        except Exception:
            pass

    if all_points_raw:
        xs = [pt[0] for pt in all_points_raw]
        ys = [pt[1] for pt in all_points_raw]
        cx = (min(xs) + max(xs)) * 0.5
        cy = (min(ys) + max(ys)) * 0.5
    else:
        cx, cy = 0.0, 0.0

    rot = int(rotacion_vista_deg) % 360
    if rot:
        rotate_modelspace(msp, cx, cy, rot)

    model.snap_ctx = build_snap_context(entities, model.render_all_layers)

    shapes_cerrados: list = []
    for layer_c, rcx0, rcy0, rr in circulos_raw:
        rcx, rcy = rotar_punto(rcx0, rcy0, cx, cy, rot) if rot else (rcx0, rcy0)
        if es_cut_layer(layer_c) or model.render_all_layers or es_outer_layer(layer_c) or es_inner_layer(layer_c):
            nang = 48
            poly_circ = [
                (
                    rcx + rr * math.cos(2 * math.pi * k / nang),
                    rcy + rr * math.sin(2 * math.pi * k / nang),
                )
                for k in range(nang)
            ]
            shapes_cerrados.append(
                {
                    "kind": "circle",
                    "rcx": rcx,
                    "rcy": rcy,
                    "rr": rr,
                    "pts": poly_circ,
                    "rol": rol_capa_pieza(layer_c),
                }
            )

    for item in contornos:
        if item[0] != "POLY":
            continue
        _, layer_p, pts_raw, is_closed = item
        pts = [rotar_punto(x, y, cx, cy, rot) for (x, y) in pts_raw] if rot else pts_raw
        if is_closed and len(pts) >= 3 and (
            es_cut_layer(layer_p)
            or model.render_all_layers
            or es_outer_layer(layer_p)
            or es_inner_layer(layer_p)
        ):
            shapes_cerrados.append(
                {
                    "kind": "poly",
                    "pts": pts,
                    "area": abs(poly_area_2d(pts)),
                    "rol": rol_capa_pieza(layer_p),
                }
            )

    # Piezas exportadas por Inventor traen el contorno como decenas de LINE+ARC
    # sueltos (sin LWPOLYLINE). Encadenamos aquí cada perfil para que el visor
    # sepa pintar el énfasis plasma rojo. Sin este stitching, `outer_rings` queda
    # vacío para brackets tipo GENE-BKT-101 y el usuario no ve la marca "+X"".
    _agregar_shapes_desde_line_arc(
        outer_line_arc_raw, "outer", shapes_cerrados, rot, cx, cy
    )
    _agregar_shapes_desde_line_arc(
        inner_line_arc_raw, "inner", shapes_cerrados, rot, cx, cy
    )

    outers, inners = clasificar_contornos_cerrados(shapes_cerrados)
    model.outer_rings = []
    for sh in outers:
        if sh.get("kind") == "circle":
            rr = float(sh.get("rr") or sh.get("r") or 0.0)
            cx = float(sh.get("cx") or 0.0)
            cy = float(sh.get("cy") or 0.0)
            area_neta += math.pi * rr ** 2
            if rr > 0:
                ring = [
                    (
                        cx + rr * math.cos(2.0 * math.pi * i / 24.0),
                        cy + rr * math.sin(2.0 * math.pi * i / 24.0),
                    )
                    for i in range(24)
                ]
                model.outer_rings.append(ring)
        else:
            pts = list(sh.get("pts") or [])
            area_neta += poly_area_2d(pts)
            if len(pts) >= 3:
                model.outer_rings.append([(float(x), float(y)) for x, y in pts])
    for sh in inners:
        if sh.get("kind") == "circle":
            area_neta -= math.pi * sh["rr"] ** 2
        else:
            area_neta -= poly_area_2d(sh.get("pts") or [])

    model.perimetro_total = perimetro_total
    model.area_neta = max(0.0, area_neta)

    if all_points_raw:
        model.min_x_raw = min(p[0] for p in all_points_raw)
        model.max_x_raw = max(p[0] for p in all_points_raw)
        model.min_y_raw = min(p[1] for p in all_points_raw)
        model.max_y_raw = max(p[1] for p in all_points_raw)
    else:
        model.min_x_raw = model.max_x_raw = model.min_y_raw = model.max_y_raw = 0.0

    snap = model.snap_ctx
    if snap and len(snap.vertices):
        model.centro_pieza = (
            float(np.mean(snap.vertices[:, 0])),
            float(np.mean(snap.vertices[:, 1])),
        )
    elif all_points_raw:
        model.centro_pieza = (
            float(sum(p[0] for p in all_points_raw)) / len(all_points_raw),
            float(sum(p[1] for p in all_points_raw)) / len(all_points_raw),
        )

    return model
