"""
Export DXF pieza ESP. Amada: contorno engañado (+10\") + barrenos, sin marcaje.

El soft Amada legacy espera un perfil alto (pieza 5\" + colchón 10\" = 15\").
El contorno exterior va como un único LWPOLYLINE cerrado (join) en CUT_OUTER.
"""
from __future__ import annotations

import math
from typing import Sequence

from modules.dxf_native_curves import export_ring_native, normalize_ring
from modules.nest_exporter import _add_lwpolyline, _export_ring_exact

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
    # Evitar duplicar vértice de cierre: closed=True en ezdxf.
    if len(pts) >= 2 and math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) < 1e-6:
        pts = pts[:-1]
    _add_lwpolyline(msp, pts, layer, closed=True)
    return True


def export_amada_esp_piece(
    msp,
    p: dict,
    *,
    draw_holes: bool = True,
    padding_in: float = AMADA_ESP_SOFT_PADDING_IN,
) -> bool:
    """
    AMADA/FIXTURA: CUT_OUTER = rectángulo 15\" join; CUT_INNER = barrenos; sin MARK.
    """
    outer = p.get("outer") or p.get("outer_poly")
    holes = p.get("holes") or p.get("inner") or []
    if not outer:
        return False

    if p.get("cu_amada_outer_padded"):
        outer_p = list(outer)
        holes_p = [list(h) for h in holes if h]
    else:
        outer_p, holes_p, _, _ = build_amada_esp_padded_geometry(
            outer, holes, padding_in=padding_in
        )

    added = export_amada_esp_joined_outer(msp, outer_p, layer="CUT_OUTER")
    if not draw_holes:
        return added

    for h in holes_p:
        if not h:
            continue
        if not export_ring_native(
            msp, h, "CUT_INNER", closed=True, prefer_circle=True
        ):
            _export_ring_exact(msp, h, "CUT_INNER", closed=True)
        added = True
    return added
