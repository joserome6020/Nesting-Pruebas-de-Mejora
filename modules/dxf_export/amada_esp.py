"""
Export DXF pieza ESP. Amada: contorno engañado (+10\") + barrenos, sin marcaje.

El soft Amada legacy espera un perfil alto (pieza 5\" + colchón 10\" = 15\").
El contorno exterior va como un único LWPOLYLINE cerrado (join) en CUT_OUTER.
Barrenos: CIRCLE o LWPOLYLINE con bulges (ranura); nunca polilíneas facetadas.
"""
from __future__ import annotations

import math
import os
from typing import Sequence

from ezdxf.math import Matrix44

from modules.dxf_native_curves import (
    circle_centroid_mean,
    fit_stadium_lwpoly_bulge,
    normalize_ring,
)
from modules.nest_exporter import DxfExportValidationError, _add_lwpolyline

_IN_TO_MM = 25.4
# Colchón inferior para el perfil Amada (pulgadas).
AMADA_ESP_SOFT_PADDING_IN = 10.0


def amada_esp_padding_mm(padding_in: float = AMADA_ESP_SOFT_PADDING_IN) -> float:
    return float(padding_in) * _IN_TO_MM


def _ring_bbox(ring: Sequence) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for pt in ring or []:
        try:
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
        except (TypeError, ValueError, IndexError):
            continue
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _shift_ring_xy(ring: Sequence, dx: float, dy: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for pt in ring or []:
        try:
            out.append((float(pt[0]) + dx, float(pt[1]) + dy))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def build_amada_esp_padded_geometry(
    outer: Sequence,
    holes: Sequence | None = None,
    *,
    padding_in: float = AMADA_ESP_SOFT_PADDING_IN,
) -> tuple[list[tuple[float, float]], list[list[tuple[float, float]]], float, float]:
    """
    Normaliza la pieza al origen, coloca el colchón de 10\" abajo y sube
    barrenos a la banda superior (5\" reales de cobre).

    Devuelve (outer_padded, holes_shifted, largo_mm, alto_total_mm).
    """
    bb = _ring_bbox(outer)
    if bb is None:
        return [], [], 0.0, 0.0
    minx, miny, maxx, maxy = bb
    dx, dy = -float(minx), -float(miny)
    outer_o = _shift_ring_xy(outer, dx, dy)
    holes_o = [_shift_ring_xy(h, dx, dy) for h in (holes or [])]

    bb2 = _ring_bbox(outer_o)
    if bb2 is None:
        return [], [], 0.0, 0.0
    _x0, _y0, maxx2, maxy2 = bb2
    largo_mm = max(0.0, float(maxx2) - float(_x0))
    alto_pieza_mm = max(0.0, float(maxy2) - float(_y0))
    pad_mm = amada_esp_padding_mm(padding_in)
    alto_total_mm = alto_pieza_mm + pad_mm

    # Rectángulo cerrado único: colchón abajo, pieza arriba (como AutoCAD de referencia).
    outer_padded = [
        (0.0, 0.0),
        (largo_mm, 0.0),
        (largo_mm, alto_total_mm),
        (0.0, alto_total_mm),
    ]
    holes_shifted = [_shift_ring_xy(h, 0.0, pad_mm) for h in holes_o if h]
    return outer_padded, holes_shifted, largo_mm, alto_total_mm


def export_amada_esp_joined_outer(msp, outer_ring: Sequence, *, layer: str = "CUT_OUTER") -> bool:
    """Contorno exterior como LWPOLYLINE cerrado (join explícito para STEP/Amada)."""
    pts = normalize_ring(outer_ring, closed=True)
    if len(pts) < 3:
        return False
    if len(pts) >= 2 and math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) < 1e-6:
        pts = pts[:-1]
    _add_lwpolyline(msp, pts, layer, closed=True)
    return True


