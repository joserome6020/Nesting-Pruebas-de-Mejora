# modules/nest_exporter.py
import os
import math
import uuid
import ezdxf
from ezdxf.addons import Importer
from ezdxf.math import Matrix44
from ezdxf import colors  
import config

from modules.dxf_native_curves import export_ring_native, normalize_ring
from modules.nesting_engine.geometry_parser import ESCALA_DXF, _clasificar_capa
from modules.nesting_engine.cu_largos_nesting import TOL_GEOM_MM
from freecad_runner import ejecutar_macro_freecad

# =========================================================
# NEST DXF EXPORTER
# =========================================================

def _ensure_closed(poly):
    if not poly or len(poly) < 2:
        return poly
    if poly[0] != poly[-1]:
        return poly + [poly[0]]
    return poly

def _rotate_point(x, y, deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return (x * c - y * s, x * s + y * c)

def _transform_poly(poly, tx=0.0, ty=0.0, rot_deg=0.0):
    out = []
    # 🚀 BLINDAJE: Validación de estructura de lista
    if not poly or not isinstance(poly, (list, tuple)): 
        return out
        
    for pt in poly:
        # 🚀 BLINDAJE: Evita 'tuple index out of range' en retazos incompletos
        if not isinstance(pt, (list, tuple)) or len(pt) < 2: 
            continue
            
        x, y = pt[0], pt[1]
        xr, yr = _rotate_point(x, y, rot_deg) if rot_deg else (x, y)
        out.append((xr + tx, yr + ty))
    return out

def _poly_bounds(poly):
    if not poly:
        return None
    xs, ys = [], []
    for pt in poly:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _bounds_close(a, b, tol=1.2):
    if a is None or b is None:
        return False
    return all(abs(float(a[i]) - float(b[i])) <= tol for i in range(4))


def _resolve_placement(p: dict) -> dict:
    """Completa shift desde el bbox del contorno colocado (p. ej. largos CU sin metadata 2D)."""
    out = dict(p)
    bounds = _poly_bounds(out.get("outer") or out.get("outer_poly"))
    if bounds:
        sx = float(out.get("shift_x", 0.0) or 0.0)
        sy = float(out.get("shift_y", 0.0) or 0.0)
        if abs(sx) < 1e-6 and abs(sy) < 1e-6:
            out["shift_x"] = bounds[0]
            out["shift_y"] = bounds[1]
    return out


def _build_placement_matrix(p) -> Matrix44:
    """Misma cadena que el visor/nesting: pulgadas→mm, normalizar, rotar en centroide, colocar en placa."""
    p = _resolve_placement(p)
    ox = float(p.get("orig_minx", 0.0))
    oy = float(p.get("orig_miny", 0.0))
    rot = math.radians(float(p.get("rot_deg", 0.0) or 0.0))
    if bool(p.get("cu_largos_piece")):
        bounds = _poly_bounds(p.get("outer") or p.get("outer_poly"))
        if bounds:
            sx, sy = bounds[0], bounds[1]
        else:
            sx = float(p.get("shift_x", 0.0))
            sy = float(p.get("shift_y", 0.0))
    else:
        sx = float(p.get("shift_x", 0.0))
        sy = float(p.get("shift_y", 0.0))
    rcx = float(p.get("rot_origin_cx", 0.0) or 0.0)
    rcy = float(p.get("rot_origin_cy", 0.0) or 0.0)

    m = Matrix44.scale(ESCALA_DXF, ESCALA_DXF, ESCALA_DXF)
    m @= Matrix44.translate(-ox, -oy, 0)
    if abs(rot) > 1e-12:
        m @= Matrix44.translate(rcx, rcy, 0)
        m @= Matrix44.z_rotate(rot)
        m @= Matrix44.translate(-rcx, -rcy, 0)
    m @= Matrix44.translate(sx, sy, 0)
    return m


def _circle_signature(ent, *, decimals: int = 2) -> tuple[float, float, float] | None:
    if ent.dxftype() != "CIRCLE":
        return None
    c = ent.dxf.center
    return (
        round(float(c.x), decimals),
        round(float(c.y), decimals),
        round(float(ent.dxf.radius), decimals),
    )


def _inner_polyline_redundant_with_circle(ent, circle_sigs: set[tuple]) -> bool:
    """DXF fuente a veces duplica barreno como CIRCLE + LWPOLYLINE facetada."""
    if ent.dxftype() not in ("LWPOLYLINE", "POLYLINE"):
        return False
    if not circle_sigs:
        return False
    try:
        from ezdxf import path as ezdxf_path

        p = ezdxf_path.make_path(ent)
        if not p.is_closed:
            return False
        verts = list(p.flattening(distance=0.05))
        if len(verts) < 8:
            return False
        xs = [float(v[0]) for v in verts]
        ys = [float(v[1]) for v in verts]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        r = sum(math.hypot(x - cx, y - cy) for x, y in zip(xs, ys)) / len(xs)
        if r < 0.2:
            return False
        sig = (round(cx, 2), round(cy, 2), round(r, 2))
        for cs in circle_sigs:
            if (
                abs(sig[0] - cs[0]) <= 0.35
                and abs(sig[1] - cs[1]) <= 0.35
                and abs(sig[2] - cs[2]) <= 0.35
            ):
                return True
    except Exception:
        pass
    return False


def _import_layers_from_source(source_doc, target_doc, layer_names: set[str]) -> None:
    """Registra en el DXF destino las capas del fuente (nombre, color, linetype)."""
    tgt = target_doc.layers
    for name in layer_names:
        if not name or name in tgt:
            continue
        try:
            src = source_doc.layers.get(name)
            attrs: dict = {}
            if src is not None:
                attrs["color"] = int(src.dxf.color)
                lt = str(src.dxf.linetype or "").strip()
                if lt and lt.upper() != "BYLAYER":
                    attrs["linetype"] = lt
            tgt.new(name, dxfattribs=attrs)
        except Exception:
            try:
                tgt.new(name)
            except Exception:
                pass


def _is_inner_cut_entity(ent) -> bool:
    return _clasificar_capa(str(ent.dxf.layer)) == "inner"


def _dedupe_staged_inner_circles(staged: list) -> list:
    circle_sigs = {
        sig
        for ent in staged
        if _is_inner_cut_entity(ent)
        for sig in [_circle_signature(ent)]
        if sig is not None
    }
    if not circle_sigs:
        return staged
    out = []
    for ent in staged:
        if _is_inner_cut_entity(ent) and _inner_polyline_redundant_with_circle(
            ent, circle_sigs
        ):
            continue
        out.append(ent)
    return out


def _edge_on_bar_exterior(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    bar_len: float,
    bar_w: float,
    tol: float = TOL_GEOM_MM,
    piece_bounds=None,
    idx: int = 0,
    n_total: int = 1,
) -> bool:
    """True si la arista es cara exterior del stock (no se corta con láser)."""
    from modules.nesting_engine.cu_largos_nesting import (
        _es_arista_horizontal,
        _es_arista_vertical,
    )

    if math.hypot(x2 - x1, y2 - y1) <= tol:
        return True

    # Cara izquierda del stock (inicio de barra → marcador CUT_CU, no láser)
    if _es_arista_vertical(x1, y1, x2, y2, tol) and x1 <= tol and x2 <= tol:
        return True

    # Guillotina vertical entre rebanadas (no el corte final de la última pieza).
    if piece_bounds and _es_arista_vertical(x1, y1, x2, y2, tol):
        minx, miny, maxx, maxy = piece_bounds
        alto = maxy - miny
        if alto > tol:
            span = abs(float(y2) - float(y1))
            if span >= alto - max(tol, alto * 0.05):
                x_mid = (x1 + x2) / 2.0
                if idx > 0 and abs(x_mid - minx) <= tol:
                    return True
                # Derecha: entre piezas o cierre final (este último va por CU_CORTE__V__N)
                if abs(x_mid - maxx) <= tol:
                    return True

    # Fondo y techo de la barra maestra: nunca láser (coinciden con esquinas del largo)
    if _es_arista_horizontal(x1, y1, x2, y2, tol):
        ymid = (y1 + y2) / 2.0
        if ymid <= tol:
            return True
        if bar_w > tol and abs(ymid - bar_w) <= tol:
            return True

    return False


def _edge_is_bar_interior_laser_cut(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    bar_len: float,
    bar_w: float,
    tol: float = TOL_GEOM_MM,
    piece_bounds=None,
    idx: int = 0,
    n_total: int = 1,
) -> bool:
    """
    Corte láser si la arista queda dentro de la barra y no es cara exterior del stock.
    Sin horizontales en fondo/techo ni guillotinas verticales completas entre piezas.
    """
    if math.hypot(x2 - x1, y2 - y1) <= tol:
        return False
    if _edge_on_bar_exterior(
        x1,
        y1,
        x2,
        y2,
        bar_len=bar_len,
        bar_w=bar_w,
        tol=tol,
        piece_bounds=piece_bounds,
        idx=idx,
        n_total=n_total,
    ):
        return False
    mx = (x1 + x2) / 2.0
    my = (y1 + y2) / 2.0
    if mx < -tol or my < -tol:
        return False
    if bar_len > tol and mx > bar_len + tol:
        return False
    if bar_w > tol and my > bar_w + tol:
        return False
    return True


def _arc_sample_points(arc, n: int = 5) -> list[tuple[float, float]]:
    c = arc.dxf.center
    r = float(arc.dxf.radius)
    sa = math.radians(float(arc.dxf.start_angle))
    ea = math.radians(float(arc.dxf.end_angle))
    if ea < sa:
        ea += 2.0 * math.pi
    pts = []
    for i in range(max(2, n)):
        t = sa + (ea - sa) * (i / max(n - 1, 1))
        pts.append((float(c.x) + r * math.cos(t), float(c.y) + r * math.sin(t)))
    return pts


def _iter_outer_edge_segments_mm(part_doc, m, *, flat_tol_mm: float = 0.02):
    """(p1, p2, entidad_nativa|None) del contorno exterior en mm."""
    from ezdxf import path as ezdxf_path

    for entity in part_doc.modelspace():
        if _clasificar_capa(str(entity.dxf.layer)) != "outer":
            continue
        try:
            e = entity.copy()
            if not e.transform(m):
                continue
            typ = e.dxftype()
            if typ == "LINE":
                s, en = e.dxf.start, e.dxf.end
                yield (
                    (float(s.x), float(s.y)),
                    (float(en.x), float(en.y)),
                    e,
                )
            elif typ == "ARC":
                pts = _arc_sample_points(e, n=12)
                for i in range(len(pts) - 1):
                    yield (pts[i], pts[i + 1], e if i == 0 else None)
            elif typ in ("LWPOLYLINE", "POLYLINE"):
                for sub in e.virtual_entities():
                    st = sub.dxftype()
                    if st == "LINE":
                        s, en = sub.dxf.start, sub.dxf.end
                        yield (
                            (float(s.x), float(s.y)),
                            (float(en.x), float(en.y)),
                            sub,
                        )
                    elif st == "ARC":
                        pts = _arc_sample_points(sub, n=12)
                        for i in range(len(pts) - 1):
                            yield (pts[i], pts[i + 1], sub if i == 0 else None)
            else:
                p = ezdxf_path.make_path(e)
                verts = list(p.flattening(distance=max(flat_tol_mm, 1e-4)))
                for i in range(len(verts) - 1):
                    a, b = verts[i], verts[i + 1]
                    yield (
                        (float(a[0]), float(a[1])),
                        (float(b[0]), float(b[1])),
                        None,
                    )
        except Exception:
            continue


def _emit_cut_outer_segment(
    msp,
    p1: tuple,
    p2: tuple,
    native,
    *,
    seen_lines: set,
    seen_arcs: set,
) -> bool:
    if native is not None and native.dxftype() == "ARC":
        c = native.dxf.center
        key = (
            round(float(c.x), 3),
            round(float(c.y), 3),
            round(float(native.dxf.radius), 4),
            round(float(native.dxf.start_angle), 3),
            round(float(native.dxf.end_angle), 3),
        )
        if key in seen_arcs:
            return False
        seen_arcs.add(key)
        native.dxf.layer = "CUT_OUTER"
        msp.add_entity(native)
        return True
    k1 = (round(float(p1[0]), 4), round(float(p1[1]), 4))
    k2 = (round(float(p2[0]), 4), round(float(p2[1]), 4))
    if (k1, k2) in seen_lines or (k2, k1) in seen_lines:
        return False
    seen_lines.add((k1, k2))
    if math.hypot(p2[0] - p1[0], p2[1] - p1[1]) < 1e-6:
        return False
    if native is not None and native.dxftype() == "LINE":
        native.dxf.layer = "CUT_OUTER"
        msp.add_entity(native)
    else:
        msp.add_line(p1, p2, dxfattribs={"layer": "CUT_OUTER"})
    return True


def _export_cu_contour_cuts_from_ring(msp, ring, p: dict) -> int:
    """Respaldo: aristas del contorno colocado que son corte interior a la barra."""
    bar_w = float(p.get("cu_bar_w_mm") or 0.0)
    bar_l = float(p.get("cu_bar_l_mm") or 0.0)
    idx = int(p.get("cu_slice_idx", 0) or 0)
    n_total = max(1, int(p.get("cu_slice_count", 1) or 1))
    pts = normalize_ring(ring, closed=True)
    if len(pts) < 2:
        return 0
    piece_bounds = _poly_bounds(pts)
    seen_lines: set = set()
    seen_arcs: set = set()
    added = 0
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        p1, p2 = pts[i], pts[j]
        if _edge_is_bar_interior_laser_cut(
            p1[0],
            p1[1],
            p2[0],
            p2[1],
            bar_len=bar_l,
            bar_w=bar_w,
            tol=TOL_GEOM_MM,
            piece_bounds=piece_bounds,
            idx=idx,
            n_total=n_total,
        ):
            if _emit_cut_outer_segment(msp, p1, p2, None, seen_lines=seen_lines, seen_arcs=seen_arcs):
                added += 1
    return added


def _export_cu_laser_outer_cuts_native(msp, part_doc, m, p: dict) -> int:
    """
    Aristas del contorno DXF dentro de la barra → CUT_OUTER (LINE/ARC nativos, 1:1).
    """
    bar_w = float(p.get("cu_bar_w_mm") or 0.0)
    bar_l = float(p.get("cu_bar_l_mm") or 0.0)
    idx = int(p.get("cu_slice_idx", 0) or 0)
    n_total = max(1, int(p.get("cu_slice_count", 1) or 1))
    outer = p.get("outer") or p.get("outer_poly") or []
    piece_bounds = _poly_bounds(outer)
    if not piece_bounds and (bar_w <= 0 or bar_l <= 0):
        return 0
    if bar_w <= 0 and piece_bounds:
        bar_w = piece_bounds[3] - piece_bounds[1]
    if bar_l <= 0 and piece_bounds:
        bar_l = piece_bounds[2] - piece_bounds[0]

    seen_lines: set = set()
    seen_arcs: set = set()
    added = 0

    for p1, p2, native in _iter_outer_edge_segments_mm(part_doc, m):
        if not _edge_is_bar_interior_laser_cut(
            p1[0],
            p1[1],
            p2[0],
            p2[1],
            bar_len=bar_l,
            bar_w=bar_w,
            tol=TOL_GEOM_MM,
            piece_bounds=piece_bounds,
            idx=idx,
            n_total=n_total,
        ):
            continue
        if _emit_cut_outer_segment(
            msp, p1, p2, native, seen_lines=seen_lines, seen_arcs=seen_arcs
        ):
            added += 1

    return added


def _layer_for_exported_entity(clase: str, entity, p: dict) -> str:
    """Capa destino según modo de exportación."""
    raw = str(entity.dxf.layer or "").strip()
    if bool(p.get("cu_largos_piece")):
        if clase == "inner":
            return "CUT_INNER"
        if clase == "mark":
            return "MARK"
        return raw or "CUT_OUTER"
    return raw


def _export_cu_inner_and_marks(
    msp,
    p: dict,
    *,
    draw_holes: bool = True,
    draw_marks: bool = True,
) -> bool:
    """Inner/marks cobre largos desde polígonos del nest (mm placa, 1:1 con visor)."""
    added = False
    if draw_holes:
        for h in p.get("holes") or p.get("inner") or []:
            h_t = _transform_poly(h, tx=0.0, ty=0.0, rot_deg=0.0)
            if not h_t:
                continue
            export_ring_native(msp, h_t, "CUT_INNER", closed=True, prefer_circle=True)
            added = True
    if draw_marks:
        for mk in p.get("marks") or p.get("mark") or []:
            if not mk:
                continue
            mk_t = _transform_poly(mk, tx=0.0, ty=0.0, rot_deg=0.0)
            if mk_t:
                _add_lwpolyline(msp, mk_t, layer="MARK", closed=False)
                added = True
    return added


def _export_cu_largos_from_source(
    msp,
    doc,
    p: dict,
    *,
    draw_marks: bool = True,
) -> bool:
    """
    Cobre largos con DXF fuente: cortes CUT_OUTER nativos + inner/marks del nest colocado.
    Los polígonos del nest ya están en mm de placa; no clonar inner/mark del DXF (evita desfase).
    """
    ruta = str(p.get("ruta") or "").strip()
    outer = p.get("outer") or p.get("outer_poly") or []
    if not ruta or not os.path.isfile(ruta) or len(outer) < 2:
        return False

    try:
        part_doc = ezdxf.readfile(ruta)
    except Exception as e:
        print(f"[WARN] DXF fuente ilegible {ruta}: {e}")
        return False

    m = _build_placement_matrix(p)
    laser_added = _export_cu_laser_outer_cuts_native(msp, part_doc, m, p)
    if laser_added == 0:
        outer_t = _transform_poly(outer, tx=0.0, ty=0.0, rot_deg=0.0)
        if outer_t:
            laser_added = _export_cu_contour_cuts_from_ring(msp, outer_t, p)

    inner_ok = _export_cu_inner_and_marks(msp, p, draw_marks=draw_marks)
    return laser_added > 0 or inner_ok


def _export_source_dxf_at_placement(
    msp,
    doc,
    p: dict,
    *,
    draw_marks: bool = True,
    bounds_tol: float = 3.0,
) -> bool:
    """
    Clona entidades nativas del DXF fuente en la posición del nest.
    Conserva capas y geometría originales (solo escala pulg→mm + colocación).
    """
    ruta = str(p.get("ruta") or "").strip()
    outer = p.get("outer") or p.get("outer_poly") or []
    if not ruta or not os.path.isfile(ruta) or len(outer) < 2:
        return False

    try:
        part_doc = ezdxf.readfile(ruta)
    except Exception as e:
        print(f"[WARN] DXF fuente ilegible {ruta}: {e}")
        return False

    m = _build_placement_matrix(_resolve_placement(p))
    outer_bounds = _poly_bounds(outer)
    staged = []
    outer_pts = []

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
        if clase == "outer" and p.get("cu_largos_piece"):
            continue
        try:
            new_e = entity.copy()
            if not new_e.transform(m):
                continue
            new_e.dxf.layer = _layer_for_exported_entity(clase, new_e, p)
            staged.append(new_e)
            if clase == "outer":
                try:
                    from ezdxf import bbox as ezdxf_bbox

                    ext = ezdxf_bbox.extents([new_e])
                    outer_pts.extend(
                        [
                            (ext.extmin.x, ext.extmin.y),
                            (ext.extmax.x, ext.extmax.y),
                        ]
                    )
                except Exception:
                    pass
        except Exception:
            continue

    if not staged and not p.get("cu_largos_piece"):
        return False

    if outer_bounds and outer_pts:
        xs = [pt[0] for pt in outer_pts]
        ys = [pt[1] for pt in outer_pts]
        src_bounds = (min(xs), min(ys), max(xs), max(ys))
        if not _bounds_close(outer_bounds, src_bounds, tol=bounds_tol):
            print(
                f"[WARN] DXF fuente bbox distinto al nest para "
                f"{p.get('part_name', '?')}: nest={outer_bounds} vs dxf={src_bounds}"
            )

    staged = _dedupe_staged_inner_circles(staged)
    layer_names = {str(ent.dxf.layer) for ent in staged if ent.dxf.layer}
    _import_layers_from_source(part_doc, doc, layer_names)
    for ent in staged:
        msp.add_entity(ent)

    laser_added = 0
    if p.get("cu_largos_piece"):
        laser_added = _export_cu_laser_outer_cuts_native(msp, part_doc, m, p)

    return bool(staged) or laser_added > 0


def _export_ring_exact(msp, points, layer: str, *, closed: bool = True) -> bool:
    """
    Exporta anillo como LINE entre vértices del nest — sin inferir círculos/arcos.
    Posición 1:1 con el visor; respaldo cuando no hay DXF fuente válido.
    """
    pts = normalize_ring(points, closed=closed)
    if len(pts) < 2:
        return False
    if closed:
        for i in range(len(pts)):
            j = (i + 1) % len(pts)
            x1, y1 = pts[i]
            x2, y2 = pts[j]
            if math.hypot(x2 - x1, y2 - y1) < 1e-6:
                continue
            msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer})
    else:
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            if math.hypot(x2 - x1, y2 - y1) < 1e-6:
                continue
            msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer})
    return True


