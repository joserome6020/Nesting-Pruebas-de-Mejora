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

# Familias de capa que reciben desfase plasma (como OFFSET de AutoCAD).
# Se comparan por SUBCADENA, igual que `_clasificar_capa` del nest: Inventor
# exporta `IV_OUTER_PROFILE` / `IV_INTERIOR_PROFILES`, no `IV_OUTER` /
# `IV_INTERIOR` exactos. Con match exacto ninguna pieza flat-pattern de chapa
# clasificaba su contorno y el export moría en "plasma sin contorno
# exportable desde el nest" pese a que el nest sí la reconocía.
_PLASMA_OFFSET_OUTER_LAYERS = ("CUT_OUTER", "OUTER", "CORTE_EXTERNO", "IV_OUTER")
_PLASMA_OFFSET_INNER_LAYERS = (
    "CUT_INNER",
    "INNER",
    "CORTE_INTERNO",
    "IV_INTERIOR",
    "INTERIOR",
)


def resolver_fuente_plasma(
    pz: dict,
    *,
    compensada: bool,
    offset_mm: float,
    es_linea_corte: bool = False,
) -> dict:
    """
    Decide con qué DXF se corta una pieza plasma y devuelve las llaves del placement.

    Cuando la pieza se anidó compensada, la geometría certificada de corte ya
    existe en `Plasma Compensated` (con sus ARC/bulges reales). Resolver esa ruta
    aquí — en vez de confiar en que la pieza cargada traiga `ruta_plasma` — es lo
    que habilita la inyección 1:1 de `export_plasma_placement`. Sin esto un
    `.arganest` reabierto llegaba sin la llave, el export recalculaba el offset
    sobre el original y escribía el contorno muestreado: los arcos salían como
    decenas de segmentos rectos.
    """
    ruta_src = str(pz.get("ruta") or "").strip()
    if es_linea_corte or not ruta_src or not os.path.isfile(ruta_src):
        ruta_src = ""
    ruta_plasma = str(pz.get("ruta_plasma") or "").strip()
    if ruta_plasma and not os.path.isfile(ruta_plasma):
        ruta_plasma = ""
    off = float(offset_mm or 0.0)

    if compensada and off > 0.0 and ruta_src and not ruta_plasma:
        try:
            from modules.plasma_compensator import asegurar_dxf_plasma_compensado

            ruta_ok, _err = asegurar_dxf_plasma_compensado(ruta_src, off)
            if ruta_ok and os.path.isfile(str(ruta_ok)):
                ruta_plasma = str(ruta_ok)
        except Exception:
            ruta_plasma = ""

    if compensada and off > 0.0 and ruta_plasma:
        return {
            "ruta": ruta_plasma,
            "ruta_plasma": ruta_plasma,
            "plasma_fuente_ya_compensada": True,
            "compensated_plasma_source": True,
            # El fuente ya trae el desfase: aplicarlo otra vez duplicaría el crecimiento.
            "plasma_offset_mm": 0.0,
        }
    return {
        "ruta": ruta_src,
        "ruta_plasma": "",
        "plasma_fuente_ya_compensada": False,
        "compensated_plasma_source": bool(ruta_src) and compensada and off > 0.0,
        "plasma_offset_mm": off if compensada else 0.0,
    }


def _plasma_desfase_clase(layer_name: str) -> str | None:
    """
    Capas que reciben desfase en export plasma.
    CUT_OUTER -> hacia afuera (+offset); CUT_INNER -> hacia adentro (-offset).
    Cualquier otra capa (MARK, Plate, 0, etc.) no se desfasa aquí.

    La capa por defecto ``"0"`` queda fuera a propósito: el nest la trata como
    contorno, pero en plasma suele traer marcos y notas que no deben cortarse.
    """
    from modules.nesting_engine.geometry_parser import LAYER_MARK

    u = str(layer_name or "").upper().strip()
    if not u:
        return None
    if any(m in u for m in LAYER_MARK):
        return None
    # Inner primero: "IV_INTERIOR_PROFILES" contiene "INTERIOR"; el guard de
    # "OUTER" evita que una capa mixta caiga del lado equivocado.
    if any(x in u for x in _PLASMA_OFFSET_INNER_LAYERS) and "OUTER" not in u:
        return "inner"
    if any(x in u for x in _PLASMA_OFFSET_OUTER_LAYERS):
        return "outer"
    return None

from modules.plasma_compensator import _buffer_polygon_points, _entity_points_xy

Point = Tuple[float, float]

# Contorno exterior válido debe superar este radio (mm); evita círculos basura del fallback viejo.

_MIN_OUTER_SPAN_MM = 8.0

_MIN_OUTER_SPAN_RATIO = 0.55

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

    from modules.plasma_offset2d import offset_closed_profile

    outer_raw, holes_raw = _rings_from_poligonos(pols)

    if already_compensated:

        return sanitize_plasma_profile(outer_raw, holes_raw)

    if offset_mm <= 0:

        return sanitize_plasma_profile(outer_raw, holes_raw)

    try:

        if len(outer_raw) < 3:

            return sanitize_plasma_profile(outer_raw, holes_raw)

        result = offset_closed_profile(
            outer_raw,
            delta=float(offset_mm),
            holes=holes_raw if holes_raw else None,
        )
        if not result.ok or not result.rings:
            return sanitize_plasma_profile(outer_raw, holes_raw)

        # Primer anillo = outer; siguientes con área menor típica = holes.
        plasma_outer = result.rings[0]
        plasma_holes = result.rings[1:] if len(result.rings) > 1 else []
        # Si el servicio devolvió solo exteriores (FreeCAD wires), contraer huecos aparte.
        if holes_raw and not plasma_holes:
            for h in holes_raw:
                hr = offset_closed_profile(h, delta=-float(offset_mm))
                if hr.ok and hr.rings:
                    plasma_holes.extend(hr.rings)

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

    already_comp = bool(p.get("compensated"))

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

            already_compensated=already_comp,

        )

    if len(outer) >= 3 or holes:

        if already_comp:

            outer, holes = sanitize_plasma_profile(outer, holes)

        else:

            outer, holes = _clean_profile_for_production(outer, holes)

        holes = _dedupe_hole_rings(holes)

    return outer, holes

