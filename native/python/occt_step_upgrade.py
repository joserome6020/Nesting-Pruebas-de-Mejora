"""STEP de producción vía OCCT (OCP) a partir de un pack ArgaNestCore."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_CAD_ROOT = _ROOT / "CAD (OCCT)"


def _ensure_imports():
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    if str(_CAD_ROOT) not in sys.path:
        sys.path.insert(0, str(_CAD_ROOT))


def _wire_from_ring(ring: list):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.gp import gp_Pnt

    if len(ring) < 3:
        raise ValueError("ring too short")
    pts = [(float(p[0]), float(p[1])) for p in ring]
    if abs(pts[0][0] - pts[-1][0]) < 1e-9 and abs(pts[0][1] - pts[-1][1]) < 1e-9:
        pts = pts[:-1]
    if len(pts) < 3:
        raise ValueError("ring degenerates")
    mk = BRepBuilderAPI_MakeWire()
    for i in range(len(pts)):
        a = pts[i]
        b = pts[(i + 1) % len(pts)]
        e = BRepBuilderAPI_MakeEdge(gp_Pnt(a[0], a[1], 0.0), gp_Pnt(b[0], b[1], 0.0)).Edge()
        mk.Add(e)
    if not mk.IsDone():
        raise RuntimeError("wire failed")
    return mk.Wire()


def _solid_from_piece(piece: dict[str, Any], thickness_mm: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    polys = piece.get("poligonos") or []
    if not polys:
        raise ValueError(f"piece {piece.get('nombre')} sin poligonos")
    outer = _wire_from_ring(polys[0])
    face_mk = BRepBuilderAPI_MakeFace(outer)
    for hole in polys[1:]:
        try:
            face_mk.Add(_wire_from_ring(hole))
        except Exception:
            pass
    if not face_mk.IsDone():
        raise RuntimeError(f"face failed for {piece.get('nombre')}")
    prism = BRepPrimAPI_MakePrism(face_mk.Face(), gp_Vec(0, 0, float(thickness_mm)))
    if not prism.IsDone():
        raise RuntimeError(f"extrude failed for {piece.get('nombre')}")
    return prism.Shape()


def export_pack_to_step_occt(
    pack_result: dict[str, Any],
    out_path: str | Path,
    *,
    thickness_mm: float = 6.0,
) -> Path:
    """Escribe STEP real (OCCT) con sólidos extruidos por pieza colocada."""
    _ensure_imports()
    from engine.occt_runtime import ensure_ocp, write_step_shape

    ensure_ocp()
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)

    placed = pack_result.get("placed") or []
    if not placed:
        raise ValueError("pack_result sin piezas colocadas")

    n = 0
    for piece in placed:
        try:
            shape = _solid_from_piece(piece, thickness_mm)
            builder.Add(compound, shape)
            n += 1
        except Exception as ex:
            print(f"[OCCT-STEP] skip {piece.get('nombre')}: {ex}", flush=True)
    if n == 0:
        raise RuntimeError("ningún sólido OCCT generado")

    path = Path(out_path)
    write_step_shape(compound, path)
    return path


def export_request_via_core_then_occt(
    request: dict[str, Any],
    out_path: str | Path,
    *,
    thickness_mm: float | None = None,
) -> Path:
    """Pack con ArgaNestCore y exporta STEP OCCT."""
    _ensure_imports()
    from modules.nesting_engine import arga_nest_core_bridge as bridge

    thick = float(
        thickness_mm if thickness_mm is not None else request.get("thickness_mm", 6.0)
    )
    result = bridge.pack_sheet_json(request)
    if not (result.get("certify") or {}).get("ok", result.get("ok")):
        raise RuntimeError(f"certify fail: {result.get('certify')}")
    return export_pack_to_step_occt(result, out_path, thickness_mm=thick)
