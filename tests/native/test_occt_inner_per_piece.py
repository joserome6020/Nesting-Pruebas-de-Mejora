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
        _assign_inners_to_outers,
        _extrude_wire,
        _shape_volume,
        _point_in_wire,
        _wire_bbox_xy,
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

    # Agujero grande NO debe pertenecer a la chica (bbox no cabe).
    from engine.dxf_to_step import _inner_belongs_to_outer

    assert _inner_belongs_to_outer(hole_big, outer_small) is False
    assert _inner_belongs_to_outer(hole_small, outer_small) is True

    kept = _apply_inners_to_outer(
        body_small, outer_small, [hole_big, hole_small], thk_mm=thk
    )
    v1 = _shape_volume(kept)
    assert v1 > v0 * 0.5, f"pieza chica destruida: v0={v0} v1={v1}"
    assert v1 < v0, "debería restar solo el agujero propio"

    assigned = _assign_inners_to_outers(
        [outer_big, outer_small], [hole_big, hole_small]
    )
    assert len(assigned[0]) == 1  # hole_big → grande
    assert len(assigned[1]) == 1  # hole_small → chica

    geom = DxfNestGeometry(
        outer_wires=[outer_big, outer_small],
        inner_wires=[hole_big, hole_small],
        mark_segs=[],
        plate_wires=[],
    )
    _parts, solids, _bb = build_freecad_like_shapes(
        geom, thk_mm=thk, mark_mode="SKIP", apply_placement=False
    )
    assert len(solids) == 2, f"esperaba 2 sólidos, got {len(solids)}"
    vols = sorted(_shape_volume(s) for s in solids)
    assert vols[0] > 1.0 and vols[1] > 1.0


def test_point_in_circle_outer():
    """Círculos CUT_OUTER: centro debe contar como interior (FClass2d fallaba)."""
    from engine.dxf_to_step import _point_in_wire
    from engine.occt_runtime import ensure_ocp
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.GC import GC_MakeCircle
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    ensure_ocp()
    circ = GC_MakeCircle(gp_Ax2(gp_Pnt(100.0, 200.0, 0.0), gp_Dir(0, 0, 1)), 50.0).Value()
    wire = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(circ).Edge()).Wire()
    assert _point_in_wire(100.0, 200.0, wire) is True
    assert _point_in_wire(100.0, 200.0 + 49.0, wire) is True
    assert _point_in_wire(100.0, 200.0 + 60.0, wire) is False


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
    n_out = len(g.outer_wires)
    assert n_out >= 9, f"H4 outers={n_out}"
    thk = thickness_mm_from_dxf_name(dxf.name, default_mm=9.525)
    _p, solids, _bb = build_freecad_like_shapes(
        g, thk_mm=thk, mark_mode="SKIP", apply_placement=False
    )
    assert len(solids) == n_out, f"H4 debe conservar {n_out} piezas, got {len(solids)}"


def test_swo033_h7_keep_volumes():
    """H7: 15 outers; agujeros no deben comerse piezas (vol simétrico en gemelas)."""
    dxf = (
        ROOT
        / "_tmp"
        / "swo033_step_regen"
        / "ROBOT_LASER_+_MINI_NEST"
        / "SWO-033_0.375_SWO-033-H7.dxf"
    )
    if not dxf.is_file():
        print("SKIP test_swo033_h7_keep_volumes (sin DXF local)")
        return
    from engine.dxf_to_step import (
        collect_dxf_nest,
        build_freecad_like_shapes,
        thickness_mm_from_dxf_name,
        _assign_inners_to_outers,
        _shape_volume,
        _point_in_wire,
        _wire_bbox_xy,
    )

    g = collect_dxf_nest(dxf)
    assert len(g.outer_wires) == 15, f"outers={len(g.outer_wires)}"
    # Círculos: centro interior
    for i in (0, 1):
        ob = _wire_bbox_xy(g.outer_wires[i])
        assert ob is not None
        cx = 0.5 * (ob[0] + ob[2])
        cy = 0.5 * (ob[1] + ob[3])
        assert _point_in_wire(cx, cy, g.outer_wires[i]), f"outer[{i}] centro fuera"

    assigned = _assign_inners_to_outers(g.outer_wires, g.inner_wires)
    assert sum(len(a) for a in assigned) == len(g.inner_wires)

    thk = thickness_mm_from_dxf_name(dxf.name, default_mm=9.525)
    _p, solids, _bb = build_freecad_like_shapes(
        g, thk_mm=thk, mark_mode="SKIP", apply_placement=False
    )
    assert len(solids) == 15, f"H7 debe conservar 15 piezas, got {len(solids)}"
    # Gemelas circulares [0]/[1] y rectángulos [8]/[9] con volúmenes casi iguales
    v0 = _shape_volume(solids[0])
    v1 = _shape_volume(solids[1])
    assert abs(v0 - v1) / max(v0, v1) < 0.02, f"circulos asimétricos {v0} vs {v1}"
    v8 = _shape_volume(solids[8])
    v9 = _shape_volume(solids[9])
    assert abs(v8 - v9) / max(v8, v9) < 0.05, f"piezas [8]/[9] comidas: {v8} vs {v9}"
    assert v8 > 1_000_000 and v9 > 1_000_000


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


def test_ring_large_inner_hole_not_blocked_by_volume_guard():
    """Anillo: hueco central grande debe restar material (no disco sólido)."""
    from engine.dxf_to_step import (
        DxfNestGeometry,
        build_freecad_like_shapes,
        _apply_inners_to_outer,
        _extrude_wire,
        _shape_volume,
    )
    from engine.occt_runtime import ensure_ocp
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.GC import GC_MakeCircle
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    ensure_ocp()
    ax = gp_Ax2(gp_Pnt(360.0, 576.0, 0.0), gp_Dir(0, 0, 1))
    outer = BRepBuilderAPI_MakeWire(
        BRepBuilderAPI_MakeEdge(GC_MakeCircle(ax, 355.6).Value()).Edge()
    ).Wire()
    inner = BRepBuilderAPI_MakeWire(
        BRepBuilderAPI_MakeEdge(GC_MakeCircle(ax, 266.7).Value()).Edge()
    ).Wire()
    thk = 12.7
    body = _extrude_wire(outer, thk)
    v_full = _shape_volume(body)
    cut = _apply_inners_to_outer(body, outer, [inner], thk_mm=thk, prefiltered=True)
    v_cut = _shape_volume(cut)
    assert v_cut < v_full * 0.55, f"anillo sigue casi lleno: {v_cut}/{v_full}"
    assert v_cut > v_full * 0.30, f"anillo sobre-cortado: {v_cut}/{v_full}"

    geom = DxfNestGeometry(
        outer_wires=[outer],
        inner_wires=[inner],
        mark_segs=[],
        plate_wires=[],
    )
    _p, solids, _bb = build_freecad_like_shapes(
        geom, thk_mm=thk, mark_mode="SKIP", apply_placement=False
    )
    assert len(solids) == 1
    assert _shape_volume(solids[0]) < v_full * 0.55


if __name__ == "__main__":
    test_inners_solo_de_su_pieza()
    test_point_in_circle_outer()
    test_swo033_h4_keep_all_outers()
    test_swo033_h7_keep_volumes()
    test_circle_cut_outer_wire()
    test_ring_large_inner_hole_not_blocked_by_volume_guard()
    print("OK occt_inner_per_piece")
