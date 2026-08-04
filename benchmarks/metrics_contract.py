"""Contrato JSON unificado de métricas de nesting.

Campos obligatorios del resultado de una corrida:
  scenario, engine_id, elapsed_ms, placed, expected,
  efi_directa, solape_ok, kerf_violations, min_gap_in, in_holes

Campos opcionales útiles:
  nest_mode, restos, efi_real, overlap_detail, error, pass_ok,
  baseline_delta_pp, notes
"""
from __future__ import annotations

from typing import Any

REQUIRED_FIELDS = (
    "scenario",
    "engine_id",
    "elapsed_ms",
    "placed",
    "expected",
    "efi_directa",
    "solape_ok",
    "kerf_violations",
    "min_gap_in",
    "in_holes",
)


def empty_result(
    *,
    scenario: str,
    engine_id: str,
    expected: int = 0,
    error: str = "",
) -> dict[str, Any]:
    return {
        "scenario": str(scenario),
        "engine_id": str(engine_id),
        "elapsed_ms": 0.0,
        "placed": 0,
        "expected": int(expected),
        "efi_directa": 0.0,
        "solape_ok": False if error else True,
        "kerf_violations": 0,
        "min_gap_in": None,
        "in_holes": 0,
        "restos": expected,
        "efi_real": 0.0,
        "pass_ok": False,
        "error": str(error or ""),
        "baseline_delta_pp": None,
        "notes": "",
    }


def validate_result(row: dict[str, Any]) -> list[str]:
    missing = [k for k in REQUIRED_FIELDS if k not in (row or {})]
    return missing


def result_pass_ok(row: dict[str, Any], *, require_full_place: bool = True) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("error"):
        return False
    if not bool(row.get("solape_ok", False)):
        return False
    if int(row.get("kerf_violations") or 0) > 0:
        return False
    if require_full_place and int(row.get("placed") or 0) < int(row.get("expected") or 0):
        return False
    return True
