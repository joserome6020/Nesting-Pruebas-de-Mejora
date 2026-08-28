"""Candados SWO-043: match exacto DXF + reproceso conserva prefijo W.O.__.

Caso real: BUSHING PATCH TAPA vs BUSHING PATCH TAPA 2 — el startswith con espacio
hacía que la pieza corta tomara el DXF de la larga (suplantación / doble en nest).
REPROCESAR AUTODXF reescribía PARTS con el stem crudo y quitaba `W.O. N XN__`.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))

from interface.autodxf_metadata import (  # noqa: E402
    dxf_corresponde_a_item,
    item_sin_prefijo_wo,
)


def test_tapa_no_suplanta_tapa_2():
    tapa = "BUSHING PATCH TAPA, INOXIDABLE, QTY 18, CAL 0.1875.dxf"
    tapa2 = "BUSHING PATCH TAPA 2, A 36, QTY 6, CAL 0.1875.dxf"
    assert dxf_corresponde_a_item(tapa, "BUSHING PATCH TAPA")
    assert dxf_corresponde_a_item(tapa2, "BUSHING PATCH TAPA 2")
    assert not dxf_corresponde_a_item(tapa2, "BUSHING PATCH TAPA")
    assert not dxf_corresponde_a_item(tapa, "BUSHING PATCH TAPA 2")


def test_legacy_underscore_tab_sigue_ok():
    assert dxf_corresponde_a_item(
        "62135-1251-P03_TAB, A 36, QTY 1, CAL 0.25.dxf",
        "62135-1251-P03",
    )
    assert dxf_corresponde_a_item(
        "62135-1251-P03_TAB, A 36, QTY 1, CAL 0.25.dxf",
        "62135-1251-P03_TAB",
    )


def test_item_sin_prefijo_wo():
    assert (
        item_sin_prefijo_wo("W.O. 47 X6__BUSHING PATCH TAPA")
        == "BUSHING PATCH TAPA"
    )
    assert (
        item_sin_prefijo_wo("W.O. 47 X6__BUSHING PATCH TAPA 2")
        == "BUSHING PATCH TAPA 2"
    )
    assert item_sin_prefijo_wo("BUSHING PATCH TAPA") == "BUSHING PATCH TAPA"
    assert item_sin_prefijo_wo("25430-Placa Segmento 2") == "25430-Placa Segmento 2"


def test_buscar_dxf_prefiere_exacto_no_hermano_mas_largo():
    from interface.qt.tabs.tab_files import TabFiles

    with tempfile.TemporaryDirectory() as tmp:
        autodxf = Path(tmp) / "AutoDXF"
        proc = autodxf / "Processed Files"
        proc.mkdir(parents=True)
        # Orden en disco: primero el hermano largo (como el bug viejo).
        (proc / "BUSHING PATCH TAPA 2, A 36, QTY 6, CAL 0.1875.dxf").write_text(
            "0\nEOF\n", encoding="utf-8"
        )
        (proc / "BUSHING PATCH TAPA, INOXIDABLE, QTY 18, CAL 0.1875.dxf").write_text(
            "0\nEOF\n", encoding="utf-8"
        )
        tab = TabFiles.__new__(TabFiles)
        hallada = tab._buscar_dxf_item_en_autodxf(str(autodxf), "BUSHING PATCH TAPA")
        assert hallada
        assert os.path.basename(hallada).startswith("BUSHING PATCH TAPA,")
        assert "TAPA 2" not in os.path.basename(hallada)


def test_reproceso_swo_conserva_prefijo_wo_y_qty():
    from interface.qt.tabs.tab_files import TabFiles

    tab = TabFiles.__new__(TabFiles)
    tab.app = SimpleNamespace(
        job_activo="SWO-043",
        multiplicador_tanques=1,
        datos_partes_actuales=[
            (
                "W.O. 47 X6__BUSHING PATCH TAPA",
                "INOXIDABLE",
                "18",
                "0.1875",
                "LISTO",
                r"\\srv\old\BUSHING PATCH TAPA, INOXIDABLE.dxf",
            ),
            (
                "W.O. 47 X6__BUSHING PATCH TAPA 2",
                "A 36",
                "6",
                "0.1875",
                "LISTO",
                r"\\srv\old\BUSHING PATCH TAPA 2, A 36.dxf",
            ),
        ],
        meta_pdf_por_ruta={
            tab._normalizar_ruta(r"\\srv\old\BUSHING PATCH TAPA, INOXIDABLE.dxf"): {
                "job": "25400",
                "item": "BUSHING PATCH TAPA",
                "work_order": "W.O. 47 X6",
            },
            tab._normalizar_ruta(r"\\srv\old\BUSHING PATCH TAPA 2, A 36.dxf"): {
                "job": "25400",
                "item": "BUSHING PATCH TAPA 2",
                "work_order": "W.O. 47 X6",
            },
        },
        vista_nesting=None,
        editable_inputs_actuales=None,
        editable_inputs_by_lote=None,
    )

    loaded = []

    def _cargar(items, thumbnails_async=False):
        loaded.clear()
        loaded.extend(items)

    tab.app.cargar_datos_parts = _cargar

    scanned = [
        ("BUSHING PATCH TAPA", "INOXIDABLE", "1", "0.1875", "LISTO", r"\\srv\new\tapa.dxf"),
        ("BUSHING PATCH TAPA 2", "A 36", "1", "0.1875", "LISTO", r"\\srv\new\tapa2.dxf"),
    ]
    payload = {
        "items": scanned,
        "meta_pdf": {
            tab._normalizar_ruta(r"\\srv\new\tapa.dxf"): {
                "job": "25400",
                "item": "BUSHING PATCH TAPA",
            },
            tab._normalizar_ruta(r"\\srv\new\tapa2.dxf"): {
                "job": "25400",
                "item": "BUSHING PATCH TAPA 2",
            },
        },
        "job_name": "25400",
        "multiplicador": 3,
    }
    n = tab.aplicar_partes_resincronizadas(payload)
    assert n == 2
    assert tab.app.job_activo == "SWO-043"
    assert loaded[0][0] == "W.O. 47 X6__BUSHING PATCH TAPA"
    assert loaded[0][2] == "18"
    assert loaded[0][5].endswith("tapa.dxf")
    assert loaded[1][0] == "W.O. 47 X6__BUSHING PATCH TAPA 2"
    assert loaded[1][2] == "6"
    meta = tab.app.meta_pdf_por_ruta[tab._normalizar_ruta(r"\\srv\new\tapa.dxf")]
    assert meta.get("work_order") == "W.O. 47 X6"


if __name__ == "__main__":
    test_tapa_no_suplanta_tapa_2()
    test_legacy_underscore_tab_sigue_ok()
    test_item_sin_prefijo_wo()
    test_buscar_dxf_prefiere_exacto_no_hermano_mas_largo()
    test_reproceso_swo_conserva_prefijo_wo_y_qty()
    print("OK test_swo_autodxf_item_match")
