"""Candado 2026-08-19u — mudar una pieza plasma no la deja en geometría base."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

import ezdxf  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

from modules.nesting_engine.manager import MotorNesting  # noqa: E402
from modules.plasma_compensator import (  # noqa: E402
    asegurar_dxf_plasma_compensado,
    compute_plasma_offset_mm,
)


def test_pack_visual_de_mudanza_recarga_dxf_plasma() -> None:
    off_mm = compute_plasma_offset_mm(0.375)
    d_in = off_mm / 25.4
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "P63.dxf"
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 1
        doc.modelspace().add_lwpolyline(
            [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)],
            close=True,
            dxfattribs={"layer": "CUT_OUTER"},
        )
        doc.saveas(src)
        out, err = asegurar_dxf_plasma_compensado(src, off_mm)
        assert out and not err, err

        # Polígonos del nest AÚN en tamaño original (el bug de mudar).
        pieza = {
            "nombre": "P63",
            "poligonos": [[[0.0, 0.0], [254.0, 0.0], [254.0, 127.0], [0.0, 127.0]]],
            "marcas": [],
            "area": 254.0 * 127.0,
            "calibre": "0.375",
            "material": "A 36",
            "ruta": str(src),
            "plasma_compensada_manual": True,
            "plasma_offset_mm_manual": float(off_mm),
        }
        motor = MotorNesting.__new__(MotorNesting)
        pack = motor._as_pack_piece_visual(pieza)
        assert pack is not None
        poly = pack["poly"]
        assert isinstance(poly, Polygon)
        minx, miny, maxx, maxy = poly.bounds
        dx_in = (maxx - minx) / 25.4
        dy_in = (maxy - miny) / 25.4
        assert abs(dx_in - (10.0 + 2.0 * d_in)) < 0.02, (dx_in, 10.0 + 2.0 * d_in)
        assert abs(dy_in - (5.0 + 2.0 * d_in)) < 0.02, (dy_in, 5.0 + 2.0 * d_in)
        assert pack.get("plasma_fuente_ya_compensada") is True
        assert pack.get("ruta_plasma")

        var = {"poly": pack["poly"], "marks": pack.get("marks")}
        colocada = motor._pieza_colocada_incremental(pieza, var, 12.0, 8.0)
        assert colocada is not None
        assert colocada.get("plasma_compensada_manual") is True
        assert colocada.get("plasma_fuente_ya_compensada") is True
        assert colocada.get("ruta") == str(src)
        assert colocada.get("ruta_plasma")
        rings = (colocada.get("poligonos") or [[]])[0]
        xs = [float(pt[0]) for pt in rings]
        ys = [float(pt[1]) for pt in rings]
        assert abs(((max(xs) - min(xs)) / 25.4) - (10.0 + 2.0 * d_in)) < 0.02
        assert abs(((max(ys) - min(ys)) / 25.4) - (5.0 + 2.0 * d_in)) < 0.02

        from modules.nesting_engine.display_geometry import _ruta_dxf_efectiva

        ruta_disp = _ruta_dxf_efectiva(colocada)
        assert ruta_disp and Path(ruta_disp).is_file()
        assert "Plasma Compensated" in str(ruta_disp).replace("\\", "/")


def test_no_salta_asegurar_si_ya_hay_archivo() -> None:
    mixin = (
        RAIZ / "interface" / "qt" / "tabs" / "_mixin_nesting_calc.py"
    ).read_text(encoding="utf-8")
    mgr = (RAIZ / "modules" / "nesting_engine" / "manager.py").read_text(
        encoding="utf-8"
    )
    assert "if prec and os.path.isfile(prec):" not in mgr
    assert "if not ruta_plasma or not os.path.isfile(ruta_plasma):" not in mixin


if __name__ == "__main__":
    test_pack_visual_de_mudanza_recarga_dxf_plasma()
    test_no_salta_asegurar_si_ya_hay_archivo()
    print("OK plasma_transfer_sigue_compensada")
