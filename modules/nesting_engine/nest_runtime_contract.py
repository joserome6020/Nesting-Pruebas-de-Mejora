"""Contrato estable nest remoto/local.

Job y resultado JSON compatibles con ``pack_sheet`` / ArgaNestWorker.
El campo ``runtime`` indica dónde se ejecutó el nest (no cambia el motor).
"""
from __future__ import annotations

from typing import Any


CONTRACT_VERSION = "1.0.0"
RUNTIME_LOCAL = "local"
RUNTIME_SPARK = "spark"
RUNTIME_REMOTE = "remote"  # alias genérico (p.ej. worker LAN Windows)
PREFERS = ("local", "spark", "auto")


def validate_pack_request(request: dict[str, Any]) -> list[str]:
    """Devuelve lista de errores; vacía = OK."""
    errs: list[str] = []
    if not isinstance(request, dict):
        return ["request debe ser objeto JSON"]
    for key in ("plate_w", "plate_h", "pieces"):
        if key not in request:
            errs.append(f"falta campo '{key}'")
    pieces = request.get("pieces")
    if pieces is not None and not isinstance(pieces, list):
        errs.append("'pieces' debe ser lista")
    elif isinstance(pieces, list) and not pieces:
        errs.append("'pieces' no puede estar vacía")
    try:
        if "plate_w" in request and float(request["plate_w"]) <= 0:
            errs.append("plate_w debe ser > 0")
        if "plate_h" in request and float(request["plate_h"]) <= 0:
            errs.append("plate_h debe ser > 0")
    except (TypeError, ValueError):
        errs.append("plate_w/plate_h deben ser numéricos")
    return errs


def normalize_prefer(value: str | None) -> str:
    v = (value or "local").strip().lower()
    if v in ("remote", "server"):
        return "spark"
    if v not in PREFERS:
        return "local"
    return v


def attach_runtime_meta(
    result: dict[str, Any],
    *,
    runtime: str,
    prefer: str,
    fallback_used: bool = False,
    host: str | None = None,
    detail: str | None = None,
    elapsed_ms: float | None = None,
) -> dict[str, Any]:
    """Añade bloque ``runtime`` al resultado sin alterar métricas del core."""
    out = dict(result)
    meta: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "runtime": str(runtime),
        "prefer": normalize_prefer(prefer),
        "fallback_used": bool(fallback_used),
    }
    if host:
        meta["host"] = str(host)
    if detail:
        meta["detail"] = str(detail)
    if elapsed_ms is not None:
        meta["executor_elapsed_ms"] = float(elapsed_ms)
    out["runtime"] = meta
    return out


def runtime_from_result(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    meta = result.get("runtime")
    return dict(meta) if isinstance(meta, dict) else {}
