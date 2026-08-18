#!/usr/bin/env python
"""Candado: Lite hole-fill mete una pieza chica en el orificio de una brida.

Caso sintético (tipo flange/SP ring): host con agujero grande + guest fuera
del agujero en la misma hoja. Con ARGA_LITE_HOLE_FILL=1 el guest debe
terminar dentro de la cavidad (centroide cubierto), sin solape.
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _ring_poly(outer, hole):
    from shapely.geometry import Polygon

    return Polygon(outer, [hole])


def main() -> int:
    os.environ["ARGA_LITE_HOLE_FILL"] = "1"
    os.environ["ARGA_NEST_VENOM"] = "0"

    from shapely.geometry import box, Polygon

    from modules.nesting_engine.venom_hole_fill import (
        apply_lite_hole_fill,
        list_host_cavities,
        lite_hole_fill_enabled,
    )

    assert lite_hole_fill_enabled() is True

    # Brida ~24"×24" con orificio ~16"×16" (mm).
    outer = [(0, 0), (600, 0), (600, 600), (0, 600), (0, 0)]
    hole = [(100, 100), (500, 100), (500, 500), (100, 500), (100, 100)]
    host_poly = _ring_poly(outer, hole)
    guest_poly = box(620, 50, 700, 120)  # fuera del anillo, misma hoja

    cavs = list_host_cavities(host_poly, open_profile=False)
    assert cavs, "la brida debe exponer cavidad interior"
    assert max(c.area for c in cavs) > 50_000.0

    host = {
        "nombre": "BRIDA-P13",
        "poly": host_poly,
        "poly_exact": host_poly,
        "area": float(host_poly.area),
        "poligonos": [list(outer), list(hole)],
    }
    guest = {
        "nombre": "SPACER-S",
        "poly": guest_poly,
        "poly_exact": guest_poly,
        "area": float(guest_poly.area),
        "poligonos": [
            [
                [620.0, 50.0],
                [700.0, 50.0],
                [700.0, 120.0],
                [620.0, 120.0],
                [620.0, 50.0],
            ]
        ],
    }
    hoja = {
        "piezas": [host, guest],
        "kerf_usado": 0.15,
        "placa_w": 2000.0,
        "placa_h": 1200.0,
    }

    stats = apply_lite_hole_fill(hoja, engine_id="arga_lite")
    assert int(stats.get("filled") or 0) >= 1, f"esperaba fill>=1, stats={stats}"
    assert not stats.get("reverted"), f"fill revertido: {stats}"

    g2 = hoja["piezas"][1]["poly"]
    assert isinstance(g2, Polygon) and not g2.is_empty
    c = g2.centroid
    assert any(cav.contains(c) or cav.covers(c) for cav in cavs), (
        f"guest centroid {c.x:.1f},{c.y:.1f} no quedó dentro del orificio"
    )

    # Opt-out debe ser no-op.
    os.environ["ARGA_LITE_HOLE_FILL"] = "0"
    from importlib import reload
    import modules.nesting_engine.venom_hole_fill as vhf

    reload(vhf)
    assert vhf.lite_hole_fill_enabled() is False
    guest_out = box(620, 50, 700, 120)
    hoja2 = {
        "piezas": [
            {"nombre": "H", "poly": host_poly, "area": float(host_poly.area)},
            {"nombre": "G", "poly": guest_out, "area": float(guest_out.area)},
        ],
        "kerf_usado": 0.15,
    }
    empty = vhf.apply_lite_hole_fill(hoja2, engine_id="arga_lite")
    assert empty == {}
    assert hoja2["piezas"][1]["poly"].equals(guest_out)

    # Shot único (renest placa): post-pack también debe fill.
    os.environ["ARGA_LITE_HOLE_FILL"] = "1"
    reload(vhf)
    guest3 = box(620, 50, 700, 120)
    hoja3 = {
        "piezas": [
            {
                "nombre": "BRIDA-P13",
                "poly": host_poly,
                "poly_exact": host_poly,
                "area": float(host_poly.area),
                "poligonos": [list(outer), list(hole)],
            },
            {
                "nombre": "SPACER-S",
                "poly": guest3,
                "poly_exact": guest3,
                "area": float(guest3.area),
            },
        ],
        "kerf_usado": 0.15,
        "placa_w": 2000.0,
        "placa_h": 1200.0,
    }
    from modules.nesting_engine.algorithm_bridge import _lite_apply_post_pack

    _lite_apply_post_pack(hoja3, 2000.0, 1200.0, 0.15)
    stats3 = hoja3.get("lite_hole_fill") or {}
    assert int(stats3.get("filled") or 0) >= 1, f"shot post-pack sin fill: {stats3}"

    # Dos guests: kerf completo entre sí y contra el metal del anillo.
    kerf_in = 0.15
    kerf_full_mm = kerf_in * 25.4
    g_a = box(620, 50, 700, 120)
    g_b = box(720, 50, 800, 120)
    hoja4 = {
        "piezas": [
            {
                "nombre": "BRIDA-P13",
                "poly": host_poly,
                "poly_exact": host_poly,
                "area": float(host_poly.area),
                "poligonos": [list(outer), list(hole)],
            },
            {"nombre": "GA", "poly": g_a, "poly_exact": g_a, "area": float(g_a.area)},
            {"nombre": "GB", "poly": g_b, "poly_exact": g_b, "area": float(g_b.area)},
        ],
        "kerf_usado": kerf_in,
        "placa_w": 2000.0,
        "placa_h": 1200.0,
    }
    stats4 = vhf.apply_lite_hole_fill(hoja4, engine_id="arga_lite")
    assert int(stats4.get("filled") or 0) >= 2, f"esperaba 2 fills: {stats4}"
    pa = hoja4["piezas"][1]["poly"]
    pb = hoja4["piezas"][2]["poly"]
    gap_gg = float(pa.distance(pb))
    gap_ha = float(pa.distance(host_poly))
    gap_hb = float(pb.distance(host_poly))
    assert gap_gg + 0.05 >= kerf_full_mm, (
        f"gap guest↔guest {gap_gg:.2f}mm < kerf {kerf_full_mm:.2f}mm"
    )
    assert gap_ha + 0.05 >= kerf_full_mm, (
        f"gap guestA↔host {gap_ha:.2f}mm < kerf {kerf_full_mm:.2f}mm"
    )
    assert gap_hb + 0.05 >= kerf_full_mm, (
        f"gap guestB↔host {gap_hb:.2f}mm < kerf {kerf_full_mm:.2f}mm"
    )

    # Placa sólida ≥80 in² debe poder ser GUEST en Lite dense (antes era host).
    solid_12 = box(650, 200, 650 + 12 * 25.4, 200 + 12 * 25.4)
    assert not vhf._is_cavity_host(
        solid_12, {"nombre": "62176-1254-P01", "area": float(solid_12.area)}
    ), "placa sólida no debe ser cavity-host"
    assert vhf._is_cavity_host(
        host_poly, {"nombre": "BRIDA-P13", "area": float(host_poly.area)}
    ), "brida con orificio sí es cavity-host"

    # poly sólido sin interiors pero poligonos con hueco → debe ver cavidad.
    solid_disk = Polygon(outer)
    p_bad = {
        "nombre": "BRIDA-POLY-SOLIDO",
        "poly": solid_disk,
        "poly_exact": solid_disk,
        "poligonos": [list(outer), list(hole)],
        "area": float(host_poly.area),
    }
    poly_fixed = vhf._piece_poly(p_bad)
    assert len(getattr(poly_fixed, "interiors", []) or []) >= 1, "debe recuperar hueco de poligonos"
    g_small = box(800, 50, 850, 100)
    hoja_poly = {
        "piezas": [
            p_bad,
            {
                "nombre": "TINY",
                "poly": g_small,
                "poly_exact": g_small,
                "area": float(g_small.area),
            },
        ],
        "kerf_usado": 0.25,
        "placa_w": 2000.0,
        "placa_h": 1200.0,
    }
    stats_poly = vhf.apply_lite_hole_fill(hoja_poly, engine_id="arga_lite")
    assert int(stats_poly.get("filled") or 0) >= 1, (
        f"con poligonos+hueco debía fill: {stats_poly}"
    )

    hoja5 = {
        "piezas": [
            {
                "nombre": "BRIDA-P13",
                "poly": host_poly,
                "poly_exact": host_poly,
                "area": float(host_poly.area),
                "poligonos": [list(outer), list(hole)],
            },
            {
                "nombre": "62176-1254-P01",
                "poly": solid_12,
                "poly_exact": solid_12,
                "area": float(solid_12.area),
            },
        ],
        "kerf_usado": 0.15,
        "placa_w": 2000.0,
        "placa_h": 1200.0,
    }
    stats5 = vhf.apply_lite_hole_fill(hoja5, engine_id="arga_lite")
    assert int(stats5.get("filled") or 0) >= 1, (
        f"placa 12x12 debía entrar al orificio: {stats5}"
    )

    # Varios guests chicos en un anillo grande (meta acomodo manual denso).
    outer2 = [(0, 0), (700, 0), (700, 700), (0, 700), (0, 0)]
    hole2 = [(80, 80), (620, 80), (620, 620), (80, 620), (80, 80)]
    host2 = _ring_poly(outer2, hole2)
    smalls = []
    for i in range(8):
        s = box(800 + (i % 4) * 90, 40 + (i // 4) * 90, 800 + (i % 4) * 90 + 70, 110 + (i // 4) * 90)
        smalls.append(
            {
                "nombre": f"S{i}",
                "poly": s,
                "poly_exact": s,
                "area": float(s.area),
            }
        )
    hoja_m = {
        "piezas": [
            {
                "nombre": "BRIDA-BIG",
                "poly": host2,
                "poly_exact": host2,
                "area": float(host2.area),
                "poligonos": [list(outer2), list(hole2)],
            },
            *smalls,
        ],
        "kerf_usado": 0.15,
        "placa_w": 3000.0,
        "placa_h": 1200.0,
    }
    # Reset idempotencia entre casos del mismo proceso.
    hoja_m.pop("_lite_hole_fill_done", None)
    st_m = vhf.apply_lite_hole_fill(hoja_m, engine_id="arga_lite")
    assert int(st_m.get("filled") or 0) >= 6, (
        f"mini-placa densa debía meter ≥6 guests en el anillo: {st_m}"
    )
    cav_m = max(list_host_cavities(host2, open_profile=False), key=lambda c: c.area)
    inside = 0
    for p in hoja_m["piezas"][1:]:
        c = p["poly"].centroid
        if cav_m.contains(c) or cav_m.covers(c):
            inside += 1
    assert inside >= 6, f"solo {inside}/8 guests dentro del orificio"

    # Recompact: tras fill, una pieza exterior con hueco a la izquierda debe acercarse.
    from modules.nesting_engine.compact_lite import recompact_exterior_after_hole_fill

    outer_r = [(0, 0), (500, 0), (500, 500), (0, 500), (0, 0)]
    hole_r = [(100, 100), (400, 100), (400, 400), (100, 400), (100, 100)]
    host_r = _ring_poly(outer_r, hole_r)
    # Guest ya "dentro" (centroide en orificio).
    g_in = box(180, 180, 260, 240)
    # Exterior lejos en X (hueco artificial a la izquierda del exterior).
    g_out = box(900, 50, 980, 120)
    hoja_rc = {
        "piezas": [
            {
                "nombre": "HOST-RC",
                "poly": host_r,
                "poly_exact": host_r,
                "area": float(host_r.area),
                "poligonos": [list(outer_r), list(hole_r)],
            },
            {
                "nombre": "GIN",
                "poly": g_in,
                "poly_exact": g_in,
                "area": float(g_in.area),
            },
            {
                "nombre": "GOUT",
                "poly": g_out,
                "poly_exact": g_out,
                "area": float(g_out.area),
            },
        ],
        "kerf_usado": 0.15,
        "placa_w": 2000.0,
        "placa_h": 800.0,
        "lite_hole_fill": {"filled": 1},
    }
    x_before = float(hoja_rc["piezas"][2]["poly"].bounds[0])
    gin_c_before = (
        float(hoja_rc["piezas"][1]["poly"].centroid.x),
        float(hoja_rc["piezas"][1]["poly"].centroid.y),
    )
    rc = recompact_exterior_after_hole_fill(hoja_rc, engine_id="arga_lite")
    assert not rc.get("reverted"), f"recompact revertido: {rc}"
    x_after = float(hoja_rc["piezas"][2]["poly"].bounds[0])
    assert x_after < x_before - 5.0, (
        f"exterior no se reacomodó: x {x_before:.1f} → {x_after:.1f} rc={rc}"
    )
    gin_c_after = (
        float(hoja_rc["piezas"][1]["poly"].centroid.x),
        float(hoja_rc["piezas"][1]["poly"].centroid.y),
    )
    # Guest en orificio no debe moverse solo (queda en cavidad).
    assert abs(gin_c_after[0] - gin_c_before[0]) < 1.0 and abs(
        gin_c_after[1] - gin_c_before[1]
    ) < 1.0, f"guest en orificio se movió solo: {gin_c_before} → {gin_c_after}"

    # Void-first: guests salen del pool MC y viajan como cargo del host.
    from modules.nesting_engine.venom_hole_fill import (
        expand_void_cargo_onto_hoja,
        prefill_voids_in_pool,
    )
    from shapely import affinity as _aff

    outer_vf = [(0, 0), (600, 0), (600, 600), (0, 600), (0, 0)]
    hole_vf = [(100, 100), (500, 100), (500, 500), (100, 500), (100, 100)]
    host_vf = _ring_poly(outer_vf, hole_vf)
    guests_vf = []
    for i in range(4):
        s = box(700 + i * 90, 40, 770 + i * 90, 110)
        guests_vf.append(
            {
                "nombre": f"VF{i}",
                "poly": s,
                "poly_exact": s,
                "area": float(s.area),
            }
        )
    solid_vf = box(800, 200, 800 + 50, 200 + 50)
    pool_vf = [
        {
            "nombre": "HOST-VF",
            "poly": host_vf,
            "poly_exact": host_vf,
            "area": float(host_vf.area),
            "poligonos": [list(outer_vf), list(hole_vf)],
        },
        *guests_vf,
        {
            "nombre": "SOLID-VF",
            "poly": solid_vf,
            "poly_exact": solid_vf,
            "area": float(solid_vf.area),
        },
    ]
    mc_pool, st_vf = prefill_voids_in_pool(pool_vf, 0.15, engine_id="arga_lite")
    assert int(st_vf.get("filled") or 0) >= 3, f"void-first filled: {st_vf}"
    assert len(mc_pool) < len(pool_vf), "guests debían salir del pool MC"
    host_mc = next(p for p in mc_pool if p["nombre"] == "HOST-VF")
    assert len(host_mc.get("_void_cargo") or []) >= 3
    # Simula nest: traslada host 1000,200 y expande cargo.
    host_placed = copy.deepcopy(host_mc)
    host_placed["poly"] = _aff.translate(host_vf, 1000, 200)
    host_placed["poligonos"] = [
        [[x + 1000, y + 200] for x, y in outer_vf],
        [[x + 1000, y + 200] for x, y in hole_vf],
    ]
    hoja_vf = {"piezas": [host_placed], "kerf_usado": 0.15}
    n_exp = expand_void_cargo_onto_hoja(hoja_vf, mc_pool, engine_id="arga_lite")
    assert n_exp >= 3, f"expand cargo={n_exp}"
    cav_vf = max(
        list_host_cavities(host_placed["poly"], open_profile=False),
        key=lambda c: c.area,
    )
    inside_vf = 0
    for p in hoja_vf["piezas"][1:]:
        c = p["poly"].centroid
        if cav_vf.contains(c) or cav_vf.covers(c):
            inside_vf += 1
    assert inside_vf >= 3, f"cargo no quedó en orificio tras nest: {inside_vf}"

    print(
        "LITE_HOLE_FILL_BRIDA PASS",
        f"filled={stats.get('filled')} mini_filled={st_m.get('filled')} "
        f"kerf_gg={gap_gg:.2f} wall={gap_ha:.2f}/{gap_hb:.2f} "
        f"recompact_dx={x_before - x_after:.1f} "
        f"void_first={st_vf.get('filled')} expand={n_exp}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
