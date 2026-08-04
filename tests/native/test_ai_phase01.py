#!/usr/bin/env python
"""Smoke Fase 0–1–3lite: telemetría + ranker + bandit.

Prefer Python 3.14 (arga_nest_core.cp314).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    os.environ["ARGA_NEST_TELEMETRY"] = "1"
    os.environ["ARGA_NEST_AI"] = "1"
    os.environ["ARGA_NEST_AI_BANDIT"] = "1"
    os.environ["ARGA_NEST_CORE"] = "1"
    os.environ["ARGA_NEST_WORKER"] = "0"
    os.environ.pop("ARGA_NEST_AI_POLICY", None)
    os.environ["ARGA_NEST_AI_EPSILON"] = "0"

    from modules.nesting_engine import ai_ranker, ai_telemetry
    from modules.nesting_engine import arga_nest_core_bridge as bridge

    pieces = [
        {"nombre": "big", "area": 9000, "rings": [[(0, 0), (90, 0), (90, 100), (0, 100), (0, 0)]]},
        {"nombre": "small", "area": 1000, "rings": [[(0, 0), (20, 0), (20, 50), (0, 50), (0, 0)]]},
        {"nombre": "mid", "area": 4000, "rings": [[(0, 0), (40, 0), (40, 100), (0, 100), (0, 0)]]},
        {
            "nombre": "host",
            "area": 5000,
            "rings": [
                [(0, 0), (80, 0), (80, 70), (0, 70), (0, 0)],
                [(20, 20), (50, 20), (50, 45), (20, 45), (20, 20)],
            ],
        },
    ]
    feats = ai_telemetry.piece_features(pieces[3])
    assert feats.get("n_holes", 0) >= 1 or feats.get("host_like", 0) > 0.5

    host_first = ai_ranker.rank_pieces(pieces, policy="host_first")
    assert host_first[0]["nombre"] == "host"
    print("HOST_FIRST", [p["nombre"] for p in host_first])

    aspect = ai_ranker.rank_pieces(pieces, policy="aspect_first")
    print("ASPECT", [p["nombre"] for p in aspect])

    # Bandit choose + reward
    pol = ai_ranker.choose_policy(epsilon=0.0)
    assert pol in ai_ranker.SEED_POLICIES
    ai_ranker.record_policy_reward(pol, 55.0, win=True)
    st = ai_ranker.load_bandit()
    assert st["policies"][pol]["uses"] >= 1
    print("BANDIT", pol, st["policies"][pol])

    ranked = ai_ranker.maybe_rank_pieces(pieces)
    names = [p["nombre"] for p in ranked]
    print("RANKED", names, "policy", ai_ranker.last_policy())
    assert "small" in names
    # Con bandit exploit, small no debe liderar
    assert names[0] != "small"

    rec = ai_telemetry.log_nest_event(
        wo="TEST",
        efi=42.5,
        nest_reward=42.5,
        n_piezas=4,
        n_sheets=1,
        seed_order=names,
        seed_policy=f"ai_{ai_ranker.last_policy() or 'host_blend'}",
        certify_ok=True,
        source="test_ai_phase01",
    )
    assert rec.get("seed_order_hash")

    for efi in (30.0, 55.0, 60.0, 48.0, 52.0, 70.0, 40.0, 58.0):
        ai_telemetry.log_nest_event(
            wo="SYN",
            efi=efi,
            nest_reward=efi,
            n_piezas=4,
            seed_order=names,
            seed_policy="ai_host_blend",
            source="test_synth",
        )
    tr = ai_ranker.train_from_telemetry(min_events=5)
    print("TRAIN", {k: tr.get(k) for k in ("ok", "n_events", "bandit_updates")})
    assert tr.get("ok") is True

    cal = ai_ranker.calibrate_on_pieces([pieces])
    assert cal.get("ok") is True

    req = bridge.prepare_pack_request(
        plate_w=400,
        plate_h=300,
        pieces=pieces,
        kerf=0.2,
        profile="first",
        ga_population=4,
        ga_generations=3,
        enable_tabu=False,
    )
    assert req.get("seed_policy")
    lead = [p["nombre"] for p in req["pieces"]][0]
    assert lead != "small"

    st_core = bridge.core_status()
    if not st_core.get("active"):
        print("PACK SKIP (core inactive):", st_core.get("load_error"))
        print("AI_PHASE01 PASS (telemetry+ranker+bandit)")
        return 0

    r = bridge.pack_sheet_json(req)
    assert (r.get("certify") or {}).get("ok") is True
    print("PACK", r.get("metrics"))
    print("AI_PHASE01 PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
