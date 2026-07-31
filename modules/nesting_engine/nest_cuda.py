"""Acelerador CUDA compartido para motores ANS existentes (+ Venom).

No crea motores nuevos. Opt-in: ``ARGA_NEST_CUDA=1``.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator


_TRUE = ("1", "true", "yes", "on")


def _env_on(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in _TRUE


def nest_cuda_enabled() -> bool:
    """Flag global: acelera Ultra/Force/Lite/Burke/Venom si el .pyd tiene CUDA."""
    return _env_on("ARGA_NEST_CUDA")


def engine_cuda_enabled(engine_id: str) -> bool:
    """Permite aislar por motor; si no hay flag específico, usa el global."""
    eid = str(engine_id or "").strip().lower()
    specific = {
        "svgnest_ultra": "ARGA_ULTRA_CUDA",
        "arga_force": "ARGA_FORCE_CUDA",
        "arga_base": "ARGA_FORCE_CUDA",
        "arga_lite": "ARGA_LITE_CUDA",
        "arga_apex": "ARGA_APEX_CUDA",
        "burke_blf": "ARGA_BURKE_CUDA",
        "venom": "ARGA_VENOM_CUDA",
    }.get(eid)
    if specific and _env_on(specific):
        return True
    if specific and os.environ.get(specific) is not None:
        # Flag explícito en 0/false → apagado aunque el global esté on.
        return _env_on(specific)
    # APEX: por defecto intenta CUDA si el runtime existe (Fase 4).
    if eid == "arga_apex" and not _env_on("ARGA_APEX_CUDA_OFF"):
        if nest_cuda_enabled():
            return True
        try:
            return cuda_runtime_available()
        except Exception:
            return False
    return nest_cuda_enabled()


@contextmanager
def nest_cuda_env(enabled: bool) -> Iterator[None]:
    previous = os.environ.get("ARGA_NEST_CUDA")
    try:
        if enabled:
            os.environ["ARGA_NEST_CUDA"] = "1"
        else:
            os.environ.pop("ARGA_NEST_CUDA", None)
        yield
    finally:
        if previous is None:
            os.environ.pop("ARGA_NEST_CUDA", None)
        else:
            os.environ["ARGA_NEST_CUDA"] = previous


def cuda_runtime_available() -> bool:
    try:
        from . import algorithm_cpp as cpp

        return bool(getattr(cpp, "nest_cuda_available", lambda: False)())
    except Exception:
        return False


def cuda_runtime_status() -> str:
    try:
        from . import algorithm_cpp as cpp

        return str(getattr(cpp, "nest_cuda_status", lambda: "algorithm_cpp sin CUDA")())
    except Exception as exc:  # noqa: BLE001
        return f"algorithm_cpp no cargado: {exc}"


def cuda_status_for_engine(engine_id: str) -> dict[str, Any]:
    return {
        "engine_id": str(engine_id or ""),
        "flag_enabled": engine_cuda_enabled(engine_id),
        "runtime_available": cuda_runtime_available(),
        "detail": cuda_runtime_status(),
    }
