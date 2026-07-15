"""Contexto de motor de nesting activo (por hilo / por worker de proceso)."""
from __future__ import annotations

import contextvars
import os
from typing import Any, Callable, Iterable, Optional

ENGINE_ARGA_FORCE = "arga_force"
ENGINE_ARGA_BASE = "arga_base"  # alias legacy → FORCE
ENGINE_ARGA_LITE = "arga_lite"
ENGINE_BURKE_BLF = "burke_blf"
ENGINE_LIBNEST2D = "libnest2d"
ENGINE_SVGNEST_ULTRA = "svgnest_ultra"

STEEL_ENGINE_IDS: tuple[str, ...] = (
    ENGINE_ARGA_FORCE,
    ENGINE_BURKE_BLF,
    ENGINE_LIBNEST2D,
    ENGINE_SVGNEST_ULTRA,
    ENGINE_ARGA_LITE,  # al final: respaldo rápido / menor densidad
)

# Menú diario / renest / FILES / SIM-LAB. Código de libnest2d intacto.
# Reactivar en UI: ARGA_SHOW_LIBNEST2D=1
UI_HIDDEN_STEEL_ENGINE_IDS: frozenset[str] = frozenset({ENGINE_LIBNEST2D})

UI_STEEL_ENGINE_IDS: tuple[str, ...] = tuple(
    eid for eid in STEEL_ENGINE_IDS if eid not in UI_HIDDEN_STEEL_ENGINE_IDS
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
# Renesteo Ultra: Cancelar/Aceptar = conservar mejor (no en SIM-PLACA).
_ultra_renest_accept: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "nest_ultra_renest_accept",
    default=False,
)
# Durante sims multi-placa: forzar GA acotado aunque accept-mode esté ON.
_ultra_sim_bounded: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "nest_ultra_sim_bounded",
    default=False,
)
_ultra_best_callback: contextvars.ContextVar[Optional[Callable[..., Any]]] = contextvars.ContextVar(
    "nest_ultra_best_callback",
    default=None,
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


def is_engine_ui_visible(engine_id: str | None) -> bool:
    """False para motores retenidos pero fuera de la interfaz diaria."""
    key = normalize_engine_id(engine_id)
    if key not in UI_HIDDEN_STEEL_ENGINE_IDS:
        return True
    raw = str(os.environ.get("ARGA_SHOW_LIBNEST2D", "")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def iter_ui_steel_engine_ids() -> Iterable[str]:
    show_hidden = str(os.environ.get("ARGA_SHOW_LIBNEST2D", "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    return STEEL_ENGINE_IDS if show_hidden else UI_STEEL_ENGINE_IDS


def is_ultra_renest_accept_mode() -> bool:
    return bool(_ultra_renest_accept.get())


def set_ultra_renest_accept_mode(enabled: bool) -> contextvars.Token:
    return _ultra_renest_accept.set(bool(enabled))


def reset_ultra_renest_accept_mode(token: contextvars.Token) -> None:
    _ultra_renest_accept.reset(token)


def is_ultra_sim_bounded() -> bool:
    return bool(_ultra_sim_bounded.get())


def set_ultra_sim_bounded(enabled: bool) -> contextvars.Token:
    return _ultra_sim_bounded.set(bool(enabled))


def reset_ultra_sim_bounded(token: contextvars.Token) -> None:
    _ultra_sim_bounded.reset(token)


def set_ultra_best_callback(fn: Optional[Callable[..., Any]]) -> contextvars.Token:
    return _ultra_best_callback.set(fn)


def reset_ultra_best_callback(token: contextvars.Token) -> None:
    _ultra_best_callback.reset(token)


def notify_ultra_best_ready(resumen: str = "") -> None:
    cb = _ultra_best_callback.get()
    if not callable(cb):
        return
    try:
        cb(str(resumen or ""))
    except Exception:
        pass
