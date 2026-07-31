"""Paridad nest normal vs export: misma inferencia de rot/shift desde DXF + polígonos."""
from __future__ import annotations

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ezdxf
from shapely import affinity
from shapely.geometry import Polygon

from modules.nesting_engine.geometry_parser import poligonos_desde_shapely, recuperar_geometria_robusta
from modules.nesting_engine.manager import (
    _inferir_transformacion_desde_resultado,
    _origen_rotacion_pieza,
)
from modules.nesting_engine.display_geometry import completar_transform_export_pieza


def _make_rect_dxf(path: str, w_in: float, h_in: float) -> None:
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    pts = [(0, 0), (w_in, 0), (w_in, h_in), (0, h_in), (0, 0)]
    msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "CUT_OUTER"})
    doc.saveas(path)


def _simulate_post_nest_match(p_orig: dict, placed_poly) -> dict:
    """Réplica del bloque MATCH en manager.ejecutar_nesting_visual."""
    p_final = {
        "nombre": p_orig["nombre"],
        "poligonos": poligonos_desde_shapely(placed_poly),
        "marcas": [],
    }
    transform = _inferir_transformacion_desde_resultado(p_orig, p_final)
    rot_origin = _origen_rotacion_pieza(p_orig.get("poly_exact") or p_orig.get("poly"))
    p_final["ruta"] = p_orig["ruta"]
    p_final["orig_minx"] = p_orig.get("orig_minx", 0.0)
    p_final["orig_miny"] = p_orig.get("orig_miny", 0.0)
    p_final["rot_origin_cx"] = rot_origin[0]
    p_final["rot_origin_cy"] = rot_origin[1]
    if transform:
        p_final["rot_deg"] = transform["rot_deg"]
        p_final["shift_x"] = transform["shift_x"]
        p_final["shift_y"] = transform["shift_y"]
    else:
        p_final["rot_deg"] = 0.0
        p_final["shift_x"] = 0.0
        p_final["shift_y"] = 0.0
    return p_final


def _bbox_from_polys(pols) -> tuple[float, float, float, float]:
    poly = Polygon(pols[0])
    minx, miny, maxx, maxy = poly.bounds
    return minx, miny, maxx, maxy


def _placed_bbox_from_meta(pieza: dict, p_orig: dict) -> tuple[float, float, float, float]:
    poly_local = p_orig["poly_exact"]
    rot_origin = (pieza["rot_origin_cx"], pieza["rot_origin_cy"])
    rotated = affinity.rotate(
        poly_local,
        float(pieza["rot_deg"]),
        origin=rot_origin,
    )
    placed = affinity.translate(
        rotated,
        float(pieza["shift_x"]),
        float(pieza["shift_y"]),
    )
    minx, miny, maxx, maxy = placed.bounds
    return minx, miny, maxx, maxy


def _run_case(label: str, w_mm: float, h_mm: float, rot_deg: float, tx: float, ty: float) -> None:
    w_in = w_mm / 25.4
    h_in = h_mm / 25.4
    with tempfile.TemporaryDirectory() as tmp:
        ruta = os.path.join(tmp, f"{label}.dxf")
        _make_rect_dxf(ruta, w_in, h_in)

        poly, _ = recuperar_geometria_robusta(ruta)
        assert poly is not None and not poly.is_empty
        minx, miny, _, _ = poly.bounds
        poly_exact = affinity.translate(poly, -minx, -miny)

        p_orig = {
            "nombre": label,
            "ruta": ruta,
            "poly": poly_exact,
            "poly_exact": poly_exact,
            "orig_minx": minx,
            "orig_miny": miny,
        }

        rot_origin = _origen_rotacion_pieza(poly_exact)
        placed = affinity.rotate(poly_exact, rot_deg, origin=rot_origin)
        placed = affinity.translate(placed, tx, ty)

        nest_piece = _simulate_post_nest_match(p_orig, placed)
        export_piece = {
            "nombre": label,
            "poligonos": list(nest_piece["poligonos"]),
            "ruta": ruta,
        }
        assert completar_transform_export_pieza(export_piece)

        for key in ("rot_deg", "shift_x", "shift_y", "orig_minx", "orig_miny"):
            a = float(nest_piece[key])
            b = float(export_piece[key])
            if abs(a - b) > 0.05:
                raise AssertionError(f"{label}: {key} nest={a} export={b}")

        nest_bb = _bbox_from_polys(nest_piece["poligonos"])
        meta_bb = _placed_bbox_from_meta(nest_piece, p_orig)
        export_bb = _placed_bbox_from_meta(export_piece, p_orig)

        for name, bb in (("nest", nest_bb), ("meta", meta_bb), ("export", export_bb)):
            w = bb[2] - bb[0]
            h = bb[3] - bb[1]
            if abs(w - (max(w_mm, h_mm) if rot_deg in (90, 270) else min(w_mm, h_mm))) > 1.0:
                if rot_deg in (0, 180):
                    exp_w, exp_h = w_mm, h_mm
                else:
                    exp_w, exp_h = h_mm, w_mm
                if abs(w - exp_w) > 1.0 or abs(h - exp_h) > 1.0:
                    raise AssertionError(f"{label} {name}: bbox size {w:.1f}x{h:.1f} unexpected")

        print(f"OK {label}: rot={nest_piece['rot_deg']} shift=({nest_piece['shift_x']:.1f},{nest_piece['shift_y']:.1f})")


def main() -> None:
    _run_case("rect_0", 3416.0, 2376.0, 0, 7.6, 7.6)
    _run_case("rect_90", 801.6, 1419.2, 90, 5056.1, 10.2)
    _run_case("square", 400.0, 400.0, 0, 4251.7, 1795.4)
    print("parity OK — nest normal y export usan la misma inferencia")


if __name__ == "__main__":
    main()
