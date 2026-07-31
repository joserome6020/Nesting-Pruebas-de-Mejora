"""Re-nest hojas VFM de GIGA FLUIDSTACK (0.1196 GALV) con motor kerf-correcto."""
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
OUT = os.path.join(_ROOT, "_logs", "giga_vfm_renest.json")
IN = 25.4
IN2 = IN * IN
MAT = "0.1196_A 36 GALV"
KERF = 0.3
MARGIN = 0.15


def load():
    return json.loads(gzip.decompress(open(ARGANEST, "rb").read()).decode("utf-8"))


def base(n):
    return str(n or "").split("#")[0]


def poly_of(p):
    rings = p.get("poligonos") or []
    if not rings:
        return None
    try:
        g = Polygon(rings[0], rings[1:] if len(rings) > 1 else None)
        return None if g.is_empty else g
    except Exception:
        return None


def open_cavs(host, min_in2=5.0):
    free = box(*host.bounds).difference(host)
    geoms = [free] if free.geom_type == "Polygon" else list(getattr(free, "geoms", []) or [])
    out = []
    for g in geoms:
        if g.is_empty or g.area / IN2 < min_in2:
            continue
        if g.area > host.envelope.area * 0.85:
            continue
        minx, miny, maxx, maxy = g.bounds
        out.append(
            {
                "g": g,
                "area": g.area / IN2,
                "w": (maxx - minx) / IN,
                "h": (maxy - miny) / IN,
            }
        )
    return sorted(out, key=lambda d: -d["area"])


def cavity_stats(piezas):
    hosts, smalls = [], []
    for p in piezas:
        g = poly_of(p)
        if g is None:
            continue
        n = base(p.get("nombre"))
        if "VFM" in n or "HFM" in n:
            hosts.append({"n": n, "g": g})
        else:
            smalls.append({"n": n, "c": g.centroid})
    per, assigned, total = [], set(), 0
    for h in hosts:
        cavs = open_cavs(h["g"])
        kinds = Counter()
        nfill = 0
        per_cav = []
        for ci, cav in enumerate(cavs):
            nin = 0
            for si, s in enumerate(smalls):
                if si in assigned:
                    continue
                if cav["g"].contains(s["c"]):
                    assigned.add(si)
                    kinds[s["n"]] += 1
                    nin += 1
                    nfill += 1
            per_cav.append({"i": ci, "w": round(cav["w"], 2), "h": round(cav["h"], 2), "n": nin})
        total += nfill
        per.append({"host": h["n"], "filled": nfill, "kinds": dict(kinds), "cavs": per_cav})
    return total, per


def gap_ok(piezas, kerf):
    polys = []
    for p in piezas:
        g = poly_of(p)
        if g is not None:
            polys.append(g)
    min_g = 1e9
    viol = 0
    req = kerf * 0.92 * IN
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            gap = polys[i].distance(polys[j])
            min_g = min(min_g, gap)
            if gap + 1e-6 < req:
                viol += 1
    return (None if min_g > 1e8 else round(min_g / IN, 4)), viol


def main():
    payload = load()
    block = payload["resultados_multilote"][0]["data"][MAT]
    hojas = block.get("hojas") or []
    report = {"mat": MAT, "sheets": []}

    # Index VFM sheets
    targets = []
    for i, h in enumerate(hojas):
        mix = Counter(base(p.get("nombre")) for p in (h.get("piezas") or []))
        if any("VFM" in k for k in mix):
            cav0, per0 = cavity_stats(h.get("piezas") or [])
            targets.append((i, h, mix, cav0, per0))

    print(f"{MAT}: {len(targets)} hojas con VFM / {len(hojas)} total")
    for i, h, mix, cav0, per0 in targets:
        g0, v0 = gap_ok(h.get("piezas") or [], KERF)
        eff = h.get("eficiencia_real") or h.get("eficiencia")
        print(
            f"\n=== [{i}] {h.get('sheet_code')} n={len(h.get('piezas') or [])} "
            f"eff={eff:.1f} cav={cav0} min_gap={g0} viol={v0}"
        )
        print(f"  mix={dict(mix)}")
        for ph in per0:
            if "VFM" in ph["host"]:
                print(f"  {ph['host']}: filled={ph['filled']} kinds={ph['kinds']} cavs={ph['cavs']}")

        pool = piezas_pack_desde_hoja(h)
        srcs = Counter(str(p.get("_lab_geom_src", "?")) for p in pool)
        print(f"  pack_pool={len(pool)} geom_src={dict(srcs)}", flush=True)

        w = float(h.get("placa_w") or 120 * IN)
        hh = float(h.get("placa_h") or 48 * IN)
        tl = run_plate_sim(
            pool,
            w_mm=w,
            h_mm=hh,
            kerf_in=KERF,
            margin_in=MARGIN,
            engine_id="arga_base",
            isolate_process=False,
        )
        placed = list((tl.hoja or {}).get("piezas") or [])
        restos = list(tl.restos or [])
        cav1, per1 = cavity_stats(placed)
        g1, v1 = gap_ok(placed, KERF)
        eff1 = float((tl.hoja or {}).get("eficiencia") or 0)
        print(
            f"  AFTER: n={len(placed)} restos={len(restos)} eff={eff1:.1f} "
            f"cav={cav1} min_gap={g1} viol={v1}",
            flush=True,
        )
        for ph in per1:
            if "VFM" in ph["host"] or ph["filled"]:
                print(f"    {ph['host']}: filled={ph['filled']} kinds={ph['kinds']}")
        if restos:
            rc = Counter(base(p.get("nombre")) for p in restos)
            print(f"  RESTOS: {dict(rc)}")

        report["sheets"].append(
            {
                "i": i,
                "code": h.get("sheet_code"),
                "before": {
                    "n": len(h.get("piezas") or []),
                    "eff": eff,
                    "cav": cav0,
                    "gap": g0,
                    "viol": v0,
                    "mix": dict(mix),
                    "per_host": per0,
                },
                "after": {
                    "n": len(placed),
                    "restos": len(restos),
                    "eff": eff1,
                    "cav": cav1,
                    "gap": g1,
                    "viol": v1,
                    "per_host": per1,
                    "restos_mix": dict(Counter(base(p.get("nombre")) for p in restos)),
                },
            }
        )

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
