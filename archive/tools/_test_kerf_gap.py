"""Verifica que el kerf solicitado se respeta entre sólidos (placa + cavidades)."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from shapely.geometry import Polygon
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

# Placa de la captura (espacio 0.3")
TABLA = [
    ("GENE-BKS-M-44-10-301", 6),
    ("GENE-VFM-20-101", 1),
    ("GENE-HFM-10-102", 2),
    ("GENE-BKT-295", 4),
    ("GENE-BKT-267", 12),
    ("GENE-BKT-369", 24),
    ("GENE-BKT-CT-103", 12),
    ("GENE-BKT-299", 2),
    ("GENE-GS-0820-708", 16),
]

KERF_IN = 0.3
# Tolerancia Clipper/numérica: permitir float ruido, NUNCA half-kerf cheating.
MIN_GAP_MM = KERF_IN * 25.4 * 0.92  # 92% del kerf como mínimo aceptable


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
    for e in errs:
        print("parse", e)

    tl = run_plate_sim(
        piezas,
        w_mm=inches_to_mm(120),
        h_mm=inches_to_mm(48),
        kerf_in=KERF_IN,
        margin_in=0.15,
        engine_id="arga_base",
        isolate_process=False,
    )
    placed = list((tl.hoja or {}).get("piezas") or [])
    polys = []
    for p in placed:
        rings = p.get("poligonos") or []
        if not rings:
            continue
        poly = Polygon(rings[0], rings[1:] if len(rings) > 1 else None)
        if poly.is_empty:
            continue
        polys.append((str(p.get("nombre") or "").split("#")[0], poly))

    print(f"piezas={len(polys)} kerf={KERF_IN}\" min_gap_mm={MIN_GAP_MM:.2f}")

    violations = []
    min_gap = 1e9
    for i in range(len(polys)):
        ni, pi = polys[i]
        for j in range(i + 1, len(polys)):
            nj, pj = polys[j]
            if pi.intersects(pj) and not pi.touches(pj):
                inter = pi.intersection(pj)
                if inter.area > 1.0:
                    violations.append((ni, nj, "OVERLAP", inter.area))
                    continue
            gap = pi.distance(pj)
            if gap < min_gap:
                min_gap = gap
            if gap + 1e-6 < MIN_GAP_MM:
                violations.append((ni, nj, "GAP", gap / 25.4))

    print(f"min_gap_in={min_gap/25.4:.4f}\" (req>={KERF_IN * 0.92:.4f}\")")
    print(f"violations={len(violations)}")
    for v in violations[:20]:
        print(" ", v)

    if violations:
        print("FAIL: kerf no respetado")
        return 2
    print("PASS: kerf respetado entre solids")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
