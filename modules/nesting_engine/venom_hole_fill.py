"""
Venom hole-fill — reacomodo same-sheet en cavidades de hosts (VFM/C).

Reglas duras (no negociables):
  - Kerf completo entre guest↔guest y guest↔metal del host.
  - Marcas siguen la misma transformación rígida (traslación + rotación).
  - Si tras el fill hay solape, se revierten los movimientos de fill.
"""
from __future__ import annotations

import copy
import math
import os
import time
from typing import Any

from shapely import affinity
from shapely.geometry import Polygon, box

IN2_MM2 = 25.4 * 25.4
MIN_CAVITY_MM2 = 5.0 * IN2_MM2
# Lite dense: solo orificios grandes (centros de brida), no barrenos.
MIN_CAVITY_DENSE_MM2 = 25.0 * IN2_MM2
MIN_HOST_BBOX_MM2 = 80.0 * IN2_MM2
MIN_HOST_AREA_MM2 = 40.0 * IN2_MM2
MAX_CANDIDATES_PER_GUEST = 220
MAX_GUESTS_TO_TRY = 120
MAX_FILL_SECONDS = 35.0
MAX_LITE_FILL_SECONDS = 16.0
# Tolerancia numérica; cualquier solape real de metal se rechaza.
METAL_OVERLAP_EPS_MM2 = 0.05
MIN_SHEET_POCKET_MM2 = 8.0 * IN2_MM2
# Pasillos entre marcos (p.ej. WFM/VFM apilados): umbrales más permisivos.
MIN_CORRIDOR_POCKET_MM2 = 2.0 * IN2_MM2
MIN_CORRIDOR_GAP_MM = 10.0
MIN_CORRIDOR_LEN_MM = 50.0
MAX_CORRIDOR_MOVES = 48
MAX_CORRIDOR_SECONDS = 28.0


