"""Candado: cobre forzado a DXF+STEP desactiva sin_gap / RTZCU / Amada nest."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    previous_data_dir = os.environ.get("ARGA_NEST_DATA_DIR")
    previous_env = os.environ.get("ARGA_CU_FORCE_DXF_STEP")
    with tempfile.TemporaryDirectory() as temp_dir:
        os.environ["ARGA_NEST_DATA_DIR"] = temp_dir
        os.environ.pop("ARGA_CU_FORCE_DXF_STEP", None)

        from modules.nesting_engine.cu_largos_nesting import (
            _familia_corte_cu,
            _modo_separacion_barra,
            _pieza_cu_forzar_sin_gap,
        )
        from modules.nesting_engine.cu_rtz_sin_gap import (
            aplica_rtz_sin_gap,
            asignar_rtz_cu_sin_gap_ids,
        )
        from modules.nesting_engine.exporter import (
            _hoja_cobre_es_especial,
            _hoja_cobre_export_3d,
        )
        from modules.nesting_engine.nest_runtime_prefs import (
            is_cu_force_dxf_step_enabled,
            load_nest_runtime_prefs,
            save_nest_runtime_prefs,
        )

        prefs0 = load_nest_runtime_prefs()
        assert prefs0.get("cu_force_dxf_step") is False, prefs0
        assert is_cu_force_dxf_step_enabled() is False

        # Pieza especial PARTS: en modo normal fuerza sin_gap / Amada.
        especial = {
            "nombre": "CU_ESP",
            "cu_especial_vertical": True,
            "poligonos": [[(0, 0), (100, 0), (100, 50), (0, 50), (0, 0)]],
        }
        assert _pieza_cu_forzar_sin_gap(especial) is True
        assert _familia_corte_cu(especial) == "amada"
        assert _modo_separacion_barra([especial]) == "sin_gap"
        assert aplica_rtz_sin_gap("sin_gap") is True

        save_nest_runtime_prefs({"cu_force_dxf_step": True})
        assert is_cu_force_dxf_step_enabled() is True
        assert _pieza_cu_forzar_sin_gap(especial) is False
        assert _familia_corte_cu(especial) == "guillotina"
        assert _modo_separacion_barra([especial]) == "con_gap"
        assert aplica_rtz_sin_gap("sin_gap") is False

        hoja = {
            "modo_largos_cu": True,
            "cu_modo_separacion_barra": "sin_gap",
            "cu_barra_especial": True,
            "piezas": [especial],
            "cu_rtz_activo": True,
            "cu_rtz_id": "RTZCU1-H1",
        }
        assert _hoja_cobre_es_especial(hoja) is False
        assert _hoja_cobre_export_3d(hoja) == "step"

        resultados = {
            "0.250_CU": {
                "modo_largos_cu": True,
                "hojas": [
                    dict(hoja),
                    {
                        "modo_largos_cu": True,
                        "cu_rtz_virtual": True,
                        "placa_id": "RTZCU1-H1",
                        "piezas": [],
                    },
                ],
            }
        }
        n = asignar_rtz_cu_sin_gap_ids(resultados)
        assert n == 0
        hojas = resultados["0.250_CU"]["hojas"]
        assert len(hojas) == 1
        assert hojas[0].get("cu_rtz_activo") is False
        assert hojas[0].get("cu_modo_separacion_barra") == "con_gap"
        assert hojas[0].get("export_3d_format") == "step"

        save_nest_runtime_prefs({"cu_force_dxf_step": False})
        assert is_cu_force_dxf_step_enabled() is False

    if previous_data_dir is None:
        os.environ.pop("ARGA_NEST_DATA_DIR", None)
    else:
        os.environ["ARGA_NEST_DATA_DIR"] = previous_data_dir
    if previous_env is None:
        os.environ.pop("ARGA_CU_FORCE_DXF_STEP", None)
    else:
        os.environ["ARGA_CU_FORCE_DXF_STEP"] = previous_env
    print("SMOKE OK")


if __name__ == "__main__":
    main()
