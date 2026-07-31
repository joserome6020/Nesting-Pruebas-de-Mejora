"""A/B Ultra rápido: perfil viejo vs NestFab-upgrade (calidad + tiempo)."""
from __future__ import annotations

import copy
import sys
import time

sys.path.insert(0, r"c:\Proyectos\New Arga Nesting Suite")

from shapely.geometry import Polygon, box

from modules.nesting_engine import nest_optimization as nopt
from modules.nesting_engine.algorithm_bridge import empaquetar_una_hoja_svgnest_ultra
from modules.nesting_engine.nest_engine_context import ENGINE_SVGNEST_ULTRA


def make_pieces() -> list[dict]:
    specs = []
    for i in range(2):
        specs.append(box(0, 0, 700, 450))  # ~mid/large
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


OLD = {
    "mc_iterations": 4,
    "continual_until_user_stops": False,
    "continual_optimization": False,
    "rotation_step_deg": 30,
    "rotation_fine_deg": 30,
    "rotation_any_small": False,
    "tilt_deg": 0.0,
    "common_line_lite": False,
    "nest_repeats_mode": "off",
    "parallel_seeds": 1,
    "ga_population": 10,
    "part_in_part": True,
    "lock_profile": True,
    "pack_explore_then_refine": True,
}

NEW = {
    "mc_iterations": 4,
    "continual_until_user_stops": True,
    "continual_optimization": True,
    "rotation_step_deg": 15,
    "rotation_fine_deg": 5,
    "rotation_any_small": True,
    "tilt_deg": 3.0,
    "common_line_lite": True,
    "nest_repeats_mode": "balanced",
    "parallel_seeds": 4,
    "ga_population": 10,
    "part_in_part": True,
    "lock_profile": True,
    "pack_explore_then_refine": True,
}


def apply_profile(overrides: dict) -> None:
    base = dict(nopt.ENGINE_BASE_PROFILES[ENGINE_SVGNEST_ULTRA])
    base.update(overrides)
    nopt.ENGINE_BASE_PROFILES[ENGINE_SVGNEST_ULTRA] = base


def run_once(label: str, overrides: dict, piezas: list) -> dict:
    apply_profile(overrides)
    t0 = time.perf_counter()
    hoja, restos = empaquetar_una_hoja_svgnest_ultra(
        copy.deepcopy(piezas),
        3048.0,
        1219.2,
        kerf_override=0.3,
        margin_override=0.15,
        ga_generations=int(overrides["mc_iterations"]),
        ga_population=int(overrides["ga_population"]),
    )
    dt = time.perf_counter() - t0
    efi = float(hoja.get("eficiencia") or 0.0)
    if efi <= 1.5:
        efi *= 100.0
    return {
        "label": label,
        "s": dt,
        "placed": len(hoja.get("piezas") or []),
        "restos": len(restos or []),
        "efi": efi,
        "area": float(hoja.get("area_usada") or 0.0),
        "cl": int((hoja.get("common_line_lite") or {}).get("n_pairs") or 0),
    }


def main() -> None:
    piezas = make_pieces()
    print(f"PIEZAS={len(piezas)}", flush=True)
    original = dict(nopt.ENGINE_BASE_PROFILES[ENGINE_SVGNEST_ULTRA])
    try:
        # warmup
        apply_profile(OLD)
        empaquetar_una_hoja_svgnest_ultra(
            copy.deepcopy(piezas[:6]),
            3048.0,
            1219.2,
            kerf_override=0.3,
            margin_override=0.15,
            ga_generations=1,
            ga_population=8,
        )
        rows = [run_once("OLD", OLD, piezas), run_once("NEW", NEW, piezas)]
        print("\n=== RESULTS ===", flush=True)
        for r in rows:
            print(
                f"{r['label']}: t={r['s']:.2f}s placed={r['placed']} restos={r['restos']} "
                f"efi={r['efi']:.2f}% area={r['area']:.0f} cl={r['cl']}",
                flush=True,
            )
        o, n = rows[0], rows[1]
        print(
            f"\nDELTA NEW-OLD: time={n['s']-o['s']:+.2f}s "
            f"efi={n['efi']-o['efi']:+.2f}pp placed={n['placed']-o['placed']:+d} "
            f"restos={n['restos']-o['restos']:+d} cl={n['cl']-o['cl']:+d}",
            flush=True,
        )
        # Veredicto simple
        better_q = (n["placed"], -n["restos"], n["efi"]) > (o["placed"], -o["restos"], o["efi"])
        faster = n["s"] < o["s"] * 1.15  # NEW no >15% más lento, o gana calidad
        print(
            f"VERDICT: quality_better={better_q} time_ok={faster} "
            f"(NEW denser/faster tradeoff)",
            flush=True,
        )
    finally:
        nopt.ENGINE_BASE_PROFILES[ENGINE_SVGNEST_ULTRA] = original


if __name__ == "__main__":
    main()
