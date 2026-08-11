"""Persistencia del motor de nesting por defecto (configuracion_nesting.json)."""
from __future__ import annotations

import json
import os

import config

from .nest_engine_context import (
    DEFAULT_STEEL_ENGINE_ID,
    normalize_engine_id,
    set_active_engine_id,
    set_selected_engine_id,
)

# Junto al .exe en builds frozen (BASE_DIR apunta a _MEIPASS y no es escribible).
_CONFIG_PATH = config.ruta_persistente("configuracion_nesting.json")


def _read_config() -> dict:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_config(data: dict) -> None:
    with open(_CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=4, ensure_ascii=False)
        fh.write("\n")


def load_default_steel_engine_id() -> str:
    data = _read_config()
    reg = data.get("nest_engine_registry") or {}
    return normalize_engine_id(reg.get("default_steel_engine"))


def save_default_steel_engine_id(engine_id: str) -> str:
    eid = normalize_engine_id(engine_id)
    data = _read_config()
    reg = dict(data.get("nest_engine_registry") or {})
    reg["default_steel_engine"] = eid
    data["nest_engine_registry"] = reg
    _write_config(data)
    return eid


def apply_steel_engine(engine_id: str, motor=None) -> str:
    """Activa motor en contexto global y opcionalmente en MotorNesting."""
    eid = save_default_steel_engine_id(engine_id)
    set_active_engine_id(eid)
    set_selected_engine_id(eid)
    if motor is not None:
        motor.active_engine_id = eid
    return eid


def apply_saved_steel_engine(motor=None) -> str:
    eid = load_default_steel_engine_id()
    set_active_engine_id(eid)
    set_selected_engine_id(eid)
    if motor is not None:
        motor.active_engine_id = eid
    return eid
