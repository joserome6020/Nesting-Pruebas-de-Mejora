"""Sanity: nest AABB != DXF AABB must hard-fail export matrix resolve."""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

import ezdxf
from ezdxf.math import Matrix44

from modules.nest_exporter import (
    DxfExportValidationError,
    _build_placement_matrix,
    _resolve_placement_matrix,
)


def _make_rect_dxf(path: Path, w_in: float, h_in: float) -> None:
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (w_in, 0), (w_in, h_in), (0, h_in)],
        close=True,
        dxfattribs={"layer": "CUT_OUTER"},
    )
    doc.saveas(str(path))


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        dxf = Path(td) / "part.dxf"
        # Source: 7.55 x 6.25 in → ~191.8 x 158.8 mm
        _make_rect_dxf(dxf, 7.55, 6.25)

        part_doc = ezdxf.readfile(str(dxf))
        # Nest claims wrong footprint (BKT-223 style): 237.3 x 117.4 mm
        nest_outer = [
            (15.2, 1077.2),
            (252.6, 1077.2),
            (252.6, 1194.6),
            (15.2, 1194.6),
        ]
        p = {
            "part_name": "GENE-BKT-223",
            "ruta": str(dxf),
            "outer": nest_outer,
            "rot_deg": 90.0,
            "shift_x": 15.2,
            "shift_y": 1077.2,
            "orig_minx": 0.0,
            "orig_miny": 0.0,
            "prefer_source_dxf": True,
        }
        try:
            _resolve_placement_matrix(part_doc, p)
            raise SystemExit("FAIL: expected DxfExportValidationError")
        except DxfExportValidationError as exc:
            msg = str(exc)
            assert "GENE-BKT-223" in msg, msg
            assert "no coincide" in msg, msg
            print("OK hard-fail:", msg)

        # Matching nest AABB @ 90° of source should succeed
        # 90° of 7.55x6.25 in = 6.25 x 7.55 in = 158.75 x 191.77 mm
        w_mm, h_mm = 6.25 * 25.4, 7.55 * 25.4
        nest_ok = [
            (10.0, 20.0),
            (10.0 + w_mm, 20.0),
            (10.0 + w_mm, 20.0 + h_mm),
            (10.0, 20.0 + h_mm),
        ]
        p_ok = {
            "part_name": "OK-PART",
            "ruta": str(dxf),
            "outer": nest_ok,
            "rot_deg": 90.0,
            "shift_x": 10.0,
            "shift_y": 20.0,
            "orig_minx": 0.0,
            "orig_miny": 0.0,
            "prefer_source_dxf": True,
        }
        m = _resolve_placement_matrix(part_doc, p_ok)
        assert isinstance(m, Matrix44)
        print("OK match resolves matrix")


if __name__ == "__main__":
    main()
