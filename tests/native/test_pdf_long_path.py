"""Candado: rutas PDF > MAX_PATH usan prefijo \\?\\ en Windows."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from reporte_pdf_nesting import _win_long_path


def test_win_long_path_unc():
    raw = (
        r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals\\"
        r"ARGA METALS CORPORATE SYSTEM\ATC_COMPARTMENT\VANTRAN\\"
        r"06-70-1612COMPARTMENT261093\MODEL CORE FILES\W.O. 82 X3\\"
        r"ARGA MODEL CORE\NESTING\REPORTE DE NESTEO PDF\\"
        r"Nesting_Reporte_06-70-1612COMPARTMENT261093-W.O. 82 X3.pdf"
    )
    assert len(raw) > 260
    longp = _win_long_path(raw)
    if os.name == "nt":
        assert longp.startswith("\\\\?\\UNC\\")
        assert "192.168.2.80" in longp
    else:
        assert longp == raw


def test_win_long_path_local():
    raw = (
        r"C:\Users\aaron_orrantia\OneDrive - grupoarga.com\Escritorio\\"
        r"Nesteos Locales\ATC_COMPARTMENT\VANTRAN\06-70-1612COMPARTMENT261093\\"
        r"MODEL CORE FILES\W.O. 140 X3\ARGA MODEL CORE\NESTING\\"
        r"REPORTE DE NESTEO PDF\\"
        r"Nesting_Reporte_06-70-1612COMPARTMENT261093-W.O. 140 X3.pdf"
    )
    assert len(raw) > 260
    longp = _win_long_path(raw)
    if os.name == "nt":
        assert longp.startswith("\\\\?\\")
        assert not longp.startswith("\\\\?\\UNC\\")
    else:
        assert longp == raw


if __name__ == "__main__":
    test_win_long_path_unc()
    test_win_long_path_local()
    print("OK")
