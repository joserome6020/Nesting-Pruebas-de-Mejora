"""Candado: NvidiaSpark inicia local y solo se habilita al guardar Auto."""
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
    with tempfile.TemporaryDirectory() as temp_dir:
        os.environ["ARGA_NEST_DATA_DIR"] = temp_dir
        from modules.nesting_engine.nest_runtime_prefs import (
            load_nest_runtime_prefs,
            save_nest_runtime_prefs,
        )

        prefs = load_nest_runtime_prefs()
        assert prefs["prefer"] == "local", prefs
        assert prefs["spark"]["host"] == "192.168.2.35"
        assert prefs.get("exportar_a_servidor") is True, prefs

        save_nest_runtime_prefs(
            {
                "prefer": "auto",
                "spark": {"host": "127.0.0.1", "port": 9876},
                "exportar_a_servidor": False,
            }
        )
        enabled = load_nest_runtime_prefs()
        assert enabled["prefer"] == "auto", enabled
        assert enabled["spark"]["host"] == "127.0.0.1"
        assert enabled["spark"]["port"] == 9876
        assert enabled["exportar_a_servidor"] is False, enabled

        from modules.nesting_engine.nest_runtime_prefs import (
            is_exportar_a_servidor_enabled,
            set_exportar_a_servidor,
        )

        set_exportar_a_servidor(True)
        assert is_exportar_a_servidor_enabled() is True
        set_exportar_a_servidor(False)
        assert is_exportar_a_servidor_enabled() is False

        # Guardado parcial (Config Global) no debe pisar exportar_a_servidor.
        save_nest_runtime_prefs({"prefer": "local", "cu_force_dxf_step": False})
        assert load_nest_runtime_prefs()["exportar_a_servidor"] is False

    if previous_data_dir is None:
        os.environ.pop("ARGA_NEST_DATA_DIR", None)
    else:
        os.environ["ARGA_NEST_DATA_DIR"] = previous_data_dir
    print("SMOKE OK")


if __name__ == "__main__":
    main()
