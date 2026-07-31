"""Medir bbox real GS y probar fill solo en un canal sintético estrecho."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from shapely.geometry import Polygon, box

from modules.nesting_engine.sim_lab import SimPieceEntry, build_pieces_from_entries, inches_to_mm, run_plate_sim

DXF_DIR = (
    r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals"
    r"\ARGA METALS CORPORATE SYSTEM\TANKS\GIGA\GIGA FLUIDSTACK"
    r"\MODEL CORE FILES\AutoDXF\Processed Files"
)
IN = 25.4


def find(item: str) -> str:
    for n in os.listdir(DXF_DIR):
        if n.upper().startswith(item.upper()) and n.lower().endswith(".dxf"):
            return os.path.join(DXF_DIR, n)
    raise FileNotFoundError(item)


def main() -> int:
    for item in ("GENE-GS-0820-708", "GENE-BKT-369", "GENE-BKT-320", "GENE-VFM-20-101"):
        piezas, _ = build_pieces_from_entries([SimPieceEntry(ruta=find(item), qty=1, nombre=item)])
        p = piezas[0]["poly"]
        minx, miny, maxx, maxy = p.bounds
        print(
            f"{item}: bbox_in={(maxx-minx)/IN:.4f}x{(maxy-miny)/IN:.4f} "
            f"area_in2={p.area/(IN*IN):.3f}"
        )

    # Solo 1 VFM + 41 GS — ¿cuántas GS entran en canales?
    entries = [
        SimPieceEntry(ruta=find("GENE-VFM-20-101"), qty=1, nombre="GENE-VFM-20-101"),
        SimPieceEntry(ruta=find("GENE-GS-0820-708"), qty=41, nombre="GENE-GS-0820-708"),
    ]
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
    placed = (tl.hoja or {}).get("piezas") or []
    host = next(p for p in placed if "VFM" in str(p.get("nombre")))
    rings = host["poligonos"]
    hp = Polygon(rings[0], rings[1:] if len(rings) > 1 else None)
    free = box(*hp.bounds).difference(hp)
    cavs = [free] if free.geom_type == "Polygon" else list(free.geoms)
    cavs = [g for g in cavs if g.area / (IN * IN) >= 5]
    gs = [p for p in placed if "GS" in str(p.get("nombre"))]
    in_c = sum(1 for p in gs if any(c.contains(Polygon(p["poligonos"][0]).centroid) for c in cavs))
    print(f"1 VFM + 41 GS: in_cav={in_c}/{len(gs)} restos={len(tl.restos or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
