"""Promover piezas a RTZ natural(es) sin re-nestear.

Dos caminos:
- Como RTZ (UI): N seleccionadas → N RTZ (1:1).
- Switch PARTS forzar_rtz: 1 RTZ por zona (barreno/remanente), N piezas.
"""
from __future__ import annotations

import copy
import os
import re
from typing import Any

from shapely.geometry import Polygon
from shapely.ops import unary_union

from .efficiency_metrics import actualizar_eficiencias_hoja, nombre_rtz_para_placa
from .geometry_parser import generar_texto_vectorial

RTZ_MINI_NEST_MAX_ANCHO_MM = 120.0 * 25.4
RTZ_MINI_NEST_MAX_LARGO_MM = 60.0 * 25.4
DEFAULT_KERF_IN = 0.15


def _is_virtual(nombre: str) -> bool:
    n = str(nombre or "")
    return n.startswith(
        ("REF__", "TATUAJE__", "RETAZO_GUILLOTINA__", "REMANENTE__", "CU_CORTE__")
    )


def stays_on_madre(nombre: str) -> bool:
    """Heurística histórica (aviso); ya NO bloquea si el usuario las seleccionó."""
    raw = str(nombre or "").upper()
    if "PLACA TOP COVER" in raw:
        return True
    if "PLACA BASE" in raw:
        return True
    if "PREFORMADOS BASE" in raw or "PREFORMADO BASE" in raw:
        return True
    if re.search(r"(^|__)TOP COVER\b", raw) and "LUG" not in raw:
        return True
    return False


def _poly_of(pieza: dict) -> Polygon | None:
    polys = pieza.get("poligonos") or []
    if not polys:
        return None
    try:
        holes = list(polys[1:]) if len(polys) > 1 else None
        poly = Polygon(polys[0], holes)
        return None if poly.is_empty else poly
    except Exception:
        try:
            poly = Polygon(polys[0])
            return None if poly.is_empty else poly
        except Exception:
            return None


def _translate_poligonos(poligonos, dx: float, dy: float) -> list:
    out = []
    for ring in poligonos or []:
        nr = []
        for pt in ring or []:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                nr.append([float(pt[0]) + dx, float(pt[1]) + dy])
            else:
                nr.append(pt)
        out.append(nr)
    return out


def _translate_marcas(marcas, dx: float, dy: float) -> list:
    out = []
    for line in marcas or []:
        nl = []
        for pt in line or []:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                nl.append([float(pt[0]) + dx, float(pt[1]) + dy])
            else:
                nl.append(pt)
        if len(nl) >= 2:
            out.append(nl)
    return out


def _piece_translate(pieza: dict, dx: float, dy: float) -> dict:
    p = copy.deepcopy(pieza)
    p["poligonos"] = _translate_poligonos(p.get("poligonos") or [], dx, dy)
    p["marcas"] = _translate_marcas(p.get("marcas") or [], dx, dy)
    return p


def max_rtz_counter(hojas: list | None) -> int:
    mx = 0
    for h in hojas or []:
        pid = str((h or {}).get("placa_id") or "")
        m = re.match(r"RTZ(\d+)", pid, re.I)
        if m:
            mx = max(mx, int(m.group(1)))
        for p in (h or {}).get("piezas") or []:
            nom = str(p.get("nombre") or "")
            for pref in ("TATUAJE__", "RETAZO_GUILLOTINA__", "REF__"):
                if nom.startswith(pref):
                    m2 = re.search(r"RTZ(\d+)", nom, re.I)
                    if m2:
                        mx = max(mx, int(m2.group(1)))
                    break
    return mx


def _kerf_gap_mm(madre: dict | None) -> float:
    try:
        k_in = float((madre or {}).get("kerf_usado") or DEFAULT_KERF_IN)
    except (TypeError, ValueError):
        k_in = DEFAULT_KERF_IN
    if k_in < 0:
        k_in = DEFAULT_KERF_IN
    return max(float(k_in) * 25.4, 2.0)


def _bbox_of_items(items: list[dict]) -> tuple[float, float, float, float]:
    minx = min(it["poly"].bounds[0] for it in items)
    miny = min(it["poly"].bounds[1] for it in items)
    maxx = max(it["poly"].bounds[2] for it in items)
    maxy = max(it["poly"].bounds[3] for it in items)
    return float(minx), float(miny), float(maxx), float(maxy)


