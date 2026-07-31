"""Por que sobran BKT en H34 si hay patio libre? Diagnostico paso a paso."""
from __future__ import annotations

import gzip
import json
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from shapely.geometry import Polygon, box

from modules.nesting_engine.sim_lab import piezas_pack_desde_hoja, run_plate_sim

ARGANEST = (
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\NANS"
    r"\GIGA FLUIDSTACK.arganest"
)
MAT = "0.1196_A 36 GALV"
IN = 25.4


def base(n):
    return str(n or "").split("#")[0]


def poly(p):
    rings = p.get("poligonos") or []
    if not rings:
        return None
    g = Polygon(rings[0], rings[1:] if len(rings) > 1 else None)
    return None if g.is_empty else g


def free_area_in2(piezas, w, h, margin_in=0.15):
    sheet = box(margin_in * IN, margin_in * IN, w - margin_in * IN, h - margin_in * IN)
    solids = []
    for p in piezas:
        g = poly(p)
        if g is not None:
            solids.append(g)
    if not solids:
        return sheet.area / (IN * IN)
    from shapely.ops import unary_union

    occ = unary_union(solids)
    free = sheet.difference(occ)
    return free.area / (IN * IN)


def main():
    payload = json.loads(gzip.decompress(open(ARGANEST, "rb").read()).decode("utf-8"))
    h = payload["resultados_multilote"][0]["data"][MAT]["hojas"][3]
    pool = piezas_pack_desde_hoja(h)
    w = float(h["placa_w"])
    hh = float(h["placa_h"])

    # Solo estructurales
    est = [p for p in pool if "VFM" in base(p.get("nombre")) or "HFM" in base(p.get("nombre"))]
    peq = [p for p in pool if p not in est and base(p.get("nombre")) not in
           {base(x.get("nombre")) for x in est}]
    # rebuild peq properly
    peq = [p for p in pool if "VFM" not in base(p.get("nombre")) and "HFM" not in base(p.get("nombre"))]
    print(f"est={len(est)} peq={len(peq)}", flush=True)

    tl_e = run_plate_sim(est, w_mm=w, h_mm=hh, kerf_in=0.3, margin_in=0.15,
                         engine_id="arga_force", isolate_process=False)
    placed_e = list((tl_e.hoja or {}).get("piezas") or [])
    print(f"estructurales placed={len(placed_e)} restos={len(tl_e.restos or [])} "
          f"eff={tl_e.hoja.get('eficiencia'):.1f} free~={free_area_in2(placed_e,w,hh):.0f} in2", flush=True)

    # Full pack
    tl = run_plate_sim(pool, w_mm=w, h_mm=hh, kerf_in=0.3, margin_in=0.15,
                       engine_id="arga_force", isolate_process=False)
    placed = list((tl.hoja or {}).get("piezas") or [])
    restos = list(tl.restos or [])
    print(f"FULL placed={len(placed)} restos={len(restos)} eff={tl.hoja.get('eficiencia'):.1f} "
          f"free~={free_area_in2(placed,w,hh):.0f} in2", flush=True)
    print("restos", dict(Counter(base(p.get("nombre")) for p in restos)))

    # Solo peq en placa vacia
    tl_p = run_plate_sim(peq, w_mm=w, h_mm=hh, kerf_in=0.3, margin_in=0.15,
                         engine_id="arga_force", isolate_process=False)
    print(f"ONLY_BKT placed={len(tl_p.hoja.get('piezas') or [])} restos={len(tl_p.restos or [])} "
          f"eff={tl_p.hoja.get('eficiencia'):.1f}", flush=True)

    # Empacar peq DESPUES usando pool = est_placed + peq? no - simula juntos. Try sequential:
    # No API for continue. Instead re-run full.

    # Max inscribed free regions after estructurales
    from shapely.ops import unary_union
    sheet = box(0.15 * IN, 0.15 * IN, w - 0.15 * IN, hh - 0.15 * IN)
    solids = [poly(p) for p in placed_e]
    solids = [g for g in solids if g]
    free = sheet.difference(unary_union(solids))
    geoms = [free] if free.geom_type == "Polygon" else list(free.geoms)
    geoms = sorted(geoms, key=lambda g: -g.area)
    print(f"free regions after structural: {len(geoms)}")
    for i, g in enumerate(geoms[:8]):
        minx, miny, maxx, maxy = g.bounds
        print(f"  reg[{i}] {((maxx-minx)/IN):.1f}x{((maxy-miny)/IN):.1f}\" area={g.area/(IN*IN):.0f} in2")

    # Can a 7.08x4.20 rectangle fit in any free region with kerf shrink?
    kerf = 0.3 * IN
    need_w, need_h = 7.08 * IN + kerf, 4.20 * IN + kerf
    for i, g in enumerate(geoms[:12]):
        sh = g.buffer(-kerf, join_style=2)
        if sh.is_empty:
            print(f"  reg[{i}] shrink empty")
            continue
        parts = [sh] if sh.geom_type == "Polygon" else list(sh.geoms)
        ok = False
        for p in parts:
            minx, miny, maxx, maxy = p.bounds
            pw, ph = maxx - minx, maxy - miny
            if (pw >= need_w and ph >= need_h) or (pw >= need_h and ph >= need_w):
                ok = True
                break
        print(f"  reg[{i}] BKT-304 AABB-fit-kerf={ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
