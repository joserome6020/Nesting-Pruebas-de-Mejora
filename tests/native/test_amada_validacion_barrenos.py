"""Candado: validación Amada ESP. en PARTS (solo ancho 5\")."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ezdxf  # noqa: E402

from modules.nesting_engine.cu_amada_validacion import (  # noqa: E402
    extraer_barrenos_dxf,
    load_amada_barrenos_catalog,
    validar_barrenos_catalogo,
    validar_candidato_amada_dxf,
)

PRUEBA_CU = Path(r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\Prueba CU")


def test_catalogo_sigue_cargando() -> None:
    cat = load_amada_barrenos_catalog()
    assert float(cat["ancho_in"]) == 5.0


def _write_rect_5in_with_circle(out: Path, *, diam_in: float = 0.4375) -> None:
    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    w = 10.0 * 25.4
    h = 5.0 * 25.4
    msp.add_lwpolyline(
        [(0, 0), (w, 0), (w, h), (0, h)],
        close=True,
        dxfattribs={"layer": "CUT_OUTER"},
    )
    r = (diam_in / 2.0) * 25.4
    msp.add_circle((2 * 25.4, 2 * 25.4), r, dxfattribs={"layer": "CUT_INNER"})
    doc.layers.add("CUT_OUTER")
    doc.layers.add("CUT_INNER")
    doc.saveas(str(out))


def test_sintetico_5in_ok_sin_validar_barrenos() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "SYN_AMADA_OK.dxf"
        _write_rect_5in_with_circle(path)
        ok, msg, rot = validar_candidato_amada_dxf(str(path), 0)
        assert ok, msg
        assert rot is None


def test_barreno_fuera_catalogo_no_bloquea_marcar_esp() -> None:
    """El catálogo de barrenos sigue existiendo, pero ya no bloquea PARTS."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "SYN_BAD_HOLE.dxf"
        _write_rect_5in_with_circle(path, diam_in=1.25)
        ok, msg, _rot = validar_candidato_amada_dxf(str(path), 0)
        assert ok, msg
        holes = extraer_barrenos_dxf(str(path), 0)
        ok_h, _msg_h = validar_barrenos_catalogo(holes)
        assert not ok_h


def test_rechaza_ancho_distinto_de_5() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "SYN_BAD_W.dxf"
        doc = ezdxf.new(dxfversion="R2010")
        doc.header["$INSUNITS"] = 4
        msp = doc.modelspace()
        msp.add_lwpolyline(
            [(0, 0), (254, 0), (254, 101.6), (0, 101.6)],
            close=True,
            dxfattribs={"layer": "CUT_OUTER"},
        )
        doc.saveas(str(path))
        ok, msg, _rot = validar_candidato_amada_dxf(str(path), 0)
        assert not ok
        assert "5" in msg or "ancho" in msg.lower()


def test_prueba_cu_candidatas_pasan() -> None:
    if not PRUEBA_CU.is_dir():
        return
    fallos = []
    for f in sorted(PRUEBA_CU.glob("*.dxf")):
        ok, msg, _rot = validar_candidato_amada_dxf(str(f), 0)
        if not ok:
            fallos.append(f"{f.name}: {msg}")
    assert not fallos, "\n".join(fallos)


if __name__ == "__main__":
    test_catalogo_sigue_cargando()
    test_sintetico_5in_ok_sin_validar_barrenos()
    test_barreno_fuera_catalogo_no_bloquea_marcar_esp()
    test_rechaza_ancho_distinto_de_5()
    test_prueba_cu_candidatas_pasan()
    print("[OK] validación Amada ESP. (solo 5\")")
