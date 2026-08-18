"""Poka-yoke de integridad de nest (inventario, solapes, kerf/margin, pack faults).

Helpers compartidos para no dejar pasar nests incompletos, solapados o con
configuración inválida hacia UI/export.
"""
from __future__ import annotations

import os
from typing import Any, Optional


class PackEngineFault(RuntimeError):
    """El motor nativo falló; no confundir con 'no cabe nada'."""


def allow_incomplete_nest() -> bool:
    """Escape shop: ARGA_ALLOW_INCOMPLETE_NEST=1 permite aviso en vez de error."""
    return str(os.environ.get("ARGA_ALLOW_INCOMPLETE_NEST", "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def distancia_menor_que_kerf_mm(
    poly_a,
    poly_b,
    kerf_full_mm: float,
) -> bool:
    """True si metal↔metal < kerf completo (contrato TABLA GAPS)."""
    try:
        if poly_a is None or poly_b is None:
            return False
        if getattr(poly_a, "is_empty", True) or getattr(poly_b, "is_empty", True):
            return False
        return float(poly_a.distance(poly_b)) + 1e-6 < float(kerf_full_mm)
    except Exception:
        return True  # fail-closed: tratar como colisión


def metal_solapa(poly_a, poly_b, *, area_tol_mm2: float = 25.0) -> bool:
    try:
        if poly_a is None or poly_b is None:
            return False
        return float(poly_a.intersection(poly_b).area) > float(area_tol_mm2)
    except Exception:
        return True


def validar_kerf_in(kerf: float, *, default: float = 0.15) -> tuple[float, Optional[str]]:
    """
    Returns (kerf_ok, error_msg).
    No coerce silencioso: si es inválido, error_msg != None.
    """
    try:
        k = float(kerf)
    except Exception:
        return float(default), f"Kerf no numérico ({kerf!r}). Use pulgadas > 0 (ej. 0.15)."
    if k <= 0:
        return float(default), f"Kerf inválido ({k}). Debe ser > 0 in."
    if k > 2.0:
        return float(default), f"Kerf fuera de rango ({k} in). Máximo razonable: 2.0 in."
    return k, None


# Epsilon geométrico (shapely/float ~0.05 mm). No es holgura de planta:
# 0.025" dejaba pasar 0.225" cuando la tabla pide 0.250".
TABLA_GAP_EPS_IN = 0.002


def validar_separacion_minima_hoja(
    hoja: dict,
    kerf_in: float | None = None,
    *,
    margin_in: float | None = None,
    w_placa: float | None = None,
    h_placa: float | None = None,
    clave: str = "",
    tol_in: float = TABLA_GAP_EPS_IN,
) -> tuple[bool, str]:
    """Fail-closed: distancia pieza↔pieza ≥ kerf de tabla (y placa↔pieza ≥ margin).

    Si hay calibre en la hoja/clave, **relee la tabla** (no confía en un kerf
    caller de 0.15" cuando la tabla pide 0.375").
    """
    if not isinstance(hoja, dict):
        return False, "hoja_invalida"
    piezas = list(hoja.get("piezas") or [])
    if len(piezas) < 2 and (margin_in is None or (margin_in or 0) <= 0):
        return True, ""

    try:
        from .cut_gaps_table import gaps_efectivos_para_hoja

        kerf_tabla, margin_tabla = gaps_efectivos_para_hoja(
            hoja,
            clave=clave or str(hoja.get("clave") or ""),
            kerf_fallback=kerf_in,
            margin_fallback=margin_in,
        )
    except Exception:
        try:
            kerf_tabla = float(kerf_in or hoja.get("kerf_usado") or 0.0)
        except Exception:
            return False, f"kerf_no_numerico:{kerf_in!r}"
        try:
            margin_tabla = float(
                margin_in
                if margin_in is not None
                else (hoja.get("margin_usado") or 0.25)
            )
        except Exception:
            margin_tabla = 0.25

    # Si el caller pasó un kerf, no permitir que sea MENOR que la tabla.
    try:
        kerf_caller = float(kerf_in) if kerf_in is not None else 0.0
    except Exception:
        kerf_caller = 0.0
    kerf = max(float(kerf_tabla), float(kerf_caller)) if kerf_caller > 0 else float(kerf_tabla)
    try:
        margin_caller = float(margin_in) if margin_in is not None else 0.0
    except Exception:
        margin_caller = 0.0
    margin = (
        max(float(margin_tabla), float(margin_caller))
        if margin_caller > 0
        else float(margin_tabla)
    )

    if kerf <= 0:
        return True, ""  # cobre / sin kerf

    min_gap_mm = (kerf - float(tol_in)) * 25.4
    if min_gap_mm < 0:
        min_gap_mm = 0.0

    try:
        from .geometry_parser import reconstruir_poly_seguro
        from .sheet_integrity import _es_pieza_real_nombre
    except Exception as exc:
        return False, f"validacion_gap_no_disponible:{exc}"

    polys: list[tuple[str, Any]] = []
    for p in piezas:
        nom = str((p or {}).get("nombre") or "")
        if not _es_pieza_real_nombre(nom):
            continue
        poly = (p or {}).get("poly")
        if poly is None or getattr(poly, "is_empty", True):
            try:
                poly = reconstruir_poly_seguro((p or {}).get("poligonos") or [])
            except Exception:
                poly = None
        if poly is None or getattr(poly, "is_empty", True):
            continue
        polys.append((nom, poly))

    for i in range(len(polys)):
        ni, pi = polys[i]
        for j in range(i + 1, len(polys)):
            nj, pj = polys[j]
            try:
                inter = float(pi.intersection(pj).area)
                if inter > 25.0:
                    return False, f"solape_metal {ni}×{nj} area={inter:.1f}mm2"
                dist = float(pi.distance(pj))
            except Exception as exc:
                return False, f"dist_error {ni}×{nj}:{exc}"
            if dist + 1e-6 < min_gap_mm:
                dist_in = dist / 25.4
                return (
                    False,
                    f"gap_insuficiente {ni}×{nj} gap={dist_in:.3f}in "
                    f"< kerf_tabla={kerf:.3f}in (tol={tol_in:.3f}in)",
                )

    if margin > 0:
        try:
            w = float(w_placa if w_placa is not None else (hoja.get("placa_w") or 0))
            h = float(h_placa if h_placa is not None else (hoja.get("placa_h") or 0))
        except Exception:
            w, h = 0.0, 0.0
        if w > 0 and h > 0:
            min_m = (margin - float(tol_in)) * 25.4
            for nom, poly in polys:
                try:
                    minx, miny, maxx, maxy = poly.bounds
                except Exception:
                    continue
                gaps = (minx, miny, w - maxx, h - maxy)
                if any(g + 1e-6 < min_m for g in gaps):
                    return (
                        False,
                        f"margen_placa {nom} gaps_mm={tuple(round(g, 2) for g in gaps)} "
                        f"< margin_tabla={margin:.3f}in",
                    )

    return True, ""


def _poly_en_cavidad_de(guest, host) -> bool:
    """True si guest está (casi) dentro de un orificio interior de host."""
    try:
        if guest is None or host is None:
            return False
        if getattr(guest, "is_empty", True) or getattr(host, "is_empty", True):
            return False
        interiors = list(getattr(host, "interiors", None) or [])
        if not interiors:
            return False
        from shapely.geometry import Polygon

        c = guest.representative_point()
        for ring in interiors:
            try:
                hole = Polygon(ring)
            except Exception:
                continue
            if hole.is_empty:
                continue
            if c.within(hole) or guest.centroid.within(hole):
                return True
            try:
                if float(guest.intersection(hole).area) > 0.5 * float(guest.area):
                    return True
            except Exception:
                pass
        return False
    except Exception:
        return False


def _poly_pieza_reparacion(p: dict):
    from .geometry_parser import reconstruir_poly_seguro

    poly = (p or {}).get("poly")
    if poly is not None and not getattr(poly, "is_empty", True):
        return poly
    try:
        return reconstruir_poly_seguro((p or {}).get("poligonos") or [])
    except Exception:
        return None


def _indices_par_gap_insuficiente(
    hoja: dict,
    min_gap_mm: float,
) -> tuple[int, int] | None:
    """Índices reales del par más cercano bajo kerf (no el primer homónimo).

    WO 62176: 3×P13 y 6×P32; buscar por nombre movía copias lejanas, el repair
    “éxito” falso 40 veces y el manager vaciaba la hoja.
    """
    try:
        from .sheet_integrity import _es_pieza_real_nombre
    except Exception:
        return None
    piezas = list((hoja or {}).get("piezas") or [])
    items: list[tuple[int, Any]] = []
    for idx, p in enumerate(piezas):
        nom = str((p or {}).get("nombre") or "")
        if not _es_pieza_real_nombre(nom):
            continue
        poly = _poly_pieza_reparacion(p)
        if poly is None:
            continue
        items.append((idx, poly))
    best: tuple[float, int, int] | None = None
    for a in range(len(items)):
        ia, pa = items[a]
        for b in range(a + 1, len(items)):
            ib, pb = items[b]
            try:
                dist = float(pa.distance(pb))
            except Exception:
                continue
            if dist + 1e-6 >= float(min_gap_mm):
                continue
            if best is None or dist < best[0]:
                best = (dist, ia, ib)
    if best is None:
        return None
    return best[1], best[2]


def _plate_slack_in_dir(poly, ux: float, uy: float, w: float, h: float, margin_mm: float) -> float:
    """Cuánto se puede trasladar ``poly`` en (ux, uy) sin romper placa→pieza."""
    if poly is None or w <= 0 or h <= 0 or margin_mm <= 0:
        return 1e12
    try:
        minx, miny, maxx, maxy = poly.bounds
    except Exception:
        return 0.0
    slack = 1e12
    if ux > 1e-12:
        slack = min(slack, ((w - margin_mm) - float(maxx)) / ux)
    elif ux < -1e-12:
        slack = min(slack, (float(minx) - margin_mm) / (-ux))
    if uy > 1e-12:
        slack = min(slack, ((h - margin_mm) - float(maxy)) / uy)
    elif uy < -1e-12:
        slack = min(slack, (float(miny) - margin_mm) / (-uy))
    return max(0.0, float(slack))


def _intentar_separar_par_kerf(
    hoja: dict,
    ia: int,
    ib: int,
    *,
    kerf_in: float,
    tol_in: float,
    margin_in: float,
    w_placa: float,
    h_placa: float,
) -> bool:
    """Empuja el par violador hasta kerf de tabla. True si el gap del par quedó OK.

    Evita el culerismo de expulsar 10–15 piezas cuando el packer solo quedó
    a ~0.18\" (faltan ~1–2 mm). Expulsión queda como último recurso.
    """
    piezas = list(hoja.get("piezas") or [])
    if ia < 0 or ib < 0 or ia >= len(piezas) or ib >= len(piezas):
        return False
    pa, pb = piezas[ia], piezas[ib]
    poly_a, poly_b = _poly_pieza_reparacion(pa), _poly_pieza_reparacion(pb)
    if poly_a is None or poly_b is None:
        return False

    kerf_full_mm = float(kerf_in) * 25.4
    min_gap_mm = max(0.0, (float(kerf_in) - float(tol_in)) * 25.4)
    try:
        dist0 = float(poly_a.distance(poly_b))
    except Exception:
        return False
    if dist0 + 1e-6 >= min_gap_mm:
        return True
    # Empujar al kerf COMPLETO de tabla (no a kerf−tol).
    need = (kerf_full_mm - dist0) + 0.15

    # Quién se mueve: guest en orificio, si no la de menor área.
    move_idx, fixed_poly = ib, poly_a
    move_p = pb
    if _poly_en_cavidad_de(poly_a, poly_b):
        move_idx, fixed_poly, move_p = ia, poly_b, pa
    elif _poly_en_cavidad_de(poly_b, poly_a):
        move_idx, fixed_poly, move_p = ib, poly_a, pb
    else:
        area_a = float((pa or {}).get("area") or getattr(poly_a, "area", 0) or 0)
        area_b = float((pb or {}).get("area") or getattr(poly_b, "area", 0) or 0)
        if area_a <= area_b:
            move_idx, fixed_poly, move_p = ia, poly_b, pa
        else:
            move_idx, fixed_poly, move_p = ib, poly_a, pb

    move_poly = _poly_pieza_reparacion(move_p)
    if move_poly is None:
        return False

    try:
        from shapely.ops import nearest_points

        p_move, p_fix = nearest_points(move_poly, fixed_poly)
        vx = float(p_move.x) - float(p_fix.x)
        vy = float(p_move.y) - float(p_fix.y)
    except Exception:
        c0, c1 = move_poly.centroid, fixed_poly.centroid
        vx = float(c0.x) - float(c1.x)
        vy = float(c0.y) - float(c1.y)
    norm = (vx * vx + vy * vy) ** 0.5
    if norm < 1e-9:
        # Solape casi total: empuja en +X.
        vx, vy, norm = 1.0, 0.0, 1.0
    ux, uy = vx / norm, vy / norm

    try:
        from .venom_ai import _translate_piece_data
    except Exception:
        return False

    # Placa→pieza es 0.250" completo (nunca menos). No gastar la holgura 0.002".
    margin_mm = max(0.0, float(margin_in) * 25.4)
    w = float(w_placa or 0.0)
    h = float(h_placa or 0.0)
    keys_snap = ("poly", "poly_exact", "poligonos", "marcas", "shift_x", "shift_y")

    def _snap(p: dict) -> dict:
        return {k: p.get(k) for k in keys_snap}

    def _restore(p: dict, snap: dict) -> None:
        for k, v in snap.items():
            if v is None and k in p:
                p.pop(k, None)
            elif v is not None:
                p[k] = v

    def _in_plate(poly) -> bool:
        if w <= 0 or h <= 0 or margin_mm <= 0:
            return True
        try:
            b = poly.bounds
        except Exception:
            return False
        return not (
            b[0] + 1e-6 < margin_mm
            or b[1] + 1e-6 < margin_mm
            or b[2] > w - margin_mm + 1e-6
            or b[3] > h - margin_mm + 1e-6
        )

    def _pair_ok(poly_m, poly_f) -> bool:
        try:
            if float(poly_m.intersection(poly_f).area) > 25.0:
                return False
            return float(poly_m.distance(poly_f)) + 1e-6 >= kerf_full_mm
        except Exception:
            return False

    def _vs_others(poly, skip: set[int], poly_before=None) -> bool:
        """No solapar. No empeorar un vecino que ya estaba bajo kerf (se reubica)."""
        for k, other in enumerate(piezas):
            if k in skip:
                continue
            op = _poly_pieza_reparacion(other)
            if op is None:
                continue
            try:
                if float(poly.intersection(op).area) > 25.0:
                    return False
                dist = float(poly.distance(op))
            except Exception:
                return False
            if dist + 1e-6 >= min_gap_mm:
                continue
            if poly_before is None:
                return False
            try:
                d0 = float(poly_before.distance(op))
            except Exception:
                return False
            if dist + 0.05 < d0:
                return False
        return True

    dirs = [(ux, uy), (-ux, -uy), (1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)]
    seen_dir = set()
    uniq_dirs = []
    for dxu, dyu in dirs:
        key = (round(dxu, 4), round(dyu, 4))
        if key in seen_dir:
            continue
        seen_dir.add(key)
        uniq_dirs.append((dxu, dyu))

    skip_one = {ia, ib}

    # 0) Abrir el par gastando holgura de placa (P64 en rincón: la de afuera
    # no puede salir del 0.250"; se mueve solo la que mira al centro).
    pa_p, pb_p = piezas[ia], piezas[ib]
    poly_a0 = _poly_pieza_reparacion(pa_p)
    poly_b0 = _poly_pieza_reparacion(pb_p)
    if poly_a0 is not None and poly_b0 is not None:
        for dxu, dyu in uniq_dirs:
            slack_a = _plate_slack_in_dir(poly_a0, -dxu, -dyu, w, h, margin_mm)
            slack_b = _plate_slack_in_dir(poly_b0, dxu, dyu, w, h, margin_mm)
            if slack_a + slack_b + 1e-6 < need:
                continue
            if slack_b >= slack_a:
                take_b = min(slack_b, need)
                take_a = need - take_b
            else:
                take_a = min(slack_a, need)
                take_b = need - take_a
            if take_a > slack_a + 1e-6 or take_b > slack_b + 1e-6:
                continue
            sa, sb = _snap(pa_p), _snap(pb_p)
            try:
                if take_a > 1e-9:
                    _translate_piece_data(pa_p, -dxu * take_a, -dyu * take_a)
                if take_b > 1e-9:
                    _translate_piece_data(pb_p, dxu * take_b, dyu * take_b)
                na, nb = _poly_pieza_reparacion(pa_p), _poly_pieza_reparacion(pb_p)
                if (
                    na is not None
                    and nb is not None
                    and _in_plate(na)
                    and _in_plate(nb)
                    and _pair_ok(na, nb)
                    and _vs_others(na, skip_one, poly_a0)
                    and _vs_others(nb, skip_one, poly_b0)
                ):
                    piezas[ia], piezas[ib] = pa_p, pb_p
                    hoja["piezas"] = piezas
                    return True
            except Exception:
                pass
            _restore(pa_p, sa)
            _restore(pb_p, sb)

    # 1) Un solo lado, varios ejes (grid P64: nearest_points choca con el vecino).
    poly_move0 = _poly_pieza_reparacion(move_p)
    for dxu, dyu in uniq_dirs:
        for scale in (1.0, 1.25, 1.5, 2.0, 3.0, 4.0):
            snap = _snap(move_p)
            try:
                _translate_piece_data(move_p, dxu * need * scale, dyu * need * scale)
                moved = _poly_pieza_reparacion(move_p)
                if (
                    moved is not None
                    and _in_plate(moved)
                    and _pair_ok(moved, fixed_poly)
                    and _vs_others(moved, skip_one, poly_move0)
                ):
                    piezas[move_idx] = move_p
                    hoja["piezas"] = piezas
                    return True
            except Exception:
                pass
            _restore(move_p, snap)

    # 2) Abrir el par por ambos lados (espejo Cal 2: 0.355" → 0.375").
    pa_p, pb_p = piezas[ia], piezas[ib]
    poly_a0 = _poly_pieza_reparacion(pa_p)
    poly_b0 = _poly_pieza_reparacion(pb_p)
    for dxu, dyu in uniq_dirs:
        half = need * 0.5
        sa, sb = _snap(pa_p), _snap(pb_p)
        try:
            _translate_piece_data(pa_p, -dxu * half, -dyu * half)
            _translate_piece_data(pb_p, dxu * half, dyu * half)
            na, nb = _poly_pieza_reparacion(pa_p), _poly_pieza_reparacion(pb_p)
            if (
                na is not None
                and nb is not None
                and _in_plate(na)
                and _in_plate(nb)
                and _pair_ok(na, nb)
                and _vs_others(na, skip_one, poly_a0)
                and _vs_others(nb, skip_one, poly_b0)
            ):
                piezas[ia], piezas[ib] = pa_p, pb_p
                hoja["piezas"] = piezas
                return True
        except Exception:
            pass
        _restore(pa_p, sa)
        _restore(pb_p, sb)

    # 3) Acordeón: mueve el bloque entero del lado +eje (abre ~mm, no desparrama).
    poly_a0 = _poly_pieza_reparacion(piezas[ia])
    poly_b0 = _poly_pieza_reparacion(piezas[ib])
    if poly_a0 is not None and poly_b0 is not None:
        for dxu, dyu in uniq_dirs:
            ca, cb = poly_a0.centroid, poly_b0.centroid
            mid = ((float(ca.x) + float(cb.x)) * 0.5, (float(ca.y) + float(cb.y)) * 0.5)
            plus = []
            for k, pz in enumerate(piezas):
                pk = _poly_pieza_reparacion(pz)
                if pk is None:
                    continue
                ck = pk.centroid
                if (float(ck.x) - mid[0]) * dxu + (float(ck.y) - mid[1]) * dyu >= -1e-6:
                    plus.append(k)
            if len(plus) < 1 or len(plus) >= len(piezas):
                continue
            snaps = {k: _snap(piezas[k]) for k in plus}
            try:
                for k in plus:
                    _translate_piece_data(piezas[k], dxu * need, dyu * need)
                ok_acc = True
                new_polys = []
                for pz in piezas:
                    np = _poly_pieza_reparacion(pz)
                    if np is None or not _in_plate(np):
                        ok_acc = False
                        break
                    new_polys.append(np)
                if ok_acc:
                    for ii in range(len(new_polys)):
                        for jj in range(ii + 1, len(new_polys)):
                            try:
                                if float(new_polys[ii].intersection(new_polys[jj]).area) > 25.0:
                                    ok_acc = False
                                    break
                            except Exception:
                                ok_acc = False
                                break
                        if not ok_acc:
                            break
                if ok_acc:
                    try:
                        if not _pair_ok(new_polys[ia], new_polys[ib]):
                            ok_acc = False
                    except Exception:
                        ok_acc = False
                if ok_acc:
                    hoja["piezas"] = piezas
                    return True
            except Exception:
                ok_acc = False
            for k, sn in snaps.items():
                _restore(piezas[k], sn)

    return False


def _poly_legal_en_placa(poly, others, *, kerf_mm: float, margin_mm: float, w: float, h: float) -> bool:
    if poly is None:
        return False
    try:
        b = poly.bounds
    except Exception:
        return False
    if (
        b[0] + 1e-6 < margin_mm
        or b[1] + 1e-6 < margin_mm
        or b[2] > w - margin_mm + 1e-6
        or b[3] > h - margin_mm + 1e-6
    ):
        return False
    for op in others:
        if op is None:
            continue
        try:
            if float(poly.intersection(op).area) > 25.0:
                return False
            if float(poly.distance(op)) + 1e-6 < kerf_mm:
                return False
        except Exception:
            return False
    return True


def _reubicar_pieza_en_hueco(
    hoja: dict,
    idx: int,
    *,
    kerf_in: float,
    margin_in: float,
    w_placa: float,
    h_placa: float,
) -> bool:
    """Si no se puede abrir el par, mueve la pieza al hueco libre de la MISMA placa."""
    piezas = list(hoja.get("piezas") or [])
    if idx < 0 or idx >= len(piezas):
        return False
    move_p = piezas[idx]
    poly0 = _poly_pieza_reparacion(move_p)
    if poly0 is None:
        return False
    try:
        from .venom_ai import _translate_piece_data
    except Exception:
        return False

    kerf_mm = float(kerf_in) * 25.4
    margin_mm = max(0.0, float(margin_in) * 25.4)
    w = float(w_placa or 0.0)
    h = float(h_placa or 0.0)
    if w <= 0 or h <= 0:
        return False
    others = []
    for k, other in enumerate(piezas):
        if k == idx:
            continue
        op = _poly_pieza_reparacion(other)
        if op is not None:
            others.append(op)
    b0 = poly0.bounds
    pw, ph = float(b0[2] - b0[0]), float(b0[3] - b0[1])
    if pw <= 0 or ph <= 0:
        return False

    keys_snap = ("poly", "poly_exact", "poligonos", "marcas", "shift_x", "shift_y")

    def _snap(p: dict) -> dict:
        return {k: p.get(k) for k in keys_snap}

    def _restore(p: dict, snap: dict) -> None:
        for k, v in snap.items():
            if v is None and k in p:
                p.pop(k, None)
            elif v is not None:
                p[k] = v

    def _try_xy(x: float, y: float) -> bool:
        dx, dy = x - b0[0], y - b0[1]
        snap = _snap(move_p)
        try:
            _translate_piece_data(move_p, dx, dy)
            moved = _poly_pieza_reparacion(move_p)
            if _poly_legal_en_placa(
                moved, others, kerf_mm=kerf_mm, margin_mm=margin_mm, w=w, h=h
            ):
                piezas[idx] = move_p
                hoja["piezas"] = piezas
                return True
        except Exception:
            pass
        _restore(move_p, snap)
        return False

    xmin, ymin = margin_mm, margin_mm
    xmax, ymax = w - margin_mm - pw, h - margin_mm - ph
    if xmax < xmin or ymax < ymin:
        return False

    # Hop local (derecha / arriba de ESTA pieza). Nunca al maxx global:
    # Ultra flojo + max(others) mandaba a X=900 y destrozaba el nido.
    hop = max(pw, ph) + kerf_mm + 1.0
    cands = [
        (min(xmax, max(xmin, float(b0[2]) + kerf_mm)), min(ymax, max(ymin, float(b0[1])))),
        (min(xmax, max(xmin, float(b0[0]))), min(ymax, max(ymin, float(b0[3]) + kerf_mm))),
    ]

    seen = set()
    for x, y in cands:
        if abs(x - b0[0]) > hop and abs(y - b0[1]) > hop:
            continue
        key = (round(x, 1), round(y, 1))
        if key in seen:
            continue
        seen.add(key)
        if x < xmin - 1e-6 or y < ymin - 1e-6 or x > xmax + 1e-6 or y > ymax + 1e-6:
            continue
        if _try_xy(x, y):
            print(
                f"[POKA-KERF-RELOC] idx={idx} hop -> ({x:.1f},{y:.1f})",
                flush=True,
            )
            return True
    return False


def colocar_piezas_cerca_origen(
    hoja: dict,
    pool: list,
    *,
    kerf_in: float,
    margin_in: float,
    w_placa: float,
    h_placa: float,
) -> list:
    """Reinyecta expulsadas junto al origen (lado izquierdo), no al bbox global."""
    if not pool or not isinstance(hoja, dict):
        return list(pool or [])
    try:
        from shapely import affinity

        from .venom_ai import _translate_piece_data
    except Exception:
        return list(pool)
    kerf_mm = float(kerf_in) * 25.4
    margin_mm = max(0.0, float(margin_in) * 25.4)
    w = float(w_placa or 0.0)
    h = float(h_placa or 0.0)
    leftover: list = []
    for raw in pool:
        if not isinstance(raw, dict):
            continue
        p = dict(raw)
        poly0 = _poly_pieza_reparacion(p)
        if poly0 is None:
            leftover.append(raw)
            continue
        try:
            minx, miny, maxx, maxy = poly0.bounds
            if abs(minx) > 1e-6 or abs(miny) > 1e-6:
                _translate_piece_data(p, -minx, -miny)
                poly0 = _poly_pieza_reparacion(p)
                minx, miny, maxx, maxy = poly0.bounds
        except Exception:
            leftover.append(raw)
            continue
        pw, ph = float(maxx - minx), float(maxy - miny)
        others = [
            op
            for op in (
                _poly_pieza_reparacion(o) for o in (hoja.get("piezas") or [])
            )
            if op is not None
        ]
        placed = False
        xmax = min(w - margin_mm - pw, max(margin_mm, w * 0.42))
        ymax = h - margin_mm - ph
        step = max(8.0, min(pw, ph, 25.0) * 0.35)
        x = margin_mm
        while x <= xmax + 1e-6 and not placed:
            y = margin_mm
            while y <= ymax + 1e-6 and not placed:
                dx, dy = x - minx, y - miny
                try:
                    test = affinity.translate(poly0, dx, dy)
                except Exception:
                    y += step
                    continue
                if _poly_legal_en_placa(
                    test, others, kerf_mm=kerf_mm, margin_mm=margin_mm, w=w, h=h
                ):
                    _translate_piece_data(p, dx, dy)
                    hoja.setdefault("piezas", []).append(p)
                    placed = True
                    print(
                        f"[POKA-KERF-REINJECT] {p.get('nombre')} -> ({x:.1f},{y:.1f})",
                        flush=True,
                    )
                    break
                y += step
            x += step
        if not placed:
            leftover.append(raw)
    return leftover


def reparar_separacion_minima_hoja(
    hoja: dict,
    kerf_in: float | None = None,
    *,
    margin_in: float | None = None,
    w_placa: float | None = None,
    h_placa: float | None = None,
    clave: str = "",
    tol_in: float = TABLA_GAP_EPS_IN,
    max_rounds: int = 40,
    permitir_expulsar: bool = True,
) -> tuple[bool, str, list[dict]]:
    """Corrige gaps < TABLA: primero empuja (~mm), si no cabe expulsa.

    ``permitir_expulsar=False``: no reacomoda a lo bruto (renest). Solo nudge.
    """
    expulsadas: list[dict] = []
    if not isinstance(hoja, dict):
        return False, "hoja_invalida", expulsadas

    # Resolver kerf/margin efectivos una vez (misma regla que validar).
    try:
        from .cut_gaps_table import gaps_efectivos_para_hoja

        kerf_tabla, margin_tabla = gaps_efectivos_para_hoja(
            hoja,
            clave=clave or str(hoja.get("clave") or ""),
            kerf_fallback=kerf_in,
            margin_fallback=margin_in,
        )
    except Exception:
        kerf_tabla = float(kerf_in or hoja.get("kerf_usado") or 0.25)
        margin_tabla = float(
            margin_in if margin_in is not None else (hoja.get("margin_usado") or 0.25)
        )
    try:
        kerf_caller = float(kerf_in) if kerf_in is not None else 0.0
    except Exception:
        kerf_caller = 0.0
    kerf_eff = max(float(kerf_tabla), kerf_caller) if kerf_caller > 0 else float(kerf_tabla)
    try:
        margin_caller = float(margin_in) if margin_in is not None else 0.0
    except Exception:
        margin_caller = 0.0
    margin_eff = (
        max(float(margin_tabla), margin_caller) if margin_caller > 0 else float(margin_tabla)
    )
    try:
        w_eff = float(w_placa if w_placa is not None else (hoja.get("placa_w") or 0))
        h_eff = float(h_placa if h_placa is not None else (hoja.get("placa_h") or 0))
    except Exception:
        w_eff, h_eff = 0.0, 0.0

    nudged = 0
    for _ in range(max(1, int(max_rounds))):
        ok, detail = validar_separacion_minima_hoja(
            hoja,
            kerf_in,
            margin_in=margin_in,
            w_placa=w_placa,
            h_placa=h_placa,
            clave=clave,
            tol_in=tol_in,
        )
        if ok:
            if nudged:
                return True, f"ok_separado nudges={nudged}", expulsadas
            return True, detail, expulsadas
        if str(detail).startswith("margen_placa"):
            # El nest ya debía respetar 0.250"; no empujar post-facto.
            return False, detail, expulsadas
        if not str(detail).startswith("gap_insuficiente"):
            return False, detail, expulsadas

        min_gap_mm = max(0.0, (float(kerf_eff) - float(tol_in)) * 25.4)
        par = _indices_par_gap_insuficiente(hoja, min_gap_mm)
        piezas = list(hoja.get("piezas") or [])
        if par is None:
            return False, detail, expulsadas
        ia, ib = par
        if ia < 0 or ib < 0 or ia >= len(piezas) or ib >= len(piezas):
            return False, detail, expulsadas
        pa, pb = piezas[ia], piezas[ib]

        if _intentar_separar_par_kerf(
            hoja,
            ia,
            ib,
            kerf_in=kerf_eff,
            tol_in=tol_in,
            margin_in=margin_eff,
            w_placa=w_eff,
            h_placa=h_eff,
        ):
            nudged += 1
            continue

        # Renest: el motor ya nesté; no expulsar / saltar a otro hueco.
        if not permitir_expulsar:
            return False, detail, expulsadas

        poly_a, poly_b = _poly_pieza_reparacion(pa), _poly_pieza_reparacion(pb)
        drop_idx = ib
        in_cavity = False
        # Guest en cavidad del otro → expulsar guest (no el host grande).
        if poly_a is not None and poly_b is not None:
            if _poly_en_cavidad_de(poly_a, poly_b):
                drop_idx = ia
                in_cavity = True
            elif _poly_en_cavidad_de(poly_b, poly_a):
                drop_idx = ib
                in_cavity = True
            else:
                area_a = float((pa or {}).get("area") or getattr(poly_a, "area", 0) or 0)
                area_b = float((pb or {}).get("area") or getattr(poly_b, "area", 0) or 0)
                drop_idx = ia if area_a <= area_b else ib

        if not in_cavity and _reubicar_pieza_en_hueco(
            hoja,
            drop_idx,
            kerf_in=kerf_eff,
            margin_in=margin_eff,
            w_placa=w_eff,
            h_placa=h_eff,
        ):
            nudged += 1
            continue

        expulsadas.append(piezas.pop(drop_idx))
        hoja["piezas"] = piezas

    ok_f, det_f = validar_separacion_minima_hoja(
        hoja,
        kerf_in,
        margin_in=margin_in,
        w_placa=w_placa,
        h_placa=h_placa,
        clave=clave,
        tol_in=tol_in,
    )
    return ok_f, det_f, expulsadas


def validar_margin_in(margin: float) -> tuple[float, Optional[str]]:
    try:
        m = float(margin)
    except Exception:
        return 0.0, f"Margin no numérico ({margin!r})."
    if m < 0:
        return 0.0, f"Margin inválido ({m}). No puede ser negativo."
    if m > 5.0:
        return 0.0, f"Margin fuera de rango ({m} in). Máximo razonable: 5.0 in."
    return m, None


def marcar_pack_fault(hoja: dict, motivo: str) -> dict:
    out = dict(hoja or {})
    out["_pack_engine_fault"] = str(motivo or "unknown")
    out.setdefault("piezas", [])
    out.setdefault("area_usada", 0.0)
    out.setdefault("eficiencia", 0.0)
    return out


def es_pack_fault(hoja: Any) -> bool:
    return isinstance(hoja, dict) and bool(hoja.get("_pack_engine_fault"))


def motivo_pack_fault(hoja: Any) -> str:
    if not isinstance(hoja, dict):
        return ""
    return str(hoja.get("_pack_engine_fault") or "")


def validar_solapes_hojas_fail_closed(
    hojas,
    *,
    incluir_retazos: bool = True,
) -> tuple[bool, str]:
    """
    True, "" si OK.
    False, msg si hay solape O si el validador no pudo correr (fail-closed).

    Por defecto incluye hojas RTZ reales (es_retazo). Solo omite overlays
    virtuales (cu_rtz_virtual) que no son metal de corte.
    """
    from .sheet_integrity import hoja_tiene_solapes_metal

    for hx in hojas or []:
        if not isinstance(hx, dict):
            continue
        if hx.get("cu_rtz_virtual"):
            continue
        if hx.get("es_retazo") and not incluir_retazos:
            continue
        try:
            solapa, detalle = hoja_tiene_solapes_metal(hx)
        except Exception as exc:
            return False, f"No se pudo validar solapes: {exc}"
        det = str(detalle or "")
        if det.startswith("validacion_solape_no_disponible"):
            return False, (
                "Validación de solapes no disponible (fail-closed). "
                f"Detalle: {det}"
            )
        if solapa:
            tipo = "RTZ" if hx.get("es_retazo") else "placa"
            return False, (
                f"Piezas solapadas en {tipo} {hx.get('placa_id')}: {det}"
            )
    return True, ""


def es_resultado_grupo_fallido(resultado) -> bool:
    """True si el dict de grupo (o mapa de grupos) tiene fallo duro de integridad."""
    if not isinstance(resultado, dict):
        return True
    if resultado.get("error") or resultado.get("inventario_incompleto"):
        return True
    # Mapa multi-calibre (sin hojas propias): revisar hijos.
    if "hojas" not in resultado:
        for k, v in resultado.items():
            if str(k).startswith("_"):
                continue
            if isinstance(v, dict) and (
                v.get("error") or v.get("inventario_incompleto")
            ):
                return True
    return False


def aplicar_resultado_inventario(
    grupo: dict,
    *,
    ok_inv: bool,
    msg_inv: str,
) -> None:
    """
    Poka-yoke: inventario incompleto → error duro (salvo escape env).
    """
    if not isinstance(grupo, dict):
        return
    if ok_inv:
        grupo.pop("advertencia", None)
        grupo.pop("inventario_incompleto", None)
        return
    grupo["inventario_incompleto"] = True
    if allow_incomplete_nest():
        grupo["advertencia"] = msg_inv
        grupo.pop("error", None)
    else:
        grupo["error"] = (
            f"{msg_inv} "
            "(Poka-yoke: nest incompleto rechazado. "
            "Para forzar aviso: ARGA_ALLOW_INCOMPLETE_NEST=1)"
        )
        grupo["advertencia"] = msg_inv


def edad_cache_placas_horas() -> tuple[float | None, str]:
    """
    Edad del snapshot Herinox de placas en horas.
    Returns (horas, fuente). horas=None si no hay meta usable.
    """
    try:
        from modules.herinox_catalog_cache import cargar_snapshot_placas

        _emp, _prov, meta = cargar_snapshot_placas()
    except Exception as exc:
        return None, f"sin_cache:{exc}"
    meta = meta or {}
    raw = meta.get("updated_at")
    if not raw:
        return None, str(meta.get("source") or "sin_updated_at")
    try:
        from datetime import datetime, timezone

        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
        return max(0.0, float(age_h)), str(meta.get("source") or "cache")
    except Exception as exc:
        return None, f"parse_error:{exc}"


def validar_stock_antes_nest(
    datos_placas,
    *,
    max_age_hours: float = 24.0,
) -> tuple[bool, str, str]:
    """
    Poka-yoke stock: sin placas → bloquea; cache vieja → aviso (ok=True, nivel=warn).
    Returns (puede_continuar, mensaje, nivel) donde nivel in {"ok","warn","block"}.
    """
    if not datos_placas:
        return (
            False,
            "No hay placas DISPONIBLE en inventario. "
            "Sincronice Herinox / revise stock antes de nestear.",
            "block",
        )
    age_h, fuente = edad_cache_placas_horas()
    if age_h is None:
        return (
            True,
            f"No se pudo verificar frescura del cache de placas ({fuente}). "
            "Confirme sync Herinox si el stock puede estar desactualizado.",
            "warn",
        )
    if age_h > float(max_age_hours):
        return (
            True,
            f"Cache de placas Herinox tiene ~{age_h:.1f} h ({fuente}). "
            f"Umbral recomendado: {max_age_hours:.0f} h. "
            "Sincronice stock antes de nestear si hubo movimientos recientes.",
            "warn",
        )
    return True, "", "ok"


def _extraer_numero_placa(valor) -> float:
    try:
        if isinstance(valor, (int, float)):
            return float(valor)
        limpio = str(valor).replace("$", "").replace(",", ".").strip()
        import re

        nums = re.findall(r"[-+]?\d*\.\d+|\d+", limpio)
        return float(nums[0]) if nums else 0.0
    except Exception:
        return 0.0


def _agrupar_partes_por_clave(lista_partes) -> dict[str, list]:
    grupos: dict[str, list] = {}
    for item in lista_partes or []:
        try:
            pieza, mat, qty, cal, _st, ruta = item
        except Exception:
            continue
        clave = f"{str(cal).strip().upper()}_{str(mat).strip().upper()}"
        try:
            q = max(0, int(qty or 0))
        except Exception:
            q = 0
        if q <= 0:
            continue
        grupos.setdefault(clave, []).append(
            {
                "nombre": str(pieza or "").strip(),
                "material": str(mat or "").strip(),
                "calibre": str(cal or "").strip(),
                "qty": q,
                "ruta": str(ruta or "").strip(),
            }
        )
    return grupos


def _placas_matching_grupo(
    datos_placas,
    req_cal: str,
    req_mat: str,
    *,
    coinciden,
) -> list[dict]:
    """Misma regla que el motor: SOLO calibre exacto (sin tolerancia %)."""
    try:
        from .manager import MotorNesting

        motor = MotorNesting.__new__(MotorNesting)
        placas, _mode = motor._clasificar_placas_por_calibre(
            req_cal, req_mat, datos_placas
        )
        return [
            {
                "id": p.get("id", ""),
                "w": p["w"],
                "h": p["h"],
                "origen": p.get("origen", "EMPRESA"),
                "w_in": float(p["w"]) / 25.4,
                "h_in": float(p["h"]) / 25.4,
            }
            for p in placas
        ]
    except Exception:
        pass

    # Fallback legacy (exacto vía coinciden)
    placas_empresa: list[dict] = []
    placas_proveedor: list[dict] = []
    for placa in datos_placas or []:
        try:
            p_cal = placa[0] if len(placa) > 0 else ""
            p_mat = placa[1] if len(placa) > 1 else ""
        except Exception:
            continue
        if not coinciden(req_cal, p_cal) or not coinciden(req_mat, p_mat):
            continue
        w_in = _extraer_numero_placa(placa[3] if len(placa) > 3 else 0)
        h_in = _extraer_numero_placa(placa[4] if len(placa) > 4 else 0)
        if w_in <= 0 or h_in <= 0:
            continue
        origen = str(placa[9]).upper() if len(placa) > 9 else "EMPRESA"
        row = {
            "id": str(placa[2]) if len(placa) > 2 else "",
            "w": w_in * 25.4,
            "h": h_in * 25.4,
            "origen": origen,
            "w_in": w_in,
            "h_in": h_in,
        }
        if "EMPRESA" in origen or origen.strip() == "":
            placas_empresa.append(row)
        else:
            placas_proveedor.append(row)
    return placas_empresa if placas_empresa else placas_proveedor


def validar_stock_por_grupos_antes_nest(
    lista_partes,
    datos_placas,
    *,
    coinciden,
    solo_claves: set[str] | None = None,
) -> tuple[bool, str]:
    """
    Poka-yoke INPUT por calibre/material:
    - cada grupo de PARTS debe tener inventario matching
    - cobre: barras largos 144\"×1.75–6\"
    """
    if not lista_partes:
        return False, "No hay piezas en PARTS para nestear."
    if not datos_placas:
        return False, "No hay placas DISPONIBLE en inventario."

    try:
        from .cu_inventory import inventario_barras_largos_cu
    except Exception:
        inventario_barras_largos_cu = None  # type: ignore

    try:
        from interface.utils_nesting import es_material_cobre
    except Exception:
        def es_material_cobre(m):  # type: ignore
            mu = str(m or "").strip().upper()
            return mu in ("CU", "COBRE", "COPPER") or "COBRE" in mu or "COPPER" in mu

    grupos = _agrupar_partes_por_clave(lista_partes)
    if solo_claves:
        grupos = {k: v for k, v in grupos.items() if k in solo_claves}
    if not grupos:
        return False, "No hay grupos calibre/material válidos en PARTS."

    fallas: list[str] = []
    for clave, filas in sorted(grupos.items()):
        partes = str(clave).split("_", 1)
        req_cal = partes[0]
        req_mat = partes[1] if len(partes) > 1 else ""
        n_pz = sum(int(f.get("qty") or 0) for f in filas)
        placas = _placas_matching_grupo(
            datos_placas, req_cal, req_mat, coinciden=coinciden
        )
        if not placas:
            fallas.append(
                f"{clave}: sin inventario de placas para {req_cal} / {req_mat} "
                f"({n_pz} pieza(s) en PARTS)."
            )
            continue
        mat_u = str(req_mat).strip().upper()
        if mat_u == "CU" or es_material_cobre(req_mat):
            if inventario_barras_largos_cu is None:
                continue
            barras = inventario_barras_largos_cu(placas)
            if not barras:
                fallas.append(
                    f"{clave}: hay stock CU pero ninguna barra 144\"×1.75–6\" "
                    f"para largos ({n_pz} pieza(s))."
                )

    if fallas:
        texto = "\n".join(f"• {f}" for f in fallas[:10])
        if len(fallas) > 10:
            texto += f"\n(+{len(fallas) - 10} grupos más)"
        return (
            False,
            "Poka-yoke: hay calibres/materiales sin stock usable.\n"
            "Corrija inventario Herinox o quite esas piezas de PARTS "
            "antes de nestear/renestear.\n\n"
            f"{texto}",
        )
    return True, ""


def listar_fallas_resultados_nest(resultados) -> list[str]:
    """Errores / inventario incompleto / solapes en un dict de grupos."""
    fallas: list[str] = []
    if not isinstance(resultados, dict):
        return ["Resultado de nesting inválido."]
    # Payload top-level de error (p. ej. aborto DXF).
    top_err = resultados.get("error")
    if isinstance(top_err, str) and top_err.strip() and "hojas" not in resultados:
        fallas.append(top_err.strip())
    for clave, info in resultados.items():
        if str(clave).startswith("_"):
            continue
        if not isinstance(info, dict):
            continue
        if info.get("error"):
            fallas.append(f"{clave}: {info.get('error')}")
            continue
        if info.get("inventario_incompleto"):
            fallas.append(
                f"{clave}: {info.get('advertencia') or 'inventario incompleto'}"
            )
            continue
        if "hojas" not in info:
            continue
        ok_s, msg_s = validar_solapes_hojas_fail_closed(info.get("hojas") or [])
        if not ok_s:
            fallas.append(f"{clave}: {msg_s}")
            continue
        ok_b, msg_b = validar_piezas_dentro_placas(info.get("hojas") or [])
        if not ok_b:
            fallas.append(f"{clave}: {msg_b}")
    return fallas


def validar_piezas_dentro_placa(hoja, *, tol_mm: float = 1.5) -> tuple[bool, str]:
    """True si todas las piezas reales caben en el bbox de la placa."""
    if not isinstance(hoja, dict) or hoja.get("cu_rtz_virtual"):
        return True, ""
    try:
        w = float(hoja.get("placa_w") or 0.0)
        h = float(hoja.get("placa_h") or 0.0)
    except Exception:
        return True, ""
    if w <= 0 or h <= 0:
        return True, ""

    from .geometry_parser import reconstruir_poly_seguro
    from .sheet_integrity import piezas_reales_en_hoja

    tol = float(tol_mm)
    for p in piezas_reales_en_hoja(hoja):
        try:
            poly = reconstruir_poly_seguro(p.get("poligonos") or [])
            if poly is None or poly.is_empty:
                continue
            minx, miny, maxx, maxy = poly.bounds
        except Exception as exc:
            return False, (
                f"validacion_bbox_no_disponible:{p.get('nombre')}:{exc}"
            )
        if (
            minx < -tol
            or miny < -tol
            or maxx > w + tol
            or maxy > h + tol
        ):
            return False, (
                f"Pieza fuera de placa {hoja.get('placa_id')}: "
                f"{p.get('nombre')} "
                f"bounds=({minx:.1f},{miny:.1f})-({maxx:.1f},{maxy:.1f}) "
                f"placa={w:.1f}x{h:.1f} mm"
            )
    return True, ""


def validar_piezas_dentro_placas(hojas, *, tol_mm: float = 1.5) -> tuple[bool, str]:
    for hx in hojas or []:
        ok, msg = validar_piezas_dentro_placa(hx, tol_mm=tol_mm)
        if not ok:
            if str(msg).startswith("validacion_bbox_no_disponible"):
                return False, f"Validación bbox no disponible (fail-closed): {msg}"
            return False, msg
    return True, ""


def validar_integridad_bloque_hojas(hojas) -> tuple[bool, str]:
    """Solapes (incl. RTZ) + piezas dentro de placa. Fail-closed."""
    ok_s, msg_s = validar_solapes_hojas_fail_closed(hojas)
    if not ok_s:
        return False, msg_s
    return validar_piezas_dentro_placas(hojas)


def validar_auditoria_dxf_antes_nest(
    audit: dict | None,
    *,
    pending: bool = False,
) -> tuple[bool, str]:
    """
    Poka-yoke input: no arrancar nest caro si la auditoría PARTS aún corre
    o hay DXF omitidos (ruta faltante / ilegible / sin geometría).
    """
    if pending:
        return (
            False,
            "La auditoría de DXF aún está en curso (DXF NESTEO: validando…).\n"
            "Espere a que termine antes de nestear para no desperdiciar tiempo "
            "re-parseando piezas inválidas.",
        )
    if not isinstance(audit, dict):
        return (
            False,
            "No hay auditoría de DXF disponible.\n"
            "Vuelva a PARTS / reimporte el job para validar geometría antes de nestear.",
        )
    total = int(audit.get("total") or 0)
    omitidos = list(audit.get("omitidos") or [])
    if total <= 0:
        return False, "No hay piezas/DXF auditados en PARTS."
    if omitidos:
        lineas = []
        for item in omitidos[:8]:
            if not isinstance(item, dict):
                lineas.append(str(item))
                continue
            pieza = item.get("pieza") or item.get("archivo") or "?"
            err = item.get("error") or "sin detalle"
            lineas.append(f"• {pieza}: {err}")
        extra = ""
        if len(omitidos) > 8:
            extra = f"\n(+{len(omitidos) - 8} más — use VER OMITIDOS en PARTS)"
        return (
            False,
            f"{len(omitidos)} DXF no apto(s) para nesting (de {total}).\n"
            "Repare o cambie esos DXF en PARTS (VER OMITIDOS / REEMPLAZAR DXF)\n"
            "antes de nestear o renestear.\n\n"
            + "\n".join(lineas)
            + extra,
        )
    ok = int(audit.get("ok") or 0)
    if ok <= 0:
        return False, "Ningún DXF pasó la auditoría de geometría."
    if ok < total:
        return (
            False,
            f"Auditoría inconsistente: ok={ok} total={total} sin lista de omitidos.",
        )
    return True, ""


def validar_step_cama_ab_pares(
    step_dir_a: str,
    step_dir_b: str,
    *,
    etiqueta: str = "ROBOT",
    motor_label: str = "STEP",
) -> tuple[bool, str]:
    """
    Poka-yoke: Cama A y Cama B deben tener la misma cantidad de .step
    (mismo set de DXF robot exportados a ambos destinos).
    """
    import glob

    def _count(folder: str) -> int:
        folder = os.path.normpath(str(folder or "").strip())
        if not folder or not os.path.isdir(folder):
            return 0
        files = glob.glob(os.path.join(folder, "*.step"))
        files.extend(glob.glob(os.path.join(folder, "*.STEP")))
        vistos = set()
        n = 0
        for p in files:
            key = os.path.normcase(p)
            if key in vistos:
                continue
            vistos.add(key)
            try:
                if os.path.getsize(p) < 64:
                    continue
            except OSError:
                continue
            n += 1
        return n

    na = _count(step_dir_a)
    nb = _count(step_dir_b)
    if na == 0 and nb == 0:
        return True, ""
    if na != nb:
        return (
            False,
            f"{motor_label} poka-yoke [{etiqueta}]: Cama A tiene {na} STEP y "
            f"Cama B tiene {nb}. Deben coincidir (mismo DXF → A y B).",
        )
    return True, ""
