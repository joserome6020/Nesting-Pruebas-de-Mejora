"""Smoke: un job sin lista de largos se reporta como aviso, no tumba la exportacion.

Casos reales:
- 251008-COMPARTMENT: AutoDXF existe pero solo con DXF → `csv_no_encontrado`.
- GIGA BOARD 5: no hay carpeta AutoDXF bajo MODEL CORE FILES → `autodxf_no_existe`.
- SWO-022 / 9919-11CABINET: export SWO sin CSV; rglob de TANKS congelaba 10+ min
  y luego ContPAQ intentaba una PO vacía.

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
    _buscar_carpeta_job_corporate,
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


def test_corporate_search_no_usa_rglob():
    """SWO-022: rglob sobre TANKS congelaba el export; solo 3 niveles."""
    with tempfile.TemporaryDirectory() as tmp:
        tanks = Path(tmp) / "TANKS"
        decoy = tanks / "PROD" / "CLI" / "OTRO-JOB" / "MODEL CORE FILES" / "W.O. 1 X1"
        decoy.mkdir(parents=True)
        (decoy / "dummy.dxf").write_text("0\nEOF\n")
        job_dir = tanks / "CABINETS" / "9919" / "9919-11CABINET"
        (job_dir / "MODEL CORE FILES" / "AutoDXF").mkdir(parents=True)

        def _rglob_prohibido(self, *args, **kwargs):
            raise AssertionError("rglob sobre TANKS está prohibido")

        with patch.object(Path, "rglob", _rglob_prohibido):
            hallada = _buscar_carpeta_job_corporate("9919-11CABINET", roots=[tanks])

        assert hallada == job_dir


def test_corporate_prefiere_carpeta_con_autodxf():
    """GIGA BOARD 5 (vacía) vs GIGABOARD5 (con AutoDXF), sin rglob."""
    with tempfile.TemporaryDirectory() as tmp:
        tanks = Path(tmp) / "TANKS"
        sin_ad = tanks / "GIGA" / "GIGA" / "GIGA BOARD 5"
        (sin_ad / "MODEL CORE FILES" / "W.O. 5 X3").mkdir(parents=True)
        con_ad = tanks / "GIGA" / "GIGA" / "GIGABOARD5"
        (con_ad / "MODEL CORE FILES" / "AutoDXF").mkdir(parents=True)

        def _rglob_prohibido(self, *args, **kwargs):
            raise AssertionError("rglob sobre TANKS está prohibido")

        with patch.object(Path, "rglob", _rglob_prohibido):
            hallada = _buscar_carpeta_job_corporate("GIGA BOARD 5", roots=[tanks])

        assert hallada is not None
        assert Path(hallada).name == "GIGABOARD5"


def test_swo_sin_csv_omite_lista_sin_rglob():
    """Export SWO-022: carpeta SWO sin AutoDXF → autodxf_no_existe, sin rglob."""
    with tempfile.TemporaryDirectory() as tmp:
        swo_export = Path(tmp) / "S.W.O 22 X1" / "ARGA MODEL CORE"
        swo_export.mkdir(parents=True)
        tanks = Path(tmp) / "TANKS"
        decoy = tanks / "X" / "Y" / "OTRO" / "MODEL CORE FILES" / "deep"
        decoy.mkdir(parents=True)
        (decoy / "a.dxf").write_text("x")

        def _rglob_prohibido(self, *args, **kwargs):
            raise AssertionError("rglob sobre TANKS está prohibido")

        with patch.object(Path, "rglob", _rglob_prohibido):
            with patch(
                "modules.lista_largos_importer.TANKS_CORPORATE_ROOTS",
                (tanks,),
            ):
                resultado = importar_lista_largos_job(
                    job="9919-11CABINET",
                    ruta_exportacion=str(swo_export),
                    db_config={
                        "host": "127.0.0.1",
                        "dbname": "x",
                        "user": "x",
                        "password": "x",
                    },
                )

        assert resultado["ok"] is False
        assert resultado["status"] == "autodxf_no_existe"
        assert resultado["status"] in ESTADOS_LARGOS_SIN_LISTA


if __name__ == "__main__":
    test_autodxf_sin_csv_no_resuelve_lista()
    test_job_sin_carpeta_autodxf_reporta_estado()
    test_estados_sin_lista_son_avisos()
    test_avisos_se_acumulan_sin_duplicar()
    test_corporate_search_no_usa_rglob()
    test_corporate_prefiere_carpeta_con_autodxf()
    test_swo_sin_csv_omite_lista_sin_rglob()
    print("SMOKE OK")