def _union_exteriores(items: list[dict]) -> Polygon | None:
    polys = []
    for it in items:
        try:
            polys.append(Polygon(it["poly"].exterior))
        except Exception:
            continue
    if not polys:
        return None
    try:
        u = unary_union(polys)
    except Exception:
        return None
    if u is None or u.is_empty:
        return None
    if u.geom_type == "Polygon":
        return u
    if u.geom_type == "MultiPolygon":
        return max(u.geoms, key=lambda g: g.area)
    return None


def _clip_contorno_fuera_keepers(
    union_g: Polygon, keepers: list[dict], gap_mm: float
) -> Polygon | None:
    """Recorta el contorno para no entrar en metal+gap de piezas que quedan."""
    if union_g is None or getattr(union_g, "is_empty", True):
        return None
    if not keepers:
        return union_g
    obstacles = []
    g = max(float(gap_mm or 0.0), 0.0)
    for k in keepers:
        poly = k.get("poly")
        if poly is None or getattr(poly, "is_empty", True):
            continue
        try:
            obstacles.append(poly.buffer(g) if g > 0 else poly)
        except Exception:
            obstacles.append(poly)
    if not obstacles:
        return union_g
    try:
        clipped = union_g.difference(unary_union(obstacles))
    except Exception:
        return union_g
    if clipped is None or clipped.is_empty:
        # Pieza ya nestada pegada al keeper: conservar contorno de la pieza.
        return union_g
    if clipped.geom_type == "MultiPolygon":
        return max(clipped.geoms, key=lambda x: x.area)
    if clipped.geom_type == "Polygon":
        return clipped
    return union_g


RTZ_SEP_MM = 1.6


def _plate_poly(madre: dict | None) -> Polygon | None:
    try:
        w = float((madre or {}).get("placa_w") or 0.0)
        h = float((madre or {}).get("placa_h") or 0.0)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return Polygon([(0, 0), (w, 0), (w, h), (0, h)])


def rtz_existentes_en_madre(madre: dict | None) -> tuple[list[Polygon], list[Polygon]]:
    """(piezas REF__ de RTZ ya creados, contornos RETAZO_GUILLOTINA__) en la madre."""
    piezas: list[Polygon] = []
    regiones: list[Polygon] = []
    for p in (madre or {}).get("piezas") or []:
        nom = str((p or {}).get("nombre") or "")
        if not nom.startswith(("REF__", "RETAZO_GUILLOTINA__")):
            continue
        try:
            poly = Polygon(((p.get("poligonos") or [[]])[0]))
            if not poly.is_valid:
                poly = poly.buffer(0)
        except Exception:
            continue
        if poly.is_empty:
            continue
        (piezas if nom.startswith("REF__") else regiones).append(poly)
    return piezas, regiones


def contorno_rtz_con_holgura(
    items: list[dict],
    keepers: list[dict],
    gap_mm: float,
    *,
    plate_poly: Polygon | None = None,
    otras_piezas_rtz: list[Polygon] | None = None,
    otras_regiones_rtz: list[Polygon] | None = None,
) -> Polygon | None:
    """Contorno de corte de un RTZ sobrante que no pisa piezas vecinas.

    Casco de las piezas + margen `gap`, recortado a `gap/2` de las piezas que
    quedan en la madre (línea media del espacio del nesteo) y a RTZ_SEP_MM de
    otros RTZ para no compartir línea de corte. None si no cabe con holgura.
    """
    try:
        own = unary_union([Polygon(it["poly"].exterior) for it in items])
    except Exception:
        return None
    if own is None or own.is_empty:
        return None
    gap = max(float(gap_mm or 0.0), 2.0)
    c = gap / 2.0
    mitre = {"join_style": 2, "mitre_limit": 2.0}
    reg = own.convex_hull.buffer(gap, **mitre).simplify(0.02)
    obst = []
    for k in keepers or []:
        poly = k.get("poly")
        if poly is not None and not poly.is_empty:
            obst.append(Polygon(poly.exterior).buffer(c, **mitre))
    for poly in otras_piezas_rtz or []:
        obst.append(poly.buffer(c + RTZ_SEP_MM / 2.0, **mitre))
    for poly in otras_regiones_rtz or []:
        obst.append(poly.buffer(RTZ_SEP_MM, **mitre))
    try:
        if obst:
            reg = reg.difference(unary_union(obst))
    except Exception:
        return None
    if plate_poly is not None:
        for lim in (plate_poly.buffer(-c, **mitre), plate_poly):
            cand = reg.intersection(lim)
            if not cand.is_empty and cand.buffer(1e-6).contains(own):
                reg = cand
                break
    if reg.is_empty:
        return None
    if reg.geom_type == "MultiPolygon":
        reg = unary_union([g for g in reg.geoms if g.intersects(own)])
    if reg.geom_type != "Polygon":
        return None
    reg = Polygon(reg.exterior)
    if not reg.contains(own):
        return None
    margen_min = max(0.5, min(2.0, 0.9 * (gap - RTZ_SEP_MM) / 2.0))
    if reg.exterior.distance(own) < margen_min:
        return None
    return reg


