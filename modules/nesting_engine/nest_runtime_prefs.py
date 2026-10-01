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
    # Piezas cobre normales (rectangulares) SIEMPRE llevan MARK.
    # ON (default): Zapato/Botella/Z y especiales Amada sin MARK; verticales solo *_Corte.dxf.
    # OFF: Z también con MARK + split Corte/Marcaje CyPTube.
    "cu_sin_marcaje": True,
    # Barras de piezas cobre normales:
    # "exacto" (default): primero piezas de ancho exacto de solera; las de ancho con
    #   decimal (recorte a lo largo) van en barras propias y las exactas sobrantes
    #   pueden rellenarlas.
    # "mixto": exactas y con decimal comparten barra (optimización previa).
    "cu_ancho_modo": "exacto",
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

# Cache en memoria: empaquetar CU llama is_cu_force_dxf_step miles de veces por corrida.
_PREFS_CACHE_MTIME: float | None = None
_PREFS_CACHE_DATA: dict[str, Any] | None = None


CU_ANCHO_MODOS = ("exacto", "mixto")


def normalize_cu_ancho_modo(value: Any) -> str:
    v = str(value or "").strip().lower()
    if v in ("mixto", "2", "mezclado", "conf2", "configuracion 2"):
        return "mixto"
    return "exacto"


def invalidate_nest_runtime_prefs_cache() -> None:
    global _PREFS_CACHE_MTIME, _PREFS_CACHE_DATA
    _PREFS_CACHE_MTIME = None
    _PREFS_CACHE_DATA = None


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


def _apply_env_overrides(prefs: dict[str, Any]) -> dict[str, Any]:
    """Env gana siempre (worker/CI/tests); se reaplica aunque el JSON esté cacheado."""
    out = copy.deepcopy(prefs)
    env_prefer = (os.environ.get("ARGA_NEST_RUNTIME") or "").strip()
    env_host = (os.environ.get("ARGA_NEST_SPARK_HOST") or "").strip()
    env_port = (os.environ.get("ARGA_NEST_SPARK_PORT") or "").strip()
    env_cu = (os.environ.get("ARGA_CU_FORCE_DXF_STEP") or "").strip().lower()
    env_cu_mark = (os.environ.get("ARGA_CU_SIN_MARCAJE") or "").strip().lower()
    env_giga = (os.environ.get("ARGA_GIGA_CAL11_GALV") or "").strip().lower()
    env_step = (os.environ.get("ARGA_STEP_FEEDSTOCK") or "").strip().lower()
    env_ancho = (os.environ.get("ARGA_CU_ANCHO_MODO") or "").strip().lower()
    if env_ancho:
        out["cu_ancho_modo"] = normalize_cu_ancho_modo(env_ancho)
    if env_prefer:
        out["prefer"] = normalize_prefer(env_prefer)
    if env_host:
        spark = dict(out.get("spark") or {})
        spark["host"] = env_host
        out["spark"] = spark
    if env_port:
        try:
            spark = dict(out.get("spark") or {})
            spark["port"] = int(env_port)
            out["spark"] = spark
        except ValueError:
            pass
    if env_cu in ("1", "true", "on", "yes"):
        out["cu_force_dxf_step"] = True
    elif env_cu in ("0", "false", "off", "no"):
        out["cu_force_dxf_step"] = False
    if env_cu_mark in ("1", "true", "on", "yes"):
        out["cu_sin_marcaje"] = True
    elif env_cu_mark in ("0", "false", "off", "no"):
        out["cu_sin_marcaje"] = False
    if env_giga in ("1", "true", "on", "yes"):
        out["giga_cal11_galv"] = True
    elif env_giga in ("0", "false", "off", "no"):
        out["giga_cal11_galv"] = False
    if env_step in ("1", "true", "on", "yes"):
        out["step_feedstock_enabled"] = True
    elif env_step in ("0", "false", "off", "no"):
        out["step_feedstock_enabled"] = False
    out["prefer"] = normalize_prefer(str(out.get("prefer") or "local"))
    out["cu_force_dxf_step"] = bool(out.get("cu_force_dxf_step"))
    out["cu_sin_marcaje"] = bool(out.get("cu_sin_marcaje"))
    out["cu_ancho_modo"] = normalize_cu_ancho_modo(out.get("cu_ancho_modo"))
    out["giga_cal11_galv"] = bool(out.get("giga_cal11_galv"))
    out["step_feedstock_enabled"] = bool(out.get("step_feedstock_enabled"))
    out["exportar_a_servidor"] = bool(out.get("exportar_a_servidor", True))
    spark = dict(out.get("spark") or {})
    for key, value in _DEFAULTS["spark"].items():
        spark.setdefault(key, value)
    out["spark"] = spark
    return out


