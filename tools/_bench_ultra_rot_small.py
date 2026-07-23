"""A/B rotación: todo 90° vs chicas con más ángulos (C++ por área).

Mide tiempo vs efi/colocadas en mixes distintos.
"""
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

W, H = 3048.0, 1219.2  # 120x48 in
IN2 = 25.4 * 25.4


def piece(name: str, poly) -> dict:
    return {
        "nombre": name,
        "area": float(poly.area),
        "calibre": "0.25",
        "material": "SS",
        "poly": poly,
    }


def mix_tank_like() -> list[dict]:
    """Parecido a placa con grandes + relleno (pocas chicas relativas)."""
    out = []
    for i in range(3):
        out.append(piece(f"G{i}", box(0, 0, 900, 600)))  # ~334 in²
    for i in range(4):
        out.append(piece(f"M{i}", box(0, 0, 500 - i * 20, 350)))  # ~mid
    for i in range(8):
        out.append(piece(f"S{i}", box(0, 0, 140 - (i % 4) * 15, 70)))
    for i in range(2):
        out.append(
            piece(
                f"L{i}",
                Polygon([(0, 0), (180, 0), (180, 55), (55, 55), (55, 160), (0, 160)]),
            )
        )
    return out


def mix_small_heavy() -> list[dict]:
    """Muchas chicas / cóncavas: donde más ángulos deberían ayudar."""
    out = []
    for i in range(2):
        out.append(piece(f"G{i}", box(0, 0, 700, 450)))
    for i in range(6):
        out.append(piece(f"M{i}", box(0, 0, 320 - i * 10, 180)))
    for i in range(20):
        w = 110 - (i % 5) * 8
        h = 65 - (i % 4) * 5
        out.append(piece(f"S{i}", box(0, 0, w, h)))
    for i in range(6):
        out.append(
            piece(
                f"L{i}",
                Polygon([(0, 0), (140, 0), (140, 45), (45, 45), (45, 130), (0, 130)]),
            )
        )
    return out


def mix_all_small() -> list[dict]:
    """Sin estructurales grandes: peor caso de costo de rotación."""
    out = []
    for i in range(28):
        w = 130 - (i % 6) * 10
        h = 80 - (i % 5) * 8
        out.append(piece(f"S{i}", box(0, 0, max(40, w), max(30, h))))
    for i in range(4):
        out.append(
            piece(
                f"L{i}",
                Polygon([(0, 0), (120, 0), (120, 40), (40, 40), (40, 110), (0, 110)]),
            )
        )
    return out


def apply_rot_mode(mode: str) -> None:
    """
    all90: fast_first_rotation_deg=90 (todas ortogonales vía perfil).
    small30: perfil 30° → C++: grandes 90, mid 45, chicas 30.
    small15: perfil 15° → chicas más ángulos.
    """
    base = dict(nopt.ENGINE_BASE_PROFILES[ENGINE_SVGNEST_ULTRA])
    base["fast_first"] = True
    base["fast_first_pop"] = 8
    base["fast_first_gens"] = 1
    base["fast_first_refine_gens"] = 0  # solo primer nest, sin refine
    base["ga_population"] = 8
    base["lock_profile"] = True
    if mode == "all90":
        base["fast_first_rotation_deg"] = 90.0
        base["rotation_step_deg"] = 90.0
    elif mode == "small30":
        # Importante: C++ usa rotation_step_deg como base para chicas;
        # effective_rotation fuerza 90/45 en grandes/medias.
        base["fast_first_rotation_deg"] = 30.0
        base["rotation_step_deg"] = 30.0
    elif mode == "small15":
        base["fast_first_rotation_deg"] = 15.0
        base["rotation_step_deg"] = 15.0
    else:
        raise ValueError(mode)
    nopt.ENGINE_BASE_PROFILES[ENGINE_SVGNEST_ULTRA] = base
    os.environ["ARGA_ULTRA_FAST_FIRST"] = "1"
    os.environ.pop("ARGA_ULTRA_TILT_DEG", None)


