"""
Candado SWITCH PATCH 1: el barreno con muesca no se espeja al compensar.

Processed Files guarda el CUT_INNER como LWPOLYLINE de 2–3 vértices con un
bulge > 1 (arco mayor ~336°). La conversión bulge→centro usaba
``h = +sqrt(r²-(c/2)²)`` siempre al lado del signo del bulge; en arcos
reflejos el centro va al otro lado. Resultado en planta: el agujero saltaba
debajo de la muesca, la muesca quedaba “arriba” y el MARK “SWITCH PATCH 1”
aparecía dentro del verde.
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _hacer_switch_patch(destino: Path) -> None:
    import ezdxf  # type: ignore

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    # Outer 4x4 con radio 0.5 (como la pieza real).
    r = 0.50
    b = math.tan(math.radians(90.0) / 4.0)
    msp.add_lwpolyline(
        [
            (r, 0.0, 0.0),
            (4.0 - r, 0.0, b),
            (4.0, r, 0.0),
            (4.0, 4.0 - r, b),
            (4.0 - r, 4.0, 0.0),
            (r, 4.0, b),
            (0.0, 4.0 - r, 0.0),
            (0.0, r, b),
        ],
        format="xyb",
        close=True,
        dxfattribs={"layer": "CUT_OUTER"},
    )
    # CUT_INNER idéntico al Processed real: cuerda en y≈1.35, muesca abajo
    # (bulge negativo) + arco mayor arriba (bulge ≈ 9.42) + vértice de cierre.
    msp.add_lwpolyline(
        [
            (1.3603454976158775, 1.3498295454545421, -0.932089),
            (1.6396545023841225, 1.3498295454545421, 9.417315),
            (1.3603454976158775, 1.3498295454545421, 0.0),
        ],
        format="xyb",
        close=True,
        dxfattribs={"layer": "CUT_INNER"},
    )
    # MARK debajo del agujero (como el texto SWITCH PATCH 1).
    msp.add_line((0.5, 0.9), (3.5, 0.9), dxfattribs={"layer": "MARK"})
    doc.saveas(destino)


def _centros_inner(ruta: Path) -> list[tuple[float, float, float]]:
    import ezdxf  # type: ignore

    doc = ezdxf.readfile(ruta)
    out = []
    for e in doc.modelspace():
        if str(e.dxf.layer or "").upper() != "CUT_INNER":
            continue
        if e.dxftype() == "CIRCLE":
            c = e.dxf.center
            out.append((float(c.x), float(c.y), float(e.dxf.radius)))
            continue
        if e.dxftype() == "ARC":
            c = e.dxf.center
            out.append((float(c.x), float(c.y), float(e.dxf.radius)))
            continue
        if e.dxftype() != "LWPOLYLINE":
            continue
        for ve in e.virtual_entities():
            if ve.dxftype() == "ARC":
                c = ve.dxf.center
                out.append((float(c.x), float(c.y), float(ve.dxf.radius)))
    return out


def _inner_bbox(ruta: Path) -> tuple[float, float, float, float, float, float]:
    """(minx, miny, maxx, maxy, cx, cy) del CUT_INNER (arcos nativos o densificado)."""
    import ezdxf  # type: ignore

    doc = ezdxf.readfile(ruta)
    xs: list[float] = []
    ys: list[float] = []
    for e in doc.modelspace():
        if str(e.dxf.layer or "").upper() != "CUT_INNER":
            continue
        if e.dxftype() == "LWPOLYLINE":
            for x, y, *_ in e.get_points("xy"):
                xs.append(float(x))
                ys.append(float(y))
        elif e.dxftype() in {"CIRCLE", "ARC"}:
            c = e.dxf.center
            r = float(e.dxf.radius)
            xs.extend([float(c.x) - r, float(c.x) + r])
            ys.extend([float(c.y) - r, float(c.y) + r])
    assert xs and ys, "CUT_INNER vacío"
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    return minx, miny, maxx, maxy, (minx + maxx) * 0.5, (miny + maxy) * 0.5


def test_switch_patch_barreno_no_se_espeja_al_compensar() -> None:
    from modules.plasma_compensator import (
        compensate_dxf_for_plasma,
        compute_plasma_offset_mm,
    )

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src, dst = root / "patch.dxf", root / "out.dxf"
        _hacer_switch_patch(src)

        antes = _centros_inner(src)
        assert any(abs(c[1] - 2.0) < 1e-3 and abs(c[2] - 0.665) < 1e-3 for c in antes), antes
        assert any(abs(c[1] - 1.34) < 1e-2 and abs(c[2] - 0.14) < 1e-2 for c in antes), antes

        off = compute_plasma_offset_mm(0.1875)
        stats = compensate_dxf_for_plasma(src, dst, offset_mm=off)
        assert int(stats.get("changed") or 0) >= 1, stats

        despues = _centros_inner(dst)
        d_in = off / 25.4
        grandes = [c for c in despues if abs(c[2] - (0.665 - d_in)) < 1e-3]
        muescas = [c for c in despues if abs(c[2] - (0.14 + d_in)) < 1e-3]
        if grandes:
            assert all(abs(c[1] - 2.0) < 0.05 for c in grandes), (
                "el barreno se espejó debajo de la muesca",
                grandes,
                despues,
            )
        if muescas:
            assert all(abs(c[1] - 1.34) < 0.05 for c in muescas), (
                "la muesca se movió",
                muescas,
                despues,
            )
        # Con 0.0625\" Clipper densifica el inner (sin ARC nativo). El bug
        # original era el espejo: el barreno grande saltaba debajo de la muesca.
        _minx, miny, _maxx, maxy, _cx, cy = _inner_bbox(dst)
        assert cy > 1.70, ("el inner se espejó hacia la muesca", cy, miny, maxy)
        assert maxy > 2.35, ("falta el barreno grande arriba", cy, miny, maxy)
        assert miny > 1.05, ("la muesca cayó sobre el MARK", cy, miny, maxy)


def test_bulge_reflejo_coloca_el_centro_al_lado_correcto() -> None:
    """|bulge|>1: el centro no puede calcularse con h=+|sqrt|."""
    import ezdxf  # type: ignore

    from modules.plasma_occt_offset import _polyline_edges, _wire_ring_points
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire

    doc = ezdxf.new()
    ent = doc.modelspace().add_lwpolyline(
        [
            (1.3603454976158775, 1.3498295454545421, -0.932089),
            (1.6396545023841225, 1.3498295454545421, 9.417315),
            (1.3603454976158775, 1.3498295454545421, 0.0),
        ],
        format="xyb",
        close=True,
    )
    edges = _polyline_edges(ent)
    wire = BRepBuilderAPI_MakeWire()
    for e in edges:
        wire.Add(e)
    assert wire.IsDone()
    ring = _wire_ring_points(wire.Wire())
    ys = [p[1] for p in ring]
    assert max(ys) > 2.5, ("arco mayor quedó espejado hacia abajo", min(ys), max(ys))
    assert min(ys) > 1.2


if __name__ == "__main__":
    test_bulge_reflejo_coloca_el_centro_al_lado_correcto()
    test_switch_patch_barreno_no_se_espeja_al_compensar()
    print("OK plasma_switch_patch_muesca")
