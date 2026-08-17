"""Preferencias Local / NvidiaSpark (persistidas en ``_config/nest_runtime.json``)."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from modules.nesting_engine.nest_runtime_contract import normalize_prefer


_CONFIG_RELATIVE_PATH = os.path.join("_config", "nest_runtime.json")
_DEFAULTS: dict[str, Any] = {
    "prefer": "local",
    # OFF: cobre normal (sin_gap / RTZCU / Amada vertical según geometría).
    # ON: fuerza gap + DXF/STEP y desactiva RTZCU / CyPTube / fixtura Amada nest.
    "cu_force_dxf_step": False,
    "spark": {
        "host": "192.168.2.35",
        "port": 8765,
        "timeout_s": 600.0,
        "connect_timeout_s": 3.0,
        "label": "NvidiaSpark",
    },
}


def config_path() -> Path:
    try:
        import config as app_config

        return Path(app_config.asegurar_archivo_persistente(_CONFIG_RELATIVE_PATH))
    except Exception:
        return Path(__file__).resolve().parents[2] / _CONFIG_RELATIVE_PATH


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(dict(out[key]), value)
        else:
            out[key] = value
    return out


def load_nest_runtime_prefs() -> dict[str, Any]:
    prefs = copy.deepcopy(_DEFAULTS)
    path = config_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                prefs = _deep_merge(prefs, raw)
        except Exception:
            pass

    # Variables de entorno son el override final para worker/CI/diagnóstico.
    env_prefer = (os.environ.get("ARGA_NEST_RUNTIME") or "").strip()
    env_host = (os.environ.get("ARGA_NEST_SPARK_HOST") or "").strip()
    env_port = (os.environ.get("ARGA_NEST_SPARK_PORT") or "").strip()
    env_cu = (os.environ.get("ARGA_CU_FORCE_DXF_STEP") or "").strip().lower()
    if env_prefer:
        prefs["prefer"] = normalize_prefer(env_prefer)
    if env_host:
        prefs["spark"]["host"] = env_host
    if env_port:
        try:
            prefs["spark"]["port"] = int(env_port)
        except ValueError:
            pass
    if env_cu in ("1", "true", "on", "yes"):
        prefs["cu_force_dxf_step"] = True
    elif env_cu in ("0", "false", "off", "no"):
        prefs["cu_force_dxf_step"] = False

    prefs["prefer"] = normalize_prefer(str(prefs.get("prefer") or "local"))
    prefs["cu_force_dxf_step"] = bool(prefs.get("cu_force_dxf_step"))
    spark = dict(prefs.get("spark") or {})
    for key, value in _DEFAULTS["spark"].items():
        spark.setdefault(key, value)
    prefs["spark"] = spark
    return prefs


def save_nest_runtime_prefs(prefs: dict[str, Any]) -> Path:
    data = _deep_merge(_DEFAULTS, dict(prefs or {}))
    data["prefer"] = normalize_prefer(str(data.get("prefer") or "local"))
    data["cu_force_dxf_step"] = bool(data.get("cu_force_dxf_step"))
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def is_cu_force_dxf_step_enabled(prefs: dict[str, Any] | None = None) -> bool:
    """True = cobre solo con gap + DXF/STEP (sin RTZCU / sin_gap / Amada nest)."""
    data = prefs if isinstance(prefs, dict) else load_nest_runtime_prefs()
    return bool(data.get("cu_force_dxf_step"))
