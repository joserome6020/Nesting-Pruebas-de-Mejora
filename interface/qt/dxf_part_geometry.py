"""Utilidades geométricas y de capas DXF para el visor CAD de piezas."""
from __future__ import annotations

import math

import numpy as np

from modules.plasma_compensator import _arc_points_from_bulge


def poly_area_2d(pts) -> float:
    if not pts or len(pts) < 3:
        return 0.0
    area = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        area += (x1 * y2) - (x2 * y1)
    return abs(area) * 0.5


def rotar_punto(x, y, cx, cy, deg):
    rad = math.radians(float(deg))
    dx = x - cx
    dy = y - cy
    xr = (dx * math.cos(rad)) - (dy * math.sin(rad))
    yr = (dx * math.sin(rad)) + (dy * math.cos(rad))
    return (cx + xr, cy + yr)


def dxf_arc_ccw_sweep_rad(start_deg, end_deg):
    sa = math.radians(float(start_deg))
    span_deg = (float(end_deg) - float(start_deg)) % 360.0
    if span_deg < 1e-12:
        span_deg = 360.0
    return sa, math.radians(span_deg)


def es_mark_layer(layer_upper: str) -> bool:
    u = str(layer_upper or "").upper()
    return any(m in u for m in ("MARK", "ETCH", "IV_MARK"))


def es_outer_layer(layer_upper: str) -> bool:
    u = str(layer_upper or "").upper()
    return ("CUT_OUTER" in u) or ("IV_OUTER_PROFILE" in u)


def es_inner_layer(layer_upper: str) -> bool:
    u = str(layer_upper or "").upper()
    return ("CUT_INNER" in u) or ("IV_INTERIOR_PROFILES" in u)


def es_cut_layer(layer_upper: str) -> bool:
    u = str(layer_upper or "").upper()
    return ("CUT" in u) or es_outer_layer(u) or es_inner_layer(u)


def capa_relevante_visual(layer: str, render_all_layers: bool) -> bool:
    if render_all_layers:
        return True
    u = layer.upper()
    if "CUT" in u or "IV_OUTER_PROFILE" in u or "IV_INTERIOR_PROFILES" in u:
        return True
    return any(m in u for m in ("MARK", "ETCH", "IV_MARK"))


def rol_capa_pieza(layer_upper: str) -> str:
    if es_inner_layer(layer_upper):
        return "inner"
    if es_outer_layer(layer_upper):
        return "outer"
    if es_mark_layer(layer_upper):
        return "mark"
    return "auto"


def centroid_2d(pts):
    if not pts:
        return 0.0, 0.0
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def punto_en_poligono(x, y, poly) -> bool:
    inside = False
    n = len(poly)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / max(yj - yi, 1e-18) + xi
        ):
            inside = not inside
        j = i
    return inside


def clasificar_contornos_cerrados(shapes: list) -> tuple[list, list]:
    outers: list = []
    inners: list = []
    pendientes: list = []
    for sh in shapes:
        rol = sh.get("rol", "auto")
        if rol == "inner":
            inners.append(sh)
        elif rol == "outer":
            outers.append(sh)
        elif rol == "mark":
            continue
        else:
            pendientes.append(sh)

    if not outers and pendientes:
        pendientes.sort(key=lambda s: float(s.get("area", 0.0) or 0.0), reverse=True)
        outers.append(pendientes.pop(0))
        for sh in pendientes:
            cx, cy = sh.get("centroid", (0.0, 0.0))
            if any(punto_en_poligono(cx, cy, o.get("pts") or []) for o in outers):
                inners.append(sh)
            else:
                outers.append(sh)
        return outers, inners

    for sh in pendientes:
        cx, cy = sh.get("centroid", (0.0, 0.0))
        if outers and any(punto_en_poligono(cx, cy, o.get("pts") or []) for o in outers):
            inners.append(sh)
        else:
            outers.append(sh)
    return outers, inners


def dist_punto_segmento(px, py, x1, y1, x2, y2):
    vx, vy = x2 - x1, y2 - y1
    L2 = vx * vx + vy * vy
    if L2 < 1e-18:
        return math.hypot(px - x1, py - y1), (x1, y1)
    t = max(0.0, min(1.0, ((px - x1) * vx + (py - y1) * vy) / L2))
    qx = x1 + t * vx
    qy = y1 + t * vy
    return math.hypot(px - qx, py - qy), (qx, qy)


