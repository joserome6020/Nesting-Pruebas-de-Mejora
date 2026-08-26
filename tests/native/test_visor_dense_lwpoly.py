"""Candado 2026-08-26 — visor no bloquea con LWPOLY densas (SPLINE procesadas)."""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _crear_dxf_denso(ruta: Path, n_pts: int = 5000) -> None:
    import ezdxf  # type: ignore

    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    doc.layers.new("CUT_OUTER", dxfattribs={"color": 1})
    pts = []
    for i in range(n_pts):
        t = 2.0 * 3.141592653589793 * i / n_pts
        pts.append((10.0 + 8.0 * __import__("math").cos(t), 5.0 + 3.0 * __import__("math").sin(t)))
    msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "CUT_OUTER"})
    doc.saveas(str(ruta))


def test_load_dxf_part_dense_lwpoly_rapido():
    from interface.qt.dxf_part_loader import load_dxf_part

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "denso.dxf"
        _crear_dxf_denso(p, n_pts=6000)
        t0 = time.perf_counter()
        model = load_dxf_part(str(p))
        elapsed = time.perf_counter() - t0
        assert model is not None
        assert model.use_shape_render is True
        assert len(model.shapes_cerrados) >= 1
        assert elapsed < 8.0, f"load_dxf_part demasiado lento: {elapsed:.2f}s"


if __name__ == "__main__":
    test_load_dxf_part_dense_lwpoly_rapido()
    print("OK visor dense lwpoly")
