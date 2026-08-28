"""Candado: reexport WO/SWO limpia NESTING previo (no deja DXF huérfanos).

Caso real: reexportar la misma W.O./S.W.O. tras cambiar piezas o fallar un
export parcial deja DXF viejos en familias (cobre/láser/plasma) porque el
motor solo sobrescribía rutas coincidentes. Al reexportar debe borrarse
todo el árbol NESTING (y placeholders DXF/3D NESTING) antes de escribir.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from modules.nesting_engine.exporter import (  # noqa: E402
    _CARPETAS_NESTING_PREVIO,
    limpiar_nesting_previo,
)


def test_limpiar_no_hace_nada_si_no_existe():
    with tempfile.TemporaryDirectory() as tmp:
        assert limpiar_nesting_previo(tmp) == 0
        assert limpiar_nesting_previo("") == 0
        assert limpiar_nesting_previo(str(Path(tmp) / "no_existe")) == 0


def test_limpiar_borra_todas_las_familias_y_placeholders():
    with tempfile.TemporaryDirectory() as tmp:
        arga = Path(tmp) / "ARGA MODEL CORE"
        nesting = arga / "NESTING"
        (nesting / "ROBOT PLASMA" / "DXF").mkdir(parents=True)
        (nesting / "NESTEOS DE COBRE" / "DXF").mkdir(parents=True)
        (nesting / "CAMA LASER SIN MINI NEST" / "DXF").mkdir(parents=True)
        (nesting / "ROBOT LASER + MINI NEST" / "JSON" / "Cama A").mkdir(parents=True)
        (nesting / "REPORTE DE NESTEO PDF").mkdir(parents=True)

        huerfano = nesting / "ROBOT PLASMA" / "DXF" / "NESTING_0.25_OLD.dxf"
        huerfano.write_text("0\nEOF\n", encoding="utf-8")
        (nesting / "NESTEOS DE COBRE" / "DXF" / "orphan.dxf").write_text(
            "0\nEOF\n", encoding="utf-8"
        )
        (nesting / "REPORTE DE NESTEO PDF" / "viejo.pdf").write_bytes(b"%PDF")

        dxf_ph = arga / "DXF NESTING"
        step_ph = arga / "3D NESTING"
        dxf_ph.mkdir(parents=True)
        step_ph.mkdir(parents=True)
        (dxf_ph / "residuo.dxf").write_text("0\nEOF\n", encoding="utf-8")
        (step_ph / "residuo.step").write_text("ISO-10303", encoding="utf-8")

        # Carpeta ajena al nesting: no debe tocarse.
        materials = Path(tmp) / "MATERIALS" / "LISTA LARGOS 2"
        materials.mkdir(parents=True)
        keep = materials / "lista.csv"
        keep.write_text("a,b\n", encoding="utf-8")

        n = limpiar_nesting_previo(str(arga))
        assert n > 0
        assert not nesting.exists()
        assert not dxf_ph.exists()
        assert not step_ph.exists()
        assert keep.is_file()


def test_carpetas_previo_cubre_nesting_completo():
    assert "NESTING" in _CARPETAS_NESTING_PREVIO
    assert "DXF NESTING" in _CARPETAS_NESTING_PREVIO
    assert "3D NESTING" in _CARPETAS_NESTING_PREVIO


if __name__ == "__main__":
    test_limpiar_no_hace_nada_si_no_existe()
    test_limpiar_borra_todas_las_familias_y_placeholders()
    test_carpetas_previo_cubre_nesting_completo()
    print("OK test_limpiar_nesting_previo")
