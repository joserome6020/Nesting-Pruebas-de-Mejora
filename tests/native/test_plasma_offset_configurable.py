"""Candado 2026-10-02 — offset plasma editable desde PARTS (clave Configuración Global).

Antes el 0.0625\" estaba fijo en ``compute_plasma_offset_mm``. Ahora vive en
``_config/plasma_offset.json``; PARTS, nesting, export y despachador deben leer
el mismo valor y un JSON inválido no puede degradar a otro stock.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))


def _con_config(td: str) -> Path:
    path = Path(td) / "plasma_offset.json"
    os.environ["ARGA_PLASMA_OFFSET_CONFIG"] = str(path)
    return path


def test_default_sin_archivo() -> None:
    from modules.plasma_compensator import compute_plasma_offset_mm, get_plasma_offset_in

    with tempfile.TemporaryDirectory() as td:
        _con_config(td)
        assert abs(get_plasma_offset_in() - 0.1875) < 1e-9
        assert abs(compute_plasma_offset_mm(0.25) - 0.1875 * 25.4) < 1e-9


def test_valor_guardado_llega_a_compute() -> None:
    from modules.plasma_compensator import (
        compute_plasma_offset_mm,
        get_plasma_offset_in,
        set_plasma_offset_in,
    )

    with tempfile.TemporaryDirectory() as td:
        path = _con_config(td)
        set_plasma_offset_in(0.09375)
        assert json.loads(path.read_text(encoding="utf-8"))["offset_in"] == 0.09375
        assert abs(get_plasma_offset_in() - 0.09375) < 1e-9
        for thk in (0.0747, 0.25, 1.0):
            assert abs(compute_plasma_offset_mm(thk) - 0.09375 * 25.4) < 1e-9
        set_plasma_offset_in(0.0625)
        assert abs(compute_plasma_offset_mm(0.5) - 0.0625 * 25.4) < 1e-9


def test_fuera_de_rango_se_rechaza() -> None:
    from modules.plasma_compensator import PlasmaOffsetError, set_plasma_offset_in

    with tempfile.TemporaryDirectory() as td:
        _con_config(td)
        for malo in (0.0, -0.1, 0.25, 1.5875, "abc"):
            try:
                set_plasma_offset_in(malo)
            except PlasmaOffsetError:
                continue
            raise AssertionError(f"debió rechazar {malo!r}")


def test_json_invalido_cae_al_estandar() -> None:
    import time

    from modules.plasma_compensator import get_plasma_offset_in

    with tempfile.TemporaryDirectory() as td:
        path = _con_config(td)
        path.write_text(json.dumps({"offset_in": 1.5875}), encoding="utf-8")
        assert abs(get_plasma_offset_in() - 0.1875) < 1e-9
        time.sleep(0.02)
        path.write_text("{no json", encoding="utf-8")
        os.utime(path, None)
        assert abs(get_plasma_offset_in() - 0.1875) < 1e-9


def test_dxf_compensado_usa_valor_configurado() -> None:
    import ezdxf

    from modules.plasma_compensator import (
        asegurar_dxf_plasma_compensado,
        compute_plasma_offset_mm,
        set_plasma_offset_in,
    )

    with tempfile.TemporaryDirectory() as td:
        _con_config(td)
        src = Path(td) / "pieza.dxf"
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 1
        doc.modelspace().add_lwpolyline(
            [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0)],
            close=True,
            dxfattribs={"layer": "CUT_OUTER"},
        )
        doc.saveas(src)

        def ancho(off_in: float) -> float:
            set_plasma_offset_in(off_in)
            out, err = asegurar_dxf_plasma_compensado(src, compute_plasma_offset_mm(0.25))
            assert out and not err, err
            xs = [
                float(x)
                for e in ezdxf.readfile(out).modelspace()
                if e.dxftype() == "LWPOLYLINE"
                for x, *_ in e.get_points("xy")
            ]
            return max(xs) - min(xs)

        assert abs(ancho(0.0625) - 10.125) < 1e-3
        # Mismo archivo, offset nuevo: el sidecar viejo no se reusa.
        assert abs(ancho(0.125) - 10.25) < 1e-3


def test_parts_tiene_selector_con_clave() -> None:
    src = (RAIZ / "interface" / "qt" / "tabs" / "tab_parts.py").read_text(encoding="utf-8")
    assert "cmb_offset_plasma" in src
    assert "_autorizar_edicion_dyt" in src
    assert "set_plasma_offset_in" in src


def test_sin_fallbacks_fijos() -> None:
    for rel in (
        ("modules", "nesting_engine", "manager.py"),
        ("modules", "nesting_engine", "exporter.py"),
        ("interface", "qt", "nesting_graphics.py"),
    ):
        txt = RAIZ.joinpath(*rel).read_text(encoding="utf-8")
        assert "= 0.0625 * 25.4" not in txt, rel


if __name__ == "__main__":
    test_default_sin_archivo()
    test_valor_guardado_llega_a_compute()
    test_fuera_de_rango_se_rechaza()
    test_json_invalido_cae_al_estandar()
    test_dxf_compensado_usa_valor_configurado()
    test_parts_tiene_selector_con_clave()
    test_sin_fallbacks_fijos()
    print("OK plasma_offset_configurable")
