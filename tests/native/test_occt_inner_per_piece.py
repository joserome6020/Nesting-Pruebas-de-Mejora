"""Candado: CUT_INNER global no debe borrar piezas chicas del nest."""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CAD = ROOT / "CAD (OCCT)"
if str(CAD) not in sys.path:
    sys.path.insert(0, str(CAD))


def _rect_wire(xmin, ymin, xmax, ymax):
    from engine.dxf_to_step import _wire_from_xy

    return _wire_from_xy(
        [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)],
        closed=True,
    )


def test_inners_solo_de_su_pieza():
    from engine.dxf_to_step import (
        DxfNestGeometry,
        build_freecad_like_shapes,
        _apply_inners_to_outer,
        _extrude_wire,
        _shape_volume,
    )
    from engine.occt_runtime import ensure_ocp

    ensure_ocp()
    # Pieza grande 0..100 y pieza chica 40..50 (dentro del bbox de la grande).
    # Agujero grande 10..90 centrado en la grande: si se aplica a la chica, la borra.
    outer_big = _rect_wire(0, 0, 100, 100)
    outer_small = _rect_wire(40, 40, 50, 50)
    hole_big = _rect_wire(10, 10, 90, 90)
    hole_small = _rect_wire(42, 42, 44, 44)

    thk = 5.0
    body_small = _extrude_wire(outer_small, thk)
    v0 = _shape_volume(body_small)
    assert v0 > 1.0

    # Bug antiguo: tool = todos los inners → volumen ~0.
    kept = _apply_inners_to_outer(
        body_small, outer_small, [hole_big, hole_small], thk_mm=thk
    )
    v1 = _shape_volume(kept)
    assert v1 > v0 * 0.5, f"pieza chica destruida: v0={v0} v1={v1}"
    assert v1 < v0, "debería restar solo el agujero propio"

    geom = DxfNestGeometry(
        outer_wires=[outer_big, outer_small],
        inner_wires=[hole_big, hole_small],
        mark_segs=[],
        plate_wires=[],
    )
    _parts, solids, _bb = build_freecad_like_shapes(
        geom, thk_mm=thk, mark_mode="EDGES", apply_placement=False
    )
    assert len(solids) == 2, f"esperaba 2 sólidos, got {len(solids)}"
    vols = sorted(_shape_volume(s) for s in solids)
    assert vols[0] > 1.0 and vols[1] > 1.0


def test_swo033_h4_keep_all_outers():
    """Caso real: H4 tenía 9 CUT_OUTER y el STEP viejo solo 5 sólidos."""
    dxf = ROOT / "_tmp" / "swo033_step_diag" / "SWO-033_0.375_SWO-033-H4.dxf"
    if not dxf.is_file():
        print("SKIP test_swo033_h4_keep_all_outers (sin DXF local)")
        return
    from engine.dxf_to_step import (
        collect_dxf_nest,
        build_freecad_like_shapes,
        thickness_mm_from_dxf_name,
    )

    g = collect_dxf_nest(dxf)
    assert len(g.outer_wires) == 9
    thk = thickness_mm_from_dxf_name(dxf.name, default_mm=9.525)
    _p, solids, _bb = build_freecad_like_shapes(
        g, thk_mm=thk, mark_mode="EDGES", apply_placement=False
    )
    assert len(solids) == 9, f"H4 debe conservar 9 piezas, got {len(solids)}"


def test_circle_cut_outer_wire():
    """CUT_OUTER como CIRCLE (caso H9) debe generar sólido."""
    from engine.dxf_to_step import (
        DxfNestGeometry,
        build_freecad_like_shapes,
        _shape_volume,
    )
    from engine.occt_runtime import ensure_ocp
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.GC import GC_MakeCircle
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    ensure_ocp()
    circ = GC_MakeCircle(gp_Ax2(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0, 0, 1)), 50.0).Value()
    wire = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(circ).Edge()).Wire()
    geom = DxfNestGeometry(
        outer_wires=[wire],
        inner_wires=[],
        mark_segs=[],
        plate_wires=[],
    )
    _p, solids, _bb = build_freecad_like_shapes(
        geom, thk_mm=12.7, mark_mode="SKIP", apply_placement=False
    )
    assert len(solids) == 1
    assert _shape_volume(solids[0]) > 1.0


if __name__ == "__main__":
    test_inners_solo_de_su_pieza()
    test_swo033_h4_keep_all_outers()
    test_circle_cut_outer_wire()
    print("OK occt_inner_per_piece")
