"""OFFSET plasma exacto con Open CASCADE, sin proceso FreeCAD.

Reglas de producción:
  * Solo primitivas que el DXF conserva exactamente: LINE, ARC, CIRCLE
    (LWPOLYLINE/POLYLINE se leen, incluidos bulges, y salen como LINE/ARC).
  * El resultado se **valida geométricamente** antes de devolverse: contorno
    cerrado, sin auto-intersecciones y con crecimiento real de ``|delta|`` por
    lado. Si no cumple, se falla cerrado: nunca se entrega un perfil deforme.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# Muestreo para validación (no se escribe al DXF: las curvas siguen nativas).
_SAMPLE_STEP_DEG = 3.0
# Más allá de esta relación punta/offset el inglete produce una aguja; en ese
# caso se conserva el redondeo de OCCT. También acota el crecimiento axial
# del AABB al validar perfiles con lados inclinados.
_MITER_LIMIT = 8.0


@dataclass
class OcctOffsetResult:
    entities: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.entities) and not self.error


def occt_available() -> bool:
    try:
        import OCP  # noqa: F401
    except Exception:
        return False
    return True


# ---------------------------------------------------------------------------
# Geometría auxiliar (muestreo / métricas) — usada para validar, no para escribir
# ---------------------------------------------------------------------------
def _angle_in_ccw_sweep(a0: float, a1: float, a: float) -> bool:
    sweep = (a1 - a0) % 360.0
    if sweep <= 1e-9:
        sweep = 360.0
    t = (a - a0) % 360.0
    return t <= sweep + 1e-9


def _arc_sample(
    center: tuple[float, float],
    radius: float,
    start_angle: float,
    end_angle: float,
    *,
    step_deg: float = _SAMPLE_STEP_DEG,
) -> list[tuple[float, float]]:
    cx, cy = float(center[0]), float(center[1])
    sweep = (float(end_angle) - float(start_angle)) % 360.0
    if sweep <= 1e-9:
        sweep = 360.0
    n = max(2, int(math.ceil(sweep / max(step_deg, 0.25))))
    pts = []
    for i in range(n + 1):
        ang = math.radians(float(start_angle) + sweep * (i / n))
        pts.append((cx + radius * math.cos(ang), cy + radius * math.sin(ang)))
    return pts


def ring_from_specs(
    specs: Sequence[dict[str, Any]], *, step_deg: float = _SAMPLE_STEP_DEG
) -> list[tuple[float, float]]:
    """Muestrea las entidades nativas resultantes tal como las leerá un CAD.

    LINE y ARC de DXF no llevan dirección de recorrido, así que cada tramo se
    encadena por el extremo más cercano al punto anterior.
    """
    segs: list[tuple[str, list[tuple[float, float]]]] = []
    for spec in specs or []:
        typ = str(spec.get("type") or "")
        if typ == "LINE":
            s, e = spec["start"], spec["end"]
            segs.append((typ, [(float(s[0]), float(s[1])), (float(e[0]), float(e[1]))]))
        elif typ == "ARC":
            segs.append(
                (
                    typ,
                    _arc_sample(
                        (spec["center"][0], spec["center"][1]),
                        float(spec["radius"]),
                        float(spec["start_angle"]),
                        float(spec["end_angle"]),
                        step_deg=step_deg,
                    ),
                )
            )
        elif typ == "CIRCLE":
            segs.append(
                (
                    typ,
                    _arc_sample(
                        (spec["center"][0], spec["center"][1]),
                        float(spec["radius"]),
                        0.0,
                        360.0,
                        step_deg=step_deg,
                    ),
                )
            )

    # El primer tramo no tiene punto previo: se orienta contra el siguiente.
    if len(segs) >= 2 and segs[0][0] != "CIRCLE" and segs[1][0] != "CIRCLE":
        primero, siguiente = segs[0][1], segs[1][1]
        d_directo = min(
            math.dist(primero[-1], siguiente[0]), math.dist(primero[-1], siguiente[-1])
        )
        d_invertido = min(
            math.dist(primero[0], siguiente[0]), math.dist(primero[0], siguiente[-1])
        )
        if d_invertido < d_directo:
            segs[0] = (segs[0][0], list(reversed(primero)))

    ring: list[tuple[float, float]] = []
    for typ, pts in segs:
        if typ != "CIRCLE" and ring and math.dist(ring[-1], pts[0]) > math.dist(
            ring[-1], pts[-1]
        ):
            pts = list(reversed(pts))
        for p in pts:
            if ring and math.dist(ring[-1], p) <= 1e-12:
                continue
            ring.append((float(p[0]), float(p[1])))
    return ring


def specs_from_dxf_entities(entities: Iterable) -> list[dict[str, Any]]:
    """Normaliza entidades ezdxf a specs LINE/ARC/CIRCLE para medir/validar.

    Las polilíneas se explotan con ``virtual_entities()`` para no duplicar la
    matemática de bulges.
    """
    out: list[dict[str, Any]] = []
    for ent in entities or []:
        typ = ent.dxftype()
        if typ in ("LWPOLYLINE", "POLYLINE"):
            try:
                out.extend(specs_from_dxf_entities(list(ent.virtual_entities())))
            except Exception:
                continue
        elif typ == "LINE":
            s, e = ent.dxf.start, ent.dxf.end
            out.append(
                {
                    "type": "LINE",
                    "start": (float(s.x), float(s.y), 0.0),
                    "end": (float(e.x), float(e.y), 0.0),
                }
            )
        elif typ == "ARC":
            c = ent.dxf.center
            out.append(
                {
                    "type": "ARC",
                    "center": (float(c.x), float(c.y), 0.0),
                    "radius": float(ent.dxf.radius),
                    "start_angle": float(ent.dxf.start_angle),
                    "end_angle": float(ent.dxf.end_angle),
                }
            )
        elif typ == "CIRCLE":
            c = ent.dxf.center
            out.append(
                {
                    "type": "CIRCLE",
                    "center": (float(c.x), float(c.y), 0.0),
                    "radius": float(ent.dxf.radius),
                }
            )
    return out


def lwpolyline_points_from_specs(
    specs: Sequence[dict[str, Any]],
) -> list[tuple[float, float, float]] | None:
    """Convierte una cadena LINE/ARC ordenada a vértices ``xyb`` de polilínea.

    Devolver una polilínea cerrada (no entidades sueltas) mantiene el DXF con la
    misma topología que el original: el área neta y el resto de la suite siguen
    reconociendo el contorno. Un arco se representa exacto con su bulge.
    """
    if not specs or any(str(s.get("type")) not in ("LINE", "ARC") for s in specs):
        return None

    pts: list[tuple[float, float, float]] = []
    for spec in specs:
        if spec["type"] == "LINE":
            s = spec["start"]
            pts.append((float(s[0]), float(s[1]), 0.0))
            continue
        cx, cy = float(spec["center"][0]), float(spec["center"][1])
        r = float(spec["radius"])
        a0, a1 = float(spec["start_angle"]), float(spec["end_angle"])
        sweep = (a1 - a0) % 360.0
        if sweep <= 1e-9:
            sweep = 360.0
        ccw = bool(spec.get("ccw", True))
        ang_ini = a0 if ccw else a1
        bulge = math.tan(math.radians(sweep) / 4.0) * (1.0 if ccw else -1.0)
        pts.append(
            (
                cx + r * math.cos(math.radians(ang_ini)),
                cy + r * math.sin(math.radians(ang_ini)),
                bulge,
            )
        )
    return pts if len(pts) >= 2 else None


def specs_bbox(specs: Sequence[dict[str, Any]]) -> tuple[float, float] | None:
    """Ancho/alto del conjunto; no requiere orden de recorrido."""
    xs: list[float] = []
    ys: list[float] = []
    for spec in specs or []:
        for p in ring_from_specs([spec]):
            xs.append(p[0])
            ys.append(p[1])
    if not xs:
        return None
    return (max(xs) - min(xs), max(ys) - min(ys))


def _ring_metrics(ring: Sequence[tuple[float, float]]) -> dict[str, float] | None:
    pts = [(float(p[0]), float(p[1])) for p in ring or []]
    if len(pts) < 4:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    area = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        area += x0 * y1 - x1 * y0
    return {
        "w": max(xs) - min(xs),
        "h": max(ys) - min(ys),
        "area_abs": abs(area * 0.5),
        "gap": math.dist(pts[0], pts[-1]),
    }


def ring_is_simple(ring: Sequence[tuple[float, float]]) -> bool:
    """Sin auto-intersecciones: detecta los lazos de esquina de un offset mal hecho."""
    try:
        from shapely.geometry import LinearRing

        pts = [(float(p[0]), float(p[1])) for p in ring or []]
        if len(pts) < 4:
            return False
        if math.dist(pts[0], pts[-1]) > 1e-9:
            pts.append(pts[0])
        return bool(LinearRing(pts).is_simple)
    except Exception:
        # Sin shapely no se puede certificar: para producción es un rechazo.
        return False


def _muestra(ring: Sequence[tuple[float, float]], maximo: int = 600) -> list:
    pts = list(ring or [])
    if len(pts) <= maximo:
        return pts
    paso = max(1, len(pts) // maximo)
    return pts[::paso]


def rings_coinciden(
    ring_a: Sequence[tuple[float, float]],
    ring_b: Sequence[tuple[float, float]],
    tol: float,
) -> bool:
    """Compara dos contornos punto a punto (Hausdorff) sin importar el orden."""
    try:
        from shapely.geometry import LineString

        a, b = list(ring_a or []), list(ring_b or [])
        if len(a) < 2 or len(b) < 2:
            return False
        la, lb = LineString(a), LineString(b)
        return max(la.hausdorff_distance(lb), lb.hausdorff_distance(la)) <= tol
    except Exception:
        return False


def _limite_inglete(turn_deg: float, delta: float) -> float:
    """Distancia de la punta a inglete al vértice origen, para un giro dado."""
    media = math.radians(abs(float(turn_deg))) / 2.0
    return abs(float(delta)) / max(math.cos(media), 1e-6)


def _punta_admite(
    p: tuple[float, float],
    dist: float,
    delta: float,
    puntas: Sequence[dict[str, Any]] | None,
    tol: float,
) -> bool:
    """Una punta a inglete queda legítimamente a más de ``|delta|`` del origen."""
    d = abs(float(delta))
    for k in puntas or []:
        limite = _limite_inglete(k["turn"], d)
        if dist <= limite + tol and math.dist(p, k["pt"]) <= limite + tol:
            return True
    return False


def _distancia_offset_valida(
    src_ring: Sequence[tuple[float, float]],
    out_ring: Sequence[tuple[float, float]],
    delta: float,
    *,
    puntas: Sequence[dict[str, Any]] | None = None,
) -> str:
    """Todo punto del resultado debe estar a ``|delta|`` del contorno origen.

    Es la definición de un offset; detecta bultos, picos y arcos convertidos al
    complementario, que las comprobaciones de bbox/área no ven.
    """
    try:
        from shapely.geometry import LineString, Point
    except Exception:
        return "shapely no disponible para validar el offset"

    d = abs(float(delta))
    tol = max(d * 0.12, 5e-5)
    src = list(src_ring or [])
    out = list(out_ring or [])
    if len(src) < 4 or len(out) < 4:
        return "contornos insuficientes para validar distancia"
    if math.dist(src[0], src[-1]) > 1e-9:
        src = src + [src[0]]
    if math.dist(out[0], out[-1]) > 1e-9:
        out = out + [out[0]]
    src_line, out_line = LineString(src), LineString(out)

    # Solo se comprueba en este sentido: en un vértice reflex el offset corta la
    # esquina y el origen queda legítimamente a más de |delta| del resultado.
    for p in _muestra(out):
        dist = src_line.distance(Point(p))
        if abs(dist - d) <= tol:
            continue
        if dist > d and _punta_admite(p, dist, d, puntas, tol):
            continue
        return (
            f"el contorno resultante no es un offset de {d:.4f} "
            f"(punto a {dist:.4f} en {p[0]:.3f},{p[1]:.3f})"
        )
    return ""


def validate_offset_bbox_growth(
    src_wh: tuple[float, float],
    out_wh: tuple[float, float],
    delta: float,
) -> str:
    """Comprueba crecimiento del AABB tras un offset; "" si es aceptable.

    ``AABB + 2·|delta|`` solo es exacto en rectángulos alineados a ejes con
    join redondo (Minkowski con disco). Un triángulo, bracket o cualquier
    perfil con lados inclinados + inglete crece de forma anisotrópica: una
    punta puede empujar un eje por encima de ``2·|delta|`` y el otro quedar
    ligeramente por debajo. Rechazar eso tumba piezas reales de planta
    (p. ej. SOLERA JACKING PAD) cuyo offset geométrico es correcto.
    """
    d = abs(float(delta))
    if d <= 0.0:
        return ""
    try:
        sw, sh = float(src_wh[0]), float(src_wh[1])
        ow, oh = float(out_wh[0]), float(out_wh[1])
    except (TypeError, ValueError, IndexError):
        return "bbox inválido"
    dw, dh = ow - sw, oh - sh
    # Signo de delta: outward crece, inward encoge.
    if float(delta) < 0.0:
        dw, dh = -dw, -dh
    target = 2.0 * d
    # Déficit axial: normales oblicuas proyectan menos de |delta| en X/Y.
    tol_bajo = max(d * 0.5, 1e-4)
    # Exceso axial: puntas a inglete salen fuera del casquete redondo.
    tol_alto = max(d * (_MITER_LIMIT - 1.0), d * 2.0)
    if dw < target - tol_bajo or dh < target - tol_bajo:
        return (
            "dimensiones no crecieron como offset: "
            f"Δ={dw:.4f}x{dh:.4f}, esperado ~{target:.4f} (±bajo {tol_bajo:.4f})"
        )
    if dw > target + tol_alto or dh > target + tol_alto:
        return (
            "dimensiones crecieron de más (posible lazo de esquina): "
            f"Δ={dw:.4f}x{dh:.4f}, techo {target + tol_alto:.4f}"
        )
    return ""


def validate_offset_ring(
    src_ring: Sequence[tuple[float, float]],
    out_ring: Sequence[tuple[float, float]],
    delta: float,
    *,
    puntas: Sequence[dict[str, Any]] | None = None,
) -> str:
    """Devuelve "" si el offset es válido; si no, el motivo del rechazo."""
    m_in = _ring_metrics(src_ring)
    m_out = _ring_metrics(out_ring)
    if m_in is None:
        return "contorno origen insuficiente"
    if m_out is None:
        return "contorno resultante insuficiente"

    d = float(delta)

    if m_out["gap"] > max(abs(d) * 0.5, 1e-3):
        return f"contorno resultante abierto (gap={m_out['gap']:.5f})"
    if not ring_is_simple(out_ring):
        return "contorno resultante se auto-intersecta (lazos de esquina)"

    motivo_bbox = validate_offset_bbox_growth(
        (m_in["w"], m_in["h"]), (m_out["w"], m_out["h"]), d
    )
    if motivo_bbox:
        return motivo_bbox
    if d > 0 and m_out["area_abs"] <= m_in["area_abs"]:
        return "el área no creció con offset positivo"
    if d < 0 and m_out["area_abs"] >= m_in["area_abs"]:
        return "el área no disminuyó con offset negativo"
    return _distancia_offset_valida(src_ring, out_ring, d, puntas=puntas)


# ---------------------------------------------------------------------------
# Construcción de wire OCCT desde entidades DXF
# ---------------------------------------------------------------------------
def _safe_edge(maker):
    if not maker.IsDone():
        raise ValueError("BRepBuilderAPI_MakeEdge no pudo crear arista")
    return maker.Edge()


def _edge_from_entity(entity):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Pnt

    typ = entity.dxftype()
    if typ == "LINE":
        s, e = entity.dxf.start, entity.dxf.end
        if math.hypot(float(e.x) - float(s.x), float(e.y) - float(s.y)) <= 1e-12:
            raise ValueError("LINE degenerada (longitud cero)")
        return _safe_edge(
            BRepBuilderAPI_MakeEdge(
                gp_Pnt(float(s.x), float(s.y), 0.0),
                gp_Pnt(float(e.x), float(e.y), 0.0),
            )
        )
    if typ == "ARC":
        c = entity.dxf.center
        r = float(entity.dxf.radius)
        if r <= 1e-12:
            raise ValueError("ARC con radio inválido")
        a0 = math.radians(float(entity.dxf.start_angle))
        a1 = math.radians(float(entity.dxf.end_angle))
        while a1 <= a0 + 1e-15:
            a1 += math.tau
        if (a1 - a0) >= math.tau - 1e-9:
            a1 = a0 + math.pi
        mid = (a0 + a1) * 0.5
        p0 = gp_Pnt(float(c.x) + r * math.cos(a0), float(c.y) + r * math.sin(a0), 0.0)
        pm = gp_Pnt(float(c.x) + r * math.cos(mid), float(c.y) + r * math.sin(mid), 0.0)
        p1 = gp_Pnt(float(c.x) + r * math.cos(a1), float(c.y) + r * math.sin(a1), 0.0)
        arc = GC_MakeArcOfCircle(p0, pm, p1)
        if not arc.IsDone():
            raise ValueError("ARC DXF inválido")
        return _safe_edge(BRepBuilderAPI_MakeEdge(arc.Value()))
    raise ValueError(f"Entidad no soportada por wire OCCT: {typ}")


def _polyline_edges(entity) -> list:
    typ = entity.dxftype()
    if typ not in ("LWPOLYLINE", "POLYLINE"):
        return [_edge_from_entity(entity)]
    if not bool(getattr(entity, "closed", False)):
        raise ValueError("La polilínea plasma debe ser cerrada")
    if typ == "LWPOLYLINE":
        points = [
            (float(v[0]), float(v[1]), float(v[2] or 0.0))
            for v in entity.get_points("xyb")
        ]
    else:
        points = [
            (
                float(v.dxf.location.x),
                float(v.dxf.location.y),
                float(getattr(v.dxf, "bulge", 0.0) or 0.0),
            )
            for v in entity.vertices
        ]
    # Un círculo/obround se dibuja como polilínea cerrada de 2 vértices con
    # bulges: dos arcos ya cierran el contorno.
    if len(points) < 2:
        raise ValueError("Polilínea con menos de dos vértices")

    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Pnt

    edges = []
    for i, (x0, y0, bulge) in enumerate(points):
        x1, y1, _ = points[(i + 1) % len(points)]
        p0, p1 = gp_Pnt(x0, y0, 0.0), gp_Pnt(x1, y1, 0.0)
        if abs(bulge) <= 1e-12:
            if math.hypot(x1 - x0, y1 - y0) <= 1e-12:
                continue
            edges.append(_safe_edge(BRepBuilderAPI_MakeEdge(p0, p1)))
            continue
        dx, dy = x1 - x0, y1 - y0
        chord = math.hypot(dx, dy)
        theta = 4.0 * math.atan(float(bulge))
        sin_half = math.sin(abs(theta) / 2.0)
        if chord <= 1e-12 or abs(sin_half) <= 1e-12:
            raise ValueError("Bulge DXF degenerado")
        radius = chord / (2.0 * sin_half)
        mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        h = math.sqrt(max(radius * radius - (chord / 2.0) ** 2, 0.0))
        nx, ny = -dy / chord, dx / chord
        sign = 1.0 if bulge > 0 else -1.0
        cx, cy = mx + sign * h * nx, my + sign * h * ny
        a0 = math.atan2(y0 - cy, x0 - cx)
        pm = gp_Pnt(
            cx + radius * math.cos(a0 + theta / 2.0),
            cy + radius * math.sin(a0 + theta / 2.0),
            0.0,
        )
        arc = GC_MakeArcOfCircle(p0, pm, p1)
        if not arc.IsDone():
            raise ValueError("No se pudo construir ARC desde bulge")
        edges.append(_safe_edge(BRepBuilderAPI_MakeEdge(arc.Value())))
    if len(edges) < 2:
        raise ValueError("Polilínea sin aristas suficientes tras limpiar degeneradas")
    return edges


def _collect_edges(entities: Iterable) -> list:
    """Ordena LINE/ARC del grupo para que MakeWire conecte extremos contiguos."""
    from OCP.TopoDS import TopoDS

    ents = list(entities or [])
    if not ents:
        return []
    try:
        from modules.plasma_dxf_export import _order_connected_entities

        line_arc = [e for e in ents if e.dxftype() in ("LINE", "ARC")]
        ordered = _order_connected_entities(ents, tol=_GAP_PUENTE_MAX)
        if ordered and len(ordered) == len(line_arc):
            edges = []
            for ent, rev in ordered:
                for edge in _polyline_edges(ent):
                    edges.append(TopoDS.Edge_s(edge.Reversed()) if rev else edge)
            for ent in ents:
                if ent.dxftype() in ("LWPOLYLINE", "POLYLINE"):
                    edges.extend(_polyline_edges(ent))
            if edges:
                return edges
    except Exception:
        pass
    edges = []
    for entity in ents:
        edges.extend(_polyline_edges(entity))
    return edges


# Hueco máximo que se puentea entre entidades del DXF (unidades del dibujo).
# Los exports de CAD dejan micro-gaps; más allá de esto el perfil está roto y
# se rechaza en vez de ofsetear una cadena abierta (casquetes redondos).
_GAP_PUENTE_MAX = 0.02


def _cerrar_edges(edges: list) -> list:
    """Puentea el hueco final con una LINE si el perfil casi cierra."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.TopExp import TopExp
    from OCP.gp import gp_Pnt

    if len(edges) < 2:
        return edges
    inicio = BRep_Tool.Pnt_s(TopExp.FirstVertex_s(edges[0], True))
    fin = BRep_Tool.Pnt_s(TopExp.LastVertex_s(edges[-1], True))
    p0 = (float(inicio.X()), float(inicio.Y()))
    p1 = (float(fin.X()), float(fin.Y()))
    hueco = math.dist(p0, p1)
    if hueco <= 1e-9:
        return edges
    if hueco > _GAP_PUENTE_MAX:
        raise ValueError(
            f"el contorno plasma está abierto (hueco={hueco:.4f} en unidades DXF)"
        )
    puente = BRepBuilderAPI_MakeEdge(gp_Pnt(p1[0], p1[1], 0.0), gp_Pnt(p0[0], p0[1], 0.0))
    if puente.IsDone():
        edges = list(edges) + [puente.Edge()]
    return edges