def ajuste_circulo_desde_puntos(pts):
    if len(pts) < 4:
        return None
    arr = np.asarray(pts, dtype=float)
    x, y = arr[:, 0], arr[:, 1]
    x_m, y_m = float(np.mean(x)), float(np.mean(y))
    u, v = x - x_m, y - y_m
    Suu = float(np.sum(u * u))
    Svv = float(np.sum(v * v))
    Suv = float(np.sum(u * v))
    if abs(Suu * Svv - Suv * Suv) < 1e-22:
        return None
    Suuu = float(np.sum(u**3))
    Svvv = float(np.sum(v**3))
    Suvv = float(np.sum(u * v * v))
    Svuu = float(np.sum(v * u * u))
    A = np.array([[Suu, Suv], [Suv, Svv]])
    b = 0.5 * np.array([Suuu + Suvv, Svvv + Svuu])
    try:
        uc, vc = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        return None
    cx, cy = uc + x_m, vc + y_m
    npt = len(x)
    r_sq = uc * uc + vc * vc + (Suu + Svv) / max(npt, 1)
    if r_sq <= 1e-18:
        return None
    r = float(math.sqrt(r_sq))
    dists = np.hypot(x - cx, y - cy)
    err_max = float(np.max(np.abs(dists - r)))
    return cx, cy, r, err_max


def centro_y_radio_bulge(p1, p2, bulge):
    if abs(bulge) < 1e-12:
        return None
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    chord = math.hypot(dx, dy)
    if chord <= 1e-9:
        return None
    theta = 4.0 * math.atan(bulge)
    half_theta = abs(theta) / 2.0
    sin_half = math.sin(half_theta)
    if abs(sin_half) < 1e-12:
        return None
    r = chord / (2.0 * sin_half)
    mx = (x1 + x2) / 2.0
    my = (y1 + y2) / 2.0
    alpha = math.atan2(dy, dx)
    h_sq = max(r * r - (chord / 2.0) ** 2, 0.0)
    h = math.sqrt(h_sq)
    nx = -math.sin(alpha)
    ny = math.cos(alpha)
    sign = 1.0 if bulge > 0 else -1.0
    cx = mx + sign * h * nx
    cy = my + sign * h * ny
    return cx, cy, r


def vector_unitario_arista(seg):
    x1, y1, x2, y2 = seg[0], seg[1], seg[2], seg[3]
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    if L < 1e-12:
        return None
    return (dx / L, dy / L)


def aristas_misma_geometria(seg1, sc) -> bool:
    if sc.get("tipo") != "arista":
        return False
    a = (seg1[0], seg1[1], seg1[2], seg1[3])
    b = (sc["x1"], sc["y1"], sc["x2"], sc["y2"])
    eps = 1e-5

    def cerca(p, q):
        return abs(p[0] - q[0]) < eps and abs(p[1] - q[1]) < eps

    return (cerca((a[0], a[1]), (b[0], b[1])) and cerca((a[2], a[3]), (b[2], b[3]))) or (
        cerca((a[0], a[1]), (b[2], b[3])) and cerca((a[2], a[3]), (b[0], b[1]))
    )


def aristas_paralelas(u1, u2, tol_deg=3.0) -> bool:
    if u1 is None or u2 is None:
        return False
    c = min(1.0, abs(u1[0] * u2[0] + u1[1] * u2[1]))
    ang = math.degrees(math.acos(c))
    return ang < tol_deg or ang > 180.0 - tol_deg


def interseccion_lineas_inf(seg1, seg2):
    x1, y1, x2, y2 = float(seg1[0]), float(seg1[1]), float(seg1[2]), float(seg1[3])
    x3, y3, x4, y4 = float(seg2[0]), float(seg2[1]), float(seg2[2]), float(seg2[3])
    rx, ry = x2 - x1, y2 - y1
    sx, sy = x4 - x3, y4 - y3
    den = rx * sy - ry * sx
    if abs(den) < 1e-12:
        return None
    qpx, qpy = x3 - x1, y3 - y1
    t = (qpx * sy - qpy * sx) / den
    return (x1 + t * rx, y1 + t * ry)


def dir_desde_vertice(seg, vtx, pt_ref=None):
    ax, ay, bx, by = float(seg[0]), float(seg[1]), float(seg[2]), float(seg[3])
    vx, vy = float(vtx[0]), float(vtx[1])
    cand = []
    for ex, ey in ((ax, ay), (bx, by)):
        dx, dy = ex - vx, ey - vy
        L = math.hypot(dx, dy)
        if L > 1e-12:
            cand.append((dx / L, dy / L, L))
    if not cand:
        return None
    if pt_ref is not None:
        rx, ry = float(pt_ref[0]) - vx, float(pt_ref[1]) - vy
        rl = math.hypot(rx, ry)
        if rl > 1e-12:
            rx, ry = rx / rl, ry / rl
            cand.sort(key=lambda it: -(it[0] * rx + it[1] * ry))
            return (cand[0][0], cand[0][1])
    cand.sort(key=lambda it: -it[2])
    return (cand[0][0], cand[0][1])


