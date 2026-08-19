"""Motor oculto Cal 11 / 0.11811 / GALVANIZADO (GIGA).

No vive en el selector. Si el switch de Configuración Global está ON y el
grupo es 0.11811_GALVANIZADO, usa giga_cal11_galv. OFF = motor del selector.
"""
from __future__ import annotations

import copy
import time
from typing import Any

from .cut_gaps_table import PLATE_TO_PIECE_DEFAULT_IN
from .nest_engine_context import (
    ENGINE_GIGA_CAL11_GALV,
    get_pack_group_clave,
)

ENGINE_ID = ENGINE_GIGA_CAL11_GALV
CLAVE_CANON = "0.11811_GALVANIZADO"
FRAME_TAGS = ("VFM", "HFM", "WFM", "VTN", "WFN")

# Cal 11 planta: 0.11811; también 0.118 / 0.1196 / gauge 11.
_CAL11_THICK = (0.11811, 0.118, 0.1196, 0.119)
_CAL11_TOKENS = ("0.11811", "0.118", "0.1196", "0.119")


def is_giga_cal11_galv_clave(clave: str | None) -> bool:
    """True para el grupo Cal 11 galvanizado (GIGA), no A36 del mismo espesor."""
    s = str(clave or "").strip().upper().replace("  ", " ")
    if not s:
        return False
    if "GALV" not in s:
        return False
    if s == CLAVE_CANON.upper() or s.startswith("0.11811_GALV"):
        return True
    cal, _, mat = s.partition("_")
    cal = cal.strip()
    mat = mat.strip()
    if "GALV" not in mat:
        return False
    if cal in {"11", "CAL11", "CAL 11"}:
        return True
    if cal in {t.upper() for t in _CAL11_TOKENS}:
        return True
    try:
        thk = float(cal)
    except Exception:
        return False
    return any(abs(thk - t) <= 0.0035 for t in _CAL11_THICK)


def clave_desde_debug_tag(tag: str | None) -> str:
    s = str(tag or "")
    if "clave=" in s:
        return s.split("clave=", 1)[1].split("|", 1)[0].strip()
    if s.startswith("preflight|"):
        parts = s.split("|")
        if len(parts) >= 2:
            return parts[1].strip()
    return ""


def is_giga_cal11_motor_enabled() -> bool:
    try:
        from .nest_runtime_prefs import is_giga_cal11_galv_enabled

        return bool(is_giga_cal11_galv_enabled())
    except Exception:
        return False


def should_force_giga_engine(clave: str | None = None) -> bool:
    """Switch ON y clave Cal 11 Galv → motor nativo. OFF → Ultra/Lite de siempre."""
    if not is_giga_cal11_motor_enabled():
        return False
    return is_giga_cal11_galv_clave(clave if clave is not None else get_pack_group_clave())


# Compat tests / código viejo: el overlay Python ya no existe.
should_overlay_pack = should_force_giga_engine


def engine_id_for_renest(clave: str | None) -> str | None:
    """Motor fijo de renest; None = preguntar al usuario / motor del selector."""
    if should_force_giga_engine(clave):
        return ENGINE_ID
    return None


def engine_id_for_group(clave: str | None, fallback: str | None = None) -> str:
    if should_force_giga_engine(clave):
        return ENGINE_ID
    return str(fallback or "")


def is_frame_piece(pieza: dict | None) -> bool:
    nom = str((pieza or {}).get("nombre") or "").upper()
    return any(tag in nom for tag in FRAME_TAGS)


def _is_vfm_i_host(nombre: str, poly) -> bool:
    nom = str(nombre or "").upper()
    if "VFM" not in nom:
        return False
    if poly is None or getattr(poly, "is_empty", True):
        return "VFM-20" in nom
    minx, miny, maxx, maxy = poly.bounds
    bbox_a = max((maxx - minx) * (maxy - miny), 1.0)
    return float(poly.area) / bbox_a <= 0.72


def _is_vfm20_name(nombre: str) -> bool:
    nom = str(nombre or "").upper()
    return "VFM-20" in nom or ("VFM" in nom and ("-101" in nom or "-102" in nom))


