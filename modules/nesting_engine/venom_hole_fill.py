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
import time
from typing import Any

from shapely import affinity
from shapely.geometry import Polygon, box

IN2_MM2 = 25.4 * 25.4
MIN_CAVITY_MM2 = 5.0 * IN2_MM2
MIN_HOST_BBOX_MM2 = 80.0 * IN2_MM2
MIN_HOST_AREA_MM2 = 40.0 * IN2_MM2
MAX_CANDIDATES_PER_GUEST = 220
MAX_GUESTS_TO_TRY = 120
MAX_FILL_SECONDS = 35.0
# Tolerancia numérica; cualquier solape real de metal se rechaza.
METAL_OVERLAP_EPS_MM2 = 0.05
MIN_SHEET_POCKET_MM2 = 8.0 * IN2_MM2


def _is_virtual(nombre: str) -> bool:
    n = str(nombre or "")
    return n.startswith(
        ("REF__", "TATUAJE__", "RETAZO_GUILLOTINA__", "RTZCU_ZONA__", "CU_CORTE__", "REMANENTE__")
    )


def _piece_poly(p: dict) -> Polygon | None:
    from .geometry_parser import reconstruir_poly_seguro

    poly = p.get("poly_exact") or p.get("poly")
    if poly is not None and hasattr(poly, "bounds") and not getattr(poly, "is_empty", True):
        if not poly.is_valid:
            poly = poly.buffer(0)
        if hasattr(poly, "geoms"):
            poly = max(poly.geoms, key=lambda g: g.area)
        if poly is not None and not poly.is_empty:
            return poly
    rings = p.get("poligonos") or []
    if rings:
        poly = reconstruir_poly_seguro(rings)
        if poly is not None and not poly.is_empty:
            return poly
    return None


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
    if "VFM" in nombre or "HFM" in nombre:
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
    if area >= 80.0 * IN2_MM2 and bbox_a >= MIN_HOST_BBOX_MM2:
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
) -> list[tuple[float, float]]:
    gw = guest_centered.bounds[2] - guest_centered.bounds[0]
    gh = guest_centered.bounds[3] - guest_centered.bounds[1]
    regions = _legal_regions(cavity, kerf_half)
    if not regions:
        return []

    cands: list[tuple[float, float]] = []
    for legal in sorted(regions, key=lambda g: g.area, reverse=True):
        lminx, lminy, lmaxx, lmaxy = legal.bounds
        if (lmaxx - lminx) + 0.5 < gw or (lmaxy - lminy) + 0.5 < gh:
            continue
        for cx in (lminx, lmaxx - gw):
            for cy in (lminy, lmaxy - gh):
                cands.append((cx, cy))
        cands.append(((lminx + lmaxx - gw) * 0.5, (lminy + lmaxy - gh) * 0.5))

        step = max(6.0, min(gw, gh, 20.0) * 0.4)
        x = lminx
        while x <= lmaxx - gw + 1e-6:
            y = lminy
            while y <= lmaxy - gh + 1e-6:
                cands.append((x, y))
                y += step
            x += step

        try:
            cav_pts = list(legal.simplify(3.0).exterior.coords)
            g_pts = list(guest_centered.simplify(3.0).exterior.coords)
            step_c = max(1, len(cav_pts) // 24)
            step_g = max(1, len(g_pts) // 10)
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
        # El filtro duro es place_ok (dentro de cavidad + sin metal).
        out.append((cx, cy))
        if len(out) >= MAX_CANDIDATES_PER_GUEST:
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
    Reglas:
      - Guest casi entero dentro de la cavidad (ya shrinkeada por kerf en candidatos).
      - Sin solape de metal con el host (sin volver a bufferizar: el shrink ya da
        holgura a la pared; buffer extra mataba el fill con kerf 0.3").
      - Entre guests: separación kerf completa (buffer kerf/2 en ambos).
    """
    try:
        c = test_poly.centroid
        if not (cavity.contains(c) or cavity.covers(c)):
            return False

        inside = test_poly.intersection(cavity)
        if getattr(inside, "area", 0) < float(test_poly.area) * 0.95:
            return False

        # Metal del host: cero solape sólido (la holgura de pared ya viene del shrink).
        if host_metal is not None and not host_metal.is_empty:
            inter_raw = test_poly.intersection(host_metal)
            if getattr(inter_raw, "area", 0) > METAL_OVERLAP_EPS_MM2:
                return False

        try:
            test_clear = test_poly.buffer(kerf_half, resolution=3, join_style=2)
        except Exception:
            test_clear = test_poly

        for other in other_raw_polys:
            if other is None or other.is_empty:
                continue
            if (
                test_clear.bounds[2] < other.bounds[0]
                or test_clear.bounds[0] > other.bounds[2]
                or test_clear.bounds[3] < other.bounds[1]
                or test_clear.bounds[1] > other.bounds[3]
            ):
                continue
            try:
                op_clear = other.buffer(kerf_half, resolution=3, join_style=2)
            except Exception:
                op_clear = other
            inter = test_clear.intersection(op_clear)
            if getattr(inter, "area", 0) > METAL_OVERLAP_EPS_MM2:
                return False
            inter_m = test_poly.intersection(other)
            if getattr(inter_m, "area", 0) > METAL_OVERLAP_EPS_MM2:
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
) -> Polygon | None:
    """Cavidad restante tras restar guests ya metidos (+ holgura kerf)."""
    free = cavity
    for op in occupants:
        if op is None or op.is_empty:
            continue
        try:
            bloated = op.buffer(kerf_half, resolution=3, join_style=2)
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


def _sheet_has_metal_overlaps(hoja: dict) -> tuple[bool, str]:
    try:
        from .sheet_integrity import hoja_tiene_solapes_metal

        # True = hay solape
        return hoja_tiene_solapes_metal(hoja, min_area_mm2=METAL_OVERLAP_EPS_MM2)
    except Exception as exc:
        return True, f"validacion_solape_no_disponible:{exc}"


def fill_host_cavities(hoja: dict, engine_id: str = "default") -> dict[str, Any]:
    t0 = time.perf_counter()
    stats: dict[str, Any] = {
        "hosts": 0,
        "cavities": 0,
        "filled": 0,
        "area_filled": 0.0,
        "engine_id": engine_id,
        "guests_out": 0,
        "reverted": False,
    }
    piezas = hoja.get("piezas") or []
    if len(piezas) < 2:
        return stats

    kerf_in = float(hoja.get("kerf_usado", 0.0) or 0.0)
    kerf_half = max((kerf_in * 25.4) / 2.0, 0.5)  # mínimo 0.5 mm de holgura

    # Snapshot para rollback si se genera solape.
    snapshot_piezas = copy.deepcopy(piezas)

    entries = []
    for idx, p in enumerate(piezas):
        if _is_virtual(str(p.get("nombre") or "")):
            continue
        poly = _piece_poly(p)
        if poly is None:
            continue
        entries.append({"idx": idx, "p": p, "poly": poly, "is_host": _is_host(poly, p)})

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

    guests_out = []
    for g in guests:
        if _guest_already_in_cavity(g["poly"], all_cavs):
            continue
        guests_out.append(g)

    def _min_dim(e: dict) -> float:
        b = e["poly"].bounds
        return min(b[2] - b[0], b[3] - b[1])

    # Primero las más delgadas: canales VFM (~2.5–3.7") solo aceptan min_dim chico.
    guests_out.sort(key=lambda e: (_min_dim(e), float(e["poly"].area)))
    guests_out = guests_out[:MAX_GUESTS_TO_TRY]
    stats["guests_out"] = len(guests_out)

    def _other_raw(host_idx: int, guest_idx: int) -> list[Polygon]:
        return [
            e["poly"]
            for e in entries
            if e["idx"] != guest_idx and e["idx"] != host_idx
        ]

    filled = 0
    area_filled = 0.0
    used_guest_idx: set[int] = set()

    cavity_jobs: list[tuple[dict, Polygon]] = []
    for h, cavs in host_cavs:
        for cav in sorted(cavs, key=lambda c: c.area, reverse=True):
            cavity_jobs.append((h, cav))

    for host_e, cavity in cavity_jobs:
        if time.perf_counter() - t0 > MAX_FILL_SECONDS:
            break
        host_metal = host_e["poly"]
        host_idx = host_e["idx"]
        cavity_occupants: list[Polygon] = []

        progress = True
        while progress:
            progress = False
            if time.perf_counter() - t0 > MAX_FILL_SECONDS:
                break
            work_cav = _cavity_minus_occupants(cavity, cavity_occupants, kerf_half)
            if work_cav is None or work_cav.is_empty:
                break
            if float(work_cav.area) < MIN_CAVITY_MM2:
                break

            for guest_e in guests_out:
                if guest_e["idx"] in used_guest_idx:
                    continue
                if time.perf_counter() - t0 > MAX_FILL_SECONDS:
                    break

                gpoly = guest_e["poly"]
                if float(gpoly.area) > float(work_cav.area) * 0.98:
                    continue
                cw = work_cav.bounds[2] - work_cav.bounds[0]
                ch = work_cav.bounds[3] - work_cav.bounds[1]

                placed = False
                legal_regs = _legal_regions(work_cav, kerf_half)
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
                            (gw + kerf_half <= cw + 0.5 and gh + kerf_half <= ch + 0.5)
                            or (gh + kerf_half <= cw + 0.5 and gw + kerf_half <= ch + 0.5)
                        ):
                            continue
                    cands = _candidate_translations(centered, work_cav, kerf_half)
                    if not cands:
                        continue

                    others = _other_raw(host_idx, guest_e["idx"])
                    for cx, cy in cands:
                        test = affinity.translate(centered, cx, cy)
                        if not _place_ok(test, work_cav, others, host_metal, kerf_half):
                            continue

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
        log_msg = (
            f"[VENOM-FILL] Motor: {engine_id} | hosts={stats['hosts']} "
            f"cavities={stats['cavities']} guests_out={stats['guests_out']} "
            f"filled={filled} area={area_filled / IN2_MM2:.1f}in2 "
            f"kerf={kerf_in:.3f}in gap_guest={kerf_in:.3f}in "
            f"gap_host_wall~{kerf_in/2:.3f}in (half p/caber canal) "
            f"kerf_half={kerf_half:.2f}mm t={time.perf_counter() - t0:.2f}s"
        )
        print(log_msg)
        _append_ai_log(log_msg)

    stats["filled"] = filled
    stats["area_filled"] = area_filled
    # Empuja filas dentro del canal a kerf exacto (cierra huecos horizontales flojos).
    if filled > 0 and not stats["reverted"]:
        try:
            n_strip = densify_cavity_strips(hoja, entries, host_cavs, kerf_half)
            stats["strip_packed"] = n_strip
            if n_strip:
                log_msg = (
                    f"[VENOM-FILL] Motor: {engine_id} | strip_pack={n_strip} "
                    f"kerf_full={kerf_half * 2:.2f}mm"
                )
                print(log_msg)
                _append_ai_log(log_msg)
        except Exception:
            stats["strip_packed"] = 0
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
        for cav in cavs:
            guests_in = [
                e
                for e in entries
                if (not e.get("is_host")) and _guest_already_in_cavity(e["poly"], [cav])
            ]
            if len(guests_in) < 2:
                continue

            legal_regs = _legal_regions(cav, kerf_half)
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


def fill_sheet_free_pockets(hoja: dict, engine_id: str = "default") -> int:
    """Reubica guests sueltos en huecos libres de placa. Llamar DESPUÉS de gravedad."""
    import copy as _copy

    t0 = time.perf_counter()
    piezas = hoja.get("piezas") or []
    if len(piezas) < 2:
        return 0
    kerf_in = float(hoja.get("kerf_usado", 0.0) or 0.0)
    kerf_half = max((kerf_in * 25.4) / 2.0, 0.5)
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
    moved = _fill_sheet_free_pockets(hoja, entries, hosts, used, kerf_half, t0)
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
        f"[VENOM-FILL] Motor: {engine_id} | sheet_pockets={moved} "
        f"kerf_half={kerf_half:.2f}mm t={time.perf_counter() - t0:.2f}s"
    )
    print(log_msg)
    _append_ai_log(log_msg)
    hoja["venom_sheet_pockets"] = moved
    return moved


def _fill_sheet_free_pockets(
    hoja: dict,
    entries: list[dict],
    hosts: list[dict],
    used_guest_idx: set[int],
    kerf_half: float,
    t0: float,
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
            free = box(kerf_half, kerf_half, placa_w - kerf_half, placa_h - kerf_half).difference(occ)
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
                if gb[2] >= (placa_w - kerf_half) - 2.0 or gb[0] > nest_maxx + attach_max_mm:
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