def _add_lwpolyline_xyb(
    msp,
    points: Sequence[tuple[float, float, float]],
    layer: str,
    *,
    closed: bool = True,
) -> bool:
    if not points or len(points) < 2:
        return False
    msp.add_lwpolyline(
        list(points),
        format="xyb",
        dxfattribs={"layer": layer, "closed": bool(closed)},
    )
    return True


def _write_amada_inner_preserved(msp, entity, layer: str) -> int:
    """CIRCLE o LWPOLYLINE con bulges — sin expandir a segmentos facetados."""
    typ = entity.dxftype()
    if typ == "CIRCLE":
        c = entity.dxf.center
        msp.add_circle(
            (float(c.x), float(c.y)),
            float(entity.dxf.radius),
            dxfattribs={"layer": layer},
        )
        return 1
    if typ == "LWPOLYLINE":
        pts = list(entity.get_points("xyb"))
        if len(pts) < 2:
            return 0
        msp.add_lwpolyline(
            pts,
            format="xyb",
            dxfattribs={"layer": layer, "closed": bool(entity.closed)},
        )
        return 1
    return 0


def export_amada_holes_from_source_dxf(
    msp,
    doc,
    ruta: str,
    *,
    pad_mm: float,
    placement: dict | None = None,
) -> bool:
    """
    Clona CUT_INNER del Processed Files 1:1 (círculos + ranuras con bulge).
    Escala pulgadas→mm, normaliza al origen del outer y sube barrenos al colchón +10\".
    """
    import ezdxf

    from modules.nest_exporter import (
        _clasificar_capa,
        _dxf_outer_origin_mm,
        _import_layers_from_source,
    )

    try:
        part_doc = ezdxf.readfile(str(ruta))
    except Exception:
        return False

    inners = [
        e
        for e in part_doc.modelspace()
        if _clasificar_capa(str(getattr(e.dxf, "layer", "") or "")) == "inner"
        and e.dxftype() in ("CIRCLE", "LWPOLYLINE", "POLYLINE")
    ]
    if not inners:
        return False

    ox_mm, oy_mm = _dxf_outer_origin_mm(str(ruta)) or (0.0, 0.0)
    ox_in = float(ox_mm) / _IN_TO_MM
    oy_in = float(oy_mm) / _IN_TO_MM
    # ezdxf transform aplica el factor derecho primero: T_orig @ S @ T_pad.
    m = (
        Matrix44.translate(-ox_in, -oy_in, 0.0)
        @ Matrix44.scale(_IN_TO_MM, _IN_TO_MM, _IN_TO_MM)
        @ Matrix44.translate(0.0, float(pad_mm), 0.0)
    )

    added = 0
    for ent in inners:
        try:
            copy = ent.copy()
            copy.transform(m)
            added += _write_amada_inner_preserved(msp, copy, "CUT_INNER")
        except Exception:
            continue
    if added <= 0:
        return False
    _import_layers_from_source(part_doc, doc, {"CUT_INNER"})
    return True


def export_amada_esp_inner_closed(
    msp,
    ring: Sequence,
    *,
    layer: str = "CUT_INNER",
) -> bool:
    """Barreno Amada: CIRCLE o LWPOLYLINE con bulges (ranura); sin facetas."""
    pts = normalize_ring(ring, closed=True)
    if len(pts) < 3:
        return False

    if len(pts) >= 6:
        circ = circle_centroid_mean(pts)
        if circ is not None:
            _cx, _cy, r, err = circ
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            w = max(xs) - min(xs)
            h = max(ys) - min(ys)
            short = min(w, h)
            if (
                r > 1e-6
                and short > 1e-6
                and err <= max(0.1, r * 0.06)
                and max(w, h) / short <= 1.2
            ):
                msp.add_circle(
                    (float(_cx), float(_cy)),
                    float(r),
                    dxfattribs={"layer": layer},
                )
                return True

    slot = fit_stadium_lwpoly_bulge(pts)
    if slot:
        return _add_lwpolyline_xyb(msp, slot, layer, closed=True)

    # Rectángulo simple (4 esquinas): LWPOLY cerrada exacta, no facetas.
    if len(pts) == 4:
        _add_lwpolyline(msp, pts, layer, closed=True)
        return True

    return False


