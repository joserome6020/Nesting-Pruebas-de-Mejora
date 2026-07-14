"""Registro central de motores de nesting para placas de acero."""
from __future__ import annotations

import time
from typing import Callable, Optional, Type

from .engines import ArgaForceEngine, BurkeBlfEngine, Libnest2dEngine, SvgnestUltraEngine
from .engines.types import NestEngineMeta, NestEngineNotReadyError, PackSheetRequest, PackSheetResult
from .nest_engine_context import STEEL_ENGINE_IDS, get_active_engine_id, normalize_engine_id

_ENGINE_CLASSES: dict[str, Type] = {
    ArgaForceEngine.META.engine_id: ArgaForceEngine,
    BurkeBlfEngine.META.engine_id: BurkeBlfEngine,
    Libnest2dEngine.META.engine_id: Libnest2dEngine,
    SvgnestUltraEngine.META.engine_id: SvgnestUltraEngine,
}


def list_engine_metas() -> list[NestEngineMeta]:
    return [_ENGINE_CLASSES[eid].meta() for eid in STEEL_ENGINE_IDS if eid in _ENGINE_CLASSES]


def get_engine_meta(engine_id: str) -> NestEngineMeta:
    key = normalize_engine_id(engine_id)
    cls = _ENGINE_CLASSES.get(key)
    if cls is None:
        raise KeyError(f"Motor desconocido: {engine_id}")
    return cls.meta()


def is_engine_ready(engine_id: str) -> bool:
    key = normalize_engine_id(engine_id)
    cls = _ENGINE_CLASSES.get(key)
    if cls is None:
        return False
    return bool(cls.is_ready())


def list_ready_engine_ids() -> list[str]:
    return [eid for eid in STEEL_ENGINE_IDS if is_engine_ready(eid)]


def resolve_engine_class(engine_id: str | None = None) -> Type:
    key = normalize_engine_id(engine_id or get_active_engine_id())
    cls = _ENGINE_CLASSES.get(key)
    if cls is None:
        raise KeyError(f"Motor desconocido: {engine_id}")
    return cls


def empaquetar_una_hoja(
    piezas,
    w_placa,
    h_placa,
    kerf_override=0.3,
    margin_override=0.15,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_poly=None,
    mc_iterations=None,
    engine_id: str | None = None,
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> tuple[dict, list]:
    """API unificada: devuelve (hoja, restos) para manager.py."""
    request = PackSheetRequest(
        piezas=piezas,
        w_placa=w_placa,
        h_placa=h_placa,
        kerf_override=kerf_override,
        margin_override=margin_override,
        opt_override=opt_override,
        corner_override=corner_override,
        limite_poly=limite_poly,
        mc_iterations=mc_iterations,
        cancel_checker=cancel_checker,
    )
    result = empaquetar_una_hoja_detalle(request, engine_id=engine_id)
    if result.error and not (result.hoja.get("piezas")):
        raise RuntimeError(result.error)
    return result.hoja, result.restos


def empaquetar_una_hoja_detalle(
    request: PackSheetRequest,
    engine_id: str | None = None,
) -> PackSheetResult:
    cls = resolve_engine_class(engine_id)
    if not cls.is_ready():
        raise NestEngineNotReadyError(
            f"{cls.meta().display_name} no está listo (Fase {cls.meta().phase})."
        )
    return cls.empaquetar(request)


def engine_name(engine_id: str | None = None) -> str:
    try:
        meta = get_engine_meta(engine_id or get_active_engine_id())
        return f"{meta.engine_id}@{meta.status}"
    except Exception:
        return "unknown"


def probe_engines() -> list[dict]:
    """Diagnóstico rápido de motores (smoke / benchmark)."""
    rows = []
    for eid in STEEL_ENGINE_IDS:
        meta = get_engine_meta(eid)
        row = {
            "engine_id": eid,
            "display_name": meta.display_name,
            "status": meta.status,
            "ready": is_engine_ready(eid),
            "phase": meta.phase,
        }
        if not is_engine_ready(eid):
            rows.append(row)
            continue
        t0 = time.perf_counter()
        try:
            empaquetar_una_hoja_detalle(
                PackSheetRequest(
                    piezas=[],
                    w_placa=1000.0,
                    h_placa=2000.0,
                ),
                engine_id=eid,
            )
            row["probe_ok"] = True
        except Exception as exc:
            row["probe_ok"] = False
            row["probe_error"] = str(exc)
        row["probe_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        rows.append(row)
    return rows
