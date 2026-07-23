"""Baseline + diagnóstico por-host: piezas en cada cavidad VFM (sin degradar)."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from shapely.geometry import Polygon, box

from modules.nesting_engine.sim_lab import (
    SimPieceEntry,
    build_pieces_from_entries,
    inches_to_mm,
    run_plate_sim,
)

DXF_DIR = (
    r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals"
    r"\ARGA METALS CORPORATE SYSTEM\TANKS\GIGA\GIGA FLUIDSTACK"
    r"\MODEL CORE FILES\AutoDXF\Processed Files"
)
OUT = os.path.join(_ROOT, "_logs", "cavity_baseline_p4.json")
IN = 25.4
IN2 = IN * IN

P4 = [
    ("GENE-VFM-20-101", 3),
    ("GENE-HFM-10-102", 4),
    ("GENE-BKT-295", 1),
    ("GENE-BKT-320", 2),
    ("GENE-BKT-270", 1),
    ("GENE-BKT-271", 6),
    ("GENE-GS-0820-708", 41),
    ("GENE-BKT-369", 18),
]


def find(item: str) -> str:
    for n in os.listdir(DXF_DIR):
        if n.upper().startswith(item.upper()) and n.lower().endswith(".dxf"):
            return os.path.join(DXF_DIR, n)
    raise FileNotFoundError(item)


def open_cavs(host: Polygon, min_in2: float = 5.0):
    free = box(*host.bounds).difference(host)
    geoms = [free] if free.geom_type == "Polygon" else list(getattr(free, "geoms", []))
    out = []
    for g in geoms:
        if g.area / IN2 < min_in2:
            continue
        minx, miny, maxx, maxy = g.bounds
        w, h = (maxx - minx) / IN, (maxy - miny) / IN
        eff = (g.area / IN2) / max(w, h, 1e-6)  # grosor medio aproximado
        out.append({"g": g, "area": g.area / IN2, "w": w, "h": h, "eff_thick": eff})
    return sorted(out, key=lambda d: -d["area"])


def analyze(kerf: float) -> dict:
    entries = [SimPieceEntry(ruta=find(i), qty=q, nombre=i) for i, q in P4]
    piezas, _ = build_pieces_from_entries(entries)
    tl = run_plate_sim(
        piezas,
        w_mm=inches_to_mm(120),
        h_mm=inches_to_mm(48),
        kerf_in=kerf,
        margin_in=0.15,
        engine_id="arga_base",
        isolate_process=False,
    )
    placed = list((tl.hoja or {}).get("piezas") or [])
    hosts = []
    smalls = []
    for p in placed:
        nom = str(p.get("nombre") or "").split("#")[0]
        rings = p.get("poligonos") or []
        poly = Polygon(rings[0], rings[1:] if len(rings) > 1 else None)
        cy = poly.centroid.y
        if "VFM" in nom or "HFM" in nom:
            hosts.append({"nom": nom, "poly": poly, "cy": cy})
        else:
            smalls.append({"nom": nom, "poly": poly, "c": poly.centroid})

    hosts.sort(key=lambda h: h["cy"])  # bottom → top
    per_host = []
    in_total = 0
    out_c = Counter()
    assigned = set()
    for hi, h in enumerate(hosts):
        cavs = open_cavs(h["poly"])
        counts = Counter()
        n = 0
        cav_fill = []
        for ci, cav in enumerate(cavs):
            nin = 0
            for si, s in enumerate(smalls):
                if si in assigned:
                    continue
                if cav["g"].contains(s["c"]):
                    assigned.add(si)
                    counts[s["nom"]] += 1
                    nin += 1
                    n += 1
            cav_fill.append(
                {
                    "i": ci,
                    "w": round(cav["w"], 2),
                    "h": round(cav["h"], 2),
                    "area": round(cav["area"], 1),
                    "eff_thick": round(cav["eff_thick"], 2),
                    "n_pieces": nin,
                }
            )
        in_total += n
        per_host.append(
            {
                "idx": hi,
                "nom": h["nom"],
                "cy_in": round(h["cy"] / IN, 2),
                "n_in": n,
                "by_type": dict(counts),
                "cavs": cav_fill,
            }
        )

    for si, s in enumerate(smalls):
        if si not in assigned:
            out_c[s["nom"]] += 1

    # ¿Entre VFMs (gap) vs dentro de canal?
    vfm = [h for h in hosts if "VFM" in h["nom"]]
    between = 0
    if len(vfm) >= 2:
        for si, s in enumerate(smalls):
            if si not in assigned:
                continue
            # already in cavity — skip
        # recount: pieces whose centroid is in bbox strip between two VFMs but not in any open cav
        for si, s in enumerate(smalls):
            y = s["c"].y
            for a, b in zip(vfm, vfm[1:]):
                ya0, ya1 = a["poly"].bounds[1], a["poly"].bounds[3]
                yb0, yb1 = b["poly"].bounds[1], b["poly"].bounds[3]
                gap0, gap1 = min(ya1, yb1), max(ya0, yb0)
                # if stacked, gap between max of lower and min of upper
                lo = min(a, b, key=lambda h: h["cy"])
                hi = max(a, b, key=lambda h: h["cy"])
                gap_lo = lo["poly"].bounds[3]
                gap_hi = hi["poly"].bounds[1]
                if gap_hi > gap_lo and gap_lo <= y <= gap_hi:
                    # not inside either host metal
                    if not lo["poly"].contains(s["c"]) and not hi["poly"].contains(s["c"]):
                        # and not in open cav of either
                        in_cav = False
                        for h in (lo, hi):
                            for c in open_cavs(h["poly"]):
                                if c["g"].contains(s["c"]):
                                    in_cav = True
                                    break
                        if not in_cav:
                            between += 1

    efi = float((tl.hoja or {}).get("area_usada") or 0) / (tl.w_mm * tl.h_mm) * 100
    return {
        "kerf": kerf,
        "efi": round(efi, 2),
        "n_small": len(smalls),
        "in_cav_total": in_total,
        "out_total": sum(out_c.values()),
        "out_by_type": dict(out_c),
        "per_host": per_host,
        "floor_ok": in_total >= 66 if kerf <= 0.11 else in_total >= 48,
    }


def main() -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    results = {}
    for kerf in (0.1, 0.3):
        r = analyze(kerf)
        results[str(kerf)] = r
        print("=" * 72)
        print(f"kerf={kerf} in_cav={r['in_cav_total']}/{r['n_small']} out={r['out_total']} efi={r['efi']}%")
        print("OUT", r["out_by_type"])
        for h in r["per_host"]:
            if "VFM" not in h["nom"] and "HFM" not in h["nom"]:
                continue
            print(
                f"  host[{h['idx']}] {h['nom']} y={h['cy_in']}\" n_in={h['n_in']} {h['by_type']}"
            )
            for c in h["cavs"]:
                print(
                    f"    cav[{c['i']}] {c['w']}x{c['h']} eff~{c['eff_thick']}\" "
                    f"area={c['area']} pieces={c['n_pieces']}"
                )
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print("Wrote", OUT)
    # Floor: kerf 0.1 must stay >= 66 and cada VFM con cavidades >= 12 piezas
    r01 = results["0.1"]
    if r01["in_cav_total"] < 66:
        print("FAIL FLOOR: kerf0.1 in_cav < 66")
        return 2
    vfm_counts = [h["n_in"] for h in r01["per_host"] if "VFM" in h["nom"]]
    if vfm_counts and min(vfm_counts) < 12:
        print("FAIL BALANCE: min VFM fill", min(vfm_counts), "< 12", vfm_counts)
        return 3
    print("FLOOR OK: kerf0.1 in_cav >= 66; VFM fills", vfm_counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
