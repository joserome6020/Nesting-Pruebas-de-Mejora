"""Ranker L1 + bandit L3-lite de orden de piezas (plan IA punto 4).

- L1: scoring lineal interpretable (schema 2 host_blend).
- L3-lite: ε-greedy entre políticas de orden (sin NN/RL).
- Calibración por pack real (burke_blf / GA mínimo) cuando hay core.

``ARGA_NEST_AI=1`` activa ranking.
``ARGA_NEST_AI_BANDIT=1`` (default ON si AI=1) elige política vía bandit.
El packer C++ permanece dueño del placement.
"""
from __future__ import annotations

import json
import math
import os
import random
import time
from pathlib import Path
from typing import Any

from .ai_telemetry import ai_ranker_enabled, piece_features, read_events

_ROOT = Path(__file__).resolve().parents[2]
_MODEL_PATH = _ROOT / "cache" / "ai_ranker_v1.json"
_BANDIT_PATH = _ROOT / "cache" / "ai_bandit_v1.json"

DEFAULT_WEIGHTS = {
    "schema": 2,
    "policy": "bandit",
    "w_log_area": 1.0,
    "w_aspect": 0.12,
    "w_holes": 0.55,
    "w_host": 0.85,
    "w_fill": -0.35,
    "w_peri": 0.05,
    "w_grain": 0.12,
    "bias": 0.0,
    "epsilon": 0.18,
}

# Políticas de seed_order (L1/L3). No incluye L4.
SEED_POLICIES = (
    "area_desc",
    "host_first",
    "host_blend",
    "aspect_first",
    "eddie",
)

_LAST_POLICY: str = ""


def model_path() -> Path:
    env = (os.environ.get("ARGA_NEST_AI_MODEL") or "").strip()
    return Path(env) if env else _MODEL_PATH


def bandit_path() -> Path:
    env = (os.environ.get("ARGA_NEST_AI_BANDIT_PATH") or "").strip()
    return Path(env) if env else _BANDIT_PATH


def bandit_enabled() -> bool:
    """Bandit ON por defecto cuando AI=1; off con ARGA_NEST_AI_BANDIT=0."""
    if not ai_ranker_enabled():
        return False
    v = (os.environ.get("ARGA_NEST_AI_BANDIT") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def load_weights() -> dict[str, Any]:
    path = model_path()
    w = dict(DEFAULT_WEIGHTS)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k == "schema":
                        w["schema"] = int(v or 2)
                    elif k in ("policy",):
                        w[k] = str(v or "bandit")
                    elif isinstance(v, (int, float)):
                        w[k] = float(v)
        except Exception:
            pass
    if int(w.get("schema") or 1) < 2:
        w["schema"] = 2
        w.setdefault("policy", "bandit")
        w.setdefault("w_host", DEFAULT_WEIGHTS["w_host"])
        w.setdefault("w_fill", DEFAULT_WEIGHTS["w_fill"])
        w.setdefault("w_peri", DEFAULT_WEIGHTS["w_peri"])
        if float(w.get("w_aspect") or 0) < 0:
            w["w_aspect"] = abs(float(w["w_aspect"])) * 0.5
    return w


def save_weights(weights: dict[str, Any]) -> Path:
    path = model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(DEFAULT_WEIGHTS)
    payload.update(weights)
    payload["schema"] = int(payload.get("schema") or 2)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_bandit() -> dict[str, Any]:
    path = bandit_path()
    base = {
        "schema": 1,
        "policies": {
            p: {"reward_sum": 1.0, "uses": 1, "wins": 0} for p in SEED_POLICIES
        },
        "updated_ts": 0.0,
    }
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("policies"), dict):
                for p in SEED_POLICIES:
                    cur = base["policies"][p]
                    got = raw["policies"].get(p) or {}
                    if isinstance(got, dict):
                        cur["reward_sum"] = float(got.get("reward_sum") or cur["reward_sum"])
                        cur["uses"] = max(1, int(got.get("uses") or cur["uses"]))
                        cur["wins"] = max(0, int(got.get("wins") or 0))
                base["updated_ts"] = float(raw.get("updated_ts") or 0)
        except Exception:
            pass
    return base