def _regiones_guillotina(overlays: list[dict]) -> list[Polygon]:
    out = []
    for ov in overlays or []:
        if str(ov.get("nombre") or "").startswith("RETAZO_GUILLOTINA__"):
            try:
                out.append(Polygon(ov["poligonos"][0]))
            except Exception:
                continue
    return out


def _selection_in_keeper_hole(items: list[dict], keepers: list[dict]) -> bool:
    """True si todas las piezas están dentro de un barreno de algún keeper."""
    if not items or not keepers:
        return False
    for it in items:
        c = it["poly"].centroid
        ok = False
        for k in keepers:
            poly = k.get("poly")
            if poly is None:
                continue
            try:
                for interior in poly.interiors:
                    hole = Polygon(interior)
                    if hole.contains(c) or hole.covers(c):
                        ok = True
                        break
            except Exception:
                continue
            if ok:
                break
        if not ok:
            return False
    return True


def _tatuaje_local(rtz_id: str, rw: float, rh: float, cal, mat) -> dict:
    cx, cy = rw / 2.0, rh / 2.0
    w_txt = max(50.0, min(rw * 0.45, 400.0))
    h_txt = max(15.0, min(rh * 0.45, 40.0))
    marks = generar_texto_vectorial(rtz_id, cx, cy, w_txt, h_txt)
    dummy = [
        [
            (cx - 1, cy - 1),
            (cx + 1, cy - 1),
            (cx + 1, cy + 1),
            (cx - 1, cy + 1),
            (cx - 1, cy - 1),
        ]
    ]
    return {
        "nombre": f"TATUAJE__{rtz_id}",
        "poligonos": dummy,
        "marcas": marks,
        "area": 0.0,
        "calibre": cal,
        "material": mat,
    }


def _tatuaje_madre(rtz_id: str, gx: float, gy: float, rw: float, rh: float, cal, mat) -> dict:
    cx, cy = gx + rw / 2.0, gy + rh / 2.0
    w_txt = max(50.0, min(rw * 0.45, 400.0))
    h_txt = max(15.0, min(rh * 0.45, 40.0))
    marks = generar_texto_vectorial(rtz_id, cx, cy, w_txt, h_txt)
    dummy = [
        [
            (cx - 1, cy - 1),
            (cx + 1, cy - 1),
            (cx + 1, cy + 1),
            (cx - 1, cy + 1),
            (cx - 1, cy - 1),
        ]
    ]
    return {
        "nombre": f"TATUAJE__{rtz_id}",
        "poligonos": dummy,
        "marcas": marks,
        "area": 0.0,
        "calibre": cal,
        "material": mat,
    }


def _ref_overlay(pieza_orig: dict, rtz_id: str) -> dict:
    p = copy.deepcopy(pieza_orig)
    base = str(p.get("nombre") or "")
    p["nombre"] = f"REF__{base}"
    p["rtz_overlay_id"] = rtz_id
    p["marcas"] = []
    return p


def _guillotina_desde_poly(rtz_id: str, poly_global: Polygon, cal, mat) -> dict:
    try:
        if poly_global.geom_type == "MultiPolygon":
            poly_global = max(poly_global.geoms, key=lambda g: g.area)
        coords = list(poly_global.exterior.coords)
    except Exception:
        minx, miny, maxx, maxy = poly_global.bounds
        coords = [
            (minx, miny),
            (maxx, miny),
            (maxx, maxy),
            (minx, maxy),
            (minx, miny),
        ]
    return {
        "nombre": f"RETAZO_GUILLOTINA__{rtz_id}",
        "poligonos": [coords],
        "marcas": [],
        "area": 0.0,
        "calibre": cal,
        "material": mat,
    }


def _norm_nombre_forzar(nombre: str) -> str:
    n = str(nombre or "").strip().upper()
    if "__" in n:
        n = n.split("__", 1)[-1].strip()
    # Quitar sufijos de instancia / qty si existen
    n = re.sub(r"\s+#\d+\s*$", "", n)
    return n