def _ring_span_mm(ring) -> float:

    xs: list[float] = []

    ys: list[float] = []

    for pt in ring or []:

        if isinstance(pt, (list, tuple)) and len(pt) >= 2:

            try:

                xs.append(float(pt[0]))

                ys.append(float(pt[1]))

            except Exception:

                continue

    if not xs:

        return 0.0

    return max(max(xs) - min(xs), max(ys) - min(ys))

def _expected_outer_span_mm(p: dict) -> float:

    outer = list(p.get("outer") or p.get("outer_poly") or [])

    if len(outer) < 3:

        pols = list(p.get("nested_poligonos") or [])

        if pols:

            outer = list(pols[0] or [])

    return _ring_span_mm(outer)

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

            elif ent.dxftype() == "LWPOLYLINE":

                for x, y, *_ in ent.get_points("xy"):

                    xs.append(float(x))

                    ys.append(float(y))

        except Exception:

            continue

    if not xs:

        return 0.0

    return max(max(xs) - min(xs), max(ys) - min(ys))

def _plasma_export_accepts(entities, *, expected_span_mm: float = 0.0) -> bool:
    return _plasma_export_looks_valid(entities, expected_span_mm=expected_span_mm)


def _plasma_export_looks_valid(entities, *, expected_span_mm: float = 0.0) -> bool:

    outer_ents = [

        e for e in (entities or [])

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

    if only_tiny_circles:

        return False

    if expected_span_mm >= _MIN_OUTER_SPAN_MM:

        if span < expected_span_mm * _MIN_OUTER_SPAN_RATIO:

            return False

    return True

def _msp_destroy_entities(entities) -> None:

    for ent in entities or []:

        try:

            ent.destroy()

        except Exception:

            pass

def _dedupe_hole_rings(holes: list) -> list:
    """Elimina huecos duplicados (mismo centro/radio) que generan empalmes en plasma."""
    out: list = []
    seen: list[tuple[float, float, float]] = []
    for h in holes or []:
        ring = _sanitize_ring_coords(h)
        if len(ring) < 3:
            continue
        xs = [float(p[0]) for p in ring]
        ys = [float(p[1]) for p in ring]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        r = sum(math.hypot(x - cx, y - cy) for x, y in zip(xs, ys)) / len(xs)
        dup = False
        for scx, scy, sr in seen:
            if (
                math.hypot(cx - scx, cy - scy) <= max(0.6, sr * 0.08)
                and abs(r - sr) <= max(0.5, sr * 0.08)
            ):
                dup = True
                break
        if dup:
            continue
        seen.append((cx, cy, r))
        out.append(ring)
    return out


def _entity_endpoints_inches(entity) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Extremos en pulgadas DXF (para agrupar LINE/ARC en un contorno)."""
    typ = entity.dxftype()
    try:
        if typ == "LINE":
            s, e = entity.dxf.start, entity.dxf.end
            return (float(s.x), float(s.y)), (float(e.x), float(e.y))
        if typ == "ARC":
            c = entity.dxf.center
            r = float(entity.dxf.radius)
            sa = math.radians(float(entity.dxf.start_angle))
            ea = math.radians(float(entity.dxf.end_angle))
            return (
                (float(c.x) + r * math.cos(sa), float(c.y) + r * math.sin(sa)),
                (float(c.x) + r * math.cos(ea), float(c.y) + r * math.sin(ea)),
            )
    except Exception:
        return None
    return None


def _points_near(
    a: tuple[float, float], b: tuple[float, float], *, tol: float = 1e-4
) -> bool:
    return abs(float(a[0]) - float(b[0])) <= tol and abs(
        float(a[1]) - float(b[1])
    ) <= tol


def _signed_ring_area(pts: list[tuple[float, float]]) -> float:
    if len(pts) < 3:
        return 0.0
    area = 0.0
    for i in range(len(pts) - 1):
        area += float(pts[i][0]) * float(pts[i + 1][1]) - float(
            pts[i + 1][0]
        ) * float(pts[i][1])
    return area * 0.5


def _outgoing_tangent_angle(
    ent, from_pt: tuple[float, float], *, reverse: bool, flat_in: float = 0.002
) -> float | None:
    """Ángulo de salida al recorrer ent desde from_pt."""
    from ezdxf import path as ezdxf_path

    try:
        p = ezdxf_path.make_path(ent)
        pts = [
            (float(v[0]), float(v[1]))
            for v in p.flattening(distance=max(flat_in, 1e-5))
        ]
        if reverse:
            pts = list(reversed(pts))
        if len(pts) < 2:
            return None
        idx = min(
            range(len(pts)),
            key=lambda i: math.hypot(
                pts[i][0] - from_pt[0], pts[i][1] - from_pt[1]
            ),
        )
        j = idx + 1 if idx + 1 < len(pts) else idx - 1
        if j < 0:
            return None
        return math.atan2(pts[j][1] - pts[idx][1], pts[j][0] - pts[idx][0])
    except Exception:
        return None


def _normalize_turn(in_ang: float, out_ang: float) -> float:
    """Giro CCW entre tangente entrante y saliente (-pi, pi]."""
    return (out_ang - in_ang + math.pi) % (2 * math.pi) - math.pi


def _order_chain_from_start(
    items: list[tuple[object, tuple[float, float], tuple[float, float]]],
    start_idx: int,
    *,
    reverse_start: bool,
    tol: float,
) -> list[tuple[object, bool]]:
    remaining = list(items)
    ent, p1, p2 = remaining.pop(start_idx)
    if reverse_start:
        chain: list[tuple[object, bool]] = [(ent, True)]
        tail = p1
    else:
        chain = [(ent, False)]
        tail = p2
    while remaining:
        pick = -1
        rev = False
        for i, (_, a, b) in enumerate(remaining):
            if _points_near(a, tail, tol=tol):
                pick, rev = i, False
                tail = b
                break
            if _points_near(b, tail, tol=tol):
                pick, rev = i, True
                tail = a
                break
        if pick < 0:
            break
        ent, _, _ = remaining.pop(pick)
        chain.append((ent, rev))
    return chain


def _chain_ring_area(
    chain: list[tuple[object, bool]], *, flat_in: float = 0.02
) -> float:
    out: list[tuple[float, float]] = []
    for ent, rev in chain:
        ht = _entity_head_tail(ent, rev)
        if ht is None:
            continue
        head, tail = ht
        pts = _sample_entity_head_tail(ent, head, tail, flat_in=flat_in)
        if not pts:
            continue
        if not out:
            out.extend(pts)
        else:
            out.extend(pts[1:])
    if len(out) >= 3 and not _points_near(out[0], out[-1], tol=flat_in * 2):
        out.append(out[0])
    return abs(_signed_ring_area(out))


def _order_connected_entities(entities, *, tol: float = 1e-4) -> list[tuple[object, bool]]:
    """
    Recorre LINE/ARC siguiendo el borde real (giro CCW en cada vértice).
    Evita contornos en moño cuando las LINE vienen en sentidos opuestos.
    """
    items: list[tuple[object, tuple[float, float], tuple[float, float]]] = []
    for ent in entities or []:
        ep = _entity_endpoints_inches(ent)
        if ep is None:
            continue
        items.append((ent, ep[0], ep[1]))
    n = len(items)
    if n == 0:
        return []
    if n == 1:
        return [(items[0][0], False)]

    start_pt = min(
        (items[i][1] for i in range(n)),
        key=lambda p: (float(p[1]), float(p[0])),
    )

    best: list[tuple[object, bool]] | None = None
    best_area = -1.0
    for i, (ent, a, b) in enumerate(items):
        for rev, head, tail in (
            (False, a, b),
            (True, b, a),
        ):
            if not _points_near(head, start_pt, tol=tol):
                continue
            chain: list[tuple[object, bool]] = [(ent, rev)]
            used = {i}
            point = tail
            in_ang = _outgoing_tangent_angle(ent, head, reverse=rev)
            for _ in range(n - 1):
                cands: list[tuple[float, int, bool, tuple[float, float]]] = []
                for j, (ent2, a2, b2) in enumerate(items):
                    if j in used:
                        continue
                    for rev2, head2, nxt in (
                        (False, a2, b2),
                        (True, b2, a2),
                    ):
                        if not _points_near(head2, point, tol=tol):
                            continue
                        out_ang = _outgoing_tangent_angle(
                            ent2, head2, reverse=rev2
                        )
                        if out_ang is None:
                            continue
                        turn = (
                            0.0
                            if in_ang is None
                            else _normalize_turn(in_ang, out_ang)
                        )
                        cands.append((turn, j, rev2, nxt))
                if not cands:
                    break
                pos = [c for c in cands if c[0] > 1e-5]
                turn, j, rev2, nxt = (
                    min(pos, key=lambda c: c[0]) if pos else max(cands, key=lambda c: c[0])
                )
                used.add(j)
                ent2 = items[j][0]
                chain.append((ent2, rev2))
                head2 = point
                out_ang = _outgoing_tangent_angle(ent2, head2, reverse=rev2)
                point = nxt
                in_ang = (out_ang + math.pi) if out_ang is not None else None
            if len(chain) != n:
                continue
            area = _chain_ring_area(chain)
            if area > best_area:
                best_area = area
                best = chain
    if best:
        return best
    return _order_chain_from_start(items, 0, reverse_start=False, tol=tol)


def _entity_head_tail(
    entity, reverse: bool
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    ep = _entity_endpoints_inches(entity)
    if ep is None:
        return None
    return (ep[1], ep[0]) if reverse else ep


def _arc_points_head_tail(
    entity,
    head: tuple[float, float],
    tail: tuple[float, float],
    *,
    flat_in: float = 0.01,
) -> list[tuple[float, float]]:
    """Muestrea el arco entre head→tail usando el sentido DXF (no el semicírculo opuesto)."""
    ep = _entity_endpoints_inches(entity)
    if ep is None:
        return [head, tail]
    sa_pt, ea_pt = ep[0], ep[1]
    c = entity.dxf.center
    cx, cy = float(c.x), float(c.y)
    r = float(entity.dxf.radius)
    if r <= 1e-9:
        return [head, tail]

    if _points_near(head, sa_pt, tol=1e-3) and _points_near(tail, ea_pt, tol=1e-3):
        a0 = math.radians(float(entity.dxf.start_angle))
        a1 = math.radians(float(entity.dxf.end_angle))
        while a1 <= a0:
            a1 += 2 * math.pi
        ccw = True
    elif _points_near(head, ea_pt, tol=1e-3) and _points_near(tail, sa_pt, tol=1e-3):
        a0 = math.radians(float(entity.dxf.end_angle))
        a1 = math.radians(float(entity.dxf.start_angle))
        while a1 <= a0:
            a1 += 2 * math.pi
        ccw = False
    else:
        ah = math.atan2(head[1] - cy, head[0] - cx)
        at = math.atan2(tail[1] - cy, tail[0] - cx)
        ccw_sweep = (at - ah) % (2 * math.pi)
        cw_sweep = (ah - at) % (2 * math.pi)
        ccw = ccw_sweep <= cw_sweep
        a0, a1 = ah, at
        if ccw:
            while a1 <= a0:
                a1 += 2 * math.pi
        else:
            while a0 <= a1:
                a0 += 2 * math.pi

    sweep = (a1 - a0) if ccw else -(a0 - a1)
    arc_len = abs(sweep) * r
    steps = max(4, int(arc_len / max(flat_in, 1e-5)) + 1)
    pts = [head]
    for i in range(1, steps):
        t = i / steps
        ang = a0 + sweep * t
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    pts.append(tail)
    return pts


def _sample_entity_head_tail(
    entity,
    head: tuple[float, float],
    tail: tuple[float, float],
    *,
    flat_in: float = 0.01,
) -> list[tuple[float, float]]:
    if entity.dxftype() == "LINE":
        return [head, tail]
    if entity.dxftype() == "ARC":
        return _arc_points_head_tail(entity, head, tail, flat_in=flat_in)
    from ezdxf import path as ezdxf_path

    try:
        p = ezdxf_path.make_path(entity)
        return [
            (float(v[0]), float(v[1]))
            for v in p.flattening(distance=max(flat_in, 1e-5))
        ]
    except Exception:
        return [head, tail]


def _group_connected_cut_entities(entities, *, tol: float = 1e-4) -> list[list]:
    """Agrupa LINE/ARC que comparten vértices → un contorno conectado."""
    items: list[tuple[object, tuple[float, float], tuple[float, float]]] = []
    for ent in entities or []:
        if ent.dxftype() not in ("LINE", "ARC"):
            continue
        ep = _entity_endpoints_inches(ent)
        if ep is None:
            continue
        items.append((ent, ep[0], ep[1]))

    n = len(items)
    if n == 0:
        return []
    parent = list(range(n))

    def _find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[rb] = ra

    def _key(p: tuple[float, float]) -> tuple[int, int]:
        return (round(p[0] / tol), round(p[1] / tol))

    pt_map: dict[tuple[int, int], int] = {}
    for j, (_, p1, p2) in enumerate(items):
        for p in (p1, p2):
            k = _key(p)
            if k in pt_map:
                _union(j, pt_map[k])
            else:
                pt_map[k] = j

    groups: dict[int, list] = {}
    for j, (ent, _, _) in enumerate(items):
        groups.setdefault(_find(j), []).append(ent)
    return list(groups.values())


def _flatten_entity_group_inches(
    entities, *, flat_in: float = 0.01, offset_in: float | None = None
) -> list[tuple[float, float]]:
    """Unifica LINE/ARC conectados en orden topológico (pulgadas)."""
    if offset_in is not None and offset_in > 0:
        flat_in = max(float(flat_in), float(offset_in) * 0.35, 0.008)

    out: list[tuple[float, float]] = []
    for ent, rev in _order_connected_entities(entities):
        ht = _entity_head_tail(ent, rev)
        if ht is None:
            continue
        head, tail = ht
        pts = _sample_entity_head_tail(ent, head, tail, flat_in=flat_in)
        if not pts:
            continue
        if not out:
            out.extend(pts)
        else:
            out.extend(pts[1:])
    if len(out) >= 3 and not _points_near(out[0], out[-1], tol=flat_in * 2):
        out.append(out[0])
    return out


def _transform_points_matrix(pts: list, m) -> list[tuple[float, float]]:
    from ezdxf.math import Vec3

    out: list[tuple[float, float]] = []
    for raw in pts or []:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        v = m.transform(Vec3(float(raw[0]), float(raw[1]), 0.0))
        out.append((float(v.x), float(v.y)))
    return out


def _simplify_ring_inches(
    ring: list[tuple[float, float]], tol_in: float
) -> list[tuple[float, float]]:
    from shapely.geometry import LineString

    if len(ring) < 4 or tol_in <= 0:
        return ring
    try:
        ls = LineString(ring)
        simp = ls.simplify(max(float(tol_in), 1e-5), preserve_topology=True)
        coords = [(float(x), float(y)) for x, y in simp.coords]
        if len(coords) >= 3 and not _points_near(coords[0], coords[-1], tol=tol_in):
            coords.append(coords[0])
        return coords if len(coords) >= 3 else ring
    except Exception:
        return ring


def _offset_closed_profile_inches(
    points: list[tuple[float, float]], offset_in: float, *, rectilinear: bool = False
) -> list[list[tuple[float, float]]]:
    """Desfase de contorno cerrado en pulgadas (mismo servicio que PARTS).

    Cascada de motores: FreeCAD/GEOS de :mod:`plasma_offset2d` primero (por
    compatibilidad histórica); si devuelve vacío se usa Clipper2 —el motor de
    FreeCAD Path/CAM— que no falla con perfiles reales. Antes esto devolvía
    ``[]`` y el export terminaba en "plasma sin contorno exportable".
    """
    from modules.plasma_offset2d import offset_simple_ring

    result = offset_simple_ring(list(points or []), delta=float(offset_in))
    rings: list[list[tuple[float, float]]] = list(result.rings or []) if result.ok else []

    if not rings:
        try:
            from modules.plasma_offset_clipper import clipper_disponible, offset_ring

            if clipper_disponible():
                fallback = offset_ring(list(points or []), delta=float(offset_in))
                if fallback.ok:
                    rings = list(fallback.rings or [])
        except Exception:
            pass

    if not rings:
        return []
    tol = abs(float(offset_in)) * (0.15 if rectilinear else 0.2)
    rings = [_simplify_ring_inches(r, tol) for r in rings]
    return [r for r in rings if len(r) >= 3]


def _export_rectilinear_offset_with_arcs(
    msp,
    ring_mm: list[tuple[float, float]],
    layer: str,
    *,
    corner_radius_mm: float,
) -> bool:
    """Escribe el OFFSET redondo de un perfil ortogonal como LINE + ARC.

    Clipper/GEOS devuelve los radios del join como tres puntos pequeños. El
    export genérico de curvas intenta agrupar tramos lejanos y, en perfiles
    con escalones, puede inferir un ARC grande y repetir el ciclo entero. Aquí
    sólo se convierte un triplete consecutivo cuyo radio coincide con el
    desfase pedido; el resto conserva LINE exactas. Es deliberadamente
    específico para el OFFSET de esquinas rectilíneas, no un detector CAD
    generalista.
    """
    from modules.dxf_native_curves import circle_from_three_points, normalize_ring

    pts = normalize_ring(ring_mm, closed=True)
    if len(pts) < 3:
        return False
    target = abs(float(corner_radius_mm))
    if target <= 1e-6:
        return False

    n = len(pts)
    i = 0
    pasos = 0
    added = 0
    # Consume una arista por LINE o dos por el triplete que forma el ARC.
    while pasos < n:
        p0 = pts[i]
        p1 = pts[(i + 1) % n]
        p2 = pts[(i + 2) % n]
        circ = circle_from_three_points(p0, p1, p2)
        use_arc = False
        if circ:
            cx, cy, radius = circ
            cross = (
                (p1[0] - p0[0]) * (p2[1] - p1[1])
                - (p1[1] - p0[1]) * (p2[0] - p1[0])
            )
            # 15 % + 0.03 mm permite la discretización, no los ARC gigantes
            # que antes aparecían en OP-1010-211 (R≈26 mm vs offset 0.318).
            tol_r = max(0.03, target * 0.15)
            use_arc = abs(float(radius) - target) <= tol_r and abs(cross) > 1e-9
        if use_arc:
            start = math.degrees(math.atan2(p0[1] - cy, p0[0] - cx))
            end = math.degrees(math.atan2(p2[1] - cy, p2[0] - cx))
            if cross < 0.0:
                start, end = end, start
            while end < start - 1e-9:
                end += 360.0
            msp.add_arc((cx, cy), radius, start, end, dxfattribs={"layer": layer})
            i = (i + 2) % n
            pasos += 2
        else:
            if math.hypot(p1[0] - p0[0], p1[1] - p0[1]) > 1e-6:
                msp.add_line(p0, p1, dxfattribs={"layer": layer})
            i = (i + 1) % n
            pasos += 1
        added += 1
    return added > 0


def _export_offset_contour_to_msp(
    msp,
    m,
    points_in: list[tuple[float, float]],
    *,
    offset_mm: float,
    outward: bool,
    clase: str,
    layer: str,
    stats: dict,
) -> int:
    """Buffer del contorno completo → ARC/CIRCLE/LINE en la placa."""
    if len(points_in) < 3:
        return 0
    off_in = _offset_inches(offset_mm)
    sign = 1.0 if outward else -1.0
    rectilinear = _ring_is_rectilinear(points_in, tol=max(0.02, off_in * 0.5))
    rings = _offset_closed_profile_inches(
        points_in, sign * off_in, rectilinear=rectilinear
    )
    if not rings:
        return 0
    added = 0
    from modules.nest_exporter import _export_ring_exact

    for ring in rings:
        if _signed_ring_area(ring) < 0:
            ring = list(reversed(ring))
        ring_mm = _transform_points_matrix(ring, m)
        if not ring_mm:
            continue
        wrote = False
        # Un perfil fuente rectilíneo tiene líneas, pero su OFFSET con join
        # redondo tiene LINE + ARC. No usar el detector general de curvas aquí:
        # con escalones puede inferir ARC grandes y recorrer el anillo varias
        # veces. La rutina dedicada acepta únicamente radios del tamaño exacto
        # del desfase.
        if rectilinear:
            wrote = _export_rectilinear_offset_with_arcs(
                msp, ring_mm, layer, corner_radius_mm=abs(float(offset_mm))
            )
        elif _outer_export_line_exact(ring_mm):
            wrote = bool(_export_ring_exact(msp, ring_mm, layer, closed=True))
        elif export_ring_native(
            msp,
            ring_mm,
            layer,
            closed=True,
            prefer_circle=(clase == "inner"),
        ):
            wrote = True
        else:
            wrote = bool(_export_ring_exact(msp, ring_mm, layer, closed=True))
        if wrote:
            added += 1
            stats["ok"] = True
            if clase == "outer":
                stats["outer"] += 1
            else:
                stats["inner"] += 1
    if added > 0:
        pass  # layers_used se completa en export_compensated_plasma_from_source
    return added


def _plasma_source_skip_polyline(
    entity,
    *,
    clase: str,
    has_outer_native: bool,
    circle_sigs: set[tuple[float, float, float]],
) -> bool:
    """No exportar LWPOLYLINE duplicada si ya hay geometría nativa equivalente."""
    typ = entity.dxftype()
    if typ not in ("LWPOLYLINE", "POLYLINE"):
        return False
    if clase == "outer" and has_outer_native:
        return True
    if clase == "inner" and circle_sigs:
        from modules.nest_exporter import _inner_polyline_redundant_with_circle

        return _inner_polyline_redundant_with_circle(entity, circle_sigs)
    return False


def _plan_plasma_contours(
    pool: list,
    clase: str,
    *,
    has_outer_native: bool,
    circle_sigs: set[tuple[float, float, float]],
) -> list[tuple[str, object]]:
    """
    Plan de contornos a desfasar — un solo outer; sin duplicar LWPOLYLINE + LINE/ARC.
    """
    circles = [e for e in pool if e.dxftype() == "CIRCLE"]
    polylines = [e for e in pool if e.dxftype() in ("LWPOLYLINE", "POLYLINE")]
    chain_ents = [e for e in pool if e.dxftype() in ("LINE", "ARC")]

    if clase == "outer":
        closed_chains: list[tuple[float, list]] = []
        for group in _group_connected_cut_entities(chain_ents):
            pts = _flatten_entity_group_inches(group, offset_in=0.01)
            area = abs(_signed_ring_area(pts))
            if len(pts) >= 3 and area > 1e-8:
                closed_chains.append((area, group))
        if closed_chains:
            return [("chain", max(closed_chains, key=lambda x: x[0])[1])]

        usable: list[tuple[float, object]] = []
        for ent in polylines:
            if _plasma_source_skip_polyline(
                ent,
                clase=clase,
                has_outer_native=has_outer_native,
                circle_sigs=circle_sigs,
            ):
                continue
            pts = _entity_points_xy(ent)
            if not pts:
                continue
            usable.append((abs(_signed_ring_area(pts)), ent))
        if usable:
            return [("polyline", max(usable, key=lambda x: x[0])[1])]
        if circles:
            best = max(circles, key=lambda e: float(e.dxf.radius))
            return [("circle", best)]
        return []

    tasks: list[tuple[str, object]] = []
    for ent in circles:
        tasks.append(("circle", ent))
    for ent in polylines:
        if _plasma_source_skip_polyline(
            ent,
            clase=clase,
            has_outer_native=has_outer_native,
            circle_sigs=circle_sigs,
        ):
            continue
        tasks.append(("polyline", ent))
    for group in _group_connected_cut_entities(chain_ents):
        pts = _flatten_entity_group_inches(group, offset_in=0.01)
        if len(pts) >= 3:
            tasks.append(("chain", group))
    return tasks


def _compensate_entity(entity, offset_mm: float, *, outward: bool):
    typ = entity.dxftype()
    if typ == "CIRCLE":
        return _compensate_circle(entity, offset_mm, outward=outward)
    if typ == "ARC":
        return _compensate_arc(entity, offset_mm, outward=outward)
    if typ == "LINE":
        return _compensate_line(entity, offset_mm, outward=outward)
    return None


def _ring_is_rectilinear(pts, tol: float = 0.55) -> bool:
    """True si cada segmento no degenerado es horizontal o vertical.

    No equivale a "todos los vértices están sobre el borde del bounding box":
    un perfil de chapa con escalones/notches internos sigue siendo 100 %
    rectilíneo aunque sus vértices vivan dentro del bbox. El predicado viejo
    rechazaba esos perfiles y enviaba su offset (con joins redondos) al
    detector de curvas nativas, que inventaba ARC enormes y duplicaba el
    contorno al escribirlo.

    ``tol`` está en las mismas unidades que los puntos (pulgadas antes del
    placement) y absorbe ruido de DXF sin convertir una diagonal real en un
    segmento ortogonal.
    """
    ring = [(float(p[0]), float(p[1])) for p in (pts or []) if len(p) >= 2]
    if len(ring) >= 2 and _points_near(ring[0], ring[-1], tol=tol * 0.01):
        ring = ring[:-1]
    if len(ring) < 3:
        return False
    non_degenerate = 0
    for i, (x0, y0) in enumerate(ring):
        x1, y1 = ring[(i + 1) % len(ring)]
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        if dx <= tol * 0.01 and dy <= tol * 0.01:
            continue
        non_degenerate += 1
        if dx > tol and dy > tol:
            return False
    return non_degenerate >= 3


def _outer_export_line_exact(ring, *, tol: float = 0.55) -> bool:
    """Perfiles rectilíneos (sin diagonales): LINE exactas; curvos: ARC/CIRCLE.

    ``tol`` está en unidades del anillo. La ruta de OFFSET usa 0.02 mm para
    no confundir las cuerdas de un radio pequeño con ruido de DXF; los paths
    de geometría fuente conservan el 0.55 mm histórico.
    """
    return _ring_is_rectilinear(list(ring or []), tol=float(tol))


def _bbox_aspect_ratio_plasma(pts) -> float:
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    if not xs:
        return 1.0
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    return max(w, h) / max(min(w, h), 1e-9)


def _export_plasma_polygon_rings(
    msp,
    p: dict,
    outer: list,
    holes: list,
    *,
    draw_holes: bool = True,
    draw_marks: bool = True,
) -> bool:
    """Dibuja anillos del nest: arcos en outer, CIRCLE/ARC en huecos."""
    from modules.nest_exporter import _export_ring_exact, _transform_poly, _add_lwpolyline

    holes = _dedupe_hole_rings(holes)

    if len(outer) < 3:
        return False

    outer_t = _transform_poly(outer, tx=0.0, ty=0.0, rot_deg=0.0)
    if outer_t:
        layer_outer = str(p.get("layer_override") or "CUT_OUTER")
        closed_outer = bool(p.get("closed", True))
        if _outer_export_line_exact(outer_t):
            _export_ring_exact(msp, outer_t, layer_outer, closed=closed_outer)
        elif not export_ring_native(
            msp, outer_t, layer_outer, closed=closed_outer, prefer_circle=False
        ):
            _export_ring_exact(msp, outer_t, layer_outer, closed=closed_outer)

    if draw_holes:
        hole_layer = str(p.get("inner_layer_override") or "CUT_INNER")
        for h in holes or []:
            h_t = _transform_poly(h, tx=0.0, ty=0.0, rot_deg=0.0)
            if not h_t:
                continue
            if not export_ring_native(
                msp, h_t, hole_layer, closed=True, prefer_circle=True
            ):
                _export_ring_exact(msp, h_t, hole_layer, closed=True)

    if draw_marks:
        part_name = str(p.get("part_name") or p.get("name") or "")
        marks_layer = str(
            p.get("marks_layer")
            or p.get("marks_layer_override")
            or ("RTZ_LABEL" if part_name.startswith("TATUAJE") else "MARK")
        )
        for mk in p.get("marks") or p.get("mark") or []:
            if not mk:
                continue
            mk_t = _transform_poly(mk, tx=0.0, ty=0.0, rot_deg=0.0)
            if mk_t:
                _add_lwpolyline(msp, mk_t, layer=marks_layer, closed=False)

    return bool(outer_t)


def _export_plasma_polygon_fallback(
    msp,
    p: dict,
    *,
    draw_holes: bool = True,
    draw_marks: bool = True,
) -> bool:
    from modules.nest_exporter import _msp_count, _msp_snapshot

    outer, holes = _resolve_plasma_profile(p)
    if len(outer) < 3:
        return False

    expected_span = _expected_outer_span_mm(p)
    count_before = _msp_count(msp)
    if not _export_plasma_polygon_rings(
        msp,
        p,
        outer,
        holes,
        draw_holes=draw_holes,
        draw_marks=draw_marks,
    ):
        return False

    new_entities = _msp_snapshot(msp)[count_before:]
    if _plasma_export_accepts(new_entities, expected_span_mm=expected_span):
        return True
    _msp_destroy_entities(new_entities)
    return False


def _try_plasma_source_export(
    msp,
    doc,
    p: dict,
    *,
    draw_holes: bool = True,
    draw_marks: bool = True,
) -> bool:
    from modules.nest_exporter import _msp_count, _msp_snapshot

    ruta = str(p.get("ruta") or "").strip()
    offset_mm = float(p.get("plasma_offset_mm") or 0.0)
    if not ruta or not os.path.isfile(ruta) or offset_mm <= 0:
        return False
    if not bool(p.get("compensated_plasma_source", True)):
        return False

    expected_span = _expected_outer_span_mm(p)
    count_before = _msp_count(msp)
    stats = export_compensated_plasma_from_source(
        msp,
        doc,
        p,
        draw_holes=draw_holes,
        draw_marks=draw_marks,
    )
    new_entities = _msp_snapshot(msp)[count_before:]
    if int(stats.get("outer", 0) or 0) > 0 and _plasma_export_accepts(
        new_entities, expected_span_mm=expected_span
    ):
        return True
    _msp_destroy_entities(new_entities)
    return False


def export_plasma_placement(
    msp,
    doc,
    p: dict,
    *,
    draw_holes: bool = True,
    draw_marks: bool = True,
    sheet: dict | None = None,
    all_piece_bounds: list | None = None,
) -> bool:
    """
    Plasma = desfase AutoCAD sobre entidades nativas del DXF fuente.
    LINE/ARC/CIRCLE: radio o normal +/- offset_mm; colocar con matriz del nest.
    Sin fuente DXF: respaldo poligonal del nest (sin marcas facetadas).
    """
    from modules.nesting_engine.dxf_export_log import log
    from modules.dxf_export.validate import validate_plasma_piece

    nom = str(p.get("part_name") or p.get("name") or "?")
    ruta = str(p.get("ruta") or "").strip()
    ruta_plasma = str(p.get("ruta_plasma") or "").strip()
    offset_mm = float(p.get("plasma_offset_mm") or 0.0)
    count_before = 0
    try:
        from modules.nest_exporter import _msp_count

        count_before = _msp_count(msp)
    except Exception:
        pass

    # Los DXF existentes en Plasma Compensated pueden haber sido generados con
    # joins Round de una versión anterior. Antes de inyectarlos verificamos el
    # sidecar de versión y se regeneran desde el original con la política
    # actual (Miter para LINE→LINE, ARC nativo para curvas de origen).
    if (
        bool(p.get("plasma_fuente_ya_compensada"))
        and ruta
        and os.path.isfile(ruta)
        and offset_mm > 0.0
    ):
        try:
            from modules.plasma_compensator import asegurar_dxf_plasma_compensado

            ruta_actualizada, error_actualizar = asegurar_dxf_plasma_compensado(
                ruta, offset_mm
            )
            if ruta_actualizada:
                ruta_plasma = str(ruta_actualizada)
                p["ruta_plasma"] = ruta_plasma
            elif error_actualizar:
                log(
                    f"    plasma[{nom}]: no se pudo actualizar Plasma Compensated "
                    f"({error_actualizar})",
                    level="WARN",
                )
        except Exception as exc:
            log(
                f"    plasma[{nom}]: verificación Plasma Compensated falló ({exc})",
                level="WARN",
            )

    # La geometría que se anidó ya vive en Plasma Compensated. Recalcular el
    # OFFSET desde `ruta` aquí hacía que el DXF final no fuera la misma pieza
    # que el packer colocó: además de perder ARC/bulges nativos, podía aplicar
    # joins diferentes a los del archivo compensado. Exportarla 1:1 es el
    # único modo de conservar la geometría de corte certificada.
    if (
        bool(p.get("plasma_fuente_ya_compensada"))
        and ruta_plasma
        and os.path.isfile(ruta_plasma)
    ):
        try:
            from modules.nest_exporter import (
                _export_source_dxf_at_placement,
                _msp_snapshot,
            )

            p_compensada = dict(p)
            p_compensada["ruta"] = ruta_plasma
            _export_source_dxf_at_placement(
                msp, doc, p_compensada, draw_marks=draw_marks, strict=False
            )
            new_ents = _msp_snapshot(msp)[count_before:]
            outer = [
                e
                for e in new_ents
                if str(getattr(e.dxf, "layer", "") or "").upper() == "CUT_OUTER"
            ]
            ok = bool(outer)
            log(
                f"    plasma[{nom}]: Plasma Compensated 1:1 "
                f"outer={len(outer)} -> {'OK' if ok else 'FALLO'}",
                level="INFO" if ok else "WARN",
            )
            if not ok:
                return False
            issues = validate_plasma_piece(
                p,
                new_ents,
                # El fuente ya contiene el offset y el `outer` del nest debe
                # coincidir con él; no se espera un segundo crecimiento.
                offset_mm=0.0,
                sheet=sheet,
                all_piece_bounds=all_piece_bounds,
            )
            if issues:
                for iss in issues:
                    log(f"    plasma[{nom}] FAIL: {iss}", level="ERROR")
                p["_plasma_validation_error"] = (
                    f"plasma inválido: {issues[0]}. "
                    "Renestee esta placa con la compensación activa."
                )
                return False
            return True
        except Exception as exc:
            log(
                f"    plasma[{nom}]: Plasma Compensated 1:1 falló ({exc})",
                level="ERROR",
            )
            p["_plasma_validation_error"] = (
                "plasma compensado no se pudo exportar 1:1 desde su DXF fuente"
            )
            return False

    if ruta and os.path.isfile(ruta) and offset_mm > 0:
        stats = export_compensated_plasma_from_source(
            msp,
            doc,
            p,
            draw_holes=draw_holes,
            draw_marks=draw_marks,
        )
        ok = int(stats.get("outer", 0) or 0) > 0
        log(
            f"    plasma[{nom}]: desfase DXF fuente "
            f"outer={stats.get('outer', 0)} inner={stats.get('inner', 0)} "
            f"marks={stats.get('marks', 0)} -> {'OK' if ok else 'FALLO'}",
            level="INFO" if ok else "WARN",
        )
        if ok and count_before >= 0:
            try:
                from modules.nest_exporter import _msp_snapshot

                new_ents = _msp_snapshot(msp)[count_before:]
                issues = validate_plasma_piece(
                    p,
                    new_ents,
                    offset_mm=offset_mm,
                    sheet=sheet,
                    all_piece_bounds=all_piece_bounds,
                )
                if issues:
                    for iss in issues:
                        log(f"    plasma[{nom}] FAIL: {iss}", level="ERROR")
                    # El caller antes reducía cualquier validación a "sin
                    # contorno exportable", que oculta el problema real. En
                    # particular un .arganest legacy base+offset requiere
                    # renestear para que la geometría compensada respete la
                    # tabla de margen/kerf.
                    p["_plasma_validation_error"] = (
                        f"plasma inválido: {issues[0]}. "
                        "Renestee esta placa con la compensación activa."
                    )
                    # Poka-yoke fail-closed: no dar por buena una pieza plasma inválida.
                    return False
            except Exception as exc:
                log(f"    plasma[{nom}] validacion fail-closed: {exc}", level="ERROR")
                return False
        return ok

    # Sin compensación: DXF fuente 1:1 (igual que el nest), sin desfase.
    if ruta and os.path.isfile(ruta):
        try:
            from modules.nest_exporter import _export_source_dxf_at_placement

            _export_source_dxf_at_placement(
                msp, doc, p, draw_marks=draw_marks, strict=False
            )
            ok = True
            log(f"    plasma[{nom}]: fuente 1:1 sin desfase -> OK", level="INFO")
            return ok
        except Exception as exc:
            log(
                f"    plasma[{nom}]: fuente 1:1 fallo ({exc}); usando poligono nest",
                level="WARN",
            )

    ok = _export_plasma_polygon_fallback(
        msp, p, draw_holes=draw_holes, draw_marks=False
    )
    log(
        f"    plasma[{nom}]: sin fuente, poligono nest {'OK' if ok else 'FALLO'}",
        level="INFO" if ok else "WARN",
    )
    return ok


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
    """
    Desfase plasma (como AutoCAD OFFSET) sobre entidades nativas del DXF fuente.
    - CUT_OUTER: +offset_mm hacia afuera
    - CUT_INNER: -offset_mm hacia adentro
    - MARK/TEXT: 1:1 sin desfase, solo colocación
    """
    from modules.nest_exporter import (
        _circle_signature,
        _import_layers_from_source,
        _resolve_placement_matrix,
        _write_native_entity,
    )

    ruta = str(p.get("ruta") or "").strip()
    offset_mm = float(p.get("plasma_offset_mm") or 0.0)
    stats = {"ok": False, "outer": 0, "inner": 0, "marks": 0}

    if not ruta or not os.path.isfile(ruta) or offset_mm <= 0:
        return stats

    try:
        part_doc = ezdxf.readfile(ruta)
    except Exception:
        return stats

    m = _resolve_placement_matrix(part_doc, p)
    layers_used: set[str] = set()
    entities = list(part_doc.modelspace())

    def _commit_entity(new_e, clase: str, layer: str) -> int:
        if not new_e.transform(m):
            return 0
        n = _write_native_entity(msp, new_e, layer)
        if n <= 0:
            return 0
        stats["ok"] = True
        if clase == "outer":
            stats["outer"] += n
        elif clase == "inner":
            stats["inner"] += n
        elif clase == "mark":
            stats["marks"] += n
        layers_used.add(layer)
        return n

    cut_types = frozenset({"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE"})
    mark_types = frozenset(
        {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "TEXT", "MTEXT", "SPLINE"}
    )

    by_clase: dict[str, list] = {"outer": [], "inner": []}
    for entity in entities:
        if entity.dxftype() not in cut_types:
            continue
        clase = _plasma_desfase_clase(str(entity.dxf.layer or ""))
        if clase is None:
            continue
        by_clase[clase].append(entity)

    has_outer_native = any(
        e.dxftype() in ("LINE", "ARC", "CIRCLE")
        for e in by_clase["outer"]
    )
    circle_sigs = {
        sig
        for e in by_clase["inner"]
        if e.dxftype() == "CIRCLE"
        for sig in [_circle_signature(e)]
        if sig is not None
    }

    # --- Paso 1: CUT_OUTER / CUT_INNER — un contorno por outer, sin empalmes ---
    for clase in ("outer", "inner"):
        if clase == "inner" and not draw_holes:
            continue
        pool = by_clase[clase]
        if not pool:
            continue
        outward = clase == "outer"
        layer = "CUT_OUTER" if clase == "outer" else "CUT_INNER"

        for kind, payload in _plan_plasma_contours(
            pool,
            clase,
            has_outer_native=has_outer_native,
            circle_sigs=circle_sigs,
        ):
            if kind == "circle":
                compensated = _compensate_entity(payload, offset_mm, outward=outward)
                if compensated is None:
                    continue
                try:
                    _commit_entity(compensated.copy(), clase, layer)
                except Exception:
                    continue
            elif kind == "polyline":
                pts = _entity_points_xy(payload)
                if not pts:
                    continue
                _export_offset_contour_to_msp(
                    msp,
                    m,
                    pts,
                    offset_mm=offset_mm,
                    outward=outward,
                    clase=clase,
                    layer=layer,
                    stats=stats,
                )
                layers_used.add(layer)
            elif kind == "chain":
                pts = _flatten_entity_group_inches(
                    payload, offset_in=_offset_inches(offset_mm)
                )
                if len(pts) < 3:
                    continue
                if _export_offset_contour_to_msp(
                    msp,
                    m,
                    pts,
                    offset_mm=offset_mm,
                    outward=outward,
                    clase=clase,
                    layer=layer,
                    stats=stats,
                ):
                    layers_used.add(layer)

    # --- Paso 2: marcas 1:1 (sin desfase) ---
    if draw_marks:
        for entity in entities:
            typ = entity.dxftype()
            if typ not in mark_types:
                continue
            clase = _clasificar_capa(str(entity.dxf.layer))
            if clase != "mark":
                continue
            layer = str(
                p.get("marks_layer")
                or p.get("marks_layer_override")
                or "MARK"
            )
            try:
                _commit_entity(entity.copy(), "mark", layer)
            except Exception:
                continue

    if doc is not None and layers_used:
        _import_layers_from_source(part_doc, doc, layers_used)

    return stats

