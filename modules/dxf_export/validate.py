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


def _arc_segments_xy(
    center, radius: float, start_deg: float, end_deg: float
) -> list[tuple[float, float, float, float]]:
    """Discretiza un ARC en segmentos con cuerda <= ~0.4 mm."""
    cx, cy = float(center.x), float(center.y)
    r = max(float(radius), 0.0)
    if r <= 1e-9:
        return []
    sa = math.radians(float(start_deg))
    sweep = math.radians((float(end_deg) - float(start_deg)) % 360.0) or (2.0 * math.pi)
    pasos = max(2, min(256, int(math.ceil(r * sweep / 0.4))))
    pts = [
        (cx + r * math.cos(sa + sweep * i / pasos), cy + r * math.sin(sa + sweep * i / pasos))
        for i in range(pasos + 1)
    ]
    return [(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) for i in range(pasos)]


def _clearance_segments(entities) -> list[tuple[float, float, float, float]]:
    """Contorno de corte como segmentos rectos, en mm.

    Sirve para medir separación real entre piezas: los bounding boxes mienten
    en cuanto dos perfiles con escalones se entrelazan.
    """
    segs: list[tuple[float, float, float, float]] = []
    for ent in entities or []:
        try:
            typ = ent.dxftype()
            if typ == "LINE":
                segs.append(
                    (
                        float(ent.dxf.start.x),
                        float(ent.dxf.start.y),
                        float(ent.dxf.end.x),
                        float(ent.dxf.end.y),
                    )
                )
            elif typ == "ARC":
                segs.extend(
                    _arc_segments_xy(
                        ent.dxf.center,
                        float(ent.dxf.radius),
                        float(ent.dxf.start_angle),
                        float(ent.dxf.end_angle),
                    )
                )
            elif typ == "CIRCLE":
                segs.extend(
                    _arc_segments_xy(ent.dxf.center, float(ent.dxf.radius), 0.0, 360.0)
                )
            elif typ == "LWPOLYLINE":
                pts = [(float(x), float(y)) for x, y, *_ in ent.get_points("xy")]
                if getattr(ent, "closed", False) and len(pts) >= 3:
                    pts.append(pts[0])
                for i in range(len(pts) - 1):
                    segs.append((pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]))
        except Exception:
            continue
    return segs


def piece_clearance_record(entities) -> dict[str, Any] | None:
    """bbox + segmentos del contorno exterior, para validar separación."""
    outer = [
        e
        for e in (entities or [])
        if str(getattr(e.dxf, "layer", "") or "").upper() in _OUTER_LAYERS
    ]
    bbox = _entities_bbox_mm(outer) or _entities_bbox_mm(entities)
    if bbox is None:
        return None
    return {"bbox": bbox, "segs": _clearance_segments(outer or entities)}


def _record_bbox(rec) -> tuple[float, float, float, float] | None:
    if isinstance(rec, dict):
        b = rec.get("bbox")
        return tuple(b) if b else None  # type: ignore[return-value]
    if rec and len(rec) == 4:
        return tuple(rec)  # type: ignore[return-value]
    return None


def _record_segments(rec) -> list[tuple[float, float, float, float]]:
    return list(rec.get("segs") or []) if isinstance(rec, dict) else []