def save_bandit(state: dict[str, Any]) -> Path:
    path = bandit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["updated_ts"] = time.time()
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return path


def score_piece(p: dict[str, Any], weights: dict[str, Any] | None = None) -> float:
    w = weights or load_weights()
    f = piece_features(p)
    area = max(1e-6, float(f["area"]))
    holes = max(0, int(f.get("n_holes") or max(0, int(f.get("n_rings") or 1) - 1)))
    s = float(w.get("bias") or 0.0)
    s += float(w.get("w_log_area") or 1.0) * math.log(area)
    s += float(w.get("w_aspect") or 0.0) * math.log(max(1.0, float(f["aspect"])))
    s += float(w.get("w_holes") or 0.0) * float(holes)
    s += float(w.get("w_host") or 0.0) * float(f.get("host_like") or 0.0)
    s += float(w.get("w_fill") or 0.0) * float(f.get("fill_ratio") or 1.0)
    s += float(w.get("w_peri") or 0.0) * float(f.get("peri_norm") or 0.0)
    if f.get("grain_locked"):
        s += float(w.get("w_grain") or 0.0)
    return s


def _rank_host_first(pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hosts: list[tuple[float, int, dict]] = []
    rest: list[tuple[float, int, dict]] = []
    for i, p in enumerate(pieces):
        f = piece_features(p)
        if float(f.get("host_like") or 0.0) > 0.5 or int(f.get("n_holes") or 0) > 0:
            hosts.append((float(f["area"]), i, p))
        else:
            rest.append((float(f["area"]), i, p))
    hosts.sort(key=lambda t: (-t[0], t[1]))
    rest.sort(key=lambda t: (-t[0], t[1]))
    return [p for _, _, p in hosts + rest]


def _rank_aspect_first(pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = []
    for i, p in enumerate(pieces):
        f = piece_features(p)
        scored.append((float(f["aspect"]), float(f["area"]), i, p))
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return [p for _, _, _, p in scored]


def _rank_eddie(pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        from .ai_heuristic import smart_seed_order

        return smart_seed_order(list(pieces), engine_id="ai_ranker")
    except Exception:
        return sorted(pieces, key=lambda p: float(p.get("area") or 0.0), reverse=True)


def rank_pieces(
    pieces: list[dict[str, Any]],
    *,
    weights: dict[str, Any] | None = None,
    policy: str | None = None,
) -> list[dict[str, Any]]:
    """Orden por política / score."""
    w = weights or load_weights()
    pol = str(policy or w.get("policy") or "host_blend").strip().lower()
    if pol == "bandit":
        pol = choose_policy(epsilon=float(w.get("epsilon") or 0.18))
    global _LAST_POLICY
    _LAST_POLICY = pol

    if pol == "host_first":
        return _rank_host_first(pieces)
    if pol == "aspect_first":
        return _rank_aspect_first(pieces)
    if pol == "eddie":
        return _rank_eddie(pieces)
    if pol == "area_desc":
        return sorted(pieces, key=lambda p: float(p.get("area") or 0.0), reverse=True)
    # host_blend (default lineal)
    scored = [(score_piece(p, w), i, p) for i, p in enumerate(pieces)]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [p for _, _, p in scored]


def last_policy() -> str:
    return _LAST_POLICY or str(load_weights().get("policy") or "")


def choose_policy(*, epsilon: float | None = None) -> str:
    """ε-greedy sobre media de reward por política."""
    # Force fijo (benches / debug): ARGA_NEST_AI_POLICY=host_first
    forced = (os.environ.get("ARGA_NEST_AI_POLICY") or "").strip().lower()
    if forced in SEED_POLICIES:
        return forced
    w = load_weights()
    if epsilon is None:
        env_eps = (os.environ.get("ARGA_NEST_AI_EPSILON") or "").strip()
        if env_eps:
            try:
                epsilon = float(env_eps)
            except ValueError:
                epsilon = None
    eps = float(epsilon if epsilon is not None else w.get("epsilon") or 0.18)
    state = load_bandit()
    if random.random() < max(0.0, min(1.0, eps)):
        return random.choice(SEED_POLICIES)
    best_p = "host_blend"
    best_s = -1e18
    for p, st in (state.get("policies") or {}).items():
        if p not in SEED_POLICIES:
            continue
        usos = max(1, int(st.get("uses") or 1))
        score = float(st.get("reward_sum") or 0.0) / usos
        if score > best_s:
            best_s = score
            best_p = p
    return best_p


def record_policy_reward(policy: str, reward: float, *, win: bool = False) -> dict[str, Any]:
    """Actualiza bandit tras un nest (reward tipicamente efi o nest_reward)."""
    pol = str(policy or "").strip().lower()
    if pol not in SEED_POLICIES:
        if pol.startswith("ai_"):
            pol = pol.replace("ai_", "", 1)
        if pol not in SEED_POLICIES:
            pol = "host_blend"
    state = load_bandit()
    st = state["policies"].setdefault(pol, {"reward_sum": 0.0, "uses": 0, "wins": 0})
    st["reward_sum"] = float(st.get("reward_sum") or 0.0) + float(reward)
    st["uses"] = int(st.get("uses") or 0) + 1
    if win:
        st["wins"] = int(st.get("wins") or 0) + 1
    save_bandit(state)
    return state


def maybe_rank_pieces(pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Si ARGA_NEST_AI=1 aplica ranker (±bandit); si no, largest-first."""
    if not pieces:
        return []
    if not ai_ranker_enabled():
        return sorted(pieces, key=lambda p: float(p.get("area") or 0.0), reverse=True)
    forced = (os.environ.get("ARGA_NEST_AI_POLICY") or "").strip().lower()
    if forced in SEED_POLICIES:
        return rank_pieces(pieces, policy=forced)
    w = load_weights()
    if bandit_enabled() or str(w.get("policy") or "") == "bandit":
        return rank_pieces(pieces, weights={**w, "policy": "bandit"})
    return rank_pieces(pieces, weights=w)


def train_from_telemetry(min_events: int = 8) -> dict[str, Any]:
    """Ajuste de pesos + refuerzo bandit desde JSONL."""
    ev = [e for e in read_events() if float(e.get("nest_reward") or e.get("efi") or 0) > 0.5]
    if len(ev) < min_events:
        return {
            "ok": False,
            "reason": f"need>={min_events} events with reward, have={len(ev)}",
            "weights": load_weights(),
        }

    rewards = [float(e.get("nest_reward") or e.get("efi") or 0) for e in ev]
    mean_r = sum(rewards) / len(rewards)
    rem_frac = sum(1 for e in ev if e.get("remnant_used")) / len(ev)

    w = load_weights()
    w["schema"] = 2
    if mean_r >= 50:
        w["w_log_area"] = min(1.6, float(w.get("w_log_area") or 1.0) + 0.04)
        w["w_host"] = min(1.4, float(w.get("w_host") or 0.85) + 0.03)
    else:
        w["w_log_area"] = max(0.55, float(w.get("w_log_area") or 1.0) - 0.04)
        w["w_aspect"] = min(0.45, float(w.get("w_aspect") or 0.12) + 0.02)
        w["w_fill"] = max(-0.6, float(w.get("w_fill") or -0.35) - 0.02)
    if rem_frac > 0.2:
        w["w_holes"] = min(0.8, float(w.get("w_holes") or 0.55) + 0.03)

    # Alimentar bandit con eventos que declaran seed_policy
    n_bandit = 0
    for e in ev[-80:]:
        pol = str(e.get("seed_policy") or "")
        if not pol:
            continue
        # Normalizar nombres tipo ai_host_blend / host_blend
        for cand in SEED_POLICIES:
            if cand in pol:
                record_policy_reward(cand, float(e.get("nest_reward") or e.get("efi") or 0))
                n_bandit += 1
                break

    # Preferir bandit como política activa
    w["policy"] = "bandit"
    path = save_weights(w)
    return {
        "ok": True,
        "n_events": len(ev),
        "mean_reward": mean_r,
        "remnant_frac": rem_frac,
        "bandit_updates": n_bandit,
        "weights": w,
        "path": str(path),
        "bandit": load_bandit(),
    }


def _order_signature(pieces: list[dict[str, Any]], n: int = 8) -> tuple[str, ...]:
    return tuple(str(p.get("nombre") or "") for p in pieces[:n])


def calibrate_on_pieces(
    piece_sets: list[list[dict[str, Any]]],
    *,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Calibración rápida por proxy (sin packer)."""
    base = dict(DEFAULT_WEIGHTS)
    base["policy"] = "host_blend"
    cands = candidates or [
        dict(base),
        {**base, "policy": "host_first"},
        {**base, "w_host": 1.2, "w_holes": 0.7, "w_aspect": 0.05},
        {**base, "w_host": 0.5, "w_aspect": 0.25, "w_fill": -0.2},
        {**base, "w_log_area": 1.3, "w_host": 0.7, "w_aspect": 0.08},
        {**base, "policy": "aspect_first"},
    ]

    def proxy(ordered: list[dict[str, Any]]) -> float:
        if not ordered:
            return 0.0
        score = 0.0
        n = len(ordered)
        for i, p in enumerate(ordered):
            f = piece_features(p)
            pos = 1.0 - (i / max(1, n - 1))
            if float(f.get("host_like") or 0) > 0.5:
                score += 1.4 * pos
            if float(f.get("aspect") or 1) >= 3.0:
                score += 0.35 * pos
            score += 0.15 * pos * math.log(max(1.0, float(f["area"])))
        return score

    best_w = dict(base)
    best_score = -1e18
    for cand in cands:
        total = 0.0
        for pcs in piece_sets:
            ordered = rank_pieces(pcs, weights=cand, policy=str(cand.get("policy") or "host_blend"))
            total += proxy(ordered)
            area_ord = sorted(pcs, key=lambda p: float(p.get("area") or 0.0), reverse=True)
            if _order_signature(ordered) != _order_signature(area_ord):
                total += 0.25
        if total > best_score:
            best_score = total
            best_w = dict(cand)

    # Mantener policy=bandit en disco; pesos lineales del mejor host_blend-like
    out = dict(best_w)
    out["policy"] = "bandit"
    path = save_weights(out)
    return {
        "ok": True,
        "best_proxy": best_score,
        "weights": out,
        "path": str(path),
        "n_candidates": len(cands),
        "n_sets": len(piece_sets),
    }


def calibrate_default_corpus() -> dict[str, Any]:
    try:
        from benchmarks.corpus_loader import list_scenarios, load_scenario
        from modules.nesting_engine.algorithm_bridge import _piece_to_native
    except Exception as ex:
        return {"ok": False, "reason": str(ex)}

    wanted = ["s0_micro", "s2_host_fill", "s3_strips", "s4_mixed_qty", "s5_tight_order", "s6_host_wins"]
    available = set(list_scenarios())
    sets: list[list[dict[str, Any]]] = []
    for sid in wanted:
        if sid not in available:
            continue
        _params, piezas = load_scenario(sid)
        sets.append([_piece_to_native(p) for p in piezas])
    if not sets:
        return {"ok": False, "reason": "no corpus scenarios"}
    return calibrate_on_pieces(sets)


def calibrate_by_pack(
    scenarios: list[str] | None = None,
    *,
    engine: str = "burke_blf",
    ga_population: int = 1,
    ga_generations: int = 1,
) -> dict[str, Any]:
    """
    Elige política que maximiza (placed, efi) en packs reales.
    Prefiere burke_blf (más sensible al orden) o Ultra con GA mínimo.
    Actualiza bandit + pesos.
    """
    try:
        from benchmarks.corpus_loader import list_scenarios, load_scenario
        from modules.nesting_engine import arga_nest_core_bridge as bridge
        from modules.nesting_engine.algorithm_bridge import _piece_to_native
    except Exception as ex:
        return {"ok": False, "reason": str(ex)}

    if not bridge.core_available():
        return {"ok": False, "reason": "core unavailable", "fallback": calibrate_default_corpus()}

    wanted = scenarios or [
        "s5_tight_order",
        "s6_host_wins",
        "s2_host_fill",
        "s0_micro",
        "s4_mixed_qty",
    ]
    available = set(list_scenarios())
    ids = [s for s in wanted if s in available]
    if not ids:
        return {"ok": False, "reason": "no scenarios"}

    IN_TO_MM = 25.4
    policy_scores: dict[str, float] = {p: 0.0 for p in SEED_POLICIES}
    detail: list[dict[str, Any]] = []

    old_ai = os.environ.get("ARGA_NEST_AI")
    old_bandit = os.environ.get("ARGA_NEST_AI_BANDIT")
    os.environ["ARGA_NEST_AI"] = "1"
    os.environ["ARGA_NEST_AI_BANDIT"] = "0"  # fijar política manual
    os.environ.setdefault("ARGA_NEST_WORKER", "0")
    os.environ.setdefault("ARGA_NEST_CORE", "1")

    try:
        for sid in ids:
            params, piezas = load_scenario(sid)
            native = [_piece_to_native(p) for p in piezas]
            best_local = None
            for pol in SEED_POLICIES:
                ordered = rank_pieces(native, policy=pol)
                req = bridge.prepare_pack_request(
                    plate_w=float(params["plate_w_in"]) * IN_TO_MM,
                    plate_h=float(params["plate_h_in"]) * IN_TO_MM,
                    pieces=ordered,
                    kerf=float(params["kerf_in"]),
                    margin=float(params.get("margin_in") or 0.0),
                    engine=engine,
                    profile="first",
                    ga_population=ga_population,
                    ga_generations=ga_generations,
                    enable_tabu=False,
                    rank_order=False,  # ya ordenamos
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
                metrics = raw.get("metrics") or {}
                placed = int(metrics.get("placed_count") or 0)
                efi = float(metrics.get("eficiencia") or 0.0)
                # Aprovechamiento primero (efi), luego cobertura (placed)
                score = efi * 100.0 + placed * 8.0 - ms * 0.0001
                policy_scores[pol] += score
                row = {
                    "scenario": sid,
                    "policy": pol,
                    "placed": placed,
                    "efi": efi,
                    "ms": round(ms, 2),
                    "score": score,
                }
                detail.append(row)
                if best_local is None or score > best_local["score"]:
                    best_local = row
            if best_local:
                record_policy_reward(best_local["policy"], best_local["efi"], win=True)
                for pol in SEED_POLICIES:
                    if pol == best_local["policy"]:
                        continue
                    # reward suave a no-ganadores del escenario
                    match = next(
                        (d for d in detail if d["scenario"] == sid and d["policy"] == pol),
                        None,
                    )
                    if match:
                        record_policy_reward(pol, float(match["efi"]) * 0.5)
    finally:
        if old_ai is None:
            os.environ.pop("ARGA_NEST_AI", None)
        else:
            os.environ["ARGA_NEST_AI"] = old_ai
        if old_bandit is None:
            os.environ.pop("ARGA_NEST_AI_BANDIT", None)
        else:
            os.environ["ARGA_NEST_AI_BANDIT"] = old_bandit

    best_pol = max(policy_scores, key=lambda p: policy_scores[p])
    w = load_weights()
    w["policy"] = "bandit"
    w["epsilon"] = 0.12
    # Prior fuerte al ganador del calibrate-by-pack
    for _ in range(5):
        record_policy_reward(best_pol, 80.0, win=True)
    if best_pol in ("host_first", "host_blend"):
        w["w_host"] = min(1.4, float(w.get("w_host") or 0.85) + 0.05)
        w["w_holes"] = min(0.9, float(w.get("w_holes") or 0.55) + 0.05)
    elif best_pol == "aspect_first":
        w["w_aspect"] = min(0.5, float(w.get("w_aspect") or 0.12) + 0.08)
    path = save_weights(w)

    return {
        "ok": True,
        "best_policy": best_pol,
        "policy_scores": policy_scores,
        "detail": detail,
        "weights": w,
        "path": str(path),
        "bandit": load_bandit(),
        "engine": engine,
    }