def run_once(label: str, mode: str, piezas: list) -> dict:
    apply_rot_mode(mode)
    t0 = time.perf_counter()
    hoja, restos = empaquetar_una_hoja_svgnest_ultra(
        copy.deepcopy(piezas),
        W,
        H,
        kerf_override=0.3,
        margin_override=0.15,
        ga_generations=1,
        ga_population=8,
        rotation_step_deg=float(
            nopt.ENGINE_BASE_PROFILES[ENGINE_SVGNEST_ULTRA]["fast_first_rotation_deg"]
        ),
    )
    dt = time.perf_counter() - t0
    efi = float(hoja.get("eficiencia") or 0.0)
    if efi <= 1.5:
        efi *= 100.0
    placed = len(hoja.get("piezas") or [])
    n_rest = len(restos or [])
    return {
        "label": label,
        "mode": mode,
        "s": round(dt, 2),
        "placed": placed,
        "restos": n_rest,
        "efi": round(efi, 2),
        "n": len(piezas),
    }


def main() -> None:
    original = dict(nopt.ENGINE_BASE_PROFILES[ENGINE_SVGNEST_ULTRA])
    scenarios = [
        ("tank_like", mix_tank_like()),
        ("small_heavy", mix_small_heavy()),
        ("all_small", mix_all_small()),
    ]
    modes = ["all90", "small30", "small15"]
    try:
        # warmup
        apply_rot_mode("all90")
        empaquetar_una_hoja_svgnest_ultra(
            mix_tank_like()[:6], W, H, kerf_override=0.3, margin_override=0.15,
            ga_generations=1, ga_population=6, rotation_step_deg=90.0,
        )
        rows = []
        for scen, piezas in scenarios:
            for mode in modes:
                r = run_once(f"{scen}|{mode}", mode, piezas)
                rows.append(r)
                print(
                    f"{r['label']:28} t={r['s']:6.2f}s placed={r['placed']:2}/{r['n']} "
                    f"restos={r['restos']:2} efi={r['efi']:6.2f}%",
                    flush=True,
                )

        print("\n=== DELTA vs all90 (mismo scenario) ===", flush=True)
        by = {}
        for r in rows:
            scen = r["label"].split("|")[0]
            by.setdefault(scen, {})[r["mode"]] = r
        for scen, m in by.items():
            base = m["all90"]
            for mode in ("small30", "small15"):
                o = m[mode]
                print(
                    f"{scen:12} {mode}: dt={o['s']-base['s']:+.2f}s "
                    f"defi={o['efi']-base['efi']:+.2f}pp "
                    f"dplaced={o['placed']-base['placed']:+d} "
                    f"drestos={o['restos']-base['restos']:+d}",
                    flush=True,
                )

        print("\n=== VEREDICTO HEURÍSTICO ===", flush=True)
        for scen, m in by.items():
            base = m["all90"]
            best = base
            for mode in ("small30", "small15"):
                o = m[mode]
                # Vale la pena si gana colocadas, o efi >= +0.5pp, o menos restos,
                # y el tiempo extra < 40% o < 5s.
                better_q = (o["placed"], -o["restos"], o["efi"]) > (
                    base["placed"],
                    -base["restos"],
                    base["efi"],
                )
                dt_ratio = o["s"] / max(base["s"], 1e-6)
                worth = better_q and (dt_ratio < 1.4 or (o["s"] - base["s"]) < 5.0)
                mild = better_q and not worth
                print(
                    f"{scen:12} {mode}: better_q={better_q} "
                    f"time_x={dt_ratio:.2f} worth={worth} mild_gain_expensive={mild}",
                    flush=True,
                )
                if worth and (o["placed"], -o["restos"], o["efi"]) > (
                    best["placed"],
                    -best["restos"],
                    best["efi"],
                ):
                    best = o
            print(
                f"  -> prefer for {scen}: {best['mode']} "
                f"(t={best['s']}s efi={best['efi']}% placed={best['placed']})",
                flush=True,
            )
    finally:
        nopt.ENGINE_BASE_PROFILES[ENGINE_SVGNEST_ULTRA] = original


if __name__ == "__main__":
    main()