def _wire_from_entities(entities: Iterable) -> Any:
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
    from OCP.ShapeFix import ShapeFix_Wire

    edges = _cerrar_edges(_collect_edges(entities))
    if not edges:
        raise ValueError("No hay aristas para wire OCCT")
    mk = BRepBuilderAPI_MakeWire()
    for edge in edges:
        mk.Add(edge)
    if not mk.IsDone():
        mk = BRepBuilderAPI_MakeWire(edges[0])
        for edge in edges[1:]:
            mk.Add(edge)
            if not mk.IsDone():
                raise ValueError("No se pudo construir wire OCCT cerrado")
    wire = mk.Wire()
    try:
        fixer = ShapeFix_Wire()
        fixer.Load(wire)
        fixer.SetPrecision(1e-4)
        fixer.SetMaxTolerance(_GAP_PUENTE_MAX * 2.0)
        fixer.ClosedWireMode = True
        fixer.FixReorder()
        try:
            fixer.FixConnected(_GAP_PUENTE_MAX)
        except Exception:
            fixer.FixConnected()
        fixer.FixDegenerated()
        fixer.FixClosed()
        fixed = fixer.Wire()
        if not fixed.IsNull():
            wire = fixed
    except Exception:
        pass
    return wire


def _wires(shape) -> list[Any]:
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    out = []
    exp = TopExp_Explorer(shape, TopAbs_WIRE)
    while exp.More():
        out.append(TopoDS.Wire_s(exp.Current()))
        exp.Next()
    return out


