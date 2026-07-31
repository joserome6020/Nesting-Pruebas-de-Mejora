"""Compara ACTUAL vs re-sim de una hoja tipo H34 (la de las capturas)."""
from __future__ import annotations

import gzip
import json
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from shapely.geometry import Polygon, box, LineString
from shapely.ops import unary_union

from modules.nesting_engine.sim_lab import piezas_pack_desde_hoja, run_plate_sim

ARGANEST = (
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\NANS"
    r"\GIGA FLUIDSTACK.arganest"
)
MAT = "0.1196_A 36 GALV"
IN = 25.4
KERF = 0.3
MARGIN = 0.15


def base(n):
    return str(n or "").split("#")[0]


def poly(p):
    rings = p.get("poligonos") or []
    if not rings:
        return None
    try:
        g = Polygon(rings[0], rings[1:] if len(rings) > 1 else None)
        return None if g.is_empty else g
    except Exception:
        return None


def vfm_gaps(piezas):
    vfms = []
    for p in piezas:
        if "VFM-20-101" not in base(p.get("nombre")):
            continue
        g = poly(p)
        if g is not None:
            vfms.append(g)
    vfms.sort(key=lambda g: g.bounds[1])  # bottom→top
    gaps = []
    for i in range(len(vfms) - 1):
        a, b = vfms[i], vfms[i + 1]
        # gap vertical between outer envelopes
        gap = b.bounds[1] - a.bounds[3]
        gaps.append(round(gap / IN, 3))
    return gaps, len(vfms)


def between_fill(piezas):
    """Piezas pequeñas cuyo centro está entre strips VFM (no dentro del metal)."""
    vfms = []
    smalls = []
    for p in piezas:
        g = poly(p)
        if g is None:
            continue
        n = base(p.get("nombre"))
        if "VFM-20-101" in n:
            vfms.append(g)
        elif "HFM" not in n:
            smalls.append((n, g))
    vfms.sort(key=lambda g: g.bounds[1])
    if len(vfms) < 2:
        return 0, Counter()
    # corridors = sheet bbox between consecutive VFM envelopes (AABB band)
    filled = Counter()
    n = 0
    for i in range(len(vfms) - 1):
        y0 = vfms[i].bounds[3]
        y1 = vfms[i + 1].bounds[1]
        if y1 <= y0 + 1:
            continue
        band = box(0, y0, 120 * IN, y1)
        for nom, g in smalls:
            c = g.centroid
            if band.contains(c):
                filled[nom] += 1
                n += 1
    return n, filled


def main():
    payload = json.loads(gzip.decompress(open(ARGANEST, "rb").read()).decode("utf-8"))
    hojas = payload["resultados_multilote"][0]["data"][MAT]["hojas"]
    # H34 = index 3 (W.O. 1-H34) matches mix 3 VFM + 8 HFM + BKTs
    h = hojas[3]
    print("sheet", h.get("sheet_code"), "eff", h.get("eficiencia_real"))
    act = h.get("piezas") or []
    mix = Counter(base(p.get("nombre")) for p in act)
    print("ACTUAL mix", dict(mix), "n=", len(act))
    gaps, nv = vfm_gaps(act)
    print("ACTUAL VFM gaps_in", gaps, "n_vfm", nv)
    nb, fb = between_fill(act)
    print("ACTUAL between_VFM", nb, dict(fb))

    pool = piezas_pack_desde_hoja(h)
    print("pool", len(pool), flush=True)
    w = float(h.get("placa_w"))
    hh = float(h.get("placa_h"))
    tl = run_plate_sim(
        pool, w_mm=w, h_mm=hh, kerf_in=KERF, margin_in=MARGIN,
        engine_id="arga_force", isolate_process=False,
    )
    sim = list((tl.hoja or {}).get("piezas") or [])
    restos = list(tl.restos or [])
    print(
        f"SIM n={len(sim)} restos={len(restos)} eff={tl.hoja.get('eficiencia'):.1f}",
        flush=True,
    )
    print("SIM mix", dict(Counter(base(p.get("nombre")) for p in sim)))
    print("RESTOS", dict(Counter(base(p.get("nombre")) for p in restos)))
    gaps2, nv2 = vfm_gaps(sim)
    print("SIM VFM gaps_in", gaps2, "n_vfm", nv2)
    nb2, fb2 = between_fill(sim)
    print("SIM between_VFM", nb2, dict(fb2))

    # Distancia mínima entre sólidos
    def min_gap(piezas):
        ps = [poly(p) for p in piezas]
        ps = [g for g in ps if g]
        mg = 1e9
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                mg = min(mg, ps[i].distance(ps[j]))
        return None if mg > 1e8 else round(mg / IN, 4)

    print("ACTUAL min_gap", min_gap(act), "SIM min_gap", min_gap(sim))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
