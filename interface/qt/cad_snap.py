"""Snap OSNAP para cotas en visor CAD de piezas."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from interface.qt.dxf_part_geometry import clasificar_snap_arista


@dataclass
class SnapContext:
    vertices: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    circulos_snap: list = field(default_factory=list)
    geom_segmentos: list = field(default_factory=list)
    arcos_pick: list = field(default_factory=list)


def snap_cota(x, y, span: float, ctx: SnapContext) -> dict:
    if x is None or y is None:
        return {"tipo": "libre", "pt": (0.0, 0.0), "snap_kind": None}
    tol_seg = span * 0.032
    tol_vert = span * 0.05
    cand = []
    for item in ctx.circulos_snap:
        cx, cy, r, tag = item[0], item[1], item[2], item[3]
        dc = math.hypot(x - cx, y - cy)
        d_rim = abs(dc - r)
        if dc < 1e-9:
            rim = (cx + r, cy)
        else:
            s = r / dc
            rim = (cx + (x - cx) * s, cy + (y - cy) * s)
        cand.append(
            (
                d_rim,
                {
                    "tipo": "circulo",
                    "pt": rim,
                    "cx": cx,
                    "cy": cy,
                    "r": r,
                    "tag": tag,
                    "snap_kind": "rim",
                },
            )
        )
    for seg in ctx.geom_segmentos:
        if len(seg) == 5:
            x1, y1, x2, y2, aid = seg
        else:
            x1, y1, x2, y2 = seg[0], seg[1], seg[2], seg[3]
            aid = None
        cl = clasificar_snap_arista(x, y, x1, y1, x2, y2, span)
        if cl is None:
            continue
        d_best, pt_snap, kind = cl
        arc_seg = aid is not None
        base = {
            "pt": pt_snap,
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "snap_kind": kind,
            "arc_seg": arc_seg,
        }
        if arc_seg and 0 <= int(aid) < len(ctx.arcos_pick):
            ag = ctx.arcos_pick[int(aid)]
            info = {
                "tipo": "arco",
                "cx": ag["cx"],
                "cy": ag["cy"],
                "r": ag["r"],
                **base,
            }
        else:
            info = {"tipo": "arista", **base}
        cand.append((d_best, info))
    if len(ctx.vertices) > 0:
        d = np.sqrt(np.sum((ctx.vertices - np.array([x, y])) ** 2, axis=1))
        idx = int(np.argmin(d))
        if d[idx] < tol_vert:
            p = tuple(ctx.vertices[idx])
            cand.append(
                (float(d[idx]), {"tipo": "vertice", "pt": p, "snap_kind": "vertice"})
            )
    if not cand:
        return {"tipo": "libre", "pt": (float(x), float(y)), "snap_kind": None}
    cand.sort(key=lambda t: t[0])
    first_circ = next((c for c in cand if c[1].get("tipo") == "circulo"), None)
    first_arc = next((c for c in cand if c[1].get("tipo") == "arco"), None)
    best_d, best = cand[0]

    curve_pick = None
    if first_arc is not None and first_arc[0] <= tol_seg:
        curve_pick = first_arc
    if first_circ is not None and first_circ[0] <= tol_seg:
        if curve_pick is None or first_circ[0] <= curve_pick[0]:
            curve_pick = first_circ
    if curve_pick is not None:
        d_curve, info_curve = curve_pick
        if best.get("tipo") in ("arista", "vertice"):
            if d_curve <= max(best_d * 1.45, tol_seg * 0.20):
                best_d, best = d_curve, info_curve
        elif best.get("tipo") in ("arco", "circulo"):
            if d_curve < best_d:
                best_d, best = d_curve, info_curve
    if best["tipo"] in ("circulo", "arco", "arista"):
        if best_d > tol_seg:
            return {"tipo": "libre", "pt": (float(x), float(y)), "snap_kind": None}
    elif best["tipo"] == "vertice":
        if best_d > tol_vert:
            return {"tipo": "libre", "pt": (float(x), float(y)), "snap_kind": None}
    if best.get("tipo") in ("arista", "vertice") and first_arc is not None:
        d_arc, info_arc = first_arc
        if d_arc <= tol_seg and d_arc <= max(best_d * 1.2, tol_seg * 0.85):
            return info_arc
    return best