def _wire_ring_points(wire, *, step_deg: float = _SAMPLE_STEP_DEG) -> list[tuple[float, float]]:
    """Puntos del wire en orden de recorrido (respetando orientación de arista)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.TopExp import TopExp

    ring: list[tuple[float, float]] = []
    exp = BRepTools_WireExplorer(wire)
    while exp.More():
        edge = exp.Current()
        curve = BRepAdaptor_Curve(edge)
        u0, u1 = curve.FirstParameter(), curve.LastParameter()
        n = 2
        try:
            from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line

            tipo = curve.GetType()
            if tipo == GeomAbs_Circle:
                # Parámetro de un círculo = radianes: el muestreo sigue el barrido
                # real (un arco de 340° necesita muchos más puntos que uno de 10°).
                barrido = abs(math.degrees(float(u1 - u0)))
                n = max(2, int(math.ceil(barrido / max(step_deg, 0.25))))
            elif tipo != GeomAbs_Line:
                n = 64
        except Exception:
            n = 64
        pts = []
        for i in range(n + 1):
            p = curve.Value(u0 + (u1 - u0) * (i / n))
            pts.append((float(p.X()), float(p.Y())))
        head = BRep_Tool.Pnt_s(TopExp.FirstVertex_s(edge, True))
        if math.dist(pts[0], (float(head.X()), float(head.Y()))) > math.dist(
            pts[-1], (float(head.X()), float(head.Y()))
        ):
            pts.reverse()
        for p in pts:
            if ring and math.dist(ring[-1], p) <= 1e-12:
                continue
            ring.append(p)
        exp.Next()
    return ring


def _native_entities_from_wire(wire) -> list[dict[str, Any]]:
    """Convierte el wire a LINE/ARC/CIRCLE de DXF respetando el arco real.

    DXF dibuja el ARC siempre CCW de ``start_angle`` a ``end_angle``. Si el
    recorrido del wire va CW hay que invertir los ángulos: si no, el DXF
    describe el arco complementario y el perfil sale deforme.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
    from OCP.TopExp import TopExp

    out: list[dict[str, Any]] = []
    exp = BRepTools_WireExplorer(wire)
    while exp.More():
        edge = exp.Current()
        curve = BRepAdaptor_Curve(edge)
        kind = curve.GetType()
        p0 = BRep_Tool.Pnt_s(TopExp.FirstVertex_s(edge, True))
        p1 = BRep_Tool.Pnt_s(TopExp.LastVertex_s(edge, True))
        if kind == GeomAbs_Line:
            out.append(
                {
                    "type": "LINE",
                    "start": (float(p0.X()), float(p0.Y()), 0.0),
                    "end": (float(p1.X()), float(p1.Y()), 0.0),
                }
            )
        elif kind == GeomAbs_Circle:
            circ = curve.Circle()
            center = circ.Location()
            cx, cy = float(center.X()), float(center.Y())
            r = float(circ.Radius())
            if math.dist((p0.X(), p0.Y()), (p1.X(), p1.Y())) <= 1e-7:
                out.append({"type": "CIRCLE", "center": (cx, cy, 0.0), "radius": r})
            else:
                a0 = math.degrees(math.atan2(float(p0.Y()) - cy, float(p0.X()) - cx)) % 360.0
                a1 = math.degrees(math.atan2(float(p1.Y()) - cy, float(p1.X()) - cx)) % 360.0
                u0, u1 = curve.FirstParameter(), curve.LastParameter()
                pm = curve.Value((u0 + u1) * 0.5)
                am = math.degrees(math.atan2(float(pm.Y()) - cy, float(pm.X()) - cx)) % 360.0
                ccw = _angle_in_ccw_sweep(a0, a1, am)
                if not ccw:
                    a0, a1 = a1, a0
                out.append(
                    {
                        "type": "ARC",
                        "center": (cx, cy, 0.0),
                        "radius": r,
                        "start_angle": a0,
                        "end_angle": a1,
                        # Sentido del recorrido del wire; el ARC de DXF ya quedó
                        # normalizado CCW, pero el bulge de polilínea lo necesita.
                        "ccw": bool(ccw),
                    }
                )
        else:
            raise ValueError(f"OCCT produjo curva no nativa DXF: {kind}")
        exp.Next()
    return out


