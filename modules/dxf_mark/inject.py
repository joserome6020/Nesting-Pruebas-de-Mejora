"""
Inyecta marcaje stick (capa MARK) en un DXF de pieza.

Texto por defecto: primer segmento del nombre de archivo
  "62135-1247-P01, A 36, QTY 1, Cal 0.375.dxf" -> "62135-1247-P01"

Altura visible: arranca en 0.35 in y se reescala al tamaño de la pieza
(banda preferida 0.25–0.35 in; piezas pequeñas pueden bajar hasta 0.08 in).
No solapa con ninguna geometría existente (cortes, marks, líneas, etc.).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import ezdxf
from ezdxf.path import make_path

from modules.dxf_mark.place_mark import (
    PolyData,
    find_mark_position,
    make_poly_data,
    segments_from_points,
)
from modules.dxf_mark.stick_font import measure_text_aabb, normalize_mark_text

MARK_LAYER = "MARK"
AUTODXF_MARK_LAYER = "IV_MARK_SURFACE_BACK"
# Tope máximo / arranque del reescalado.
MAX_MARK_HEIGHT_IN = 0.35
# Tope inferior preferido: no bajar de aquí salvo piezas pequeñas.
PREFERRED_MIN_MARK_HEIGHT_IN = 0.25
DEFAULT_TEXT_HEIGHT_IN = MAX_MARK_HEIGHT_IN
DEFAULT_CLEARANCE_IN = 0.08
# Altura visible mínima absoluta al re-escalar en piezas pequeñas.
MIN_MARK_HEIGHT_IN = 0.08
# Escalones de re-escalado (fracción de la altura solicitada/ajustada).
HEIGHT_SCALE_STEPS = (1.0, 0.92, 0.85, 0.78, 0.72, 0.65, 0.60, 0.50, 0.42)
STICK_VISIBLE_HEIGHT_FACTOR = 0.84
STICK_APPID = "ARGA_STICK"
STICK_HEADER_VAR = "ARGA_STICK_MARK"

# INSUNITS → factor a pulgadas (1 drawing unit * factor = inches).
# https://ezdxf.readthedocs.io/en/stable/dxfentities/header.html
_INSUNITS_TO_INCH = {
    0: 1.0,  # unitless → asumir pulgadas (convención del suite)
    1: 1.0,  # inches
    2: 12.0,  # feet
    4: 1.0 / 25.4,  # mm
    5: 1.0 / 2.54,  # cm
    6: 39.37007874015748,  # m
}


def mark_text_from_dxf_path(path: str | Path) -> str:
    """Extrae el código de pieza del nombre del DXF (antes de la 1ª coma)."""
    stem = Path(path).stem.strip()
    if not stem:
        return "MARK"
    head = stem.split(",", 1)[0].strip()
    return normalize_mark_text(head) or normalize_mark_text(stem) or "MARK"


def drawing_units_per_inch(doc) -> float:
    """Cuántas unidades de dibujo equivalen a 1 pulgada."""
    try:
        insunits = int(doc.header.get("$INSUNITS", 0) or 0)
    except Exception:
        insunits = 0
    to_inch = _INSUNITS_TO_INCH.get(insunits, 1.0)
    if to_inch <= 0:
        return 1.0
    return 1.0 / to_inch


def ensure_mark_layer(doc, layer_name: str = MARK_LAYER, *, color: int = 4) -> None:
    if layer_name in doc.layers:
        return
    doc.layers.add(layer_name, dxfattribs={"color": int(color)})


def _ensure_stick_appid(doc) -> None:
    try:
        if STICK_APPID not in doc.appids:
            doc.appids.add(STICK_APPID)
    except Exception:
        pass


def _tag_stick_entity(entity, mark_text: str) -> None:
    """Marca la entidad como marcaje stick de Arga (para no reinyectar)."""
    try:
        entity.set_xdata(STICK_APPID, [(1000, str(mark_text or "STICK")[:250])])
    except Exception:
        pass


def _entity_has_stick_tag(entity) -> bool:
    try:
        xd = entity.get_xdata(STICK_APPID)
        return bool(xd)
    except Exception:
        return False


def _doc_has_stick_header(doc) -> bool:
    try:
        custom = getattr(doc.header, "custom_vars", None)
        if custom is None:
            return False
        for name, _val in custom:
            if str(name).upper().lstrip("$") == STICK_HEADER_VAR:
                return True
    except Exception:
        pass
    return False


def _set_stick_header(doc, mark_text: str) -> None:
    try:
        custom = getattr(doc.header, "custom_vars", None)
        if custom is None:
            return
        # Quita previas y escribe una sola.
        try:
            while True:
                custom.remove(STICK_HEADER_VAR)
        except Exception:
            pass
        try:
            while True:
                custom.remove(f"${STICK_HEADER_VAR}")
        except Exception:
            pass
        custom.add(STICK_HEADER_VAR, str(mark_text or "1")[:250])
    except Exception:
        pass


def _stick_like_open_polys(msp, layer_names: set[str], units_per_in: float) -> bool:
    """Heurística: capa de mark con varios trazos abiertos a ~0.25–0.35 in de alto."""
    target = {n.upper() for n in layer_names}
    xs: list[float] = []
    ys: list[float] = []
    open_count = 0
    for e in msp:
        lyr = str(getattr(e.dxf, "layer", "") or "").upper()
        if lyr not in target:
            continue
        dxftype = e.dxftype()
        if dxftype == "LINE":
            try:
                xs.extend([float(e.dxf.start.x), float(e.dxf.end.x)])
                ys.extend([float(e.dxf.start.y), float(e.dxf.end.y)])
                open_count += 1
            except Exception:
                continue
        elif dxftype == "LWPOLYLINE":
            if bool(getattr(e, "closed", False)):
                continue
            pts = [(float(v[0]), float(v[1])) for v in e.get_points("xy")]
            if len(pts) < 2:
                continue
            open_count += 1
            xs.extend(p[0] for p in pts)
            ys.extend(p[1] for p in pts)
        else:
            continue
    if open_count < 3 or not xs or not ys:
        return False
    # Con texto vertical el span largo es la lectura; el corto ≈ altura de letra.
    glyph_h = min(max(xs) - min(xs), max(ys) - min(ys))
    lo = PREFERRED_MIN_MARK_HEIGHT_IN * units_per_in * 0.65
    hi = MAX_MARK_HEIGHT_IN * units_per_in * 1.40
    return lo <= glyph_h <= hi


def _height_candidates_stroke(
    h0: float,
    *,
    preferred_min_stroke: float,
    min_stroke: float,
) -> list[float]:
    """Candidatos de altura: primero banda 0.25–0.35, luego piezas pequeñas."""
    raw: list[float] = []
    for f in HEIGHT_SCALE_STEPS:
        hh = h0 * float(f)
        if hh >= min_stroke - 1e-9:
            raw.append(hh)
    if not raw or raw[-1] > min_stroke * 1.05:
        raw.append(min_stroke)

    preferred: list[float] = []
    small: list[float] = []
    seen: set[float] = set()
    for hh in raw:
        key = round(hh, 6)
        if key in seen:
            continue
        seen.add(key)
        if hh + 1e-9 >= preferred_min_stroke:
            preferred.append(hh)
        else:
            small.append(hh)
    # Si el fit inicial quedó bajo el preferido, igual intentamos el piso 0.25 primero.
    if h0 + 1e-9 >= preferred_min_stroke and (
        not preferred or preferred[-1] > preferred_min_stroke * 1.02
    ):
        preferred.append(preferred_min_stroke)
    return preferred + small


def tiene_marcaje_stick(path: str | Path) -> bool:
    """
    True si el DXF ya tiene marcaje stick del script Arga:
    - XDATA ARGA_STICK, o
    - header custom ARGA_STICK_MARK, o
    - nombre *_MARKED*, o
    - capa IV_MARK_SURFACE_BACK / MARK con trazos stick típicos.
    """
    p = Path(path)
    if not p.is_file():
        return False
    if "_MARKED" in p.stem.upper():
        return True
    try:
        doc = ezdxf.readfile(str(p))
    except Exception:
        return False
    if _doc_has_stick_header(doc):
        return True
    msp = doc.modelspace()
    for e in msp:
        if _entity_has_stick_tag(e):
            return True
    units = drawing_units_per_inch(doc)
    return _stick_like_open_polys(
        msp,
        {MARK_LAYER, AUTODXF_MARK_LAYER, "IV_MARK_SURFACE", "IV_MARK"},
        units,
    )


def _lwpolyline_points(entity, flatten_dist: float) -> list[tuple[float, float]]:
    try:
        path = make_path(entity)
        pts = [(float(v.x), float(v.y)) for v in path.flattening(distance=max(flatten_dist, 1e-4))]
        if pts and entity.closed and pts[0] != pts[-1]:
            pts.append(pts[0])
        return pts
    except Exception:
        pts = [(float(v[0]), float(v[1])) for v in entity.get_points("xy")]
        if pts and bool(entity.closed) and pts[0] != pts[-1]:
            pts.append(pts[0])
        return pts


def _snap_xy(x: float, y: float, ndigits: int = 6) -> tuple[float, float]:
    """Redondea para que extremos de ARC/LINE coincidan al unir contornos."""
    return (round(float(x), ndigits), round(float(y), ndigits))


def _entity_points(entity, flatten_dist: float) -> tuple[list[tuple[float, float]], bool]:
    """(puntos, closed_hint)."""
    typ = entity.dxftype()
    if typ == "LWPOLYLINE":
        pts = _lwpolyline_points(entity, flatten_dist)
        return [_snap_xy(x, y) for x, y in pts], bool(entity.closed)
    if typ == "LINE":
        s, e = entity.dxf.start, entity.dxf.end
        return [_snap_xy(s.x, s.y), _snap_xy(e.x, e.y)], False
    if typ == "CIRCLE":
        try:
            path = make_path(entity)
            pts = [_snap_xy(v.x, v.y) for v in path.flattening(distance=max(flatten_dist, 1e-4))]
            if pts and pts[0] != pts[-1]:
                pts.append(pts[0])
            return pts, True
        except Exception:
            return [], False
    if typ == "ARC":
        # Extremos exactos del ARC + interior aplanado (Inventor IV_OUTER_PROFILE).
        try:
            cx, cy = float(entity.dxf.center.x), float(entity.dxf.center.y)
            r = float(entity.dxf.radius)
            sa = math.radians(float(entity.dxf.start_angle))
            ea = math.radians(float(entity.dxf.end_angle))
            sweep = (ea - sa) % (2.0 * math.pi)
            if sweep < 1e-12:
                sweep = 2.0 * math.pi
            n = max(8, int(math.ceil(r * sweep / max(flatten_dist, 1e-4))))
            pts = [
                _snap_xy(cx + r * math.cos(sa + sweep * i / n), cy + r * math.sin(sa + sweep * i / n))
                for i in range(n + 1)
            ]
            return pts, False
        except Exception:
            return [], False
    if typ in ("ELLIPSE", "SPLINE", "POLYLINE"):
        try:
            path = make_path(entity)
            pts = [_snap_xy(v.x, v.y) for v in path.flattening(distance=max(flatten_dist, 1e-4))]
            closed = False
            if typ == "POLYLINE":
                closed = bool(getattr(entity, "is_closed", False))
            elif typ == "ELLIPSE":
                closed = abs(float(entity.dxf.start_param) - float(entity.dxf.end_param)) >= (
                    2.0 * math.pi - 1e-6
                )
            if closed and pts and pts[0] != pts[-1]:
                pts.append(pts[0])
            return pts, closed
        except Exception:
            return [], False
    return [], False


def _layer_role(layer: str) -> str | None:
    u = str(layer or "").upper().strip()
    if any(m in u for m in ("MARK", "ETCH", "SCRIBE", "ENGRAVE", "GRABADO", "MARCAJE", "IV_MARK", "TEXT")):
        return "mark"
    if ("CUT_INNER" in u) or ("IV_INTERIOR" in u) or ("INTERIOR" in u and "OUTER" not in u):
        return "inner"
    if ("CUT_OUTER" in u) or ("IV_OUTER" in u) or u in ("0", "OUTER", "CORTE_EXTERNO"):
        return "outer"
    if "CUT" in u:
        return "outer"
    return None


@dataclass
class InjectResult:
    input_path: Path
    output_path: Path
    mark_text: str
    height_du: float
    components_marked: int
    components_skipped: int
    already_marked: bool = False


def _polydata_from_ring(coords) -> PolyData | None:
    ring = [(float(x), float(y)) for x, y in coords]
    if ring and ring[0] == ring[-1]:
        ring = ring[:-1]
    return make_poly_data(ring)


def _polys_from_open_segments(
    segs: list[tuple[tuple[float, float], tuple[float, float]]],
) -> list[PolyData]:
    """Une LINE/arcos abiertos (p. ej. IV_OUTER_PROFILE) en polígonos cerrados."""
    if not segs:
        return []

    # Snap para cerrar micro-gaps entre ARC y LINE de Inventor/AutoDXF.
    snapped = [
        (_snap_xy(a[0], a[1]), _snap_xy(b[0], b[1]))
        for a, b in segs
        if a != b
    ]
    snapped = [(a, b) for a, b in snapped if a != b]
    if not snapped:
        return []

    try:
        from shapely.geometry import LineString, Polygon
        from shapely.ops import linemerge, polygonize
    except ImportError:
        return _polys_from_open_segments_fallback(snapped)

    lineas = [LineString([a, b]) for a, b in snapped]
    polys: list[PolyData] = []

    def _absorb(poly_like) -> None:
        if poly_like is None or getattr(poly_like, "is_empty", True):
            return
        if poly_like.geom_type == "Polygon":
            pd = _polydata_from_ring(poly_like.exterior.coords)
            if pd:
                polys.append(pd)
        elif poly_like.geom_type == "MultiPolygon":
            for g in poly_like.geoms:
                _absorb(g)

    try:
        for poly in polygonize(lineas):
            _absorb(poly)
    except Exception:
        pass
    if polys:
        return polys

    try:
        merged = linemerge(lineas)
        geoms = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
        # Cierra LineStrings casi cerrados (anillo ARC+LINE).
        closed_rings = []
        for g in geoms:
            if g.geom_type != "LineString" or len(g.coords) < 4:
                continue
            coords = list(g.coords)
            a, b = coords[0], coords[-1]
            if math.hypot(a[0] - b[0], a[1] - b[1]) <= 1e-3:
                coords = coords + [coords[0]]
            if coords[0] == coords[-1]:
                closed_rings.append(LineString(coords))
                try:
                    _absorb(Polygon(coords))
                except Exception:
                    pass
        if not polys and closed_rings:
            for poly in polygonize(closed_rings):
                _absorb(poly)
        if not polys:
            for poly in polygonize(geoms):
                _absorb(poly)
    except Exception:
        pass

    if polys:
        return polys
    return _polys_from_open_segments_fallback(snapped)


def _polys_from_open_segments_fallback(
    segs: list[tuple[tuple[float, float], tuple[float, float]]],
) -> list[PolyData]:
    """Encadena segmentos por extremos cercanos cuando no hay shapely."""
    unused = list(segs)
    out: list[PolyData] = []
    tol = 1e-4

    def near(a, b) -> bool:
        return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol

    while unused:
        a, b = unused.pop(0)
        chain = [a, b]
        progressed = True
        while progressed:
            progressed = False
            for i, (s, e) in enumerate(unused):
                if near(chain[-1], s):
                    chain.append(e)
                    unused.pop(i)
                    progressed = True
                    break
                if near(chain[-1], e):
                    chain.append(s)
                    unused.pop(i)
                    progressed = True
                    break
                if near(chain[0], e):
                    chain.insert(0, s)
                    unused.pop(i)
                    progressed = True
                    break
                if near(chain[0], s):
                    chain.insert(0, e)
                    unused.pop(i)
                    progressed = True
                    break
        if len(chain) >= 4 and near(chain[0], chain[-1]):
            pd = make_poly_data(chain[:-1])
            if pd:
                out.append(pd)
    return out


def collect_geometry(msp, flatten_dist: float):
    """Recoge outers, inners y TODOS los segmentos como obstáculos."""
    outers: list[PolyData] = []
    inners: list[PolyData] = []
    obstacles: list[tuple[tuple[float, float], tuple[float, float]]] = []
    open_outer_segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    open_inner_segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    open_any_segs: list[tuple[tuple[float, float], tuple[float, float]]] = []

    for entity in msp:
        typ = entity.dxftype()
        if typ not in ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"):
            continue
        pts, closed = _entity_points(entity, flatten_dist)
        if len(pts) < 2:
            continue
        role = _layer_role(str(getattr(entity.dxf, "layer", "") or ""))
        # Obstáculos: toda geometría (cualquier capa)
        is_closed = closed or (len(pts) >= 3 and pts[0] == pts[-1])
        core = pts[:-1] if is_closed and pts[0] == pts[-1] else pts
        segs = segments_from_points(core, closed=is_closed)
        obstacles.extend(segs)

        if role == "outer" and is_closed:
            poly = make_poly_data(core if pts[0] == pts[-1] else pts)
            if poly:
                outers.append(poly)
        elif role == "inner" and is_closed:
            poly = make_poly_data(core if pts[0] == pts[-1] else pts)
            if poly:
                inners.append(poly)
        elif not is_closed:
            open_any_segs.extend(segs)
            if role == "outer":
                open_outer_segs.extend(segs)
            elif role == "inner":
                open_inner_segs.extend(segs)

    # Inventor/AutoDXF: contorno = varias LINE en IV_OUTER_PROFILE / IV_INTERIOR_*
    if not outers and open_outer_segs:
        outers.extend(_polys_from_open_segments(open_outer_segs))
    if open_inner_segs:
        inners.extend(_polys_from_open_segments(open_inner_segs))

    # Si no hay outer tipado, usa el polígono cerrado más grande de cualquier capa.
    if not outers:
        candidates: list[PolyData] = []
        for entity in msp:
            typ = entity.dxftype()
            if typ not in ("LWPOLYLINE", "POLYLINE", "CIRCLE"):
                continue
            pts, closed = _entity_points(entity, flatten_dist)
            is_closed = closed or (len(pts) >= 3 and pts[0] == pts[-1])
            if not is_closed:
                continue
            core = pts[:-1] if pts and pts[0] == pts[-1] else pts
            poly = make_poly_data(core)
            if poly:
                candidates.append(poly)
        candidates.extend(_polys_from_open_segments(open_any_segs))
        if candidates:
            candidates.sort(key=lambda p: p.area, reverse=True)
            outers = [candidates[0]]
            for c in candidates[1:]:
                if point_in_largest(c, outers[0]):
                    inners.append(c)

    outers.sort(key=lambda p: (p.bbox[1], p.bbox[0]))
    return outers, inners, obstacles


def point_in_largest(inner: PolyData, outer: PolyData) -> bool:
    from modules.dxf_mark.place_mark import point_in_polygon

    return point_in_polygon(inner.centroid, outer.points)


def inject_mark_into_dxf(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    mark_text: str | None = None,
    text_height_in: float = DEFAULT_TEXT_HEIGHT_IN,
    clearance_in: float = DEFAULT_CLEARANCE_IN,
    search_rings: int = 20,
    replace_existing_mark: bool = False,
    mark_layer: str = MARK_LAYER,
    skip_if_present: bool = False,
) -> InjectResult:
    path = Path(input_path)
    if not path.is_file():
        raise FileNotFoundError(f"No existe DXF: {path}")

    text = normalize_mark_text(mark_text) if mark_text else mark_text_from_dxf_path(path)
    if not text:
        raise ValueError("Texto MARK vacío")

    if skip_if_present and tiene_marcaje_stick(path):
        out = Path(output_path) if output_path else path
        return InjectResult(
            input_path=path,
            output_path=out,
            mark_text=text,
            height_du=float(text_height_in),
            components_marked=0,
            components_skipped=0,
            already_marked=True,
        )

    layer_name = str(mark_layer or MARK_LAYER).strip() or MARK_LAYER
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    ensure_mark_layer(doc, layer_name)
    _ensure_stick_appid(doc)

    units_per_in = drawing_units_per_inch(doc)
    # Arranca en tope (0.35) o el valor pedido, sin pasar del máximo.
    target_vis_in = min(float(text_height_in), float(MAX_MARK_HEIGHT_IN))
    if target_vis_in <= 0:
        target_vis_in = float(MAX_MARK_HEIGHT_IN)
    visible_height = target_vis_in * units_per_in
    height = visible_height / STICK_VISIBLE_HEIGHT_FACTOR
    clearance = float(clearance_in) * units_per_in
    flatten = max(0.01 * units_per_in, height * 0.02)

    if replace_existing_mark:
        layer_u = layer_name.upper()
        to_delete = [
            e
            for e in msp
            if str(getattr(e.dxf, "layer", "") or "").upper() == layer_u
        ]
        for e in to_delete:
            msp.delete_entity(e)

    outers, inners, obstacles = collect_geometry(msp, flatten)
    if not outers:
        raise RuntimeError("No se encontró contorno exterior para colocar el MARK")

    marked = 0
    skipped = 0
    placed_segs: list[tuple[tuple[float, float], tuple[float, float]]] = []

    preferred_min_stroke = (
        PREFERRED_MIN_MARK_HEIGHT_IN * units_per_in
    ) / STICK_VISIBLE_HEIGHT_FACTOR
    min_stroke_h = (MIN_MARK_HEIGHT_IN * units_per_in) / STICK_VISIBLE_HEIGHT_FACTOR

    for outer in outers:
        holes = [h for h in inners if point_in_largest(h, outer)]
        # Obstáculos de esta pieza: todo + marks ya puestos en esta pasada
        obs = list(obstacles) + placed_segs
        # Orientación: el texto corre a lo largo del lado más largo del bbox
        # (piezas 2"×5" → vertical; piezas 13"×5" → horizontal).
        bw = outer.bbox[2] - outer.bbox[0]
        bh = outer.bbox[3] - outer.bbox[1]
        prefer_90 = bh > bw + 1e-9
        angles = (90.0, 0.0) if prefer_90 else (0.0, 90.0)

        usable_w = max(bw - 2.0 * clearance, 1e-6)
        usable_h = max(bh - 2.0 * clearance, 1e-6)

        strokes = None
        for angle in angles:
            tw0, th0 = measure_text_aabb(text, height, angle_deg=angle)
            h0 = height
            if tw0 > 0.92 * usable_w:
                h0 = min(h0, height * (0.92 * usable_w) / tw0)
            if th0 > 0.75 * usable_h:
                h0 = min(h0, height * (0.75 * usable_h) / th0)
            if h0 <= 0:
                continue

            height_candidates = _height_candidates_stroke(
                h0,
                preferred_min_stroke=preferred_min_stroke,
                min_stroke=min_stroke_h,
            )

            for hh in height_candidates:
                strokes = find_mark_position(
                    text,
                    hh,
                    outer,
                    holes,
                    obs,
                    clearance=clearance,
                    angle_deg=angle,
                    search_rings=search_rings,
                )
                if strokes:
                    break
            if not strokes:
                strokes = find_mark_position(
                    text,
                    height_candidates[-1],
                    outer,
                    holes,
                    obs,
                    clearance=clearance * 0.5,
                    angle_deg=angle,
                    search_rings=search_rings,
                )
            if strokes:
                break

        if not strokes:
            skipped += 1
            continue
        for stroke in strokes:
            if len(stroke) < 2:
                continue
            # LINE abiertas (no LWPOLYLINE): FreeCAD las toma como Edges /
            # Part.makeLine 1:1 — mismo camino que el sidecar MARK JSON.
            for i in range(len(stroke) - 1):
                x1, y1 = float(stroke[i][0]), float(stroke[i][1])
                x2, y2 = float(stroke[i + 1][0]), float(stroke[i + 1][1])
                if abs(x2 - x1) < 1e-12 and abs(y2 - y1) < 1e-12:
                    continue
                ent = msp.add_line(
                    (x1, y1),
                    (x2, y2),
                    dxfattribs={"layer": layer_name},
                )
                _tag_stick_entity(ent, text)
            placed_segs.extend(segments_from_points(stroke, closed=False))
        marked += 1

    if marked <= 0:
        raise RuntimeError(
            f"No se pudo colocar MARK '{text}' sin solapes "
            f"(altura_visible={visible_height:.4f} u, clearance={clearance:.4f} u)"
        )

    _set_stick_header(doc, text)
    out = Path(output_path) if output_path else path.with_name(f"{path.stem}_MARKED{path.suffix}")
    doc.saveas(str(out))
    return InjectResult(
        input_path=path,
        output_path=out,
        mark_text=text,
        height_du=visible_height,
        components_marked=marked,
        components_skipped=skipped,
        already_marked=False,
    )


def prompt_dxf() -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        p = filedialog.askopenfilename(
            title="Seleccionar DXF para marcaje",
            filetypes=[("DXF", "*.dxf"), ("Todos", "*.*")],
        )
        root.destroy()
        return Path(p) if p else None
    except Exception:
        line = input("Ruta DXF: ").strip().strip('"')
        return Path(line) if line else None
