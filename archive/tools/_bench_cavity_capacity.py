"""Capacidad de cavidades abiertas VFM/HFM vs relleno actual (PLC152)."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from shapely.geometry import Polygon, box
from shapely.affinity import rotate, translate

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


def find(item: str) -> str | None:
    for n in os.listdir(DXF_DIR):
        if n.upper().startswith(item.upper()) and n.lower().endswith(".dxf"):
            return os.path.join(DXF_DIR, n)
    return None


def open_cavs(host: Polygon, min_in2: float = 5.0):
    free = box(*host.bounds).difference(host)
    geoms = [free] if free.geom_type == "Polygon" else list(getattr(free, "geoms", []))
    out = []
    for g in geoms:
        if g.area / IN2 < min_in2:
            continue
        minx, miny, maxx, maxy = g.bounds
        w, h = (maxx - minx) / IN, (maxy - miny) / IN
        out.append({"g": g, "area_in2": g.area / IN2, "w": w, "h": h})
    return sorted(out, key=lambda d: -d["area_in2"])


def fits_in_cav(piece: Polygon, cav: dict, kerf_in: float = 0.1) -> bool:
    """Rough fit: piece bbox (0/90) inside cavity bounds with kerf."""
    clearance = kerf_in
    cb = cav["g"].bounds
    cw = (cb[2] - cb[0]) / IN - clearance
    ch = (cb[3] - cb[1]) / IN - clearance
    minx, miny, maxx, maxy = piece.bounds
    pw, ph = (maxx - minx) / IN, (maxy - miny) / IN
    return (pw <= cw + 1e-6 and ph <= ch + 1e-6) or (ph <= cw + 1e-6 and pw <= ch + 1e-6)


def greedy_capacity(cavs, small_polys, kerf_in=0.1) -> int:
    """Cota inferior: cuántas piezas caben por área + fit AABB (greedy smallest first)."""
    remaining = sorted(small_polys, key=lambda p: p.area)
    filled = 0
    # Sort cavities narrow-first (prefer thin channels for small)
    cavs_work = sorted(cavs, key=lambda c: min(c["w"], c["h"]))
    # Area budget per cavity
    budgets = [c["area_in2"] * 0.85 for c in cavs_work]  # 85% packing dens.
    for p in remaining:
        placed = False
        for i, c in enumerate(cavs_work):
            if not fits_in_cav(p, c, kerf_in):
                continue
            need = p.area / IN2
            if budgets[i] >= need:
                budgets[i] -= need
                filled += 1
                placed = True
                break
        if not placed:
            continue
    return filled


def analyze_pack(label: str, tabla, kerf_in: float) -> dict:
    entries = []
    for item, qty in tabla:
        ruta = find(item)
        if not ruta:
            raise FileNotFoundError(item)
        entries.append(SimPieceEntry(ruta=ruta, qty=qty, nombre=item))
    piezas, _ = build_pieces_from_entries(entries)

    # Theoretical cavities from pool hosts (pre-pack geometry)
    host_polys = []
    small_polys = []
    for p in piezas:
        nom = str(p.get("nombre") or "")
        poly = p["poly"]
        if "VFM" in nom or "HFM" in nom:
            host_polys.append(poly)
        else:
            small_polys.append(poly)

    all_cavs = []
    for hp in host_polys:
        all_cavs.extend(open_cavs(hp))
    capa = greedy_capacity(all_cavs, small_polys, kerf_in)

    tl = run_plate_sim(
        piezas,
        w_mm=inches_to_mm(120),
        h_mm=inches_to_mm(48),
        kerf_in=kerf_in,
        margin_in=0.15,
        engine_id="arga_base",
        isolate_process=False,
    )
    hoja = tl.hoja or {}
    placed = list(hoja.get("piezas") or [])
    hosts = []
    smalls = []
    for p in placed:
        nom = str(p.get("nombre") or "").split("#")[0]
        rings = p.get("poligonos") or []
        if not rings:
            continue
        poly = Polygon(rings[0], rings[1:] if len(rings) > 1 else None)
        if "VFM" in nom or "HFM" in nom:
            hosts.append(poly)
        else:
            smalls.append((nom, poly))

    packed_cavs = []
    for hp in hosts:
        packed_cavs.extend(open_cavs(hp))

    in_cav = 0
    for _, sp in smalls:
        if any(c["g"].contains(sp.centroid) for c in packed_cavs):
            in_cav += 1

    out = {
        "label": label,
        "n_small": len(smalls),
        "n_cavs": len(packed_cavs),
        "cav_area_in2": sum(c["area_in2"] for c in packed_cavs),
        "small_area_in2": sum(sp.area for _, sp in smalls) / IN2,
        "capacity_greedy": capa,
        "in_cav": in_cav,
        "fill_pct": 100.0 * in_cav / max(1, len(smalls)),
        "cap_pct": 100.0 * in_cav / max(1, capa),
    }
    print(
        f"{label}: in_cav={in_cav}/{len(smalls)} ({out['fill_pct']:.0f}% smalls) "
        f"vs capa~{capa} ({out['cap_pct']:.0f}% of capa) | "
        f"cav_area={out['cav_area_in2']:.0f}in2 small_area={out['small_area_in2']:.0f}in2 "
        f"cavs={len(packed_cavs)}"
    )
    for i, c in enumerate(packed_cavs[:12]):
        print(f"  cav[{i}] {c['w']:.1f}\"x{c['h']:.1f}\" area={c['area_in2']:.0f}")
    return out


def main() -> int:
    print("=" * 72)
    print("PLC152-P4 cavity capacity vs actual (kerf=0.1)")
    print("=" * 72)
    analyze_pack("P4@0.1", P4, 0.1)
    print()
    print("PLC152-P4 cavity capacity vs actual (kerf=0.3)")
    analyze_pack("P4@0.3", P4, 0.3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
