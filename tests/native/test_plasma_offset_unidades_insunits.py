"""Candado: compensación plasma no infla piezas por $INSUNITS mal puesto.

Caso planta (SWO): DXF en pulgadas con header mm → stock 1.59\"/lado en vez
de 0.0625\". También: no re-compensar un DXF ya en Plasma Compensated/.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

import ezdxf  # noqa: E402

from modules.plasma_compensator import (  # noqa: E402
    compensate_dxf_for_plasma,
    compute_plasma_offset_mm,
    resolver_origen_dxf_sin_compensar,
    resolver_off_dxf_plasma,
    ruta_dxf_plasma_compensado,
)


OFFSET_MM = 0.0625 * 25.4


def _rect_dxf(path: Path, *, insunits: int, w: float = 10.0, h: float = 6.0) -> None:
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = int(insunits)
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)],
        close=True,
        dxfattribs={"layer": "CUT_OUTER"},
    )
    doc.saveas(path)


def test_insunits_mm_con_geometria_pulgadas_usa_0625_in():
    """Header dice mm pero la pieza mide 10\"×6\" → off_dxf debe ser 0.0625\"."""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "part.dxf"
        _rect_dxf(src, insunits=4, w=10.0, h=6.0)  # mentira: son pulgadas
        doc = ezdxf.readfile(src)
        off_dxf, label, _utm = resolver_off_dxf_plasma(doc, OFFSET_MM)
        assert abs(off_dxf - 0.0625) < 1e-9, (off_dxf, label)
        assert "inch" in label

        dst = Path(td) / "out.dxf"
        st = compensate_dxf_for_plasma(src, dst, offset_mm=OFFSET_MM)
        assert abs(float(st["offset_dxf"]) - 0.0625) < 1e-9, st
        out = ezdxf.readfile(dst)
        # BBox debe crecer ~0.125\" en cada eje (2×0.0625).
        xs, ys = [], []
        for e in out.modelspace():
            if e.dxftype() == "LWPOLYLINE":
                for p in e.get_points("xy"):
                    xs.append(float(p[0]))
                    ys.append(float(p[1]))
        assert xs and ys
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        assert abs(w - 10.125) < 0.02, w
        assert abs(h - 6.125) < 0.02, h


def test_no_recompensar_si_origen_ya_es_plasma_compensated():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        processed = root / "Processed Files"
        processed.mkdir()
        src = processed / "FOO.dxf"
        _rect_dxf(src, insunits=1)
        comp_dir = processed / "Plasma Compensated"
        comp_dir.mkdir()
        already = comp_dir / "FOO.dxf"
        _rect_dxf(already, insunits=1, w=10.125, h=6.125)

        resolved = resolver_origen_dxf_sin_compensar(already)
        assert resolved.resolve() == src.resolve()

        # Destino no anida Plasma Compensated/Plasma Compensated/
        dst = ruta_dxf_plasma_compensado(already)
        assert dst.parent.resolve() == comp_dir.resolve()


def test_offset_regla_sigue_0625():
    assert abs(compute_plasma_offset_mm(0.25) - OFFSET_MM) < 1e-9
    assert abs(compute_plasma_offset_mm(1.0) - OFFSET_MM) < 1e-9


if __name__ == "__main__":
    test_offset_regla_sigue_0625()
    test_insunits_mm_con_geometria_pulgadas_usa_0625_in()
    test_no_recompensar_si_origen_ya_es_plasma_compensated()
    print("OK")