def resolver_angulo_aristas(seg1, seg2, p1_ref, p2_ref, span):
    vtx = interseccion_lineas_inf(seg1, seg2)
    if vtx is None:
        return None
    v1 = dist_punto_segmento(vtx[0], vtx[1], seg1[0], seg1[1], seg1[2], seg1[3])[0]
    v2 = dist_punto_segmento(vtx[0], vtx[1], seg2[0], seg2[1], seg2[2], seg2[3])[0]
    if max(v1, v2) > span * 0.10:
        return None
    u1 = dir_desde_vertice(seg1, vtx, p1_ref)
    u2 = dir_desde_vertice(seg2, vtx, p2_ref)
    if u1 is None or u2 is None:
        return None
    d = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
    ang = math.degrees(math.acos(d))
    if ang < 1.0 or ang > 179.0:
        return None
    return {"vtx": vtx, "u1": u1, "u2": u2}


def normal_cota_desde_cuerda(p1, p2, centro_pieza):
    x1, y1 = float(p1[0]), float(p1[1])
    x2, y2 = float(p2[0]), float(p2[1])
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    if L < 1e-12:
        return None
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux
    midx, midy = (x1 + x2) * 0.5, (y1 + y2) * 0.5
    cmx, cmy = centro_pieza
    if nx * (midx - cmx) + ny * (midy - cmy) < 0:
        nx, ny = -nx, -ny
    return nx, ny, ux, uy, L


def snap_en_borde_para_lineal(sc) -> bool:
    return sc.get("tipo") in ("arista", "vertice")


def clasificar_snap_arista(x, y, x1, y1, x2, y2, span):
    tol_ep = span * 0.015
    tol_mid = span * 0.011
    tol_seg = span * 0.032
    vx, vy = x2 - x1, y2 - y1
    L2 = vx * vx + vy * vy
    if L2 < 1e-18:
        return None
    t = max(0.0, min(1.0, ((x - x1) * vx + (y - y1) * vy) / L2))
    qx = x1 + t * vx
    qy = y1 + t * vy
    dseg = math.hypot(x - qx, y - qy)
    da = math.hypot(x - x1, y - y1)
    db = math.hypot(x - x2, y - y2)
    mx, my = (x1 + x2) * 0.5, (y1 + y2) * 0.5
    dm = math.hypot(x - mx, y - my)
    if da <= tol_ep:
        return da, (x1, y1), "endpoint"
    if db <= tol_ep:
        return db, (x2, y2), "endpoint"
    if dm <= tol_mid:
        return dm, (mx, my), "midpoint"
    if dseg <= tol_seg:
        return dseg, (qx, qy), "arista_cuerpo"
    return None


def tessellate_arco_ccw(cx, cy, r, start_deg, end_deg, aid, out_segs, max_deg_step=7.5):
    """Aproxima un arco DXF en segmentos con índice de arco para snap/cotas radiales."""
    t0, sweep = dxf_arc_ccw_sweep_rad(start_deg, end_deg)
    n = max(2, int(math.degrees(sweep) / max_deg_step) + 1)
    prev = None
    for i in range(n + 1):
        u = t0 + sweep * (i / n)
        px = cx + r * math.cos(u)
        py = cy + r * math.sin(u)
        if prev is not None:
            out_segs.append((prev[0], prev[1], px, py, aid))
        prev = (px, py)


