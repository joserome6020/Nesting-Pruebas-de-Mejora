"""MVP: sólidos tipo placa plana en STEP → DXF con capas Inventor (IV_*)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ProgressCb = Callable[[str, float], None]

# Espesor razonable para chapa (pulgadas) tras conversión de unidades.
_THK_MIN_IN = 0.02
_THK_MAX_IN = 2.5
_MIN_FACE_AREA_IN2 = 0.5


@dataclass
class PlateExport:
    part_name: str
    thickness_in: float
    material: str
    qty: int
    dxf_path: Path
    revisar_sm: bool
    note: str = ""


@dataclass
class PlateExtractReport:
    exports: list[PlateExport] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    solids_total: int = 0
    plates_detected: int = 0


def _ensure_cad_engine():
    import sys

    root = Path(__file__).resolve().parents[2]
    cad = root / "CAD (OCCT)"
    cad_s = str(cad)
    if cad_s not in sys.path:
        sys.path.insert(0, cad_s)
    from engine.occt_runtime import ensure_ocp  # noqa: WPS433
    from engine.step_io import detect_step_to_inch_factor, read_step_shape  # noqa: WPS433

    return ensure_ocp, read_step_shape, detect_step_to_inch_factor


def _fmt_thk(thk: float) -> str:
    t = float(thk)
    if abs(t - round(t)) < 1e-6:
        return f"{int(round(t))}"
    s = f"{t:.4f}".rstrip("0").rstrip(".")
    return s or "0"


def _safe_part_token(name: str, fallback: str) -> str:
    raw = re.sub(r"[^\w\-]+", "_", str(name or "").strip(), flags=re.UNICODE)
    raw = re.sub(r"_+", "_", raw).strip("_")
    return (raw[:80] if raw else fallback)


def _iter_solids(shape: Any):
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    if int(shape.ShapeType()) == int(TopAbs_SOLID):
        yield TopoDS.Solid_s(shape)
        return
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        yield TopoDS.Solid_s(exp.Current())
        exp.Next()


def _face_area(face: Any) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return float(props.Mass())


def _plane_data(face: Any):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_REVERSED

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Plane:
        return None
    pln = surf.Plane()
    ax = pln.Position()
    origin = ax.Location()
    xdir = ax.XDirection()
    ydir = ax.YDirection()
    zdir = ax.Direction()
    u = 0.5 * (surf.FirstUParameter() + surf.LastUParameter())
    v = 0.5 * (surf.FirstVParameter() + surf.LastVParameter())
    sl = BRepLProp_SLProps(surf, u, v, 1, 1e-6)
    normal = (float(zdir.X()), float(zdir.Y()), float(zdir.Z()))
    if sl.IsNormalDefined():
        n = sl.Normal()
        normal = (float(n.X()), float(n.Y()), float(n.Z()))
        if face.Orientation() == TopAbs_REVERSED:
            normal = (-normal[0], -normal[1], -normal[2])
    return {
        "origin": (float(origin.X()), float(origin.Y()), float(origin.Z())),
        "xdir": (float(xdir.X()), float(xdir.Y()), float(xdir.Z())),
        "ydir": (float(ydir.X()), float(ydir.Y()), float(ydir.Z())),
        "normal": normal,
        "plane": pln,
    }


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _wire_xy_polylines(wire: Any, plane_info: dict, *, deflection: float) -> list[list[tuple[float, float]]]:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    origin = plane_info["origin"]
    xdir = plane_info["xdir"]
    ydir = plane_info["ydir"]
    rings: list[list[tuple[float, float]]] = []
    exp = TopExp_Explorer(wire, TopAbs_EDGE)
    while exp.More():
        edge = TopoDS.Edge_s(exp.Current())
        try:
            curve = BRepAdaptor_Curve(edge)
            sampler = GCPnts_QuasiUniformDeflection(curve, float(deflection))
            if not sampler.IsDone() or sampler.NbPoints() < 2:
                exp.Next()
                continue
            pts: list[tuple[float, float]] = []
            for i in range(1, sampler.NbPoints() + 1):
                p = sampler.Value(i)
                v = (float(p.X()) - origin[0], float(p.Y()) - origin[1], float(p.Z()) - origin[2])
                pts.append((_dot(v, xdir), _dot(v, ydir)))
            if len(pts) >= 2:
                rings.append(pts)
        except Exception:
            pass
        exp.Next()
    return rings


def _merge_edge_rings(rings: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    """Une segmentos de arista en un anillo continuo (mejor esfuerzo)."""
    if not rings:
        return []
    if len(rings) == 1:
        return list(rings[0])

    unused = [list(r) for r in rings if len(r) >= 2]
    poly = unused.pop(0)
    tol = 1e-4

    def _near(a, b) -> bool:
        return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol

    guard = 0
    while unused and guard < 10_000:
        guard += 1
        end = poly[-1]
        matched = False
        for i, seg in enumerate(unused):
            if _near(end, seg[0]):
                poly.extend(seg[1:])
                unused.pop(i)
                matched = True
                break
            if _near(end, seg[-1]):
                poly.extend(reversed(seg[:-1]))
                unused.pop(i)
                matched = True
                break
        if not matched:
            # Empezar otro tramo (contorno abierto / fallido)
            poly.extend(unused.pop(0))
    return poly


def _face_outer_inner_xy(face: Any, plane_info: dict, *, deflection: float):
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    outer_wire = BRepTools.OuterWire_s(face)
    outer_pts = _merge_edge_rings(_wire_xy_polylines(outer_wire, plane_info, deflection=deflection))
    inners: list[list[tuple[float, float]]] = []
    exp = TopExp_Explorer(face, TopAbs_WIRE)
    while exp.More():
        wire = TopoDS.Wire_s(exp.Current())
        if not outer_wire.IsNull() and wire.IsSame(outer_wire):
            exp.Next()
            continue
        pts = _merge_edge_rings(_wire_xy_polylines(wire, plane_info, deflection=deflection))
        if len(pts) >= 3:
            inners.append(pts)
        exp.Next()
    return outer_pts, inners


def _classify_plate_solid(solid: Any, *, to_inch: float, deflection_model: float):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    planar: list[tuple[float, Any, dict]] = []
    exp = TopExp_Explorer(solid, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        info = _plane_data(face)
        if info is None:
            exp.Next()
            continue
        area = _face_area(face) * (to_inch ** 2)
        if area >= _MIN_FACE_AREA_IN2:
            planar.append((area, face, info))
        exp.Next()
    if len(planar) < 2:
        return None
    planar.sort(key=lambda t: t[0], reverse=True)
    area0, face0, info0 = planar[0]
    n0 = info0["normal"]
    mate = None
    for area1, face1, info1 in planar[1:]:
        n1 = info1["normal"]
        align = abs(_dot(n0, n1))
        if align < 0.985:
            continue
        # Distancia entre planos ≈ espesor
        o0 = info0["origin"]
        o1 = info1["origin"]
        mid = ((o1[0] - o0[0]), (o1[1] - o0[1]), (o1[2] - o0[2]))
        thk_model = abs(_dot(mid, n0))
        thk_in = thk_model * to_inch
        if _THK_MIN_IN <= thk_in <= _THK_MAX_IN:
            mate = (area1, face1, info1, thk_in)
            break
    if mate is None:
        return None
    _a1, _f1, _i1, thk_in = mate
    # Preferir cara con mayor área como flat pattern
    outer, inners = _face_outer_inner_xy(face0, info0, deflection=deflection_model)
    if len(outer) < 3:
        return None
    # Cerrar anillo
    if abs(outer[0][0] - outer[-1][0]) > 1e-6 or abs(outer[0][1] - outer[-1][1]) > 1e-6:
        outer = list(outer) + [outer[0]]
    # Pasar a pulgadas en 2D
    scale = float(to_inch)
    outer_in = [(x * scale, y * scale) for x, y in outer]
    inners_in = [[(x * scale, y * scale) for x, y in ring] for ring in inners]
    for ring in inners_in:
        if ring and (abs(ring[0][0] - ring[-1][0]) > 1e-6 or abs(ring[0][1] - ring[-1][1]) > 1e-6):
            ring.append(ring[0])
    # Firma para agrupar QTY
    xs = [p[0] for p in outer_in]
    ys = [p[1] for p in outer_in]
    bbox = (round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3))
    sig = (round(thk_in, 4), bbox, len(inners_in), round(area0, 2))
    return {
        "thickness_in": float(thk_in),
        "outer": outer_in,
        "inners": inners_in,
        "signature": sig,
        "area_in2": float(area0),
    }


def _write_iv_dxf(path: Path, outer, inners) -> None:
    import ezdxf

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new("R2010")
    doc.layers.add("IV_OUTER_PROFILE", color=1)
    doc.layers.add("IV_INTERIOR_PROFILES", color=3)
    msp = doc.modelspace()
    if len(outer) >= 2:
        msp.add_lwpolyline(outer, close=True, dxfattribs={"layer": "IV_OUTER_PROFILE"})
    for ring in inners:
        if len(ring) >= 2:
            msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": "IV_INTERIOR_PROFILES"})
    doc.saveas(str(path))


def _snap_thk(thk_in: float) -> tuple[float, bool]:
    """Snap Herinox si hay match cercano; si no, deja geo y marca revisar."""
    try:
        from modules.arga_gauge_snap import snap_calibre_token

        token = snap_calibre_token(_fmt_thk(thk_in), "A 36")
        if token:
            snapped = float(token)
            if abs(snapped - thk_in) <= 0.012:
                return snapped, False
    except Exception:
        pass
    return float(thk_in), True


def extract_plates_from_step(
    step_path: str | Path,
    out_root: str | Path,
    *,
    material: str = "UNKNOWN",
    progress_cb: ProgressCb | None = None,
) -> PlateExtractReport:
    """
    Lee STEP, exporta placas planas detectadas a ``out_root/Cal …/[REVISAR SM]/``.
    """
    report = PlateExtractReport()
    ensure_ocp, read_step_shape, detect_step_to_inch_factor = _ensure_cad_engine()
    ensure_ocp()

    step_path = Path(step_path)
    out_root = Path(out_root)
    if callable(progress_cb):
        progress_cb(f"Leyendo STEP {step_path.name}…", 0.05)

    try:
        to_inch = float(detect_step_to_inch_factor(step_path))
        shape = read_step_shape(step_path)
    except Exception as exc:
        report.errors.append(f"No se pudo leer STEP: {exc}")
        return report

    # Si el factor mm→in no produce placas, reintentar asumiendo modelo ya en pulgadas
    # (OCCT a veces etiqueta MILLI METRE aunque el CAD exportó cotas en in).
    factors_to_try = [to_inch]
    if abs(to_inch - 1.0) > 1e-9:
        factors_to_try.append(1.0)

    groups: dict[tuple, dict] = {}
    solids: list = []
    used_factor = to_inch
    for factor in factors_to_try:
        deflection = 0.02 if factor >= 0.9 else 0.4
        solids = list(_iter_solids(shape))
        report.solids_total = len(solids)
        if not solids:
            report.errors.append("El STEP no contiene sólidos (TopAbs_SOLID).")
            return report

        groups = {}
        report.plates_detected = 0
        report.skipped = []
        for idx, solid in enumerate(solids, start=1):
            if callable(progress_cb):
                progress_cb(
                    f"Analizando sólido {idx}/{len(solids)}…",
                    0.1 + 0.7 * (idx / max(1, len(solids))),
                )
            try:
                plate = _classify_plate_solid(
                    solid, to_inch=factor, deflection_model=deflection
                )
            except Exception as exc:
                report.skipped.append(f"STEP-P{idx:03d}: error {exc}")
                continue
            if plate is None:
                report.skipped.append(f"STEP-P{idx:03d}: no es placa plana detectable")
                continue
            report.plates_detected += 1
            sig = plate["signature"]
            if sig not in groups:
                groups[sig] = {
                    "thickness_in": plate["thickness_in"],
                    "outer": plate["outer"],
                    "inners": plate["inners"],
                    "qty": 1,
                    "first_idx": idx,
                }
            else:
                groups[sig]["qty"] += 1
        if groups:
            used_factor = factor
            break

    if not groups:
        report.errors.append(
            f"sin placas planas (factor_modelo→in={to_inch:.6g}; "
            f"sólidos={report.solids_total})"
        )
        return report

    _ = used_factor  # reservado para telemetría futura
    mat = str(material or "UNKNOWN").strip() or "UNKNOWN"
    for gidx, (_sig, g) in enumerate(sorted(groups.items(), key=lambda kv: kv[1]["first_idx"]), start=1):
        thk_raw = float(g["thickness_in"])
        thk, needs_review = _snap_thk(thk_raw)
        if mat.upper() == "UNKNOWN":
            needs_review = True
        part = f"STEP-P{int(g['first_idx']):03d}"
        cal = _fmt_thk(thk)
        folder_name = f"Cal {cal} {mat}"
        if needs_review:
            folder_name = f"{folder_name} [REVISAR SM]"
        fname = f"{part}, {mat}, QTY {int(g['qty'])}, Cal {cal}.dxf"
        if needs_review and "[REVISAR SM]" not in fname:
            fname = f"{part}, {mat}, QTY {int(g['qty'])}, Cal {cal} [REVISAR SM].dxf"
        dxf_path = out_root / folder_name / fname
        try:
            _write_iv_dxf(dxf_path, g["outer"], g["inners"])
            report.exports.append(
                PlateExport(
                    part_name=part,
                    thickness_in=thk,
                    material=mat,
                    qty=int(g["qty"]),
                    dxf_path=dxf_path,
                    revisar_sm=needs_review,
                    note=f"geo_thk={_fmt_thk(thk_raw)}",
                )
            )
        except Exception as exc:
            report.errors.append(f"{part}: no se pudo escribir DXF ({exc})")
        if callable(progress_cb):
            progress_cb(
                f"DXF {gidx}/{len(groups)}…",
                0.85 + 0.1 * (gidx / max(1, len(groups))),
            )

    if callable(progress_cb):
        progress_cb("Feedstock STEP listo", 1.0)
    return report