def _dist_punto_segmento(px: float, py: float, seg) -> float:
    x0, y0, x1, y1 = seg
    dx, dy = x1 - x0, y1 - y0
    largo2 = dx * dx + dy * dy
    if largo2 <= 1e-18:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / largo2))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def _segments_min_distance_mm(
    a: list[tuple[float, float, float, float]],
    b: list[tuple[float, float, float, float]],
    *,
    limit: float,
) -> float | None:
    """Distancia mínima real entre dos contornos discretizados.

    En dos segmentos disjuntos el mínimo se alcanza siempre en un extremo de
    alguno de los dos, así que basta evaluar punto-a-segmento en ambos sentidos.
    Devuelve ``None`` si falta geometría para decidir (el caller no reprueba).
    """
    if not a or not b:
        return None
    mejor = float("inf")
    for sa in a:
        ax0, ay0, ax1, ay1 = sa
        amin_x, amax_x = (ax0, ax1) if ax0 <= ax1 else (ax1, ax0)
        amin_y, amax_y = (ay0, ay1) if ay0 <= ay1 else (ay1, ay0)
        for sb in b:
            bx0, by0, bx1, by1 = sb
            bmin_x, bmax_x = (bx0, bx1) if bx0 <= bx1 else (bx1, bx0)
            bmin_y, bmax_y = (by0, by1) if by0 <= by1 else (by1, by0)
            # Poda por caja del par: barato y descarta la mayoría.
            gx = max(0.0, max(amin_x - bmax_x, bmin_x - amax_x))
            gy = max(0.0, max(amin_y - bmax_y, bmin_y - amax_y))
            if math.hypot(gx, gy) >= mejor:
                continue
            d = min(
                _dist_punto_segmento(ax0, ay0, sb),
                _dist_punto_segmento(ax1, ay1, sb),
                _dist_punto_segmento(bx0, by0, sa),
                _dist_punto_segmento(bx1, by1, sa),
            )
            if d < mejor:
                mejor = d
                if mejor <= 1e-9:
                    return 0.0
    if mejor == float("inf"):
        return None
    return mejor


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
    all_piece_bounds: list[Any] | None = None,
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

        # Nunca inventar kerf/margen: la tabla oficial los fija por calibre y un
        # default equivocado hace daño en los dos sentidos. Suponer kerf 0.30"
        # reprobaba un cal 14 (tabla: 0.150") y bloqueaba el export de un nest
        # correcto; suponer margen 0.15" dejaba pasar violaciones de los 0.250"
        # reales. Si la hoja no trae el dato, no se juzga.
        margin_in = float(sheet.get("margin_usado") or 0.0)
        kerf_in = float(sheet.get("kerf_usado") or 0.0)
        margin_mm = margin_in * ESCALA_DXF
        kerf_mm = kerf_in * ESCALA_DXF
        sl = float(sheet.get("length") or 0.0)
        sw = float(sheet.get("width") or 0.0)
        if margin_in > 0 and sl > 0 and sw > 0:
            # `margin_usado` es la distancia final PLACA→PIEZA configurada por
            # planta. El packer recibe el perfil ya compensado, por lo que el
            # DXF CUT_OUTER debe respetarla completa: restar `off` aquí
            # validaría un nest nominal y permitiría que la compensación
            # invada el margen real.
            margen_min = margin_mm
            if float(cut_b[0]) < margen_min - 0.5:
                issues.append(
                    f"margen placa X: corte minX={cut_b[0]:.1f} mm < margen {margen_min:.1f} mm"
                )
            if float(cut_b[1]) < margen_min - 0.5:
                issues.append(
                    f"margen placa Y: corte minY={cut_b[1]:.1f} mm < margen {margen_min:.1f} mm"
                )
            if float(cut_b[2]) > sl - margen_min + 0.5:
                issues.append(
                    f"margen placa X max: corte maxX={cut_b[2]:.1f} > "
                    f"placa {sl:.1f} - margen {margen_min:.1f} mm"
                )
            if float(cut_b[3]) > sw - margen_min + 0.5:
                issues.append(
                    f"margen placa Y max: corte maxY={cut_b[3]:.1f} > "
                    f"placa {sw:.1f} - margen {margen_min:.1f} mm"
                )

        if kerf_in > 0 and cut_b and all_piece_bounds:
            # `kerf_usado` es la separación final ENTRE PIEZAS configurada por
            # calibre. Los perfiles que entran al packer ya contienen el
            # desfase plasma, por tanto los CUT_OUTER exportados deben
            # conservar el kerf completo. Restar 2*off admitía contornos de
            # corte más cercanos que la tabla oficial.
            min_gap = kerf_mm
            propio = _clearance_segments(outer_ents)
            for ob in all_piece_bounds:
                otro_bbox = _record_bbox(ob)
                if otro_bbox is None or otro_bbox is cut_b:
                    continue
                # Cajas lejanas no pueden violar el kerf: filtro barato.
                if (_bbox_separation_mm(cut_b, otro_bbox) or 0.0) >= min_gap:
                    continue
                # Cajas cercanas se miden contra la geometría real. Estas piezas
                # se entrelazan (escalones/notches) y sus bounding boxes se
                # acercan mucho más que el metal, lo que producía un rechazo
                # falso con el kerf perfectamente respetado.
                gap = _segments_min_distance_mm(
                    propio, _record_segments(ob), limit=min_gap
                )
                if gap is None:
                    continue
                if gap < min_gap - 0.5:
                    issues.append(
                        f"separación pieza-pieza {gap:.1f} mm < mínimo "
                        f"{min_gap:.1f} mm (kerf nest {kerf_mm:.1f} - "
                        f"configurado de tabla)"
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
