"""Colmena de nests — DB de episodios para ML real (APEX / motores).

Preferencia red:
  \\\\SERVER-ARGA\\ArgaNesting\\hive_mind_nests
Fallback:
  cache/hive_mind_nests

Cada línea JSONL = un nest/renest con fingerprint + métricas.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HIVE_NEST_PATHS = [
    Path(r"\\SERVER-ARGA\ArgaNesting\hive_mind_nests"),
    Path(__file__).resolve().parents[2] / "cache" / "hive_mind_nests",
]

SCHEMA_VERSION = 1


def _resolve_hive_dir() -> Path:
    for path in HIVE_NEST_PATHS:
        try:
            if path.exists() or path.parent.exists():
                path.mkdir(parents=True, exist_ok=True)
                return path
        except OSError:
            continue
        except Exception:
            continue
    local = Path(__file__).resolve().parents[2] / "cache" / "hive_mind_nests"
    local.mkdir(parents=True, exist_ok=True)
    return local


def hive_nests_dir() -> Path:
    return _resolve_hive_dir()


def _piece_area(p: dict) -> float:
    try:
        return float(p.get("area") or 0.0)
    except Exception:
        return 0.0


def _piece_holes(p: dict) -> int:
    poly = p.get("poly") or p.get("poly_exact")
    try:
        if poly is not None and hasattr(poly, "interiors"):
            return len(list(poly.interiors or []))
    except Exception:
        pass
    rings = p.get("poligonos") or []
    return max(0, len(rings) - 1) if rings else 0


def fingerprint_batch(
    piezas: list,
    *,
    w_placa: float = 0.0,
    h_placa: float = 0.0,
    kerf: float = 0.15,
) -> dict[str, Any]:
    """Firma estable del lote (para kNN / similitud)."""
    areas = sorted(_piece_area(p) for p in (piezas or []) if isinstance(p, dict))
    holes = sorted(_piece_holes(p) for p in (piezas or []) if isinstance(p, dict))
    n = len(piezas or [])
    area_sum = sum(areas)
    holes_sum = sum(holes)
    # Buckets para comparar lotes parecidos sin IDs de pieza.
    area_buckets = [0, 0, 0, 0]  # chica / med / grande / xl
    for a in areas:
        ain = a / (25.4 * 25.4)  # mm² → in² aprox
        if ain < 20:
            area_buckets[0] += 1
        elif ain < 80:
            area_buckets[1] += 1
        elif ain < 200:
            area_buckets[2] += 1
        else:
            area_buckets[3] += 1
    raw = {
        "n": n,
        "area_sum": round(area_sum, 1),
        "holes_sum": holes_sum,
        "buckets": area_buckets,
        "w": round(float(w_placa or 0), 1),
        "h": round(float(h_placa or 0), 1),
        "kerf": round(float(kerf or 0), 3),
    }
    digest = hashlib.sha1(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    raw["fp"] = digest
    return raw


def _efi_norm(hoja: dict) -> float:
    efi = float((hoja or {}).get("eficiencia", 0) or 0)
    if efi > 1.5:
        efi = efi / 100.0
    return max(0.0, min(1.0, efi))


def save_nest_episode(
    *,
    engine_id: str,
    piezas_in: list,
    hoja: dict,
    restos: list,
    w_placa: float,
    h_placa: float,
    kerf: float,
    elapsed_s: float,
    seed_policy: str | None = None,
    occt_stats: dict | None = None,
    cuda_used: bool = False,
    extra: dict | None = None,
) -> Path | None:
    """Append JSONL episode. Returns path written or None."""
    if str(os.environ.get("ARGA_HIVE_NESTS", "1")).strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return None

    fp = fingerprint_batch(
        piezas_in, w_placa=w_placa, h_placa=h_placa, kerf=kerf
    )
    placed = len((hoja or {}).get("piezas") or [])
    pending = len(restos or [])
    efi = _efi_norm(hoja or {})
    # Reward: eficiencia * fill_ratio - tiempo normalizado suave
    fill = placed / max(1, placed + pending)
    reward = (efi * 0.7 + fill * 0.3) - min(0.15, float(elapsed_s or 0) / 600.0)

    episode: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "ts": datetime.now(timezone.utc).isoformat(),
        "engine_id": str(engine_id or ""),
        "fingerprint": fp,
        "placed": placed,
        "restos": pending,
        "efficiency": efi,
        "elapsed_s": round(float(elapsed_s or 0), 2),
        "reward": round(reward, 4),
        "seed_policy": seed_policy or "",
        "cuda": bool(cuda_used),
        "occt": dict(occt_stats or {}),
        "kerf": float(kerf or 0),
        "placa_w": float(w_placa or 0),
        "placa_h": float(h_placa or 0),
    }
    if extra:
        episode["extra"] = dict(extra)

    base = _resolve_hive_dir()
    out = base / "nests.jsonl"
    try:
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(episode, ensure_ascii=False) + "\n")
        # Índice rápido por fingerprint
        idx = base / "by_fp" / f"{fp['fp']}.jsonl"
        idx.parent.mkdir(parents=True, exist_ok=True)
        with open(idx, "a", encoding="utf-8") as f:
            f.write(json.dumps(episode, ensure_ascii=False) + "\n")
        print(
            f"[HIVE-NESTS] saved reward={episode['reward']:.3f} "
            f"efi={efi:.3f} placed={placed}/{placed+pending} "
            f"fp={fp['fp']} -> {out}",
            flush=True,
        )
        return out
    except Exception as exc:
        print(f"[HIVE-NESTS] save fail: {exc}", flush=True)
        return None


def _distance(a: dict, b: dict) -> float:
    """Distancia simple entre fingerprints."""
    try:
        dn = abs(int(a.get("n", 0)) - int(b.get("n", 0))) / 50.0
        da = abs(float(a.get("area_sum", 0)) - float(b.get("area_sum", 0))) / max(
            1.0, float(a.get("area_sum", 0)) + float(b.get("area_sum", 0))
        )
        dh = abs(int(a.get("holes_sum", 0)) - int(b.get("holes_sum", 0))) / 50.0
        ba = a.get("buckets") or [0, 0, 0, 0]
        bb = b.get("buckets") or [0, 0, 0, 0]
        db = sum(abs(int(x) - int(y)) for x, y in zip(ba, bb)) / 20.0
        return dn + da + dh + db
    except Exception:
        return 99.0


def load_recent_episodes(limit: int = 400) -> list[dict]:
    path = _resolve_hive_dir() / "nests.jsonl"
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    if limit > 0 and len(rows) > limit:
        return rows[-limit:]
    return rows


def suggest_seed_policy(
    piezas: list,
    *,
    w_placa: float = 0.0,
    h_placa: float = 0.0,
    kerf: float = 0.15,
    k: int = 8,
) -> dict[str, Any]:
    """
    kNN sobre nests pasados: sugiere seed_policy con mejor reward medio.
    Si no hay datos → host_parasite (bueno para VFM/huecos).
    """
    fp = fingerprint_batch(piezas, w_placa=w_placa, h_placa=h_placa, kerf=kerf)
    hist = load_recent_episodes(500)
    if not hist:
        return {
            "policy": "host_parasite",
            "confidence": 0.0,
            "neighbors": 0,
            "reason": "cold_start",
            "fingerprint": fp,
        }

    scored = []
    for ep in hist:
        efp = ep.get("fingerprint") or {}
        if not isinstance(efp, dict):
            continue
        d = _distance(fp, efp)
        scored.append((d, ep))
    scored.sort(key=lambda x: x[0])
    neighbors = [ep for d, ep in scored[: max(1, k)] if d < 1.5]
    if not neighbors:
        neighbors = [ep for _, ep in scored[:3]]

    # Voto ponderado por reward
    votes: dict[str, float] = {}
    for ep in neighbors:
        pol = str(ep.get("seed_policy") or "").strip() or "area_class"
        w = max(0.05, float(ep.get("reward") or 0.0) + 0.2)
        votes[pol] = votes.get(pol, 0.0) + w

    if not votes:
        best = "host_parasite"
        conf = 0.0
    else:
        best = max(votes.items(), key=lambda kv: kv[1])[0]
        total = sum(votes.values()) or 1.0
        conf = float(votes[best]) / total

    # Sesgo: muchos huecos → host_parasite
    if int(fp.get("holes_sum") or 0) >= max(8, int(fp.get("n") or 1)):
        if conf < 0.55:
            best = "host_parasite"
            conf = max(conf, 0.4)

    return {
        "policy": best,
        "confidence": round(conf, 3),
        "neighbors": len(neighbors),
        "reason": "knn",
        "fingerprint": fp,
        "votes": {k: round(v, 3) for k, v in votes.items()},
    }


def force_eddie_policy(engine_id: str, policy: str) -> None:
    """Empuja la próxima elección de Eddie hacia `policy` (recompensa temporal)."""
    try:
        from .ai_heuristic import DEFAULT_WEIGHTS, load_weights, save_weights

        weights = load_weights(engine_id)
        policies = dict(weights.get("seed_policies") or DEFAULT_WEIGHTS["seed_policies"])
        for name in list(policies.keys()):
            st = dict(policies.get(name) or {})
            if name == policy:
                st["recompensa_acumulada"] = float(st.get("recompensa_acumulada", 1)) + 3.0
                st["usos"] = max(1, int(st.get("usos", 1)))
            policies[name] = st
        weights["seed_policies"] = policies
        weights["exploracion"] = min(0.12, float(weights.get("exploracion", 0.25) or 0.25))
        weights["_apex_force_policy"] = policy
        weights["_apex_force_ts"] = time.time()
        save_weights(weights, engine_id)
    except Exception as exc:
        print(f"[HIVE-NESTS] force_policy skip: {exc}", flush=True)


def publish_nest_learning(
    *,
    engine_id: str,
    hoja: dict,
    restos: list | None = None,
    piezas_in: list | None = None,
    elapsed_s: float = 0.0,
    seed_policy: str | None = None,
    occt_stats: dict | None = None,
    cuda_used: bool = False,
    extra: dict | None = None,
) -> None:
    """
    Publica un nest a la colmena + telemetría Eddie.
    Pensado para CUALQUIER motor (llamado desde Venom / manager).
    """
    if hoja.get("_hive_nest_published"):
        return
    try:
        from .ai_heuristic import (
            ai_learn_from_feedback,
            get_last_seed_info,
            record_telemetry,
        )

        piezas = list(piezas_in or hoja.get("piezas") or [])
        restos_l = list(restos if restos is not None else [])
        w = float(hoja.get("placa_w", 0) or 0)
        h = float(hoja.get("placa_h", 0) or 0)
        kerf = float(hoja.get("kerf_usado", 0.15) or 0.15)
        pol = seed_policy or str(
            (hoja.get("apex_ml") or {}).get("policy")
            or get_last_seed_info(engine_id).get("policy")
            or ""
        )
        efi = float(hoja.get("eficiencia", 0) or 0)
        record_telemetry(
            piezas,
            efi,
            engine_id=engine_id,
            post_venom_efficiency=efi,
            compactness_pre=hoja.get("venom_compactness_pre"),
            compactness_post=hoja.get("venom_compactness_post"),
            seed_policy=pol or None,
            nest_reward=hoja.get("venom_reward"),
        )
        save_nest_episode(
            engine_id=engine_id,
            piezas_in=piezas,
            hoja=hoja,
            restos=restos_l,
            w_placa=w,
            h_placa=h,
            kerf=kerf,
            elapsed_s=float(elapsed_s or hoja.get("apex_elapsed_s") or 0),
            seed_policy=pol,
            occt_stats=occt_stats or hoja.get("apex_occt"),
            cuda_used=bool(cuda_used or hoja.get("apex_cuda")),
            extra=extra or {"source": "venom_or_shared"},
        )
        ai_learn_from_feedback()
        hoja["_hive_nest_published"] = True
        print(f"[HIVE-NESTS] published engine={engine_id} policy={pol or '-'}", flush=True)
    except Exception as exc:
        print(f"[HIVE-NESTS] publish skip: {exc}", flush=True)