def _ruta_keys_forzar(ruta: str) -> list[str]:
    raw = str(ruta or "").strip()
    if not raw:
        return []
    keys = [raw]
    try:
        from interface.utils_nesting import (
            clave_orientacion_cobre_ruta,
            clave_orientacion_pieza,
        )

        k = clave_orientacion_cobre_ruta(raw)
        if k and k not in keys:
            keys.append(k)
        # sin mapa plasma; basta normpath
        k2 = clave_orientacion_pieza(raw, None)
        if k2 and k2 not in keys:
            keys.append(k2)
    except Exception:
        try:
            k = os.path.normcase(os.path.normpath(raw))
            if k not in keys:
                keys.append(k)
        except Exception:
            pass
    try:
        base = os.path.basename(raw).lower()
        if base and base not in keys:
            keys.append(base)
    except Exception:
        pass
    return keys


def stamp_forzar_rtz_on_piezas(
    piezas: list | None,
    *,
    flags_ruta: dict | None = None,
    flags_nombre: dict | None = None,
) -> int:
    """Reaplica forzar_rtz: flag del usuario (ruta norm / nombre; True o False)
    y, si nunca la tocó, el tope de área (regla_area_rtz). Devuelve cuántas quedan RTZ."""
    from .regla_area_rtz import resolver_forzar_rtz

    if not isinstance(piezas, list):
        return 0
    flags_ruta_n: dict[str, bool] = {}
    flags_base: dict[str, bool] = {}
    for k, v in dict(flags_ruta or {}).items():
        if v is None:
            continue
        for kk in _ruta_keys_forzar(str(k)):
            flags_ruta_n[kk] = bool(v)
        try:
            flags_base[os.path.basename(str(k)).lower()] = bool(v)
        except Exception:
            pass
    flags_nombre_n = {
        _norm_nombre_forzar(k): bool(v)
        for k, v in dict(flags_nombre or {}).items()
        if v is not None
    }
    n = 0
    for p in piezas:
        if not isinstance(p, dict):
            continue
        nom = str(p.get("nombre") or "")
        if _is_virtual(nom):
            continue
        explicito = None
        ruta = str(p.get("ruta") or "").strip()
        for kk in _ruta_keys_forzar(ruta) if ruta else []:
            if kk in flags_ruta_n:
                explicito = flags_ruta_n[kk]
                break
            base = os.path.basename(kk).lower()
            if base in flags_base:
                explicito = flags_base[base]
                break
        if explicito is None:
            explicito = flags_nombre_n.get(_norm_nombre_forzar(nom))
        area = p.get("area")
        if not area:
            poly = _poly_of(p)
            area = float(poly.area) if poly is not None else 0.0
        if resolver_forzar_rtz(area, explicito, p.get("material") or ""):
            p["forzar_rtz"] = True
            n += 1
        else:
            p.pop("forzar_rtz", None)
    return n


def _guest_in_hole(guest_poly: Polygon, hole: Polygon) -> bool:
    """True si el huésped está (casi) dentro del barreno."""
    if guest_poly is None or hole is None:
        return False
    try:
        c = guest_poly.centroid
        if hole.contains(c) or hole.covers(c):
            return True
    except Exception:
        pass
    try:
        inter = float(guest_poly.intersection(hole).area)
        ga = float(guest_poly.area) or 1.0
        if inter / ga >= 0.55:
            return True
    except Exception:
        pass
    return False