def registrar_arcos_bulge(entity, layer, rocx, rocy, rot, render_all_layers, out_arcos, out_segs):
    if not capa_relevante_visual(layer, render_all_layers):
        return
    typ = entity.dxftype()
    verts = []
    try:
        if typ == "LWPOLYLINE":
            for item in entity.get_points("xyb"):
                if len(item) >= 3:
                    verts.append((float(item[0]), float(item[1]), float(item[2] or 0.0)))
                else:
                    verts.append((float(item[0]), float(item[1]), 0.0))
        elif typ == "POLYLINE":
            for v in entity.vertices:
                p = v.dxf.location
                b = float(getattr(v.dxf, "bulge", 0.0) or 0.0)
                verts.append((float(p.x), float(p.y), b))
        else:
            return
    except Exception:
        return
    n = len(verts)
    if n < 2:
        return
    closed = False
    try:
        if typ == "LWPOLYLINE":
            closed = bool(entity.closed)
        elif typ == "POLYLINE":
            closed = bool(getattr(entity, "is_closed", False))
    except Exception:
        closed = False
    nseg = n if closed else n - 1
    for i in range(nseg):
        x1, y1, b = verts[i]
        x2, y2, _ = verts[(i + 1) % n]
        if abs(b) < 1e-12:
            continue
        cr = centro_y_radio_bulge((x1, y1), (x2, y2), b)
        if cr is None:
            continue
        cx_m, cy_m, r = cr
        arc_pts = _arc_points_from_bulge((x1, y1), (x2, y2), b, max_deg_step=7.5)
        if len(arc_pts) < 2:
            continue
        if rot:
            cx_v, cy_v = rotar_punto(cx_m, cy_m, rocx, rocy, rot)
        else:
            cx_v, cy_v = cx_m, cy_m
        out_arcos.append({"cx": cx_v, "cy": cy_v, "r": float(r)})
        aid = len(out_arcos) - 1
        poly = []
        for px, py in arc_pts:
            if rot:
                px, py = rotar_punto(px, py, rocx, rocy, rot)
            poly.append((px, py))
        for ii in range(len(poly) - 1):
            a, b2 = poly[ii], poly[ii + 1]
            out_segs.append((a[0], a[1], b2[0], b2[1], aid))


def circulos_snap_agregar_si_circular(pts, layer_upper, span_ref, factor_conversion, circulos_snap):
    if len(pts) < 6:
        return
    lu = layer_upper.upper()
    if not es_cut_layer(lu) and not es_mark_layer(lu):
        return
    fit = ajuste_circulo_desde_puntos(pts)
    if fit is None:
        return
    rcx, rcy, rr, err_max = fit
    if rr <= 1e-9:
        return
    tol_abs = max(span_ref * 8e-5, float(factor_conversion) * 4e-5)
    tol_rel = 0.014
    if err_max > tol_abs and err_max / rr > tol_rel:
        return
    tag = "inner" if es_inner_layer(lu) else ("mark" if es_mark_layer(lu) else "outer")
    for ex in circulos_snap:
        ecx, ecy, er = float(ex[0]), float(ex[1]), float(ex[2])
        if math.hypot(rcx - ecx, rcy - ecy) < max(rr, er) * 0.04:
            if abs(rr - er) / max(rr, er, 1e-12) < 0.055:
                return
    circulos_snap.append((rcx, rcy, rr, tag))


def _dedupe_segments(segments, tol=1e-4):
    seen = set()
    out = []
    for seg in segments:
        x1, y1, x2, y2 = seg[0], seg[1], seg[2], seg[3]
        aid = seg[4] if len(seg) > 4 else None
        k1 = (round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4))
        k2 = (k1[2], k1[3], k1[0], k1[1])
        if k1 in seen or k2 in seen:
            continue
        seen.add(k1)
        out.append((x1, y1, x2, y2, aid))
    return out


def _merge_collinear_segments(segments, tol=1e-4):
    if len(segments) < 2:
        return segments

    def _same_pt(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1]) <= tol

    def _colinear(seg_a, seg_b):
        x1, y1, x2, y2 = seg_a[0], seg_a[1], seg_a[2], seg_a[3]
        x3, y3, x4, y4 = seg_b[0], seg_b[1], seg_b[2], seg_b[3]
        dx, dy = x2 - x1, y2 - y1
        ln = math.hypot(dx, dy)
        if ln < tol:
            return False
        dx2, dy2 = x4 - x3, y4 - y3
        ln2 = math.hypot(dx2, dy2)
        if ln2 < tol:
            return False
        cross = abs((dx / ln) * (dy2 / ln2) - (dy / ln) * (dx2 / ln2))
        if cross > 1e-3:
            return False
        dist = abs((y2 - y1) * x3 - (x2 - x1) * y3 + x2 * y1 - y2 * x1)
        return dist <= max(tol, ln * 1e-4)

    merged = list(segments)
    changed = True
    while changed:
        changed = False
        nxt = []
        used = [False] * len(merged)
        for i, seg in enumerate(merged):
            if used[i]:
                continue
            chain = [(seg[0], seg[1]), (seg[2], seg[3])]
            aid = seg[4] if len(seg) > 4 else None
            used[i] = True
            extended = True
            while extended:
                extended = False
                for j, seg2 in enumerate(merged):
                    if used[j] or not _colinear(seg, seg2):
                        continue
                    a = (seg2[0], seg2[1])
                    b = (seg2[2], seg2[3])
                    if _same_pt(chain[-1], a):
                        chain.append(b)
                        used[j] = True
                        extended = True
                    elif _same_pt(chain[-1], b):
                        chain.append(a)
                        used[j] = True
                        extended = True
                    elif _same_pt(chain[0], b):
                        chain.insert(0, a)
                        used[j] = True
                        extended = True
                    elif _same_pt(chain[0], a):
                        chain.insert(0, b)
                        used[j] = True
                        extended = True
            nxt.append((chain[0][0], chain[0][1], chain[-1][0], chain[-1][1], aid))
        if len(nxt) < len(merged):
            changed = True
        merged = nxt
    return merged


