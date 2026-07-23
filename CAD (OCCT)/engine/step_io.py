"""Lectura STEP + mallado triangular + aristas libres (marcaje FreeCAD)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .occt_runtime import ensure_ocp


@dataclass(frozen=True)
class TriangleMesh:
    """Malla simple: vertices Nx3, triangles Mx3 (índices 0-based)."""

    vertices: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int]]

    @property
    def n_verts(self) -> int:
        return len(self.vertices)

    @property
    def n_tris(self) -> int:
        return len(self.triangles)


@dataclass
class MeasurableEdge:
    """Arista B-Rep lista para medición (unidades del modelo = pulgadas)."""

    id: int
    kind: str  # line | circle | arc | other
    length_in: float
    radius_in: float | None
    is_full_circle: bool
    polyline: list[tuple[float, float, float]]
    center: tuple[float, float, float] | None = None
    p0: tuple[float, float, float] | None = None
    p1: tuple[float, float, float] | None = None
    edge: Any = field(default=None, repr=False, compare=False)


@dataclass
class MeasurableFace:
    """Cara B-Rep medible (plano / cilindro / otra) con malla de resaltado."""

    id: int
    kind: str  # plane | cylinder | other
    area: float
    center: tuple[float, float, float]
    normal: tuple[float, float, float] | None
    outline: list[tuple[float, float, float]]
    mesh_verts: list[tuple[float, float, float]] = field(default_factory=list)
    mesh_tris: list[tuple[int, int, int]] = field(default_factory=list)
    bbox: tuple[float, float, float, float, float, float] | None = None
    radius_in: float | None = None
    face: Any = field(default=None, repr=False, compare=False)


@dataclass
class StepDisplayData:
    """
    Datos para el visor:
    - mesh: caras B-Rep malladas (sólidos / ranuras ENGRAVE)
    - polylines: aristas libres (marcaje FreeCAD YELLOW_MARKS = curvas, no caras)
    - measure_edges: aristas B-Rep para modo medición (tecla M)
    - measure_faces: caras B-Rep para modo medición
    """

    mesh: TriangleMesh
    polylines: list[list[tuple[float, float, float]]] = field(default_factory=list)
    measure_edges: list[MeasurableEdge] = field(default_factory=list)
    measure_faces: list[MeasurableFace] = field(default_factory=list)

    @property
    def n_tris(self) -> int:
        return self.mesh.n_tris

    @property
    def n_verts(self) -> int:
        return self.mesh.n_verts

    @property
    def n_mark_segs(self) -> int:
        return len(self.polylines)

    @property
    def n_measure_edges(self) -> int:
        return len(self.measure_edges)

    @property
    def n_measure_faces(self) -> int:
        return len(self.measure_faces)


def read_step_shape(path: str | Path) -> Any:
    """Lee un .step/.stp y devuelve el TopoDS_Shape compuesto."""
    ensure_ocp()
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"STEP no encontrado: {p}")

    reader = STEPControl_Reader()
    status = reader.ReadFile(str(p))
    if status != IFSelect_RetDone:
        raise RuntimeError(f"OCCT no pudo leer STEP (status={int(status)}): {p}")
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape is None or shape.IsNull():
        raise RuntimeError(f"STEP vacío o sin forma: {p}")
    return shape


def tessellate_shape(shape: Any, *, deflection: float = 0.12) -> TriangleMesh:
    """Mallado BRep → triángulos (deflection en unidades del modelo)."""
    ensure_ocp()
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    # deflection relativa=False: valor absoluto (pulgadas/mm del modelo).
    # 0.12 captura ranuras ENGRAVE (~0.02) mejor que 0.35.
    mesher = BRepMesh_IncrementalMesh(shape, float(deflection), False, 0.5, True)
    mesher.Perform()

    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []

    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        loc = TopLoc_Location()
        poly = BRep_Tool.Triangulation_s(face, loc)
        if poly is None:
            exp.Next()
            continue

        trsf = loc.Transformation()
        offset = len(vertices)
        n_nodes = int(poly.NbNodes())
        for i in range(1, n_nodes + 1):
            pnt = poly.Node(i)
            pnt.Transform(trsf)
            vertices.append((float(pnt.X()), float(pnt.Y()), float(pnt.Z())))

        reversed_face = face.Orientation() == TopAbs_REVERSED
        for i in range(1, int(poly.NbTriangles()) + 1):
            tri = poly.Triangle(i)
            n1, n2, n3 = tri.Get()
            a, b, c = offset + n1 - 1, offset + n2 - 1, offset + n3 - 1
            if reversed_face:
                triangles.append((a, c, b))
            else:
                triangles.append((a, b, c))
        exp.Next()

    return TriangleMesh(vertices=vertices, triangles=triangles)


def _edge_to_polyline(edge: Any, *, deflection: float) -> list[tuple[float, float, float]] | None:
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection

    if BRep_Tool.Degenerated_s(edge):
        return None
    try:
        curve = BRepAdaptor_Curve(edge)
        sampler = GCPnts_QuasiUniformDeflection(curve, float(deflection))
        if not sampler.IsDone() or sampler.NbPoints() < 2:
            return None
        pts: list[tuple[float, float, float]] = []
        for i in range(1, sampler.NbPoints() + 1):
            p = sampler.Value(i)
            pts.append((float(p.X()), float(p.Y()), float(p.Z())))
        return pts if len(pts) >= 2 else None
    except Exception:
        return None


def extract_free_edge_polylines(
    shape: Any,
    *,
    deflection: float = 0.08,
) -> list[list[tuple[float, float, float]]]:
    """
    Aristas libres (no pertenecen a ninguna cara).

    En STEPs FreeCAD ARGA el marcaje clásico es un compound de edges
    (YELLOW_MARKS) con ExportFreeEdges — no son caras.
    """
    ensure_ocp()
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape, TopTools_MapOfShape
    from OCP.TopoDS import TopoDS

    edge_face_map = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, edge_face_map)

    polylines: list[list[tuple[float, float, float]]] = []
    seen = TopTools_MapOfShape()
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    while exp.More():
        edge = TopoDS.Edge_s(exp.Current())
        if seen.Contains(edge):
            exp.Next()
            continue
        seen.Add(edge)

        faces = edge_face_map.FindFromKey(edge) if edge_face_map.Contains(edge) else None
        n_faces = 0 if faces is None else int(faces.Size())
        if n_faces > 0:
            exp.Next()
            continue

        pts = _edge_to_polyline(edge, deflection=deflection)
        if pts:
            polylines.append(pts)
        exp.Next()

    return polylines


def extract_engrave_mark_polylines(
    shape: Any,
    *,
    deflection: float = 0.06,
) -> list[list[tuple[float, float, float]]]:
    """
    Marcaje ENGRAVE (ranuras boolean): caras casi horizontales, cerca del Zmax,
    de área pequeña respecto a la cara superior principal.

    El mallado grueso las pierde visualmente; aquí se dibujan sus aristas.
    """
    ensure_ocp()
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.Bnd import Bnd_Box
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopTools import TopTools_MapOfShape
    from OCP.TopoDS import TopoDS

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    if box.IsVoid():
        return []
    _x0, _y0, zmin, _x1, _y1, zmax = box.Get()
    z_span = max(1e-6, zmax - zmin)
    top_band = zmax - 0.25 * z_span

    horiz: list[tuple[float, Any]] = []  # (area, face)
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        fb = Bnd_Box()
        BRepBndLib.Add_s(face, fb)
        if fb.IsVoid():
            exp.Next()
            continue
        _a, _b, _z0, _c, _d, fz1 = fb.Get()
        if fz1 < top_band:
            exp.Next()
            continue

        try:
            surf = BRepAdaptor_Surface(face)
            u = 0.5 * (surf.FirstUParameter() + surf.LastUParameter())
            v = 0.5 * (surf.FirstVParameter() + surf.LastVParameter())
            props_n = BRepLProp_SLProps(surf, u, v, 1, 1e-6)
            if not props_n.IsNormalDefined():
                exp.Next()
                continue
            nz = abs(float(props_n.Normal().Z()))
            if face.Orientation() == TopAbs_REVERSED:
                pass  # abs already
            if nz < 0.85:
                exp.Next()
                continue
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            area = float(props.Mass())
            if area > 0.0:
                horiz.append((area, face))
        except Exception:
            pass
        exp.Next()

    if not horiz:
        return []

    max_area = max(a for a, _ in horiz)
    # Piso de ranura MARK: mucho más chico que la cara superior de la chapa
    area_cap = max(0.002, 0.015 * max_area)
    mark_faces = [f for a, f in horiz if 1e-10 < a <= area_cap]
    if not mark_faces:
        return []

    polylines: list[list[tuple[float, float, float]]] = []
    seen = TopTools_MapOfShape()
    for face in mark_faces:
        eexp = TopExp_Explorer(face, TopAbs_EDGE)
        while eexp.More():
            edge = TopoDS.Edge_s(eexp.Current())
            if seen.Contains(edge):
                eexp.Next()
                continue
            seen.Add(edge)
            pts = _edge_to_polyline(edge, deflection=deflection)
            if pts:
                polylines.append(pts)
            eexp.Next()
    return polylines


def extract_mark_polylines(
    shape: Any,
    *,
    deflection: float = 0.06,
) -> list[list[tuple[float, float, float]]]:
    """Aristas libres FreeCAD, o si no hay, bordes de ranuras ENGRAVE."""
    free = extract_free_edge_polylines(shape, deflection=deflection)
    if free:
        return free
    return extract_engrave_mark_polylines(shape, deflection=deflection)


def extract_measurable_edges(
    shape: Any,
    *,
    deflection: float = 0.04,
    min_length: float = 1e-4,
) -> list[MeasurableEdge]:
    """
    Extrae aristas B-Rep medibles (línea / círculo / arco / otras).
    Longitudes en unidades del archivo STEP (suele ser mm en export FreeCAD).
    """
    ensure_ocp()
    import math

    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_AbscissaPoint
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopTools import TopTools_MapOfShape
    from OCP.TopoDS import TopoDS

    out: list[MeasurableEdge] = []
    seen = TopTools_MapOfShape()
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    while exp.More():
        edge = TopoDS.Edge_s(exp.Current())
        if seen.Contains(edge):
            exp.Next()
            continue
        seen.Add(edge)
        if BRep_Tool.Degenerated_s(edge):
            exp.Next()
            continue
        try:
            curve = BRepAdaptor_Curve(edge)
            length = float(GCPnts_AbscissaPoint.Length_s(curve))
        except Exception:
            exp.Next()
            continue
        if length < min_length:
            exp.Next()
            continue

        pts = _edge_to_polyline(edge, deflection=deflection)
        if not pts or len(pts) < 2:
            exp.Next()
            continue

        kind = "other"
        radius_in: float | None = None
        is_full = False
        center = None
        p0 = pts[0]
        p1 = pts[-1]
        try:
            ctype = curve.GetType()
            if ctype == GeomAbs_Line:
                kind = "line"
            elif ctype == GeomAbs_Circle:
                circ = curve.Circle()
                radius_in = float(circ.Radius())
                loc = circ.Location()
                center = (float(loc.X()), float(loc.Y()), float(loc.Z()))
                first = float(curve.FirstParameter())
                last = float(curve.LastParameter())
                span = abs(last - first)
                # Círculo completo ≈ 2π o arista cerrada
                is_full = bool(BRep_Tool.IsClosed_s(edge)) or span >= (2.0 * math.pi - 1e-3)
                kind = "circle" if is_full else "arc"
        except Exception:
            pass

        out.append(
            MeasurableEdge(
                id=len(out),
                kind=kind,
                length_in=length,
                radius_in=radius_in,
                is_full_circle=is_full,
                polyline=pts,
                center=center,
                p0=p0,
                p1=p1,
                edge=edge,
            )
        )
        exp.Next()
    return out


def extract_measurable_faces(
    shape: Any,
    *,
    deflection: float = 0.08,
    min_area: float = 1e-4,
) -> list[MeasurableFace]:
    """
    Extrae caras B-Rep medibles (plano / cilindro / otras) con malla de resaltado.
    Áreas en unidades del archivo STEP.
    """
    ensure_ocp()
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.Bnd import Bnd_Box
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopTools import TopTools_MapOfShape
    from OCP.TopoDS import TopoDS

    # Asegurar triangulación para resaltado
    try:
        from OCP.BRepMesh import BRepMesh_IncrementalMesh

        mesher = BRepMesh_IncrementalMesh(shape, float(deflection), False, 0.5, True)
        mesher.Perform()
    except Exception:
        pass

    out: list[MeasurableFace] = []
    seen = TopTools_MapOfShape()
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        if seen.Contains(face):
            exp.Next()
            continue
        seen.Add(face)

        try:
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            area = float(props.Mass())
        except Exception:
            exp.Next()
            continue
        if area < float(min_area):
            exp.Next()
            continue

        try:
            com = props.CentreOfMass()
            center = (float(com.X()), float(com.Y()), float(com.Z()))
        except Exception:
            center = (0.0, 0.0, 0.0)

        kind = "other"
        normal = None
        radius_in = None
        try:
            surf = BRepAdaptor_Surface(face)
            st = surf.GetType()
            if st == GeomAbs_Plane:
                kind = "plane"
                u = 0.5 * (surf.FirstUParameter() + surf.LastUParameter())
                v = 0.5 * (surf.FirstVParameter() + surf.LastVParameter())
                sl = BRepLProp_SLProps(surf, u, v, 1, 1e-6)
                if sl.IsNormalDefined():
                    n = sl.Normal()
                    normal = (float(n.X()), float(n.Y()), float(n.Z()))
                    if face.Orientation() == TopAbs_REVERSED:
                        normal = (-normal[0], -normal[1], -normal[2])
            elif st == GeomAbs_Cylinder:
                kind = "cylinder"
                cyl = surf.Cylinder()
                radius_in = float(cyl.Radius())
                ax = cyl.Axis().Direction()
                normal = (float(ax.X()), float(ax.Y()), float(ax.Z()))  # eje
        except Exception:
            pass

        # Contorno (wire exterior aproximado: todas las aristas)
        outline: list[tuple[float, float, float]] = []
        try:
            eexp = TopExp_Explorer(face, TopAbs_EDGE)
            # Usar la arista más larga como contorno visible simple
            best_pl: list[tuple[float, float, float]] = []
            while eexp.More():
                edge = TopoDS.Edge_s(eexp.Current())
                pl = _edge_to_polyline(edge, deflection=deflection)
                if pl and len(pl) > len(best_pl):
                    best_pl = pl
                eexp.Next()
            outline = best_pl
        except Exception:
            outline = []

        # Malla de la cara para highlight
        mesh_verts: list[tuple[float, float, float]] = []
        mesh_tris: list[tuple[int, int, int]] = []
        try:
            loc = TopLoc_Location()
            poly = BRep_Tool.Triangulation_s(face, loc)
            if poly is not None:
                trsf = loc.Transformation()
                n_nodes = int(poly.NbNodes())
                for i in range(1, n_nodes + 1):
                    pnt = poly.Node(i)
                    pnt.Transform(trsf)
                    mesh_verts.append((float(pnt.X()), float(pnt.Y()), float(pnt.Z())))
                reversed_face = face.Orientation() == TopAbs_REVERSED
                for i in range(1, int(poly.NbTriangles()) + 1):
                    tri = poly.Triangle(i)
                    n1, n2, n3 = tri.Get()
                    a, b, c = n1 - 1, n2 - 1, n3 - 1
                    if reversed_face:
                        mesh_tris.append((a, c, b))
                    else:
                        mesh_tris.append((a, b, c))
        except Exception:
            pass

        bbox = None
        try:
            box = Bnd_Box()
            BRepBndLib.Add_s(face, box)
            if not box.IsVoid():
                bbox = tuple(float(v) for v in box.Get())
        except Exception:
            bbox = None

        out.append(
            MeasurableFace(
                id=len(out),
                kind=kind,
                area=area,
                center=center,
                normal=normal,
                outline=outline,
                mesh_verts=mesh_verts,
                mesh_tris=mesh_tris,
                bbox=bbox,
                radius_in=radius_in,
                face=face,
            )
        )
        exp.Next()
    return out


def shape_min_distance(
    shape_a: Any,
    shape_b: Any,
) -> tuple[float, tuple[float, float, float], tuple[float, float, float]] | None:
    """Distancia mínima entre dos shapes B-Rep + puntos de cota."""
    ensure_ocp()
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    if shape_a is None or shape_b is None:
        return None
    try:
        dss = BRepExtrema_DistShapeShape(shape_a, shape_b)
        dss.Perform()
        if not dss.IsDone() or int(dss.NbSolution()) < 1:
            return None
        dist = float(dss.Value())
        p1 = dss.PointOnShape1(1)
        p2 = dss.PointOnShape2(1)
        return (
            dist,
            (float(p1.X()), float(p1.Y()), float(p1.Z())),
            (float(p2.X()), float(p2.Y()), float(p2.Z())),
        )
    except Exception:
        return None


def point_to_shape_closest(
    point: tuple[float, float, float],
    shape: Any,
) -> tuple[float, tuple[float, float, float]] | None:
    """Distancia de un punto a un shape + pie más cercano."""
    ensure_ocp()
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.gp import gp_Pnt

    try:
        vtx = BRepBuilderAPI_MakeVertex(
            gp_Pnt(float(point[0]), float(point[1]), float(point[2]))
        ).Vertex()
        info = shape_min_distance(vtx, shape)
        if info is None:
            return None
        return float(info[0]), info[2]
    except Exception:
        return None


def edge_min_distance(
    edge_a: Any,
    edge_b: Any,
) -> tuple[float, tuple[float, float, float], tuple[float, float, float]] | None:
    """Distancia mínima entre dos aristas B-Rep + puntos de cota."""
    return shape_min_distance(edge_a, edge_b)


def load_step_display(
    path: str | Path,
    *,
    deflection: float = 0.10,
    edge_deflection: float = 0.06,
    include_measure: bool = False,
) -> StepDisplayData:
    """
    STEP → malla + marcaje.
    Las aristas/caras medibles (tecla M) se cargan bajo demanda: include_measure=True
    o vía load_measure_data_for_path (evita congelar el visor).
    """
    shape = read_step_shape(path)
    mesh = tessellate_shape(shape, deflection=deflection)
    marks = extract_mark_polylines(shape, deflection=edge_deflection)
    measure_edges: list[MeasurableEdge] = []
    measure_faces: list[MeasurableFace] = []
    if include_measure:
        measure_edges = extract_measurable_edges(
            shape, deflection=min(0.04, edge_deflection)
        )
        measure_faces = extract_measurable_faces(
            shape, deflection=min(0.08, edge_deflection)
        )
    return StepDisplayData(
        mesh=mesh,
        polylines=marks,
        measure_edges=measure_edges,
        measure_faces=measure_faces,
    )


def detect_step_to_inch_factor(path: str | Path) -> float:
    """
    Factor para convertir longitudes del STEP a pulgadas.
    FreeCAD ARGA exporta STEP en mm (SI_UNIT MILLI METRE) aunque el nesting sea en in.
    """
    p = Path(path)
    try:
        # Unidades suelen estar al final del archivo
        size = p.stat().st_size
        with p.open("rb") as fh:
            if size > 400_000:
                fh.seek(max(0, size - 350_000))
            text = fh.read().decode("utf-8", errors="ignore").upper()
    except Exception:
        return 1.0 / 25.4  # default seguro: mm→in (export FreeCAD típico)

    if "SI_UNIT(.MILLI.,.METRE.)" in text or "SI_UNIT(.MILLI.,.METRE.)" in text.replace(" ", ""):
        return 1.0 / 25.4
    if "SI_UNIT($,.METRE.)" in text or "SI_UNIT( $, .METRE. )" in text:
        return 1.0 / 0.0254
    if "INCH" in text and "CONVERSION_BASED_UNIT" in text:
        return 1.0
    # Heurística: si no hay INCH y aparece MILLI, mm
    if "MILLI" in text and "METRE" in text:
        return 1.0 / 25.4
    return 1.0


def load_measure_data_for_path(
    path: str | Path,
    *,
    deflection: float = 0.04,
) -> tuple[list[MeasurableEdge], list[MeasurableFace], float]:
    """Carga aristas + caras medibles + factor modelo→pulgadas."""
    to_inch = detect_step_to_inch_factor(path)
    shape = read_step_shape(path)
    defl = float(deflection)
    min_area = 1e-4
    if to_inch < 0.1:  # modelo en mm
        defl = max(defl, 0.5)  # ~0.5 mm
        min_area = 0.05  # mm² — ignora micro-caras de grabado
    edges = extract_measurable_edges(shape, deflection=defl)
    faces = extract_measurable_faces(
        shape, deflection=max(defl, 0.08), min_area=min_area
    )
    return edges, faces, to_inch


def load_measure_edges_for_path(
    path: str | Path,
    *,
    deflection: float = 0.04,
) -> tuple[list[MeasurableEdge], float]:
    """Compat: solo aristas (+ factor). Preferir load_measure_data_for_path."""
    edges, _faces, to_inch = load_measure_data_for_path(path, deflection=deflection)
    return edges, to_inch


def load_step_mesh(path: str | Path, *, deflection: float = 0.12) -> TriangleMesh:
    """Atajo: STEP → malla triangular (sin aristas libres)."""
    return tessellate_shape(read_step_shape(path), deflection=deflection)
