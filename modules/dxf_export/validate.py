"""Validación post-export por canal (log terminal + archivo)."""
from __future__ import annotations

import math
from typing import Any

from modules.nesting_engine.dxf_export_log import log, _poly_bounds_mm

_OUTER_LAYERS = frozenset({"CUT_OUTER", "OUTER", "CORTE_EXTERNO", "IV_OUTER"})


def _arc_extent_xy(center, radius: float, start_deg: float, end_deg: float) -> list[tuple[float, float]]:
    """Extremos reales de un ARC (no el círculo completo).

    Tratar el ARC como ``center ± r`` inflaba el bbox de joins redondos y
    disparaba falsos positivos de 'medidas vs nest' / 'min-corner' en plasma.
    """
    cx, cy = float(center.x), float(center.y)
    r = float(radius)
    sa = float(start_deg) % 360.0
    ea = float(end_deg) % 360.0
    pts = [
        (cx + r * math.cos(math.radians(sa)), cy + r * math.sin(math.radians(sa))),
        (cx + r * math.cos(math.radians(ea)), cy + r * math.sin(math.radians(ea))),
    ]
    sweep = (ea - sa) % 360.0
    if sweep < 1e-9:
        sweep = 360.0
    for card in (0.0, 90.0, 180.0, 270.0):
        # ¿el cardinal cae dentro del barrido CCW desde sa?
        delta = (card - sa) % 360.0
        if delta <= sweep + 1e-9:
            pts.append((cx + r * math.cos(math.radians(card)), cy + r * math.sin(math.radians(card))))
    return pts


def _entities_bbox_mm(entities) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for ent in entities or []:
        try:
            typ = ent.dxftype()
            if typ == "LINE":
                xs.extend([float(ent.dxf.start.x), float(ent.dxf.end.x)])
                ys.extend([float(ent.dxf.start.y), float(ent.dxf.end.y)])
            elif typ == "ARC":
                for x, y in _arc_extent_xy(
                    ent.dxf.center,
                    float(ent.dxf.radius),
                    float(ent.dxf.start_angle),
                    float(ent.dxf.end_angle),
                ):
                    xs.append(x)
                    ys.append(y)
            elif typ == "CIRCLE":
                r = float(ent.dxf.radius)
                c = ent.dxf.center
                xs.extend([float(c.x) - r, float(c.x) + r])
                ys.extend([float(c.y) - r, float(c.y) + r])
            elif typ == "LWPOLYLINE":
                for x, y, *_ in ent.get_points("xy"):
                    xs.append(float(x))
                    ys.append(float(y))
        except Exception:
            continue
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _count_duplicate_lines(entities, *, layer_set: frozenset[str]) -> int:
    seen: set[tuple[float, float, float, float]] = set()
    dupes = 0
    for ent in entities or []:
        if ent.dxftype() != "LINE":
            continue
        if str(ent.dxf.layer or "").upper() not in layer_set:
            continue
        try:
            k = (
                round(float(ent.dxf.start.x), 2),
                round(float(ent.dxf.start.y), 2),
                round(float(ent.dxf.end.x), 2),
                round(float(ent.dxf.end.y), 2),
            )
            kr = (k[2], k[3], k[0], k[1])
            if k in seen or kr in seen:
                dupes += 1
            seen.add(k)
        except Exception:
            continue
    return dupes