def _add_lwpolyline(msp, points, layer, closed=True):
    """
    Versión Purificada: 
    Delega el cierre del polígono puramente a la etiqueta 'closed'.
    """
    # 🚀 BLINDAJE: AutoCAD requiere al menos 2 puntos para una polilínea
    if not points or len(points) < 2: 
        return
    msp.add_lwpolyline(points, dxfattribs={"layer": layer, "closed": bool(closed)})


def _export_placed_geometry(msp, p, *, draw_holes=True, draw_marks=True) -> bool:
    """
    Exporta contorno/marcas en coordenadas de placa (mm), 1:1 con el visor de nesting.
    Fuente de verdad: poligonos/marcas ya colocados por el motor.
    """
    outer = p.get("outer") or p.get("outer_poly")
    has_outer = bool(outer and len(outer) >= 2)
    marks = p.get("marks") or p.get("mark") or []
    if not has_outer and not marks:
        return False

    if has_outer:
        outer_t = _transform_poly(outer, tx=0.0, ty=0.0, rot_deg=0.0)
        if outer_t and not p.get("cu_largos_holes_only"):
            if p.get("cu_largos_piece"):
                _export_cu_contour_cuts_from_ring(msp, outer_t, p)
            else:
                layer_destino = str(p.get("layer_override") or "CUT_OUTER")
                closed_destino = bool(p.get("closed", True))
                if p.get("use_native_curves"):
                    export_ring_native(msp, outer_t, layer_destino, closed=closed_destino)
                else:
                    _export_ring_exact(msp, outer_t, layer_destino, closed=closed_destino)

    if p.get("cu_largos_piece"):
        _export_cu_inner_and_marks(msp, p, draw_holes=draw_holes, draw_marks=draw_marks)
    else:
        if draw_holes and has_outer:
            holes = p.get("holes") or p.get("inner") or []
            for h in holes:
                h_t = _transform_poly(h, tx=0.0, ty=0.0, rot_deg=0.0)
                if not h_t:
                    continue
                hole_layer = str(p.get("inner_layer_override") or "CUT_INNER")
                if p.get("use_native_curves"):
                    export_ring_native(msp, h_t, hole_layer, closed=True, prefer_circle=True)
                else:
                    _export_ring_exact(msp, h_t, hole_layer, closed=True)

        if draw_marks:
            part_name = str(p.get("part_name") or p.get("name") or "")
            marks_layer = str(
                p.get("marks_layer")
                or p.get("marks_layer_override")
                or ("RTZ_LABEL" if part_name.startswith("TATUAJE") else "MARK")
            )
            for mk in marks:
                if not mk:
                    continue
                mk_t = _transform_poly(mk, tx=0.0, ty=0.0, rot_deg=0.0)
                if mk_t:
                    _add_lwpolyline(msp, mk_t, layer=marks_layer, closed=False)

    return has_outer or bool(marks)


