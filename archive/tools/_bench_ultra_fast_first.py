"""Prueba rápida Ultra fast-first vs Ultra legacy (ARGA_ULTRA_FAST_FIRST=0)."""
from __future__ import annotations

import copy
import os
import sys
import time

sys.path.insert(0, r"c:\Proyectos\New Arga Nesting Suite")

from shapely.geometry import Polygon, box

from modules.nesting_engine.algorithm_bridge import empaquetar_una_hoja_svgnest_ultra


def make_pieces():
    specs = []
    for i in range(2):
        specs.append(box(0, 0, 700, 450))
    for i in range(4):
        specs.append(box(0, 0, 280 - i * 15, 160))
    for i in range(8):
        specs.append(box(0, 0, 120 - (i % 3) * 10, 70))
    for i in range(2):
        specs.append(
            Polygon([(0, 0), (160, 0), (160, 50), (50, 50), (50, 140), (0, 140)])
        )
    out = []
    for i, poly in enumerate(specs):
        out.append(
            {
                "nombre": f"P{i}",
                "area": float(poly.area),
                "calibre": "0.25",
                "material": "SS",
                "poly": poly,
            }
        )
    return out


def run(label: str, fast: bool):
    os.environ["ARGA_ULTRA_FAST_FIRST"] = "1" if fast else "0"
    piezas = make_pieces()
    t0 = time.perf_counter()
    hoja, restos = empaquetar_una_hoja_svgnest_ultra(
        copy.deepcopy(piezas),
        3048.0,
        1219.2,
        kerf_override=0.3,
        margin_override=0.15,
        ga_generations=6,
        ga_population=8,
    )
    dt = time.perf_counter() - t0
    efi = float(hoja.get("eficiencia") or 0.0)
    if efi <= 1.5:
        efi *= 100.0
    print(
        f"{label}: t={dt:.2f}s placed={len(hoja.get('piezas') or [])} "
        f"restos={len(restos or [])} efi={efi:.2f}%",
        flush=True,
    )
    return dt, len(hoja.get("piezas") or []), len(restos or []), efi


if __name__ == "__main__":
    print("=== FAST-FIRST ON ===", flush=True)
    a = run("FAST", True)
    print("=== FAST-FIRST OFF ===", flush=True)
    b = run("LEGACY", False)
    print(
        f"DELTA FAST-LEGACY: time={a[0]-b[0]:+.2f}s placed={a[1]-b[1]:+d} "
        f"restos={a[2]-b[2]:+d} efi={a[3]-b[3]:+.2f}pp",
        flush=True,
    )
