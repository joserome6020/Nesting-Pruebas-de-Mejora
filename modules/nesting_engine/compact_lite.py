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
