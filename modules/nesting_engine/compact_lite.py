"""
Compact-lite — densificar sin Venom full.

1) Band-close (cerrar pasillos entre columnas/filas)
2) Backfill obligatorio del remanente L antes de abrir hoja nueva

Opt-out: ARGA_NEST_COMPACT=0
"""
from __future__ import annotations

import copy
import os
import time
from collections import Counter
from typing import Any


def compact_enabled() -> bool:
    """Default ON. Opt-out: ARGA_NEST_COMPACT=0."""
    v = (os.environ.get("ARGA_NEST_COMPACT") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def apply_band_compact(hoja: dict, engine_id: str = "default") -> dict[str, Any]:
    """Cierra aire entre hileras/columnas (force, independiente de Venom)."""
    stats: dict[str, Any] = {"enabled": compact_enabled(), "skipped": True}
    if not compact_enabled() or not isinstance(hoja, dict):
        return stats
    if not (hoja.get("piezas") or []):
        return stats
    try:
        from .venom_band_close import close_inter_band_gaps

        out = close_inter_band_gaps(hoja, engine_id=engine_id, force=True) or {}
        out["source"] = "compact_lite"
        hoja["compact_lite_band"] = out
        return out
    except Exception as exc:
        stats["error"] = str(exc)
        return stats


def _piece_fits_zone_bbox(poly, w_z: float, h_z: float, tol: float = 8.0) -> bool:
    """True si alguna rotación 0/90 cabe en el bbox del hueco."""
    if poly is None:
        return False
    try:
        bx0, by0, bx1, by1 = poly.bounds
    except Exception:
        return False
    w_p = float(bx1 - bx0)
    h_p = float(by1 - by0)
    return (w_p <= w_z + tol and h_p <= h_z + tol) or (
        h_p <= w_z + tol and w_p <= h_z + tol
    )


def _catalog_voids(zonas: list, kerf_mm: float) -> list[dict]:
    """
    Inventario de huecos: área exacta + bbox + ranking.
    Pasillos/estrechos primero; best-fit por área después.
    """
    from .manager import _polygon_usable_for_limite

    out: list[dict] = []
    for zona in zonas or []:
        try:
            zona_pack = zona.buffer(-kerf_mm, join_style=2) if kerf_mm > 0 else zona
            if getattr(zona_pack, "is_empty", True):
                zona_pack = zona
        except Exception:
            zona_pack = zona
        zona_pack = _polygon_usable_for_limite(zona_pack)
        if zona_pack is None:
            continue
        minx, miny, maxx, maxy = zona_pack.bounds
        w_z = float(maxx - minx)
        h_z = float(maxy - miny)
        if w_z < 8.0 or h_z < 8.0:
            continue
        area = float(zona_pack.area)
        narrow = min(w_z, h_z)
        wide = max(w_z, h_z)
        aspect = wide / max(narrow, 1.0)
        # Pasillo: alargado y no patio gigante.
        is_corridor = aspect >= 2.2 and narrow <= 450.0
        out.append(
            {
                "geom": zona_pack,
                "area": area,
                "w": w_z,
                "h": h_z,
                "narrow": narrow,
                "aspect": aspect,
                "corridor": is_corridor,
                # Sort: corridors first, then smaller area (llenar huecos justos antes).
                "rank": (0 if is_corridor else 1, area),
            }
        )
    out.sort(key=lambda v: v["rank"])
    return out


def _pieces_fitting_void(pool: list, void: dict, area_factor: float = 0.95) -> list:
    """Piezas cuya área y bbox caben en el hueco; best-fit (menos desperdicio de área)."""
    area_z = float(void["area"])
    w_z, h_z = float(void["w"]), float(void["h"])
    fitted = []
    for p in pool:
        area_p = float(p.get("area", 0) or 0)
        if area_p <= 0 or area_p > area_z * area_factor:
            continue
        poly = p.get("poly")
        if poly is None:
            continue
        if not _piece_fits_zone_bbox(poly, w_z, h_z, tol=10.0):
            continue
        waste = area_z - area_p
        fitted.append((waste, area_p, p))
    fitted.sort(key=lambda t: (t[0], -t[1]))
    return [t[2] for t in fitted]


def _subtract_placed(pool: list, placed: Counter) -> list:
    """Quita del pool solo las piezas recién colocadas (por nombre)."""
    restante = Counter(placed or {})
    out = []
    for p in pool or []:
        nom = str(p.get("nombre") or "")
        if restante.get(nom, 0) > 0:
            restante[nom] -= 1
            continue
        out.append(copy.deepcopy(p))
    return out


def backfill_remnant_into_sheet(
    hoja: dict,
    leftovers: list,
    w_placa: float,
    h_placa: float,
    kerf: float,
    margin: float,
    opt: str,
    corner: str,
    *,
    mc_iterations: int = 1,
    max_passes: int = 3,
    clave: str = "",
    engine_id: str = "default",
) -> list:
    """
    Obliga a meter en el remanente libre todo lo que quepa (bbox + MC con limite).
    Devuelve leftovers que aún no caben. No abre hoja nueva.
    """
    if not compact_enabled():
        return list(leftovers or [])
    if not leftovers or not isinstance(hoja, dict) or not (hoja.get("piezas") or []):
        return list(leftovers or [])

    from .manager import (
        _empaquetar_mejor_hoja_mc,
        _is_virtual_piece,
        _es_pieza_fisica_hoja,
        _polygon_usable_for_limite,
        _translate_poligonos_for_overlay,
        _zonas_libres_hoja_madre,
        actualizar_eficiencias_hoja,
        _dbg_nesting,
    )
    from .geometry_parser import reconstruir_poly_seguro
    from shapely import affinity
    from shapely.geometry import LineString

    pool = [copy.deepcopy(p) for p in (leftovers or [])]
    t0 = time.perf_counter()
    colocados_total = 0

    for pase in range(1, max(1, int(max_passes)) + 1):
        if not pool:
            break
        zonas = _zonas_libres_hoja_madre(hoja, w_placa, h_placa, kerf, margin)
        if not zonas:
            break

        kerf_mm = (float(kerf or 0.0) * 25.4) / 2.0
        voids = _catalog_voids(zonas, kerf_mm)
        if not voids:
            break

        msg_cat = (
            f"[LITE-VOID] clave={clave} | pase={pase} | voids={len(voids)} | "
            f"corr={sum(1 for v in voids if v['corridor'])} | "
            f"areas=[{', '.join(f'{v['area']:.0f}' for v in voids[:6])}...] | "
            f"pool={len(pool)}"
        )
        print(msg_cat, flush=True)
        try:
            _dbg_nesting(msg_cat)
        except Exception:
            pass

        avance = 0
        for void in voids:
            if not pool:
                break

            zona_pack = void["geom"]
            # Restar metal ya colocado que solape la zona.
            try:
                ocupado = []
                for p_ex in hoja.get("piezas") or []:
                    if not _es_pieza_fisica_hoja(p_ex.get("nombre")):
                        continue
                    g_ex = reconstruir_poly_seguro(p_ex.get("poligonos") or [])
                    if g_ex is None or g_ex.is_empty:
                        continue
                    if zona_pack.intersects(g_ex):
                        ocupado.append(g_ex)
                if ocupado:
                    from shapely.ops import unary_union

                    zona_pack = zona_pack.difference(unary_union(ocupado))
                    zona_pack = _polygon_usable_for_limite(zona_pack)
                    if zona_pack is None:
                        continue
                    minx, miny, maxx, maxy = zona_pack.bounds
                    void = {
                        **void,
                        "geom": zona_pack,
                        "area": float(zona_pack.area),
                        "w": float(maxx - minx),
                        "h": float(maxy - miny),
                    }
            except Exception:
                pass

            candidatos_src = _pieces_fitting_void(pool, void, area_factor=0.95)
            if not candidatos_src:
                continue

            # Quitar candidatas del pool; las no puestas vuelven.
            cand_names = Counter(str(p.get("nombre") or "") for p in candidatos_src)
            restantes = _subtract_placed(pool, cand_names)
            candidatos = [copy.deepcopy(p) for p in candidatos_src]

            minx, miny, maxx, maxy = void["geom"].bounds
            w_z, h_z = float(void["w"]), float(void["h"])
            poly_local = affinity.translate(void["geom"], -minx, -miny)

            hoja_z, _restos_z = _empaquetar_mejor_hoja_mc(
                candidatos,
                w_z,
                h_z,
                kerf,
                margin,
                opt,
                corner,
                limite_poly=poly_local,
                debug_tag=f"clave={clave} | void_bestfit_p{pase}",
                mc_iterations=max(1, int(mc_iterations or 1)),
                solo_accesorios=True,
                accesorios_retries=8,
            )
            if not hoja_z or not hoja_z.get("piezas"):
                pool = restantes + candidatos
                continue

            placed: Counter = Counter()
            for p_acc in hoja_z.get("piezas") or []:
                if _is_virtual_piece(str(p_acc.get("nombre") or "")):
                    continue
                p_clon = copy.deepcopy(p_acc)
                if p_clon.get("poligonos"):
                    p_clon["poligonos"] = _translate_poligonos_for_overlay(
                        p_clon["poligonos"], minx, miny
                    )
                g_new = reconstruir_poly_seguro(p_clon.get("poligonos") or [])
                choca = False
                if g_new is not None and not g_new.is_empty:
                    for p_ex in hoja.get("piezas") or []:
                        if not _es_pieza_fisica_hoja(p_ex.get("nombre")):
                            continue
                        g_ex = reconstruir_poly_seguro(p_ex.get("poligonos") or [])
                        if g_ex is None or g_ex.is_empty:
                            continue
                        try:
                            if float(g_new.intersection(g_ex).area) >= 100.0:
                                choca = True
                                break
                        except Exception:
                            continue
                if choca:
                    continue
                if p_clon.get("marcas"):
                    nuevas_marcas = []
                    for line_coords in p_clon["marcas"]:
                        try:
                            nuevas_marcas.append(
                                list(
                                    affinity.translate(
                                        LineString(line_coords), xoff=minx, yoff=miny
                                    ).coords
                                )
                            )
                        except Exception:
                            nuevas_marcas.append(line_coords)
                    p_clon["marcas"] = nuevas_marcas
                hoja.setdefault("piezas", []).append(p_clon)
                placed[str(p_acc.get("nombre") or "")] += 1
                colocados_total += 1
                avance += 1

            no_puestas = _subtract_placed(candidatos, placed)
            pool = list(restantes) + no_puestas

        if avance <= 0:
            break

        apply_band_compact(hoja, engine_id=engine_id)

    if colocados_total:
        actualizar_eficiencias_hoja(hoja)
        dt = time.perf_counter() - t0
        msg = (
            f"[COMPACT-BACKFILL] clave={clave} | colocadas={colocados_total} | "
            f"restan={len(pool)} | t={dt:.2f}s"
        )
        print(msg, flush=True)
        try:
            _dbg_nesting(msg)
        except Exception:
            pass
        hoja["compact_lite_backfill"] = {
            "colocadas": colocados_total,
            "restan": len(pool),
            "t": dt,
        }
    else:
        dt = time.perf_counter() - t0
        msg = (
            f"[COMPACT-BACKFILL] clave={clave} | colocadas=0 | "
            f"pool={len(leftovers or [])} | t={dt:.2f}s | "
            f"(pack_combinado debería absorber remanente en el MC principal)"
        )
        print(msg, flush=True)
        try:
            _dbg_nesting(msg)
        except Exception:
            pass

    return pool


def densify_sheet(
    hoja: dict,
    leftovers: list | None = None,
    *,
    w_placa: float | None = None,
    h_placa: float | None = None,
    kerf: float = 0.15,
    margin: float = 0.0,
    opt: str = "OPTIMIZAR LARGO Y ANCHO",
    corner: str = "INFERIOR IZQUIERDA",
    mc_iterations: int = 1,
    clave: str = "",
    engine_id: str = "default",
) -> list:
    """Backfill remanente (si hay leftovers) + band-close. Devuelve leftovers finales."""
    if not compact_enabled() or not isinstance(hoja, dict):
        return list(leftovers or [])

    try:
        from .cut_gaps_table import gaps_efectivos_para_hoja

        kt, mt = gaps_efectivos_para_hoja(
            hoja,
            clave=clave,
            kerf_fallback=kerf,
            margin_fallback=margin if margin > 0 else None,
        )
        kerf = max(float(kerf or 0.0), float(kt)) if float(kerf or 0.0) > 0 else float(kt)
        margin = (
            max(float(margin or 0.0), float(mt))
            if float(margin or 0.0) > 0
            else float(mt)
        )
    except Exception:
        pass

    w = float(w_placa if w_placa is not None else (hoja.get("placa_w") or 0) or 0)
    h = float(h_placa if h_placa is not None else (hoja.get("placa_h") or 0) or 0)
    pool = list(leftovers or [])

    if pool and w > 0 and h > 0:
        pool = backfill_remnant_into_sheet(
            hoja,
            pool,
            w,
            h,
            kerf,
            margin,
            opt,
            corner,
            mc_iterations=mc_iterations,
            max_passes=3,
            clave=clave,
            engine_id=engine_id,
        )

    apply_band_compact(hoja, engine_id=engine_id)
    return pool


def _classify_cavity_guests(hoja: dict) -> tuple[set[int], dict[int, list[int]]]:
    """Guests dentro de orificio → skip_idxs + rigid_children[host]=[guests]."""
    from .venom_hole_fill import (
        _guest_already_in_cavity,
        _is_cavity_host,
        _is_virtual,
        _piece_poly,
        list_closed_interior_cavities,
    )

    skip: set[int] = set()
    rigid: dict[int, list[int]] = {}
    piezas = hoja.get("piezas") or []
    hosts: list[tuple[int, list]] = []
    for idx, p in enumerate(piezas):
        if _is_virtual(str(p.get("nombre") or "")):
            continue
        poly = _piece_poly(p)
        if poly is None:
            continue
        if _is_cavity_host(poly, p):
            cavs = list_closed_interior_cavities(poly)
            if cavs:
                hosts.append((idx, cavs))

    if not hosts:
        return skip, rigid

    for idx, p in enumerate(piezas):
        if _is_virtual(str(p.get("nombre") or "")):
            continue
        # Hosts no son guests.
        if any(idx == h for h, _ in hosts):
            continue
        poly = _piece_poly(p)
        if poly is None:
            continue
        for host_idx, cavs in hosts:
            if _guest_already_in_cavity(poly, cavs):
                skip.add(idx)
                rigid.setdefault(host_idx, []).append(idx)
                break
    return skip, rigid


def _gravity_slide_exterior(
    hoja: dict,
    *,
    skip_idxs: set[int],
    rigid_children: dict[int, list[int]],
    engine_id: str = "arga_lite",
    max_slide_mm: float | None = None,
) -> dict[str, Any]:
    """Desliza piezas exteriores (y hosts con sus guests) hacia origen."""
    from .venom_band_close import (
        _apply_group_move,
        _group_can_move,
        _piece_entries,
    )

    stats = {"moved": 0, "mm": 0.0}
    entries = _piece_entries(hoja)
    if len(entries) < 2:
        return stats

    skip = {int(i) for i in skip_idxs}
    kids = {int(k): [int(x) for x in v] for k, v in (rigid_children or {}).items()}
    placa_w = float(hoja.get("placa_w", 0) or 0)
    placa_h = float(hoja.get("placa_h", 0) or 0)
    try:
        from .cut_gaps_table import gaps_efectivos_para_hoja

        kerf_in, margin_in = gaps_efectivos_para_hoja(hoja)
    except Exception:
        kerf_in = float(hoja.get("kerf_usado", 0.0) or 0.0)
        margin_in = float(hoja.get("margin_usado", 0.25) or 0.25)
    kerf_half = max((kerf_in * 25.4) / 2.0, 0.5)
    plate_inset = max(float(margin_in) * 25.4, 0.0)
    step = min(0.5, max(kerf_half * 0.15, 0.25))
    if max_slide_mm is None:
        max_mm = min(600.0, max(placa_w, placa_h) * 0.35)
    else:
        max_mm = max(1.0, float(max_slide_mm))

    movable = [e for e in entries if e["idx"] not in skip]
    # Lejos del origen primero → llenan huecos que dejaron las piezas metidas al orificio.
    movable.sort(
        key=lambda e: -(float(e["poly"].bounds[0]) + float(e["poly"].bounds[1]))
    )

    for e in movable:
        members = [e]
        moved_mm = 0.0
        for dx_unit, dy_unit in ((0.0, -step), (-step, 0.0)):
            while moved_mm + step <= max_mm + 1e-9:
                if not _group_can_move(
                    members,
                    entries,
                    dx_unit,
                    dy_unit,
                    kerf_half,
                    placa_w,
                    placa_h,
                    rigid_children=kids,
                    plate_inset_mm=plate_inset,
                ):
                    break
                _apply_group_move(
                    members, entries, dx_unit, dy_unit, rigid_children=kids
                )
                moved_mm += step
        if moved_mm > 0.5:
            stats["moved"] += 1
            stats["mm"] += moved_mm

    if stats["moved"]:
        print(
            f"[LITE-RECOMPACT] gravity Motor: {engine_id} | "
            f"moved={stats['moved']} mm={stats['mm']:.1f}",
            flush=True,
        )
    return stats


def recompact_exterior_after_hole_fill(
    hoja: dict, engine_id: str = "arga_lite"
) -> dict[str, Any]:
    """Tras meter guests a orificios: reacomoda lo que NO está dentro de una pieza.

    - Guests en cavidad: no se mueven solos (van pegados al host si el host se mueve).
    - Exterior + hosts: band-close + gravedad hacia origen para cerrar huecos.
    """
    stats: dict[str, Any] = {
        "enabled": compact_enabled(),
        "skipped": True,
        "frozen": 0,
        "band": {},
        "gravity": {},
        "reverted": False,
    }
    if not compact_enabled() or not isinstance(hoja, dict):
        return stats
    filled = int((hoja.get("lite_hole_fill") or {}).get("filled") or 0)
    if filled <= 0:
        stats["reason"] = "no_fill"
        return stats

    skip, rigid = _classify_cavity_guests(hoja)
    stats["frozen"] = len(skip)
    if not skip and filled > 0:
        # Fill reportó movimientos pero no clasificó guests — igual compactar todo.
        skip, rigid = set(), {}

    snapshot = copy.deepcopy(hoja.get("piezas") or [])
    t0 = time.perf_counter()
    try:
        from .venom_band_close import _piece_entries, close_inter_band_gaps

        entries = _piece_entries(hoja)
        band = close_inter_band_gaps(
            hoja,
            engine_id=engine_id,
            force=True,
            skip_idxs=skip,
            rigid_children=rigid,
            all_entries=entries,
        )
        stats["band"] = {
            "bands_y": int(band.get("bands_y") or 0),
            "bands_x": int(band.get("bands_x") or 0),
            "mm_y": float(band.get("mm_y") or 0),
            "mm_x": float(band.get("mm_x") or 0),
        }
        grav = _gravity_slide_exterior(
            hoja, skip_idxs=skip, rigid_children=rigid, engine_id=engine_id
        )
        stats["gravity"] = grav
        stats["skipped"] = False
        stats["t"] = time.perf_counter() - t0
    except Exception as exc:
        stats["error"] = str(exc)
        hoja["piezas"] = snapshot
        return stats

    # Pokayoke con polys en memoria (no fail-closed por poligonos ausentes).
    try:
        from .venom_band_close import _piece_entries

        ents = _piece_entries(hoja)
        bad = False
        detail = ""
        for i in range(len(ents)):
            for j in range(i + 1, len(ents)):
                try:
                    a = float(ents[i]["poly"].intersection(ents[j]["poly"]).area)
                except Exception:
                    continue
                if a > 25.0:
                    ni = str(ents[i]["p"].get("nombre") or i)
                    nj = str(ents[j]["p"].get("nombre") or j)
                    bad = True
                    detail = f"{ni} × {nj} ({a:.0f} mm²)"
                    break
            if bad:
                break
        if bad:
            hoja["piezas"] = snapshot
            stats["reverted"] = True
            stats["revert_detail"] = detail
            print(
                f"[LITE-RECOMPACT] REVERTIDO | {detail}",
                flush=True,
            )
            return stats
    except Exception as exc:
        hoja["piezas"] = snapshot
        stats["reverted"] = True
        stats["error"] = str(exc)
        return stats

    print(
        f"[LITE-RECOMPACT] Motor: {engine_id} | frozen={stats['frozen']} | "
        f"band_y={stats['band'].get('bands_y', 0)} "
        f"band_x={stats['band'].get('bands_x', 0)} | "
        f"grav={stats['gravity'].get('moved', 0)} | t={stats.get('t', 0):.2f}s",
        flush=True,
    )
    hoja["lite_recompact"] = dict(stats)
    return stats


def densificar_nido_en_placa(hoja: dict, engine_id: str = "arga_lite") -> dict[str, Any]:
    """Junta el nido al origen (band-close + gravedad) con kerf/margen de tabla.

    No depende de Venom. Ultra/renest con Venom OFF dejaba el acomodo desparramado
    (18% de área pero huecos de pulgadas entre piezas).
    """
    stats: dict[str, Any] = {
        "enabled": compact_enabled(),
        "skipped": True,
        "band": {},
        "gravity": {},
        "reverted": False,
    }
    if not compact_enabled() or not isinstance(hoja, dict):
        return stats
    if not (hoja.get("piezas") or []):
        return stats

    snapshot = copy.deepcopy(hoja.get("piezas") or [])
    t0 = time.perf_counter()
    try:
        from .venom_band_close import close_inter_band_gaps

        skip, rigid = _classify_cavity_guests(hoja)
        placa_w = float(hoja.get("placa_w", 0) or 0)
        placa_h = float(hoja.get("placa_h", 0) or 0)
        band = close_inter_band_gaps(
            hoja,
            engine_id=engine_id,
            force=True,
            skip_idxs=skip,
            rigid_children=rigid,
        )
        stats["band"] = {
            "bands_y": int(band.get("bands_y") or 0),
            "bands_x": int(band.get("bands_x") or 0),
            "mm_y": float(band.get("mm_y") or 0),
            "mm_x": float(band.get("mm_x") or 0),
        }
        grav = _gravity_slide_exterior(
            hoja,
            skip_idxs=skip,
            rigid_children=rigid,
            engine_id=engine_id,
            max_slide_mm=max(placa_w, placa_h, 1.0),
        )
        stats["gravity"] = grav
        stats["skipped"] = False
        stats["t"] = time.perf_counter() - t0
    except Exception as exc:
        hoja["piezas"] = snapshot
        stats["error"] = str(exc)
        stats["reverted"] = True
        return stats

    try:
        from .nest_poka_yoke import validar_separacion_minima_hoja

        ok, detail = validar_separacion_minima_hoja(hoja)
        if not ok:
            hoja["piezas"] = snapshot
            stats["reverted"] = True
            stats["revert_detail"] = detail
            print(f"[DENSIFY] REVERTIDO | {detail}", flush=True)
            return stats
    except Exception as exc:
        hoja["piezas"] = snapshot
        stats["reverted"] = True
        stats["error"] = str(exc)
        return stats

    print(
        f"[DENSIFY] Motor: {engine_id} | "
        f"band_y={stats['band'].get('bands_y', 0)} "
        f"band_x={stats['band'].get('bands_x', 0)} | "
        f"grav={stats['gravity'].get('moved', 0)} "
        f"mm={float(stats['gravity'].get('mm') or 0):.1f} | "
        f"t={stats.get('t', 0):.2f}s",
        flush=True,
    )
    hoja["densify_pack"] = dict(stats)
    return stats
