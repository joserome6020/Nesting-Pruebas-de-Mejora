"""APEX: DXF/poly → OCCT extruir/sanar → anillos 2D limpios para nest.

Solo se usa en el bridge APEX (no cambia LITE/Ultra/Force).
Si OCP falta o falla una pieza, se deja la geometría original.
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
_CAD = _REPO / "CAD (OCCT)"
if _CAD.is_dir() and str(_CAD) not in sys.path:
    sys.path.insert(0, str(_CAD))


def _occt_enabled() -> bool:
    return str(os.environ.get("ARGA_APEX_OCCT", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _piece_poly(piece: dict):
    poly = piece.get("poly") or piece.get("poly_exact")
    if poly is not None and hasattr(poly, "exterior"):
        return poly
    rings = piece.get("poligonos") or []
    if not rings:
        return None
    try:
        from .geometry_parser import reconstruir_poly_seguro

        return reconstruir_poly_seguro(rings)
    except Exception:
        return None


def _ring_xy(coords) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for c in coords:
        if c is None or len(c) < 2:
            continue
        out.append((float(c[0]), float(c[1])))
    if len(out) >= 2 and out[0] == out[-1]:
        out = out[:-1]
    return out


def _wire_from_xy(pts: list[tuple[float, float]], *, z: float = 0.0):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt

    if len(pts) < 3:
        return None
    poly = BRepBuilderAPI_MakePolygon()
    for x, y in pts:
        poly.Add(gp_Pnt(float(x), float(y), float(z)))
    poly.Close()
    if not poly.IsDone():
        return None
    return poly.Wire()


def _wire_to_xy(wire) -> list[tuple[float, float]]:
    from OCP.BRep import BRep_Tool
    from OCP.BRepTools import BRepTools_WireExplorer

    pts: list[tuple[float, float]] = []
    exp = BRepTools_WireExplorer(wire)
    while exp.More():
        v = exp.CurrentVertex()
        p = BRep_Tool.Pnt_s(v)
        pts.append((float(p.X()), float(p.Y())))
        exp.Next()
    if len(pts) >= 3 and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def _fix_face(face):
    try:
        from OCP.ShapeFix import ShapeFix_Face

        fix = ShapeFix_Face(face)
        fix.Perform()
        return fix.Face()
    except Exception:
        return face


def _fix_shape(shape):
    try:
        from OCP.ShapeFix import ShapeFix_Shape

        fix = ShapeFix_Shape(shape)
        fix.Perform()
        return fix.Shape()
    except Exception:
        return shape


def _face_from_poly(poly, *, z: float = 0.0):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace

    outer = _ring_xy(list(poly.exterior.coords))
    outer_w = _wire_from_xy(outer, z=z)
    if outer_w is None:
        return None
    mk = BRepBuilderAPI_MakeFace(outer_w, True)
    if not mk.IsDone():
        return None
    for interior in getattr(poly, "interiors", []) or []:
        hole = _ring_xy(list(interior.coords))
        hw = _wire_from_xy(hole, z=z)
        if hw is None:
            continue
        try:
            mk.Add(hw)
        except Exception:
            pass
    if not mk.IsDone():
        return None
    return _fix_face(mk.Face())


def _extrude_face(face, thk_mm: float):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    prism = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, float(thk_mm)))
    if not prism.IsDone():
        return None
    return _fix_shape(prism.Shape())


def _wires_from_face(face) -> list:
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    wires = []
    exp = TopExp_Explorer(face, TopAbs_WIRE)
    while exp.More():
        wires.append(TopoDS.Wire_s(exp.Current()))
        exp.Next()
    return wires


def _shapely_from_face(face):
    from shapely.geometry import Polygon

    wires = _wires_from_face(face)
    rings = []
    for w in wires:
        xy = _wire_to_xy(w)
        if len(xy) >= 3:
            rings.append(xy)
    if not rings:
        return None
    # Outer = mayor área
    polys = []
    for r in rings:
        try:
            p = Polygon(r)
            if p.is_valid and not p.is_empty and p.area > 1e-6:
                polys.append(p)
        except Exception:
            continue
    if not polys:
        return None
    outer = max(polys, key=lambda g: g.area)
    holes = []
    for p in polys:
        if p is outer:
            continue
        try:
            if outer.contains(p.representative_point()) or outer.contains(p.centroid):
                holes.append(list(p.exterior.coords))
        except Exception:
            continue
    try:
        out = Polygon(list(outer.exterior.coords), holes)
        if not out.is_valid:
            out = out.buffer(0)
        if out.is_empty:
            return None
        if out.geom_type == "MultiPolygon":
            out = max(out.geoms, key=lambda g: g.area)
        return out
    except Exception:
        return None


def _thickness_mm(piece: dict) -> float:
    for key in ("espesor_mm", "thickness_mm", "thk_mm"):
        try:
            v = float(piece.get(key) or 0)
            if v > 0.05:
                return v
        except Exception:
            pass
    # Calibre aproximado (lámina): fallback 3 mm
    return 3.0


def heal_piece_occt(piece: dict) -> tuple[dict | None, str]:
    """Extruir+sanar con OCCT y devolver pieza con poly limpio, o (None, motivo)."""
    poly = _piece_poly(piece)
    if poly is None or getattr(poly, "is_empty", True):
        return None, "no_poly"
    try:
        if not poly.is_valid:
            poly = poly.buffer(0)
        if getattr(poly, "geom_type", "") == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)
        if poly is None or poly.is_empty:
            return None, "empty"
    except Exception:
        return None, "invalid"

    face = _face_from_poly(poly)
    if face is None:
        return None, "face_fail"
    thk = _thickness_mm(piece)
    solid = _extrude_face(face, thk)
    if solid is None:
        return None, "extrude_fail"

    # Releer cara base ya saneada (misma topología 2D que usará el nest).
    healed = _shapely_from_face(face)
    if healed is None or healed.is_empty:
        return None, "project_fail"

    # Si perdimos demasiada área, descartar
    try:
        if float(healed.area) < float(poly.area) * 0.85:
            return None, "area_loss"
    except Exception:
        pass

    out = copy.deepcopy(piece)
    out["poly"] = healed
    out["poly_exact"] = healed
    try:
        from .geometry_parser import poligonos_desde_shapely

        out["poligonos"] = poligonos_desde_shapely(healed)
    except Exception:
        out["poligonos"] = [list(healed.exterior.coords)] + [
            list(h.coords) for h in healed.interiors
        ]
    try:
        out["area"] = float(healed.area)
    except Exception:
        pass
    out["apex_occt"] = True
    out["apex_occt_thk_mm"] = thk
    n_holes = len(list(getattr(healed, "interiors", []) or []))
    out["apex_occt_holes"] = n_holes
    return out, "ok"


def prepare_pieces_for_apex(piezas: list) -> tuple[list, dict[str, Any]]:
    """
    Prepara piezas para APEX vía OCCT.
    Returns: (piezas_nuevas, stats)
    """
    stats: dict[str, Any] = {
        "enabled": True,
        "ok": 0,
        "skip": 0,
        "fail": 0,
        "holes_in": 0,
        "holes_out": 0,
    }
    if not _occt_enabled():
        stats["enabled"] = False
        return list(piezas or []), stats

    try:
        from engine.occt_runtime import ensure_ocp

        ensure_ocp()
    except Exception as exc:
        stats["enabled"] = False
        stats["error"] = f"ocp:{exc}"
        return list(piezas or []), stats

    out: list = []
    for p in piezas or []:
        poly = _piece_poly(p)
        if poly is not None:
            try:
                stats["holes_in"] += len(list(getattr(poly, "interiors", []) or []))
            except Exception:
                pass
        healed, reason = heal_piece_occt(p if isinstance(p, dict) else {})
        if healed is not None:
            stats["ok"] += 1
            stats["holes_out"] += int(healed.get("apex_occt_holes") or 0)
            out.append(healed)
        else:
            if reason in ("no_poly", "empty"):
                stats["skip"] += 1
            else:
                stats["fail"] += 1
            out.append(copy.deepcopy(p) if isinstance(p, dict) else p)
    return out, stats