def validate_plasma_piece(
    p: dict,
    entities,
    *,
    offset_mm: float,
    sheet: dict | None = None,
    all_piece_bounds: list[tuple[float, float, float, float]] | None = None,
) -> list[str]:
    """Comprueba medidas, empalmes y separación; devuelve lista de problemas."""
    name = str(p.get("part_name") or p.get("name") or "?")
    issues: list[str] = []
    outer_ents = [
        e
        for e in (entities or [])
        if str(getattr(e.dxf, "layer", "") or "").upper() in _OUTER_LAYERS
    ]
    if not outer_ents:
        issues.append("sin geometría CUT_OUTER exportada")
    else:
        dupes = _count_duplicate_lines(entities, layer_set=_OUTER_LAYERS)
        if dupes > 0:
            issues.append(f"empalmes CUT_OUTER: {dupes} segmento(s) LINE duplicado(s)")

    nest_b = _poly_bounds_mm(p.get("outer") or p.get("outer_poly"))
    cut_b = _entities_bbox_mm(outer_ents)
    off = float(offset_mm or 0.0)
    if nest_b and cut_b and off > 0:
        nw = float(nest_b[2]) - float(nest_b[0])
        nh = float(nest_b[3]) - float(nest_b[1])
        cw = float(cut_b[2]) - float(cut_b[0])
        ch = float(cut_b[3]) - float(cut_b[1])
        exp = 2.0 * off
        tol = max(1.5, off * 0.5)
        dw = cw - nw
        dh = ch - nh
        if abs(dw - exp) > tol or abs(dh - exp) > tol:
            issues.append(
                f"medidas vs nest: nest {nw:.1f}x{nh:.1f} mm, "
                f"corte {cw:.1f}x{ch:.1f} mm "
                f"(delta {dw:.2f}x{dh:.2f} mm; esperado ~+{exp:.2f} mm por lado)"
            )
        nest_pos = (float(nest_b[0]), float(nest_b[1]))
        cut_pos = (float(cut_b[0]), float(cut_b[1]))
        shift_err = math.hypot(cut_pos[0] - nest_pos[0], cut_pos[1] - nest_pos[1])
        if shift_err > max(2.0, off * 1.5):
            issues.append(
                f"posición min-corner desplazada {shift_err:.2f} mm respecto al nest "
                f"(esperado ~{off:.2f} mm por desfase exterior)"
            )

    if sheet and cut_b:
        from modules.nesting_engine.geometry_parser import ESCALA_DXF

        margin_mm = float(sheet.get("margin_usado") or 0.15) * ESCALA_DXF
        kerf_mm = float(sheet.get("kerf_usado") or 0.3) * ESCALA_DXF
        sl = float(sheet.get("length") or 0.0)
        sw = float(sheet.get("width") or 0.0)
        if sl > 0 and sw > 0:
            if float(cut_b[0]) < margin_mm - 0.5:
                issues.append(
                    f"margen placa X: corte minX={cut_b[0]:.1f} mm < margen {margin_mm:.1f} mm"
                )
            if float(cut_b[1]) < margin_mm - 0.5:
                issues.append(
                    f"margen placa Y: corte minY={cut_b[1]:.1f} mm < margen {margin_mm:.1f} mm"
                )
            if float(cut_b[2]) > sl - margin_mm + 0.5:
                issues.append(
                    f"margen placa X max: corte maxX={cut_b[2]:.1f} > "
                    f"placa {sl:.1f} - margen {margin_mm:.1f} mm"
                )
            if float(cut_b[3]) > sw - margin_mm + 0.5:
                issues.append(
                    f"margen placa Y max: corte maxY={cut_b[3]:.1f} > "
                    f"placa {sw:.1f} - margen {margin_mm:.1f} mm"
                )

        if cut_b and all_piece_bounds:
            min_gap = kerf_mm
            cx0, cy0, cx1, cy1 = cut_b
            for ob in all_piece_bounds:
                if ob is cut_b:
                    continue
                gap = _bbox_separation_mm(cut_b, ob)
                if gap is not None and gap < min_gap - 0.5:
                    issues.append(
                        f"separación pieza-pieza {gap:.1f} mm < kerf nest {min_gap:.1f} mm"
                    )
                    break

    for msg in issues:
        log(f"    plasma[{name}] VALIDACION: {msg}", level="ERROR")
    return issues


def _bbox_separation_mm(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float | None:
    """Distancia mínima entre bboxes axis-aligned (0 si se solapan)."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    dx = max(0.0, max(ax0 - bx1, bx0 - ax1))
    dy = max(0.0, max(ay0 - by1, by0 - ay1))
    if dx == 0 and dy == 0:
        return 0.0
    return math.hypot(dx, dy)