def partition_vfm_sheet_quota(piezas: list) -> tuple[list, list]:
    """Esta hoja: 1×101 + 1×102. El resto de I no entra al MC (no llenan el alto)."""
    a101: list = []
    a102: list = []
    other_i: list = []
    rest: list = []
    for p in piezas or []:
        nom = str(p.get("nombre") or "").upper()
        if not _is_vfm20_name(nom):
            rest.append(p)
            continue
        if "-101" in nom:
            a101.append(p)
        elif "-102" in nom:
            a102.append(p)
        else:
            other_i.append(p)
    seed: list = []
    held: list = []
    if a101:
        seed.append(a101[0])
        held.extend(a101[1:])
    if a102:
        seed.append(a102[0])
        held.extend(a102[1:])
    held.extend(other_i)
    return seed + rest, held


def restore_unplaced_void_cargo(hoja: dict, mc_pool: list, restos: list) -> int:
    """Todo cargo que no se expandió a la hoja vuelve a restos (nada se pierde)."""
    del hoja
    n_back = 0
    for p in mc_pool or []:
        cargo = list(p.get("_void_cargo") or [])
        if not cargo:
            continue
        p["_void_cargo"] = []
        for g in cargo:
            g2 = copy.deepcopy(g)
            g2.pop("_void_prefilled", None)
            g2.pop("_void_parent", None)
            restos.append(g2)
            n_back += 1
    if n_back:
        print(f"[GIGA-CAL11] cargo_a_restos={n_back}", flush=True)
    return n_back


def _channel_like(cav, kerf_full: float = 0.150 * 25.4) -> bool:
    """Bahías con alto legal ≥ ~2.76\" (tira 3.74\" / bolsa 8.77\"). No el gutter 2.69\"."""
    minx, miny, maxx, maxy = cav.bounds
    w = float(maxx - minx)
    h = float(maxy - miny)
    short, long = min(w, h), max(w, h)
    area = float(getattr(cav, "area", 0.0) or 0.0)
    legal_short = short - 2.0 * float(kerf_full)
    return (
        18.0 <= short <= 260.0
        and long >= 200.0
        and legal_short >= 70.0
        and area >= (5.0 * 25.4 * 25.4)
    )


def _bay_free_regions(host_metal, cavity, kerf_full: float):
    """Rectángulo inscrito de la bahía (AABB - kerf), no el C residual."""
    from shapely.geometry import box as shp_box

    k = float(kerf_full)
    minx, miny, maxx, maxy = cavity.bounds
    if (maxx - minx) < 2.0 * k + 8.0 or (maxy - miny) < 2.0 * k + 8.0:
        return []
    legal = shp_box(minx + k, miny + k, maxx - k, maxy - k)
    if legal.is_empty or float(legal.area) < 40.0:
        return []
    return [legal]


def _fits_legal_box(guest_poly, legal) -> bool:
    lw = legal.bounds[2] - legal.bounds[0]
    lh = legal.bounds[3] - legal.bounds[1]
    gw = guest_poly.bounds[2] - guest_poly.bounds[0]
    gh = guest_poly.bounds[3] - guest_poly.bounds[1]
    return (gw <= lw + 0.5 and gh <= lh + 0.5) or (
        gh <= lw + 0.5 and gw <= lh + 0.5
    )


def _try_strip_place(guest_poly, free, host_metal, placed, kerf_full: float):
    """Primer hueco BLF a lo largo de la tira; pocas poses, no grilla Venom."""
    from shapely.affinity import translate as shp_translate

    from .venom_hole_fill import _guest_variants, _iter_free_parts

    need = max(50.0, float(guest_poly.area) * 0.3)
    for angle_deg, centered in _guest_variants(guest_poly):
        gw = centered.bounds[2] - centered.bounds[0]
        gh = centered.bounds[3] - centered.bounds[1]
        for part in _iter_free_parts(free, min_area=need):
            minx, miny, maxx, maxy = part.bounds
            lw, lh = maxx - minx, maxy - miny
            if gw > lw + 0.5 or gh > lh + 0.5:
                continue
            along_x = lw >= lh
            cands: list[tuple[float, float]] = [
                (minx, miny),
                (minx, maxy - gh),
                (maxx - gw, miny),
                (maxx - gw, maxy - gh),
                ((minx + maxx - gw) * 0.5, (miny + maxy - gh) * 0.5),
            ]
            if along_x:
                step = max(8.0, min(gw * 0.55, 50.0))
                x = minx
                n = 0
                while x <= maxx - gw + 0.5 and n < 24:
                    cands.append((x, miny))
                    cands.append((x, maxy - gh))
                    x += step
                    n += 1
            else:
                step = max(8.0, min(gh * 0.55, 50.0))
                y = miny
                n = 0
                while y <= maxy - gh + 0.5 and n < 24:
                    cands.append((minx, y))
                    cands.append((maxx - gw, y))
                    y += step
                    n += 1
            seen: set[tuple[float, float]] = set()
            for cx, cy in cands:
                key = (round(cx, 1), round(cy, 1))
                if key in seen:
                    continue
                seen.add(key)
                test = shp_translate(centered, cx, cy)
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
                try:
                    if float(test.distance(host_metal)) + 1e-3 < kerf_full:
                        continue
                except Exception:
                    continue
                ok_gap = True
                for op in placed:
                    try:
                        if float(test.distance(op)) + 1e-3 < kerf_full:
                            ok_gap = False
                            break
                    except Exception:
                        ok_gap = False
                        break
                if not ok_gap:
                    continue
                return angle_deg, test
    return None


