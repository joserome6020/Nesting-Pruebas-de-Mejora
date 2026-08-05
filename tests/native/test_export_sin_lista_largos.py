"""Smoke: un job sin CSV de largos se reporta como aviso, no tumba la exportacion.

Caso real: 251008-COMPARTMENT no lleva perfiles, su AutoDXF solo tiene DXF y el
importador devuelve `csv_no_encontrado`. Antes eso abortaba el multi-lote entero
en la etapa PostgreSQL.
"""
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))

from modules.lista_largos_importer import _resolver_csv_lista_largos
from postgres_connector import (
    ESTADOS_LARGOS_SIN_LISTA,
    _registrar_wo_sin_lista_largos,
    obtener_wos_sin_lista_largos,
    reiniciar_avisos_lista_largos,
)


def test_autodxf_sin_csv_no_resuelve_lista():
    with tempfile.TemporaryDirectory() as tmp:
        autodxf = Path(tmp) / "AutoDXF"
        autodxf.mkdir()
        (autodxf / "251008 - ITEM 1, SSTL 304, QTY 1, Cal 0.1046.dxf").write_text("0\nEOF\n")

        assert _resolver_csv_lista_largos(autodxf) is None

        (autodxf / "Lista_Perfiles_Clasificado.csv").write_text("Nombre,Largo\n")
        assert _resolver_csv_lista_largos(autodxf) is not None


def test_estados_sin_lista_son_avisos():
    assert "csv_no_encontrado" in ESTADOS_LARGOS_SIN_LISTA
    assert "csv_vacio" in ESTADOS_LARGOS_SIN_LISTA


def test_avisos_se_acumulan_sin_duplicar():
    reiniciar_avisos_lista_largos()
    _registrar_wo_sin_lista_largos("W.O. 3 X2", "251008-COMPARTMENT", "csv_no_encontrado")
    _registrar_wo_sin_lista_largos("W.O. 3 X2", "251008-COMPARTMENT", "csv_no_encontrado")
    _registrar_wo_sin_lista_largos("W.O. 4 X2", "251008-COMPARTMENT", "csv_no_encontrado")

    assert obtener_wos_sin_lista_largos() == [
        "W.O. 3 X2 · 251008-COMPARTMENT",
        "W.O. 4 X2 · 251008-COMPARTMENT",
    ]

    reiniciar_avisos_lista_largos()
    assert obtener_wos_sin_lista_largos() == []


if __name__ == "__main__":
    test_autodxf_sin_csv_no_resuelve_lista()
    test_estados_sin_lista_son_avisos()
    test_avisos_se_acumulan_sin_duplicar()
    print("SMOKE OK")
