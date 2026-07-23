"""Export DXF nest → STEP con paridad visual FreeCAD (cobre metálico + MARK ENGRAVE)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ezdxf

from .occt_runtime import ensure_ocp, solid_volume, write_step_xcaf

# FreeCAD freecad_batch_dxf_to_step._appearance_preset("copper")
COPPER_RGB = (0.78, 0.48, 0.22)
COPPER_AMBIENT = (0.32, 0.20, 0.09)
COPPER_SPECULAR = (0.95, 0.78, 0.45)
COPPER_SHININESS = 0.90

STEEL_RGB = (0.58, 0.60, 0.63)
MARK_RGB = (0.098039215, 0.098039215, 0.098039215)

# generador_verde / FREECAD_MARK_* defaults
MARK_WIDTH = 0.02
MARK_Z_OFFSET = -0.5
MARK_RIB_H = 1.0

MM_PER_IN = 25.4


@dataclass
class DxfNestGeometry:
    outer_wires: list[Any]
    inner_wires: list[Any]
    mark_segs: list[tuple[float, float, float, float]]
    plate_wires: list[Any] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.plate_wires is None:
            self.plate_wires = []


@dataclass(frozen=True)
class MaterialAppearance:
    diffuse: tuple[float, float, float]
    ambient: tuple[float, float, float]
    specular: tuple[float, float, float]
    shininess: float


def copper_appearance() -> MaterialAppearance:
    return MaterialAppearance(
        diffuse=COPPER_RGB,
        ambient=COPPER_AMBIENT,
        specular=COPPER_SPECULAR,
        shininess=COPPER_SHININESS,
    )


def steel_appearance() -> MaterialAppearance:
    return MaterialAppearance(
        diffuse=STEEL_RGB,
        ambient=(0.22, 0.23, 0.25),
        specular=(0.90, 0.90, 0.92),
        shininess=0.72,
    )


def _layer_u(e) -> str:
    return str(getattr(e.dxf, "layer", "") or "").upper()


def _is_closed_lw(e) -> bool:
    try:
        if bool(e.closed):
            return True
    except Exception:
        pass
    try:
        return bool(int(getattr(e.dxf, "flags", 0)) & 1)
    except Exception:
        return False


def _lw_points_xy(e) -> list[tuple[float, float]]:
    return [(float(item[0]), float(item[1])) for item in e.get_points("xy")]


def _wire_from_xy(pts: list[tuple[float, float]], *, z: float = 0.0, closed: bool = True):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt

    if len(pts) < 2:
        return None
    poly = BRepBuilderAPI_MakePolygon()
    for x, y in pts:
        poly.Add(gp_Pnt(float(x), float(y), float(z)))
    if closed:
        poly.Close()
    if not poly.IsDone():
        return None
    return poly.Wire()


def _extrude_wire(wire, thk: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    face_mk = BRepBuilderAPI_MakeFace(wire, True)
    if not face_mk.IsDone():
        return None
    prism = BRepPrimAPI_MakePrism(face_mk.Face(), gp_Vec(0.0, 0.0, float(thk)))
    return prism.Shape() if prism.IsDone() else None


def _compound(shapes: list[Any]):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    if not shapes:
        return None
    if len(shapes) == 1:
        return shapes[0]
    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)
    for sh in shapes:
        builder.Add(comp, sh)
    return comp


def _cut(body, tool, *, parallel: bool = False):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    cut = BRepAlgoAPI_Cut(body, tool)
    if parallel:
        try:
            cut.SetRunParallel(True)
        except Exception:
            pass
    cut.Build()
    if not cut.IsDone():
        return None
    return cut.Shape()


def _common(a, b):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common

    op = BRepAlgoAPI_Common(a, b)
    op.Build()
    if not op.IsDone():
        return None
    return op.Shape()


def _unify_shape(shape):
    """Fusiona caras/aristas coplanares → menos entidades STEP (cerca de FreeCAD)."""
    if shape is None:
        return None
    try:
        from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

        un = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
        un.Build()
        out = un.Shape()
        return out if out is not None else shape
    except Exception:
        return shape


def _cookie_prism(wire, thk_mm: float):
    """Prisma OUTER con margen Z (generador_verde: extrude thk+2, translate z=-1)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Trsf, gp_Vec

    face_mk = BRepBuilderAPI_MakeFace(wire, True)
    if not face_mk.IsDone():
        return None
    prism = BRepPrimAPI_MakePrism(face_mk.Face(), gp_Vec(0.0, 0.0, float(thk_mm) + 2.0))
    if not prism.IsDone():
        return None
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(0.0, 0.0, -1.0))
    return BRepBuilderAPI_Transform(prism.Shape(), trsf, True).Shape()