def prefill_vfm_void_cargo(
    piezas: list,
    kerf_in: float,
) -> tuple[list, dict[str, Any]]:
    """Void-first VFM-20: invitados en bahías abiertas (coords locales del host).

    El MC no ve esas bahías; el cargo viaja pegado y se expande tras colocar.
    """
    from shapely.ops import unary_union

    from .venom_hole_fill import (
        _apply_rigid_pose,
        _is_virtual,
        _piece_poly,
        list_host_cavities,
    )

    stats: dict[str, Any] = {
        "mode": "vfm_void_first",
        "hosts": 0,
        "bays": 0,
        "filled": 0,
    }
    if not piezas:
        return list(piezas or []), stats

    t0 = time.perf_counter()
    pool = [copy.deepcopy(p) for p in piezas]
    kerf_in = float(kerf_in or 0.150)
    kerf_full = max(kerf_in * 25.4, 1.0)
    time_budget = 6.0

    entries: list[dict] = []
    for idx, p in enumerate(pool):
        if _is_virtual(str(p.get("nombre") or "")):
            continue
        poly = _piece_poly(p)
        if poly is None:
            continue
        if p.get("poly_exact") is None:
            p["poly_exact"] = poly
        p["_void_uid"] = f"giga{idx}"
        entries.append(
            {
                "idx": idx,
                "p": p,
                "poly": poly,
                "is_host": bool(_is_vfm_i_host(str(p.get("nombre") or ""), poly)),
            }
        )

    hosts = [e for e in entries if e["is_host"]]
    guests = [e for e in entries if not e["is_host"]]
    stats["hosts"] = len(hosts)
    seed_idx: set[int] = set()
    got_101 = got_102 = False
    for e in hosts:
        nom = str(e["p"].get("nombre") or "").upper()
        if not got_101 and "-101" in nom:
            seed_idx.add(e["idx"])
            got_101 = True
        elif not got_102 and "-102" in nom:
            seed_idx.add(e["idx"])
            got_102 = True
        if got_101 and got_102:
            break
    if not seed_idx and hosts:
        seed_idx.add(hosts[0]["idx"])
    stats["seed_hosts"] = len(seed_idx)
    if not hosts or not guests:
        return pool, stats

    jobs: list[tuple[dict, Any]] = []
    for h in hosts:
        if h["idx"] not in seed_idx:
            continue
        cavs = [
            c
            for c in list_host_cavities(h["poly"], open_profile=True)
            if _channel_like(c, kerf_full)
        ]
        for cav in cavs:
            jobs.append((h, cav))
            stats["bays"] += 1
    jobs.sort(
        key=lambda t: (t[1].bounds[2] - t[1].bounds[0])
        * (t[1].bounds[3] - t[1].bounds[1]),
        reverse=True,
    )
    if not jobs:
        return pool, stats

    used: set[int] = set()
    filled = 0

    for host_e, cavity in jobs:
        if time.perf_counter() - t0 > time_budget:
            break
        host_metal = host_e["poly"]
        regions = _bay_free_regions(host_metal, cavity, kerf_full)
        if not regions:
            continue
        free = regions[0] if len(regions) == 1 else unary_union(regions)
        if free is None or getattr(free, "is_empty", True):
            continue

        cargo: list[dict] = list(host_e["p"].get("_void_cargo") or [])
        placed_polys: list = []
        for g in cargo:
            gp = _piece_poly(g)
            if gp is not None:
                placed_polys.append(gp)
                try:
                    free = free.difference(gp.buffer(kerf_full, resolution=4, join_style=2))
                except Exception:
                    pass

        pool_g = [
            e
            for e in guests
            if e["idx"] not in used
            and not e["p"].get("_void_prefilled")
            and _fits_legal_box(e["poly"], free)
        ]
        pool_g.sort(key=lambda e: float(e["poly"].area), reverse=True)

        for guest_e in pool_g:
            if time.perf_counter() - t0 > time_budget:
                break
            if guest_e["idx"] in used:
                continue
            if free is None or getattr(free, "is_empty", True):
                break
            hit = _try_strip_place(
                guest_e["poly"], free, host_metal, placed_polys, kerf_full
            )
            if hit is None:
                continue
            angle_deg, test = hit
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
            except Exception:
                break

        if cargo:
            host_e["p"]["_void_cargo"] = cargo

    mc_pool = [p for i, p in enumerate(pool) if i not in used]
    stats["filled"] = filled
    stats["t"] = time.perf_counter() - t0
    if filled or stats["bays"]:
        print(
            f"[GIGA-CAL11] void-first filled={filled} hosts={stats['hosts']} "
            f"seed={stats.get('seed_hosts', 0)} "
            f"bays={stats['bays']} pool_mc={len(mc_pool)}/{len(pool)} "
            f"kerf={kerf_in:.3f}in t={stats['t']:.2f}s",
            flush=True,
        )
    return mc_pool, stats


