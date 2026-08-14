"""OFFSET plasma exacto con Open CASCADE, sin proceso FreeCAD.

Este módulo acepta solo primitivas DXF que puede conservar exactamente:
LINE, ARC, CIRCLE y LWPOLYLINE/POLYLINE (con o sin bulges). Si OCCT produce
una curva no representable en DXF nativo, falla cerrado en vez de facetizarla.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable


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
                gp_Pnt(float(s.x), float(s.y), float(getattr(s, "z", 0.0))),
                gp_Pnt(float(e.x), float(e.y), float(getattr(e, "z", 0.0))),
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
        # Arco casi completo: partir en dos para evitar degeneración.
        if (a1 - a0) >= math.tau - 1e-9:
            a1 = a0 + math.pi
            mid = a0 + math.pi * 0.5
            z = float(getattr(c, "z", 0.0))
            p0 = gp_Pnt(float(c.x) + r * math.cos(a0), float(c.y) + r * math.sin(a0), z)
            pm = gp_Pnt(float(c.x) + r * math.cos(mid), float(c.y) + r * math.sin(mid), z)
            p1 = gp_Pnt(float(c.x) + r * math.cos(a1), float(c.y) + r * math.sin(a1), z)
            arc = GC_MakeArcOfCircle(p0, pm, p1)
            if not arc.IsDone():
                raise ValueError("ARC DXF inválido")
            return _safe_edge(BRepBuilderAPI_MakeEdge(arc.Value()))
        mid = (a0 + a1) * 0.5
        z = float(getattr(c, "z", 0.0))
        p0 = gp_Pnt(float(c.x) + r * math.cos(a0), float(c.y) + r * math.sin(a0), z)
        pm = gp_Pnt(float(c.x) + r * math.cos(mid), float(c.y) + r * math.sin(mid), z)
        p1 = gp_Pnt(float(c.x) + r * math.cos(a1), float(c.y) + r * math.sin(a1), z)
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
        verts = list(entity.get_points("xyb"))
        points = [(float(v[0]), float(v[1]), float(v[2] or 0.0)) for v in verts]
    else:
        points = [
            (
                float(v.dxf.location.x),
                float(v.dxf.location.y),
                float(getattr(v.dxf, "bulge", 0.0) or 0.0),
            )
            for v in entity.vertices
        ]
    if len(points) < 3:
        raise ValueError("Polilínea con menos de tres vértices")
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Pnt

    edges = []
    for i, (x0, y0, _) in enumerate(points):
        x1, y1, _b = points[(i + 1) % len(points)]
        bulge = points[i][2]
        p0 = gp_Pnt(x0, y0, 0.0)
        p1 = gp_Pnt(x1, y1, 0.0)
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
        amid = a0 + theta / 2.0
        pm = gp_Pnt(cx + radius * math.cos(amid), cy + radius * math.sin(amid), 0.0)
        arc = GC_MakeArcOfCircle(p0, pm, p1)
        if not arc.IsDone():
            raise ValueError("No se pudo construir ARC desde bulge")
        edges.append(_safe_edge(BRepBuilderAPI_MakeEdge(arc.Value())))
    if len(edges) < 3:
        raise ValueError("Polilínea sin aristas suficientes tras limpiar degeneradas")
    return edges


def _collect_edges(entities: Iterable) -> list:
    """Ordena LINE/ARC del grupo para que MakeWire conecte extremos cercanos."""
    from OCP.TopoDS import TopoDS

    ents = list(entities or [])
    if not ents:
        return []
    try:
        from modules.plasma_dxf_export import _order_connected_entities

        ordered = _order_connected_entities(ents, tol=1e-3)
        line_arc = [e for e in ents if e.dxftype() in ("LINE", "ARC")]
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


def _wire_from_entities(entities: Iterable) -> Any:
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
    from OCP.ShapeFix import ShapeFix_Wire

    edges = _collect_edges(entities)
    if len(edges) < 1:
        raise ValueError("No hay aristas para wire OCCT")
    mk = BRepBuilderAPI_MakeWire()
    for edge in edges:
        mk.Add(edge)
    if not mk.IsDone():
        # Reintento: unir aristas con MakeWire incremental tolerante.
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
        fixer.SetMaxTolerance(1e-2)
        fixer.ClosedWireMode = True
        fixer.FixReorder()
        fixer.FixConnected()
        fixer.FixDegenerated()
        fixer.FixClosed()
        fixed = fixer.Wire()
        if not fixed.IsNull():
            wire = fixed
    except Exception:
        pass
    return wire


def _wire_signed_area(wire) -> float:
    """Área con signo 2D (CCW > 0) muestreando extremos de aristas."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.TopExp import TopExp

    pts: list[tuple[float, float]] = []
    exp = BRepTools_WireExplorer(wire)
    while exp.More():
        edge = exp.Current()
        v0 = TopExp.FirstVertex_s(edge)
        p0 = BRep_Tool.Pnt_s(v0)
        pts.append((float(p0.X()), float(p0.Y())))
        exp.Next()
    if len(pts) < 3:
        return 0.0
    area = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        area += x0 * y1 - x1 * y0
    return area * 0.5


