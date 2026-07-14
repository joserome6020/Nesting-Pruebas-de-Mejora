"""Contexto de motor de nesting activo (por hilo / por worker de proceso)."""
from __future__ import annotations

import contextvars
import os
from typing import Iterable

ENGINE_ARGA_FORCE = "arga_force"
ENGINE_ARGA_BASE = "arga_base"  # alias legacy
ENGINE_BURKE_BLF = "burke_blf"
ENGINE_LIBNEST2D = "libnest2d"
ENGINE_SVGNEST_ULTRA = "svgnest_ultra"

STEEL_ENGINE_IDS: tuple[str, ...] = (
    ENGINE_ARGA_FORCE,
    ENGINE_BURKE_BLF,
    ENGINE_LIBNEST2D,
    ENGINE_SVGNEST_ULTRA,
)

_ENGINE_ALIASES: dict[str, str] = {
    "arga_base": ENGINE_ARGA_FORCE,
    "arga_base_pizarron": ENGINE_ARGA_FORCE,
    "base": ENGINE_ARGA_FORCE,
}

DEFAULT_STEEL_ENGINE_ID = ENGINE_SVGNEST_ULTRA
DEFAULT_SELECTED_ENGINE_ID = ENGINE_SVGNEST_ULTRA

_active_engine_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "nest_active_engine_id",
    default=DEFAULT_STEEL_ENGINE_ID,
)
_compare_mode: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "nest_compare_mode",
    default=False,
)
_selected_engine_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "nest_selected_engine_id",
    default=DEFAULT_SELECTED_ENGINE_ID,
)


def normalize_engine_id(engine_id: str | None) -> str:
    key = str(engine_id or "").strip().lower()
    key = _ENGINE_ALIASES.get(key, key)
    if key in STEEL_ENGINE_IDS:
        return key
    env = str(os.environ.get("ARGA_NEST_ENGINE", "")).strip().lower()
    env = _ENGINE_ALIASES.get(env, env)
    if env in STEEL_ENGINE_IDS:
        return env
    return DEFAULT_STEEL_ENGINE_ID


def get_active_engine_id() -> str:
    return normalize_engine_id(_active_engine_id.get())


def set_active_engine_id(engine_id: str) -> contextvars.Token:
    return _active_engine_id.set(normalize_engine_id(engine_id))


def reset_active_engine_id(token: contextvars.Token) -> None:
    _active_engine_id.reset(token)


def is_compare_mode() -> bool:
    return bool(_compare_mode.get())


def set_compare_mode(enabled: bool) -> contextvars.Token:
    return _compare_mode.set(bool(enabled))


def reset_compare_mode(token: contextvars.Token) -> None:
    _compare_mode.reset(token)


def get_selected_engine_id() -> str:
    return normalize_engine_id(_selected_engine_id.get())


def set_selected_engine_id(engine_id: str) -> contextvars.Token:
    return _selected_engine_id.set(normalize_engine_id(engine_id))


def reset_selected_engine_id(token: contextvars.Token) -> None:
    _selected_engine_id.reset(token)


def iter_steel_engine_ids() -> Iterable[str]:
    return STEEL_ENGINE_IDS
