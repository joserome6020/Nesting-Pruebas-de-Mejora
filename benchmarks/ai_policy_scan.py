"""Quick policy scan for one scenario."""
from __future__ import annotations

import os
import sys
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
sid = sys.argv[1] if len(sys.argv) > 1 else "s6_host_wins"
params, piezas = load_scenario(sid)
native = [_piece_to_native(p) for p in piezas]
print(sid)
for pol in SEED_POLICIES:
    ordered = rank_pieces(native, policy=pol)
    req = bridge.prepare_pack_request(
        plate_w=params["plate_w_in"] * IN,
        plate_h=params["plate_h_in"] * IN,
        pieces=ordered,
        kerf=params["kerf_in"],
        margin=params.get("margin_in") or 0,
        engine="burke_blf",
        enable_tabu=False,
        rank_order=False,
        extra={
            "preserve_order": True,
            "ga_seed": 42,
            "enable_sa_refine": False,
            "hill_climb_iterations": 1,
        },
    )
    m = bridge.pack_sheet_json(req).get("metrics") or {}
    pl = int(m.get("placed_count") or 0)
    efi = float(m.get("eficiencia") or 0)
    seed = [p.get("nombre") for p in ordered[:3]]
    print(f"  {pol:12} pl={pl:2} efi={efi:6.2f} seed={seed}")
