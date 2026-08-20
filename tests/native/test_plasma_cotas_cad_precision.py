"""Candado 2026-08-20b — cotas CAD a milésima; misma ruta DXF ⇒ mismo L×W."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

import ezdxf  # noqa: E402

from interface.qt.nesting_graphics import (  # noqa: E402
    CAD_INCH_DECIMALS,
    _dims_in_desde_ruta_dxf,
    _dims_pieza_tabla_in,
    _fmt_cad_in,
)
from modules.plasma_compensator import (  # noqa: E402
    asegurar_dxf_plasma_compensado,
    compute_plasma_offset_mm,
)


def test_formato_milesima() -> None:
    assert CAD_INCH_DECIMALS == 3
    # 77.375 exacto (77.25 + 0.125) no debe colapsar a 77.37/77.38
    assert _fmt_cad_in(77.375) == "77.375"
    assert _fmt_cad_in(21.6875) == "21.688"


def test_misma_ruta_mismas_cotas() -> None:
    off_mm = compute_plasma_offset_mm(0.375)
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "P63.dxf"
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 1
        doc.modelspace().add_lwpolyline(
            [(0.0, 0.0), (77.25, 0.0), (77.25, 21.56), (0.0, 21.56)],
            close=True,
            dxfattribs={"layer": "CUT_OUTER"},
        )
        doc.saveas(src)
        out, err = asegurar_dxf_plasma_compensado(src, off_mm)
        assert out and not err, err

        a = _dims_in_desde_ruta_dxf(str(out))
        b = _dims_in_desde_ruta_dxf(str(out))
        assert a is not None and b is not None
        assert a == b
        L, W = a
        assert abs(L - (77.25 + 2.0 * (off_mm / 25.4))) < 0.002
        assert abs(W - (21.56 + 2.0 * (off_mm / 25.4))) < 0.002

        p16 = {
            "nombre": "P16",
            "ruta": str(src),
            "ruta_plasma": str(out),
            "plasma_compensada_manual": True,
            "plasma_fuente_ya_compensada": True,
            "plasma_offset_mm_manual": float(off_mm),
            "poligonos": [[[0, 0], [1965.0, 0], [1965.0, 550.0], [0, 550.0]]],
        }
        p63 = dict(p16)
        p63["nombre"] = "P63"
        # Polígonos nest distintos a propósito (ruido); cotas deben salir del DXF.
        p63["poligonos"] = [[[0, 0], [1966.0, 0], [1966.0, 551.0], [0, 551.0]]]
        L1, W1, f1 = _dims_pieza_tabla_in(p16)
        L2, W2, f2 = _dims_pieza_tabla_in(p63)
        assert f1 and f2
        assert (L1, W1) == (L2, W2)
        assert _fmt_cad_in(L1) == _fmt_cad_in(L2)


def test_visor_parts_usa_tres_decimales() -> None:
    src = (RAIZ / "interface" / "qt" / "visualizer.py").read_text(encoding="utf-8")
    assert ':.3f}"' in src
    assert "round(ancho_in, 3)" in src


if __name__ == "__main__":
    test_formato_milesima()
    test_misma_ruta_mismas_cotas()
    test_visor_parts_usa_tres_decimales()
    print("OK plasma_cotas_cad_precision")