def _export_block_at_placement(msp, doc, cache_blocks: dict, p: dict) -> bool:
    """Clona el modelspace del DXF fuente con transformación (geometría nativa exacta)."""
    ruta_original = str(p.get("ruta") or "").strip()
    if not ruta_original or not os.path.isfile(ruta_original):
        return False

    if ruta_original not in cache_blocks:
        safe_block_name = f"BLK_{uuid.uuid4().hex[:8]}"
        try:
            part_doc = ezdxf.readfile(ruta_original)
            _import_layers_from_source(
                part_doc, doc, {str(n) for n in part_doc.layers if str(n)}
            )
            blk = doc.blocks.new(name=safe_block_name)
            importer = Importer(part_doc, doc)
            importer.import_modelspace(blk)
            importer.finalize()
            cache_blocks[ruta_original] = safe_block_name
        except Exception as e:
            print(f"[ERROR] No se pudo leer el DXF base {ruta_original}: {e}")
            return False

    safe_block_name = cache_blocks.get(ruta_original)
    if not safe_block_name or safe_block_name not in doc.blocks:
        return False

    try:
        m = _build_placement_matrix(_resolve_placement(p))
        blockref = msp.add_blockref(safe_block_name, insert=(0, 0))
        blockref.transform(m)
        blockref.explode()
        return True
    except Exception as e:
        part_name = str(p.get("part_name", p.get("name", "?")))
        print(f"[ERROR] Transformación block falló para {part_name}: {e}")
        return False

