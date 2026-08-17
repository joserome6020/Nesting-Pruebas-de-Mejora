"""Offset 2D con Clipper2 — mismo motor que FreeCAD Path/CAM.

El uso de Clipper2 lo tiene FreeCAD para su workbench Path/CAM porque
``BRepOffsetAPI_MakeOffset`` de OCCT es finicky con perfiles de producción
(orden de aristas, micro-gaps, esquinas cerradas). Clipper2 opera con
aritmética entera y siempre devuelve un polígono cerrado — bulletproof.

Este módulo es el motor que **no falla**: rasteriza todas las curvas y
devuelve el contorno como polilínea. Para conservar arcos/círculos exactos
se usa primero `plasma_occt_offset` y este se queda como respaldo.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

_ESCALA = 100000  # 1e-5 in de resolución interna: > que la tolerancia del kerf
_ARC_TOL_IN = 0.001  # 0.001" ≈ 0.025 mm; imperceptible a simple vista


@dataclass
class ClipperOffsetResult:
    """Anillo (o anillos) resultantes en coordenadas del DXF (mismas unidades)."""

    rings: list[list[tuple[float, float]]] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.rings) and not self.error


def _arc_points(
    center: tuple[float, float],
    radius: float,
    start_angle: float,
    end_angle: float,
    *,
    step_deg: float,
) -> list[tuple[float, float]]:
    cx, cy = float(center[0]), float(center[1])
    r = float(radius)
    sweep = (float(end_angle) - float(start_angle)) % 360.0
    if sweep <= 1e-9:
        sweep = 360.0
    n = max(4, int(math.ceil(sweep / max(step_deg, 0.05))))
    pts: list[tuple[float, float]] = []
    for i in range(n + 1):
        ang = math.radians(float(start_angle) + sweep * (i / n))
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def _step_deg_para(radio: float) -> float:
    if radio <= 0:
        return 5.0
    # Sagita ≤ _ARC_TOL_IN: 2·asin(tol/r) en grados.
    ratio = min(1.0, _ARC_TOL_IN / max(radio, 1e-9))
    return max(0.25, min(10.0, math.degrees(2.0 * math.asin(ratio))))


def rasterizar_entidades(entities: Iterable) -> list[tuple[float, float]] | None:
    """Encadena LINE/ARC/(LW)POLYLINE en un anillo cerrado de vértices.

    Los micro-huecos entre entidades se cierran; huecos grandes devuelven None
    (perfil realmente roto). No hay ARC/CIRCLE aislados sin encadenar.
    """
    from modules.plasma_dxf_export import _order_connected_entities

    ents = [e for e in (entities or [])]
    if not ents:
        return None

    tipos = {e.dxftype() for e in ents}
    if tipos <= {"CIRCLE"} and len(ents) == 1:
        c = ents[0].dxf.center
        r = float(ents[0].dxf.radius)
        return _arc_points((float(c.x), float(c.y)), r, 0.0, 360.0, step_deg=_step_deg_para(r))

    polys = [e for e in ents if e.dxftype() in ("LWPOLYLINE", "POLYLINE")]
    line_arc = [e for e in ents if e.dxftype() in ("LINE", "ARC")]
    if polys and not line_arc:
        p = polys[0]
        return _puntos_de_polilinea(p)

    if not line_arc:
        return None

    orden = _order_connected_entities(line_arc, tol=0.05)
    if not orden:
        return None

    ring: list[tuple[float, float]] = []

    def _apend(pts: Sequence[tuple[float, float]]) -> None:
        for x, y in pts:
            if ring and math.dist(ring[-1], (x, y)) <= 1e-9:
                continue
            ring.append((float(x), float(y)))

    for ent, invertida in orden:
        typ = ent.dxftype()
        if typ == "LINE":
            s, e = ent.dxf.start, ent.dxf.end
            p0 = (float(s.x), float(s.y))
            p1 = (float(e.x), float(e.y))
            _apend([p1, p0] if invertida else [p0, p1])
        elif typ == "ARC":
            c = ent.dxf.center
            pts = _arc_points(
                (float(c.x), float(c.y)),
                float(ent.dxf.radius),
                float(ent.dxf.start_angle),
                float(ent.dxf.end_angle),
                step_deg=_step_deg_para(float(ent.dxf.radius)),
            )
            _apend(pts[::-1] if invertida else pts)

    if len(ring) < 3:
        return None
    # Solo micro-gaps (típicos de export CAD) se cierran con línea; huecos grandes
    # significan perfil realmente roto y se dejan fallar arriba.
    gap = math.dist(ring[0], ring[-1])
    if gap > 0.05:  # > 0.05" ≈ 1.27 mm → contorno abierto de verdad
        return None
    if gap > 1e-9:
        ring = ring + [ring[0]]
    return ring


def _puntos_de_polilinea(ent) -> list[tuple[float, float]] | None:
    typ = ent.dxftype()
    if typ not in ("LWPOLYLINE", "POLYLINE"):
        return None
    try:
        virtuales = list(ent.virtual_entities())
    except Exception:
        virtuales = []
    if virtuales:
        return rasterizar_entidades(virtuales)
    if typ == "LWPOLYLINE":
        pts = [(float(v[0]), float(v[1])) for v in ent.get_points("xy")]
    else:
        pts = [
            (float(v.dxf.location.x), float(v.dxf.location.y)) for v in ent.vertices
        ]
    if len(pts) < 3:
        return None
    if math.dist(pts[0], pts[-1]) > 1e-9:
        pts = pts + [pts[0]]
    return pts


def clipper_disponible() -> bool:
    try:
        import pyclipr  # noqa: F401

        return True
    except Exception:
        return False


def offset_ring(
    ring: Sequence[tuple[float, float]],
    delta: float,
) -> ClipperOffsetResult:
    """Offset de un anillo cerrado con Clipper2 y joins a inglete.

    ``delta`` en mismas unidades que el anillo. Positivo crece hacia fuera del
    contorno CCW; el motor detecta la orientación y aplica el signo correcto.

    Un OFFSET de producción no inventa radios en una esquina LINE→LINE:
    una esquina en punta permanece en punta. Los ARC nativos se conservan por
    la ruta OCCT; este fallback recibe un anillo muestreado y por eso debe
    usar Miter, nunca Round.
    """
    try:
        import pyclipr
    except Exception as exc:
        return ClipperOffsetResult(error=f"pyclipr no disponible: {exc}")

    pts = [(float(p[0]), float(p[1])) for p in (ring or []) if len(p) >= 2]
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return ClipperOffsetResult(error="ring insuficiente")

    # Orientación: CCW → área > 0.
    area = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        area += x0 * y1 - x1 * y0
    ccw = area > 0
    if not ccw:
        pts = list(reversed(pts))

    po = pyclipr.ClipperOffset()
    po.scaleFactor = _ESCALA
    po.addPaths([pts], pyclipr.JoinType.Miter, pyclipr.EndType.Polygon)
    try:
        salida = po.execute(float(delta))
    except Exception as exc:
        return ClipperOffsetResult(error=f"clipper: {exc}")
    if salida is None or len(salida) == 0:
        return ClipperOffsetResult(error="clipper devolvió vacío")

    anillos: list[list[tuple[float, float]]] = []
    for path in salida:
        anillo = [(float(x), float(y)) for x, y in path]
        if len(anillo) < 3:
            continue
        if anillo[0] != anillo[-1]:
            anillo.append(anillo[0])
        if not ccw:
            anillo = list(reversed(anillo))
        anillos.append(anillo)
    if not anillos:
        return ClipperOffsetResult(error="clipper: sin anillos válidos")
    return ClipperOffsetResult(rings=anillos)


def offset_entidades(entities: Iterable, *, delta: float) -> ClipperOffsetResult:
    """Rasteriza el grupo de entidades y aplica offset Clipper2.

    Este es el motor que **no rechaza** perfiles reales: cualquier LINE/ARC/
    polilínea razonable produce salida. La única forma de fallar es que el
    perfil no cierre o que el offset lo colapse.
    """
    ring = rasterizar_entidades(entities)
    if ring is None:
        return ClipperOffsetResult(error="no se pudo rasterizar el contorno")
    return offset_ring(ring, float(delta))


def offset_ring_as_lwpolyline_points(
    entities: Iterable,
    *,
    delta: float,
) -> tuple[list[tuple[float, float]] | None, str]:
    """Devuelve los vértices XY del contorno compensado, listo para LWPOLYLINE.

    Se prefiere el anillo de mayor área (el contorno principal); descartes son
    fragmentos numéricos irrelevantes.
    """
    res = offset_entidades(entities, delta=delta)
    if not res.ok:
        return None, res.error
    principal = max(res.rings, key=_area_ring)
    return [(x, y) for x, y in principal if True], ""


def _area_ring(ring: Sequence[tuple[float, float]]) -> float:
    a = 0.0
    n = len(ring)
    for i in range(n - 1):
        x0, y0 = ring[i]
        x1, y1 = ring[i + 1]
        a += x0 * y1 - x1 * y0
    return abs(a) * 0.5
