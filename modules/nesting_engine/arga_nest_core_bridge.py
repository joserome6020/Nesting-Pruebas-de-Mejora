"""Fachada Python → ArgaNestCore (ANS C++).

Activación: set ARGA_NEST_CORE=1

Contrato kerf (A/B corpus 2026-07-31):
  - Default **identity**: `kerf_mm = kerf` (misma magnitud que planta; contrato explícito).
  - ARGA_NEST_KERF_CONTRACT=legacy → solo `kerf`, sin `kerf_mm`.
  - ARGA_NEST_KERF_CONTRACT=physical_mm → `kerf_mm = kerf_in * 25.4`
    (NO usar en producción hasta re-tunear nests; reduce placement).
"""
from __future__ import annotations

import json
import os
from typing import Any

_CORE = None
_CORE_LOAD_ERROR = None
IN_TO_MM = 25.4


def _env_enabled() -> bool:
    v = (os.environ.get("ARGA_NEST_CORE") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _kerf_contract() -> str:
    # Default identity: marca kerf_mm explícito sin cambiar magnitud (A/B 3/3).
    # physical_mm queda opt-in (rompe paridad de planta hoy).
    return (os.environ.get("ARGA_NEST_KERF_CONTRACT") or "identity").strip().lower()


def _try_load_core():
    global _CORE, _CORE_LOAD_ERROR
    if _CORE is not None or _CORE_LOAD_ERROR is not None:
        return _CORE
    try:
        from modules.nesting_engine import arga_nest_core as core  # type: ignore

        _CORE = core
        return _CORE
    except Exception:
        pass
    try:
        import arga_nest_core as core  # type: ignore

        _CORE = core
        return _CORE
    except Exception as ex:
        _CORE_LOAD_ERROR = str(ex)
        return None


def core_available() -> bool:
    return _try_load_core() is not None


def core_enabled() -> bool:
    return _env_enabled() and core_available()


def core_status() -> dict[str, Any]:
    core = _try_load_core()
    worker_st = {}
    try:
        from modules.nesting_engine import arga_nest_worker_client as wcli

        worker_st = wcli.worker_status()
    except Exception as ex:
        worker_st = {"error": str(ex)}
    return {
        "env_ARGA_NEST_CORE": _env_enabled(),
        "module_loaded": core is not None,
        "active": core_enabled(),
        "version": core.version_string() if core is not None else None,
        "load_error": _CORE_LOAD_ERROR,
        "abi": getattr(core, "ABI_VERSION", None) if core is not None else None,
        "kerf_contract": _kerf_contract(),
        "worker": worker_st,
    }


def apply_kerf_contract(request: dict[str, Any], kerf_override: float | None = None) -> dict[str, Any]:
    """Aplica contrato kerf; default identity no cambia magnitud vs planta."""
    req = dict(request)
    kerf = float(kerf_override if kerf_override is not None else req.get("kerf", 0.15))
    req["kerf"] = kerf
    contract = _kerf_contract()
    if contract in ("physical_mm", "mm", "physical"):
        req["kerf_mm"] = kerf * IN_TO_MM
    elif contract in ("identity", "explicit"):
        req["kerf_mm"] = kerf
    else:
        req.pop("kerf_mm", None)
    return req


def rank_pieces_largest_first(pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Heurística ligera de orden (FFD-ish) previo al GA/Tabu."""

    def _area(p: dict[str, Any]) -> float:
        try:
            return float(p.get("area") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    return sorted(pieces, key=_area, reverse=True)


def prepare_pack_request(
    *,
    plate_w: float,
    plate_h: float,
    pieces: list[dict[str, Any]],
    kerf: float = 0.15,
    margin: float = 0.0,
    engine: str = "svgnest_ultra",
    profile: str | None = None,
    ga_population: int = 10,
    ga_generations: int = 10,
    rotation_step_deg: float = 90.0,
    part_in_part: bool = True,
    certify: bool = True,
    enable_tabu: bool = True,
    tabu_seed_trials: int = 3,
    rank_order: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if rank_order:
        try:
            from modules.nesting_engine.ai_ranker import last_policy, maybe_rank_pieces

            pcs = maybe_rank_pieces(list(pieces))
            seed_pol = last_policy() or "area_desc"
        except Exception:
            pcs = rank_pieces_largest_first(list(pieces))
            seed_pol = "area_desc"
    else:
        pcs = list(pieces)
        seed_pol = "passthrough"
    req: dict[str, Any] = {
        "engine": engine,
        "plate_w": float(plate_w),
        "plate_h": float(plate_h),
        "kerf": float(kerf),
        "margin": float(margin),
        "ga_population": int(ga_population),
        "ga_generations": int(ga_generations),
        "rotation_step_deg": float(rotation_step_deg),
        "part_in_part": bool(part_in_part),
        "certify": bool(certify),
        "enable_tabu": bool(enable_tabu),
        "tabu_seed_trials": int(tabu_seed_trials),
        "pieces": pcs,
        "seed_policy": seed_pol,
        # Burke/IA: respetar seed_order + RNG reproducible
        "preserve_order": True,
        "ga_seed": int(os.environ.get("ARGA_NEST_GA_SEED") or 1),
    }
    if profile:
        req["profile"] = profile
    if extra:
        req.update(extra)
    return apply_kerf_contract(req)


def _pack_via_worker_or_core(kind: str, request: dict[str, Any]) -> dict[str, Any] | str | None:
    """kind: pack_sheet | pack_job | export_dxf. None = usar in-process."""
    try:
        from modules.nesting_engine import arga_nest_worker_client as wcli

        if wcli.worker_enabled():
            if kind == "pack_sheet":
                return wcli.pack_sheet_via_worker(request)
            if kind == "pack_job":
                return wcli.pack_job_via_worker(request)
            if kind == "export_dxf":
                return wcli.export_dxf_via_worker(request)
    except Exception as ex:
        from modules.nesting_engine import arga_nest_worker_client as wcli

        if wcli.worker_strict():
            raise
        print(f"[ARGA_NEST_WORKER] fallback in-process: {ex}", flush=True)
    return None


def pack_sheet_json(request: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(request, dict):
        req = apply_kerf_contract(request)
    else:
        req = apply_kerf_contract(json.loads(request))

    via = _pack_via_worker_or_core("pack_sheet", req)
    if isinstance(via, dict):
        return via

    core = _try_load_core()
    if core is None:
        raise RuntimeError(
            "arga_nest_core no disponible. Compila con native\\build_arga_nest_core.ps1. "
            f"Detalle: {_CORE_LOAD_ERROR}"
        )
    return dict(core.pack_sheet(req))


def pack_job_json(request: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(request, dict):
        req = apply_kerf_contract(request)
    else:
        req = apply_kerf_contract(json.loads(request))

    via = _pack_via_worker_or_core("pack_job", req)
    if isinstance(via, dict):
        return via

    core = _try_load_core()
    if core is None:
        raise RuntimeError(f"arga_nest_core no disponible: {_CORE_LOAD_ERROR}")
    return json.loads(core.pack_job_json(json.dumps(req)))


def export_dxf_json(request: dict[str, Any] | str) -> str:
    if isinstance(request, dict):
        req = apply_kerf_contract(request)
    else:
        req = apply_kerf_contract(json.loads(request))

    via = _pack_via_worker_or_core("export_dxf", req)
    if isinstance(via, str):
        return via

    core = _try_load_core()
    if core is None:
        raise RuntimeError(f"arga_nest_core no disponible: {_CORE_LOAD_ERROR}")
    return str(core.export_dxf_json(json.dumps(req)))


def certify_dxf(dxf_text: str) -> dict[str, Any]:
    core = _try_load_core()
    if core is None:
        raise RuntimeError(f"arga_nest_core no disponible: {_CORE_LOAD_ERROR}")
    return json.loads(core.certify_dxf_json(json.dumps({"dxf": dxf_text})))


def pack_sheet_or_legacy(request: dict[str, Any], legacy_callable):
    if core_enabled():
        return pack_sheet_json(request)
    return legacy_callable()
