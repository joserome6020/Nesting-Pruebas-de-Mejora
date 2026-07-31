"""Placa con presión: ¿30° en chicas reduce restos vs 90°?"""
from __future__ import annotations

import copy
import os
import sys
import time

sys.path.insert(0, r"c:\Proyectos\New Arga Nesting Suite")

from shapely.geometry import Polygon, box

from modules.nesting_engine import nest_optimization as nopt
from modules.nesting_engine.algorithm_bridge import empaquetar_una_hoja_svgnest_ultra
from modules.nesting_engine.nest_engine_context import ENGINE_SVGNEST_ULTRA

W, H = 2438.4, 1219.2  # 96x48 — más apretada


def piece(n, p):
    return {
        "nombre": n,
        "area": float(p.area),
        "calibre": "0.25",
        "material": "SS",
        "poly": p,
    }


def mix():
    out = []
    for i in range(2):
        out.append(piece(f"G{i}", box(0, 0, 800, 500)))
    for i in range(8):
        out.append(piece(f"M{i}", box(0, 0, 280 - i * 8, 160)))
    for i in range(24):
        out.append(piece(f"S{i}", box(0, 0, 100 - (i % 5) * 8, 55 - (i % 4) * 5)))
    for i in range(6):
        out.append(
            piece(
                f"L{i}",
                Polygon([(0, 0), (130, 0), (130, 40), (40, 40), (40, 120), (0, 120)]),
            )
        )
    return out


def run(mode: str, rot: float):
    orig_base = nopt.ENGINE_BASE_PROFILES[ENGINE_SVGNEST_ULTRA]
    b = dict(orig_base)
    b.update(
        {
            "fast_first": True,
            "fast_first_pop": 8,
            "fast_first_gens": 1,
            "fast_first_refine_gens": 0,
            "ga_population": 8,
            "lock_profile": True,
            "fast_first_rotation_deg": rot,
            "rotation_step_deg": rot,
        }
    )
    nopt.ENGINE_BASE_PROFILES[ENGINE_SVGNEST_ULTRA] = b
    os.environ["ARGA_ULTRA_FAST_FIRST"] = "1"
    pz = mix()
    t0 = time.perf_counter()
    h, r = empaquetar_una_hoja_svgnest_ultra(
        copy.deepcopy(pz),
        W,
        H,
        kerf_override=0.3,
        margin_override=0.15,
        ga_generations=1,
        ga_population=8,
        rotation_step_deg=rot,
    )
    dt = time.perf_counter() - t0
    efi = float(h.get("eficiencia") or 0.0)
    if efi <= 1.5:
        efi *= 100.0
    placed = len(h.get("piezas") or [])
    restos = len(r or [])
    print(
        f"{mode}: t={dt:.2f}s placed={placed}/{len(pz)} restos={restos} efi={efi:.2f}%",
        flush=True,
    )
    return dt, placed, restos, efi


def main():
    orig = dict(nopt.ENGINE_BASE_PROFILES[ENGINE_SVGNEST_ULTRA])
    try:
        a = run("all90", 90.0)
        b = run("small30", 30.0)
        print(
            f"DELTA30: dt={b[0]-a[0]:+.2f}s dplaced={b[1]-a[1]:+d} "
            f"drestos={b[2]-a[2]:+d} defi={b[3]-a[3]:+.2f}pp",
            flush=True,
        )
        # Criterio IF: solo vale si baja restos o sube placed, y tiempo < 1.5x
        better = (b[1] > a[1]) or (b[2] < a[2]) or (b[3] > a[3] + 0.4)
        cheap = b[0] < a[0] * 1.5
        print(
            f"IF_TRIGGER_WORTH: better={better} cheap_enough={cheap} "
            f"recommend={'USE_SMALL_ANGLES' if (better and cheap) else 'KEEP_90'}",
            flush=True,
        )
    finally:
        nopt.ENGINE_BASE_PROFILES[ENGINE_SVGNEST_ULTRA] = orig


if __name__ == "__main__":
    main()
