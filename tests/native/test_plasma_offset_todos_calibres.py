"""Candado 2026-08-19t — offset plasma 0.0625\" por lado en TODOS los calibres.

Planta: el stock de corte plasma es 1/16\" por lado (el largo/ancho crecen
1/8\"). Antes la regla partía en 0.75\": fino 0.0125\" y grueso 0.250\".
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from modules.plasma_compensator import (  # noqa: E402
    asegurar_dxf_plasma_compensado,
    compensate_dxf_for_plasma,
    compute_plasma_offset_mm,
)

OFFSET_IN = 0.0625
OFFSET_MM = OFFSET_IN * 25.4


def test_offset_0625_fino_y_grueso() -> None:
    for thk in (0.0747, 0.25, 0.375, 0.75, 0.751, 1.0, 2.0):
        got = compute_plasma_offset_mm(thk)
        assert abs(got - OFFSET_MM) < 1e-9, (thk, got, OFFSET_MM)


def test_regla_unica_en_fuente() -> None:
    src = (RAIZ / "modules" / "plasma_compensator.py").read_text(encoding="utf-8")
    fn = src.split("def compute_plasma_offset_mm", 1)[1].split("\ndef ", 1)[0]
    assert "0.0625" in fn
    assert "0.0125" not in fn
    assert "0.250" not in fn
    assert "> 0.75" not in fn


def test_barrenos_inner_encogen_el_mismo_0625() -> None:
    """CUT_INNER usa −offset: CIRCLE y hueco polilínea pierden 0.0625\" de radio/lado."""
    import tempfile

    import ezdxf

    off_mm = compute_plasma_offset_mm(0.1875)
    assert abs(off_mm - OFFSET_MM) < 1e-9
    d = OFFSET_IN

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src, dst = root / "src.dxf", root / "out.dxf"
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 1
        msp = doc.modelspace()
        msp.add_lwpolyline(
            [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0)],
            close=True,
            dxfattribs={"layer": "CUT_OUTER"},
        )
        msp.add_circle((3.0, 3.0), radius=1.0, dxfattribs={"layer": "CUT_INNER"})
        msp.add_lwpolyline(
            [(6.0, 2.0), (8.0, 2.0), (8.0, 4.0), (6.0, 4.0)],
            close=True,
            dxfattribs={"layer": "CUT_INNER"},
        )
        doc.saveas(src)

        st = compensate_dxf_for_plasma(src, dst, offset_mm=off_mm)
        assert int(st.get("changed") or 0) >= 2, st
        out = ezdxf.readfile(dst).modelspace()

        circs = [
            e
            for e in out
            if e.dxftype() == "CIRCLE" and str(e.dxf.layer).upper() == "CUT_INNER"
        ]
        assert len(circs) == 1, [e.dxftype() for e in out]
        assert abs(float(circs[0].dxf.radius) - (1.0 - d)) < 1e-6

        holes = [
            e
            for e in out
            if e.dxftype() == "LWPOLYLINE" and str(e.dxf.layer).upper() == "CUT_INNER"
        ]
        assert holes, "el hueco polilínea CUT_INNER debe seguir existiendo"
        xs, ys = [], []
        for x, y, *_ in holes[0].get_points("xy"):
            xs.append(float(x))
            ys.append(float(y))
        assert abs((max(xs) - min(xs)) - (2.0 - 2.0 * d)) < 1e-4, (min(xs), max(xs))
        assert abs((max(ys) - min(ys)) - (2.0 - 2.0 * d)) < 1e-4, (min(ys), max(ys))

        outers = [
            e
            for e in out
            if e.dxftype() == "LWPOLYLINE" and str(e.dxf.layer).upper() == "CUT_OUTER"
        ]
        ox, oy = [], []
        for x, y, *_ in outers[0].get_points("xy"):
            ox.append(float(x))
            oy.append(float(y))
        assert abs((max(ox) - min(ox)) - (10.0 + 2.0 * d)) < 1e-4
        assert abs((max(oy) - min(oy)) - (6.0 + 2.0 * d)) < 1e-4


def test_compensador_inner_usa_el_negativo() -> None:
    src = (RAIZ / "modules" / "plasma_compensator.py").read_text(encoding="utf-8")
    assert "else -off_dxf" in src
    assert 'role == "outer"' in src


def test_despachador_reusa_la_regla() -> None:
    src = (RAIZ / "despachador_nocturno.py").read_text(encoding="utf-8")
    assert "compute_plasma_offset_mm" in src
    assert "0.250 if espesor" not in src
    assert "else 0.0125" not in src


def test_sidecar_offset_viejo_se_regenera() -> None:
    """Un Plasma Compensated de 0.0125\" no se reusa con la regla 0.0625\"."""
    import tempfile

    import ezdxf

    from modules.plasma_offset2d import write_version_sidecar

    old_mm = 0.0125 * 25.4
    new_mm = OFFSET_MM
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src = root / "p63.dxf"
        old = root / "Plasma Compensated" / "p63.dxf"
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 1
        doc.modelspace().add_lwpolyline(
            [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0)],
            close=True,
            dxfattribs={"layer": "CUT_OUTER"},
        )
        doc.saveas(src)
        st = compensate_dxf_for_plasma(src, old, offset_mm=old_mm)
        assert int(st.get("changed") or 0) >= 1, st
        write_version_sidecar(old, backend="test", offset_mm=old_mm)

        out, err = asegurar_dxf_plasma_compensado(src, new_mm)
        assert out and not err, err
        msp = ezdxf.readfile(out).modelspace()
        xs, ys = [], []
        for e in msp:
            if e.dxftype() != "LWPOLYLINE":
                continue
            for x, y, *_ in e.get_points("xy"):
                xs.append(float(x))
                ys.append(float(y))
        assert xs and ys
        assert abs((max(xs) - min(xs)) - (10.0 + 2.0 * OFFSET_IN)) < 1e-3
        assert abs((max(ys) - min(ys)) - (6.0 + 2.0 * OFFSET_IN)) < 1e-3


if __name__ == "__main__":
    test_offset_0625_fino_y_grueso()
    test_regla_unica_en_fuente()
    test_barrenos_inner_encogen_el_mismo_0625()
    test_compensador_inner_usa_el_negativo()
    test_despachador_reusa_la_regla()
    test_sidecar_offset_viejo_se_regenera()
    print("OK plasma_offset_todos_calibres")
