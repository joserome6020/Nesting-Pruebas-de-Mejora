"""Une LINE/ARC consecutivos de capas de marcaje en LWPOLYLINE (con bulge).

Solo para capas que no son de corte (MARK, RTZ_LABEL): menos entidades, DXF más
ligero y trazos continuos para CAM. La geometría no cambia (mismos vértices y
arcos exactos vía bulge).
"""
from __future__ import annotations

import collections
import math

MARK_JOIN_LAYERS = ("MARK", "RTZ_LABEL")
JOIN_TOL_MM = 0.01


def _key(p, tol: float) -> tuple[int, int]:
    return (round(p[0] / tol), round(p[1] / tol))


def _segmento(e):
    if e.dxftype() == "LINE":
        s, t = e.dxf.start, e.dxf.end
        return (s.x, s.y), (t.x, t.y), 0.0
    c, r = e.dxf.center, float(e.dxf.radius)
    a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
    sweep = (a1 - a0) % (2 * math.pi)
    if sweep < 1e-9:
        sweep = 2 * math.pi
    p0 = (c.x + r * math.cos(a0), c.y + r * math.sin(a0))
    p1 = (c.x + r * math.cos(a1), c.y + r * math.sin(a1))
    return p0, p1, math.tan(sweep / 4)


def join_layer(msp, layer: str, *, tol: float = JOIN_TOL_MM) -> tuple[int, int]:
    """Reemplaza LINE/ARC de `layer` por polilíneas. Devuelve (n_entidades, n_polilineas)."""
    ents = [
        e
        for e in msp.query("LINE ARC")
        if str(e.dxf.layer or "").upper() == layer.upper()
    ]
    if len(ents) < 2:
        return len(ents), len(ents)
    segs = []
    for e in ents:
        a, b, bu = _segmento(e)
        if e.dxftype() == "LINE" and _key(a, tol) == _key(b, tol):
            segs.append(None)
            continue
        if abs(bu) > 1e6:
            return len(ents), len(ents)
        segs.append((a, b, bu))
    adj = collections.defaultdict(list)
    for i, s in enumerate(segs):
        if s is None:
            continue
        adj[_key(s[0], tol)].append(i)
        adj[_key(s[1], tol)].append(i)
    used = [s is None for s in segs]
    n_poly = 0
    for i, s in enumerate(segs):
        if used[i]:
            continue
        used[i] = True
        attrs = {"layer": ents[i].dxf.layer}
        if ents[i].dxf.hasattr("color"):
            attrs["color"] = ents[i].dxf.color
        a, b, bu = s
        verts = [[a[0], a[1], bu], [b[0], b[1], 0.0]]
        for forward in (True, False):
            while True:
                end = verts[-1] if forward else verts[0]
                if len(verts) > 2 and _key(verts[0], tol) == _key(verts[-1], tol):
                    break
                cand = [j for j in adj[_key(end, tol)] if not used[j]]
                if not cand:
                    break
                j = cand[0]
                used[j] = True
                p, q, bj = segs[j]
                if forward:
                    if _key(p, tol) == _key(end, tol):
                        verts[-1][2] = bj
                        verts.append([q[0], q[1], 0.0])
                    else:
                        verts[-1][2] = -bj
                        verts.append([p[0], p[1], 0.0])
                else:
                    if _key(q, tol) == _key(end, tol):
                        verts.insert(0, [p[0], p[1], bj])
                    else:
                        verts.insert(0, [q[0], q[1], -bj])
        closed = len(verts) > 2 and _key(verts[0], tol) == _key(verts[-1], tol)
        if closed:
            verts.pop()
        msp.add_lwpolyline(
            [tuple(v) for v in verts], format="xyb", close=closed, dxfattribs=attrs
        )
        n_poly += 1
    for e in ents:
        msp.delete_entity(e)
    return len(ents), n_poly


def join_mark_layers(msp, layers=MARK_JOIN_LAYERS) -> dict[str, tuple[int, int]]:
    out = {}
    for layer in layers:
        try:
            out[layer] = join_layer(msp, layer)
        except Exception as exc:
            print(f"[DXF][JOIN][WARN] {layer}: {exc}")
    return out