def expand_giga_void_cargo(hoja: dict, mc_pool: list) -> int:
    from .venom_hole_fill import expand_void_cargo_onto_hoja

    return int(
        expand_void_cargo_onto_hoja(hoja, mc_pool, engine_id=ENGINE_ID) or 0
    )


def close_stacked_vfm_pairs(hoja: dict, kerf_in: float | None = None) -> dict[str, Any]:
    """Gira 180° el 102 si hace falta y lo acerca al 101 hasta kerf (aire entre almas)."""
    from shapely.affinity import rotate as shp_rotate
    from shapely.affinity import translate as shp_translate
    from shapely.ops import unary_union

    from .venom_hole_fill import _apply_rigid_pose, _piece_poly

    stats: dict[str, Any] = {"pairs": 0, "closed": 0, "saved_in": 0.0}
    piezas = list(hoja.get("piezas") or [])
    kerf_in = float(kerf_in if kerf_in is not None else (hoja.get("kerf_usado") or 0.150))
    kerf_mm = max(kerf_in * 25.4, 1.0)

    def _host_row(p: dict) -> bool:
        return _is_vfm20_name(p.get("nombre")) and not p.get("_void_prefilled")

    ones = [
        (i, p)
        for i, p in enumerate(piezas)
        if _host_row(p) and "-101" in str(p.get("nombre") or "").upper()
    ]
    twos = [
        (i, p)
        for i, p in enumerate(piezas)
        if _host_row(p) and "-102" in str(p.get("nombre") or "").upper()
    ]
    if not ones or not twos:
        return stats

    def _blocked(test, skip: set[int]) -> bool:
        for j, pz in enumerate(piezas):
            if j in skip:
                continue
            op = _piece_poly(pz)
            if op is None:
                continue
            try:
                if float(test.intersection(op).area) > 1.0:
                    return True
                if float(test.distance(op)) + 1e-3 < kerf_mm:
                    return True
            except Exception:
                return True
        return False

    def _snap_y(a, b0, skip: set[int]):
        b = b0
        d0 = float(a.distance(b))
        if d0 <= kerf_mm + 0.3:
            return b if not _blocked(b, skip) else None
        sign = 1.0 if b.centroid.y >= a.centroid.y else -1.0
        b = shp_translate(b, 0.0, -sign * (d0 - kerf_mm))
        try:
            inter = float(a.intersection(b).area)
        except Exception:
            inter = 1.0
        if inter > 0.5 or _blocked(b, skip):
            lo, hi = 0.0, d0 - kerf_mm
            best = None
            for _ in range(18):
                mid = 0.5 * (lo + hi)
                t = shp_translate(b0, 0.0, -sign * mid)
                try:
                    inter2 = float(a.intersection(t).area)
                except Exception:
                    inter2 = 1.0
                ok = inter2 <= 0.5 and (not _blocked(t, skip)) and float(a.distance(t)) + 1e-3 >= kerf_mm - 0.2
                if ok:
                    best = t
                    lo = mid
                else:
                    hi = mid
            return best
        if float(a.distance(b)) + 1e-3 < kerf_mm - 0.2:
            return None
        return b

    used2: set[int] = set()
    for i1, p1 in ones:
        a = _piece_poly(p1)
        if a is None:
            continue
        best_pair = None
        for i2, p2 in twos:
            if i2 in used2:
                continue
            b = _piece_poly(p2)
            if b is None:
                continue
            d = float(a.centroid.distance(b.centroid))
            if best_pair is None or d < best_pair[0]:
                best_pair = (d, i2, p2, b)
        if best_pair is None:
            continue
        _, i2, p2, b0 = best_pair
        skip = {i1, i2}
        try:
            h_now = float(unary_union([a, b0]).bounds[3] - unary_union([a, b0]).bounds[1])
        except Exception:
            h_now = (a.bounds[3] - a.bounds[1]) + (b0.bounds[3] - b0.bounds[1]) + kerf_mm
        chosen = None
        for ang in (0.0, 180.0):
            b_r = b0 if ang == 0.0 else shp_rotate(b0, ang, origin="centroid")
            b_new = _snap_y(a, b_r, skip)
            if b_new is None:
                continue
            try:
                uh = unary_union([a, b_new]).bounds
            except Exception:
                continue
            h = float(uh[3] - uh[1])
            if chosen is None or h < chosen[0] - 0.5:
                chosen = (h, ang, b_new)
        stats["pairs"] += 1
        if chosen is None:
            continue
        h, ang, b_new = chosen
        saved = h_now - h
        if saved < 3.0:
            used2.add(i2)
            continue
        _apply_rigid_pose(p2, b0, b_new, ang)
        used2.add(i2)
        stats["closed"] += 1
        stats["saved_in"] = round(stats["saved_in"] + saved / 25.4, 3)
    if stats["closed"]:
        print(
            f"[GIGA-CAL11] close_pair closed={stats['closed']} "
            f"saved={stats['saved_in']:.2f}in",
            flush=True,
        )
    return stats


