"""Diagnostica cavidades VFM vs HFM y qué piezas quedan fuera tras pack."""
from __future__ import annotations

import os
import sys
from collections import Counter

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
        out.append(
            {
                "g": g,
                "area_in2": g.area / IN2,
                "w": (maxx - minx) / IN,
                "h": (maxy - miny) / IN,
            }
        )
    return sorted(out, key=lambda d: -d["area_in2"])


def main() -> int:
    for item in ("GENE-VFM-20-101", "GENE-HFM-10-102"):
        piezas, _ = build_pieces_from_entries(
            [SimPieceEntry(ruta=find(item), qty=1, nombre=item)]
        )
        poly = piezas[0]["poly"]
        cavs = open_cavs(poly)
        print(f"{item}: aabb={box(*poly.bounds).area/IN2:.0f} metal={poly.area/IN2:.0f} cavs={len(cavs)}")
        for i, c in enumerate(cavs):
            print(f"  cav[{i}] {c['w']:.2f}x{c['h']:.2f} area={c['area_in2']:.1f}")

    entries = [SimPieceEntry(ruta=find(i), qty=q, nombre=i) for i, q in P4]
    piezas, _ = build_pieces_from_entries(entries)
    tl = run_plate_sim(
        piezas,
        w_mm=inches_to_mm(120),
        h_mm=inches_to_mm(48),
        kerf_in=0.1,
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
        if "VFM" in nom or "HFM" in nom:
            hosts.append((nom, poly))
        else:
            smalls.append((nom, poly))

    packed_cavs = []
    for nom, hp in hosts:
        cs = open_cavs(hp)
        print(f"packed host {nom}: cavs={len(cs)} areas={[round(c['area_in2'],1) for c in cs]}")
        packed_cavs.extend(cs)

    in_c = Counter()
    out_c = Counter()
    for nom, sp in smalls:
        if any(c["g"].contains(sp.centroid) for c in packed_cavs):
            in_c[nom] += 1
        else:
            out_c[nom] += 1
    print("IN CAV:", dict(in_c), "total", sum(in_c.values()))
    print("OUT   :", dict(out_c), "total", sum(out_c.values()))

    # Physical fit check GS vs channel heights with kerf 0.1
    print("Fit kerf=0.1: channel 3.74 needs piece_h+0.1<=3.74 => h<=3.64")
    print("  GS 3.61 ->", 3.61 + 0.1 <= 3.74, "(thin)")
    print("  BKT369 2.57 ->", 2.57 + 0.1 <= 3.74)
    print("  BKT271 3.25 ->", 3.25 + 0.1 <= 3.74)
    print("  BKT320 4.49 ->", 4.49 + 0.1 <= 3.74, "(needs tall)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
