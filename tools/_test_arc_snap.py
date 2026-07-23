import ezdxf

from interface.qt.cad_snap import snap_cota
from interface.qt.dxf_part_geometry import build_snap_context

doc = ezdxf.new()
msp = doc.modelspace()
pts = [(0, 0, 0), (10, 0, 1), (10, 5, 0), (0, 5, 1)]
msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "CUT_INNER"})
entities = list(msp)
ctx = build_snap_context(entities, True)
print("arcos_pick", len(ctx.arcos_pick))
arc_segs = [s for s in ctx.geom_segmentos if len(s) > 4 and s[4] is not None]
print("arc segments", len(arc_segs))
span = 12.0
for x, y in [(0.0, 2.5), (0.3, 1.5), (10.0, 2.5)]:
    sc = snap_cota(x, y, span, ctx)
    print(f"  ({x},{y}) -> {sc['tipo']} r={sc.get('r')}")

# Native ARC entity
doc2 = ezdxf.new()
msp2 = doc2.modelspace()
msp2.add_arc((5, 5), 2, 0, 180, dxfattribs={"layer": "CUT_INNER"})
ctx2 = build_snap_context(list(msp2), True)
sc2 = snap_cota(5.0, 7.0, 10.0, ctx2)
print("native arc snap", sc2["tipo"], sc2.get("r"))