def fill_vfm_facing_gap(hoja: dict) -> dict[str, Any]:
    """Un rectángulo entre dos VFM apilados (aire entre almas), kerf 0.150\"."""
    from shapely.geometry import box as shp_box
    from shapely.ops import unary_union

    from .venom_hole_fill import _apply_rigid_pose, _piece_poly

    stats: dict[str, Any] = {"facing": 0, "pairs": 0}
    piezas = list(hoja.get("piezas") or [])
    if len(piezas) < 3:
        return stats
    kerf_in = float(hoja.get("kerf_usado", 0.150) or 0.150)
    kerf_full = max(kerf_in * 25.4, 1.0)

    hosts: list[tuple[int, Any]] = []
    for i, p in enumerate(piezas):
        poly = _piece_poly(p)
        if poly is None:
            continue
        if "VFM" not in str(p.get("nombre") or "").upper():
            continue
        hosts.append((i, poly))
    if len(hosts) < 2:
        return stats

    t0 = time.perf_counter()
    used: set[int] = set()
    for a in range(len(hosts)):
        for b in range(a + 1, len(hosts)):
            if time.perf_counter() - t0 > 2.0:
                break
            ia, pa = hosts[a]
            ib, pb = hosts[b]
            aa, bb = pa.bounds, pb.bounds
            ox0, ox1 = max(aa[0], bb[0]), min(aa[2], bb[2])
            if ox1 - ox0 < 50.0:
                continue
            if aa[3] <= bb[1] + 1.0:
                gy0, gy1 = aa[3], bb[1]
            elif bb[3] <= aa[1] + 1.0:
                gy0, gy1 = bb[3], aa[1]
            else:
                continue
            gap_h = gy1 - gy0
            if gap_h < kerf_full + 8.0 or gap_h > 260.0:
                continue
            stats["pairs"] += 1
            legal = shp_box(ox0, gy0 + kerf_full, ox1, gy1 - kerf_full)
            if legal.is_empty or float(legal.area) < 80.0:
                continue
            host_u = unary_union([pa, pb])
            others: list = []
            for j, pz in enumerate(piezas):
                if j in (ia, ib):
                    continue
                gp = _piece_poly(pz)
                if gp is not None:
                    others.append(gp)

            progress = True
            while progress:
                progress = False
                if time.perf_counter() - t0 > 2.0:
                    break
                best_i = None
                best_pose = None
                cands: list[tuple[int, Any]] = []
                for i, pz in enumerate(piezas):
                    if i in (ia, ib) or i in used:
                        continue
                    gp = _piece_poly(pz)
                    if gp is None or _is_vfm_i_host(str(pz.get("nombre") or ""), gp):
                        continue
                    cands.append((i, gp))
                cands.sort(key=lambda t: float(t[1].area), reverse=True)
                for i, gp in cands:
                    hit = _try_strip_place(gp, legal, host_u, others, kerf_full)
                    if hit is None:
                        continue
                    best_i, best_pose = i, hit
                    break
                if best_i is None or best_pose is None:
                    break
                angle_deg, test = best_pose
                guest = piezas[best_i]
                old = _piece_poly(guest)
                if old is None:
                    break
                _apply_rigid_pose(guest, old, test, angle_deg)
                used.add(best_i)
                others.append(test)
                stats["facing"] += 1
                progress = True
                try:
                    legal = legal.difference(
                        test.buffer(kerf_full, resolution=4, join_style=2)
                    )
                except Exception:
                    break
    if stats["facing"] or stats["pairs"]:
        print(
            f"[GIGA-CAL11] facing_gap moved={stats['facing']} pairs={stats['pairs']} "
            f"t={time.perf_counter() - t0:.2f}s",
            flush=True,
        )
    return stats


