"""Clasificador LS-READY (Paso 1): 1 DXF → JSON UF1 (Cama A) + UF2 (Cama B).

Ya no corre al exportar (2026-09-29d); queda como herramienta manual.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from modules.ls_ready_paso1.bridge import (
    generar_ls_ready_desde_dxf,
    ls_ready_habilitado,
    rutas_json_ls_ready_para_dxf,
)


def _write_steel_nest_dxf(path: Path) -> None:
    import ezdxf

    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 4
    for name, color in (("CUT_OUTER", 1), ("CUT_INNER", 2), ("MARK", 4), ("Plate", 3)):
        doc.layers.new(name, dxfattribs={"color": color})
    msp = doc.modelspace()
    w, h = 6096.0, 2438.0
    msp.add_lwpolyline([(0, 0), (w, 0), (w, h), (0, h)], close=True, dxfattribs={"layer": "Plate"})
    outer = [(500.0, 400.0), (1100.0, 400.0), (1100.0, 900.0), (500.0, 900.0)]
    msp.add_lwpolyline(outer, close=True, dxfattribs={"layer": "CUT_OUTER"})
    hole = [(700.0, 550.0), (780.0, 550.0), (780.0, 630.0), (700.0, 630.0)]
    msp.add_lwpolyline(hole, close=True, dxfattribs={"layer": "CUT_INNER"})
    msp.add_line((520.0, 420.0), (580.0, 420.0), dxfattribs={"layer": "MARK"})
    doc.saveas(str(path))


def test_rutas_json_junto_a_familia_robot_laser():
    dxf = Path(r"C:\job\NESTING\ROBOT LASER + MINI NEST\DXF\NEST_0.25_H1.dxf")
    a, b = rutas_json_ls_ready_para_dxf(dxf)
    assert a.name == "NEST_0.25_H1_LS_READY_UF1.json"
    assert b.name == "NEST_0.25_H1_LS_READY_UF2.json"
    assert a.parent.name == "Cama A"
    assert b.parent.name == "Cama B"
    assert a.parent.parent.name == "JSON"
    assert a.parent.parent.parent.name == "ROBOT LASER + MINI NEST"


def test_dxf_nest_genera_json_cama_a_y_b():
    assert ls_ready_habilitado()
    with tempfile.TemporaryDirectory() as tmp:
        family = Path(tmp) / "NESTING" / "ROBOT LASER + MINI NEST"
        dxf_dir = family / "DXF"
        dxf_dir.mkdir(parents=True)
        dxf = dxf_dir / "NEST_0.25_W.O. TEST-H1.dxf"
        _write_steel_nest_dxf(dxf)

        result = generar_ls_ready_desde_dxf(dxf)
        assert result["ok"], result
        uf1 = Path(result["UF1"]["path"])
        uf2 = Path(result["UF2"]["path"])
        assert uf1.is_file() and uf1.stat().st_size > 64
        assert uf2.is_file() and uf2.stat().st_size > 64
        assert uf1.parent.name == "Cama A"
        assert uf2.parent.name == "Cama B"

        data_a = json.loads(uf1.read_text(encoding="utf-8"))
        data_b = json.loads(uf2.read_text(encoding="utf-8"))
        assert data_a.get("pieces")
        assert data_b.get("pieces")
        assert data_a.get("plan") is not None
        assert data_b.get("plan") is not None
        prog_a = data_a.get("robot_programming") or {}
        prog_b = data_b.get("robot_programming") or {}
        assert str(prog_a.get("bed") or "").upper() == "A"
        assert str(prog_b.get("bed") or "").upper() == "B"
        assert int(prog_a.get("uframe") or 0) == 1
        assert int(prog_b.get("uframe") or 0) == 2


if __name__ == "__main__":
    test_rutas_json_junto_a_familia_robot_laser()
    test_dxf_nest_genera_json_cama_a_y_b()
    print("test_ls_ready_paso1.py PASS")
