"""P3 cavity fill report with current ARGA Base."""
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
IN2 = 25.4 * 25.4

P3 = [
    ("GENE-VFM-20-101", 3),
    ("GENE-HFM-10-102", 4),
    ("GENE-BKT-295", 1),
    ("GENE-BKT-369", 3),
    ("GENE-GS-0820-708", 2),
    ("GENE-BKT-270", 1),
    ("GENE-BKT-304", 17),
    ("GENE-BKT-320", 10),
    ("GENE-BKT-321", 12),
]


def find(item: str) -> str:
    for n in os.listdir(DXF_DIR):
        if n.upper().startswith(item.upper()) and n.lower().endswith(".dxf"):
            return os.path.join(DXF_DIR, n)
    raise FileNotFoundError(item)


def main() -> int:
    entries = [SimPieceEntry(ruta=find(i), qty=q, nombre=i) for i, q in P3]
    piezas, _ = build_pieces_from_entries(entries)
    tl = run_plate_sim(
        piezas,
        w_mm=inches_to_mm(120),
        h_mm=inches_to_mm(48),
        kerf_in=0.3,
        margin_in=0.15,
        engine_id="arga_base",
        isolate_process=False,
    )
    placed = list((tl.hoja or {}).get("piezas") or [])
    hosts, smalls = [], []
    for p in placed:
        nom = str(p.get("nombre") or "").split("#")[0]
        rings = p.get("poligonos") or []
        poly = Polygon(rings[0], rings[1:] if len(rings) > 1 else None)
        if "VFM" in nom or "HFM" in nom:
            hosts.append(poly)
        else:
            smalls.append((nom, poly))
    cavs = []
    for hp in hosts:
        free = box(*hp.bounds).difference(hp)
        geoms = [free] if free.geom_type == "Polygon" else list(getattr(free, "geoms", []))
        cavs.extend([g for g in geoms if g.area / IN2 >= 5])
    in_c, out_c = Counter(), Counter()
    overlaps = 0
    for nom, sp in smalls:
        if any(c.contains(sp.centroid) for c in cavs):
            in_c[nom] += 1
        else:
            out_c[nom] += 1
        for hp in hosts:
            inter = sp.intersection(hp)
            if not inter.is_empty and inter.area > 1.0:
                overlaps += 1
    print(f"P3 kerf0.3: in_cav={sum(in_c.values())}/{len(smalls)} overlaps={overlaps}")
    print(" IN", dict(in_c))
    print(" OUT", dict(out_c))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