def fill_vfm_open_channels(hoja: dict, pool: list | None = None) -> dict[str, Any]:
    """Mete invitados delgados en canales de ala VFM-20 (kerf completo 0.150\")."""
    from shapely.affinity import translate as shp_translate
    from shapely.geometry import Polygon

    from .venom_hole_fill import (
        _apply_rigid_pose,
        _candidate_translations,
        _guest_already_in_cavity,
        _guest_variants,
        _legal_regions,
        _piece_poly,
        _place_ok,
        list_host_cavities,
    )

    stats: dict[str, Any] = {
        "filled": 0,
        "from_sheet": 0,
        "from_pool": 0,
        "hosts": 0,
        "channels": 0,
        "skip_fat": 0,
    }
    piezas = list(hoja.get("piezas") or [])
    if not piezas:
        return stats
    t0 = time.perf_counter()
    kerf_in = float(hoja.get("kerf_usado", 0.150) or 0.150)
    kerf_half = max((kerf_in * 25.4) / 2.0, 0.5)
    kerf_full = max(kerf_half * 2.0, 1.0)
    margin_mm = max(float(hoja.get("margin_usado") or 0.25) * 25.4, kerf_full)
    placa_w = float(hoja.get("placa_w") or 0.0)
    placa_h = float(hoja.get("placa_h") or 0.0)

    host_jobs: list[tuple[int, Any, Any]] = []
    for idx, p in enumerate(piezas):
        poly = _piece_poly(p)
        if poly is None or not _is_vfm_i_host(str(p.get("nombre") or ""), poly):
            continue
        cavs = [c for c in list_host_cavities(poly, open_profile=True) if _channel_like(c)]
        if not cavs:
            continue
        stats["hosts"] += 1
        for cav in cavs:
            host_jobs.append((idx, poly, cav))
            stats["channels"] += 1
    host_jobs.sort(key=lambda t: float(t[2].area), reverse=True)
    if not host_jobs:
        return stats

    def _min_dim(poly: Polygon) -> float:
        b = poly.bounds
        return min(b[2] - b[0], b[3] - b[1])

    def _inside_plate(poly: Polygon) -> bool:
        if placa_w <= 1.0 or placa_h <= 1.0:
            return True
        b = poly.bounds
        return (
            b[0] >= margin_mm - 0.6
            and b[1] >= margin_mm - 0.6
            and b[2] <= placa_w - margin_mm + 0.6
            and b[3] <= placa_h - margin_mm + 0.6
        )

    def _all_metal(skip_idx: int | None) -> list:
        out = []
        for i, pz in enumerate(hoja.get("piezas") or []):
            if skip_idx is not None and i == skip_idx:
                continue
            gp = _piece_poly(pz)
            if gp is not None and not gp.is_empty:
                out.append(gp)
        return out

    log_left = 16

    def _clog(msg: str) -> None:
        nonlocal log_left
        if log_left <= 0:
            return
        log_left -= 1
        print(f"[GIGA-CAL11] {msg}", flush=True)

    def _try_place(
        guest_p: dict,
        gpoly: Polygon,
        cavity,
        host_metal,
        other_raw: list,
        *,
        source: str,
        host_nom: str,
    ) -> Polygon | None:
        legal_regs = _legal_regions(cavity, kerf_full)
        if not legal_regs:
            _clog(f"canal={host_nom} legal=0 guest={guest_p.get('nombre')} no")
            return None
        lg0 = max(legal_regs, key=lambda g: g.area)
        lw = lg0.bounds[2] - lg0.bounds[0]
        lh = lg0.bounds[3] - lg0.bounds[1]
        legal_s = min(lw, lh)
        if _min_dim(gpoly) + kerf_full > legal_s + 1.0:
            stats["skip_fat"] += 1
            _clog(
                f"canal={host_nom} legal={lw:.1f}x{lh:.1f} "
                f"guest={guest_p.get('nombre')} no (gordo "
                f"{_min_dim(gpoly):.1f}+kerf>{legal_s:.1f})"
            )
            return None
        for angle_deg, centered in _guest_variants(gpoly):
            gw = centered.bounds[2] - centered.bounds[0]
            gh = centered.bounds[3] - centered.bounds[1]
            fits = any(
                (gw <= (g.bounds[2] - g.bounds[0]) + 0.5
                 and gh <= (g.bounds[3] - g.bounds[1]) + 0.5)
                or (
                    gh <= (g.bounds[2] - g.bounds[0]) + 0.5
                    and gw <= (g.bounds[3] - g.bounds[1]) + 0.5
                )
                for g in legal_regs
            )
            if not fits:
                continue
            # Tira a lo largo del canal (esquinas + paso), kerf_half → inset = kerf full.
            cands = _candidate_translations(centered, cavity, kerf_half, dense=True)
            for legal in legal_regs:
                lminx, lminy, lmaxx, lmaxy = legal.bounds
                along_x = (lmaxx - lminx) >= (lmaxy - lminy)
                if along_x:
                    cands[:0] = [
                        (lminx, lminy),
                        (lminx, lmaxy - gh),
                        (lminx, (lminy + lmaxy - gh) * 0.5),
                    ]
                else:
                    cands[:0] = [
                        (lminx, lminy),
                        (lmaxx - gw, lminy),
                        ((lminx + lmaxx - gw) * 0.5, lminy),
                    ]
            seen: set[tuple[float, float]] = set()
            for cx, cy in cands:
                key = (round(cx, 1), round(cy, 1))
                if key in seen:
                    continue
                seen.add(key)
                test = shp_translate(centered, cx, cy)
                if not _inside_plate(test):
                    continue
                if not _place_ok(test, cavity, other_raw, host_metal, kerf_half):
                    continue
                _apply_rigid_pose(guest_p, gpoly, test, angle_deg)
                _clog(
                    f"canal={host_nom} legal={lw:.1f}x{lh:.1f} "
                    f"guest={guest_p.get('nombre')} entra ({source})"
                )
                return test
        _clog(
            f"canal={host_nom} legal={lw:.1f}x{lh:.1f} guest={guest_p.get('nombre')} no"
        )
        return None

    used_sheet: set[int] = set()
    pool_list = pool if isinstance(pool, list) else []
    budget_s = 20.0
    max_place = 80

    for host_idx, host_metal, cavity in host_jobs:
        if stats["filled"] >= max_place or (time.perf_counter() - t0) > budget_s:
            break
        host_nom = str((hoja["piezas"][host_idx] or {}).get("nombre") or "VFM")
        occupants = []
        for i, pz in enumerate(hoja.get("piezas") or []):
            if i == host_idx:
                continue
            gp = _piece_poly(pz)
            if gp is None:
                continue
            if _guest_already_in_cavity(gp, [cavity]):
                occupants.append(gp)

        progress = True
        while progress:
            progress = False
            if stats["filled"] >= max_place or (time.perf_counter() - t0) > budget_s:
                break
            work = cavity
            for op in occupants:
                try:
                    work = work.difference(op.buffer(kerf_full, join_style=2))
                except Exception:
                    continue
            if work is None or work.is_empty:
                break
            if work.geom_type != "Polygon":
                geoms = [
                    g
                    for g in (getattr(work, "geoms", None) or [])
                    if g.geom_type == "Polygon" and not g.is_empty
                ]
                if not geoms:
                    break
                work = max(geoms, key=lambda g: g.area)

            sheet_cands: list[tuple[int, dict, Any]] = []
            for i, pz in enumerate(hoja.get("piezas") or []):
                if i == host_idx or i in used_sheet:
                    continue
                gp = _piece_poly(pz)
                if gp is None or _is_vfm_i_host(str(pz.get("nombre") or ""), gp):
                    continue
                if _guest_already_in_cavity(gp, [cavity]):
                    continue
                sheet_cands.append((i, pz, gp))
            sheet_cands.sort(key=lambda t: (_min_dim(t[2]), float(t[2].area)))

            placed_here = False
            for i, pz, gp in sheet_cands:
                other = _all_metal(i)
                pose = _try_place(
                    pz, gp, work, host_metal, other, source="hoja", host_nom=host_nom
                )
                if pose is None:
                    continue
                used_sheet.add(i)
                occupants.append(pose)
                stats["filled"] += 1
                stats["from_sheet"] += 1
                placed_here = True
                progress = True
                break
            if placed_here:
                continue

            pull_i = None
            for j, pz in enumerate(pool_list):
                gp = _piece_poly(pz)
                if gp is None:
                    continue
                if _is_vfm_i_host(str(pz.get("nombre") or ""), gp):
                    continue
                guest = copy.deepcopy(pz)
                gpoly = _piece_poly(guest)
                if gpoly is None:
                    continue
                other = _all_metal(None)
                pose = _try_place(
                    guest,
                    gpoly,
                    work,
                    host_metal,
                    other,
                    source="pool",
                    host_nom=host_nom,
                )
                if pose is None:
                    continue
                pull_i = j
                hoja.setdefault("piezas", []).append(guest)
                hoja["area_usada"] = float(hoja.get("area_usada") or 0.0) + float(
                    guest.get("area") or pose.area
                )
                occupants.append(pose)
                stats["filled"] += 1
                stats["from_pool"] += 1
                placed_here = True
                progress = True
                break
            if pull_i is not None:
                pool_list.pop(pull_i)
            if not placed_here:
                break

    hoja["giga_channel_fill"] = int(stats["filled"])
    if stats["filled"] or stats["channels"]:
        print(
            f"[GIGA-CAL11] canal_fill moved={stats['filled']} "
            f"from_sheet={stats['from_sheet']} from_pool={stats['from_pool']} "
            f"hosts={stats['hosts']} canales={stats['channels']} "
            f"skip_fat={stats['skip_fat']} t={time.perf_counter() - t0:.2f}s",
            flush=True,
        )
    return stats


