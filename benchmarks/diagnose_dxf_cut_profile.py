"""Describe candidatos OUTER para diagnosticar DXF bloqueados por poka-yoke."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import ezdxf
from shapely.geometry import LineString, Polygon

from modules.nesting_engine import geometry_parser as parser


def diagnose(path: Path) -> dict:
    doc = ezdxf.readfile(str(path))
    lines_outer = []
    direct_rings = []
    layer_counts: dict[str, int] = {}
    for entity in doc.modelspace():
        if entity.dxftype() not in {
            "LINE",
            "LWPOLYLINE",
            "POLYLINE",
            "ARC",
            "CIRCLE",
            "ELLIPSE",
            "SPLINE",
        }:
            continue
        layer = str(entity.dxf.layer or "").upper().strip()
        if parser._clasificar_capa(layer) != "outer":
            continue
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
        rings = parser._anillos_cerrados_entidad(entity)
        direct_rings.extend(rings)
        if rings:
            lines_outer.extend(LineString(ring) for ring in rings if len(ring) >= 2)
        else:
            lines_outer.extend(parser.entidad_a_lineas(entity))

    candidates: list[tuple[str, object]] = []
    candidates.extend(("polygonize", poly) for poly in parser._poligonos_cerrados_de_lineas(lines_outer))
    candidates.extend(("endpoint_cycle", poly) for poly in parser._poligonos_por_ciclos_de_extremos(lines_outer))
    candidates.extend(
        ("direct_ring", poly)
        for ring in direct_rings
        if (poly := parser._anillo_a_poligono(ring)) is not None
    )
    line_summary = []
    for index, line in enumerate(lines_outer, start=1):
        coords = list(line.coords)
        if len(coords) < 2:
            continue
        line_summary.append(
            {
                "index": index,
                "start_mm": [round(float(value), 4) for value in coords[0][:2]],
                "end_mm": [round(float(value), 4) for value in coords[-1][:2]],
                "point_count": len(coords),
                "length_mm": round(float(line.length or 0.0), 4),
            }
        )
    tolerance = 0.05

    def key(point):
        return (
            int(round(float(point[0]) / tolerance)),
            int(round(float(point[1]) / tolerance)),
        )

    endpoint_map: dict[tuple[int, int], list[int]] = defaultdict(list)
    segments = [list(line.coords) for line in lines_outer]
    for index, coords in enumerate(segments):
        endpoint_map[key(coords[0])].append(index)
        endpoint_map[key(coords[-1])].append(index)
    used: set[int] = set()
    traced_cycles = []
    for start_index, initial in enumerate(segments):
        if start_index in used:
            continue
        used.add(start_index)
        sequence = [start_index + 1]
        ring = list(initial)
        start_key = key(ring[0])
        current_key = key(ring[-1])
        for _ in range(len(segments) + 1):
            if current_key == start_key:
                break
            next_index = next(
                (item for item in endpoint_map[current_key] if item not in used),
                None,
            )
            if next_index is None:
                break
            coords = segments[next_index]
            if key(coords[-1]) == current_key:
                coords = list(reversed(coords))
            elif key(coords[0]) != current_key:
                break
            used.add(next_index)
            sequence.append(next_index + 1)
            ring.extend(coords[1:])
            current_key = key(ring[-1])
        if current_key == start_key and len(ring) >= 4:
            ring[-1] = ring[0]
            raw = Polygon(ring)
            fixed = raw if raw.is_valid else raw.buffer(0)
            traced_cycles.append(
                {
                    "segment_indices": sequence,
                    "raw_valid": bool(raw.is_valid),
                    "raw_area_mm2": round(float(raw.area or 0.0), 4),
                    "fixed_type": fixed.geom_type,
                    "fixed_area_mm2": round(float(fixed.area or 0.0), 4),
                    "fixed_empty": bool(fixed.is_empty),
                }
            )
        else:
            traced_cycles.append(
                {
                    "segment_indices": sequence,
                    "closed": False,
                    "start_key": list(start_key),
                    "end_key": list(current_key),
                }
            )
    rows = []
    for index, (origin, poly) in enumerate(candidates, start=1):
        bounds = [round(float(item), 4) for item in poly.bounds]
        rows.append(
            {
                "index": index,
                "origin": origin,
                "area_mm2": round(float(poly.area or 0.0), 4),
                "bounds_mm": bounds,
                "valid": bool(poly.is_valid),
                "contains": [
                    other_index + 1
                    for other_index, (_, other) in enumerate(candidates)
                    if other_index != index - 1 and poly.covers(other)
                ],
            }
        )
    return {
        "file": str(path),
        "outer_entity_layers": layer_counts,
        "outer_line_count": len(lines_outer),
        "direct_ring_count": len(direct_rings),
        "outer_lines": line_summary,
        "traced_cycles": traced_cycles,
        "candidates": rows,
    }


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Diagnóstico de perfiles OUTER DXF")
    cli.add_argument("path")
    cli.add_argument("--out", default="")
    args = cli.parse_args(argv)
    report = diagnose(Path(args.path))
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        destination = Path(args.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        print(destination)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