def _build_rtz_hoja(
    *,
    rtz_id: str,
    madre: dict,
    items: list[dict],
    cal: str,
    mat: str,
    keepers: list[dict] | None = None,
    gap_mm: float = 0.0,
    zone_poly_global: Polygon | None = None,
    force_hole: bool = False,
    otras_piezas_rtz: list[Polygon] | None = None,
    otras_regiones_rtz: list[Polygon] | None = None,
) -> tuple[dict | None, list[dict], str]:
    """Construye 1 RTZ para N piezas. Si zone_poly_global (barreno), ese es el borde."""
    keepers = list(keepers or [])
    if not items:
        return None, [], "sin_piezas"

    if zone_poly_global is not None and not getattr(zone_poly_global, "is_empty", True):
        minx, miny, maxx, maxy = zone_poly_global.bounds
        union_g = zone_poly_global
        es_hole = True
    else:
        minx, miny, maxx, maxy = _bbox_of_items(items)
        union_g = _union_exteriores(items)
        if union_g is None:
            return None, [], "no_se_pudo_unir_contorno"
        es_hole = bool(force_hole) or _selection_in_keeper_hole(items, keepers)
        if not es_hole:
            region = contorno_rtz_con_holgura(
                items,
                keepers,
                gap_mm,
                plate_poly=_plate_poly(madre),
                otras_piezas_rtz=otras_piezas_rtz,
                otras_regiones_rtz=otras_regiones_rtz,
            )
            if region is not None:
                union_g = region
            else:
                union_g = _clip_contorno_fuera_keepers(union_g, keepers, gap_mm) or union_g
            minx, miny, maxx, maxy = union_g.bounds

    gx, gy = float(minx), float(miny)
    rw, rh = float(maxx - minx), float(maxy - miny)
    if rw < 1e-6 or rh < 1e-6:
        return None, [], "bbox_degenerado"

    pieces_local = [_piece_translate(it["pza"], -gx, -gy) for it in items]
    pieces_local.append(_tatuaje_local(rtz_id, rw, rh, cal, mat))

    try:
        from shapely import affinity as _aff

        borde_local = _aff.translate(union_g, xoff=-gx, yoff=-gy)
        if borde_local.geom_type == "MultiPolygon":
            borde_local = max(borde_local.geoms, key=lambda g: g.area)
    except Exception:
        borde_local = Polygon([(0, 0), (rw, 0), (rw, rh), (0, rh), (0, 0)])

    origen = str(madre.get("origen_placa") or "").strip().upper()
    if origen not in ("EMPRESA", "PROVEEDOR"):
        origen = "EMPRESA"

    retazo_tipo = "HOLE" if es_hole else "SOBRANTE"
    hoja_rtz = {
        "placa_id": rtz_id,
        "placa_w": float(rw),
        "placa_h": float(rh),
        "precio_placa": 0.0,
        "kerf_usado": madre.get("kerf_usado"),
        "margin_usado": madre.get("margin_usado"),
        "opt_usado": madre.get("opt_usado"),
        "corner_usado": madre.get("corner_usado"),
        "es_retazo": True,
        "poly_borde_retazo": list(borde_local.exterior.coords),
        "guillotina_desde_borde": retazo_tipo == "SOBRANTE",
        "origen_placa": origen,
        "global_x": gx,
        "global_y": gy,
        "retazo_tipo": retazo_tipo,
        "placa_cal": cal,
        "placa_mat": mat,
        "sheet_code": rtz_id,
        "sheet_display_name": rtz_id,
        "piezas": pieces_local,
    }
    actualizar_eficiencias_hoja(hoja_rtz)

    overlays = [_ref_overlay(it["pza"], rtz_id) for it in items]
    if retazo_tipo == "SOBRANTE":
        overlays.append(_guillotina_desde_poly(rtz_id, union_g, cal, mat))
    overlays.append(_tatuaje_madre(rtz_id, gx, gy, rw, rh, cal, mat))
    return hoja_rtz, overlays, ""


def _host_holes(keepers: list[dict]) -> list[tuple[dict, Polygon]]:
    """Lista (keeper, hole_poly) de barrenos de piezas que permanecen en madre."""
    out: list[tuple[dict, Polygon]] = []
    for k in keepers:
        poly = k.get("poly")
        if poly is None:
            continue
        try:
            for interior in poly.interiors:
                hole = Polygon(interior)
                if hole.is_empty or float(hole.area) < 1.0:
                    continue
                out.append((k, hole))
        except Exception:
            continue
    return out


def _cluster_connected(items: list[dict], gap_mm: float) -> list[list[dict]]:
    """Agrupa piezas por contacto/proximidad (unión buffer)."""
    if not items:
        return []
    if len(items) == 1:
        return [list(items)]
    g = max(float(gap_mm or 0.0), 1.0)
    n = len(items)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    buffed = []
    for it in items:
        try:
            buffed.append(it["poly"].buffer(g))
        except Exception:
            buffed.append(it["poly"])
    for i in range(n):
        for j in range(i + 1, n):
            try:
                if buffed[i].intersects(buffed[j]):
                    union(i, j)
            except Exception:
                continue
    groups: dict[int, list[dict]] = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    return list(groups.values())


