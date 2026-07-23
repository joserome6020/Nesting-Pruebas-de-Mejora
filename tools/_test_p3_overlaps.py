"""Verifica solapes reales host↔brackets y piezas en cavidades abiertas (P3 BOM)."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from shapely.geometry import Polygon, box
from shapely.ops import unary_union

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

# BOM aproximado UI P3 (53 pz)
TABLA = [
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


def find(item: str) -> str | None:
    for n in os.listdir(DXF_DIR):
        if n.upper().startswith(item.upper()) and n.lower().endswith(".dxf"):
            return os.path.join(DXF_DIR, n)
    return None


def main() -> int:
    entries = []
    for item, qty in TABLA:
        ruta = find(item)
        if not ruta:
            print("FALTA", item)
            return 1
        entries.append(SimPieceEntry(ruta=ruta, qty=qty, nombre=item))
    piezas, errs = build_pieces_from_entries(entries)
    print("pool", len(piezas), "errs", errs)

    tl = run_plate_sim(
        piezas,
        w_mm=inches_to_mm(120),
        h_mm=inches_to_mm(48),
        kerf_in=0.3,
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
            hosts.append((nom, poly))
        else:
            smalls.append((nom, poly))

    overlaps = 0
    overlap_area = 0.0
    in_cav = 0
    for sn, sp in smalls:
        for hn, hp in hosts:
            inter = sp.intersection(hp)
            if not inter.is_empty and inter.area > 1.0:
                overlaps += 1
                overlap_area += inter.area / IN2
                print(f"OVERLAP {sn} x {hn} area_in2={inter.area/IN2:.3f}")
        # open cavity of any host?
        hit = False
        for hn, hp in hosts:
            free = box(*hp.bounds).difference(hp)
            if free.contains(sp.centroid) or (not free.intersection(sp).is_empty and free.intersection(sp).area > sp.area * 0.5):
                hit = True
                break
        if hit:
            in_cav += 1

    efi = float(hoja.get("area_usada") or 0) / (tl.w_mm * tl.h_mm) * 100
    print(f"efi={efi:.1f}% placed={len(placed)} smalls={len(smalls)}")
    print(f"real_overlaps={overlaps} overlap_in2={overlap_area:.2f}")
    print(f"in_open_cav~={in_cav}")
    return 0 if overlaps == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
