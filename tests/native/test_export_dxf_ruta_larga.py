"""Candado 2026-10-01: export DXF abortado en ruta UNC > 260 caracteres.

W.O. 154 X1 (\\\\192.168.2.80\\...\\NESTEO DXF\\DXF\\, 224 chars) +
`NESTING_0.1875_RTZ1-0.1875-60.0x120.0-W.O. 1.dxf` = 272 → os.replace sin
prefijo \\\\?\\ fallaba con WinError 3 en el .exe (no longPathAware).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import ezdxf  # noqa: E402

from modules import nest_exporter  # noqa: E402

_MAX_PATH = 259
_real_replace = os.replace


def _replace_con_max_path(src, dst):
    for p in (str(src), str(dst)):
        if len(p) > _MAX_PATH and not p.startswith("\\\\?\\"):
            err = FileNotFoundError(3, "El sistema no puede encontrar la ruta especificada", p)
            err.winerror = 3
            raise err
    return _real_replace(src, dst)


def test_save_dxf_atomic_ruta_mayor_260():
    base = tempfile.mkdtemp(prefix="arga_lp_")
    profundo = Path(base)
    while len(str(profundo)) < 225:
        profundo = profundo / "MODEL CORE FILES W.O. 154 X1"
    out = profundo / "NESTING_0.1875_RTZ1-0.1875-60.0x120.0-W.O. 154 X1.dxf"
    assert len(str(out)) > 260, len(str(out))

    doc = ezdxf.new("R2010")
    doc.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True)

    os.replace = _replace_con_max_path
    try:
        nest_exporter._save_dxf_atomic(doc, str(out))
    finally:
        os.replace = _real_replace

    largo = "\\\\?\\" + str(out)
    assert os.path.isfile(largo), largo
    sobrantes = [n for n in os.listdir("\\\\?\\" + str(profundo)) if n.startswith(".__arga_export_")]
    assert not sobrantes, sobrantes


def test_ruta_larga_win_y_makedirs():
    sys.path.insert(0, str(RAIZ / "CAD (OCCT)"))
    from engine.local_staging import ruta_larga_win
    from modules.nesting_engine.exporter import _makedirs_largo

    unc = "\\\\192.168.2.80\\Users\\x\\NESTEO DXF\\DXF\\a.dxf"
    assert ruta_larga_win(unc) == "\\\\?\\UNC\\192.168.2.80\\Users\\x\\NESTEO DXF\\DXF\\a.dxf"
    assert ruta_larga_win("\\\\?\\C:\\x") == "\\\\?\\C:\\x"
    assert ruta_larga_win("C:\\corto\\a.dxf") == "C:\\corto\\a.dxf"

    base = tempfile.mkdtemp(prefix="arga_lp_dir_")
    carpeta = Path(base)
    while len(str(carpeta)) < 270:
        carpeta = carpeta / "ARGA MODEL CORE NESTING"
    _makedirs_largo(str(carpeta))
    assert os.path.isdir("\\\\?\\" + str(carpeta))


def test_step_occt_ruta_mayor_260():
    sys.path.insert(0, str(RAIZ / "CAD (OCCT)"))
    try:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    except Exception:
        print("SKIP OCP no disponible")
        return
    from engine.occt_runtime import write_step_shape

    base = tempfile.mkdtemp(prefix="arga_lp_step_")
    carpeta = Path(base)
    while len(str(carpeta)) < 230:
        carpeta = carpeta / "NESTEO DXF STEP Cama A"
    out = carpeta / "NESTING_0.1875_RTZ1-0.1875-60.0x120.0-W.O. 154 X1.step"
    assert len(str(out)) > 260
    write_step_shape(BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape(), out)
    largo = "\\\\?\\" + str(out)
    assert os.path.isfile(largo) and os.path.getsize(largo) > 64


if __name__ == "__main__":
    test_save_dxf_atomic_ruta_mayor_260()
    test_ruta_larga_win_y_makedirs()
    test_step_occt_ruta_mayor_260()
    print("OK test_export_dxf_ruta_larga")