def _largest_solid(shape):
    """Tras un boolean, quédate con el sólido de mayor volumen (evita escombros)."""
    ensure_ocp()
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopoDS import TopoDS

    if shape is None:
        return None
    try:
        if shape.ShapeType() == TopAbs_SOLID:
            return shape
    except Exception:
        pass
    solids = []
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        solids.append(TopoDS.Solid_s(exp.Current()))
        exp.Next()
    if not solids:
        return shape
    if len(solids) == 1:
        return solids[0]
    best = max(solids, key=_shape_volume)
    if _shape_volume(best) <= 1e-9:
        return shape
    return best


def _bbox_xy(shape) -> tuple[float, float, float, float] | None:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    if shape is None:
        return None
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box, True)
    if box.IsVoid():
        return None
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return float(xmin), float(ymin), float(xmax), float(ymax)


def _seg_hits_bbox(
    seg: tuple[float, float, float, float],
    bb: tuple[float, float, float, float],
    margin: float = 1.5,
) -> bool:
    x1, y1, x2, y2 = seg
    xmin, ymin, xmax, ymax = bb
    xmin -= margin
    ymin -= margin
    xmax += margin
    ymax += margin
    for x, y in ((x1, y1), (x2, y2), (0.5 * (x1 + x2), 0.5 * (y1 + y2))):
        if xmin <= x <= xmax and ymin <= y <= ymax:
            return True
    sx0, sx1 = (x1, x2) if x1 <= x2 else (x2, x1)
    sy0, sy1 = (y1, y2) if y1 <= y2 else (y2, y1)
    return not (sx1 < xmin or sx0 > xmax or sy1 < ymin or sy0 > ymax)


