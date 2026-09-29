"""Une LINE/ARC consecutivos en LWPOLYLINE (con bulge) por capa.

- Marcaje (MARK, RTZ_LABEL): toda cadena, abierta o cerrada, pasa a polilínea.
- Corte (CUT_OUTER, CUT_INNER): solo contornos que cierran; una cadena abierta
  se deja en LINE/ARC para que ningún lector pierda un corte.
La geometría no cambia (mismos vértices y arcos exactos vía bulge).
"""
from __future__ import annotations

import collections
import math

MARK_JOIN_LAYERS = ("MARK", "RTZ_LABEL")
CUT_JOIN_LAYERS = ("CUT_OUTER", "CUT_INNER")
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
        return None
    p0 = (c.x + r * math.cos(a0), c.y + r * math.sin(a0))
    p1 = (c.x + r * math.cos(a1), c.y + r * math.sin(a1))
    return p0, p1, math.tan(sweep / 4)


def _cadenas(segs, tol):
    """Encadena segmentos por extremos. Devuelve [(indices, verts, cerrada)].

    Mismo orden que `stitch_open_contours` del lector LS-READY (arranca en la
    primera entidad y agrega siempre la de menor índice que toque cualquiera de
    los dos extremos): la polilínea conserva el punto de inicio y el sentido
    con que el robot cortaba las LINE/ARC sueltas.
    """
    adj = collections.defaultdict(list)
    for i, s in enumerate(segs):
        if s is None:
            continue
        adj[_key(s[0], tol)].append(i)
        adj[_key(s[1], tol)].append(i)
    used = [s is None for s in segs]
    out = []
    for i, s in enumerate(segs):
        if used[i]:
            continue
        used[i] = True
        a, b, bu = s
        idx = [i]
        verts = [[a[0], a[1], bu], [b[0], b[1], 0.0]]
        while True:
            k0, k1 = _key(verts[0], tol), _key(verts[-1], tol)
            if len(verts) > 2 and k0 == k1:
                break
            cand = [j for j in adj[k1] + adj[k0] if not used[j]]
            if not cand:
                break
            j = min(cand)
            used[j] = True
            idx.append(j)
            p, q, bj = segs[j]
            kp, kq = _key(p, tol), _key(q, tol)
            if kp == k1:
                verts[-1][2] = bj
                verts.append([q[0], q[1], 0.0])
            elif kq == k1:
                verts[-1][2] = -bj
                verts.append([p[0], p[1], 0.0])
            elif kq == k0:
                verts.insert(0, [p[0], p[1], bj])
            else:
                verts.insert(0, [q[0], q[1], -bj])
        cerrada = len(verts) > 2 and _key(verts[0], tol) == _key(verts[-1], tol)
        if cerrada:
            verts.pop()
        out.append((idx, verts, cerrada))
    return out


def join_layer(
    msp, layer: str, *, tol: float = JOIN_TOL_MM, solo_cerrados: bool = False
) -> tuple[int, int]:
    """Reemplaza LINE/ARC de `layer` por polilíneas. Devuelve (n_entidades, n_resultantes)."""
    ents = [
        e
        for e in msp.query("LINE ARC")
        if str(e.dxf.layer or "").upper() == layer.upper()
    ]
    if len(ents) < 2:
        return len(ents), len(ents)
    segs = []
    borrar = [False] * len(ents)
    for i, e in enumerate(ents):
        s = _segmento(e)
        if s is not None and e.dxftype() == "LINE" and _key(s[0], tol) == _key(s[1], tol):
            s = None
            borrar[i] = True
        segs.append(s)
    n_out = 0
    for idx, verts, cerrada in _cadenas(segs, tol):
        if solo_cerrados and (not cerrada or len(idx) < 2):
            n_out += len(idx)
            continue
        e0 = ents[idx[0]]
        attrs = {"layer": e0.dxf.layer}
        if e0.dxf.hasattr("color"):
            attrs["color"] = e0.dxf.color
        msp.add_lwpolyline(
            [tuple(v) for v in verts], format="xyb", close=cerrada, dxfattribs=attrs
        )
        n_out += 1
        for i in idx:
            borrar[i] = True
    for i, e in enumerate(ents):
        if borrar[i]:
            msp.delete_entity(e)
    return len(ents), n_out


def join_mark_layers(msp, layers=MARK_JOIN_LAYERS) -> dict[str, tuple[int, int]]:
    out = {}
    for layer in layers:
        try:
            out[layer] = join_layer(msp, layer)
        except Exception as exc:
            print(f"[DXF][JOIN][WARN] {layer}: {exc}")
    return out


def join_cut_layers(msp, layers=CUT_JOIN_LAYERS) -> dict[str, tuple[int, int]]:
    out = {}
    for layer in layers:
        try:
            out[layer] = join_layer(msp, layer, solo_cerrados=True)
        except Exception as exc:
            print(f"[DXF][JOIN][WARN] {layer}: {exc}")
    return out
