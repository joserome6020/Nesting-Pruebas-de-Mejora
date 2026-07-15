"""Comparación paralela de motores de nesting (Opción B)."""
from __future__ import annotations

import concurrent.futures
import copy
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .engine_registry import get_engine_meta, is_engine_ready, list_engine_metas
from .engines.types import EngineJobMetrics, NestEngineNotReadyError
from .nest_engine_context import (
    UI_STEEL_ENGINE_IDS,
    get_selected_engine_id,
    iter_ui_steel_engine_ids,
    normalize_engine_id,
    set_active_engine_id,
    set_selected_engine_id,
)


@dataclass
class EngineComparisonBundle:
    """Resultado agregado de correr varios motores sobre el mismo job."""
    runs: dict[str, dict] = field(default_factory=dict)
    metrics: list[EngineJobMetrics] = field(default_factory=list)
    selected_engine_id: str = "arga_base"
    elapsed_total_s: float = 0.0
    compare_mode: str = "parallel_full_job"


def _count_pieces_in_result(resultados: dict) -> tuple[int, int]:
    """Devuelve (colocadas, pendientes_estimadas) en grupos de acero."""
    colocadas = 0
    pendientes = 0
    if not isinstance(resultados, dict):
        return 0, 0
    for _clave, grupo in resultados.items():
        if not isinstance(grupo, dict):
            continue
        if grupo.get("error"):
            pendientes += 1
            continue
        hojas = grupo.get("hojas") or []
        for hoja in hojas:
            if not isinstance(hoja, dict):
                continue
            piezas = hoja.get("piezas") or []
            colocadas += len(
                [
                    p
                    for p in piezas
                    if isinstance(p, dict)
                    and not str(p.get("nombre", "")).startswith(
                        ("REF__", "TATUAJE__", "REMANENTE__")
                    )
                ]
            )
        pendientes += int(grupo.get("piezas_sin_colocar") or 0)
    return colocadas, pendientes


def _avg_efficiency(resultados: dict) -> float:
    effs = []
    if not isinstance(resultados, dict):
        return 0.0
    for grupo in resultados.values():
        if not isinstance(grupo, dict):
            continue
        for hoja in grupo.get("hojas") or []:
            if not isinstance(hoja, dict):
                continue
            try:
                effs.append(float(hoja.get("eficiencia") or 0.0))
            except Exception:
                pass
    return sum(effs) / len(effs) if effs else 0.0


def _count_sheets(resultados: dict) -> int:
    total = 0
    if not isinstance(resultados, dict):
        return 0
    for grupo in resultados.values():
        if isinstance(grupo, dict) and grupo.get("hojas"):
            total += len(grupo.get("hojas") or [])
    return total


def _total_cost(resultados: dict) -> float:
    total = 0.0
    if not isinstance(resultados, dict):
        return 0.0
    for grupo in resultados.values():
        if not isinstance(grupo, dict):
            continue
        try:
            total += float(grupo.get("costo_total") or 0.0)
        except Exception:
            pass
    return total


def summarize_engine_result(
    engine_id: str,
    resultados: dict,
    elapsed_s: float,
    *,
    status: str = "ok",
    error: Optional[str] = None,
) -> EngineJobMetrics:
    meta = get_engine_meta(engine_id)
    colocadas, pendientes = _count_pieces_in_result(resultados)
    advertencias = []
    if isinstance(resultados, dict):
        for clave, grupo in resultados.items():
            if isinstance(grupo, dict) and grupo.get("error"):
                advertencias.append(f"{clave}: {grupo.get('error')}")
    return EngineJobMetrics(
        engine_id=engine_id,
        display_name=meta.display_name,
        status=status,
        elapsed_s=float(elapsed_s),
        hojas=_count_sheets(resultados),
        piezas_colocadas=colocadas,
        piezas_pendientes=pendientes,
        eficiencia_promedio=_avg_efficiency(resultados),
        costo_total=_total_cost(resultados),
        error=error,
        advertencias=advertencias,
    )


def _run_single_engine_job(
    engine_id: str,
    motor_factory: Callable[[], Any],
    lista_partes: list,
    datos_placas: list,
    nest_kwargs: dict,
) -> tuple[str, dict, EngineJobMetrics]:
    token = set_active_engine_id(engine_id)
    t0 = time.perf_counter()
    status = "ok"
    error = None
    resultados: dict = {}
    try:
        if not is_engine_ready(engine_id):
            raise NestEngineNotReadyError(
                f"{get_engine_meta(engine_id).display_name} pendiente "
                f"(Fase {get_engine_meta(engine_id).phase})."
            )
        motor = motor_factory()
        resultados = motor.ejecutar_nesting_visual(
            copy.deepcopy(lista_partes),
            copy.deepcopy(datos_placas),
            engine_id=engine_id,
            **nest_kwargs,
        )
        if isinstance(resultados, dict) and resultados.get("error"):
            status = "error"
            error = str(resultados.get("error"))
    except NestEngineNotReadyError as exc:
        status = "pending"
        error = str(exc)
        resultados = {"error": str(exc)}
    except Exception as exc:
        status = "error"
        error = str(exc)
        resultados = {"error": str(exc)}
    finally:
        from .nest_engine_context import reset_active_engine_id

        reset_active_engine_id(token)

    elapsed = time.perf_counter() - t0
    metrics = summarize_engine_result(
        engine_id,
        resultados if isinstance(resultados, dict) else {},
        elapsed,
        status=status,
        error=error,
    )
    return engine_id, resultados, metrics


