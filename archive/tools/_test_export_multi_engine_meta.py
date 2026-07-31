"""Smoke: export/trazabilidad no rompe con metadatos de cualquier motor."""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "interface")]

from modules.nesting_engine.nest_engine_context import STEEL_ENGINE_IDS, UI_STEEL_ENGINE_IDS
from modules.nesting_engine.resultados_grupos import (
    es_grupo_material_nesting,
    iter_grupos_material,
    primer_grupo_con_hojas,
)


def _fake_resultados(engine_id: str) -> dict:
    """Simula un nest de acero + metadatos típicos del motor activo."""
    return {
        "0.25_A 36": {
            "hojas": [
                {
                    "placa_id": "PLC084",
                    "placa_w": 3048.0,
                    "placa_h": 1219.2,
                    "precio_placa": 100.0,
                    "eficiencia": 72.5,
                    "ignorar_deduccion": False,
                    "es_retazo": False,
                    "piezas": [
                        {
                            "nombre": "W.O. 1 X1__62135-TEST-P01",
                            "area": 10000.0,
                            "calibre": "0.25",
                            "material": "A 36",
                        }
                    ],
                }
            ],
            "costo_total": 100.0,
        },
        # Metadatos que ya rompieron export SWO al hacer info.get(...):
        "_nest_engine_id": engine_id,
        "_nest_compare_note": "selected_from_parallel",
        # Ruido adicional posible en futuros motores / fallos parciales:
        "error": "no debe tratarse como grupo",  # string (caso apply_selected vacío)
    }


def main() -> int:
    engines = list(dict.fromkeys([*STEEL_ENGINE_IDS, *UI_STEEL_ENGINE_IDS, "arga_lite"]))
    print("engines=", engines)

    for eid in engines:
        res = _fake_resultados(eid)
        grupos = list(iter_grupos_material(res))
        assert len(grupos) == 1, f"{eid}: esperado 1 grupo, got {grupos!r}"
        assert es_grupo_material_nesting("0.25_A 36", res["0.25_A 36"])
        assert not es_grupo_material_nesting("_nest_engine_id", res["_nest_engine_id"])
        assert not es_grupo_material_nesting("error", res["error"])
        hoja, clave = primer_grupo_con_hojas(res)
        assert hoja is not None and clave == "0.25_A 36", f"{eid}: primer hoja falló"

        # Camino de costeo sin DB (el que fallaba en trazabilidad SWO):
        from utils_nesting import generar_csv_compras

        with tempfile.TemporaryDirectory() as tmp:
            # Sin job_data CSV → ok; sin db_config → salta trazabilidad BD.
            generar_csv_compras(
                tmp,
                "S.W.O 01 X1",
                res,
                ruta_destino=tmp,
                es_swo=True,
                db_config=None,
            )
        print(f"OK {eid}")

    # Comparación: seleccionar resultado de un motor y exportar
    from modules.nesting_engine.engine_compare import EngineComparisonBundle, apply_selected_engine

    bundle = EngineComparisonBundle(
        runs={eid: _fake_resultados(eid) for eid in ("arga_force", "burke_blf", "svgnest_ultra", "arga_lite")},
        selected_engine_id="svgnest_ultra",
    )
    picked = apply_selected_engine(bundle, "arga_lite")
    assert len(list(iter_grupos_material(picked))) == 1
    from utils_nesting import generar_csv_compras

    with tempfile.TemporaryDirectory() as tmp:
        generar_csv_compras(tmp, "S.W.O 01 X1", picked, ruta_destino=tmp, es_swo=True)
    print("OK compare->apply_selected->csv")

    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