def _max_cu_corte_v_index(placements: list) -> int:
    best = 0
    for p in placements or []:
        nom = str(p.get("part_name", p.get("name", "")))
        if not nom.startswith("CU_CORTE__V__"):
            continue
        try:
            best = max(best, int(nom.rsplit("V__", 1)[-1]))
        except ValueError:
            continue
    return best


def _is_cu_corte_fin_bar(p: dict, placements: list) -> bool:
    """True solo para la guillotina vertical al final del nest (última placa)."""
    nom = str(p.get("part_name", p.get("name", "")))
    if not nom.startswith("CU_CORTE__V__"):
        return False
    try:
        idx = int(nom.rsplit("V__", 1)[-1])
    except ValueError:
        return False
    fin = _max_cu_corte_v_index(placements)
    return fin > 0 and idx == fin


def _export_cu_bar_inicio_marker(msp, bar_w: float) -> None:
    """Línea vertical en x=0 que marca el inicio de la barra maestra (solo CUT_CU)."""
    if bar_w <= TOL_GEOM_MM:
        return
    msp.add_line((0.0, 0.0), (0.0, float(bar_w)), dxfattribs={"layer": "CUT_CU"})


def _setup_layers(doc, *, solo_cobre: bool = False):
    layers = doc.layers
    def ensure(name, color_index):
        if name not in layers:
            layers.new(name, dxfattribs={"color": int(color_index)})
        else:
            layers.get(name).dxf.color = int(color_index)

    ensure("CUT_OUTER", 1)
    ensure("CUT_INNER", 2)
    ensure("MARK", 4)
    ensure("CUT_CU", 1)
    if not solo_cobre:
        ensure("Plate", 3)
        ensure("Plate_Text", 7)
        ensure("RTZ_LABEL", 4)


