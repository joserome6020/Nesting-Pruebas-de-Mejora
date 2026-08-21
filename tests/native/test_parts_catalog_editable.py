"""Candado: catálogo MATERIAL/CALIBRE de PARTS empatar nest (0.11811→0.1196)."""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from interface.parts_catalog import (
    MATERIALES_ANS_BASE,
    clave_nest_pieza,
    list_calibres_ans,
    list_materiales_ans,
    mutar_fila_pieza,
)


def test_materiales_canonicos():
    mats = list_materiales_ans()
    for req in ("GALVANIZADO", "A 36", "CARBONO", "INOXIDABLE", "ALUMINIO", "CU"):
        assert req in mats, f"falta material canónico {req} en {mats}"
    assert list(MATERIALES_ANS_BASE) == [
        "GALVANIZADO",
        "A 36",
        "CARBONO",
        "INOXIDABLE",
        "ALUMINIO",
        "CU",
    ]


def test_calibres_galv_incluye_01196_y_snap():
    cals = list_calibres_ans("GALVANIZADO")
    assert "0.1196" in cals, f"Cal 11 Herinox ausente: {cals[:12]}…"
    assert "0.25" in cals
    assert clave_nest_pieza("0.11811", "Galvanizado") == "0.1196_GALVANIZADO"
    assert clave_nest_pieza("0.11811", "GALVANIZADO") == "0.1196_GALVANIZADO"


def test_mutar_fila_cambia_clave_nest():
    datos = [
        ("GENE-BKT-153", "GALVANIZADO", "8", "0.11811", "LISTO", r"C:\tmp\a.dxf"),
        ("OTRA", "A 36", "1", "0.25", "LISTO", r"C:\tmp\b.dxf"),
    ]
    assert mutar_fila_pieza(
        datos, r"C:\tmp\a.dxf", material="A 36", calibre="0.25"
    )
    pieza, mat, qty, cal, st, ruta = datos[0]
    assert pieza == "GENE-BKT-153"
    assert mat == "A 36"
    assert cal == "0.25"
    assert qty == "8"
    assert clave_nest_pieza(cal, mat) == "0.25_A 36"
    # Segunda fila intacta
    assert datos[1][1] == "A 36" and datos[1][3] == "0.25"


def test_stock_enriquece_materiales():
    rows = [
        ["0.1196", "A 36 GALV", "CODE1", "120", "60"],
        ["0.125", "SSTL 304", "CODE2", "120", "60"],
    ]
    mats = list_materiales_ans(rows)
    assert "GALVANIZADO" in mats
    assert "INOXIDABLE" in mats
    cals = list_calibres_ans("GALVANIZADO", rows)
    assert "0.1196" in cals


def main():
    test_materiales_canonicos()
    test_calibres_galv_incluye_01196_y_snap()
    test_mutar_fila_cambia_clave_nest()
    test_stock_enriquece_materiales()
    print("OK parts_catalog_editable")


if __name__ == "__main__":
    main()
