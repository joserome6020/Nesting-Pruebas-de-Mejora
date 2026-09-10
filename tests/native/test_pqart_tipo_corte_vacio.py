"""Candado: guardar nesting migra CHECK tipo_corte vacío antes de PQART."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))


def test_guardar_llama_asegurar_pqart():
    from interface import postgres_connector as pc

    src = inspect.getsource(pc.guardar_nesting_en_postgresql)
    assert "_asegurar_tablas_pqart(cursor)" in src, (
        "guardar_nesting_en_postgresql debe migrar chk_pqart_*_tipo_corte "
        "antes de INSERT con tipo_corte=''"
    )
    # El CHECK canónico admite vacío (NESTEO DXF ya no clasifica canal).
    src_aseg = inspect.getsource(pc._asegurar_tablas_pqart)
    assert "''" in src_aseg
    assert "CamaLaser" in src_aseg


def test_normalizar_tipo_corte_vacio():
    from modules.nesting_engine.exporter import _normalizar_tipo_corte_pqart

    assert _normalizar_tipo_corte_pqart("NESTEO DXF") == ""
    assert _normalizar_tipo_corte_pqart("ROBOT LASER + MINI NEST") == ""


def main() -> None:
    test_guardar_llama_asegurar_pqart()
    test_normalizar_tipo_corte_vacio()
    print("OK test_pqart_tipo_corte_vacio")


if __name__ == "__main__":
    main()
