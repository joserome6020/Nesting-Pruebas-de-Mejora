"""
Módulo de IA Heurística — "Eddie" (Sembrado Inteligente y Aprendizaje Continuo)

Semillas de orden para el packer. Aprende sobre *políticas de sembrado*
comparables (mismo tipo de señal), no sobre la composición del WO.

Retroalimenta con Venom vía compactación post-polish (bbox / free-rect).
"""
from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path
from typing import Any

# --- MENTE COLMENA DE EDDIE ---
HIVE_MIND_PATHS = [
    Path(r"\\SERVER-ARGA\ArgaNesting\hive_mind_eddie"),
    Path(__file__).parent.parent.parent / "cache" / "hive_mind_eddie",
]

WEIGHTS_SCHEMA_VERSION = 2

DEFAULT_WEIGHTS = {
    "schema_version": WEIGHTS_SCHEMA_VERSION,
    "parasites_per_host": 2,
    "structural_priority": 1.0,
    "rectangular_priority": 0.9,
    "mixed_priority": 0.7,
    "huesped_priority": 0.8,
    "parasito_priority": 0.5,
    "desconocida_priority": 0.3,
    "mixta_priority": 0.7,
    "estructural_priority": 1.0,
    "learning_rate": 0.05,
    "area_exponent": 1.0,
    "exploracion": 0.25,
    # Recompensas por política de sembrado (causal).
    "seed_policies": {
        "area_desc": {"recompensa_acumulada": 1.0, "usos": 1},
        "area_class": {"recompensa_acumulada": 1.0, "usos": 1},
        "host_parasite": {"recompensa_acumulada": 1.0, "usos": 1},
        "aspect_ratio": {"recompensa_acumulada": 1.0, "usos": 1},
    },
}

SEED_POLICIES = ("area_desc", "area_class", "host_parasite", "aspect_ratio")

_VALID_CLASSES = frozenset(
    {"estructural", "huesped", "parasito", "rectangular", "mixta", "desconocida"}
)

# Última semilla por engine (para telemetría causal sin cambiar firmas del packer).
_LAST_SEED_INFO: dict[str, dict[str, Any]] = {}


def _get_eddie_memory_path(engine_id: str) -> Path:
    for path in HIVE_MIND_PATHS:
        try:
            if path.exists() or path.parent.exists():
                path.mkdir(parents=True, exist_ok=True)
                return path / f"ai_weights_{engine_id}.json"
        except OSError:
            continue
        except Exception:
            continue
    local = Path(__file__).parent / "eddie_cache"
    local.mkdir(exist_ok=True)
    return local / f"ai_weights_{engine_id}.json"