def load_nest_runtime_prefs() -> dict[str, Any]:
    global _PREFS_CACHE_MTIME, _PREFS_CACHE_DATA
    path = config_path()
    try:
        mtime = path.stat().st_mtime if path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    if _PREFS_CACHE_DATA is not None and _PREFS_CACHE_MTIME == mtime:
        # Cache = disco+defaults; env se reaplica por si cambió en runtime (tests/CI).
        return _apply_env_overrides(_PREFS_CACHE_DATA)

    prefs = copy.deepcopy(_DEFAULTS)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                prefs = _deep_merge(prefs, raw)
        except Exception:
            pass

    prefs["prefer"] = normalize_prefer(str(prefs.get("prefer") or "local"))
    prefs["cu_force_dxf_step"] = bool(prefs.get("cu_force_dxf_step"))
    prefs["cu_sin_marcaje"] = bool(prefs.get("cu_sin_marcaje"))
    prefs["cu_ancho_modo"] = normalize_cu_ancho_modo(prefs.get("cu_ancho_modo"))
    prefs["giga_cal11_galv"] = bool(prefs.get("giga_cal11_galv"))
    prefs["step_feedstock_enabled"] = bool(prefs.get("step_feedstock_enabled"))
    prefs["exportar_a_servidor"] = bool(prefs.get("exportar_a_servidor", True))
    spark = dict(prefs.get("spark") or {})
    for key, value in _DEFAULTS["spark"].items():
        spark.setdefault(key, value)
    prefs["spark"] = spark
    _PREFS_CACHE_MTIME = mtime
    _PREFS_CACHE_DATA = copy.deepcopy(prefs)
    return _apply_env_overrides(prefs)


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
    data["cu_sin_marcaje"] = bool(data.get("cu_sin_marcaje"))
    data["cu_ancho_modo"] = normalize_cu_ancho_modo(data.get("cu_ancho_modo"))
    data["giga_cal11_galv"] = bool(data.get("giga_cal11_galv"))
    data["step_feedstock_enabled"] = bool(data.get("step_feedstock_enabled"))
    data["exportar_a_servidor"] = bool(data.get("exportar_a_servidor", True))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    invalidate_nest_runtime_prefs_cache()
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
    if isinstance(prefs, dict):
        return bool(prefs.get("cu_force_dxf_step"))
    if _PREFS_CACHE_DATA is not None:
        return bool(_PREFS_CACHE_DATA.get("cu_force_dxf_step"))
    return bool(load_nest_runtime_prefs().get("cu_force_dxf_step"))


def is_cu_sin_marcaje_enabled(prefs: dict[str, Any] | None = None) -> bool:
    """True = cobre Z/Zapato/Botella/especial sin MARK; verticales CyPTube solo *_Corte."""
    if isinstance(prefs, dict):
        return bool(prefs.get("cu_sin_marcaje"))
    if _PREFS_CACHE_DATA is not None:
        return bool(_PREFS_CACHE_DATA.get("cu_sin_marcaje"))
    return bool(load_nest_runtime_prefs().get("cu_sin_marcaje"))


def cu_ancho_modo(prefs: dict[str, Any] | None = None) -> str:
    """"exacto" (Configuración 1) o "mixto" (Configuración 2) para barras cobre normales."""
    if isinstance(prefs, dict):
        return normalize_cu_ancho_modo(prefs.get("cu_ancho_modo"))
    if _PREFS_CACHE_DATA is not None and not os.environ.get("ARGA_CU_ANCHO_MODO"):
        return normalize_cu_ancho_modo(_PREFS_CACHE_DATA.get("cu_ancho_modo"))
    return normalize_cu_ancho_modo(load_nest_runtime_prefs().get("cu_ancho_modo"))


def _es_material_cobre(material: str | None) -> bool:
    try:
        from interface.utils_nesting import es_material_cobre
    except Exception:
        def es_material_cobre(m):  # type: ignore
            u = str(m or "").strip().upper()
            return u in ("CU", "COBRE", "COPPER") or "COBRE" in u or "COPPER" in u

    return bool(es_material_cobre(material))


def should_omit_copper_marks(
    material: str | None,
    *,
    pieza: dict | None = None,
    poly: Any = None,
    especial: bool = False,
) -> bool:
    """True si la pieza cobre debe salir sin MARK.

    Las piezas normales (contorno rectangular) siempre conservan MARK. Solo
    Zapato/Botella/Z (relieve) y especiales Amada lo pierden, y solo con el
    switch ``cu_sin_marcaje`` activo. Sin geometría ni pieza (p. ej. FILES al
    limpiar el DXF) no se puede saber la forma: se conserva el MARK.
    """
    if not is_cu_sin_marcaje_enabled():
        return False
    mat = material
    if mat is None and isinstance(pieza, dict):
        mat = pieza.get("material")
    if not _es_material_cobre(mat):
        return False
    try:
        from .cu_largos_nesting import pieza_cu_es_z_o_especial
    except Exception:
        return False
    return pieza_cu_es_z_o_especial(pieza=pieza, poly=poly, especial=especial)


def should_omit_copper_marks_pieza(pieza: dict | None) -> bool:
    """Atajo para piezas ya colocadas (flags del nest o contorno en poligonos)."""
    if not isinstance(pieza, dict):
        return False
    return should_omit_copper_marks(pieza.get("material"), pieza=pieza)


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