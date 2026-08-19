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
        return False
    minx, miny, maxx, maxy = poly.bounds
    bbox_a = max((maxx - minx) * (maxy - miny), 1.0)
    return float(poly.area) / bbox_a <= 0.72


def _channel_like(cav) -> bool:
    """Bahías abiertas del VFM-20: ala ~3.74\" y bolsa ~8.77\" junto a la T."""
    minx, miny, maxx, maxy = cav.bounds
    w = float(maxx - minx)
    h = float(maxy - miny)
    short, long = min(w, h), max(w, h)
    area = float(getattr(cav, "area", 0.0) or 0.0)
    # Manual planta: BKT-304/153/GS caben en la bolsa 8.77\", no solo en 3.74\".
    return 18.0 <= short <= 260.0 and long >= 200.0 and area >= (5.0 * 25.4 * 25.4)


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
    """Llena ensenadas, pasillos entre marcos y canales de ala VFM (siempre ON)."""
    stats: dict[str, Any] = {
        "cavities": 0,
        "corridors": 0,
        "pockets": 0,
        "channels": 0,
        "channel_sheet": 0,
        "channel_pool": 0,
    }
    if not isinstance(hoja, dict) or not (hoja.get("piezas") or []):
        return stats
    try:
        from .venom_hole_fill import fill_host_cavities, fill_sheet_free_pockets

        cav = fill_host_cavities(hoja, engine_id=engine_id, dense_closed=True) or {}
        stats["cavities"] = int(cav.get("filled") or 0)
        cav_open = fill_host_cavities(hoja, engine_id=engine_id, dense_closed=False) or {}
        stats["cavities"] += int(cav_open.get("filled") or 0)
        moved = int(
            fill_sheet_free_pockets(
                hoja, engine_id=engine_id, force_corridor=True
            )
            or 0
        )
        stats["pockets"] = int(hoja.get("venom_sheet_pockets") or 0)
        stats["corridors"] = int(hoja.get("venom_corridor_fill") or 0)
        stats["moved"] = moved
    except Exception as exc:
        stats["error"] = str(exc)
        print(f"[GIGA-CAL11] pasillo_fill skip: {exc}", flush=True)
    try:
        ch = fill_vfm_open_channels(hoja, pool)
        stats["channels"] = int(ch.get("filled") or 0)
        stats["channel_sheet"] = int(ch.get("from_sheet") or 0)
        stats["channel_pool"] = int(ch.get("from_pool") or 0)
        stats["channel_skip_fat"] = int(ch.get("skip_fat") or 0)
    except Exception as exc:
        stats["channel_error"] = str(exc)
        print(f"[GIGA-CAL11] canal_fill skip: {exc}", flush=True)
    return stats


def tabla_kerf_margin() -> tuple[float, float]:
    """Cal 11 Galv: pieza↔pieza 0.150\" y placa↔pieza 0.250\" (tabla oficial)."""
    try:
        from .cut_gaps_table import gaps_for_calibre

        k, m, _ = gaps_for_calibre("0.11811")
        return float(k), float(m)
    except Exception:
        return 0.150, float(PLATE_TO_PIECE_DEFAULT_IN)