def _mark_groove_tool(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    z_bottom: float,
    height: float,
    width: float,
):
    """Prisma rectangular a lo largo del segmento MARK (mismo criterio que generador_verde)."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

    dx = float(x2 - x1)
    dy = float(y2 - y1)
    length = math.hypot(dx, dy)
    if length < 1e-9 or width <= 1e-9 or height <= 1e-9:
        return None
    pad = min(width * 0.2, length * 0.08)
    box = BRepPrimAPI_MakeBox(length + 2.0 * pad, width, height).Shape()

    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(-pad, -0.5 * width, 0.0))
    box = BRepBuilderAPI_Transform(box, trsf, True).Shape()

    ang = math.atan2(dy, dx)
    trsf_r = gp_Trsf()
    trsf_r.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), ang)
    box = BRepBuilderAPI_Transform(box, trsf_r, True).Shape()

    trsf_t = gp_Trsf()
    trsf_t.SetTranslation(gp_Vec(float(x1), float(y1), float(z_bottom)))
    return BRepBuilderAPI_Transform(box, trsf_t, True).Shape()


def _shape_volume(shape) -> float:
    try:
        return float(solid_volume(shape))
    except Exception:
        return 0.0


def _engrave_marks_on_solid(
    solid,
    segs: list[tuple[float, float, float, float]],
    *,
    thk_mm: float,
    off_z: float,
    width: float = MARK_WIDTH,
    z_offset: float = MARK_Z_OFFSET,
    rib_h: float = MARK_RIB_H,
    chunk: int = 100,
):
    """
    Boolean CUT de ranuras MARK (ENGRAVE FreeCAD).
    Lotes de `chunk` como generador_verde._apply_imprint_ribbons_on_solid.
    """
    if solid is None or not segs:
        return solid
    z_bottom = float(off_z) + float(thk_mm) + float(z_offset)
    out = solid

    w = float(width)
    if w <= 1e-9:
        w = MARK_WIDTH

    tools: list[Any] = []
    for x1, y1, x2, y2 in segs:
        tool = _mark_groove_tool(
            x1, y1, x2, y2, z_bottom=z_bottom, height=float(rib_h), width=w
        )
        if tool is not None:
            tools.append(tool)
    if not tools:
        return solid

    def _try_cut(body, tool_shape):
        try:
            raw = _cut(body, tool_shape)
            if raw is None:
                return None
            trial = _largest_solid(raw)
            if trial is None:
                return None
            v1 = _shape_volume(trial)
            v_body = _shape_volume(body)
            if v_body <= 1e-12:
                return None
            # Debe quitar material, sin destruir la pieza (restos de ranura ~0.09).
            if v1 <= 1e-6:
                return None
            if v1 >= (v_body - 1e-6):
                return None  # no-op
            if v1 < v_body * 0.50:
                return None  # pieza destruida / scrap
            return trial
        except Exception:
            return None

    step = max(1, int(chunk or 100))
    for i in range(0, len(tools), step):
        bloque = tools[i : i + step]
        tool_shape = bloque[0] if len(bloque) == 1 else _compound(bloque)
        got = _try_cut(out, tool_shape)
        if got is not None:
            out = got
            continue
        for tool in bloque:
            got = _try_cut(out, tool)
            if got is not None:
                out = got
    return out


def _placement_delta(
    bbox_xy: tuple[float, float, float, float] | None,
    origen: str | None,
    off_x: float,
    off_y: float,
    off_z: float,
) -> tuple[float, float, float]:
    """Ancla TR/BR (generador_verde) + offset robot."""
    ori = str(origen or "").strip().upper()
    if bbox_xy is None or ori not in ("TR", "BR"):
        return float(off_x), float(off_y), float(off_z)
    _xmin, ymin, xmax, ymax = bbox_xy
    if ori == "TR":
        dx_ancla, dy_ancla = -float(xmax), -float(ymax)
    else:
        dx_ancla, dy_ancla = -float(xmax), -float(ymin)
    return dx_ancla + float(off_x), dy_ancla + float(off_y), float(off_z)


def _translate_shapes(shapes: list[Any], dx: float, dy: float, dz: float) -> list[Any]:
    if not shapes:
        return []
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy, BRepBuilderAPI_Transform

    if abs(dx) + abs(dy) + abs(dz) <= 1e-12:
        return [BRepBuilderAPI_Copy(sh).Shape() for sh in shapes if sh is not None]
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(float(dx), float(dy), float(dz)))
    # Copy=True ya duplica; no hace falta copy previo.
    return [
        BRepBuilderAPI_Transform(sh, trsf, True).Shape()
        for sh in shapes
        if sh is not None
    ]


def _anchor_bbox_xy(geom: DxfNestGeometry, solids: list[Any], thk_mm: float):
    """BBox de ancla: PLATE (FreeCAD) o compound de piezas."""
    if geom.plate_wires:
        plate_solids = []
        for w in geom.plate_wires:
            sol = _extrude_wire(w, thk_mm)
            if sol is not None:
                plate_solids.append(sol)
        if plate_solids:
            bb = _bbox_xy(_compound(plate_solids))
            if bb is not None:
                return bb
    if solids:
        return _bbox_xy(_compound([s for s in solids if s is not None]))
    return None


def _edge_line(x1: float, y1: float, x2: float, y2: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.gp import gp_Pnt

    if abs(x1 - x2) < 1e-12 and abs(y1 - y2) < 1e-12:
        return None
    return BRepBuilderAPI_MakeEdge(gp_Pnt(x1, y1, 0.0), gp_Pnt(x2, y2, 0.0)).Edge()


def _edge_arc(cx: float, cy: float, r: float, a0_deg: float, a1_deg: float):
    """Arco DXF (ángulos en grados, CCW) → TopoDS_Edge."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    if r <= 1e-12:
        return None
    circ = gp_Circ(gp_Ax2(gp_Pnt(cx, cy, 0.0), gp_Dir(0, 0, 1)), float(r))
    a0 = math.radians(float(a0_deg))
    a1 = math.radians(float(a1_deg))
    # Puntos extremos (mismo criterio que ezdxf: start/end angle)
    p0 = gp_Pnt(cx + r * math.cos(a0), cy + r * math.sin(a0), 0.0)
    p1 = gp_Pnt(cx + r * math.cos(a1), cy + r * math.sin(a1), 0.0)
    # Punto medio del arco CCW
    span = (a1 - a0) % (2.0 * math.pi)
    if span < 1e-9:
        span = 2.0 * math.pi
    am = a0 + 0.5 * span
    pm = gp_Pnt(cx + r * math.cos(am), cy + r * math.sin(am), 0.0)
    try:
        arc = GC_MakeArcOfCircle(p0, pm, p1).Value()
        return BRepBuilderAPI_MakeEdge(arc).Edge()
    except Exception:
        try:
            return BRepBuilderAPI_MakeEdge(circ, a0, a0 + span).Edge()
        except Exception:
            return None


