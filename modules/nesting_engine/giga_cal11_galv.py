"""Motor oculto Cal 11 / 0.11811 / GALVANIZADO (GIGA).

No vive en el selector. Si el switch de Configuración Global está ON y el
grupo es Cal 11 Galv (0.11811 / 0.1196 Fluidstack), usa giga_cal11_galv.
Hosts: perfiles I VFM (AutoDXF GIGA metal, no cobre). OFF = motor del selector.
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


def plate_too_small_for_vfm(w_mm: float, h_mm: float) -> bool:
    """Retazos < alto de la I (~12\") no merecen void-first ni pasillo."""
    return min(float(w_mm or 0.0), float(h_mm or 0.0)) < 280.0


def pick_giga_sim_plates(
    placas: list,
    *,
    format_used: dict | None = None,
    format_limits: dict | None = None,
) -> list:
    """Una candidata por hoja: la más alta *disponible* (stock + alto VFM).

    Antes solo devolvía la más alta; si esa ya estaba al límite Herinox el
    nest paraba con piezas pendientes (EMPAQUE-STOP-PARCIAL / faltan N).
    """
    if not placas:
        return []
    if len(placas) == 1:
        return list(placas)

    def _short(p: dict) -> float:
        return min(float(p.get("w") or 0.0), float(p.get("h") or 0.0))

    def _fmt_key(p: dict) -> tuple:
        # Evitar import circular con manager.
        w = float(p.get("w") or 0.0)
        h = float(p.get("h") or 0.0)
        a, b = (w, h) if w >= h else (h, w)
        return (round(a, 1), round(b, 1))

    ranked = sorted(placas, key=_short, reverse=True)
    used = format_used if isinstance(format_used, dict) else {}
    limits = format_limits if isinstance(format_limits, dict) else {}
    for p in ranked:
        w = float(p.get("w") or 0.0)
        h = float(p.get("h") or 0.0)
        if plate_too_small_for_vfm(w, h):
            continue
        fk = _fmt_key(p)
        lim = limits.get(fk)
        if lim is not None and int(used.get(fk, 0)) >= int(lim):
            continue
        return [p]
    # Ninguna libre con alto VFM: última opción la más alta (el caller reportará stop).
    return [ranked[0]]


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
    """Perfil I abierto (VFM): metal ≤72% del AABB. HFM/soleras no.

    AutoDXF GIGA (BOARD 4/5/6/11 + Fluidstack metal, sin cobre): solo
    GENE-VFM-20-101/102; bahías 8.77\" y 3.74\". Sin geometría, el nombre.
    """
    nom = str(nombre or "").upper()
    if "VFM" not in nom:
        return False
    if poly is None or getattr(poly, "is_empty", True):
        return _is_vfm20_name(nom)
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


def _is_giga_structural_bar(nombre: str | None) -> bool:
    u = str(nombre or "").upper()
    tags = ("HFM", "SIHC", "SIVC", "SHC", "WFM", "VS-20", "VS-")
    return any(t in u for t in tags)


def order_giga_pool_python(piezas: list) -> list:
    """Paridad con packer_giga_cal11.cpp::order_giga_pool (candado / docs).

    Con estructurales: 1 par + barras + chicos + resto I.
    Sin estructurales y ≥2 pares: torre de todos los pares, luego inyección.
    """
    a101: list = []
    a102: list = []
    other_i: list = []
    bars: list = []
    rest: list = []
    for p in piezas or []:
        nom = str(p.get("nombre") or "").upper()
        if _is_vfm20_name(nom) and "-101" in nom:
            a101.append(p)
        elif _is_vfm20_name(nom) and "-102" in nom:
            a102.append(p)
        elif _is_vfm20_name(nom):
            other_i.append(p)
        elif _is_giga_structural_bar(nom):
            bars.append(p)
        else:
            rest.append(p)

    def _ar(p: dict) -> float:
        try:
            return float(p.get("area") or 0.0)
        except Exception:
            return 0.0

    other_i.sort(key=_ar, reverse=True)
    bars.sort(key=_ar, reverse=True)
    rest.sort(key=_ar, reverse=True)
    n_pairs = max(len(a101), len(a102))
    out: list = []
    # Torre: máx. 2 pares por hoja (~48\"). El resto de I va después de la
    # inyección para no tumbar el nest ni dejar GS sin placa.
    if not bars and n_pairs >= 2:
        tower_n = min(2, n_pairs)
        for i in range(tower_n):
            if i < len(a101):
                out.append(a101[i])
            if i < len(a102):
                out.append(a102[i])
        out.extend(rest)
        for i in range(tower_n, n_pairs):
            if i < len(a101):
                out.append(a101[i])
            if i < len(a102):
                out.append(a102[i])
        out.extend(other_i)
        return out
    if a101:
        out.append(a101[0])
    if a102:
        out.append(a102[0])
    out.extend(bars)
    out.extend(rest)
    for i in range(1, n_pairs):
        if i < len(a101):
            out.append(a101[i])
        if i < len(a102):
            out.append(a102[i])
    out.extend(other_i)
    return out


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
        18.0 <= short <= 340.0
        and long >= 90.0
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


_CAV_CACHE: dict[tuple, list] = {}


def _channel_cavs_cached(poly, kerf_full: float):
    """Misma I local (AutoDXF) no vuelve a list_host_cavities (~1 s/host)."""
    from .venom_hole_fill import list_host_cavities

    minx, miny, maxx, maxy = poly.bounds
    key = (
        round(minx, 0),
        round(miny, 0),
        round(maxx - minx, 1),
        round(maxy - miny, 1),
        round(float(poly.area), 1),
        round(float(kerf_full), 2),
    )
    hit = _CAV_CACHE.get(key)
    if hit is not None:
        return hit
    cavs = [
        c
        for c in list_host_cavities(poly, open_profile=True)
        if _channel_like(c, kerf_full)
    ]
    _CAV_CACHE[key] = cavs
    if len(_CAV_CACHE) > 48:
        _CAV_CACHE.pop(next(iter(_CAV_CACHE)))
    return cavs


def _fits_legal_box(guest_poly, legal) -> bool:
    lw = legal.bounds[2] - legal.bounds[0]
    lh = legal.bounds[3] - legal.bounds[1]
    gw = guest_poly.bounds[2] - guest_poly.bounds[0]
    gh = guest_poly.bounds[3] - guest_poly.bounds[1]
    return (gw <= lw + 0.5 and gh <= lh + 0.5) or (
        gh <= lw + 0.5 and gw <= lh + 0.5
    )


def _strip_anchor_candidates(
    minx: float, miny: float, maxx: float, maxy: float, gw: float, gh: float
) -> list[tuple[float, float]]:
    """Esquinas + barrido a lo largo del eje largo (estilo Mudar en tira)."""
    lw, lh = maxx - minx, maxy - miny
    cands: list[tuple[float, float]] = [
        (minx, miny),
        (minx, maxy - gh),
        (maxx - gw, miny),
        (maxx - gw, maxy - gh),
    ]
    mid_y = miny + max(0.0, (lh - gh) * 0.5)
    mid_x = minx + max(0.0, (lw - gw) * 0.5)
    if lw >= lh:
        step = max(12.0, float(gw) * 0.42)
        x = minx
        while x + gw <= maxx + 0.75:
            for y in (miny, mid_y, maxy - gh):
                cands.append((x, y))
            x += step
    else:
        step = max(12.0, float(gh) * 0.42)
        y = miny
        while y + gh <= maxy + 0.75:
            for x in (minx, mid_x, maxx - gw):
                cands.append((x, y))
            y += step
    return cands


def _try_strip_place(guest_poly, free, host_metal, placed, kerf_full: float):
    """Primer hueco a lo largo de la tira; barrido denso, no grilla Venom."""
    from shapely.affinity import translate as shp_translate
    from .venom_hole_fill import _blf_positions, _guest_variants, _iter_free_parts

    need = max(50.0, float(guest_poly.area) * 0.3)
    for angle_deg, centered in _guest_variants(guest_poly):
        gw = centered.bounds[2] - centered.bounds[0]
        gh = centered.bounds[3] - centered.bounds[1]
        for part in _iter_free_parts(free, min_area=need):
            minx, miny, maxx, maxy = part.bounds
            lw, lh = maxx - minx, maxy - miny
            if gw > lw + 0.5 or gh > lh + 0.5:
                continue
            cands = _strip_anchor_candidates(minx, miny, maxx, maxy, gw, gh)
            cands.extend(_blf_positions(part, gw, gh, min(gw, gh) * 0.35))
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
                tb = test.bounds
                k = float(kerf_full)
                for op in placed:
                    ob = op.bounds
                    if tb[2] + k < ob[0] or ob[2] + k < tb[0]:
                        continue
                    if tb[3] + k < ob[1] or ob[3] + k < tb[1]:
                        continue
                    try:
                        if float(test.distance(op)) + 1e-3 < k:
                            ok_gap = False
                            break
                    except Exception:
                        ok_gap = False
                        break
                if not ok_gap:
                    continue
                return angle_deg, test
    return None


def _is_densify_guest_name(nombre: str | None) -> bool:
    """Invitados chicos/medianos que deben llenar huecos VFM (Fase B / Mudar)."""
    u = str(nombre or "").upper()
    if "VFM-20" in u or "HFM-" in u or "SIVC" in u or "SIHC" in u or "VS-20" in u:
        return False
    tags = (
        "GS-",
        "BKT-RLG",
        "BKT-287",
        "BKT-297",
        "BKT-299",
        "BKT-304",
        "BKT-153",
        "BKT-306",
        "BKT-223",
        "BKT-239",
        "BKT-240",
        "BKT-259",
    )
    return any(t in u for t in tags)


def prefill_vfm_void_cargo(
    piezas: list,
    kerf_in: float,
) -> tuple[list, dict[str, Any]]:
    """Void-first VFM-20: invitados en bahías abiertas (coords locales del host).

    El MC no ve esas bahías; el cargo viaja pegado y se expande tras colocar.
    """
    from .venom_hole_fill import (
        _apply_rigid_pose,
        _is_virtual,
        _piece_poly,
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
    time_budget = 16.0

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
    hosts.sort(
        key=lambda e: (
            0
            if "-101" in str(e["p"].get("nombre") or "").upper()
            else 1
            if "-102" in str(e["p"].get("nombre") or "").upper()
            else 2
        )
    )
    stats["seed_hosts"] = len(hosts)
    if not hosts or not guests:
        return pool, stats

    slots: list[dict] = []
    for h in hosts:
        if time.perf_counter() - t0 > time_budget:
            break
        cavs = _channel_cavs_cached(h["poly"], kerf_full)
        stats["bays"] += len(cavs)
        bays: list = []
        for cav in cavs:
            bays.extend(_bay_free_regions(h["poly"], cav, kerf_full))
        if not bays:
            continue
        slots.append(
            {
                "h": h,
                "bays": bays,
                "cargo": list(h["p"].get("_void_cargo") or []),
                "placed": [],
            }
        )
    if not slots:
        return pool, stats

    used: set[int] = set()
    filled = 0

    def _place_one(st: dict) -> bool:
        nonlocal filled
        if time.perf_counter() - t0 > time_budget:
            return False
        host_e = st["h"]
        for bi, free in enumerate(list(st["bays"])):
            if free is None or getattr(free, "is_empty", True):
                continue
            pool_g = [
                e
                for e in guests
                if e["idx"] not in used
                and not e["p"].get("_void_prefilled")
                and _fits_legal_box(e["poly"], free)
            ]
            pool_g.sort(key=lambda e: float(e["poly"].area))
            hit_e = None
            pose = None
            for guest_e in pool_g:
                if time.perf_counter() - t0 > time_budget:
                    return False
                hit = _try_strip_place(
                    guest_e["poly"], free, host_e["poly"], st["placed"], kerf_full
                )
                if hit is None:
                    continue
                hit_e, pose = guest_e, hit
                break
            if hit_e is None or pose is None:
                continue
            angle_deg, test = pose
            old = hit_e["poly"]
            _apply_rigid_pose(hit_e["p"], old, test, angle_deg)
            hit_e["poly"] = test
            hit_e["p"]["_void_prefilled"] = True
            hit_e["p"]["_void_parent"] = str(host_e["p"].get("_void_uid") or "")
            st["cargo"].append(copy.deepcopy(hit_e["p"]))
            used.add(hit_e["idx"])
            st["placed"].append(test)
            filled += 1
            try:
                st["bays"][bi] = free.difference(
                    test.buffer(kerf_full, resolution=4, join_style=2)
                )
            except Exception:
                st["bays"][bi] = None
            return True
        return False

    # Reparte invitados a TODAS las I (si no, P9 se traga el patio y P11–P24
    # quedan 4 I vacías al 29%).
    n_g = max(1, len(guests))
    n_h = max(1, len(slots))
    cap_rr = max(1, (n_g + n_h - 1) // n_h)
    moved = True
    while moved and time.perf_counter() - t0 <= time_budget:
        moved = False
        for st in slots:
            if len(st["cargo"]) >= cap_rr:
                continue
            if _place_one(st):
                moved = True

    for st in slots:
        if st["cargo"]:
            st["h"]["p"]["_void_cargo"] = st["cargo"]

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


def _vfm_hosts_on_sheet(piezas: list) -> list[tuple[int, Any]]:
    from .venom_hole_fill import _piece_poly

    hosts: list[tuple[int, Any]] = []
    for i, p in enumerate(piezas):
        if p.get("_void_prefilled"):
            continue
        poly = _piece_poly(p)
        if poly is None:
            continue
        if not _is_vfm20_name(p.get("nombre")):
            continue
        hosts.append((i, poly))
    return hosts


def _stacked_vfm_pair(
    ia: int, pa, ib: int, pb, hosts: list[tuple[int, Any]]
) -> bool:
    """Par apilado (solape X) sin otro VFM entre medias en Y."""
    aa, bb = pa.bounds, pb.bounds
    ox = min(aa[2], bb[2]) - max(aa[0], bb[0])
    if ox < 50.0:
        return False
    y1, y2 = float(pa.centroid.y), float(pb.centroid.y)
    if abs(y1 - y2) < 25.0:
        return False
    lo, hi = (y1, y2) if y1 < y2 else (y2, y1)
    ox0, ox1 = max(aa[0], bb[0]), min(aa[2], bb[2])
    for ic, pc in hosts:
        if ic in (ia, ib):
            continue
        cy = float(pc.centroid.y)
        if not (lo + 8.0 < cy < hi - 8.0):
            continue
        cc = pc.bounds
        if min(ox1, cc[2]) - max(ox0, cc[0]) > 80.0:
            return False
    return True


def _sandwich_pockets(pa, pb, kerf_full: float):
    """Aire entre dos VFM (izq/der de la T), no el hueco AABB entre puntas."""
    from shapely.geometry import box as shp_box
    from shapely.ops import unary_union

    from .venom_hole_fill import _iter_free_parts

    aa, bb = pa.bounds, pb.bounds
    span = shp_box(
        max(aa[0], bb[0]),
        min(aa[1], bb[1]),
        min(aa[2], bb[2]),
        max(aa[3], bb[3]),
    )
    try:
        host_u = unary_union([pa, pb])
        legal = span.difference(host_u.buffer(float(kerf_full), resolution=4, join_style=2))
    except Exception:
        return None, []
    y1, y2 = float(pa.centroid.y), float(pb.centroid.y)
    lo, hi = (y1, y2) if y1 < y2 else (y2, y1)
    pockets = []
    for part in _iter_free_parts(legal, min_area=80.0):
        cy = float(part.centroid.y)
        if not (lo < cy < hi):
            continue
        minx, miny, maxx, maxy = part.bounds
        if min(maxx - minx, maxy - miny) < 18.0:
            continue
        pockets.append(part)
    if not pockets:
        return host_u, []
    try:
        free = pockets[0] if len(pockets) == 1 else unary_union(pockets)
    except Exception:
        free = pockets[0]
    return host_u, [free]


def _pull_from_pool_into_legal(
    hoja: dict,
    pool: list,
    legal,
    host_metal,
    kerf_full: float,
    t0: float,
    budget: float,
    *,
    small_first: bool,
) -> tuple[int, Any]:
    """Saca invitados de restos (pool), no del patio ya nesteado."""
    from .venom_hole_fill import _apply_rigid_pose, _piece_poly

    n = 0
    if not pool or legal is None or getattr(legal, "is_empty", True):
        return n, legal
    placed: list = []
    for pz in hoja.get("piezas") or []:
        gp = _piece_poly(pz)
        if gp is None:
            continue
        placed.append(gp)
        try:
            if float(gp.intersection(legal).area) > 0.55 * float(gp.area):
                legal = legal.difference(
                    gp.buffer(kerf_full, resolution=4, join_style=2)
                )
        except Exception:
            pass
    while pool and legal is not None and not getattr(legal, "is_empty", True):
        if time.perf_counter() - t0 > budget:
            break
        idxs = list(range(len(pool)))

        def _prio(i: int) -> tuple:
            pz = pool[i]
            gp = _piece_poly(pz)
            ar = float(gp.area) if gp is not None else 0.0
            dens = 0 if _is_densify_guest_name(pz.get("nombre")) else 1
            # small_first: densify chicos primero; si no, grandes primero
            return (dens, ar if small_first else -ar)

        idxs.sort(key=_prio)
        placed_one = False
        tried = 0
        for i in idxs:
            pz = pool[i]
            if pz.get("_void_prefilled"):
                continue
            gp = _piece_poly(pz)
            if gp is None or _is_vfm_i_host(str(pz.get("nombre") or ""), gp):
                continue
            if not _fits_legal_box(gp, legal):
                continue
            tried += 1
            if tried > 64:
                break
            hit = _try_strip_place(gp, legal, host_metal, placed, kerf_full)
            if hit is None:
                continue
            angle_deg, test = hit
            guest = pool.pop(i)
            old = _piece_poly(guest)
            if old is None:
                break
            _apply_rigid_pose(guest, old, test, angle_deg)
            guest["_giga_gap_fill"] = True
            hoja.setdefault("piezas", []).append(guest)
            placed.append(test)
            n += 1
            placed_one = True
            try:
                hoja["area_usada"] = float(hoja.get("area_usada") or 0.0) + float(
                    guest.get("area") or test.area or 0.0
                )
            except Exception:
                pass
            try:
                legal = legal.difference(
                    test.buffer(kerf_full, resolution=4, join_style=2)
                )
            except Exception:
                legal = None
            break
        if not placed_one:
            break
    return n, legal


def _pull_from_sheet_into_legal(
    hoja: dict,
    legal,
    host_metal,
    host_idx: int,
    kerf_full: float,
    t0: float,
    budget: float,
) -> tuple[int, Any]:
    """Hueco que quedó vacío: mueve patio de ESTA hoja (H68 con 304 al lado)."""
    from .venom_hole_fill import _apply_rigid_pose, _piece_poly

    n = 0
    piezas = hoja.get("piezas") or []
    if legal is None or getattr(legal, "is_empty", True):
        return n, legal
    placed: list = []
    for j, pz in enumerate(piezas):
        gp = _piece_poly(pz)
        if gp is None:
            continue
        placed.append(gp)
        if j == host_idx:
            continue
        try:
            if float(gp.intersection(legal).area) > 0.55 * float(gp.area):
                legal = legal.difference(
                    gp.buffer(kerf_full, resolution=4, join_style=2)
                )
        except Exception:
            pass
    used: set[int] = set()
    while legal is not None and not getattr(legal, "is_empty", True):
        if time.perf_counter() - t0 > budget:
            break
        cands: list[tuple[int, Any]] = []
        for i, pz in enumerate(piezas):
            if i == host_idx or i in used:
                continue
            if pz.get("_void_prefilled") or pz.get("_giga_gap_fill"):
                continue
            gp = _piece_poly(pz)
            if gp is None or _is_vfm_i_host(str(pz.get("nombre") or ""), gp):
                continue
            try:
                if float(gp.intersection(legal).area) > 0.55 * float(gp.area):
                    continue
            except Exception:
                pass
            if not _fits_legal_box(gp, legal):
                continue
            dens = 0 if _is_densify_guest_name(pz.get("nombre")) else 1
            cands.append((dens, float(gp.area), i, gp))
        cands.sort(key=lambda t: (t[0], t[1]))
        hit_i = None
        pose = None
        for _d, _a, i, gp in cands:
            hit = _try_strip_place(gp, legal, host_metal, placed, kerf_full)
            if hit is None:
                continue
            hit_i, pose = i, hit
            break
        if hit_i is None or pose is None:
            break
        guest = piezas[hit_i]
        old = _piece_poly(guest)
        if old is None:
            break
        angle_deg, test = pose
        _apply_rigid_pose(guest, old, test, angle_deg)
        guest["_giga_gap_fill"] = True
        used.add(hit_i)
        n += 1
        placed.append(test)
        try:
            legal = legal.difference(
                test.buffer(kerf_full, resolution=4, join_style=2)
            )
        except Exception:
            break
    return n, legal


def fill_vfm_facing_gap(hoja: dict, pool: list | None = None) -> dict[str, Any]:
    """Rellena el aire entre dos VFM: restos primero, luego patio (como Mudar)."""
    stats: dict[str, Any] = {"facing": 0, "pairs": 0, "pockets": 0, "from_sheet": 0}
    piezas = list(hoja.get("piezas") or [])
    if not piezas:
        return stats
    live_pool = pool if isinstance(pool, list) else []
    kerf_in = float(hoja.get("kerf_usado", 0.150) or 0.150)
    kerf_full = max(kerf_in * 25.4, 1.0)

    hosts = _vfm_hosts_on_sheet(piezas)
    if len(hosts) < 2:
        return stats

    t0 = time.perf_counter()
    budget = 10.0
    for a in range(len(hosts)):
        for b in range(a + 1, len(hosts)):
            if time.perf_counter() - t0 > budget:
                break
            ia, pa = hosts[a]
            ib, pb = hosts[b]
            if not _stacked_vfm_pair(ia, pa, ib, pb, hosts):
                continue
            host_u, frees = _sandwich_pockets(pa, pb, kerf_full)
            if host_u is None or not frees:
                continue
            stats["pairs"] += 1
            for legal0 in frees:
                if time.perf_counter() - t0 > budget:
                    break
                stats["pockets"] += 1
                legal = legal0
                n, legal = _pull_from_pool_into_legal(
                    hoja,
                    live_pool,
                    legal,
                    host_u,
                    kerf_full,
                    t0,
                    budget,
                    small_first=True,
                )
                stats["facing"] += n
                n2, _legal = _pull_from_sheet_into_legal(
                    hoja, legal, host_u, ia, kerf_full, t0, budget
                )
                stats["facing"] += n2
                stats["from_sheet"] += n2
    if stats["facing"] or stats["pairs"]:
        print(
            f"[GIGA-CAL11] facing_gap moved={stats['facing']} "
            f"(sheet={stats['from_sheet']}) pairs={stats['pairs']} "
            f"pockets={stats['pockets']} t={time.perf_counter() - t0:.2f}s",
            flush=True,
        )
    return stats


def fill_vfm_host_bays_from_sheet(hoja: dict, pool: list | None = None) -> dict[str, Any]:
    """Rellena bahías VFM: restos primero, patio solo si el hueco sigue vacío."""
    from .venom_hole_fill import _piece_poly, list_host_cavities

    stats: dict[str, Any] = {"bays": 0, "moved": 0}
    kerf_in = float(hoja.get("kerf_usado", 0.150) or 0.150)
    kerf_full = max(kerf_in * 25.4, 1.0)
    t0 = time.perf_counter()
    live_pool = pool if isinstance(pool, list) else []

    for ia, p in enumerate(list(hoja.get("piezas") or [])):
        if time.perf_counter() - t0 > 8.0:
            break
        if p.get("_void_prefilled"):
            continue
        poly = _piece_poly(p)
        if poly is None:
            continue
        if not _is_vfm_i_host(str(p.get("nombre") or ""), poly):
            continue
        cavs = [
            c
            for c in list_host_cavities(poly, open_profile=True)
            if _channel_like(c, kerf_full)
        ]
        stats["bays"] += len(cavs)
        for cav in cavs:
            for legal in _bay_free_regions(poly, cav, kerf_full):
                n, legal = _pull_from_pool_into_legal(
                    hoja, live_pool, legal, poly, kerf_full, t0, 8.0, small_first=True
                )
                stats["moved"] += n
                n2, _legal = _pull_from_sheet_into_legal(
                    hoja, legal, poly, ia, kerf_full, t0, 8.0
                )
                stats["moved"] += n2
    if stats["moved"] or stats["bays"]:
        print(
            f"[GIGA-CAL11] host_bays moved={stats['moved']} bays={stats['bays']} "
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
    """Fase B por hoja: saturar facing + bahías (restos y patio), como Mudar."""
    del engine_id
    stats: dict[str, Any] = {
        "cavities": 0,
        "corridors": 0,
        "pockets": 0,
        "channels": 0,
        "facing": 0,
        "host_bays": 0,
        "phase_b_passes": 0,
        "moved": 0,
    }
    if not isinstance(hoja, dict) or not (hoja.get("piezas") or []):
        return stats
    live_pool = pool if isinstance(pool, list) else []
    t0 = time.perf_counter()
    budget = 12.0
    # Hasta 3 pases: el primero mete restos; los siguientes empujan patio
    # al pasillo/bahía que quedó con aire (caso H61).
    for _pass in range(3):
        if time.perf_counter() - t0 > budget:
            break
        moved_pass = 0
        try:
            gap = fill_vfm_facing_gap(hoja, pool=live_pool)
            n_f = int(gap.get("facing") or 0)
            stats["facing"] = int(stats["facing"]) + n_f
            stats["corridors"] = max(
                int(stats["corridors"]), int(gap.get("pairs") or 0)
            )
            stats["pockets"] = max(
                int(stats["pockets"]), int(gap.get("pockets") or 0)
            )
            moved_pass += n_f
        except Exception as exc:
            stats["error"] = str(exc)
            print(f"[GIGA-CAL11] facing_gap skip: {exc}", flush=True)
        try:
            bays = fill_vfm_host_bays_from_sheet(hoja, pool=live_pool)
            n_b = int(bays.get("moved") or 0)
            stats["host_bays"] = int(stats["host_bays"]) + n_b
            stats["channels"] = max(
                int(stats["channels"]), int(bays.get("bays") or 0)
            )
            moved_pass += n_b
        except Exception as exc:
            stats["error_bays"] = str(exc)
            print(f"[GIGA-CAL11] host_bays skip: {exc}", flush=True)
        stats["phase_b_passes"] = int(stats["phase_b_passes"]) + 1
        stats["moved"] = int(stats["moved"]) + moved_pass
        if moved_pass <= 0:
            break
    if stats["moved"] or stats["phase_b_passes"]:
        print(
            f"[GIGA-CAL11] phase_b hoja moved={stats['moved']} "
            f"passes={stats['phase_b_passes']} "
            f"facing={stats['facing']} bays={stats['host_bays']} "
            f"t={time.perf_counter() - t0:.2f}s",
            flush=True,
        )
    return stats


def _guest_in_vfm_hole(guest_poly, hoja: dict, kerf_full: float) -> bool:
    """True si el invitado ya está mayormente dentro de bahía/pasillo VFM."""
    from .venom_hole_fill import list_host_cavities

    piezas = list(hoja.get("piezas") or [])
    hosts = _vfm_hosts_on_sheet(piezas)
    if not hosts:
        return False
    for _i, hpoly in hosts:
        for cav in list_host_cavities(hpoly, open_profile=True):
            if not _channel_like(cav, kerf_full):
                continue
            try:
                if float(guest_poly.intersection(cav).area) > 0.45 * float(
                    guest_poly.area
                ):
                    return True
            except Exception:
                pass
    if len(hosts) >= 2:
        for a in range(len(hosts)):
            for b in range(a + 1, len(hosts)):
                ia, pa = hosts[a]
                ib, pb = hosts[b]
                if not _stacked_vfm_pair(ia, pa, ib, pb, hosts):
                    continue
                _hu, frees = _sandwich_pockets(pa, pb, kerf_full)
                for fr in frees or []:
                    try:
                        if float(guest_poly.intersection(fr).area) > 0.45 * float(
                            guest_poly.area
                        ):
                            return True
                    except Exception:
                        pass
    return False


def _iter_open_vfm_targets(hoja: dict, kerf_full: float):
    """Yield (legal_free, host_metal, host_idx) con aire usable."""
    from .venom_hole_fill import list_host_cavities

    piezas = list(hoja.get("piezas") or [])
    hosts = _vfm_hosts_on_sheet(piezas)
    # Facing primero (pasillo entre T): es el caso H61.
    for a in range(len(hosts)):
        for b in range(a + 1, len(hosts)):
            ia, pa = hosts[a]
            ib, pb = hosts[b]
            if not _stacked_vfm_pair(ia, pa, ib, pb, hosts):
                continue
            host_u, frees = _sandwich_pockets(pa, pb, kerf_full)
            if host_u is None:
                continue
            for fr in frees or []:
                if fr is None or getattr(fr, "is_empty", True):
                    continue
                if float(getattr(fr, "area", 0.0) or 0.0) < 80.0:
                    continue
                yield fr, host_u, ia
    for ia, hpoly in hosts:
        cavs = [
            c
            for c in list_host_cavities(hpoly, open_profile=True)
            if _channel_like(c, kerf_full)
        ]
        for cav in cavs:
            for fr in _bay_free_regions(hpoly, cav, kerf_full):
                if fr is None or getattr(fr, "is_empty", True):
                    continue
                if float(getattr(fr, "area", 0.0) or 0.0) < 80.0:
                    continue
                yield fr, hpoly, ia


def densify_giga_group_phase_b(
    hojas: list, *, budget_s: float = 90.0
) -> dict[str, Any]:
    """Pase global tipo Mudar: mueve GS/BKT de hojas donantes a huecos VFM abiertos.

    No re-empaqueta el patio. Solo piezas densify-guest que están fuera de hueco.
    """
    from .venom_hole_fill import _apply_rigid_pose, _piece_poly

    stats: dict[str, Any] = {"cross_moved": 0, "recipients": 0, "donors_used": 0}
    if not isinstance(hojas, list) or len(hojas) < 2:
        return stats
    t0 = time.perf_counter()
    budget_s = max(5.0, float(budget_s or 90.0))

    def _kerf(h: dict) -> float:
        return max(float(h.get("kerf_usado", 0.150) or 0.150) * 25.4, 1.0)

    def _donor_score(h: dict) -> tuple:
        pcs = list(h.get("piezas") or [])
        n = len(pcs)
        n_vfm = sum(
            1 for p in pcs if "VFM-20" in str(p.get("nombre") or "").upper()
        )
        # Preferir hojas pobres / casi solo I (el 29%) como donantes.
        return (0 if n <= 4 else 1, 0 if n_vfm >= 2 and n <= 6 else 1, n)

    # Repetir hasta que no quepa más o se acabe el presupuesto.
    while time.perf_counter() - t0 < budget_s:
        progress = False
        recipients: list[tuple[int, dict]] = []
        for i, h in enumerate(hojas):
            if not isinstance(h, dict):
                continue
            k = _kerf(h)
            if any(True for _ in _iter_open_vfm_targets(h, k)):
                recipients.append((i, h))
        stats["recipients"] = max(stats["recipients"], len(recipients))
        # Hojas con sandwich primero (más aire útil).
        recipients.sort(
            key=lambda t: -sum(
                1
                for p in (t[1].get("piezas") or [])
                if "VFM-20" in str(p.get("nombre") or "").upper()
            )
        )
        for ri, rh in recipients:
            if time.perf_counter() - t0 > budget_s:
                break
            k = _kerf(rh)
            placed_hit = False
            for legal0, host_u, host_idx in _iter_open_vfm_targets(rh, k):
                if time.perf_counter() - t0 > budget_s:
                    break
                legal = legal0
                # Restar ocupantes ya en el hueco.
                for pz in rh.get("piezas") or []:
                    gp = _piece_poly(pz)
                    if gp is None:
                        continue
                    try:
                        if float(gp.intersection(legal).area) > 0.55 * float(gp.area):
                            legal = legal.difference(
                                gp.buffer(k, resolution=4, join_style=2)
                            )
                    except Exception:
                        pass
                if legal is None or getattr(legal, "is_empty", True):
                    continue
                donor_order = sorted(
                    (
                        (di, dh)
                        for di, dh in enumerate(hojas)
                        if di != ri and isinstance(dh, dict)
                    ),
                    key=lambda t: _donor_score(t[1]),
                )
                for di, dh in donor_order:
                    if time.perf_counter() - t0 > budget_s:
                        break
                    dk = _kerf(dh)
                    best_j = None
                    best_poly = None
                    best_area = 1e99
                    for j, pz in enumerate(list(dh.get("piezas") or [])):
                        if not _is_densify_guest_name(pz.get("nombre")):
                            continue
                        gp = _piece_poly(pz)
                        if gp is None:
                            continue
                        if _guest_in_vfm_hole(gp, dh, dk):
                            continue
                        if not _fits_legal_box(gp, legal):
                            continue
                        ar = float(gp.area)
                        if ar < best_area:
                            best_area = ar
                            best_j = j
                            best_poly = gp
                    if best_j is None or best_poly is None:
                        continue
                    # Obstáculos en destino = todas las piezas actuales.
                    placed = []
                    for pz in rh.get("piezas") or []:
                        gp = _piece_poly(pz)
                        if gp is not None:
                            placed.append(gp)
                    hit = _try_strip_place(best_poly, legal, host_u, placed, k)
                    if hit is None:
                        continue
                    angle_deg, test = hit
                    donor_pcs = list(dh.get("piezas") or [])
                    guest = donor_pcs.pop(best_j)
                    dh["piezas"] = donor_pcs
                    old = _piece_poly(guest)
                    if old is None:
                        donor_pcs.insert(best_j, guest)
                        dh["piezas"] = donor_pcs
                        continue
                    _apply_rigid_pose(guest, old, test, angle_deg)
                    guest["_giga_gap_fill"] = True
                    guest["_giga_phase_b_cross"] = True
                    rh.setdefault("piezas", []).append(guest)
                    try:
                        ar = float(guest.get("area") or test.area or 0.0)
                        rh["area_usada"] = float(rh.get("area_usada") or 0.0) + ar
                        dh["area_usada"] = max(
                            0.0, float(dh.get("area_usada") or 0.0) - ar
                        )
                    except Exception:
                        pass
                    stats["cross_moved"] += 1
                    stats["donors_used"] += 1
                    progress = True
                    placed_hit = True
                    break
                if placed_hit:
                    break
            if placed_hit:
                break
        if not progress:
            break
    stats["t"] = time.perf_counter() - t0
    if stats["cross_moved"]:
        print(
            f"[GIGA-CAL11] phase_b grupo cross_moved={stats['cross_moved']} "
            f"recipients={stats['recipients']} t={stats['t']:.2f}s",
            flush=True,
        )
    return stats


def tabla_kerf_margin() -> tuple[float, float]:
    """Cal 11 Galv: pieza↔pieza 0.150\" y placa↔pieza 0.250\" (tabla oficial)."""
    try:
        from .cut_gaps_table import gaps_for_calibre

        k, m, _ = gaps_for_calibre("0.11811")
        return float(k), float(m)
    except Exception:
        return 0.150, float(PLATE_TO_PIECE_DEFAULT_IN)
