"""Telemetría unificada de nests (Fase 0 — plan IA).

Escribe JSONL en:
  - ``_logs/ai_nests.jsonl`` (siempre que se pueda)
  - hive Eddie ``ai_telemetry.jsonl`` (compat con ai_heuristic)

Activación: siempre ON para log; el ranker usa ``ARGA_NEST_AI=1``.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]


def _default_log_path() -> Path:
    """
    En frozen (.exe) el _logs debe vivir en data_dir (fuera del bundle temp);
    en dev, sigue en la raíz del repo para no cambiar el flujo local.
    """
    try:
        if getattr(sys, "frozen", False):
            import config as _cfg

            return Path(_cfg.ruta_persistente(os.path.join("_logs", "ai_nests.jsonl")))
    except Exception:
        pass
    return _ROOT / "_logs" / "ai_nests.jsonl"


_DEFAULT_LOG = _default_log_path()


def telemetry_enabled() -> bool:
    v = (os.environ.get("ARGA_NEST_TELEMETRY") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def ai_ranker_enabled() -> bool:
    v = (os.environ.get("ARGA_NEST_AI") or "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def log_path() -> Path:
    env = (os.environ.get("ARGA_NEST_TELEMETRY_PATH") or "").strip()
    return Path(env) if env else _DEFAULT_LOG


def _seed_order_hash(names: list[str]) -> str:
    raw = "|".join(names)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def piece_features(p: dict[str, Any]) -> dict[str, Any]:
    """Features estables por pieza (ranker / telemetría)."""
    area = float(p.get("area") or 0.0)
    rings = p.get("rings") or p.get("poligonos") or []
    n_rings = len(rings) if isinstance(rings, list) else 0
    aspect = 1.0
    peri = 0.0
    bbox_area = 0.0
    n_holes = 0
    try:
        poly = p.get("poly")
        if poly is not None and hasattr(poly, "bounds"):
            minx, miny, maxx, maxy = poly.bounds
            w = max(1e-6, float(maxx - minx))
            h = max(1e-6, float(maxy - miny))
            aspect = max(w, h) / min(w, h)
            bbox_area = w * h
            if hasattr(poly, "length"):
                peri = float(poly.length)
            if hasattr(poly, "interiors"):
                n_holes = len(list(poly.interiors))
            if area <= 0 and hasattr(poly, "area"):
                area = float(poly.area or 0.0)
        elif rings and isinstance(rings[0], (list, tuple)) and len(rings[0]) >= 2:
            xs = [float(pt[0]) for pt in rings[0] if isinstance(pt, (list, tuple)) and len(pt) >= 2]
            ys = [float(pt[1]) for pt in rings[0] if isinstance(pt, (list, tuple)) and len(pt) >= 2]
            if xs and ys:
                w = max(xs) - min(xs)
                h = max(ys) - min(ys)
                if min(w, h) > 1e-6:
                    aspect = max(w, h) / min(w, h)
                bbox_area = max(0.0, w * h)
            n_holes = max(0, n_rings - 1)
            # peri approx outer ring
            if len(xs) >= 2:
                for i in range(len(xs)):
                    j = (i + 1) % len(xs)
                    peri += math.hypot(xs[j] - xs[i], ys[j] - ys[i])
    except Exception:
        pass

    if n_holes <= 0 and n_rings > 1:
        n_holes = n_rings - 1

    fill_ratio = (area / bbox_area) if bbox_area > 1e-9 else 1.0
    fill_ratio = max(0.0, min(1.5, float(fill_ratio)))
    peri_norm = peri / math.sqrt(max(area, 1e-6))
    host_like = 1.0 if (n_holes > 0 or fill_ratio < 0.72) else 0.0

    return {
        "nombre": str(p.get("nombre") or ""),
        "area": area,
        "aspect": float(aspect),
        "n_rings": int(n_rings if n_rings else (1 + n_holes if area > 0 else 0)),
        "n_holes": int(n_holes),
        "peri": float(peri),
        "peri_norm": float(peri_norm),
        "bbox_area": float(bbox_area),
        "fill_ratio": float(fill_ratio),
        "host_like": float(host_like),
        "calibre": str(p.get("calibre") or ""),
        "material": str(p.get("material") or ""),
        "grain_locked": bool(p.get("grain_locked") or False),
    }


def log_nest_event(
    *,
    wo: str = "",
    calibre: str = "",
    material: str = "",
    engine: str = "",
    profile: str = "",
    n_piezas: int = 0,
    n_sheets: int = 0,
    efi: float = 0.0,
    elapsed_ms: float = 0.0,
    remnant_used: bool = False,
    remnant_ids: list[str] | None = None,
    seed_policy: str = "",
    seed_order: list[str] | None = None,
    certify_ok: bool | None = None,
    kerf: float | None = None,
    plate_w: float | None = None,
    plate_h: float | None = None,
    nest_reward: float | None = None,
    source: str = "ans_cpp",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append un evento de nest al JSONL unificado (+ mirror Eddie si aplica)."""
    if not telemetry_enabled():
        return {}

    names = list(seed_order or [])
    rec: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "schema": 1,
        "source": source,
        "wo": wo or "",
        "calibre": calibre or "",
        "material": material or "",
        "engine": engine or "",
        "profile": profile or "",
        "n_piezas": int(n_piezas),
        "n_sheets": int(n_sheets),
        "efi": float(efi or 0.0),
        "elapsed_ms": float(elapsed_ms or 0.0),
        "remnant_used": bool(remnant_used),
        "remnant_ids": list(remnant_ids or []),
        "seed_policy": seed_policy or "",
        "seed_order_hash": _seed_order_hash(names) if names else "",
        "seed_order_sample": names[:24],
        "certify_ok": certify_ok,
        "kerf": kerf,
        "plate_w": plate_w,
        "plate_h": plate_h,
        "nest_reward": float(nest_reward) if nest_reward is not None else float(efi or 0.0),
        "ai_ranker": ai_ranker_enabled(),
    }
    if extra:
        rec["extra"] = extra

    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as ex:
        print(f"[AI-TELEMETRY] write fail: {ex}", flush=True)

    # Mirror mínimo para Eddie learn
    try:
        from .ai_heuristic import record_telemetry as eddie_record

        fake_pieces = [{"nombre": n, "area": 1.0} for n in names[:50]]
        eddie_record(
            fake_pieces or [{"nombre": "x", "area": 1.0}],
            float(efi or 0.0),
            engine_id=engine or "default",
            seed_policy=seed_policy or None,
            nest_reward=rec["nest_reward"],
        )
    except Exception:
        pass

    return rec


def read_events(limit: int = 5000, path: Path | None = None) -> list[dict[str, Any]]:
    p = path or log_path()
    if not p.is_file():
        return []
    out: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
            if len(out) >= limit:
                break
    return out


def summarize(path: Path | None = None) -> dict[str, Any]:
    ev = read_events(path=path)
    if not ev:
        return {"count": 0}
    efis = [float(e.get("efi") or 0) for e in ev if float(e.get("efi") or 0) > 0]
    return {
        "count": len(ev),
        "efi_mean": sum(efis) / len(efis) if efis else 0.0,
        "with_remnant": sum(1 for e in ev if e.get("remnant_used")),
        "path": str(path or log_path()),
    }