def _stitch_edges_to_closed_wires(edges: list, *, tol: float = 1e-3) -> list:
    """Une aristas sueltas (LINE/ARC de nesteos acero) en wires cerrados."""
    if not edges:
        return []
    from OCP.BRep import BRep_Tool
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_HSequenceOfShape

    seq = TopTools_HSequenceOfShape()
    for e in edges:
        if e is not None:
            seq.Append(e)
    if seq.Length() <= 0:
        return []
    wires_h = TopTools_HSequenceOfShape()
    try:
        ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(seq, float(tol), False, wires_h)
    except Exception:
        # Fallback API sin _s
        try:
            ShapeAnalysis_FreeBounds.ConnectEdgesToWires(seq, float(tol), False, wires_h)
        except Exception:
            return []
    out = []
    for i in range(1, int(wires_h.Length()) + 1):
        w = TopoDS.Wire_s(wires_h.Value(i))
        try:
            if BRep_Tool.IsClosed_s(w):
                out.append(w)
                continue
        except Exception:
            pass
        try:
            if w.Closed():
                out.append(w)
        except Exception:
            pass
    return out


def collect_dxf_nest(dxf_path: str | Path) -> DxfNestGeometry:
    """
    Clasifica capas como freecad_batch_dxf_to_step:
      CUT_OUTER / CUT_CU → piezas (sólidos)
      CUT_INNER → agujeros
      MARK / ETCH / TEXT → marcaje (no es corte)
      PLATE → contorno de placa (referencia; NO es pieza de corte)
    """
    ensure_ocp()
    doc = ezdxf.readfile(str(dxf_path))
    outers: list = []
    inners: list = []
    plates: list = []
    marks: list[tuple[float, float, float, float]] = []
    outer_edges: list = []
    inner_edges: list = []

    for e in doc.modelspace():
        layer = _layer_u(e)
        dt = e.dxftype()
        is_plate = layer == "PLATE" or layer.startswith("PLATE")
        is_outer = (
            "CUT_OUTER" in layer
            or layer in ("OUTER", "EXTER", "CUT")
            or layer.endswith("_CUT")
            or "CUT_CU" in layer
        )
        is_inner = (
            "CUT_INNER" in layer
            or "INNER" in layer
            or "INTER" in layer
            or "HOLE" in layer
        )
        is_mark = (
            "MARK" in layer
            or "ETCH" in layer
            or "TEXT" in layer
            or layer == "PLATE_TEXT"
        )

        if dt == "LWPOLYLINE":
            pts = _lw_points_xy(e)
            if len(pts) < 2:
                continue
            closed = _is_closed_lw(e)
            if is_mark:
                for i in range(len(pts) - 1):
                    marks.append((pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]))
                if closed and len(pts) >= 2:
                    marks.append((pts[-1][0], pts[-1][1], pts[0][0], pts[0][1]))
                continue
            wire = _wire_from_xy(pts, closed=closed)
            if wire is None:
                continue
            if is_plate and closed:
                plates.append(wire)
            elif is_inner and closed:
                inners.append(wire)
            elif is_outer and closed:
                outers.append(wire)
            # No fallback: capas raras no se convierten en piezas (evita PLATE→sólido)

        elif dt == "LINE":
            x1, y1 = float(e.dxf.start.x), float(e.dxf.start.y)
            x2, y2 = float(e.dxf.end.x), float(e.dxf.end.y)
            if is_mark:
                marks.append((x1, y1, x2, y2))
                continue
            if is_plate:
                continue
            edge = _edge_line(x1, y1, x2, y2)
            if edge is None:
                continue
            if is_inner:
                inner_edges.append(edge)
            elif is_outer:
                outer_edges.append(edge)

        elif dt == "ARC":
            cx, cy = float(e.dxf.center.x), float(e.dxf.center.y)
            r = float(e.dxf.radius)
            a0 = float(e.dxf.start_angle)
            a1 = float(e.dxf.end_angle)
            if is_mark:
                span = (a1 - a0) % 360.0
                if span < 1e-9:
                    span = 360.0
                n = max(4, int(span / 12.0))
                for i in range(n):
                    t0 = math.radians(a0 + span * i / n)
                    t1 = math.radians(a0 + span * (i + 1) / n)
                    marks.append(
                        (
                            cx + r * math.cos(t0),
                            cy + r * math.sin(t0),
                            cx + r * math.cos(t1),
                            cy + r * math.sin(t1),
                        )
                    )
                continue
            if is_plate:
                continue
            edge = _edge_arc(cx, cy, r, a0, a1)
            if edge is None:
                continue
            if is_inner:
                inner_edges.append(edge)
            elif is_outer:
                outer_edges.append(edge)

        elif dt == "CIRCLE" and is_inner:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
            from OCP.GC import GC_MakeCircle
            from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

            cx, cy = float(e.dxf.center.x), float(e.dxf.center.y)
            r = float(e.dxf.radius)
            circ = GC_MakeCircle(gp_Ax2(gp_Pnt(cx, cy, 0.0), gp_Dir(0, 0, 1)), r).Value()
            inners.append(BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(circ).Edge()).Wire())

    # Nesteos acero: LINE/ARC de CUT_* → wires cerrados
    outers.extend(_stitch_edges_to_closed_wires(outer_edges, tol=1e-3))
    inners.extend(_stitch_edges_to_closed_wires(inner_edges, tol=1e-3))

    return DxfNestGeometry(
        outer_wires=outers,
        inner_wires=inners,
        mark_segs=marks,
        plate_wires=plates,
    )


