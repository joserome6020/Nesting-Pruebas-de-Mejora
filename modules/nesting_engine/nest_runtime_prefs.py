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
    # OFF: Cal 11 Galv usa el motor del selector (Ultra/Lite). ON: motor giga_cal11_galv.
    "giga_cal11_galv": False,
    # OFF: FILES sin botón STEP. ON: complemento feedstock STEP dentro de AutoDXF.
    "step_feedstock_enabled": False,
    # Switch footer: EXPORTAR A SERVIDOR Y BD (default ON = comportamiento histórico).
    "exportar_a_servidor": True,
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
    env_giga = (os.environ.get("ARGA_GIGA_CAL11_GALV") or "").strip().lower()
    env_step = (os.environ.get("ARGA_STEP_FEEDSTOCK") or "").strip().lower()
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
    if env_giga in ("1", "true", "on", "yes"):
        prefs["giga_cal11_galv"] = True
    elif env_giga in ("0", "false", "off", "no"):
        prefs["giga_cal11_galv"] = False
    if env_step in ("1", "true", "on", "yes"):
        prefs["step_feedstock_enabled"] = True
    elif env_step in ("0", "false", "off", "no"):
        prefs["step_feedstock_enabled"] = False

    prefs["prefer"] = normalize_prefer(str(prefs.get("prefer") or "local"))
    prefs["cu_force_dxf_step"] = bool(prefs.get("cu_force_dxf_step"))
    prefs["giga_cal11_galv"] = bool(prefs.get("giga_cal11_galv"))
    prefs["step_feedstock_enabled"] = bool(prefs.get("step_feedstock_enabled"))
    prefs["exportar_a_servidor"] = bool(prefs.get("exportar_a_servidor", True))
    spark = dict(prefs.get("spark") or {})
    for key, value in _DEFAULTS["spark"].items():
        spark.setdefault(key, value)
    prefs["spark"] = spark
    return prefs


def save_nest_runtime_prefs(prefs: dict[str, Any]) -> Path:
    """Merge parcial: preserva claves no enviadas (p.ej. exportar_a_servidor)."""
    path = config_path()
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw
        except Exception:
            pass
    data = _deep_merge(_DEFAULTS, existing)
    data = _deep_merge(data, dict(prefs or {}))
    data["prefer"] = normalize_prefer(str(data.get("prefer") or "local"))
    data["cu_force_dxf_step"] = bool(data.get("cu_force_dxf_step"))
    data["giga_cal11_galv"] = bool(data.get("giga_cal11_galv"))
    data["step_feedstock_enabled"] = bool(data.get("step_feedstock_enabled"))
    data["exportar_a_servidor"] = bool(data.get("exportar_a_servidor", True))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def is_step_feedstock_enabled(prefs: dict[str, Any] | None = None) -> bool:
    """True = FILES muestra el complemento PROCESAR STEP (dentro de AutoDXF)."""
    data = prefs if isinstance(prefs, dict) else load_nest_runtime_prefs()
    return bool(data.get("step_feedstock_enabled"))


def set_step_feedstock_enabled(enabled: bool) -> Path:
    prefs = load_nest_runtime_prefs()
    prefs["step_feedstock_enabled"] = bool(enabled)
    return save_nest_runtime_prefs(prefs)


def is_cu_force_dxf_step_enabled(prefs: dict[str, Any] | None = None) -> bool:
    """True = cobre solo con gap + DXF/STEP (sin RTZCU / sin_gap / Amada nest)."""
    data = prefs if isinstance(prefs, dict) else load_nest_runtime_prefs()
    return bool(data.get("cu_force_dxf_step"))


def is_giga_cal11_galv_enabled(prefs: dict[str, Any] | None = None) -> bool:
    """True = cualquier Cal 11 Galv usa el motor nativo giga_cal11_galv."""
    data = prefs if isinstance(prefs, dict) else load_nest_runtime_prefs()
    return bool(data.get("giga_cal11_galv"))


def set_giga_cal11_galv_enabled(enabled: bool) -> Path:
    prefs = load_nest_runtime_prefs()
    prefs["giga_cal11_galv"] = bool(enabled)
    return save_nest_runtime_prefs(prefs)


def is_exportar_a_servidor_enabled(prefs: dict[str, Any] | None = None) -> bool:
    """True = export escribe a servidor/BD; False = solo nest locales."""
    data = prefs if isinstance(prefs, dict) else load_nest_runtime_prefs()
    return bool(data.get("exportar_a_servidor", True))


def set_exportar_a_servidor(enabled: bool) -> Path:
    """Persiste el switch footer EXPORTAR A SERVIDOR Y BD."""
    prefs = load_nest_runtime_prefs()
    prefs["exportar_a_servidor"] = bool(enabled)
    return save_nest_runtime_prefs(prefs)