def build_snap_context(entities, render_all_layers: bool):
    """Snap OSNAP desde entidades DXF nativas (sin facetar)."""
    import numpy as np

    from interface.qt.cad_snap import SnapContext

    ctx = SnapContext()
    raw_segments = []
    vertices = []

    for entity in entities:
        layer = str(entity.dxf.layer).upper()
        if not capa_relevante_visual(layer, render_all_layers):
            continue
        typ = entity.dxftype()

        if typ == "LINE":
            try:
                x1, y1 = float(entity.dxf.start.x), float(entity.dxf.start.y)
                x2, y2 = float(entity.dxf.end.x), float(entity.dxf.end.y)
                raw_segments.append((x1, y1, x2, y2, None))
                vertices.extend([(x1, y1), (x2, y2)])
            except Exception:
                pass
            continue

        if typ == "CIRCLE":
            try:
                cx, cy = float(entity.dxf.center.x), float(entity.dxf.center.y)
                r = float(entity.dxf.radius)
                if r <= 0:
                    continue
                tag = (
                    "inner"
                    if es_inner_layer(layer)
                    else ("mark" if es_mark_layer(layer) else "outer")
                )
                ctx.circulos_snap.append((cx, cy, r, tag))
            except Exception:
                pass
            continue

        if typ == "ARC":
            try:
                cx, cy = float(entity.dxf.center.x), float(entity.dxf.center.y)
                r = float(entity.dxf.radius)
                sa = float(entity.dxf.start_angle)
                ea = float(entity.dxf.end_angle)
                if r <= 0:
                    continue
                ctx.arcos_pick.append({"cx": cx, "cy": cy, "r": r})
                aid = len(ctx.arcos_pick) - 1
                tessellate_arco_ccw(cx, cy, r, sa, ea, aid, raw_segments)
            except Exception:
                pass
            continue

        if typ in ("LWPOLYLINE", "POLYLINE"):
            registrar_arcos_bulge(
                entity, layer, 0.0, 0.0, 0, render_all_layers, ctx.arcos_pick, raw_segments
            )
            verts = []
            try:
                if typ == "LWPOLYLINE":
                    for item in entity.get_points("xyb"):
                        if len(item) >= 3:
                            verts.append(
                                (float(item[0]), float(item[1]), float(item[2] or 0.0))
                            )
                        else:
                            verts.append((float(item[0]), float(item[1]), 0.0))
                else:
                    for v in entity.vertices:
                        p = v.dxf.location
                        b = float(getattr(v.dxf, "bulge", 0.0) or 0.0)
                        verts.append((float(p.x), float(p.y), b))
            except Exception:
                continue
            n = len(verts)
            if n < 2:
                continue
            closed = False
            try:
                if typ == "LWPOLYLINE":
                    closed = bool(entity.closed)
                else:
                    closed = bool(getattr(entity, "is_closed", False))
            except Exception:
                closed = False
            nseg = n if closed else n - 1
            for i in range(nseg):
                x1, y1, b = verts[i]
                x2, y2, _ = verts[(i + 1) % n]
                if abs(b) >= 1e-12:
                    continue
                raw_segments.append((x1, y1, x2, y2, None))
                vertices.append((x1, y1))
            if not closed and n > 0:
                vertices.append((float(verts[-1][0]), float(verts[-1][1])))

    raw_segments = _merge_collinear_segments(_dedupe_segments(raw_segments))
    ctx.geom_segmentos = raw_segments
    uniq = {}
    for pt in vertices:
        uniq[(round(pt[0], 5), round(pt[1], 5))] = pt
    ctx.vertices = np.array(list(uniq.values())) if uniq else np.zeros((0, 2))
    return ctx
