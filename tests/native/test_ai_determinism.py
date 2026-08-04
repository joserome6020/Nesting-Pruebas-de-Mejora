#!/usr/bin/env python
"""Determinismo Burke + preserve_order (punto 4 IA)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    os.environ["ARGA_NEST_CORE"] = "1"
    os.environ["ARGA_NEST_WORKER"] = "0"
    os.environ["ARGA_NEST_AI"] = "0"
    os.environ["ARGA_NEST_GA_SEED"] = "42"

    from benchmarks.corpus_loader import load_scenario
    from modules.nesting_engine import arga_nest_core_bridge as bridge
    from modules.nesting_engine.algorithm_bridge import _piece_to_native

    st = bridge.core_status()
    assert st.get("active"), st
    print("CORE", st.get("version"))

    params, piezas = load_scenario("s5_tight_order")
    native = [_piece_to_native(p) for p in piezas]
    IN = 25.4

    def once():
        req = bridge.prepare_pack_request(
            plate_w=params["plate_w_in"] * IN,
            plate_h=params["plate_h_in"] * IN,
            pieces=native,
            kerf=params["kerf_in"],
            margin=params.get("margin_in") or 0,
            engine="burke_blf",
            enable_tabu=False,
            rank_order=True,
            extra={
                "preserve_order": True,
                "ga_seed": 42,
                "enable_sa_refine": False,
                "hill_climb_iterations": 1,
            },
        )
        raw = bridge.pack_sheet_json(req)
        m = raw.get("metrics") or {}
        return (
            int(m.get("placed_count") or 0),
            round(float(m.get("eficiencia") or 0), 4),
            [p.get("nombre") for p in (req.get("pieces") or [])[:4]],
        )

    a, b = once(), once()
    print("RUN1", a)
    print("RUN2", b)
    assert a == b, (a, b)
    print("AI_DETERMINISM PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