def ejecutar_comparacion_motores(
    motor_factory: Callable[[], Any],
    lista_partes: list,
    datos_placas: list,
    *,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    cancel_checker: Optional[Callable[[], bool]] = None,
    engine_ids: Optional[list[str]] = None,
    max_workers: Optional[int] = None,
    **nest_kwargs,
) -> EngineComparisonBundle:
    """
    Opción B: ejecuta todos los motores de acero en paralelo sobre el mismo job.
    El cobre debe excluirse antes (pipeline externo).
    """
    # Por defecto sin libnest2d (oculto en UI; código retocado intacto).
    ids = list(engine_ids or tuple(iter_ui_steel_engine_ids()) or UI_STEEL_ENGINE_IDS)
    workers = max(1, min(len(ids), int(max_workers or len(ids))))
    bundle = EngineComparisonBundle(compare_mode="parallel_full_job")
    t0 = time.perf_counter()

    def _notify(msg: str, pct: float) -> None:
        if progress_callback:
            progress_callback(msg, pct)

    _notify("Comparando motores de nesting en paralelo...", 0.02)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _run_single_engine_job,
                eid,
                motor_factory,
                lista_partes,
                datos_placas,
                dict(nest_kwargs),
            ): eid
            for eid in ids
        }
        done = 0
        for futuro in concurrent.futures.as_completed(futures):
            if cancel_checker and cancel_checker():
                for pending in futures:
                    pending.cancel()
                bundle.runs = {}
                bundle.metrics = []
                bundle.elapsed_total_s = time.perf_counter() - t0
                return bundle

            eid = futures[futuro]
            try:
                engine_id, resultados, metrics = futuro.result()
                bundle.runs[engine_id] = resultados
                bundle.metrics.append(metrics)
            except Exception as exc:
                bundle.runs[eid] = {"error": str(exc)}
                bundle.metrics.append(
                    summarize_engine_result(
                        eid,
                        {},
                        0.0,
                        status="error",
                        error=str(exc),
                    )
                )
            done += 1
            _notify(
                f"Motores comparados: {done}/{len(ids)}",
                0.02 + (done / max(1, len(ids))) * 0.90,
            )

    bundle.metrics.sort(
        key=lambda m: (
            0 if m.status == "ok" else 1,
            -m.eficiencia_promedio,
            m.costo_total,
            m.elapsed_s,
        )
    )

    preferred = get_selected_engine_id()
    if preferred in bundle.runs and bundle.runs[preferred]:
        bundle.selected_engine_id = preferred
    else:
        ok_runs = [m for m in bundle.metrics if m.status == "ok"]
        bundle.selected_engine_id = ok_runs[0].engine_id if ok_runs else normalize_engine_id(ids[0])

    bundle.elapsed_total_s = time.perf_counter() - t0
    _notify("Comparación de motores finalizada.", 0.95)
    return bundle


def apply_selected_engine(bundle: EngineComparisonBundle, engine_id: str) -> dict:
    """Devuelve el resultado del motor elegido y actualiza selección global."""
    eid = normalize_engine_id(engine_id)
    set_selected_engine_id(eid)
    set_active_engine_id(eid)
    return dict(bundle.runs.get(eid) or {"error": f"Sin resultado para motor {eid}."})


def comparison_rows_for_ui(bundle: EngineComparisonBundle) -> list[dict]:
    rows = []
    for meta in list_engine_metas():
        metric = next((m for m in bundle.metrics if m.engine_id == meta.engine_id), None)
        rows.append(
            {
                "engine_id": meta.engine_id,
                "display_name": meta.display_name,
                "status": metric.status if metric else meta.status,
                "ready": is_engine_ready(meta.engine_id),
                "phase": meta.phase,
                "hojas": metric.hojas if metric else 0,
                "eficiencia_promedio": metric.eficiencia_promedio if metric else 0.0,
                "piezas_colocadas": metric.piezas_colocadas if metric else 0,
                "piezas_pendientes": metric.piezas_pendientes if metric else 0,
                "costo_total": metric.costo_total if metric else 0.0,
                "elapsed_s": metric.elapsed_s if metric else 0.0,
                "error": metric.error if metric else None,
                "selected": meta.engine_id == bundle.selected_engine_id,
            }
        )
    return rows
