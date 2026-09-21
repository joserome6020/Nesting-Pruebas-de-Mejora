"""Candado: rutas PDF/.arganest > MAX_PATH usan prefijo \\?\\ en Windows."""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from modules.win_long_path import asegurar_ruta_escritura, needs_win_long_path, win_long_path
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
    assert needs_win_long_path(raw) is (os.name == "nt")
    longp = win_long_path(raw)
    legacy = _win_long_path(raw)
    if os.name == "nt":
        assert longp.startswith("\\\\?\\UNC\\")
        assert "192.168.2.80" in longp
        assert legacy == longp
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
    longp = win_long_path(raw)
    if os.name == "nt":
        assert longp.startswith("\\\\?\\")
        assert not longp.startswith("\\\\?\\UNC\\")
    else:
        assert longp == raw


def test_arganest_guardar_usa_long_path(monkeypatch=None):
    """Simula el WinError 3 del rename tmp→.arganest en UNC larga."""
    from interface import nesting_workspace as nw

    dest = (
        r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals\\"
        r"ARGA METALS CORPORATE SYSTEM\ATC_COMPARTMENT\VANTRAN\\"
        r"06-70-1498HVATC25502\MODEL CORE FILES\W.O. 83 X1\\"
        r"ARGA MODEL CORE\NESTING\ARCHIVO DE NESTEO ARGANEST\\"
        r"Nesting_06-70-1498HVATC25502-W.O. 83 X1.arganest"
    )
    assert len(dest) > 260

    calls = {"replace": [], "copyfile": [], "makedirs": []}

    def fake_makedirs(path, exist_ok=False):
        calls["makedirs"].append(path)

    def fake_replace(src, dst):
        calls["replace"].append((src, dst))
        # Sin \\?\ el replace real falla con WinError 3 en UNC larga.
        if os.name == "nt" and not str(dst).startswith("\\\\?\\"):
            raise OSError(3, "El sistema no puede encontrar la ruta especificada", dst)
        return None

    def fake_copyfile(src, dst):
        calls["copyfile"].append((src, dst))

    real_open = open

    # Escritura real solo del tmp local; destino UNC es mock.
    import gzip
    import shutil

    old = {
        "makedirs": os.makedirs,
        "replace": os.replace,
        "copyfile": shutil.copyfile,
        "isfile": os.path.isfile,
    }
    os.makedirs = fake_makedirs  # type: ignore
    os.replace = fake_replace  # type: ignore
    shutil.copyfile = fake_copyfile  # type: ignore
    try:
        payload = {
            "schema": "arga_nesting_workspace_v2",
            "workspace_material_kind": "steel",
        }
        # No debe lanzar: replace/copy con long-path.
        nw.guardar_workspace_payload(payload, dest)
        assert calls["replace"] or calls["copyfile"]
        used_dst = (calls["replace"] or calls["copyfile"])[0][1]
        if os.name == "nt":
            assert str(used_dst).startswith("\\\\?\\UNC\\"), used_dst
    finally:
        os.makedirs = old["makedirs"]  # type: ignore
        os.replace = old["replace"]  # type: ignore
        shutil.copyfile = old["copyfile"]  # type: ignore


if __name__ == "__main__":
    test_win_long_path_unc()
    test_win_long_path_local()
    test_arganest_guardar_usa_long_path()
    print("OK")