def corridor_fill_enabled() -> bool:
    """Relleno de pasillos entre hosts. Default OFF; on con ARGA_NEST_CORRIDOR_FILL=1."""
    v = (os.environ.get("ARGA_NEST_CORRIDOR_FILL") or "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def lite_hole_fill_enabled() -> bool:
    """Post-pase Lite: meter guests en orificios grandes del host.

    Default ON (barato, reusa fill_host_cavities). Opt-out: ARGA_LITE_HOLE_FILL=0.
    Independiente de ARGA_NEST_VENOM.
    """
    v = (os.environ.get("ARGA_LITE_HOLE_FILL") or "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def lite_void_first_enabled() -> bool:
    """Pre-llenar orificios en el pool ANTES del MC de placa.

    Default ON con hole-fill. Opt-out: ARGA_LITE_VOID_FIRST=0.
    """
    if not lite_hole_fill_enabled():
        return False
    v = (os.environ.get("ARGA_LITE_VOID_FIRST") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def prefill_voids_in_pool(
    piezas: list,
    kerf_in: float,
    *,
    engine_id: str = "arga_lite",
) -> tuple[list, dict[str, Any]]:
    """Void-first: mete guests en orificios del host (coords locales) y los saca del pool MC.

    Cada host queda con ``_void_cargo`` (guests ya poseídos en su marco local) y
    ``poly_exact`` congelado para poder expandir tras el nest de placa.
    """
    stats: dict[str, Any] = {
        "enabled": lite_void_first_enabled(),
        "hosts": 0,
        "cavities": 0,
        "filled": 0,
        "mode": "void_first",
    }
    if not lite_void_first_enabled() or not piezas:
        return list(piezas or []), stats

    t0 = time.perf_counter()
    pool = [copy.deepcopy(p) for p in piezas]
    kerf_in = float(kerf_in or 0.0)
    kerf_half = max((kerf_in * 25.4) / 2.0, 0.5)
    kerf_full = max(kerf_half * 2.0, 1.0)
    time_budget = MAX_LITE_FILL_SECONDS

    entries: list[dict] = []
    for idx, p in enumerate(pool):
        if _is_virtual(str(p.get("nombre") or "")):
            continue
        poly = _piece_poly(p)
        if poly is None:
            continue
        if p.get("poly_exact") is None:
            p["poly_exact"] = poly
        p["_void_uid"] = f"vf{idx}"
        entries.append(
            {
                "idx": idx,
                "p": p,
                "poly": poly,
                "is_host": bool(_is_cavity_host(poly, p)),
            }
        )

    hosts = [e for e in entries if e["is_host"]]
    guests = [e for e in entries if not e["is_host"]]
    stats["hosts"] = len(hosts)
    if not hosts or not guests:
        return pool, stats

    cavity_jobs: list[tuple[dict, Polygon]] = []
    for h in hosts:
        cavs = list_closed_interior_cavities(h["poly"])
        for cav in cavs:
            cavity_jobs.append((h, cav))
    cavity_jobs.sort(key=lambda t: float(t[1].area), reverse=True)
    stats["cavities"] = len(cavity_jobs)
    if not cavity_jobs:
        return pool, stats

    used: set[int] = set()
    filled = 0

    for host_e, cavity in cavity_jobs:
        if time.perf_counter() - t0 > time_budget:
            break
        host_metal = host_e["poly"]
        legal_regs = _legal_regions(cavity, kerf_full)
        if not legal_regs:
            continue
        legal0 = _largest_poly(max(legal_regs, key=lambda g: g.area))
        if legal0 is None:
            continue

        pool_g = [
            e
            for e in guests
            if e["idx"] not in used and _guest_fits_cavity_quick(e["poly"], cavity, kerf_full)
        ]
        if not pool_g:
            continue
        pool_big = sorted(pool_g, key=lambda e: float(e["poly"].area), reverse=True)
        pool_small = sorted(pool_g, key=lambda e: float(e["poly"].area))
        seen: set[int] = set()
        ordered: list[dict] = []
        n_big = max(1, (len(pool_big) + 1) // 2)
        for e in pool_big[:n_big] + pool_small:
            if e["idx"] in seen:
                continue
            seen.add(e["idx"])
            ordered.append(e)

        free = legal0
        placed_polys: list[Polygon] = []
        cargo: list[dict] = list(host_e["p"].get("_void_cargo") or [])

        for guest_e in ordered:
            if time.perf_counter() - t0 > time_budget:
                break
            if guest_e["idx"] in used:
                continue
            free_parts = _iter_free_parts(
                free, min_area=max(50.0, float(guest_e["poly"].area) * 0.4)
            )
            if not free_parts:
                continue
            best = None
            for angle_deg, centered in _guest_variants(guest_e["poly"]):
                gw = centered.bounds[2] - centered.bounds[0]
                gh = centered.bounds[3] - centered.bounds[1]
                step = max(5.0, min(gw, gh) * 0.28)
                found_rot = False
                for part in free_parts:
                    for cx, cy in _blf_positions(part, gw, gh, step):
                        test = affinity.translate(centered, cx, cy)
                        try:
                            if float(test.intersection(free).area) < float(test.area) * 0.97:
                                continue
                        except Exception:
                            continue
                        try:
                            if float(test.intersection(host_metal).area) > 1.0:
                                continue
                        except Exception:
                            continue
                        ok_gap = True
                        for op in placed_polys:
                            try:
                                if float(test.distance(op)) + 1e-3 < kerf_full:
                                    ok_gap = False
                                    break
                            except Exception:
                                ok_gap = False
                                break
                        if not ok_gap:
                            continue
                        best = (angle_deg, test)
                        found_rot = True
                        break
                    if found_rot:
                        break
                if found_rot:
                    break
            if best is None:
                continue
            angle_deg, test = best
            old = guest_e["poly"]
            _apply_rigid_pose(guest_e["p"], old, test, angle_deg)
            guest_e["poly"] = test
            guest_e["p"]["_void_prefilled"] = True
            guest_e["p"]["_void_parent"] = str(host_e["p"].get("_void_uid") or "")
            cargo.append(copy.deepcopy(guest_e["p"]))
            used.add(guest_e["idx"])
            placed_polys.append(test)
            filled += 1
            try:
                free = free.difference(
                    test.buffer(kerf_full, resolution=4, join_style=2)
                )
                if free is None or getattr(free, "is_empty", True):
                    break
            except Exception:
                break

        if cargo:
            host_e["p"]["_void_cargo"] = cargo

    mc_pool = [p for i, p in enumerate(pool) if i not in used]
    stats["filled"] = filled
    stats["t"] = time.perf_counter() - t0
    if filled:
        print(
            f"[LITE-VOID-FIRST] Motor: {engine_id} | filled={filled} "
            f"hosts={stats['hosts']} cavities={stats['cavities']} "
            f"pool_mc={len(mc_pool)}/{len(pool)} kerf={kerf_in:.3f}in "
            f"t={stats['t']:.2f}s",
            flush=True,
        )
    return mc_pool, stats


def expand_void_cargo_onto_hoja(
    hoja: dict,
    mc_pool: list,
    *,
    engine_id: str = "arga_lite",
) -> int:
    """Tras el MC: proyecta ``_void_cargo`` de cada host colocado a la hoja."""
    if not isinstance(hoja, dict) or not mc_pool:
        return 0
    placed = list(hoja.get("piezas") or [])
    if not placed:
        return 0

    lookup: dict[str, list] = {}
    for p in mc_pool:
        if not isinstance(p, dict):
            continue
        if not (p.get("_void_cargo") or []):
            continue
        key = str(p.get("nombre") or "")
        lookup.setdefault(key, []).append(p)

    if not lookup:
        return 0

    try:
        from .manager import _inferir_transformacion_desde_resultado, _origen_rotacion_pieza
    except Exception:
        return 0

    expanded: list[dict] = []
    for pz in placed:
        nom = str(pz.get("nombre") or "")
        bucket = lookup.get(nom)
        if not bucket:
            continue
        src = bucket.pop(0)
        cargo = list(src.get("_void_cargo") or [])
        if not cargo:
            continue
        transform = _inferir_transformacion_desde_resultado(src, pz)
        if not transform:
            # Fallback: solo traslación de centroides.
            try:
                pe = src.get("poly_exact") or src.get("poly")
                pf = _piece_poly(pz) or pe
                if pe is None or pf is None:
                    continue
                ox, oy = float(pe.centroid.x), float(pe.centroid.y)
                nx, ny = float(pf.centroid.x), float(pf.centroid.y)
                transform = {
                    "rot_deg": 0.0,
                    "shift_x": nx - ox,
                    "shift_y": ny - oy,
                }
            except Exception:
                continue
        rot = float(transform.get("rot_deg") or 0.0)
        sx = float(transform.get("shift_x") or 0.0)
        sy = float(transform.get("shift_y") or 0.0)
        pe0 = src.get("poly_exact") or src.get("poly")
        origin = _origen_rotacion_pieza(pe0)
        for g in cargo:
            g2 = copy.deepcopy(g)
            old = _piece_poly(g2)
            if old is None:
                continue
            try:
                new = affinity.translate(
                    affinity.rotate(old, rot, origin=origin), sx, sy
                )
            except Exception:
                continue
            _apply_rigid_pose(g2, old, new, rot)
            g2["_void_prefilled"] = True
            expanded.append(g2)

    if not expanded:
        return 0
    hoja["piezas"] = placed + expanded
    try:
        hoja["area_usada"] = float(hoja.get("area_usada") or 0.0) + sum(
            float(g.get("area") or getattr(g.get("poly"), "area", 0) or 0)
            for g in expanded
        )
    except Exception:
        pass
    print(
        f"[LITE-VOID-FIRST] expand Motor: {engine_id} | cargo={len(expanded)}",
        flush=True,
    )
    hoja["lite_void_first_expanded"] = len(expanded)
    return len(expanded)


def apply_lite_hole_fill(hoja: dict, engine_id: str = "arga_lite") -> dict[str, Any]:
    """Orificio grande = mini-placa: empaqueta guests con kerf completo (meta manual).

    Idempotente por hoja. Tope de tiempo bajo para no frenar Lite.
    """
    if not isinstance(hoja, dict) or not lite_hole_fill_enabled():
        return {}
    if hoja.get("_lite_hole_fill_done"):
        return dict(hoja.get("lite_hole_fill") or {})
    piezas = hoja.get("piezas") or []
    if len(piezas) < 2:
        return {}
    try:
        stats = pack_cavities_as_mini_sheets(
            hoja, engine_id=str(engine_id or "arga_lite")
        ) or {}
    except Exception as exc:
        print(f"[LITE-HOLE-FILL] skip: {exc}", flush=True)
        return {"error": str(exc)}
    merged = {
        "hosts": int(stats.get("hosts") or 0),
        "cavities": int(stats.get("cavities") or 0),
        "filled": int(stats.get("filled") or 0),
        "area_filled": float(stats.get("area_filled") or 0.0),
        "strip_packed": int(stats.get("strip_packed") or 0),
        "repacked": int(stats.get("repacked") or 0),
        "passes": 1,
        "engine_id": str(engine_id or "arga_lite"),
        "reverted": bool(stats.get("reverted")),
        "dense_closed": True,
        "mode": "mini_sheet",
    }
    hoja["lite_hole_fill"] = dict(merged)
    hoja["_lite_hole_fill_done"] = True
    print(
        f"[LITE-HOLE-FILL] mode=mini_sheet filled={merged['filled']} "
        f"repacked={merged.get('repacked', 0)} "
        f"cavities={merged['cavities']} hosts={merged['hosts']} "
        f"kerf_wall=full",
        flush=True,
    )
    # Reacomodar placa tras sacar piezas hacia orificios (cierra huecos exteriores).
    if int(merged["filled"]) > 0 and not merged.get("reverted"):
        try:
            from . import compact_lite

            rc = compact_lite.recompact_exterior_after_hole_fill(
                hoja, engine_id=str(engine_id or "arga_lite")
            )
            merged["recompact"] = {
                "frozen": int(rc.get("frozen") or 0),
                "reverted": bool(rc.get("reverted")),
                "band": rc.get("band") or {},
                "gravity": rc.get("gravity") or {},
            }
            hoja["lite_hole_fill"] = dict(merged)
        except Exception as rc_ex:
            print(f"[LITE-RECOMPACT] skip: {rc_ex}", flush=True)
    return dict(merged)


def _largest_poly(geom) -> Polygon | None:
    if geom is None or getattr(geom, "is_empty", True):
        return None
    if getattr(geom, "geom_type", "") == "Polygon":
        return geom
    geoms = [
        g
        for g in (getattr(geom, "geoms", None) or [])
        if g is not None and getattr(g, "geom_type", "") == "Polygon" and not g.is_empty
    ]
    if not geoms:
        return None
    return max(geoms, key=lambda g: g.area)


def _iter_free_parts(geom, min_area: float = 1.0) -> list[Polygon]:
    """Componentes de área libre (no tirar huecos secundarios tras el 1er guest)."""
    if geom is None or getattr(geom, "is_empty", True):
        return []
    gt = getattr(geom, "geom_type", "")
    parts: list = []
    if gt == "Polygon":
        parts = [geom]
    elif gt == "MultiPolygon":
        parts = list(geom.geoms)
    elif gt == "GeometryCollection":
        parts = [g for g in geom.geoms if getattr(g, "geom_type", "") == "Polygon"]
    out: list[Polygon] = []
    for p in parts:
        try:
            if p is not None and (not p.is_empty) and float(p.area) >= min_area:
                out.append(p)
        except Exception:
            continue
    out.sort(key=lambda p: float(p.area), reverse=True)
    return out


def _blf_positions(legal: Polygon, gw: float, gh: float, step: float) -> list[tuple[float, float]]:
    """BLF denso: esquinas + centro + grilla fina (llena el orificio, no solo el fondo)."""
    minx, miny, maxx, maxy = legal.bounds
    if (maxx - minx) + 0.5 < gw or (maxy - miny) + 0.5 < gh:
        return []
    out: list[tuple[float, float]] = []
    for cx in (minx, maxx - gw):
        for cy in (miny, maxy - gh):
            out.append((cx, cy))
    out.append(((minx + maxx - gw) * 0.5, (miny + maxy - gh) * 0.5))
    # Paso ~1/3 del lado menor del guest → más candidatos en anillos grandes.
    st = max(4.0, min(float(step), min(gw, gh) * 0.33, 25.0))
    y = miny
    rows = 0
    while y <= maxy - gh + 1e-6 and rows < 36:
        x = minx
        cols = 0
        while x <= maxx - gw + 1e-6 and cols < 36:
            out.append((x, y))
            x += st
            cols += 1
        y += st
        rows += 1
    seen = set()
    uniq = []
    for cx, cy in out:
        key = (round(cx, 1), round(cy, 1))
        if key in seen:
            continue
        seen.add(key)
        uniq.append((cx, cy))
    uniq.sort(key=lambda t: (t[1], t[0]))
    return uniq[:320]


def pack_cavities_as_mini_sheets(
    hoja: dict, engine_id: str = "arga_lite"
) -> dict[str, Any]:
    """Trata cada orificio grande como placa virtual y empaqueta guests (BLF).

    - Kerf completo guest↔guest y guest↔pared del orificio.
    - Re-empaqueta también los que ya estaban dentro (como el acomodo manual).
    """
    t0 = time.perf_counter()
    stats: dict[str, Any] = {
        "hosts": 0,
        "cavities": 0,
        "filled": 0,
        "area_filled": 0.0,
        "strip_packed": 0,
        "repacked": 0,
        "engine_id": engine_id,
        "reverted": False,
        "mode": "mini_sheet",
    }
    piezas = hoja.get("piezas") or []
    if len(piezas) < 2:
        return stats

    kerf_in = float(hoja.get("kerf_usado", 0.0) or 0.0)
    kerf_half = max((kerf_in * 25.4) / 2.0, 0.5)
    kerf_full = max(kerf_half * 2.0, 1.0)
    time_budget = MAX_LITE_FILL_SECONDS

    snapshot = copy.deepcopy(piezas)

    entries: list[dict] = []
    for idx, p in enumerate(piezas):
        if _is_virtual(str(p.get("nombre") or "")):
            continue
        poly = _piece_poly(p)
        if poly is None:
            continue
        entries.append(
            {
                "idx": idx,
                "p": p,
                "poly": poly,
                "is_host": bool(_is_cavity_host(poly, p)),
            }
        )

    hosts = [e for e in entries if e["is_host"]]
    guests = [e for e in entries if not e["is_host"]]
    stats["hosts"] = len(hosts)
    if not hosts or not guests:
        return stats

    host_cavs: list[tuple[dict, list[Polygon]]] = []
    all_cavs: list[Polygon] = []
    for h in hosts:
        cavs = list_closed_interior_cavities(h["poly"])
        if not cavs:
            continue
        host_cavs.append((h, cavs))
        all_cavs.extend(cavs)
    stats["cavities"] = len(all_cavs)
    if not all_cavs:
        print(
            f"[VENOM-FILL] Motor: {engine_id} | mini_sheet hosts={len(hosts)} cavities=0",
            flush=True,
        )
        return stats

    used: set[int] = set()
    filled = 0
    area_filled = 0.0
    repacked = 0

    cavity_jobs: list[tuple[dict, Polygon]] = []
    for h, cavs in host_cavs:
        for cav in cavs:
            cavity_jobs.append((h, cav))
    # Orificios grandes primero (SP/brida) para meter piezas medianas; luego chicos.
    cavity_jobs.sort(key=lambda t: float(t[1].area), reverse=True)

    for host_e, cavity in cavity_jobs:
        if time.perf_counter() - t0 > time_budget:
            break
        host_metal = host_e["poly"]
        host_idx = host_e["idx"]
        legal_regs = _legal_regions(cavity, kerf_full)
        if not legal_regs:
            continue
        legal0 = _largest_poly(max(legal_regs, key=lambda g: g.area))
        if legal0 is None:
            continue

        # Pool: ya dentro + fuera que quepan (mini-placa).
        already = [
            e
            for e in guests
            if e["idx"] not in used and _guest_already_in_cavity(e["poly"], [cavity])
        ]
        outsiders = [
            e
            for e in guests
            if e["idx"] not in used
            and e["idx"] not in {a["idx"] for a in already}
            and _guest_fits_cavity_quick(e["poly"], cavity, kerf_full)
        ]
        pool = already + outsiders
        if not pool:
            continue
        # 1) grandes → aprovechan el centro; 2) chicos rellenan huecos (manual).
        pool_big = sorted(pool, key=lambda e: float(e["poly"].area), reverse=True)
        pool_small = sorted(
            pool,
            key=lambda e: (
                float(e["poly"].area),
                min(
                    e["poly"].bounds[2] - e["poly"].bounds[0],
                    e["poly"].bounds[3] - e["poly"].bounds[1],
                ),
            ),
        )
        # Unir sin duplicar: primero mitad grande, luego chicos restantes.
        seen_pool: set[int] = set()
        ordered: list[dict] = []
        n_big = max(1, (len(pool_big) + 1) // 2)
        for e in pool_big[:n_big] + pool_small:
            if e["idx"] in seen_pool:
                continue
            seen_pool.add(e["idx"])
            ordered.append(e)

        free = legal0
        placed_polys: list[Polygon] = []
        outsider_ids = {o["idx"] for o in outsiders}

        for guest_e in ordered:
            if time.perf_counter() - t0 > time_budget:
                break
            if guest_e["idx"] in used:
                continue
            free_parts = _iter_free_parts(free, min_area=max(50.0, float(guest_e["poly"].area) * 0.4))
            if not free_parts:
                continue
            best = None  # (y, x, angle, test)
            for angle_deg, centered in _guest_variants(guest_e["poly"]):
                gw = centered.bounds[2] - centered.bounds[0]
                gh = centered.bounds[3] - centered.bounds[1]
                step = max(5.0, min(gw, gh) * 0.28)
                found_rot = False
                for part in free_parts:
                    for cx, cy in _blf_positions(part, gw, gh, step):
                        test = affinity.translate(centered, cx, cy)
                        try:
                            inter_a = float(test.intersection(free).area)
                            if inter_a < float(test.area) * 0.97:
                                continue
                        except Exception:
                            continue
                        try:
                            if float(test.intersection(host_metal).area) > 1.0:
                                continue
                        except Exception:
                            continue
                        ok_gap = True
                        for op in placed_polys:
                            try:
                                if float(test.distance(op)) + 1e-3 < kerf_full:
                                    ok_gap = False
                                    break
                            except Exception:
                                ok_gap = False
                                break
                        if not ok_gap:
                            continue
                        if not placed_polys:
                            # Primer guest: centrar en el orificio (no esquina BLF).
                            try:
                                lc = free.centroid
                                score = (
                                    abs(float(test.centroid.x) - float(lc.x))
                                    + abs(float(test.centroid.y) - float(lc.y)),
                                    float(test.bounds[1]),
                                    float(test.bounds[0]),
                                )
                            except Exception:
                                score = (
                                    float(test.bounds[1]),
                                    float(test.bounds[0]),
                                    0.0,
                                )
                            if best is None or score < (
                                best[0],
                                best[1],
                                best[2] if len(best) > 2 else 0.0,
                            ):
                                best = (
                                    score[0],
                                    score[1],
                                    score[2],
                                    angle_deg,
                                    test,
                                )
                            continue
                        score = (float(test.bounds[1]), float(test.bounds[0]))
                        best = (score[0], score[1], 0.0, angle_deg, test)
                        found_rot = True
                        break
                    if found_rot:
                        break
                if found_rot:
                    break
            if best is None:
                continue
            angle_deg, test = best[-2], best[-1]
            old = guest_e["poly"]
            was_out = guest_e["idx"] in outsider_ids
            _apply_rigid_pose(guest_e["p"], old, test, angle_deg)
            guest_e["poly"] = test
            for e in entries:
                if e["idx"] == guest_e["idx"]:
                    e["poly"] = test
                    break
            used.add(guest_e["idx"])
            placed_polys.append(test)
            if was_out:
                filled += 1
                area_filled += float(test.area)
            else:
                repacked += 1
            try:
                # Reservar kerf completo alrededor → el libre restante sí admite densificar.
                free = free.difference(
                    test.buffer(kerf_full, resolution=4, join_style=2)
                )
                if free is None or getattr(free, "is_empty", True):
                    break
            except Exception:
                break

    has_overlap, detail = _sheet_has_metal_overlaps(hoja, min_area_mm2=25.0)
    if has_overlap and (filled > 0 or repacked > 0):
        hoja["piezas"] = snapshot
        stats["filled"] = 0
        stats["area_filled"] = 0.0
        stats["repacked"] = 0
        stats["reverted"] = True
        print(
            f"[VENOM-FILL] Motor: {engine_id} | mini_sheet REVERTIDO | {detail}",
            flush=True,
        )
        return stats

    stats["filled"] = filled
    stats["area_filled"] = area_filled
    stats["repacked"] = repacked
    print(
        f"[VENOM-FILL] Motor: {engine_id} | mini_sheet hosts={stats['hosts']} "
        f"cavities={stats['cavities']} filled={filled} repacked={repacked} "
        f"area={area_filled / IN2_MM2:.1f}in2 kerf={kerf_in:.3f}in "
        f"t={time.perf_counter() - t0:.2f}s",
        flush=True,
    )
    hoja["venom_fill_hosts"] = stats["hosts"]
    hoja["venom_fill_cavities"] = stats["cavities"]
    hoja["venom_fill_count"] = filled
    hoja["venom_fill_area"] = area_filled
    hoja["venom_fill_reverted"] = False
    return stats


def _is_virtual(nombre: str) -> bool:
    n = str(nombre or "")
    return n.startswith(
        ("REF__", "TATUAJE__", "RETAZO_GUILLOTINA__", "RTZCU_ZONA__", "CU_CORTE__", "REMANENTE__")
    )


def _piece_poly(p: dict) -> Polygon | None:
    """Outline con huecos. Prioriza ``poligonos`` multi-ring (orificios reales).

    Si se usa antes poly/poly_exact sólido (sin interiors), el hole-fill no ve
    cavidades y termina filled=0 aunque el DXF/nest tenga orificios en poligonos.
    """
    from .geometry_parser import reconstruir_poly_seguro

    rings = p.get("poligonos") or p.get("rings") or []
    if isinstance(rings, (list, tuple)) and len(rings) >= 2:
        poly_r = reconstruir_poly_seguro(rings)
        if poly_r is not None and not getattr(poly_r, "is_empty", True):
            if not poly_r.is_valid:
                poly_r = poly_r.buffer(0)
            if hasattr(poly_r, "geoms"):
                poly_r = max(poly_r.geoms, key=lambda g: g.area)
            if poly_r is not None and not poly_r.is_empty:
                # Preferir multi-ring si aporta orificios (o iguala al poly plano).
                n_holes = len(getattr(poly_r, "interiors", []) or [])
                if n_holes > 0:
                    return poly_r

    poly = p.get("poly_exact") or p.get("poly")
    if poly is not None and hasattr(poly, "bounds") and not getattr(poly, "is_empty", True):
        if not poly.is_valid:
            poly = poly.buffer(0)
        if hasattr(poly, "geoms"):
            poly = max(poly.geoms, key=lambda g: g.area)
        if poly is not None and not poly.is_empty:
            # Si poly no tiene huecos pero poligonos sí, ya retornamos arriba.
            return poly
    if rings:
        poly = reconstruir_poly_seguro(rings)
        if poly is not None and not poly.is_empty:
            return poly
    return None


def list_closed_interior_cavities(poly: Polygon) -> list[Polygon]:
    """Solo orificios cerrados grandes (centros de brida), sin barrenos ni AABB."""
    out: list[Polygon] = []
    if poly is None or poly.is_empty:
        return out
    min_a = MIN_CAVITY_DENSE_MM2
    try:
        for hole in poly.interiors:
            h = Polygon(hole)
            if not h.is_empty and float(h.area) >= min_a:
                out.append(h)
    except Exception:
        pass
    return _dedup_cavities(out)


def _guest_fits_cavity_quick(
    guest_poly: Polygon, cavity: Polygon, wall_clear_mm: float
) -> bool:
    """Filtro barato: bbox/diagonal vs cavidad shrinkeada. Evita grillas inútiles."""
    if guest_poly is None or cavity is None:
        return False
    if float(guest_poly.area) > float(cavity.area) * 0.98:
        return False
    regs = _legal_regions(cavity, wall_clear_mm)
    if not regs:
        return False
    legal = max(regs, key=lambda g: g.area)
    lw = legal.bounds[2] - legal.bounds[0]
    lh = legal.bounds[3] - legal.bounds[1]
    gw = guest_poly.bounds[2] - guest_poly.bounds[0]
    gh = guest_poly.bounds[3] - guest_poly.bounds[1]
    if (gw <= lw + 0.5 and gh <= lh + 0.5) or (gh <= lw + 0.5 and gw <= lh + 0.5):
        return True
    # Círculo aproximado: la diagonal del guest debe caber en el lado menor.
    diag = (gw * gw + gh * gh) ** 0.5
    return diag <= min(lw, lh) + 0.5


def _sync_outline(p: dict, poly: Polygon) -> None:
    from .geometry_parser import poligonos_desde_shapely

    p["poly"] = poly
    p["poly_exact"] = poly
    try:
        p["poligonos"] = poligonos_desde_shapely(poly)
    except Exception:
        pass
    try:
        p["area"] = float(poly.area)
    except Exception:
        pass


def _transform_ring_points(
    ring: list,
    ox: float,
    oy: float,
    nx: float,
    ny: float,
    angle_deg: float,
) -> list:
    """Rota alrededor de (ox,oy) y traslada el origen al nuevo centroide (nx,ny)."""
    ang = math.radians(float(angle_deg or 0.0))
    cos_a = math.cos(ang)
    sin_a = math.sin(ang)
    out = []
    for pt in ring:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            x = float(pt[0]) - ox
            y = float(pt[1]) - oy
            xr = x * cos_a - y * sin_a
            yr = x * sin_a + y * cos_a
            out.append([xr + nx, yr + ny])
        elif isinstance(pt, dict):
            nd = dict(pt)
            px = float(nd.get("x", nd.get("X", 0.0)) or 0.0)
            py = float(nd.get("y", nd.get("Y", 0.0)) or 0.0)
            x = px - ox
            y = py - oy
            xr = x * cos_a - y * sin_a
            yr = x * sin_a + y * cos_a
            if "x" in nd:
                nd["x"] = xr + nx
            elif "X" in nd:
                nd["X"] = xr + nx
            if "y" in nd:
                nd["y"] = yr + ny
            elif "Y" in nd:
                nd["Y"] = yr + ny
            out.append(nd)
        else:
            out.append(pt)
    return out


def _apply_rigid_pose(p: dict, old_poly: Polygon, new_poly: Polygon, angle_deg: float) -> None:
    """
    Aplica pose absoluta: outline = new_poly; marcas con la misma rotación+traslación
    respecto al centroide (evita el desfase Antigravity).
    """
    ox = float(old_poly.centroid.x)
    oy = float(old_poly.centroid.y)
    nx = float(new_poly.centroid.x)
    ny = float(new_poly.centroid.y)

    if p.get("marcas"):
        try:
            p["marcas"] = [
                _transform_ring_points(ring, ox, oy, nx, ny, angle_deg)
                for ring in p["marcas"]
            ]
        except Exception:
            pass

    dx = nx - ox
    dy = ny - oy
    p["shift_x"] = float(p.get("shift_x", 0.0) or 0.0) + dx
    p["shift_y"] = float(p.get("shift_y", 0.0) or 0.0) + dy
    _sync_outline(p, new_poly)


def _host_open_profile(poly: Polygon) -> bool:
    minx, miny, maxx, maxy = poly.bounds
    bbox_a = max((maxx - minx) * (maxy - miny), 1.0)
    return float(poly.area) / bbox_a < 0.85


def _large_closed_holes(poly: Polygon) -> bool:
    try:
        for hole in poly.interiors:
            if Polygon(hole).area >= MIN_CAVITY_MM2:
                return True
    except Exception:
        pass
    return False


def _is_host(poly: Polygon, p: dict) -> bool:
    if poly is None or poly.is_empty:
        return False
    nombre = str(p.get("nombre") or "").upper()
    # Marcos estructurales típicos GIGA / GENE (pasillos entre rieles).
    if any(tag in nombre for tag in ("VFM", "HFM", "WFM", "VTN", "WFN")):
        return True

    minx, miny, maxx, maxy = poly.bounds
    bbox_a = (maxx - minx) * (maxy - miny)
    area = float(p.get("area", poly.area) or poly.area)

    if _large_closed_holes(poly) and (bbox_a >= MIN_HOST_BBOX_MM2 or area >= MIN_HOST_AREA_MM2):
        return True
    if (
        _host_open_profile(poly)
        and bbox_a >= MIN_HOST_BBOX_MM2
        and area >= MIN_HOST_AREA_MM2
    ):
        return True
    # Piezas medianas irregulares (L / muescos): permiten fill de BKT en concavidades.
    mid_bbox = 25.0 * IN2_MM2
    mid_area = 12.0 * IN2_MM2
    if area >= mid_area and bbox_a >= mid_bbox:
        if _large_closed_holes(poly) or _host_open_profile(poly):
            return True
        # Concavidad notable: área << bbox
        if float(poly.area) < bbox_a * 0.72:
            return True
    if area >= 80.0 * IN2_MM2 and bbox_a >= MIN_HOST_BBOX_MM2:
        return True
    return False


def _is_cavity_host(poly: Polygon, p: dict) -> bool:
    """Host estricto para Lite dense: solo piezas con hueco/marco real.

    El ``_is_host`` amplio marca placas sólidas ≥80 in² como host y entonces
    NO pueden entrar a orificios de bridas (filled=0 con guests_out flojo).
    """
    if poly is None or poly.is_empty:
        return False
    nombre = str(p.get("nombre") or "").upper()
    if any(tag in nombre for tag in ("VFM", "HFM", "WFM", "VTN", "WFN")):
        return True
    if _large_closed_holes(poly):
        return True
    # Marcos abiertos grandes (pasillo), no placas sólidas.
    minx, miny, maxx, maxy = poly.bounds
    bbox_a = (maxx - minx) * (maxy - miny)
    area = float(p.get("area", poly.area) or poly.area)
    if (
        _host_open_profile(poly)
        and bbox_a >= MIN_HOST_BBOX_MM2
        and area >= MIN_HOST_AREA_MM2
        and float(poly.area) < bbox_a * 0.72
    ):
        return True
    return False


def _cavity_touches_aabb_sides(cav: Polygon, aabb: Polygon, tol: float = 1.0) -> int:
    minx, miny, maxx, maxy = aabb.bounds
    cminx, cminy, cmaxx, cmaxy = cav.bounds
    n = 0
    if abs(cminx - minx) <= tol:
        n += 1
    if abs(cmaxx - maxx) <= tol:
        n += 1
    if abs(cminy - miny) <= tol:
        n += 1
    if abs(cmaxy - maxy) <= tol:
        n += 1
    return n


def _dedup_cavities(cavs: list[Polygon]) -> list[Polygon]:
    seen = set()
    unique = []
    for g in sorted(cavs, key=lambda x: x.area, reverse=True):
        key = tuple(round(v, 1) for v in g.bounds) + (round(g.area, 0),)
        if key in seen:
            continue
        seen.add(key)
        unique.append(g)
    return unique


def list_host_cavities(poly: Polygon, *, open_profile: bool) -> list[Polygon]:
    out: list[Polygon] = []
    if poly is None or poly.is_empty:
        return out

    try:
        for hole in poly.interiors:
            h = Polygon(hole)
            if not h.is_empty and float(h.area) >= MIN_CAVITY_MM2:
                out.append(h)
    except Exception:
        pass

    minx, miny, maxx, maxy = poly.bounds
    aabb = box(minx, miny, maxx, maxy)
    bbox_area = float(aabb.area)
    try:
        free = aabb.difference(poly)
    except Exception:
        return _dedup_cavities(out)
    if free.is_empty:
        return _dedup_cavities(out)

    geoms = [free] if free.geom_type == "Polygon" else list(getattr(free, "geoms", []) or [])
    for g in geoms:
        if g.geom_type != "Polygon" or g.is_empty:
            continue
        a = float(g.area)
        if a < MIN_CAVITY_MM2:
            continue
        bw = maxx - minx
        bh = maxy - miny
        pb = g.bounds
        pw = pb[2] - pb[0]
        ph = pb[3] - pb[1]
        sides = _cavity_touches_aabb_sides(g, aabb, tol=2.0)
        reject_legacy = (a > bbox_area * 0.85) or (pw > bw * 0.92 and ph > bh * 0.92)
        if reject_legacy:
            if open_profile and sides <= 2:
                out.append(g)
            continue
        out.append(g)
    return _dedup_cavities(out)


def _guest_already_in_cavity(guest_poly: Polygon, cavities: list[Polygon]) -> bool:
    try:
        c = guest_poly.centroid
        return any(cav.contains(c) or cav.covers(c) for cav in cavities)
    except Exception:
        return False


def _legal_regions(cavity: Polygon, wall_clear_mm: float) -> list[Polygon]:
    """Cavidad usable tras holgura a pared (kerf/2). Expande MultiPolygon."""
    shrink = max(float(wall_clear_mm), 0.5)
    try:
        legal = cavity.buffer(-shrink, join_style=2)
    except Exception:
        legal = cavity
    if legal is None or legal.is_empty:
        return []
    if legal.geom_type == "Polygon":
        return [legal]
    return [
        g
        for g in (getattr(legal, "geoms", None) or [])
        if g is not None and getattr(g, "geom_type", "") == "Polygon" and not g.is_empty
    ]


def _candidate_translations(
    guest_centered: Polygon,
    cavity: Polygon,
    kerf_half: float,
    *,
    dense: bool = False,
) -> list[tuple[float, float]]:
    gw = guest_centered.bounds[2] - guest_centered.bounds[0]
    gh = guest_centered.bounds[3] - guest_centered.bounds[1]
    regions = _legal_regions(cavity, max(float(kerf_half) * 2.0, 1.0))
    if not regions:
        return []

    max_cands = MAX_CANDIDATES_PER_GUEST * (2 if dense else 1)
    cands: list[tuple[float, float]] = []
    for legal in sorted(regions, key=lambda g: g.area, reverse=True):
        lminx, lminy, lmaxx, lmaxy = legal.bounds
        if (lmaxx - lminx) + 0.5 < gw or (lmaxy - lminy) + 0.5 < gh:
            continue
        for cx in (lminx, lmaxx - gw):
            for cy in (lminy, lmaxy - gh):
                cands.append((cx, cy))
        cands.append(((lminx + lmaxx - gw) * 0.5, (lminy + lmaxy - gh) * 0.5))

        # Dense: grilla más fina para aprovechar anillos circulares.
        step_base = max(4.0, min(gw, gh, 20.0) * (0.25 if dense else 0.4))
        step = step_base
        x = lminx
        while x <= lmaxx - gw + 1e-6:
            y = lminy
            while y <= lmaxy - gh + 1e-6:
                cands.append((x, y))
                y += step
            x += step

        try:
            cav_pts = list(legal.simplify(2.0 if dense else 3.0).exterior.coords)
            g_pts = list(guest_centered.simplify(2.0 if dense else 3.0).exterior.coords)
            step_c = max(1, len(cav_pts) // (36 if dense else 24))
            step_g = max(1, len(g_pts) // (12 if dense else 10))
            for vx, vy in cav_pts[::step_c]:
                for ux, uy in g_pts[::step_g]:
                    cands.append((vx - ux, vy - uy))
        except Exception:
            pass

    out = []
    seen = set()
    for cx, cy in cands:
        key = (round(cx, 1), round(cy, 1))
        if key in seen:
            continue
        seen.add(key)
        out.append((cx, cy))
        if len(out) >= max_cands:
            break
    return out


def _place_ok(
    test_poly: Polygon,
    cavity: Polygon,
    other_raw_polys: list[Polygon],
    host_metal: Polygon,
    kerf_half: float,
) -> bool:
    """
    Reglas (TABLA GAPS — distancia exacta, no buffer flojo):
      - Guest casi entero dentro de la cavidad.
      - Guest↔guest: distancia ≥ kerf completo.
      - Guest↔host metal: distancia ≥ kerf completo.
    """
    kerf_full = max(float(kerf_half) * 2.0, 1.0)
    try:
        from .nest_poka_yoke import distancia_menor_que_kerf_mm, metal_solapa
    except Exception:
        distancia_menor_que_kerf_mm = None  # type: ignore
        metal_solapa = None  # type: ignore

    try:
        c = test_poly.centroid
        if not (cavity.contains(c) or cavity.covers(c)):
            return False

        inside = test_poly.intersection(cavity)
        if getattr(inside, "area", 0) < float(test_poly.area) * 0.95:
            return False

        if host_metal is not None and not host_metal.is_empty:
            if metal_solapa is not None and metal_solapa(
                test_poly, host_metal, area_tol_mm2=METAL_OVERLAP_EPS_MM2
            ):
                return False
            try:
                if float(test_poly.distance(host_metal)) + 1e-6 < kerf_full:
                    return False
            except Exception:
                return False

        tb = test_poly.bounds
        pad = kerf_full + 1.0
        for other in other_raw_polys:
            if other is None or other.is_empty:
                continue
            ob = other.bounds
            if (
                tb[2] + pad < ob[0]
                or tb[0] - pad > ob[2]
                or tb[3] + pad < ob[1]
                or tb[1] - pad > ob[3]
            ):
                continue
            if metal_solapa is not None and metal_solapa(
                test_poly, other, area_tol_mm2=METAL_OVERLAP_EPS_MM2
            ):
                return False
            if distancia_menor_que_kerf_mm is not None:
                if distancia_menor_que_kerf_mm(test_poly, other, kerf_full):
                    return False
            else:
                try:
                    if float(test_poly.distance(other)) + 1e-6 < kerf_full:
                        return False
                except Exception:
                    return False
        return True
    except Exception:
        return False


def _guest_variants(gpoly: Polygon) -> list[tuple[float, Polygon]]:
    """Lista (angulo_deg, poly_centrado_en_origen)."""
    variants = []
    for angle in (0.0, 90.0, 180.0, 270.0):
        try:
            rot = affinity.rotate(gpoly, angle, origin="centroid") if angle else gpoly
            minx, miny, _, _ = rot.bounds
            variants.append((angle, affinity.translate(rot, -minx, -miny)))
        except Exception:
            continue
    if not variants:
        variants.append(
            (0.0, affinity.translate(gpoly, -gpoly.bounds[0], -gpoly.bounds[1]))
        )
    return variants


def _cavity_minus_occupants(
    cavity: Polygon,
    occupants: list[Polygon],
    kerf_half: float,
    *,
    pad_mm: float | None = None,
) -> Polygon | None:
    """Cavidad restante tras restar guests ya metidos (+ holgura).

    pad_mm=None → kerf_half (legacy VFM). En dense Lite usar pad chico y dejar
    que ``_place_ok`` imponga el kerf completo guest↔guest.
    """
    free = cavity
    pad = float(kerf_half if pad_mm is None else pad_mm)
    for op in occupants:
        if op is None or op.is_empty:
            continue
        try:
            bloated = op.buffer(max(pad, 0.05), resolution=3, join_style=2)
        except Exception:
            bloated = op
        try:
            free = free.difference(bloated)
        except Exception:
            continue
    if free is None or free.is_empty:
        return None
    if free.geom_type == "Polygon":
        return free
    geoms = [g for g in (getattr(free, "geoms", None) or []) if g.geom_type == "Polygon" and not g.is_empty]
    if not geoms:
        return None
    return max(geoms, key=lambda g: g.area)


def _sheet_has_metal_overlaps(
    hoja: dict, *, min_area_mm2: float | None = None
) -> tuple[bool, str]:
    try:
        from .sheet_integrity import hoja_tiene_solapes_metal

        # Default del validador es 25 mm²; 0.05 era ruido numérico y revertía fills buenos.
        eps = float(METAL_OVERLAP_EPS_MM2 if min_area_mm2 is None else min_area_mm2)
        return hoja_tiene_solapes_metal(hoja, min_area_mm2=max(eps, 1.0))
    except Exception as exc:
        return True, f"validacion_solape_no_disponible:{exc}"


def fill_host_cavities(
    hoja: dict,
    engine_id: str = "default",
    *,
    dense_closed: bool = False,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    stats: dict[str, Any] = {
        "hosts": 0,
        "cavities": 0,
        "filled": 0,
        "area_filled": 0.0,
        "engine_id": engine_id,
        "guests_out": 0,
        "reverted": False,
        "dense_closed": bool(dense_closed),
    }
    piezas = hoja.get("piezas") or []
    if len(piezas) < 2:
        return stats

    kerf_in = float(hoja.get("kerf_usado", 0.0) or 0.0)
    kerf_half = max((kerf_in * 25.4) / 2.0, 0.5)  # mínimo 0.5 mm de holgura
    kerf_full = max(kerf_half * 2.0, 1.0)
    time_budget = (
        MAX_LITE_FILL_SECONDS if dense_closed else MAX_FILL_SECONDS
    )

    # Snapshot para rollback si se genera solape.
    snapshot_piezas = copy.deepcopy(piezas)

    entries = []
    host_fn = _is_cavity_host if dense_closed else _is_host
    for idx, p in enumerate(piezas):
        if _is_virtual(str(p.get("nombre") or "")):
            continue
        poly = _piece_poly(p)
        if poly is None:
            continue
        entries.append(
            {"idx": idx, "p": p, "poly": poly, "is_host": bool(host_fn(poly, p))}
        )

    hosts = [e for e in entries if e["is_host"]]
    guests = [e for e in entries if not e["is_host"]]
    stats["hosts"] = len(hosts)
    if not hosts or not guests:
        log_msg = (
            f"[VENOM-FILL] Motor: {engine_id} | hosts={len(hosts)} "
            f"guests={len(guests)} cavities=0 filled=0"
        )
        print(log_msg)
        _append_ai_log(log_msg)
        return stats

    host_cavs: list[tuple[dict, list[Polygon]]] = []
    all_cavs: list[Polygon] = []
    for h in hosts:
        open_prof = _host_open_profile(h["poly"])
        h["open_profile"] = bool(open_prof)
        if dense_closed:
            # Bridas: solo orificios reales (interiors). Sin recortes AABB.
            cavs = list_closed_interior_cavities(h["poly"])
            if not cavs:
                cavs = list_host_cavities(h["poly"], open_profile=open_prof)
        else:
            cavs = list_host_cavities(h["poly"], open_profile=open_prof)
        if cavs:
            host_cavs.append((h, cavs))
            all_cavs.extend(cavs)
    stats["cavities"] = len(all_cavs)
    if not all_cavs:
        log_msg = f"[VENOM-FILL] Motor: {engine_id} | hosts={len(hosts)} cavities=0 filled=0"
        print(log_msg)
        _append_ai_log(log_msg)
        return stats

    def _wall_clear_for(host_e: dict) -> float:
        # TABLA GAPS: pared de orificio = kerf completo entre piezas (guest↔anillo).
        # Nunca kerf/2: eso dejaba gaps ~0.03–0.12" en bridas.
        return kerf_full

    # Compactar guests ya dentro ANTES de meter más (libera hueco contiguo).
    if dense_closed:
        try:
            n0 = densify_cavity_strips(
                hoja, entries, host_cavs, kerf_half, dense_closed=True
            )
            stats["strip_packed"] = int(n0 or 0)
        except Exception:
            stats["strip_packed"] = 0

    guests_out = []
    for g in guests:
        if _guest_already_in_cavity(g["poly"], all_cavs):
            continue
        guests_out.append(g)

    def _min_dim(e: dict) -> float:
        b = e["poly"].bounds
        return min(b[2] - b[0], b[3] - b[1])

    if dense_closed:
        # Bridas: chicos primero; descartar los que no caben en ninguna cavidad.
        def _fits_any(e: dict) -> bool:
            return any(
                _guest_fits_cavity_quick(e["poly"], cav, kerf_full) for cav in all_cavs
            )

        guests_out = [e for e in guests_out if _fits_any(e)]
        guests_out.sort(key=lambda e: (float(e["poly"].area), _min_dim(e)))
    else:
        # Canales VFM: primero las más delgadas.
        guests_out.sort(key=lambda e: (_min_dim(e), float(e["poly"].area)))
    guests_out = guests_out[:MAX_GUESTS_TO_TRY]
    stats["guests_out"] = len(guests_out)
    if dense_closed and not guests_out:
        # Nada cabe: solo densificar lo ya dentro (barato) y salir.
        if not stats.get("reverted"):
            try:
                n_strip = densify_cavity_strips(
                    hoja, entries, host_cavs, kerf_half, dense_closed=True
                )
                stats["strip_packed"] = int(stats.get("strip_packed") or 0) + int(
                    n_strip or 0
                )
            except Exception:
                pass
        log_msg = (
            f"[VENOM-FILL] Motor: {engine_id} | hosts={stats['hosts']} "
            f"cavities={stats['cavities']} guests_out=0 "
            f"(ningún guest cabe en orificio) filled=0 "
            f"t={time.perf_counter() - t0:.2f}s"
        )
        print(log_msg)
        _append_ai_log(log_msg)
        stats["filled"] = 0
        stats["area_filled"] = 0.0
        hoja["venom_fill_hosts"] = stats["hosts"]
        hoja["venom_fill_cavities"] = stats["cavities"]
        hoja["venom_fill_count"] = 0
        hoja["venom_fill_area"] = 0.0
        hoja["venom_fill_reverted"] = False
        return stats

    def _other_raw(host_idx: int, guest_idx: int) -> list[Polygon]:
        return [
            e["poly"]
            for e in entries
            if e["idx"] != guest_idx and e["idx"] != host_idx
        ]

    filled = 0
    area_filled = 0.0
    used_guest_idx: set[int] = set()
    # Occupants: pad mínimo para BLF interno; la holgura real la impone _place_ok (kerf full).
    occ_pad = 0.5 if dense_closed else None

    cavity_jobs: list[tuple[dict, Polygon]] = []
    for h, cavs in host_cavs:
        for cav in sorted(cavs, key=lambda c: c.area, reverse=True):
            cavity_jobs.append((h, cav))

    for host_e, cavity in cavity_jobs:
        if time.perf_counter() - t0 > time_budget:
            break
        host_metal = host_e["poly"]
        host_idx = host_e["idx"]
        wall_clear = _wall_clear_for(host_e)
        # Sembrar occupants ya dentro (evita solape y mide hueco real).
        cavity_occupants: list[Polygon] = [
            e["poly"]
            for e in entries
            if (not e.get("is_host"))
            and e["idx"] != host_idx
            and _guest_already_in_cavity(e["poly"], [cavity])
        ]

        progress = True
        while progress:
            progress = False
            if time.perf_counter() - t0 > time_budget:
                break
            work_cav = _cavity_minus_occupants(
                cavity, cavity_occupants, kerf_half, pad_mm=occ_pad
            )
            if work_cav is None or work_cav.is_empty:
                break
            if float(work_cav.area) < MIN_CAVITY_MM2:
                break

            for guest_e in guests_out:
                if guest_e["idx"] in used_guest_idx:
                    continue
                if time.perf_counter() - t0 > time_budget:
                    break

                gpoly = guest_e["poly"]
                if float(gpoly.area) > float(work_cav.area) * 0.98:
                    continue
                cw = work_cav.bounds[2] - work_cav.bounds[0]
                ch = work_cav.bounds[3] - work_cav.bounds[1]

                placed = False
                legal_regs = _legal_regions(work_cav, wall_clear)
                if not legal_regs:
                    continue
                for angle_deg, centered in _guest_variants(gpoly):
                    gw = centered.bounds[2] - centered.bounds[0]
                    gh = centered.bounds[3] - centered.bounds[1]
                    fits_legal = False
                    for lg in legal_regs:
                        lw = lg.bounds[2] - lg.bounds[0]
                        lh = lg.bounds[3] - lg.bounds[1]
                        if (gw <= lw + 0.5 and gh <= lh + 0.5) or (
                            gh <= lw + 0.5 and gw <= lh + 0.5
                        ):
                            fits_legal = True
                            break
                    if not fits_legal:
                        if not (
                            (gw + wall_clear <= cw + 0.5 and gh + wall_clear <= ch + 0.5)
                            or (gh + wall_clear <= cw + 0.5 and gw + wall_clear <= ch + 0.5)
                        ):
                            continue
                    cands = _candidate_translations(
                        centered,
                        work_cav,
                        wall_clear,
                        dense=False if dense_closed else dense_closed,
                    )
                    if dense_closed:
                        # Lite: grilla moderada (no 2× candidatos).
                        cands = cands[:MAX_CANDIDATES_PER_GUEST]
                    if not cands:
                        continue

                    if not _guest_fits_cavity_quick(centered, work_cav, wall_clear):
                        continue

                    others = _other_raw(host_idx, guest_e["idx"])
                    for cx, cy in cands:
                        test = affinity.translate(centered, cx, cy)
                        if not _place_ok(test, work_cav, others, host_metal, kerf_half):
                            continue
                        # Holgura extra a pared del anillo cerrado (kerf full).
                        if wall_clear > kerf_half + 1e-6:
                            try:
                                if float(test.distance(host_metal)) + 1e-6 < wall_clear:
                                    continue
                            except Exception:
                                pass

                        old_poly = guest_e["poly"]
                        _apply_rigid_pose(guest_e["p"], old_poly, test, angle_deg)
                        guest_e["poly"] = test
                        for e in entries:
                            if e["idx"] == guest_e["idx"]:
                                e["poly"] = test
                                break
                        used_guest_idx.add(guest_e["idx"])
                        cavity_occupants.append(test)
                        filled += 1
                        area_filled += float(test.area)
                        placed = True
                        progress = True
                        break
                    if placed:
                        break
                if placed:
                    break

    # Pokayoke: si el fill generó solape, revertir TODO el fill.
    has_overlap, detail = _sheet_has_metal_overlaps(hoja)
    if has_overlap and filled > 0:
        hoja["piezas"] = snapshot_piezas
        stats["filled"] = 0
        stats["area_filled"] = 0.0
        stats["reverted"] = True
        filled = 0
        area_filled = 0.0
        log_msg = (
            f"[VENOM-FILL] Motor: {engine_id} | REVERTIDO por solape | {detail}"
        )
        print(log_msg)
        _append_ai_log(log_msg)
    else:
        wall_note = (
            f"gap_host_wall={kerf_in:.3f}in (full closed)"
            if dense_closed
            else f"gap_host_wall~{kerf_in/2:.3f}in (half p/caber canal)"
        )
        log_msg = (
            f"[VENOM-FILL] Motor: {engine_id} | hosts={stats['hosts']} "
            f"cavities={stats['cavities']} guests_out={stats['guests_out']} "
            f"filled={filled} area={area_filled / IN2_MM2:.1f}in2 "
            f"kerf={kerf_in:.3f}in gap_guest={kerf_in:.3f}in "
            f"{wall_note} "
            f"kerf_half={kerf_half:.2f}mm t={time.perf_counter() - t0:.2f}s"
        )
        print(log_msg)
        _append_ai_log(log_msg)

    stats["filled"] = filled
    stats["area_filled"] = area_filled
    if dense_closed and filled <= 0 and not stats["reverted"]:
        try:
            n_holes = sum(
                len(getattr(e["poly"], "interiors", []) or [])
                for e in hosts
            )
            max_cav = max((float(c.area) for c in all_cavs), default=0.0) / IN2_MM2
            min_g = min(
                (float(e["poly"].area) for e in guests_out),
                default=0.0,
            ) / IN2_MM2
            print(
                f"[LITE-HOLE-FILL] DEBUG no-fill | host_interiors={n_holes} "
                f"max_cav={max_cav:.1f}in2 min_guest_out={min_g:.1f}in2 "
                f"guests_out={len(guests_out)} kerf={kerf_in:.3f}in",
                flush=True,
            )
        except Exception:
            pass
    # Empuja filas dentro del canal a kerf exacto (cierra huecos flojos).
    # Dense: también con filled=0 si ya había guests dentro.
    if not stats["reverted"] and (filled > 0 or dense_closed):
        try:
            n_strip = densify_cavity_strips(
                hoja, entries, host_cavs, kerf_half, dense_closed=dense_closed
            )
            stats["strip_packed"] = int(stats.get("strip_packed") or 0) + int(n_strip or 0)
            if n_strip:
                log_msg = (
                    f"[VENOM-FILL] Motor: {engine_id} | strip_pack={n_strip} "
                    f"kerf_full={kerf_full:.2f}mm"
                )
                print(log_msg)
                _append_ai_log(log_msg)
        except Exception:
            stats.setdefault("strip_packed", 0)
    hoja["venom_fill_hosts"] = stats["hosts"]
    hoja["venom_fill_cavities"] = stats["cavities"]
    hoja["venom_fill_count"] = filled
    hoja["venom_fill_area"] = area_filled
    hoja["venom_fill_reverted"] = bool(stats["reverted"])
    return stats


def densify_cavity_strips(
    hoja: dict,
    entries: list[dict],
    host_cavs: list[tuple[dict, list]],
    kerf_half: float,
    *,
    dense_closed: bool = False,
) -> int:
    """
    Reemplaza guests ya dentro de cada cavidad en una franja compacta:
    separación guest↔guest = kerf completo (2*kerf_half), pegados a la pared
    del canal (como gravedad) pero sin huecos flojos entre sí.
    """
    kerf_full = max(kerf_half * 2.0, 1.0)
    moved = 0

    for host_e, cavs in host_cavs:
        host_metal = host_e["poly"]
        host_idx = host_e["idx"]
        open_prof = bool(host_e.get("open_profile", _host_open_profile(host_metal)))
        # Orificio cerrado (interiors) → kerf full aunque open_profile sea True (anillos).
        wall_clear = (
            kerf_full
            if (dense_closed and (_large_closed_holes(host_metal) or not open_prof))
            else kerf_half
        )
        for cav in cavs:
            guests_in = [
                e
                for e in entries
                if (not e.get("is_host")) and _guest_already_in_cavity(e["poly"], [cav])
            ]
            # Dense: también 1 guest → esquina (libera el resto del orificio).
            if len(guests_in) < 1:
                continue
            if len(guests_in) < 2 and not dense_closed:
                continue

            legal_regs = _legal_regions(cav, wall_clear)
            if not legal_regs:
                continue
            legal = max(legal_regs, key=lambda g: g.area)
            lw = legal.bounds[2] - legal.bounds[0]
            lh = legal.bounds[3] - legal.bounds[1]
            along_x = lw >= lh

            # Orden actual → re-empaquetar desde el extremo de gravedad (min).
            guests_in.sort(
                key=lambda e: (
                    e["poly"].bounds[0] if along_x else e["poly"].bounds[1],
                    e["poly"].bounds[1] if along_x else e["poly"].bounds[0],
                )
            )

            # Fila: alinear a borde legal min (pegado a pared / gravedad).
            cursor = legal.bounds[0] if along_x else legal.bounds[1]
            wall = legal.bounds[1] if along_x else legal.bounds[0]

            placed_polys: list[Polygon] = []
            guests_in_idx = {e["idx"] for e in guests_in}
            for guest_e in guests_in:
                gpoly = guest_e["poly"]
                best = None
                for angle_deg, centered in _guest_variants(gpoly):
                    gw = centered.bounds[2] - centered.bounds[0]
                    gh = centered.bounds[3] - centered.bounds[1]
                    if along_x:
                        if gw > lw + 0.5 or gh > lh + 0.5:
                            continue
                        cx = cursor
                        cy = wall
                        if cx + gw > legal.bounds[2] + 0.5:
                            continue
                        if cy + gh > legal.bounds[3] + 0.5:
                            cy = max(legal.bounds[1], legal.bounds[3] - gh)
                    else:
                        if gh > lh + 0.5 or gw > lw + 0.5:
                            continue
                        cy = cursor
                        cx = wall
                        if cy + gh > legal.bounds[3] + 0.5:
                            continue
                        if cx + gw > legal.bounds[2] + 0.5:
                            cx = max(legal.bounds[0], legal.bounds[2] - gw)

                    test = affinity.translate(centered, cx, cy)
                    # Excluir otros guests del mismo canal (aún en pose vieja) para
                    # poder re-empaquetar la franja; solo cuentan ya colocados + resto hoja.
                    others = placed_polys + [
                        e["poly"]
                        for e in entries
                        if e["idx"] != guest_e["idx"]
                        and e["idx"] != host_idx
                        and e["idx"] not in guests_in_idx
                    ]
                    if not _place_ok(test, cav, others, host_metal, kerf_half):
                        continue
                    if wall_clear > kerf_half + 1e-6:
                        try:
                            if float(test.distance(host_metal)) + 1e-6 < wall_clear:
                                continue
                        except Exception:
                            pass
                    best = (angle_deg, test, gw if along_x else gh)
                    break
                if best is None:
                    b = gpoly.bounds
                    cursor = (b[2] if along_x else b[3]) + kerf_full
                    placed_polys.append(gpoly)
                    continue

                angle_deg, test, span = best
                old = guest_e["poly"]
                if (
                    abs(old.bounds[0] - test.bounds[0]) > 0.5
                    or abs(old.bounds[1] - test.bounds[1]) > 0.5
                ):
                    _apply_rigid_pose(guest_e["p"], old, test, angle_deg)
                    guest_e["poly"] = test
                    for e in entries:
                        if e["idx"] == guest_e["idx"]:
                            e["poly"] = test
                            break
                    moved += 1
                placed_polys.append(test)
                cursor = (test.bounds[2] if along_x else test.bounds[3]) + kerf_full

    return moved


def fill_sheet_free_pockets(
    hoja: dict, engine_id: str = "default", *, force_corridor: bool = False
) -> int:
    """Reubica guests sueltos en huecos libres de placa. Llamar DESPUÉS de gravedad."""
    import copy as _copy

    t0 = time.perf_counter()
    piezas = hoja.get("piezas") or []
    if len(piezas) < 2:
        return 0
    kerf_in = float(hoja.get("kerf_usado", 0.0) or 0.0)
    kerf_half = max((kerf_in * 25.4) / 2.0, 0.5)
    try:
        from .cut_gaps_table import gaps_efectivos_para_hoja

        kt, mt = gaps_efectivos_para_hoja(hoja, kerf_fallback=kerf_in)
        if float(kt) > kerf_in:
            kerf_in = float(kt)
            hoja["kerf_usado"] = kerf_in
            kerf_half = max((kerf_in * 25.4) / 2.0, 0.5)
        plate_inset = max(float(mt) * 25.4, 0.0)
    except Exception:
        plate_inset = max(float(hoja.get("margin_usado") or 0.25) * 25.4, kerf_half)
    snapshot = _copy.deepcopy(piezas)

    entries = []
    for idx, p in enumerate(piezas):
        if _is_virtual(str(p.get("nombre") or "")):
            continue
        poly = _piece_poly(p)
        if poly is None:
            continue
        entries.append({"idx": idx, "p": p, "poly": poly, "is_host": _is_host(poly, p)})
    hosts = [e for e in entries if e["is_host"]]
    used: set[int] = set()
    moved = _fill_sheet_free_pockets(
        hoja, entries, hosts, used, kerf_half, t0, plate_inset_mm=plate_inset
    )
    corridor_n = 0
    if (force_corridor or corridor_fill_enabled()) and len(hosts) >= 2:
        corridor_n = _fill_corridor_gaps(
            hoja, entries, hosts, used, kerf_half, t0, plate_inset_mm=plate_inset
        )
        moved += corridor_n
    if moved <= 0:
        return 0
    has_overlap, detail = _sheet_has_metal_overlaps(hoja)
    if has_overlap:
        hoja["piezas"] = snapshot
        log_msg = (
            f"[VENOM-FILL] Motor: {engine_id} | sheet_pockets REVERTIDO | {detail}"
        )
        print(log_msg)
        _append_ai_log(log_msg)
        return 0
    log_msg = (
        f"[VENOM-FILL] Motor: {engine_id} | sheet_pockets={moved - corridor_n} "
        f"corridor={corridor_n} kerf_half={kerf_half:.2f}mm "
        f"t={time.perf_counter() - t0:.2f}s"
    )
    print(log_msg)
    _append_ai_log(log_msg)
    hoja["venom_sheet_pockets"] = int(moved - corridor_n)
    hoja["venom_corridor_fill"] = int(corridor_n)
    return moved


def _fill_sheet_free_pockets(
    hoja: dict,
    entries: list[dict],
    hosts: list[dict],
    used_guest_idx: set[int],
    kerf_half: float,
    t0: float,
    *,
    plate_inset_mm: float | None = None,
) -> int:
    """
    Rellena SOLO huecos internos / adyacentes al nest.

    No mueve piezas al remanente lejano (esquinas derechas): eso explotaba el bbox
    (BKT-304 en esquinas). Solo coloca si queda pegado al bloque ocupado.
    """
    from shapely.ops import unary_union

    placa_w = float(hoja.get("placa_w", 0) or 0)
    placa_h = float(hoja.get("placa_h", 0) or 0)
    if placa_w <= 0 or placa_h <= 0:
        return 0
    inset = (
        float(plate_inset_mm)
        if plate_inset_mm is not None
        else max(0.250 * 25.4, float(kerf_half))
    )

    host_idx = {h["idx"] for h in hosts}
    host_cavs: list[Polygon] = []
    for h in hosts:
        host_cavs.extend(
            list_host_cavities(h["poly"], open_profile=_host_open_profile(h["poly"]))
        )

    movable = []
    for e in entries:
        if e["idx"] in host_idx or e["idx"] in used_guest_idx:
            continue
        if host_cavs and _guest_already_in_cavity(e["poly"], host_cavs):
            continue
        movable.append(e)
    if not movable:
        return 0
    movable.sort(key=lambda e: float(e["poly"].area))

    attach_max_mm = max(25.0, kerf_half * 8.0)
    plate_area = max(placa_w * placa_h, 1.0)
    moved = 0
    max_moves = min(16, len(movable))

    for _ in range(max_moves):
        if time.perf_counter() - t0 > MAX_FILL_SECONDS:
            break

        raw_polys = [e["poly"] for e in entries if e["poly"] is not None]
        if not raw_polys:
            break
        try:
            occ_raw = unary_union(raw_polys)
            buffered = []
            for e in entries:
                try:
                    buffered.append(e["poly"].buffer(kerf_half, resolution=2, join_style=2))
                except Exception:
                    buffered.append(e["poly"])
            occ = unary_union(buffered)
            free = box(inset, inset, placa_w - inset, placa_h - inset).difference(occ)
        except Exception:
            break
        if free is None or free.is_empty or occ_raw is None or occ_raw.is_empty:
            break

        nest_minx, nest_miny, nest_maxx, nest_maxy = occ_raw.bounds
        try:
            nest_zone = box(nest_minx, nest_miny, nest_maxx, nest_maxy).buffer(
                attach_max_mm, join_style=2
            )
        except Exception:
            nest_zone = box(
                nest_minx - attach_max_mm,
                nest_miny - attach_max_mm,
                nest_maxx + attach_max_mm,
                nest_maxy + attach_max_mm,
            )

        pockets = []
        geoms = [free] if free.geom_type == "Polygon" else list(getattr(free, "geoms", []) or [])
        for g in geoms:
            if g.geom_type != "Polygon" or g.is_empty:
                continue
            a = float(g.area)
            if a < MIN_SHEET_POCKET_MM2:
                continue
            # Remanente exterior grande (lado derecho): no tocar.
            if a > 0.12 * plate_area:
                gb = g.bounds
                if gb[2] >= (placa_w - inset) - 2.0 or gb[0] > nest_maxx + attach_max_mm:
                    continue
            try:
                if not g.intersects(nest_zone):
                    continue
            except Exception:
                continue
            pockets.append(g)
        pockets.sort(key=lambda g: g.area, reverse=True)
        if not pockets:
            break

        placed_any = False
        for pocket in pockets[:8]:
            if time.perf_counter() - t0 > MAX_FILL_SECONDS:
                break
            for guest_e in movable:
                if guest_e["idx"] in used_guest_idx:
                    continue
                try:
                    inter = guest_e["poly"].intersection(pocket)
                    if getattr(inter, "area", 0) > float(guest_e["poly"].area) * 0.85:
                        continue
                except Exception:
                    pass

                gpoly = guest_e["poly"]
                if float(gpoly.area) > float(pocket.area) * 0.95:
                    continue

                others = [e["poly"] for e in entries if e["idx"] != guest_e["idx"]]
                try:
                    others_u = unary_union(others) if others else occ_raw
                except Exception:
                    others_u = occ_raw

                best = None
                for angle_deg, centered in _guest_variants(gpoly):
                    cands = _candidate_translations(centered, pocket, kerf_half)
                    if not cands:
                        continue
                    for cx, cy in cands:
                        test = affinity.translate(centered, cx, cy)
                        if not _place_ok(test, pocket, others, None, kerf_half):
                            continue
                        try:
                            dist = float(test.distance(others_u))
                        except Exception:
                            continue
                        if dist > attach_max_mm:
                            continue
                        if test.bounds[0] > nest_maxx + attach_max_mm:
                            continue
                        ok_hosts = True
                        for h in hosts:
                            try:
                                if (
                                    getattr(test.intersection(h["poly"]), "area", 0)
                                    > METAL_OVERLAP_EPS_MM2
                                ):
                                    ok_hosts = False
                                    break
                            except Exception:
                                ok_hosts = False
                                break
                        if not ok_hosts:
                            continue
                        if best is None or dist < best[0]:
                            best = (dist, angle_deg, test)

                if best is None:
                    continue
                dist, angle_deg, test = best
                old_poly = guest_e["poly"]
                try:
                    old_dist = float(old_poly.distance(others_u))
                    if old_dist <= attach_max_mm and dist > old_dist + 1.0:
                        continue
                except Exception:
                    pass

                _apply_rigid_pose(guest_e["p"], old_poly, test, angle_deg)
                guest_e["poly"] = test
                for e in entries:
                    if e["idx"] == guest_e["idx"]:
                        e["poly"] = test
                        break
                used_guest_idx.add(guest_e["idx"])
                moved += 1
                placed_any = True
                break
            if placed_any:
                break
        if not placed_any:
            break

    return moved


def _iter_poly_geoms(geom) -> list[Polygon]:
    if geom is None or getattr(geom, "is_empty", True):
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    out = []
    for g in getattr(geom, "geoms", []) or []:
        if g.geom_type == "Polygon" and not g.is_empty:
            out.append(g)
        elif g.geom_type == "MultiPolygon":
            out.extend(_iter_poly_geoms(g))
    return out


def _corridor_pockets_between_hosts(
    hosts: list[dict],
    free,
    kerf_half: float,
) -> list[Polygon]:
    """
    Extrae pasillos entre hosts apilados/adosados.

    El free-space conectado al remanente derecho suele ser un solo polígono enorme;
    el pocket clásico lo descarta. Aquí se recorta el gap geométrico entre marcos.
    """
    if len(hosts) < 2 or free is None or getattr(free, "is_empty", True):
        return []

    min_gap = max(MIN_CORRIDOR_GAP_MM, kerf_half * 2.5)
    pockets: list[Polygon] = []

    def _clip_gap(x0: float, y0: float, x1: float, y1: float) -> None:
        if x1 - x0 < MIN_CORRIDOR_LEN_MM * 0.5 and y1 - y0 < MIN_CORRIDOR_LEN_MM * 0.5:
            return
        if x1 <= x0 or y1 <= y0:
            return
        try:
            inter = free.intersection(box(x0, y0, x1, y1))
        except Exception:
            return
        for g in _iter_poly_geoms(inter):
            a = float(g.area)
            if a < MIN_CORRIDOR_POCKET_MM2:
                continue
            gb = g.bounds
            gw = gb[2] - gb[0]
            gh = gb[3] - gb[1]
            # Pasillo: alargado (L/W alto) o gap estrecho entre marcos.
            aspect = max(gw, gh) / max(min(gw, gh), 1.0)
            if max(gw, gh) < MIN_CORRIDOR_LEN_MM and aspect < 2.0:
                continue
            pockets.append(g)

    # Gaps verticales (marcos apilados en Y — caso GIGA / GENE-WFM).
    hs_y = sorted(
        hosts,
        key=lambda h: (h["poly"].bounds[1] + h["poly"].bounds[3]) * 0.5,
    )
    for lower, upper in zip(hs_y, hs_y[1:]):
        bl, bu = lower["poly"].bounds, upper["poly"].bounds
        y0, y1 = float(bl[3]), float(bu[1])
        if y1 - y0 < min_gap:
            continue
        ox0, ox1 = max(bl[0], bu[0]), min(bl[2], bu[2])
        if ox1 - ox0 >= MIN_CORRIDOR_LEN_MM:
            x0, x1 = ox0, ox1
        else:
            x0, x1 = min(bl[0], bu[0]), max(bl[2], bu[2])
        pad = kerf_half * 0.2
        _clip_gap(x0, y0 + pad, x1, y1 - pad)

    # Gaps horizontales (marcos lado a lado en X).
    hs_x = sorted(
        hosts,
        key=lambda h: (h["poly"].bounds[0] + h["poly"].bounds[2]) * 0.5,
    )
    for left, right in zip(hs_x, hs_x[1:]):
        bl, br = left["poly"].bounds, right["poly"].bounds
        x0, x1 = float(bl[2]), float(br[0])
        if x1 - x0 < min_gap:
            continue
        oy0, oy1 = max(bl[1], br[1]), min(bl[3], br[3])
        if oy1 - oy0 >= MIN_CORRIDOR_LEN_MM:
            y0, y1 = oy0, oy1
        else:
            y0, y1 = min(bl[1], br[1]), max(bl[3], br[3])
        pad = kerf_half * 0.2
        _clip_gap(x0 + pad, y0, x1 - pad, y1)

    return _dedup_cavities(pockets)


def _fill_corridor_gaps(
    hoja: dict,
    entries: list[dict],
    hosts: list[dict],
    used_guest_idx: set[int],
    kerf_half: float,
    t0: float,
    *,
    plate_inset_mm: float | None = None,
) -> int:
    """Mueve guests (cluster derecho) a pasillos entre hosts. Opt-in vía flag."""
    from shapely.ops import unary_union

    placa_w = float(hoja.get("placa_w", 0) or 0)
    placa_h = float(hoja.get("placa_h", 0) or 0)
    if placa_w <= 0 or placa_h <= 0 or len(hosts) < 2:
        return 0
    inset = (
        float(plate_inset_mm)
        if plate_inset_mm is not None
        else max(0.250 * 25.4, float(kerf_half))
    )

    host_idx = {h["idx"] for h in hosts}
    movable = [e for e in entries if e["idx"] not in host_idx and e["idx"] not in used_guest_idx]
    if not movable:
        return 0
    # Preferir piecitas del lado derecho (cluster) y luego las más chicas.
    movable.sort(key=lambda e: (-float(e["poly"].bounds[0]), float(e["poly"].area)))

    moved = 0
    deadline = t0 + MAX_CORRIDOR_SECONDS
    max_moves = min(MAX_CORRIDOR_MOVES, len(movable))

    for _ in range(max_moves):
        if time.perf_counter() > deadline or time.perf_counter() - t0 > MAX_FILL_SECONDS + 15.0:
            break

        raw_polys = [e["poly"] for e in entries if e["poly"] is not None]
        if not raw_polys:
            break
        try:
            buffered = []
            for e in entries:
                try:
                    buffered.append(e["poly"].buffer(kerf_half, resolution=2, join_style=2))
                except Exception:
                    buffered.append(e["poly"])
            occ = unary_union(buffered)
            free = box(inset, inset, placa_w - inset, placa_h - inset).difference(occ)
        except Exception:
            break
        if free is None or free.is_empty:
            break

        pockets = _corridor_pockets_between_hosts(hosts, free, kerf_half)
        if not pockets:
            break
        # Llenar primero pasillos más estrechos (mejor “encaje” visual / densidad).
        pockets.sort(
            key=lambda g: (
                min(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1]),
                -float(g.area),
            )
        )

        placed_any = False
        for pocket in pockets[:12]:
            if time.perf_counter() > deadline:
                break
            for guest_e in movable:
                if guest_e["idx"] in used_guest_idx:
                    continue
                gpoly = guest_e["poly"]
                # Ya está mayormente dentro de este pasillo.
                try:
                    inter = gpoly.intersection(pocket)
                    if getattr(inter, "area", 0) > float(gpoly.area) * 0.85:
                        continue
                except Exception:
                    pass
                if float(gpoly.area) > float(pocket.area) * 0.98:
                    continue

                others = [e["poly"] for e in entries if e["idx"] != guest_e["idx"]]
                best = None
                for angle_deg, centered in _guest_variants(gpoly):
                    cands = _candidate_translations(centered, pocket, kerf_half)
                    if not cands:
                        continue
                    for cx, cy in cands:
                        test = affinity.translate(centered, cx, cy)
                        if not _place_ok(test, pocket, others, None, kerf_half):
                            continue
                        # Rechazar solape sólido con metal de hosts.
                        ok_hosts = True
                        for h in hosts:
                            try:
                                if (
                                    getattr(test.intersection(h["poly"]), "area", 0)
                                    > METAL_OVERLAP_EPS_MM2
                                ):
                                    ok_hosts = False
                                    break
                            except Exception:
                                ok_hosts = False
                                break
                        if not ok_hosts:
                            continue
                        # Preferir meter hacia el centro del pasillo / más a la izquierda.
                        score = float(test.centroid.x) + 0.15 * abs(
                            test.centroid.y - (pocket.bounds[1] + pocket.bounds[3]) * 0.5
                        )
                        if best is None or score < best[0]:
                            best = (score, angle_deg, test)

                if best is None:
                    continue
                _score, angle_deg, test = best
                old_poly = guest_e["poly"]
                try:
                    old_in = float(old_poly.intersection(pocket).area) / max(
                        float(old_poly.area), 1.0
                    )
                    new_in = float(test.intersection(pocket).area) / max(
                        float(test.area), 1.0
                    )
                except Exception:
                    old_in, new_in = 0.0, 0.0
                if new_in < 0.90:
                    continue
                if old_in >= 0.85:
                    continue
                # No empujar guests más a la derecha (remanente).
                if float(test.centroid.x) > float(old_poly.centroid.x) + 25.0:
                    continue

                _apply_rigid_pose(guest_e["p"], old_poly, test, angle_deg)
                guest_e["poly"] = test
                for e in entries:
                    if e["idx"] == guest_e["idx"]:
                        e["poly"] = test
                        break
                used_guest_idx.add(guest_e["idx"])
                moved += 1
                placed_any = True
                break
            if placed_any:
                break
        if not placed_any:
            break

    return moved


def _append_ai_log(msg: str) -> None:
    try:
        from datetime import datetime
        from pathlib import Path

        p = Path(__file__).parent.parent.parent / "_logs" / "AI_ACTIVITY.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def count_pieces_in_cavities(hoja: dict) -> int:
    piezas = hoja.get("piezas") or []
    hosts = []
    guests = []
    for p in piezas:
        if _is_virtual(str(p.get("nombre") or "")):
            continue
        poly = _piece_poly(p)
        if poly is None:
            continue
        if _is_host(poly, p):
            hosts.append(poly)
        else:
            guests.append(poly)
    cavs = []
    for h in hosts:
        cavs.extend(list_host_cavities(h, open_profile=_host_open_profile(h)))
    if not cavs:
        return 0
    return sum(1 for g in guests if _guest_already_in_cavity(g, cavs))
