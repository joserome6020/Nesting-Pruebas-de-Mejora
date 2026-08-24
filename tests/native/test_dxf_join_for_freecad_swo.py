"""Candado: join LINE/ARC nest → LWPOLY para FreeCAD (SWO / CUT_OUTER)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from modules.dxf_join_for_freecad import maybe_join_nest_dxf_for_freecad  # noqa: E402


def test_join_swo_dxf_produces_lwpoly_cut_outer():
    sample = (
        Path.home()
        / "OneDrive - grupoarga.com"
        / "Escritorio"
        / "Nesteos Locales"
        / "Máxima Optimización"
        / "S.W.O 01 X1"
        / "ARGA MODEL CORE"
        / "NESTING"
        / "ROBOT LASER + MINI NEST"
        / "DXF"
        / "SWO-001_0.375_SWO-001-H3.dxf"
    )
    if not sample.is_file():
        print("SKIP test_join_swo: DXF muestra no disponible")
        return

    with tempfile.TemporaryDirectory() as tmp:
        out, note = maybe_join_nest_dxf_for_freecad(str(sample), tmp)
        assert "join LINE/ARC" in note or "join cache" in note, note
        assert os.path.isfile(out), out

        import ezdxf

        doc = ezdxf.readfile(out)
        outers = [
            e
            for e in doc.modelspace()
            if e.dxftype() == "LWPOLYLINE"
            and "CUT_OUTER" in str(e.dxf.layer).upper()
        ]
        lines = [
            e
            for e in doc.modelspace()
            if e.dxftype() == "LINE"
            and "CUT_OUTER" in str(e.dxf.layer).upper()
        ]
        assert len(outers) >= 1, f"sin LWPOLY CUT_OUTER ({note})"
        assert len(lines) == 0, "deben eliminarse LINE en CUT_OUTER"


if __name__ == "__main__":
    test_join_swo_dxf_produces_lwpoly_cut_outer()
    print("OK test_dxf_join_for_freecad_swo")
