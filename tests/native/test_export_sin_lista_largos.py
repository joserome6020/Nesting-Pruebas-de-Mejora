"""Smoke: un job sin lista de largos se reporta como aviso, no tumba la exportacion.

Casos reales:
- 251008-COMPARTMENT: AutoDXF existe pero solo con DXF → `csv_no_encontrado`.
- GIGA BOARD 5: no hay carpeta AutoDXF bajo MODEL CORE FILES → `autodxf_no_existe`.

Antes ambos abortaban el multi-lote entero en la etapa PostgreSQL/PQART aunque
DXF/PDF/.arganest ya estuvieran en disco.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))

from modules.lista_largos_importer import (
    _resolver_csv_lista_largos,
    importar_lista_largos_job,
)
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


def test_job_sin_carpeta_autodxf_reporta_estado():
    """Caso GIGA BOARD 5: solo existe MODEL CORE FILES/W.O…, sin AutoDXF."""
    with tempfile.TemporaryDirectory() as tmp:
        wo_export = (
            Path(tmp)
            / "GIGA BOARD 5"
            / "MODEL CORE FILES"
            / "W.O. 5 X3"
            / "ARGA MODEL CORE"
        )
        wo_export.mkdir(parents=True)

        with patch(
            "modules.lista_largos_importer._buscar_carpeta_job_corporate",
            return_value=None,
        ):
            resultado = importar_lista_largos_job(
                job="GIGA BOARD 5",
                ruta_exportacion=str(wo_export),
                db_config={"host": "127.0.0.1", "dbname": "x", "user": "x", "password": "x"},
            )

        assert resultado["ok"] is False
        assert resultado["status"] == "autodxf_no_existe"
        assert "AutoDXF" in str(resultado.get("ruta_autodxf") or "")


def test_estados_sin_lista_son_avisos():
    assert "csv_no_encontrado" in ESTADOS_LARGOS_SIN_LISTA
    assert "csv_vacio" in ESTADOS_LARGOS_SIN_LISTA
    assert "autodxf_no_existe" in ESTADOS_LARGOS_SIN_LISTA


def test_avisos_se_acumulan_sin_duplicar():
    reiniciar_avisos_lista_largos()
    _registrar_wo_sin_lista_largos("W.O. 3 X2", "251008-COMPARTMENT", "csv_no_encontrado")
    _registrar_wo_sin_lista_largos("W.O. 3 X2", "251008-COMPARTMENT", "csv_no_encontrado")
    _registrar_wo_sin_lista_largos("W.O. 4 X2", "251008-COMPARTMENT", "csv_no_encontrado")
    _registrar_wo_sin_lista_largos("W.O. 5 X3", "GIGA BOARD 5", "autodxf_no_existe")

    assert obtener_wos_sin_lista_largos() == [
        "W.O. 3 X2 · 251008-COMPARTMENT",
        "W.O. 4 X2 · 251008-COMPARTMENT",
        "W.O. 5 X3 · GIGA BOARD 5",
    ]

    reiniciar_avisos_lista_largos()
    assert obtener_wos_sin_lista_largos() == []


if __name__ == "__main__":
    test_autodxf_sin_csv_no_resuelve_lista()
    test_job_sin_carpeta_autodxf_reporta_estado()
    test_estados_sin_lista_son_avisos()
    test_avisos_se_acumulan_sin_duplicar()
    print("SMOKE OK")
