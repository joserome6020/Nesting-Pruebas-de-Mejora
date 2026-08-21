"""Candado: feedstock STEP complemento (discover + prefs + placa plana → DXF)."""
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
            is_step_feedstock_enabled,
            load_nest_runtime_prefs,
            save_nest_runtime_prefs,
            set_step_feedstock_enabled,
        )

        prefs = load_nest_runtime_prefs()
        assert prefs.get("step_feedstock_enabled") is False, prefs
        assert is_step_feedstock_enabled() is False

        set_step_feedstock_enabled(True)
        assert is_step_feedstock_enabled() is True
        # Guardado parcial no debe apagar el switch.
        save_nest_runtime_prefs({"prefer": "local", "cu_force_dxf_step": False})
        assert is_step_feedstock_enabled() is True
        set_step_feedstock_enabled(False)
        assert is_step_feedstock_enabled() is False

        from modules.tank_step_feedstock.discover import (
            FROM_STEP_DIRNAME,
            discover_steps_in_autodxf,
            pick_primary_step,
        )

        autodxf = Path(temp_dir) / "JOB1" / "MODEL CORE FILES" / "AutoDXF"
        (autodxf / "STEP").mkdir(parents=True)
        (autodxf / "Cal 0.25 A 36").mkdir(parents=True)
        decoy = autodxf / "Cal 0.25 A 36" / "no.stp"
        decoy.write_bytes(b"not-a-real-step")
        root_step = autodxf / "tank_root.stp"
        root_step.write_bytes(b"ISO-10303-21;")
        nested = autodxf / "STEP" / "tank_nested.step"
        nested.write_bytes(b"ISO-10303-21;")
        # FROM_STEP no debe contaminar el discovery.
        (autodxf / FROM_STEP_DIRNAME).mkdir(parents=True)
        (autodxf / FROM_STEP_DIRNAME / "junk.stp").write_bytes(b"x")

        found = discover_steps_in_autodxf(autodxf)
        names = {p.name.lower() for p in found}
        assert "tank_root.stp" in names, names
        assert "tank_nested.step" in names, names
        assert "no.stp" not in names, names
        assert "junk.stp" not in names, names
        primary = pick_primary_step(autodxf)
        assert primary is not None

        # OCCT: caja placa → DXF IV_*
        try:
            import OCP  # noqa: F401
        except ImportError:
            print("SMOKE OK (sin OCP: solo prefs/discover)")
            return

        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

        box = BRepPrimAPI_MakeBox(10.0, 4.0, 0.25).Solid()
        step_out = autodxf / "STEP" / "plate_box.stp"
        writer = STEPControl_Writer()
        writer.Transfer(box, STEPControl_AsIs)
        status = writer.Write(str(step_out))
        assert status == IFSelect_RetDone, status

        from modules.tank_step_feedstock import process_autodxf_step_feedstock

        result = process_autodxf_step_feedstock(autodxf, step_path=step_out)
        assert result.ok, result.message
        assert result.exports, result.message
        dxf = result.exports[0].dxf_path
        assert dxf.is_file(), dxf
        text = dxf.read_text(encoding="utf-8", errors="ignore").upper()
        assert "IV_OUTER_PROFILE" in text, "faltan capas Inventor"
        assert str(FROM_STEP_DIRNAME) in str(dxf).replace("\\", "/")

    if previous_data_dir is None:
        os.environ.pop("ARGA_NEST_DATA_DIR", None)
    else:
        os.environ["ARGA_NEST_DATA_DIR"] = previous_data_dir
    print("SMOKE OK")


if __name__ == "__main__":
    main()
