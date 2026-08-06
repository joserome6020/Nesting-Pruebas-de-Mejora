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


if __name__ == "__main__":
    test_jobs_equivalentes_ignora_espacios_vsm()
    test_obtener_ruta_real_job_prefiere_autodxf()
    print("SMOKE OK")