def apply_giga_pasillo_fill(
    hoja: dict, *, engine_id: str = ENGINE_ID, pool: list | None = None
) -> dict[str, Any]:
    """Post-MC barato: solo el hueco entre dos VFM apilados. Bahías = void-first."""
    del engine_id, pool
    stats: dict[str, Any] = {
        "cavities": 0,
        "corridors": 0,
        "pockets": 0,
        "channels": 0,
        "facing": 0,
        "moved": 0,
    }
    if not isinstance(hoja, dict) or not (hoja.get("piezas") or []):
        return stats
    try:
        gap = fill_vfm_facing_gap(hoja)
        stats["facing"] = int(gap.get("facing") or 0)
        stats["corridors"] = int(gap.get("pairs") or 0)
        stats["moved"] = stats["facing"]
    except Exception as exc:
        stats["error"] = str(exc)
        print(f"[GIGA-CAL11] facing_gap skip: {exc}", flush=True)
    return stats


def tabla_kerf_margin() -> tuple[float, float]:
    """Cal 11 Galv: pieza↔pieza 0.150\" y placa↔pieza 0.250\" (tabla oficial)."""
    try:
        from .cut_gaps_table import gaps_for_calibre

        k, m, _ = gaps_for_calibre("0.11811")
        return float(k), float(m)
    except Exception:
        return 0.150, float(PLATE_TO_PIECE_DEFAULT_IN)