def _oriented_wire(wire, *, want_ccw: bool):
    from OCP.TopoDS import TopoDS

    area = _wire_signed_area(wire)
    is_ccw = area > 0.0
    if want_ccw == is_ccw:
        return wire
    return TopoDS.Wire_s(wire.Reversed())


def _as_wire(shape):
    from OCP.TopoDS import TopoDS

    return TopoDS.Wire_s(shape)


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


def _native_entities_from_wire(wire) -> list[dict[str, Any]]:
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
        v0, v1 = TopExp.FirstVertex_s(edge), TopExp.LastVertex_s(edge)
        p0, p1 = BRep_Tool.Pnt_s(v0), BRep_Tool.Pnt_s(v1)
        if kind == GeomAbs_Line:
            out.append(
                {
                    "type": "LINE",
                    "start": (float(p0.X()), float(p0.Y()), float(p0.Z())),
                    "end": (float(p1.X()), float(p1.Y()), float(p1.Z())),
                }
            )
        elif kind == GeomAbs_Circle:
            circ = curve.Circle()
            center = circ.Location()
            r = float(circ.Radius())
            a0 = math.degrees(math.atan2(p0.Y() - center.Y(), p0.X() - center.X())) % 360.0
            a1 = math.degrees(math.atan2(p1.Y() - center.Y(), p1.X() - center.X())) % 360.0
            # OCCT usa círculo para arcos de join; si start/end coinciden es CIRCLE.
            if math.dist((p0.X(), p0.Y()), (p1.X(), p1.Y())) <= 1e-7:
                out.append(
                    {
                        "type": "CIRCLE",
                        "center": (float(center.X()), float(center.Y()), float(center.Z())),
                        "radius": r,
                    }
                )
            else:
                out.append(
                    {
                        "type": "ARC",
                        "center": (float(center.X()), float(center.Y()), float(center.Z())),
                        "radius": r,
                        "start_angle": a0,
                        "end_angle": a1,
                    }
                )
        else:
            raise ValueError(f"OCCT produjo curva no nativa DXF: {kind}")
        exp.Next()
    return out


def _perform_offset(wire, delta: float, join) -> Any:
    """Ejecuta MakeOffset con varios constructores; no deja que Perform tumbe sin mensaje."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset

    errors: list[str] = []

    # 1) Wire directo (preferido para 2D).
    try:
        offset = BRepOffsetAPI_MakeOffset(wire, join, False)
        offset.Perform(float(delta))
        if offset.IsDone() and not offset.Shape().IsNull():
            return offset.Shape()
        errors.append("wire+join IsDone=false")
    except Exception as exc:
        errors.append(f"wire+join: {exc}")

    # 2) Init/AddWire.
    try:
        offset = BRepOffsetAPI_MakeOffset()
        offset.Init(join, False)
        offset.AddWire(wire)
        offset.Perform(float(delta))
        if offset.IsDone() and not offset.Shape().IsNull():
            return offset.Shape()
        errors.append("Init/AddWire IsDone=false")
    except Exception as exc:
        errors.append(f"Init/AddWire: {exc}")

    # 3) Face plana.
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

    raise RuntimeError("; ".join(errors) or "OCCT OFFSET falló")


def offset_entities(entities: Iterable, *, delta: float) -> OcctOffsetResult:
    """OFFSET de un wire cerrado manteniendo LINE/ARC/CIRCLE exactos."""
    try:
        from OCP.GeomAbs import GeomAbs_Arc, GeomAbs_Intersection

        raw = _wire_from_entities(entities)
        # OUTER (+delta) exige CCW; INNER (−delta) también se orienta CCW y
        # el signo del delta define crecimiento/contracción del metal.
        wire = _oriented_wire(raw, want_ccw=True)
        last_err = ""
        for join in (GeomAbs_Arc, GeomAbs_Intersection):
            candidates = (wire, _as_wire(wire.Reversed()))
            for oriented in candidates:
                try:
                    shape = _perform_offset(oriented, float(delta), join)
                    wires = _wires(shape)
                    if not wires:
                        last_err = "OCCT OFFSET no devolvió wire"
                        continue
                    # Preferir el wire de mayor área absoluta (contorno principal).
                    wires_sorted = sorted(
                        wires, key=lambda w: abs(_wire_signed_area(w)), reverse=True
                    )
                    result: list[dict[str, Any]] = []
                    for out_wire in wires_sorted[:1]:
                        result.extend(_native_entities_from_wire(out_wire))
                    if result:
                        return OcctOffsetResult(entities=result)
                    last_err = "OCCT OFFSET sin entidades nativas"
                except Exception as exc:
                    last_err = str(exc)
                    continue
        return OcctOffsetResult(error=f"OCCT OFFSET: {last_err or 'falló'}")
    except Exception as exc:
        return OcctOffsetResult(error=f"OCCT OFFSET: {exc}")
