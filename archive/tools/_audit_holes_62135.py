"""Audit overlaps in holes + empty cavities for 62135."""
from __future__ import annotations

import gzip
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from shapely.geometry import Polygon

ARG = (
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\NANS"
    r"\62135 2.arganest"
)
IN = 25.4
IN2 = IN * IN


def base(n):
    return str(n or "").split("#")[0]


def is_virt(n):
    s = str(n or "")
    return s.startswith(("REMANENTE", "TATUAJE", "RETAZO", "CU_CORTE", "REF__"))


def poly(p):
    rings = p.get("poligonos") or []
    if not rings:
        return None
    try:
        g = Polygon(rings[0], rings[1:] if len(rings) > 1 else None)
        return None if g.is_empty else g
    except Exception:
        return None


def main():
    print("mtime", os.path.getmtime(ARG))
    payload = json.loads(gzip.decompress(open(ARG, "rb").read()).decode("utf-8"))
    data = payload["resultados_multilote"][0]["data"]
    print("engine", data.get("_nest_engine_id"))

    for mat in ["0.375_A 36", "0.5_A 36"]:
        block = data.get(mat) or {}
        hojas = block.get("hojas") or []
        print(f"\n=== {mat} n={len(hojas)}")
        for i, h in enumerate(hojas):
            if h.get("es_retazo"):
                continue
            reales = []
            for p in h.get("piezas") or []:
                if is_virt(p.get("nombre")):
                    continue
                g = poly(p)
                if g is not None:
                    reales.append((base(p.get("nombre")), g, p))
            print(
                f"  [{i}] {h.get('sheet_code') or h.get('placa_id')} "
                f"n={len(reales)} ef={float(h.get('eficiencia_real') or h.get('eficiencia') or 0):.1f}"
            )

            # metal overlaps
            for a in range(len(reales)):
                for b in range(a + 1, len(reales)):
                    inter = reales[a][1].intersection(reales[b][1])
                    if inter.area / IN2 > 0.05:
                        print(
                            f"    OVERLAP {reales[a][0][:40]} x {reales[b][0][:40]} "
                            f"= {inter.area/IN2:.2f}in2"
                        )

            # empty big holes + who could fit
            for n, g, p in reales:
                rings = p.get("poligonos") or []
                if len(rings) < 2:
                    continue
                for hr in rings[1:]:
                    try:
                        hole = Polygon(hr)
                    except Exception:
                        continue
                    ha = hole.area / IN2
                    if ha < 30:
                        continue
                    inside = []
                    for n2, g2, _ in reales:
                        if g2 is g:
                            continue
                        if hole.contains(g2.centroid) or hole.covers(g2.centroid):
                            inside.append(n2)
                    # pairwise overlap among inside
                    ov_in = 0
                    for ia in range(len(inside)):
                        ga = next(x[1] for x in reales if x[0] == inside[ia] and hole.contains(x[1].centroid))
                        # simpler: get geoms for inside pieces
                    geos = [
                        (n2, g2)
                        for n2, g2, _ in reales
                        if n2 in inside and (hole.contains(g2.centroid) or hole.covers(g2.centroid))
                    ]
                    for ia in range(len(geos)):
                        for ib in range(ia + 1, len(geos)):
                            if geos[ia][1].intersection(geos[ib][1]).area / IN2 > 0.05:
                                ov_in += 1
                                print(
                                    f"    HOLE-OVERLAP in {n[:35]} ({ha:.0f}in2): "
                                    f"{geos[ia][0][:30]} x {geos[ib][0][:30]} "
                                    f"{geos[ia][1].intersection(geos[ib][1]).area/IN2:.2f}in2"
                                )
                    if not inside:
                        print(f"    EMPTY hole {ha:.0f}in2 in {n[:50]} bounds={tuple(round(x/IN,2) for x in hole.bounds)}")
                    else:
                        print(f"    hole {ha:.0f}in2 in {n[:40]} filled_n={len(inside)} {inside[:6]}")


if __name__ == "__main__":
    raise SystemExit(main())
