"""Candado: nombre VSM compacto vs carpeta corporate con espacios.

Caso real: VSM = GIGABOARD5, carpeta duplicada sin AutoDXF = «GIGA BOARD 5»,
carpeta con AutoDXF = GIGABOARD5. Descarga SWO e import de largos deben resolver
la carpeta que sí tiene AutoDXF.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))

from modules.lista_largos_importer import (  # noqa: E402
    _job_compact,
    _jobs_equivalentes,
)


def test_jobs_equivalentes_ignora_espacios_vsm():
    assert _job_compact("GIGA BOARD 5") == "GIGABOARD5"
    assert _job_compact("GIGABOARD5") == "GIGABOARD5"
    assert _jobs_equivalentes("GIGA BOARD 5", "GIGABOARD5")
    assert _jobs_equivalentes("GIGABOARD5", "GIGA BOARD 5")
    assert not _jobs_equivalentes("GIGA BOARD 5", "GIGA BOARD 6")


def test_obtener_ruta_real_job_prefiere_autodxf():
    from interface.qt.tabs.tab_files import TabFiles

    with tempfile.TemporaryDirectory() as tmp:
        # Misma jerarquía que la red: RAIZ/TANKS/<cliente>/<job>
        raiz = Path(tmp)
        cli = raiz / "TANKS" / "GIGA"
        sin_ad = cli / "GIGA BOARD 5"
        (sin_ad / "MODEL CORE FILES" / "W.O. 5 X3").mkdir(parents=True)
        con_ad = cli / "GIGABOARD5"
        (con_ad / "MODEL CORE FILES" / "AutoDXF").mkdir(parents=True)
        (con_ad / "MODEL CORE FILES" / "AutoDXF" / "FB-10-10-A.dxf").write_text("0\nEOF\n")

        tab = TabFiles.__new__(TabFiles)
        hallada = tab.obtener_ruta_real_job(str(raiz), "GIGA BOARD 5")
        assert hallada is not None
        assert Path(hallada).name == "GIGABOARD5"
        assert (Path(hallada) / "MODEL CORE FILES" / "AutoDXF").is_dir()


def test_job_duplicado_producto_elige_autodxf_con_piezas():
    """SWO-058: job 25432 en ATC (AutoDXF vacío de piezas) y TANKS (con DXF).

    Sin hint, la primera carpeta alfabética (ATC) ganaba y la descarga fallaba
    con «No se encontró archivos .dxf». Con items_hint / búsqueda multi-ruta
    debe resolver TANKS.
    """
    from interface.qt.tabs.tab_files import TabFiles

    with tempfile.TemporaryDirectory() as tmp:
        raiz = Path(tmp)
        atc_ad = (
            raiz / "ATC_COMPARTMENT" / "VANTRAN" / "25432" / "MODEL CORE FILES" / "AutoDXF"
        )
        tanks_ad = raiz / "TANKS" / "VANTRAN" / "25432" / "MODEL CORE FILES" / "AutoDXF"
        atc_ad.mkdir(parents=True)
        tanks_ad.mkdir(parents=True)
        (atc_ad / "OTHER PART, A 36, QTY 1, Cal 0.25.dxf").write_text("0\nEOF\n")
        (tanks_ad / "CUADRO BASE, A 36, QTY 4, Cal 0.375.dxf").write_text("0\nEOF\n")
        (tanks_ad / "Inspection_Plate, A 36, QTY 1, Cal 0.313.dxf").write_text("0\nEOF\n")

        tab = TabFiles.__new__(TabFiles)
        rutas = tab.obtener_rutas_reales_job(str(raiz), "25432")
        assert len(rutas) == 2, rutas
        assert any("ATC_COMPARTMENT" in r for r in rutas)
        assert any("TANKS" in r.replace("\\", "/") for r in rutas)

        # Sin hint: puede devolver ATC (alfabético). Con hint de piezas TANKS gana.
        elegida = tab.obtener_ruta_real_job(
            str(raiz),
            "25432",
            items_hint=["CUADRO BASE", "Inspection_Plate"],
        )
        assert elegida is not None
        assert "TANKS" in elegida.replace("\\", "/")

        hit = tab._buscar_dxf_item_en_jobs(rutas, "CUADRO BASE")
        assert hit, "debe hallar DXF en TANKS aunque ATC vaya primero"
        assert "TANKS" in hit.replace("\\", "/")
        assert "CUADRO BASE" in Path(hit).name


def test_ruta_exportacion_bd_fuerza_producto_tanks():
    """VSM/BD: ruta_exportacion bajo TANKS manda aunque ATC también tenga DXF."""
    from interface.qt.tabs.tab_files import TabFiles

    with tempfile.TemporaryDirectory() as tmp:
        raiz = Path(tmp)
        atc_job = raiz / "ATC_COMPARTMENT" / "VANTRAN" / "25432"
        tanks_job = raiz / "TANKS" / "VANTRAN" / "25432"
        (atc_job / "MODEL CORE FILES" / "AutoDXF").mkdir(parents=True)
        (tanks_job / "MODEL CORE FILES" / "AutoDXF").mkdir(parents=True)
        (atc_job / "MODEL CORE FILES" / "AutoDXF" / "CUADRO BASE, A 36, QTY 1, Cal 0.375.dxf").write_text(
            "0\nEOF\n"
        )
        (tanks_job / "MODEL CORE FILES" / "AutoDXF" / "CUADRO BASE, A 36, QTY 4, Cal 0.375.dxf").write_text(
            "0\nEOF\n"
        )
        ruta_export = str(
            tanks_job / "MODEL CORE FILES" / "W.O. 62 X6" / "ARGA MODEL CORE"
        )
        (tanks_job / "MODEL CORE FILES" / "W.O. 62 X6" / "ARGA MODEL CORE").mkdir(
            parents=True, exist_ok=True
        )

        tab = TabFiles.__new__(TabFiles)
        root = tab._job_root_desde_ruta_exportacion(ruta_export)
        assert Path(root).resolve() == tanks_job.resolve()
        prod, cli = tab._producto_cliente_desde_job_root(root)
        assert prod == "TANKS"
        assert cli == "VANTRAN"

        elegida = tab.obtener_ruta_real_job(
            str(raiz),
            "25432",
            prefer_ruta=root,
            product_hint="TANKS",
            # ATC también tiene la pieza: sin prefer_ruta ganaría por score ambiguo
            items_hint=["CUADRO BASE"],
        )
        assert "TANKS" in elegida.replace("\\", "/")

        rutas = tab.obtener_rutas_reales_job(str(raiz), "25432")
        hit = tab._buscar_dxf_item_en_jobs(
            rutas, "CUADRO BASE", prefer_ruta=root, product_hint="TANKS"
        )
        assert "TANKS" in hit.replace("\\", "/")

        items = [
            ("W.O. 62 X6__CUADRO BASE", "A 36", "24", "0.375", "LISTO", hit),
        ]
        val = tab._validar_origen_swo(
            prefer_ruta=root, product_hint="TANKS", items=items
        )
        assert val["producto"] == "TANKS"
        assert val["cliente"] == "VANTRAN"
        assert val["mismatch"] == 0
        assert any("TANKS" in k for k in val["origenes"])


if __name__ == "__main__":
    test_jobs_equivalentes_ignora_espacios_vsm()
    test_obtener_ruta_real_job_prefiere_autodxf()
    test_job_duplicado_producto_elige_autodxf_con_piezas()
    test_ruta_exportacion_bd_fuerza_producto_tanks()
    print("SMOKE OK")