# Más allá de esta relación punta/offset el inglete produce una aguja; en ese
# caso se conserva el redondeo de OCCT, que es inofensivo para el corte.


def _spec_start_end(
    spec: dict[str, Any],
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Extremos inicial y final en el sentido de recorrido del wire."""
    typ = str(spec.get("type") or "")
    if typ == "LINE":
        s, e = spec["start"], spec["end"]
        return (float(s[0]), float(s[1])), (float(e[0]), float(e[1]))
    if typ != "ARC":
        return None
    cx, cy = float(spec["center"][0]), float(spec["center"][1])
    r = float(spec["radius"])
    a0, a1 = float(spec["start_angle"]), float(spec["end_angle"])
    if not bool(spec.get("ccw", True)):
        a0, a1 = a1, a0
    return (
        (cx + r * math.cos(math.radians(a0)), cy + r * math.sin(math.radians(a0))),
        (cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1))),
    )


def _spec_tangents(
    spec: dict[str, Any],
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Direcciones unitarias de entrada y salida en el sentido de recorrido."""
    typ = str(spec.get("type") or "")
    if typ == "LINE":
        ext = _spec_start_end(spec)
        if ext is None:
            return None
        (x0, y0), (x1, y1) = ext
        ln = math.hypot(x1 - x0, y1 - y0)
        if ln < 1e-12:
            return None
        d = ((x1 - x0) / ln, (y1 - y0) / ln)
        return d, d
    if typ != "ARC":
        return None
    ccw = bool(spec.get("ccw", True))
    a0, a1 = float(spec["start_angle"]), float(spec["end_angle"])
    if not ccw:
        a0, a1 = a1, a0

    def tan_at(a: float) -> tuple[float, float]:
        ra = math.radians(a)
        if ccw:
            return (-math.sin(ra), math.cos(ra))
        return (math.sin(ra), -math.cos(ra))

    return tan_at(a0), tan_at(a1)


def _puntas_vivas(
    specs: Sequence[dict[str, Any]], *, tol_deg: float = 1.0
) -> list[dict[str, Any]]:
    """Vértices donde el recorrido quiebra: esquinas que el corte debe conservar."""
    out: list[dict[str, Any]] = []
    n = len(specs or [])
    if n < 2:
        return out
    for i in range(n):
        a, b = specs[i], specs[(i + 1) % n]
        ta, tb = _spec_tangents(a), _spec_tangents(b)
        ea, eb = _spec_start_end(a), _spec_start_end(b)
        if not ta or not tb or ea is None or eb is None:
            continue
        if math.dist(ea[1], eb[0]) > 1e-6:
            continue
        (dix, diy), (dox, doy) = ta[1], tb[0]
        turn = math.degrees(math.atan2(dix * doy - diy * dox, dix * dox + diy * doy))
        if abs(turn) <= tol_deg:
            continue
        out.append({"pt": ea[1], "turn": turn})
    return out


def _inter_line_line(sa, sb) -> list[tuple[float, float]]:
    (x1, y1), (x2, y2) = sa
    (x3, y3), (x4, y4) = sb
    d1x, d1y = x2 - x1, y2 - y1
    d2x, d2y = x4 - x3, y4 - y3
    den = d1x * d2y - d1y * d2x
    if abs(den) < 1e-12:
        return []
    t = ((x3 - x1) * d2y - (y3 - y1) * d2x) / den
    return [(x1 + d1x * t, y1 + d1y * t)]


def _inter_line_circle(seg, arc) -> list[tuple[float, float]]:
    (x1, y1), (x2, y2) = seg
    cx, cy = float(arc["center"][0]), float(arc["center"][1])
    r = float(arc["radius"])
    dx, dy = x2 - x1, y2 - y1
    l2 = dx * dx + dy * dy
    if l2 < 1e-24:
        return []
    fx, fy = x1 - cx, y1 - cy
    b = 2.0 * (fx * dx + fy * dy)
    c = fx * fx + fy * fy - r * r
    disc = b * b - 4.0 * l2 * c
    if disc < 0.0:
        return []
    raiz = math.sqrt(disc)
    return [
        (x1 + dx * t, y1 + dy * t)
        for t in ((-b - raiz) / (2.0 * l2), (-b + raiz) / (2.0 * l2))
    ]


def _inter_circle_circle(a, b) -> list[tuple[float, float]]:
    ax, ay = float(a["center"][0]), float(a["center"][1])
    bx, by = float(b["center"][0]), float(b["center"][1])
    ra, rb = float(a["radius"]), float(b["radius"])
    dx, dy = bx - ax, by - ay
    dist = math.hypot(dx, dy)
    if dist < 1e-12 or dist > ra + rb or dist < abs(ra - rb):
        return []
    t = (ra * ra - rb * rb + dist * dist) / (2.0 * dist)
    h2 = ra * ra - t * t
    if h2 < 0.0:
        return []
    h = math.sqrt(h2)
    mx, my = ax + dx * t / dist, ay + dy * t / dist
    ux, uy = -dy / dist, dx / dist
    return [(mx + ux * h, my + uy * h), (mx - ux * h, my - uy * h)]


def _interseccion_specs(a, b) -> tuple[float, float] | None:
    """Corte de los soportes (recta/círculo) de dos tramos consecutivos."""
    ea, eb = _spec_start_end(a), _spec_start_end(b)
    if ea is None or eb is None:
        return None
    ref = ((ea[1][0] + eb[0][0]) * 0.5, (ea[1][1] + eb[0][1]) * 0.5)
    ta, tb = str(a.get("type") or ""), str(b.get("type") or "")
    if ta == "LINE" and tb == "LINE":
        cands = _inter_line_line(ea, eb)
    elif ta == "LINE" and tb == "ARC":
        cands = _inter_line_circle(ea, b)
    elif ta == "ARC" and tb == "LINE":
        cands = _inter_line_circle(eb, a)
    elif ta == "ARC" and tb == "ARC":
        cands = _inter_circle_circle(a, b)
    else:
        return None
    if not cands:
        return None
    return min(cands, key=lambda p: math.dist(p, ref))


def _mover_extremo(spec: dict[str, Any], pt: tuple[float, float], *, final: bool) -> bool:
    typ = str(spec.get("type") or "")
    if typ == "LINE":
        spec["end" if final else "start"] = (float(pt[0]), float(pt[1]), 0.0)
        return True
    if typ != "ARC":
        return False
    ang = (
        math.degrees(
            math.atan2(pt[1] - float(spec["center"][1]), pt[0] - float(spec["center"][0]))
        )
        % 360.0
    )
    ccw = bool(spec.get("ccw", True))
    clave = ("end_angle" if ccw else "start_angle") if final else (
        "start_angle" if ccw else "end_angle"
    )
    spec[clave] = ang
    return True


def _desredondear_puntas(
    specs: list[dict[str, Any]],
    puntas: Sequence[dict[str, Any]],
    delta: float,
) -> list[dict[str, Any]]:
    """Sustituye por inglete los filetes que OCCT inserta en esquinas vivas.

    ``BRepOffsetAPI_MakeOffset`` ignora ``GeomAbs_Intersection`` y redondea toda
    esquina convexa con radio ``|delta|``. El plasma no puede inventar ese radio:
    una escuadra debe salir en escuadra. Se identifica el filete por radio y por
    centro coincidente con un vértice vivo del origen, y se recorta contra los
    tramos vecinos como haría el OFFSET de AutoCAD.
    """
    d = abs(float(delta))
    n = len(specs or [])
    if d <= 0.0 or n < 3 or not puntas:
        return specs

    tol_r = max(d * 0.02, 1e-7)
    tol_c = max(d * 0.25, 1e-6)
    filetes: dict[int, dict[str, Any]] = {}
    for i, s in enumerate(specs):
        if str(s.get("type") or "") != "ARC":
            continue
        if abs(float(s["radius"]) - d) > tol_r:
            continue
        c = (float(s["center"][0]), float(s["center"][1]))
        cerca = [(math.dist(c, k["pt"]), k) for k in puntas if math.dist(c, k["pt"]) <= tol_c]
        if cerca:
            filetes[i] = min(cerca, key=lambda par: par[0])[1]
    if not filetes:
        return specs

    nuevos = [dict(s) for s in specs]
    quitar: set[int] = set()
    for i, punta in filetes.items():
        prev, sig = (i - 1) % n, (i + 1) % n
        if prev in filetes or sig in filetes or prev == sig:
            continue
        x = _interseccion_specs(nuevos[prev], nuevos[sig])
        if x is None:
            continue
        alcance = math.dist(x, punta["pt"])
        limite = min(_limite_inglete(punta["turn"], d) * 1.05, _MITER_LIMIT * d)
        if alcance < d - tol_r or alcance > limite:
            continue
        if not _mover_extremo(nuevos[prev], x, final=True):
            continue
        if not _mover_extremo(nuevos[sig], x, final=False):
            continue
        quitar.add(i)
    if not quitar:
        return specs
    return [s for k, s in enumerate(nuevos) if k not in quitar]


def _perform_offset(wire, delta: float, join) -> Any:
    """MakeOffset con los constructores disponibles; devuelve shape o lanza."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset

    errors: list[str] = []
    # Cara plana primero: es el spine que devuelve contorno cerrado en 2D.
    try:
        face_mk = BRepBuilderAPI_MakeFace(wire, True)
        if face_mk.IsDone():
            offset = BRepOffsetAPI_MakeOffset(face_mk.Face(), join, False)
            offset.Perform(float(delta))
            if offset.IsDone() and not offset.Shape().IsNull():
                return offset.Shape()
            errors.append("face IsDone=false")
        else:
            errors.append("MakeFace falló")
    except Exception as exc:
        errors.append(f"face: {exc}")

    try:
        offset = BRepOffsetAPI_MakeOffset(wire, join, False)
        offset.Perform(float(delta))
        if offset.IsDone() and not offset.Shape().IsNull():
            return offset.Shape()
        errors.append("wire IsDone=false")
    except Exception as exc:
        errors.append(f"wire: {exc}")

    raise RuntimeError("; ".join(errors) or "OCCT OFFSET falló")


def offset_entities(entities: Iterable, *, delta: float) -> OcctOffsetResult:
    """OFFSET de un contorno cerrado, validado, con LINE/ARC/CIRCLE exactos.

    ``delta`` > 0 crece el contorno; < 0 lo encoge. El resultado solo se
    devuelve si cumple cierre, simplicidad y crecimiento de ``|delta|`` por lado.
    """
    try:
        from OCP.GeomAbs import GeomAbs_Arc, GeomAbs_Intersection

        wire = _wire_from_entities(entities)
        src_ring = _wire_ring_points(wire)
        m_src = _ring_metrics(src_ring)
        if m_src is None:
            return OcctOffsetResult(error="OCCT OFFSET: contorno origen inválido")
        # Un contorno abierto se ofsetearía como cinta con casquetes redondos.
        if m_src["gap"] > _GAP_PUENTE_MAX:
            return OcctOffsetResult(
                error=(
                    "OCCT OFFSET: el contorno de corte no cierra "
                    f"(hueco={m_src['gap']:.4f} unidades DXF); revisa el perfil."
                )
            )

        d = float(delta)
        rechazos: list[str] = []
        # Vértices vivos del origen: son las puntas que el resultado debe
        # conservar y también los únicos lugares donde se admite que el
        # contorno quede a más de |delta| (la punta del inglete).
        try:
            puntas = _puntas_vivas(_native_entities_from_wire(wire))
        except Exception:
            puntas = []
        # La dirección del offset depende de la orientación del wire; se prueban
        # ambos signos y se acepta solo el que valide contra la geometría origen.
        # Intersección conserva las esquinas LINE→LINE como ingletes. Arc se
        # deja sólo como fallback de compatibilidad para un wire que no pueda
        # resolverse por intersección; nunca debe ser la primera opción porque
        # redondea puntas que no existían en el DXF fuente.
        for join in (GeomAbs_Intersection, GeomAbs_Arc):
            for signo in (1.0, -1.0):
                try:
                    shape = _perform_offset(wire, d * signo, join)
                except Exception as exc:
                    rechazos.append(str(exc))
                    continue
                candidatos = _wires(shape)
                if not candidatos:
                    rechazos.append("sin wire de salida")
                    continue
                validos: list[list[dict[str, Any]]] = []
                for out_wire in candidatos:
                    try:
                        specs = _native_entities_from_wire(out_wire)
                    except Exception as exc:
                        rechazos.append(str(exc))
                        continue
                    ring_specs = ring_from_specs(specs)
                    # Lo que se escribe al DXF debe ser lo que OCCT calculó: un
                    # ARC mal convertido describiría el arco complementario.
                    if not rings_coinciden(
                        _wire_ring_points(out_wire), ring_specs, max(abs(d) * 0.1, 1e-4)
                    ):
                        rechazos.append("la conversión a DXF no coincide con el wire OCCT")
                        continue
                    specs = _desredondear_puntas(specs, puntas, d)
                    ring_specs = ring_from_specs(specs)
                    motivo = validate_offset_ring(src_ring, ring_specs, d, puntas=puntas)
                    if motivo:
                        rechazos.append(motivo)
                        continue
                    validos.append(specs)
                if len(validos) == 1:
                    return OcctOffsetResult(entities=validos[0])
                if len(validos) > 1:
                    rechazos.append("offset ambiguo: varios contornos válidos")
        detalle = "; ".join(dict.fromkeys(rechazos)) or "falló"
        return OcctOffsetResult(error=f"OCCT OFFSET: {detalle}")
    except Exception as exc:
        return OcctOffsetResult(error=f"OCCT OFFSET: {exc}")