def _mark_edges_compound(
    segs: list[tuple[float, float, float, float]],
    *,
    z: float,
) -> Any | None:
    """Aristas libres de MARK en z=thk (como YELLOW_MARKS de FreeCAD)."""
    if not segs:
        return None
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.gp import gp_Pnt

    edges = []
    for x1, y1, x2, y2 in segs:
        if abs(x1 - x2) < 1e-12 and abs(y1 - y2) < 1e-12:
            continue
        try:
            edges.append(
                BRepBuilderAPI_MakeEdge(
                    gp_Pnt(float(x1), float(y1), float(z)),
                    gp_Pnt(float(x2), float(y2), float(z)),
                ).Edge()
            )
        except Exception:
            pass
    return _compound(edges)


def build_freecad_like_shapes(
    geom: DxfNestGeometry,
    *,
    thk_mm: float,
    off_x: float = 0.0,
    off_y: float = 0.0,
    off_z: float = 0.0,
    origen: str | None = None,
    mark_mode: str = "ENGRAVE",
    apply_placement: bool = True,
) -> tuple[Any | None, list[Any], tuple[float, float, float, float] | None]:
    """
    Sólidos CUT_OUTER − INNER + ENGRAVE por pieza (rápido).
    Ancla TR/BR + offset al final si apply_placement.
    """
    ensure_ocp()

    mode = str(mark_mode or "ENGRAVE").strip().upper()
    if mode in ("CUT", "GROOVE", "BOOLEAN", "CARVE", "REAL"):
        mode = "ENGRAVE"

    outer_solids = []
    for w in geom.outer_wires:
        sol = _extrude_wire(w, thk_mm)
        if sol is not None:
            outer_solids.append(sol)

    inner_solids = []
    for w in geom.inner_wires:
        sol = _extrude_wire(w, thk_mm)
        if sol is not None:
            inner_solids.append(sol)

    if inner_solids:
        tool = _compound(inner_solids)
        final_parts = []
        for body in outer_solids:
            cut_res = _cut(body, tool)
            final_parts.append(cut_res if cut_res is not None else body)
    else:
        final_parts = list(outer_solids)

    segs = list(geom.mark_segs)
    solids: list[Any] = []
    if mode == "ENGRAVE" and segs:
        for sh in final_parts:
            if sh is None:
                continue
            bb = _bbox_xy(sh)
            piece_segs = (
                [s for s in segs if bb and _seg_hits_bbox(s, bb)] if bb else list(segs)
            )
            solids.append(
                _engrave_marks_on_solid(
                    sh, piece_segs, thk_mm=thk_mm, off_z=0.0
                )
            )
    else:
        solids = [sh for sh in final_parts if sh is not None]

    anchor_bb = _anchor_bbox_xy(geom, solids, thk_mm)

    if apply_placement:
        dx, dy, dz = _placement_delta(anchor_bb, origen, off_x, off_y, off_z)
        solids = _translate_shapes(solids, dx, dy, dz)

    parts = _compound(solids) if solids else None
    return parts, solids, anchor_bb