def promote_forzar_zones_on_madre(
    madre: dict,
    *,
    hojas_grupo: list | None = None,
    calibre: str = "",
    wo_name: str = "W.O.",
    contador_rtz: int | None = None,
    flags_ruta: dict | None = None,
    flags_nombre: dict | None = None,
) -> dict[str, Any]:
    """
    Switch PARTS RTZ: 1 RTZ por zona (barreno/remanente), N piezas adentro.

    Las piezas ya nestaron normal (incl. orificios). Aquí se reclasifica la zona
    con la lógica RTZ del proyecto: contorno = barreno (o unión si SOBRANTE).
    No es 1:1 (eso es Como RTZ).
    """
    out: dict[str, Any] = {
        "ok": True,
        "motivo": "",
        "rtz_ids": [],
        "n_rtz": 0,
        "n_piezas": 0,
        "n_forzar": 0,
        "n_zonas_barreno": 0,
        "n_zonas_sobran": 0,
        "contador_rtz": int(contador_rtz or 0),
    }
    if not isinstance(madre, dict) or madre.get("es_retazo"):
        return out
    piezas = madre.get("piezas")
    if not isinstance(piezas, list):
        return out

    # Reaplicar flag (ruta norm + nombre) por si el packer lo perdió.
    stamp_forzar_rtz_on_piezas(
        piezas, flags_ruta=flags_ruta, flags_nombre=flags_nombre
    )

    físicos: list[dict] = []
    for idx, pza in enumerate(piezas):
        nom = str((pza or {}).get("nombre") or "")
        if _is_virtual(nom):
            continue
        poly = _poly_of(pza)
        if poly is None:
            continue
        físicos.append(
            {
                "idx": idx,
                "pza": pza,
                "poly": poly,
                "nombre": nom,
                "forzar": bool((pza or {}).get("forzar_rtz")),
            }
        )
    n_forzar = sum(1 for it in físicos if it["forzar"])
    out["n_forzar"] = n_forzar
    if n_forzar <= 0:
        return out

    guests = [
        it
        for it in físicos
        if it["forzar"] and not stays_on_madre(it["nombre"])
    ]
    if not guests:
        return out

    # Hosts = cualquier pieza con barrenos (interiors), no solo “keepers”.
    hosts = [it for it in físicos if list(getattr(it["poly"], "interiors", []) or [])]

    gap_mm = _kerf_gap_mm(madre)
    cal = (
        str(calibre or "").strip()
        or str(madre.get("placa_cal") or "").strip()
        or "NA"
    )
    mat = str(madre.get("placa_mat") or "").strip() or "A 36"
    wo = str(wo_name or "").strip() or "W.O."
    n_rtz = int(contador_rtz) if contador_rtz is not None else max_rtz_counter(hojas_grupo) + 1
    if n_rtz < 1:
        n_rtz = 1

    claimed: set[int] = set()
    zones: list[tuple[list[dict], Polygon | None, bool]] = []

    # 1) Por cada barreno: si hay ≥1 forzar, TODAS las físicas del orificio → 1 RTZ.
    for keeper, hole in _host_holes(hosts):
        host_idx = int(keeper["idx"])
        in_hole: list[dict] = []
        for it in físicos:
            if it["idx"] in claimed or int(it["idx"]) == host_idx:
                continue
            if stays_on_madre(it["nombre"]):
                continue
            try:
                if it["poly"].contains(hole) or float(
                    it["poly"].intersection(hole).area
                ) > float(hole.area) * 0.9:
                    continue
            except Exception:
                pass
            if _guest_in_hole(it["poly"], hole):
                in_hole.append(it)
        if not in_hole:
            continue
        if not any(it["forzar"] for it in in_hole):
            continue
        for it in in_hole:
            claimed.add(int(it["idx"]))
        zones.append((in_hole, hole, True))
        out["n_zonas_barreno"] = int(out["n_zonas_barreno"]) + 1

    # 2) Forzar restantes (fuera de barrenos) → clusters SOBRANTE (unión+gap).
    restantes = [it for it in guests if int(it["idx"]) not in claimed]
    for cluster in _cluster_connected(restantes, gap_mm):
        for it in cluster:
            claimed.add(int(it["idx"]))
        zones.append((cluster, None, False))
        out["n_zonas_sobran"] = int(out["n_zonas_sobran"]) + 1

    if not zones:
        return out

    remove_idxs: set[int] = set()
    all_overlays: list[dict] = []
    rtz_hojas: list[dict] = []
    rtz_ids: list[str] = []
    keepers_build = [it for it in físicos if int(it["idx"]) not in claimed]
    refs_prev, regiones_rtz = rtz_existentes_en_madre(madre)

    for zi, (items_z, zone_poly, is_hole) in enumerate(zones):
        otras_piezas = refs_prev + [
            it["poly"] for zj, z in enumerate(zones) if zj != zi for it in z[0]
        ]
        minx, miny, maxx, maxy = (
            zone_poly.bounds if zone_poly is not None else _bbox_of_items(items_z)
        )
        rw, rh = float(maxx - minx), float(maxy - miny)
        rtz_id = nombre_rtz_para_placa(n_rtz, cal, wo, largo_mm=rh, ancho_mm=rw)
        hoja_rtz, overlays, motivo_r = _build_rtz_hoja(
            rtz_id=rtz_id,
            madre=madre,
            items=items_z,
            cal=cal,
            mat=mat,
            keepers=keepers_build,
            gap_mm=gap_mm,
            zone_poly_global=zone_poly,
            force_hole=is_hole,
            otras_piezas_rtz=otras_piezas,
            otras_regiones_rtz=regiones_rtz,
        )
        if hoja_rtz is None:
            continue
        rtz_hojas.append(hoja_rtz)
        rtz_ids.append(rtz_id)
        all_overlays.extend(overlays)
        regiones_rtz.extend(_regiones_guillotina(overlays))
        for it in items_z:
            remove_idxs.add(int(it["idx"]))
        n_rtz += 1

    if not rtz_hojas:
        out["ok"] = False
        out["motivo"] = "No se pudo crear RTZ de zona forzar_rtz."
        return out

    for idx in sorted(remove_idxs, reverse=True):
        if 0 <= idx < len(piezas):
            piezas.pop(idx)
    piezas.extend(all_overlays)

    if isinstance(hojas_grupo, list):
        try:
            insert_at = hojas_grupo.index(madre) + 1
        except ValueError:
            insert_at = None
            for i, h in enumerate(hojas_grupo):
                if h is madre or (
                    isinstance(h, dict)
                    and str(h.get("placa_id") or "") == str(madre.get("placa_id") or "")
                    and not h.get("es_retazo")
                ):
                    insert_at = i + 1
                    break
            if insert_at is None:
                insert_at = len(hojas_grupo)
        for offset, hoja_rtz in enumerate(rtz_hojas):
            hojas_grupo.insert(insert_at + offset, hoja_rtz)

    for h in [madre, *rtz_hojas]:
        actualizar_eficiencias_hoja(h, hojas_grupo=hojas_grupo)

    out.update(
        {
            "ok": True,
            "rtz_ids": rtz_ids,
            "n_rtz": len(rtz_hojas),
            "n_piezas": len(remove_idxs),
            "contador_rtz": n_rtz,
        }
    )
    return out


