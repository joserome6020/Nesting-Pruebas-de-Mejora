"""Paths Qt de alta fidelidad leídos directamente del DXF (arcos/círculos nativos)."""
from __future__ import annotations

import math
import os
from functools import lru_cache

import ezdxf
from ezdxf import path as ezdxf_path
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainterPath, QTransform

from modules.nesting_engine.geometry_parser import (
    DXF_FLATTEN_DISTANCE,
    ESCALA_DXF,
    _clasificar_capa,
)

_HIFI_ENABLED = str(os.getenv("ARGA_DXF_HIFI_DISPLAY", "0")).strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


def hifi_display_enabled() -> bool:
    return _HIFI_ENABLED


def _entity_to_subpath(entity) -> QPainterPath | None:
    typ = entity.dxftype()
    sub = QPainterPath()
    escala = ESCALA_DXF

    try:
        if typ == "CIRCLE":
            c = entity.dxf.center
            r = float(entity.dxf.radius) * escala
            if r <= 0:
                return None
            cx, cy = float(c.x) * escala, float(c.y) * escala
            rect_x, rect_y = cx - r, cy - r
            sub.arcMoveTo(rect_x, rect_y, 2.0 * r, 2.0 * r, 0.0)
            sub.arcTo(rect_x, rect_y, 2.0 * r, 2.0 * r, 0.0, 360.0)
            sub.closeSubpath()
            return sub

        if typ == "ARC":
            c = entity.dxf.center
            r = float(entity.dxf.radius) * escala
            if r <= 0:
                return None
            cx, cy = float(c.x) * escala, float(c.y) * escala
            sa_deg = float(entity.dxf.start_angle)
            ea_deg = float(entity.dxf.end_angle)
            span_deg = (ea_deg - sa_deg) % 360.0
            if span_deg < 1e-9:
                span_deg = 360.0
            rect_x, rect_y = cx - r, cy - r
            # Qt: ángulos en grados, sentido horario; escena CAD con Y↑ vía scale(1,-1) en la vista.
            sub.arcMoveTo(rect_x, rect_y, 2.0 * r, 2.0 * r, sa_deg)
            sub.arcTo(rect_x, rect_y, 2.0 * r, 2.0 * r, sa_deg, span_deg)
            return sub

        p = ezdxf_path.make_path(entity)
        verts = list(p.flattening(distance=DXF_FLATTEN_DISTANCE))
        if len(verts) < 2:
            return None
        sub.moveTo(verts[0][0] * escala, verts[0][1] * escala)
        for v in verts[1:]:
            sub.lineTo(v[0] * escala, v[1] * escala)
        if p.is_closed:
            sub.closeSubpath()
        return sub
    except Exception:
        return None


@lru_cache(maxsize=128)
def _local_path_from_dxf_cached(ruta: str, mtime_ns: int) -> tuple | None:
  # mtime_ns invalida caché al cambiar el archivo
    try:
        doc = ezdxf.readfile(ruta)
    except Exception:
        return None

    msp = doc.modelspace()
    outer = QPainterPath()
    inner = QPainterPath()
    outer.setFillRule(Qt.FillRule.OddEvenFill)
    inner.setFillRule(Qt.FillRule.OddEvenFill)

    minx = miny = float("inf")
    maxx = maxy = float("-inf")

    def _track(sub: QPainterPath):
        nonlocal minx, miny, maxx, maxy
        br = sub.boundingRect()
        if br.isNull():
            return
        minx = min(minx, br.left())
        miny = min(miny, br.top())
        maxx = max(maxx, br.right())
        maxy = max(maxy, br.bottom())

    for entity in msp:
        if entity.dxftype() not in (
            "LINE",
            "LWPOLYLINE",
            "POLYLINE",
            "ARC",
            "CIRCLE",
            "ELLIPSE",
            "SPLINE",
        ):
            continue
        clase = _clasificar_capa(str(entity.dxf.layer))
        if clase not in ("outer", "inner"):
            continue
        sub = _entity_to_subpath(entity)
        if sub is None or sub.isEmpty():
            continue
        _track(sub)
        if clase == "inner":
            inner.addPath(sub)
        else:
            outer.addPath(sub)

    if not math.isfinite(minx):
        return None

    combined = QPainterPath()
    combined.setFillRule(Qt.FillRule.OddEvenFill)
    combined.addPath(outer)
    combined.addPath(inner)
    if combined.isEmpty():
        return None

    return (minx, miny, combined)


def piece_hifi_path_from_dxf(pieza: dict) -> QPainterPath | None:
    """Path en coords de placa (mm) leyendo el DXF + transform de colocación."""
    if not _HIFI_ENABLED:
        return None
    ruta = str(pieza.get("ruta") or "").strip()
    if not ruta or not os.path.isfile(ruta):
        return None
    # Sin metadata de colocación (nest guardado antiguo) → usar poligonos en coords de placa.
    if "orig_minx" not in pieza:
        return None

    try:
        mtime_ns = os.stat(ruta).st_mtime_ns
    except OSError:
        return None

    cached = _local_path_from_dxf_cached(ruta, mtime_ns)
    if not cached:
        return None

    minx, miny, local = cached
    rot = float(pieza.get("rot_deg", 0.0) or 0.0)
    sx = float(pieza.get("shift_x", 0.0) or 0.0)
    sy = float(pieza.get("shift_y", 0.0) or 0.0)

    xf = QTransform()
    xf.translate(sx, sy)
    if abs(rot) > 1e-9:
        xf.rotate(rot)
    xf.translate(-minx, -miny)
    return xf.map(local)