# Compat alias
def build_freecad_batch_shapes(geom, **kwargs):
    parts, solids, _bb = build_freecad_like_shapes(geom, mark_mode="EDGES", **kwargs)
    return parts, None, solids


def _write_solids_step(
    solids: list[Any],
    out_step: str | Path,
    *,
    appearance: MaterialAppearance,
) -> Path:
    body = _compound([s for s in solids if s is not None])
    if body is None:
        raise RuntimeError("Compound vacío")
    items: list[tuple] = [(body, appearance.diffuse, "surf", appearance)]
    return write_step_xcaf(items, out_step, as_multibody=True)


def export_dxf_to_step_freecad_batch(
    dxf_path: str | Path,
    out_step: str | Path,
    *,
    thk_mm: float,
    material: str = "CU",
    off_x: float = 0.0,
    off_y: float = 0.0,
    off_z: float = 0.0,
    origen: str | None = None,
    mark_mode: str | None = None,
    include_plate: bool = False,
) -> dict:
    """
    Export STEP (paridad práctica FreeCAD + Inventor IPT multibody):
    - CUT_OUTER → sólidos de piezas en UN Compound (IPT multibody, no .iam)
    - CUT_INNER → boolean cut
    - MARK → ENGRAVE (ranuras en cada sólido); no producto aparte de curvas
    - PLATE → no se extruye (evita “placa entera”); opt-in con include_plate
    - origen TR/BR → ancla bbox (FreeCAD) + off_*
    """
    geom = collect_dxf_nest(dxf_path)
    if not geom.outer_wires:
        raise RuntimeError(
            f"Sin CUT_OUTER cerrados (¿solo Plate/MARK?): {dxf_path}"
        )

    mat = str(material or "").strip().upper()
    is_copper = mat in ("CU", "COBRE", "COPPER")
    appearance = copper_appearance() if is_copper else steel_appearance()

    # Siempre ENGRAVE para que MARK viva DENTRO de los sólidos (1 producto = IPT).
    # Un segundo label de curvas → Inventor abre .iam.
    mode = (mark_mode or "ENGRAVE").strip().upper()
    if mode in ("EDGE", "EDGES", "CURVE", "CURVES", "WIRE", "FREE"):
        # Forzar grabado: curvas sueltas rompen multibody en Inventor
        mode = "ENGRAVE"

    parts, solids, anchor_bb = build_freecad_like_shapes(
        geom,
        thk_mm=thk_mm,
        off_x=off_x,
        off_y=off_y,
        off_z=off_z,
        origen=origen,
        mark_mode=mode,
        apply_placement=True,
    )
    if parts is None and not solids:
        raise RuntimeError("No se generaron sólidos de CUT_OUTER")

    shapes_for_compound: list[Any] = []

    if include_plate and geom.plate_wires:
        plate_solids = []
        for w in geom.plate_wires:
            sol = _extrude_wire(w, thk_mm)
            if sol is not None:
                plate_solids.append(sol)
        dx, dy, dz = _placement_delta(anchor_bb, origen, off_x, off_y, off_z)
        plate_solids = _translate_shapes(plate_solids, dx, dy, dz)
        plate_comp = _compound(plate_solids)
        if plate_comp is not None:
            shapes_for_compound.append(plate_comp)

    if solids:
        shapes_for_compound.extend(solids)
    elif parts is not None:
        shapes_for_compound.append(parts)

    body = _compound(shapes_for_compound)
    if body is None:
        raise RuntimeError("Compound vacío")

    written = _write_solids_step(shapes_for_compound, out_step, appearance=appearance)

    vol = 0.0
    try:
        vol = solid_volume(body)
    except Exception:
        pass

    return {
        "path": written,
        "outers": len(geom.outer_wires),
        "inners": len(geom.inner_wires),
        "mark_segs": len(geom.mark_segs),
        "plates": len(geom.plate_wires or []),
        "volume": vol,
        "bytes": written.stat().st_size,
        "material": mat,
        "thk_mm": float(thk_mm),
        "mark_mode": mode,
        "solids": len(solids or []),
        "include_plate": bool(include_plate),
        "origen": str(origen or ""),
    }


