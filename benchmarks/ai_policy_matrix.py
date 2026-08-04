"""Matriz de políticas en s5_tight_order (orden-sensible)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ARGA_NEST_CORE", "1")
os.environ.setdefault("ARGA_NEST_WORKER", "0")
os.environ["ARGA_NEST_AI"] = "1"
os.environ["ARGA_NEST_AI_BANDIT"] = "0"

from benchmarks.corpus_loader import load_scenario
from modules.nesting_engine import arga_nest_core_bridge as bridge
from modules.nesting_engine.algorithm_bridge import _piece_to_native
from modules.nesting_engine.ai_ranker import SEED_POLICIES, rank_pieces

IN = 25.4


def main() -> int:
    params, piezas = load_scenario("s5_tight_order")
    native = [_piece_to_native(p) for p in piezas]
    print("POLICY MATRIX s5_tight_order burke_blf")
    rows = []
    for pol in SEED_POLICIES:
        ordered = rank_pieces(native, policy=pol)
        req = bridge.prepare_pack_request(
            plate_w=params["plate_w_in"] * IN,
            plate_h=params["plate_h_in"] * IN,
            pieces=ordered,
            kerf=params["kerf_in"],
            margin=params.get("margin_in") or 0,
            engine="burke_blf",
            ga_population=1,
            ga_generations=1,
            enable_tabu=False,
            rank_order=False,
            extra={
                "preserve_order": True,
                "ga_seed": 42,
                "enable_sa_refine": False,
                "hill_climb_iterations": 1,
            },
        )
        t0 = time.perf_counter()
        raw = bridge.pack_sheet_json(req)
        ms = (time.perf_counter() - t0) * 1000.0
        m = raw.get("metrics") or {}
        placed = int(m.get("placed_count") or 0)
        efi = float(m.get("eficiencia") or 0.0)
        seed = [p.get("nombre") for p in ordered[:3]]
        rows.append((pol, placed, efi, ms, seed))
        print(f"  {pol:12} placed={placed:2} efi={efi:6.2f} ms={ms:7.1f} seed={seed}")
    best = max(rows, key=lambda r: (r[2], r[1], -r[3]))
    print(f"BEST_EFI {best[0]} placed={best[1]} efi={best[2]:.2f}")
    best_pl = max(rows, key=lambda r: (r[1], r[2], -r[3]))
    print(f"BEST_PLACED {best_pl[0]} placed={best_pl[1]} efi={best_pl[2]:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