def validate_amada_esp_entities_closed(entities) -> list[str]:
    """Lista problemas: el soft Amada exige islas cerradas, no segmentos sueltos."""
    issues: list[str] = []
    for ent in entities or []:
        layer = str(getattr(ent.dxf, "layer", "") or "").upper()
        if layer not in ("CUT_OUTER", "CUT_INNER"):
            continue
        typ = ent.dxftype()
        if typ == "LINE":
            issues.append(f"{layer}: segmento LINE suelto (requiere contorno cerrado)")
        elif typ == "ARC":
            issues.append(f"{layer}: ARC suelto (requiere contorno cerrado)")
        elif typ == "LWPOLYLINE":
            if not bool(getattr(ent, "closed", False) or ent.closed):
                issues.append(f"{layer}: LWPOLYLINE sin flag closed")
            else:
                pts = list(ent.get_points("xyb"))
                bulges = [abs(float(p[2] or 0.0)) for p in pts]
                if len(pts) > 6 and max(bulges) < 1e-9:
                    issues.append(
                        f"{layer}: LWPOLYLINE facetada ({len(pts)} vértices); "
                        f"requiere CIRCLE o ranura con bulge"
                    )
        elif typ == "CIRCLE":
            pass
        else:
            issues.append(f"{layer}: tipo {typ} no permitido en corte Amada")
    return issues


def export_amada_esp_piece(
    msp,
    p: dict,
    *,
    doc=None,
    draw_holes: bool = True,
    padding_in: float = AMADA_ESP_SOFT_PADDING_IN,
    strict: bool = True,
) -> bool:
    """
    AMADA/FIXTURA: CUT_OUTER = rectángulo 15\" join; CUT_INNER = barrenos; sin MARK.
    """
    from modules.nest_exporter import _msp_count, _msp_snapshot

    count_before = _msp_count(msp)
    outer = p.get("outer") or p.get("outer_poly")
    holes = p.get("holes") or p.get("inner") or []
    if not outer:
        return False

    pad_mm = amada_esp_padding_mm(padding_in)

    if p.get("cu_amada_outer_padded"):
        outer_p = list(outer)
        holes_p = [list(h) for h in holes if h]
    else:
        outer_p, holes_p, _, _ = build_amada_esp_padded_geometry(
            outer, holes, padding_in=padding_in
        )

    added = export_amada_esp_joined_outer(msp, outer_p, layer="CUT_OUTER")
    holes_ok = False

    if draw_holes:
        ruta_src = str(p.get("ruta_origen") or p.get("ruta") or "").strip()
        if ruta_src and os.path.isfile(ruta_src) and doc is not None:
            holes_ok = export_amada_holes_from_source_dxf(
                msp, doc, ruta_src, pad_mm=pad_mm, placement=p
            )

        if not holes_ok:
            for h in holes_p:
                if not h:
                    continue
                if export_amada_esp_inner_closed(msp, h, layer="CUT_INNER"):
                    added = True
                    holes_ok = True
                elif strict:
                    label = str(p.get("part_name") or p.get("name") or "PIEZA")
                    raise DxfExportValidationError(
                        f"{label}: barreno no exportable con integridad nativa "
                        f"(requiere DXF fuente o ranura/círculo reconocible)"
                    )
        elif holes_ok:
            added = True

    issues = validate_amada_esp_entities_closed(_msp_snapshot(msp)[count_before:])
    if issues and strict:
        label = str(p.get("part_name") or p.get("name") or "PIEZA")
        raise DxfExportValidationError(
            f"{label}: Amada requiere contornos cerrados — " + "; ".join(issues[:3])
        )
    return added