def export_dxf_to_step_robot_camas(
    dxf_path: str | Path,
    out_step_a: str | Path,
    out_step_b: str | Path,
    *,
    thk_mm: float,
    material: str = "STEEL",
    offset_a: tuple[float, float, float] = (4235.0, -1015.0, -700.0),
    offset_b: tuple[float, float, float] = (4235.0, 840.0, -700.0),
    origen_a: str = "TR",
    origen_b: str = "BR",
    mark_mode: str | None = None,
) -> dict:
    """
    Un solo build (extrude+engrave); Cama A/B = ancla TR/BR + offset (FreeCAD).
    Evita rehacer 1165+ boolean marks para B.
    """
    import time

    t0 = time.perf_counter()
    geom = collect_dxf_nest(dxf_path)
    if not geom.outer_wires:
        raise RuntimeError(
            f"Sin CUT_OUTER cerrados (¿solo Plate/MARK?): {dxf_path}"
        )

    mat = str(material or "").strip().upper()
    is_copper = mat in ("CU", "COBRE", "COPPER")
    appearance = copper_appearance() if is_copper else steel_appearance()

    mode = (mark_mode or "ENGRAVE").strip().upper()
    if mode in ("EDGE", "EDGES", "CURVE", "CURVES", "WIRE", "FREE"):
        mode = "ENGRAVE"

    # Build + engraver una sola vez, sin placement
    _parts, solids_base, anchor_bb = build_freecad_like_shapes(
        geom,
        thk_mm=thk_mm,
        off_x=0.0,
        off_y=0.0,
        off_z=0.0,
        origen=None,
        mark_mode=mode,
        apply_placement=False,
    )
    if not solids_base:
        raise RuntimeError("No se generaron sólidos de CUT_OUTER")
    t_build = time.perf_counter() - t0

    oxa, oya, oza = offset_a
    oxb, oyb, ozb = offset_b
    dx_a, dy_a, dz_a = _placement_delta(anchor_bb, origen_a, oxa, oya, oza)
    dx_b, dy_b, dz_b = _placement_delta(anchor_bb, origen_b, oxb, oyb, ozb)

    t1 = time.perf_counter()
    # Preparar A y B ANTES de escribir (XCAF no debe mutar la base).
    solids_a = _translate_shapes(solids_base, dx_a, dy_a, dz_a)
    solids_b = _translate_shapes(solids_base, dx_b, dy_b, dz_b)
    written_a = _write_solids_step(solids_a, out_step_a, appearance=appearance)
    t_a = time.perf_counter() - t1

    t2 = time.perf_counter()
    written_b = _write_solids_step(solids_b, out_step_b, appearance=appearance)
    t_b = time.perf_counter() - t2

    return {
        "path_a": written_a,
        "path_b": written_b,
        "outers": len(geom.outer_wires),
        "inners": len(geom.inner_wires),
        "mark_segs": len(geom.mark_segs),
        "solids": len(solids_base),
        "bytes_a": written_a.stat().st_size,
        "bytes_b": written_b.stat().st_size,
        "material": mat,
        "thk_mm": float(thk_mm),
        "mark_mode": mode,
        "origen_a": str(origen_a),
        "origen_b": str(origen_b),
        "anchor_bbox": anchor_bb,
        "sec_build": round(t_build, 3),
        "sec_write_a": round(t_a, 3),
        "sec_write_b": round(t_b, 3),
        "sec_total": round(time.perf_counter() - t0, 3),
    }


def thickness_mm_from_dxf_name(name: str, default_mm: float = 6.35) -> float:
    """Parsea NESTING_0.25_... → 0.25 in en mm."""
    import re

    m = re.search(r"NESTING[_\s-]*([0-9]*\.?[0-9]+)", str(name), re.I)
    if not m:
        return float(default_mm)
    try:
        return float(m.group(1)) * MM_PER_IN
    except Exception:
        return float(default_mm)