def promote_selection_to_rtz(
    madre: dict,
    selected_indices: list[int] | set[int] | None,
    *,
    hojas_grupo: list | None = None,
    calibre: str = "",
    wo_name: str = "W.O.",
    contador_rtz: int | None = None,
) -> dict[str, Any]:
    """
    N piezas físicas seleccionadas → N hojas RTZ (una por pieza).

    No agrupa ni descarta un subconjunto. Solo ignora overlays virtuales.
    """
    out: dict[str, Any] = {
        "ok": False,
        "motivo": "",
        "rtz_hoja": None,
        "rtz_hojas": [],
        "rtz_ids": [],
        "skipped_keepers": [],
        "skipped_virtual": [],
        "skipped_too_big": [],
        "skipped_rejected": [],
        "skipped_not_promoted": [],
        "avisos_estructurales": [],
        "contador_rtz": int(contador_rtz or 0),
        "n_piezas": 0,
        "n_seleccionadas": 0,
        "n_rtz": 0,
        "gap_mm": 0.0,
    }
    if not isinstance(madre, dict):
        out["motivo"] = "No hay placa madre válida."
        return out
    if madre.get("es_retazo"):
        out["motivo"] = "La placa activa es un RTZ; seleccione piezas en la madre."
        return out

    piezas = madre.get("piezas")
    if not isinstance(piezas, list):
        out["motivo"] = "La placa no tiene lista de piezas."
        return out

    indices = sorted(
        {int(i) for i in (selected_indices or []) if isinstance(i, (int, float))}
    )
    if not indices:
        out["motivo"] = "Seleccione al menos una pieza física en la placa madre."
        return out

    skipped_virtual: list[str] = []
    avisos: list[str] = []
    items: list[dict] = []
    selected_idx_set: set[int] = set()

    for idx in indices:
        if idx < 0 or idx >= len(piezas):
            continue
        pza = piezas[idx]
        nom = str((pza or {}).get("nombre") or "")
        if _is_virtual(nom):
            skipped_virtual.append(nom)
            continue
        poly = _poly_of(pza)
        if poly is None:
            skipped_virtual.append(nom or f"idx={idx}")
            continue
        if stays_on_madre(nom):
            # El usuario las seleccionó: se promueven, solo se avisa.
            avisos.append(nom)
        items.append({"idx": idx, "pza": pza, "poly": poly, "nombre": nom})
        selected_idx_set.add(idx)

    out["skipped_virtual"] = skipped_virtual
    out["avisos_estructurales"] = avisos
    out["n_seleccionadas"] = len(items)
    if not items:
        out["motivo"] = (
            "La selección solo tenía overlays virtuales (REF/TATUAJE/GUILLOTINA)."
        )
        return out

    keepers: list[dict] = []
    for i, pza in enumerate(piezas):
        if i in selected_idx_set:
            continue
        nom = str((pza or {}).get("nombre") or "")
        if _is_virtual(nom):
            continue
        poly = _poly_of(pza)
        if poly is None:
            continue
        keepers.append({"idx": i, "pza": pza, "poly": poly, "nombre": nom})

    gap_mm = _kerf_gap_mm(madre)
    out["gap_mm"] = gap_mm

    cal = (
        str(calibre or "").strip()
        or str(madre.get("placa_cal") or "").strip()
        or "NA"
    )
    mat = str(madre.get("placa_mat") or "").strip() or "A 36"
    wo = str(wo_name or "").strip() or "W.O."

    n_rtz = int(contador_rtz) if contador_rtz is not None else max_rtz_counter(hojas_grupo) + 1
    if n_rtz < 1:
        n_rtz = 1

    remove_idxs: set[int] = set()
    all_overlays: list[dict] = []
    rtz_hojas: list[dict] = []
    rtz_ids: list[str] = []
    rechazados: list[str] = []

    refs_prev, regiones_rtz = rtz_existentes_en_madre(madre)

    # 1 pieza seleccionada = 1 RTZ. Sin clustering.
    for it in items:
        otras_piezas = refs_prev + [o["poly"] for o in items if o is not it]
        minx, miny, maxx, maxy = it["poly"].bounds
        rw, rh = float(maxx - minx), float(maxy - miny)
        rtz_id = nombre_rtz_para_placa(n_rtz, cal, wo, largo_mm=rh, ancho_mm=rw)
        hoja_rtz, overlays, motivo_r = _build_rtz_hoja(
            rtz_id=rtz_id,
            madre=madre,
            items=[it],
            cal=cal,
            mat=mat,
            keepers=keepers,
            gap_mm=gap_mm,
            otras_piezas_rtz=otras_piezas,
            otras_regiones_rtz=regiones_rtz,
        )
        if hoja_rtz is None:
            rechazados.append(f"{it.get('nombre') or '?'} ({motivo_r})")
            continue
        rtz_hojas.append(hoja_rtz)
        rtz_ids.append(rtz_id)
        all_overlays.extend(overlays)
        regiones_rtz.extend(_regiones_guillotina(overlays))
        remove_idxs.add(int(it["idx"]))
        n_rtz += 1

    out["skipped_rejected"] = rechazados
    if len(rtz_hojas) != len(items):
        faltan = [
            str(it.get("nombre") or "?")
            for it in items
            if int(it["idx"]) not in remove_idxs
        ]
        out["skipped_not_promoted"] = faltan
        out["motivo"] = (
            f"Se seleccionaron {len(items)} piezas y solo se crearon {len(rtz_hojas)} RTZ.\n"
            f"Faltan: {', '.join(faltan[:8])}"
        )
        # No mutar la madre a medias: fallar limpio.
        return out

    if not rtz_hojas:
        out["motivo"] = "No se pudo crear ningún RTZ."
        return out

    for idx in sorted(remove_idxs, reverse=True):
        if 0 <= idx < len(piezas):
            piezas.pop(idx)
    piezas.extend(all_overlays)

    if isinstance(hojas_grupo, list):
        try:
            insert_at = hojas_grupo.index(madre) + 1
        except ValueError:
            insert_at = None
            for i, h in enumerate(hojas_grupo):
                if h is madre or (
                    isinstance(h, dict)
                    and str(h.get("placa_id") or "") == str(madre.get("placa_id") or "")
                    and not h.get("es_retazo")
                ):
                    insert_at = i + 1
                    break
            if insert_at is None:
                insert_at = len(hojas_grupo)
        for offset, hoja_rtz in enumerate(rtz_hojas):
            hojas_grupo.insert(insert_at + offset, hoja_rtz)

    for h in [madre, *rtz_hojas]:
        actualizar_eficiencias_hoja(h, hojas_grupo=hojas_grupo)

    out.update(
        {
            "ok": True,
            "motivo": "",
            "rtz_hoja": rtz_hojas[0],
            "rtz_hojas": rtz_hojas,
            "rtz_ids": rtz_ids,
            "contador_rtz": n_rtz,
            "n_piezas": len(rtz_hojas),
            "n_rtz": len(rtz_hojas),
            "skipped_not_promoted": [],
        }
    )
    return out