def _purge_capas_no_produccion_cobre(doc) -> None:
    """DXF cobre láser: solo capas con geometría de corte/marcaje."""
    msp = doc.modelspace()
    used = {str(e.dxf.layer) for e in msp if getattr(e.dxf, "layer", None)}
    for layer in list(doc.layers):
        name = str(layer.dxf.name)
        if name in used:
            continue
        try:
            doc.layers.remove(name)
        except Exception:
            pass


# =========================================================================
# EXPORTACIÓN PRINCIPAL
# =========================================================================
def export_nest_to_dxf(
    out_path: str,
    sheet: dict,
    placements: list,
    *,
    title: str = "NEST_EXPORT",
    draw_holes: bool = True,
    draw_labels: bool = False,  
    draw_marks: bool = True,
    label_height: float = 25.0,
    margin_text: float = 15.0,
    modo_largos_cu: bool = False,
):
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)

    solo_cobre = bool(
        modo_largos_cu
        or (isinstance(sheet, dict) and sheet.get("modo_largos_cu"))
        or any(bool(p.get("cu_largos_piece")) for p in (placements or []))
    )

    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 4  
    _setup_layers(doc, solo_cobre=solo_cobre)

    msp = doc.modelspace()

    _sheet_bar_l = float(sheet.get("length", sheet.get("Length", 0)) or 0)
    _sheet_bar_w = float(sheet.get("width", sheet.get("Width", 0)) or 0)

    if not solo_cobre:
        L = float(sheet.get("length", sheet.get("Length", 0)))
        W = float(sheet.get("width",  sheet.get("Width",  0)))

        sheet_poly = [(0, 0), (L, 0), (L, W), (0, W)]
        _add_lwpolyline(msp, sheet_poly, layer="Plate", closed=True)

        material = sheet.get("material", sheet.get("Material", ""))
        thickness = sheet.get("thickness", sheet.get("Thickness", ""))
        arga_code = sheet.get("arga_code", sheet.get("Arga Code", ""))

        header = f"{title} | {arga_code} | {material} | THK:{thickness} | {L:.1f}x{W:.1f} mm"
        msp.add_text(
            header,
            dxfattribs={"layer": "Plate_Text", "height": label_height} 
        ).set_placement((0, W + margin_text))

    if solo_cobre and _sheet_bar_w > TOL_GEOM_MM:
        _export_cu_bar_inicio_marker(msp, _sheet_bar_w)

    # =========================================================================
    # INSERCIÓN DE PIEZAS: CLONACIÓN GÉNESIS
    # =========================================================================
    cache_blocks = {} 

    for i, p in enumerate(placements, start=1):
        if solo_cobre and bool(p.get("cu_largos_piece")):
            if not float(p.get("cu_bar_w_mm") or 0):
                p["cu_bar_w_mm"] = _sheet_bar_w
            if not float(p.get("cu_bar_l_mm") or 0):
                p["cu_bar_l_mm"] = _sheet_bar_l

        ruta_original = p.get("ruta")
        part_name = str(p.get("part_name", p.get("name", f"PART_{i}")))
        # Guillotinas entre piezas: solo visor. Al DXF láser va únicamente el corte final.
        if solo_cobre and part_name.startswith("CU_CORTE__"):
            if not _is_cu_corte_fin_bar(p, placements):
                continue
        prefer_source = bool(p.get("prefer_source_dxf"))
        compensated = bool(p.get("compensated"))
        cu_largos_piece = bool(p.get("cu_largos_piece"))

        # 1) Cobre largos: cortes nativos + inner/marks del nest (1:1 visor).
        if cu_largos_piece and prefer_source and not compensated and ruta_original:
            if _export_cu_largos_from_source(msp, doc, p, draw_marks=draw_marks):
                continue

        # 2) DXF fuente (placas 2D y demás).
        if prefer_source and not compensated and ruta_original:
            if _export_source_dxf_at_placement(msp, doc, p, draw_marks=draw_marks):
                continue

        # 3) Block explode — no en cobre largos (mezclaría contorno con CUT_OUTER).
        if (
            not compensated
            and not cu_largos_piece
            and ruta_original
            and os.path.exists(ruta_original)
        ):
            if _export_block_at_placement(msp, doc, cache_blocks, p):
                continue

        # 4) Respaldo: polígonos del nest (sin DXF o piezas virtuales).
        if _export_placed_geometry(
            msp, p, draw_holes=draw_holes, draw_marks=draw_marks
        ):
            continue

        part_name = str(p.get("part_name", p.get("name", f"PART_{i}")))
        print(
            f"[WARN] Sin geometría exportable para {part_name} "
            f"(sin outer colocado ni DXF fuente válido)"
        )

    if solo_cobre:
        _purge_capas_no_produccion_cobre(doc)

    doc.saveas(out_path)
    return out_path

def export_all_sheets(out_dir, nests, *, base_name="NEST", draw_holes=True, draw_labels=False, draw_marks=True):
    os.makedirs(out_dir, exist_ok=True)
    exported = []
    for idx, n in enumerate(nests, start=1):
        sheet = n.get("sheet", {})
        placements = n.get("placements", [])
        out_path = os.path.join(out_dir, f"{base_name}_SHEET_{idx:02d}.dxf")
        export_nest_to_dxf(out_path, sheet, placements, title=f"{base_name} - SHEET {idx}", 
                           draw_holes=draw_holes, draw_labels=draw_labels, draw_marks=draw_marks)
        exported.append(out_path)
    return exported

class NestExporter:
    def export_all_sheets(self, job_folder_or_out_dir, *args, **kwargs):
        # Esta clase es un puente para mantener compatibilidad con la UI
        return []