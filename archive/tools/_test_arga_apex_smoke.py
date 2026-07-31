"""Smoke ARGA APEX: is_ready + pack sintético sin crash + engine_id.

No usa DXF de red. Requiere algorithm_cpp con empaquetar_una_hoja_svgnest_ultra.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from shapely.geometry import Polygon  # noqa: E402

from modules.nesting_engine.engine_registry import (  # noqa: E402
    get_engine_meta,
    is_engine_ready,
    list_ui_engine_metas,
    empaquetar_una_hoja_detalle,
)
from modules.nesting_engine.engines.types import PackSheetRequest  # noqa: E402
from modules.nesting_engine.nest_optimization import get_engine_profile  # noqa: E402

IN = 25.4


def _rect(nombre: str, w_in: float, h_in: float) -> dict:
    w, h = w_in * IN, h_in * IN
    poly = Polygon([(0, 0), (w, 0), (w, h), (0, h), (0, 0)])
    return {
        "nombre": nombre,
        "poly": poly,
        "area": float(poly.area),
        "calibre": "11",
        "material": "TEST",
        "marks": None,
    }


def main() -> int:
    os.environ["ARGA_APEX_SMOKE"] = "1"

    meta = get_engine_meta("arga_apex")
    assert meta.display_name == "ARGA APEX", meta.display_name
    assert meta.engine_id == "arga_apex"

    ui_ids = [m.engine_id for m in list_ui_engine_metas()]
    assert "arga_apex" in ui_ids, f"APEX missing from UI metas: {ui_ids}"

    profile = get_engine_profile("arga_apex")
    assert profile.get("lock_profile") is True
    assert profile.get("fast_first") is False
    assert float(profile.get("rotation_step_deg", 0)) == 5.0
    assert profile.get("use_nfp") is True
    assert profile.get("part_in_part") is True
    assert profile.get("apex_cavity_pass") is False
    assert profile.get("apex_venom_polish") is True
    assert int(profile.get("ga_population", 0) or 0) <= 16
    assert int(profile.get("ga_generations", 0) or 0) <= 6
    assert int(profile.get("force_parallel_seeds", 0) or 0) <= 1
    assert float(profile.get("apex_explore_rot_deg", 0) or 0) == 15.0
    assert int(profile.get("apex_explore_gens", 0) or 0) <= 2

    if not is_engine_ready("arga_apex"):
        print("SKIP: arga_apex not ready (C++ Ultra missing)")
        return 0

    piezas = [
        _rect("P-LARGE", 20, 10),
        _rect("P-MED-A", 8, 6),
        _rect("P-MED-B", 8, 6),
        _rect("P-SMALL-1", 3, 2.5),
        _rect("P-SMALL-2", 3, 2.5),
        _rect("P-SMALL-3", 3, 2.5),
    ]
    result = empaquetar_una_hoja_detalle(
        PackSheetRequest(
            piezas=piezas,
            w_placa=60.0 * IN,
            h_placa=48.0 * IN,
            kerf_override=0.15,
            margin_override=0.15,
            opt_override="OPTIMIZAR LARGO Y ANCHO",
            corner_override="INFERIOR IZQUIERDA",
        ),
        engine_id="arga_apex",
    )
    if result.error:
        print("FAIL error:", result.error)
        return 1
    assert result.engine_id == "arga_apex", result.engine_id
    hoja = result.hoja or {}
    assert hoja.get("engine_id") == "arga_apex", hoja.get("engine_id")
    placed = len(hoja.get("piezas") or [])
    print(
        f"OK APEX smoke placed={placed} restos={len(result.restos or [])} "
        f"efi={hoja.get('eficiencia')} elapsed={result.elapsed_s:.2f}s"
    )
    assert placed >= 1, "expected at least one placed piece"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
