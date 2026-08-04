"""Smoke: CIRCLE inner se reduce con compensación plasma."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import ezdxf

from modules.plasma_compensator import compensate_dxf_for_plasma, compute_plasma_offset_mm


def main() -> int:
    td = Path(tempfile.mkdtemp())
    src = td / "in.dxf"
    dst = td / "out.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (10, 0), (10, 5), (0, 5)],
        close=True,
        dxfattribs={"layer": "CUT_OUTER"},
    )
    msp.add_circle((5, 2.5), radius=1.0, dxfattribs={"layer": "CUT_INNER"})
    doc.saveas(str(src))

    off = compute_plasma_offset_mm(0.313)
    st = compensate_dxf_for_plasma(src, dst, offset_mm=off)
    circ = [e for e in ezdxf.readfile(str(dst)).modelspace() if e.dxftype() == "CIRCLE"][0]
    r = float(circ.dxf.radius)
    expect = 1.0 - off / 25.4
    assert abs(r - expect) < 1e-6, (r, expect, st)
    assert int(st.get("circles") or 0) == 1
    print("ok", f"r={r:.6f}", f"expect={expect:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