def _migrate_weights(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Reset/migra pesos corruptos del schema v1 (aprendía composición del WO)."""
    weights = DEFAULT_WEIGHTS.copy()
    weights["seed_policies"] = {
        k: dict(v) for k, v in DEFAULT_WEIGHTS["seed_policies"].items()
    }
    if not isinstance(raw, dict):
        return weights

    ver = int(raw.get("schema_version") or 0)
    if ver < WEIGHTS_SCHEMA_VERSION:
        # Conservar learning_rate / parasites_per_host si son sensatos.
        for key in ("learning_rate", "parasites_per_host", "area_exponent", "exploracion"):
            if key in raw and isinstance(raw[key], (int, float)):
                weights[key] = float(raw[key])
        return weights

    out = dict(weights)
    out.update(raw)
    policies = dict(DEFAULT_WEIGHTS["seed_policies"])
    if isinstance(raw.get("seed_policies"), dict):
        for k, v in raw["seed_policies"].items():
            if k in policies and isinstance(v, dict):
                policies[k] = {
                    "recompensa_acumulada": float(v.get("recompensa_acumulada", 0) or 0),
                    "usos": int(v.get("usos", 0) or 0),
                }
    out["seed_policies"] = policies
    out["schema_version"] = WEIGHTS_SCHEMA_VERSION
    return out


def load_weights(engine_id: str = "default") -> dict[str, Any]:
    mem_path = _get_eddie_memory_path(engine_id)
    raw = None
    if mem_path.exists():
        try:
            with open(mem_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = None
    weights = _migrate_weights(raw)
    if raw is None or int((raw or {}).get("schema_version") or 0) < WEIGHTS_SCHEMA_VERSION:
        save_weights(weights, engine_id)
    return weights


def save_weights(weights: dict[str, Any], engine_id: str = "default"):

    mem_path = _get_eddie_memory_path(engine_id)
    try:
        payload = dict(weights)
        payload["schema_version"] = WEIGHTS_SCHEMA_VERSION
        mem_path.parent.mkdir(parents=True, exist_ok=True)
        with open(mem_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass


def polygon_area(ring: list[tuple[float, float]]) -> float:
    if not ring or len(ring) < 3:
        return 0.0
    area = 0.0
    for i in range(len(ring)):
        p1 = ring[i]
        p2 = ring[(i + 1) % len(ring)]
        area += (p1[0] * p2[1]) - (p2[0] * p1[1])
    return abs(area) / 2.0


def bounding_box(ring: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    if not ring:
        return 0.0, 0.0, 0.0, 0.0
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def extract_features(piece: dict[str, Any], *, area_p90: float | None = None) -> dict[str, Any]:
    """Clasifica con umbrales relativos al lote cuando hay area_p90."""
    poly = piece.get("poly")
    if poly is not None and hasattr(poly, "exterior"):
        true_area = float(piece.get("area", poly.area) or poly.area or 0.0)
        min_x, min_y, max_x, max_y = poly.bounds
        w = max_x - min_x
        h = max_y - min_y
        bbox_area = w * h
    else:
        rings = piece.get("rings") or piece.get("poligonos") or []
        if not rings:
            return {
                "area": 0.0,
                "bbox_area": 0.0,
                "concavity": 0.0,
                "rectangularity": 0.0,
                "aspect": 1.0,
                "class": "desconocida",
            }

        outer_ring = rings[0]
        true_area = float(piece.get("area", 0.0) or 0.0)
        if true_area <= 0.0:
            true_area = polygon_area(outer_ring)
            for hole in rings[1:]:
                true_area -= polygon_area(hole)

        min_x, min_y, max_x, max_y = bounding_box(outer_ring)
        w = max_x - min_x
        h = max_y - min_y
        bbox_area = w * h

    rectangularity = true_area / bbox_area if bbox_area > 0 else 0.0
    concavity = (bbox_area - true_area) / true_area if true_area > 0 else 0.0
    aspect = (max(w, h) / max(min(w, h), 1e-6)) if w > 0 and h > 0 else 1.0

    # Relativo al lote: top ~10% → estructural; bottom ~15% + chica → parásito.
    p90 = float(area_p90 or 0.0)
    if p90 > 0 and true_area >= 0.85 * p90:
        p_class = "estructural"
    elif concavity > 0.4:
        p_class = "huesped"
    elif rectangularity > 0.75:
        p_class = "rectangular"
    elif p90 > 0 and true_area < 0.08 * p90:
        p_class = "parasito"
    elif true_area < 50000 and (p90 <= 0 or true_area < 0.15 * p90):
        p_class = "parasito"
    else:
        p_class = "mixta"

    return {
        "area": true_area,
        "bbox_area": bbox_area,
        "concavity": concavity,
        "rectangularity": rectangularity,
        "aspect": aspect,
        "class": p_class,
        "void_area": max(0.0, bbox_area - true_area),
    }


def _batch_area_p90(pieces: list[dict[str, Any]]) -> float:
    areas = []
    for p in pieces:
        a = float(p.get("area", 0) or 0)
        if a <= 0 and p.get("poly") is not None and hasattr(p["poly"], "area"):
            try:
                a = float(p["poly"].area)
            except Exception:
                a = 0.0
        if a > 0:
            areas.append(a)
    if not areas:
        return 0.0
    areas.sort()
    idx = min(len(areas) - 1, max(0, int(math.ceil(0.9 * len(areas)) - 1)))
    return float(areas[idx])


def _choose_seed_policy(weights: dict[str, Any]) -> str:
    # One-shot force desde hive_mind_nests (cualquier motor / APEX ML).
    try:
        forced = str(weights.get("_apex_force_policy") or "").strip()
        ts = float(weights.get("_apex_force_ts") or 0)
        if forced in SEED_POLICIES and (time.time() - ts) < 120.0:
            return forced
    except Exception:
        pass

    if random.random() < float(weights.get("exploracion", 0.25) or 0.25):
        return random.choice(SEED_POLICIES)

    best = "area_class"
    best_score = -1.0
    for name, stats in (weights.get("seed_policies") or {}).items():
        if name not in SEED_POLICIES or not isinstance(stats, dict):
            continue
        usos = max(1, int(stats.get("usos", 1) or 1))
        score = float(stats.get("recompensa_acumulada", 0) or 0) / usos
        if score > best_score:
            best_score = score
            best = name
    return best


def _order_area_desc(enriched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(enriched, key=lambda x: x["feat"]["area"], reverse=True)


def _order_area_class(enriched: list[dict[str, Any]], weights: dict[str, Any]) -> list[dict[str, Any]]:
    area_exp = float(weights.get("area_exponent", 1.0) or 1.0)

    def score(item: dict[str, Any]) -> float:
        feat = item["feat"]
        cls = feat["class"]
        class_priority = float(
            weights.get(f"{cls}_priority", weights.get("desconocida_priority", 0.5)) or 0.5
        )
        area = max(float(feat["area"]), 1.0)
        return (area ** area_exp) * class_priority

    return sorted(enriched, key=score, reverse=True)


def _order_aspect_ratio(enriched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Largos/estrechos primero (suelen anclar el layout), luego área.
    return sorted(
        enriched,
        key=lambda x: (x["feat"]["aspect"], x["feat"]["area"]),
        reverse=True,
    )


def _order_host_parasite(
    enriched: list[dict[str, Any]],
    weights: dict[str, Any],
) -> list[dict[str, Any]]:
    """Huéspedes con hueco + parásitos que quepan; estructurales primero."""
    n_parasites = max(1, int(weights.get("parasites_per_host", 2) or 2))
    estructurales = [e for e in enriched if e["feat"]["class"] == "estructural"]
    hosts = [e for e in enriched if e["feat"]["class"] == "huesped"]
    parasites = [e for e in enriched if e["feat"]["class"] == "parasito"]
    rest = [
        e
        for e in enriched
        if e["feat"]["class"] not in ("estructural", "huesped", "parasito")
    ]

    estructurales.sort(key=lambda x: x["feat"]["area"], reverse=True)
    hosts.sort(key=lambda x: (x["feat"]["void_area"], x["feat"]["area"]), reverse=True)
    parasites.sort(key=lambda x: x["feat"]["area"])
    rest.sort(key=lambda x: x["feat"]["area"], reverse=True)

    used_parasite_idx: set[int] = set()
    ordered: list[dict[str, Any]] = []
    ordered.extend(estructurales)

    for host in hosts:
        ordered.append(host)
        void_area = float(host["feat"]["void_area"])
        attached = 0
        for i, par in enumerate(parasites):
            if i in used_parasite_idx:
                continue
            if par["feat"]["area"] <= max(void_area * 0.85, 1.0):
                ordered.append(par)
                used_parasite_idx.add(i)
                attached += 1
                if attached >= n_parasites:
                    break

    for i, par in enumerate(parasites):
        if i not in used_parasite_idx:
            ordered.append(par)
    ordered.extend(rest)
    return ordered


def smart_seed_order(pieces: list[dict[str, Any]], engine_id: str = "default") -> list[dict[str, Any]]:
    """
    Ordena piezas según una política de sembrado (epsilon-greedy).
    """
    if not pieces:
        return []

    weights = load_weights(engine_id)
    policy = _choose_seed_policy(weights)
    p90 = _batch_area_p90(pieces)

    enriched = []
    for i, p in enumerate(pieces):
        feat = extract_features(p, area_p90=p90)
        enriched.append({"idx": i, "piece": p, "feat": feat})

    if policy == "area_desc":
        ordered = _order_area_desc(enriched)
    elif policy == "host_parasite":
        ordered = _order_host_parasite(enriched, weights)
    elif policy == "aspect_ratio":
        ordered = _order_aspect_ratio(enriched)
    else:
        ordered = _order_area_class(enriched, weights)
        policy = "area_class"

    signature: dict[str, int] = {}
    for item in ordered:
        cls = item["feat"]["class"]
        signature[cls] = signature.get(cls, 0) + 1

    _LAST_SEED_INFO[engine_id] = {
        "policy": policy,
        "signature": signature,
        "piece_count": len(pieces),
    }

    try:
        from datetime import datetime

        log_msg = (
            f"[EDDIE-AI] Motor: {engine_id} | Policy: {policy} | "
            f"Piezas: {len(pieces)} | Foco: "
            f"{ordered[0]['feat']['class'] if ordered else 'Ninguno'}"
        )
        print(log_msg)
        log_path = Path(__file__).parent.parent.parent / "_logs" / "AI_ACTIVITY.txt"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {log_msg}\n")
    except Exception:
        pass

    return [item["piece"] for item in ordered]


def get_last_seed_info(engine_id: str = "default") -> dict[str, Any]:
    return dict(_LAST_SEED_INFO.get(engine_id) or {})


def record_telemetry(
    pieces: list[dict[str, Any]],
    efficiency: float,
    engine_id: str = "default",
    post_venom_efficiency: float | None = None,
    *,
    compactness_pre: float | None = None,
    compactness_post: float | None = None,
    seed_policy: str | None = None,
    nest_reward: float | None = None,
):
    """
    Guarda firma + política de sembrado + resultado.
    Ignorar efficiency==0 en el learn; la señal preferida es nest_reward /
    compactness (Venom) o efficiency_real del grupo.
    """
    if not pieces and not seed_policy:
        return

    last = get_last_seed_info(engine_id)
    policy = seed_policy or last.get("policy") or "area_class"

    counts: dict[str, int] = {}
    for p in pieces or []:
        feat = extract_features(p)
        cls = feat["class"]
        if cls in _VALID_CLASSES:
            counts[cls] = counts.get(cls, 0) + 1

    record: dict[str, Any] = {
        "engine_id": engine_id,
        "seed_policy": policy,
        "signature": counts or last.get("signature") or {},
        "piece_count": len(pieces) if pieces else int(last.get("piece_count") or 0),
        "efficiency_pre_venom": float(efficiency or 0.0),
    }

    if post_venom_efficiency is not None:
        record["efficiency_post_venom"] = float(post_venom_efficiency)
        record["venom_improvement"] = float(post_venom_efficiency) - float(efficiency or 0.0)
    if compactness_pre is not None:
        record["compactness_pre"] = float(compactness_pre)
    if compactness_post is not None:
        record["compactness_post"] = float(compactness_post)
        if compactness_pre is not None:
            record["compactness_delta"] = float(compactness_post) - float(compactness_pre)
    if nest_reward is not None:
        record["nest_reward"] = float(nest_reward)

    for base_path in HIVE_MIND_PATHS:
        try:
            base_path.mkdir(parents=True, exist_ok=True)
            telemetry_file = base_path / "ai_telemetry.jsonl"
            with open(telemetry_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            break
        except OSError:
            continue
        except Exception:
            continue


def _record_reward(record: dict[str, Any]) -> float | None:
    """Señal escalar para aprendizaje. None = descartar."""
    if "nest_reward" in record:
        return float(record["nest_reward"])
    if "compactness_delta" in record:
        return float(record["compactness_delta"])
    if "compactness_post" in record:
        return float(record["compactness_post"])
    post = record.get("efficiency_post_venom")
    pre = record.get("efficiency_pre_venom")
    if post is not None:
        return float(post)
    if pre is not None and float(pre) > 0.5:
        return float(pre)
    return None


def ai_learn_from_feedback():
    """
    Ajusta recompensas de *políticas de sembrado* (causal).
    Ya no castiga/premia clases por composición del WO.
    """
    history: list[dict] = []

    for base_path in HIVE_MIND_PATHS:
        try:
            telemetry_file = base_path / "ai_telemetry.jsonl"
            if not telemetry_file.exists():
                continue
            with open(telemetry_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            history.append(json.loads(line))
                        except Exception:
                            pass
            if history:
                break
        except OSError:
            continue
        except Exception:
            continue

    if not history:
        return

    history_by_engine: dict[str, list] = {}
    for r in history:
        e_id = r.get("engine_id", "default")
        history_by_engine.setdefault(e_id, []).append(r)

    for engine_id, engine_history in history_by_engine.items():
        scored: list[tuple[dict, float]] = []
        for r in engine_history[-40:]:
            reward = _record_reward(r)
            if reward is None:
                continue
            scored.append((r, reward))

        if len(scored) < 5:
            continue

        weights = load_weights(engine_id)
        lr = float(weights.get("learning_rate", 0.05) or 0.05)
        policies = weights.setdefault(
            "seed_policies",
            {k: dict(v) for k, v in DEFAULT_WEIGHTS["seed_policies"].items()},
        )

        # Actualizar banda por política con reward medio reciente.
        by_policy: dict[str, list[float]] = {}
        for r, reward in scored:
            pol = str(r.get("seed_policy") or "area_class")
            if pol not in SEED_POLICIES:
                pol = "area_class"
            by_policy.setdefault(pol, []).append(reward)

        for pol, rewards in by_policy.items():
            avg = sum(rewards) / len(rewards)
            slot = policies.setdefault(pol, {"recompensa_acumulada": 0.0, "usos": 0})
            # EMA suave hacia el reward medio (normalizado ~0-100 si es eficiencia).
            slot["recompensa_acumulada"] = float(slot.get("recompensa_acumulada", 0) or 0) + avg
            slot["usos"] = int(slot.get("usos", 0) or 0) + len(rewards)

        # Ajuste leve de area_exponent según reward medio global.
        avg_all = sum(r for _, r in scored) / len(scored)
        current_exp = float(weights.get("area_exponent", 1.0) or 1.0)
        if avg_all < 40.0:
            weights["area_exponent"] = max(
                0.5, min(1.5, current_exp + (lr * 0.5 if current_exp < 1.0 else -lr * 0.5))
            )

        # Decay suave de exploración hacia 0.1.
        exp = float(weights.get("exploracion", 0.25) or 0.25)
        weights["exploracion"] = max(0.1, exp * 0.98)

        save_weights(weights, engine_id)
        print(
            f"[EDDIE-AI] Motor: {engine_id} | Reward medio: {avg_all:.2f} | "
            f"Políticas actualizadas ({len(by_policy)})."
        )
