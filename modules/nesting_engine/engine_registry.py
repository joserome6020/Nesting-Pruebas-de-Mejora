"""Registro central de motores de nesting para placas de acero."""
from __future__ import annotations

import time
from typing import Callable, Optional, Type

from .cut_gaps_table import PLATE_TO_PIECE_DEFAULT_IN
from .engines import (
    ArgaApexEngine,
    ArgaForceEngine,
    ArgaLabPilotEngine,
    ArgaLiteEngine,
    BurkeBlfEngine,
    GigaCal11GalvEngine,
    Libnest2dEngine,
    SvgnestUltraEngine,
)
from .engines.types import NestEngineMeta, NestEngineNotReadyError, PackSheetRequest, PackSheetResult
from .nest_engine_context import (
    STEEL_ENGINE_IDS,
    get_active_engine_id,
    iter_ui_steel_engine_ids,
    normalize_engine_id,
)

_ENGINE_CLASSES: dict[str, Type] = {
    ArgaForceEngine.META.engine_id: ArgaForceEngine,
    BurkeBlfEngine.META.engine_id: BurkeBlfEngine,
    Libnest2dEngine.META.engine_id: Libnest2dEngine,
    SvgnestUltraEngine.META.engine_id: SvgnestUltraEngine,
    ArgaApexEngine.META.engine_id: ArgaApexEngine,
    ArgaLiteEngine.META.engine_id: ArgaLiteEngine,
    ArgaLabPilotEngine.META.engine_id: ArgaLabPilotEngine,
    GigaCal11GalvEngine.META.engine_id: GigaCal11GalvEngine,
}

def _request_con_margen_final_placa(
    request: PackSheetRequest,
    engine_id: str,
) -> PackSheetRequest:
    """El packer recibe el margen de tabla (0.250\") para el METAL.

    Antes se restaba kerf/2 (0.250-0.075=0.175\") creyendo que el globo de
    kerf se pegaba al canto y el metal acababa a 0.250\". En planta el metal
    quedaba a ~4.85 mm (Galv) y ~3.3 mm (Cal 0.25). El pokayoke mide metal.
    """
    del engine_id
    return request


def list_engine_metas(*, include_hidden: bool = False) -> list[NestEngineMeta]:
    """Metas de motores. Por defecto oculta libnest2d en UI (código intacto)."""
    ids = STEEL_ENGINE_IDS if include_hidden else tuple(iter_ui_steel_engine_ids())
    return [_ENGINE_CLASSES[eid].meta() for eid in ids if eid in _ENGINE_CLASSES]


def list_ui_engine_metas() -> list[NestEngineMeta]:
    return list_engine_metas(include_hidden=False)


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


def list_ready_engine_ids(*, include_hidden: bool = False) -> list[str]:
    ids = STEEL_ENGINE_IDS if include_hidden else tuple(iter_ui_steel_engine_ids())
    return [eid for eid in ids if is_engine_ready(eid)]


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
    kerf_override=0.15,
    margin_override=None,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_poly=None,
    mc_iterations=None,
    engine_id: str | None = None,
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> tuple[dict, list]:
    """API unificada: devuelve (hoja, restos) para manager.py."""
    # margin_override=None → tabla oficial (0.250"). Cualquier motor que se
    # invoque sin margen explícito respeta la constante de planta.
    if margin_override is None:
        margin_override = PLATE_TO_PIECE_DEFAULT_IN
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
    """API unificada con routing Local | NvidiaSpark para cualquier motor."""
    eid = normalize_engine_id(engine_id or get_active_engine_id())
    request = _request_con_margen_final_placa(request, eid)
    try:
        from .giga_cal11_galv import ENGINE_ID as GIGA_ID
        from .giga_cal11_galv import should_force_giga_engine

        if should_force_giga_engine() and eid != GIGA_ID:
            print(
                f"[GIGA-CAL11] motor nativo {GIGA_ID} (cede {eid})",
                flush=True,
            )
            eid = GIGA_ID
    except Exception as giga_ex:
        print(f"[GIGA-CAL11] route skip: {giga_ex}", flush=True)
    try:
        from modules.nesting_engine.nest_executor import pack_engine

        return pack_engine(request, engine_id=eid)
    except Exception as exc:
        # El switch remoto nunca puede impedir el nesting local de planta.
        print(f"[NEST-SPARK] executor unavailable → local: {exc}", flush=True)
        return empaquetar_una_hoja_detalle_local(request, engine_id=eid)


def empaquetar_una_hoja_detalle_local(
    request: PackSheetRequest,
    engine_id: str | None = None,
) -> PackSheetResult:
    """Ejecuta el motor sin routing remoto (fallback local seguro)."""
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
