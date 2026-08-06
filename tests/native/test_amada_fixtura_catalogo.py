"""Candado: catálogo Amada elige siempre la fixtura más justa.

Caso real: GENE-BCU-5-170 = 28.87\" × 5\" → Fixtura 2 (canal 28.95\"), no la
original de ~35.33\". Piezas más largas (p. ej. 30\") van a la original.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from modules.dxf_export import amada_fixture as af  # noqa: E402


def test_catalogo_tiene_dos_fixturas():
    fx = {f["id"]: f for f in af.listar_fixturas_amada()}
    assert "fixtura_2" in fx and "original" in fx
    assert fx["fixtura_2"]["disponible"], "falta FIXTURA AMADA/Fixtura 2.DXF"
    assert fx["original"]["disponible"]
    assert abs(float(fx["fixtura_2"]["canal_in"]) - 28.95) < 0.05
    assert float(fx["original"]["canal_in"]) > 35.0


def test_gene_bcu_28_87_elige_fixtura_2():
    elec = af.elegir_fixtura_amada(28.87)
    assert elec is not None
    assert elec["id"] == "fixtura_2"
    assert float(elec["holgura_in"]) < 0.15


def test_pieza_30_elige_original():
    elec = af.elegir_fixtura_amada(30.0)
    assert elec is not None
    assert elec["id"] == "original"


def test_pieza_demasiado_larga_no_cabe():
    assert af.elegir_fixtura_amada(40.0) is None


def test_largo_max_es_el_mayor_canal():
    assert af.amada_fixtura_largo_max_in() > 35.0


if __name__ == "__main__":
    test_catalogo_tiene_dos_fixturas()
    test_gene_bcu_28_87_elige_fixtura_2()
    test_pieza_30_elige_original()
    test_pieza_demasiado_larga_no_cabe()
    test_largo_max_es_el_mayor_canal()
    print("SMOKE OK")
