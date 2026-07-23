"""Pack minimo: 1 VFM + BKTs. Reporta si cavidades abiertas reciben piezas."""
from __future__ import annotations

import os
import sys

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


def find(item: str) -> str:
    for n in os.listdir(DXF_DIR):
        if n.upper().startswith(item.upper()) and n.lower().endswith(".dxf"):
            return os.path.join(DXF_DIR, n)
    raise FileNotFoundError(item)


def main() -> int:
    entries = [
        SimPieceEntry(ruta=find("GENE-VFM-20-101"), qty=1, nombre="GENE-VFM-20-101"),
        SimPieceEntry(ruta=find("GENE-BKT-369"), qty=12, nombre="GENE-BKT-369"),
    ]
    piezas, errs = build_pieces_from_entries(entries)
    print("pool", len(piezas), "errs", errs)

    tl = run_plate_sim(
        piezas,
        w_mm=inches_to_mm(120),
        h_mm=inches_to_mm(48),
        kerf_in=0.1,
        margin_in=0.15,
        engine_id="arga_base",
        isolate_process=False,
    )
    hoja = tl.hoja or {}
    hosts = [p for p in hoja.get("piezas") or [] if str(p.get("nombre") or "").startswith("GENE-VFM")]
    bkts = [p for p in hoja.get("piezas") or [] if "BKT" in str(p.get("nombre") or "")]
    print("placed hosts", len(hosts), "bkts", len(bkts), "restos", len(tl.restos or []))
    if not hosts:
        print("NO HOST")
        return 1
    rings = hosts[0].get("poligonos") or []
    outer = Polygon(rings[0])
    free = box(*outer.bounds).difference(outer)
    geoms = [free] if free.geom_type == "Polygon" else list(getattr(free, "geoms", []))
    geoms = [g for g in geoms if g.area / IN2 >= 5]
    print("open cavs", len(geoms), "areas", [round(g.area / IN2, 1) for g in geoms])
    in_open = 0
    for p in bkts:
        c = Polygon(p["poligonos"][0]).centroid
        if any(g.contains(c) for g in geoms):
            in_open += 1
            print("  IN CAV", [round(x / 25.4, 2) for x in (c.x, c.y)])
        else:
            print("  OUT   ", [round(x / 25.4, 2) for x in (c.x, c.y)])
    print(f"in_open={in_open}/{len(bkts)}")
    return 0 if in_open > 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
