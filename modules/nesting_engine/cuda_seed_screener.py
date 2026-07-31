"""Cribado CUDA de población de semillas (experimental, no producción).

Reduce el ir-y-venir host↔GPU y Python↔C++:
1. Sube la placa parcial una vez.
2. Sube la geometría candidata una vez.
3. Evalúa muchas semillas (lotes de offsets) en una sola llamada nativa.
4. Devuelve solo rechazos seguros; los supervivientes van a Clipper2.

No decide colocaciones. No se habilita en el motor diario.
"""
from __future__ import annotations

from typing import Any, Sequence

from .algorithm_bridge_v2 import (
    create_cuda_raster_session,
    cuda_raster_filter_available,
    cuda_raster_filter_status,
    cuda_raster_safe_reject_batch,
    cuda_raster_screen_population,
)

Offset = tuple[int, int]
OffsetBatch = Sequence[Offset]


def available() -> bool:
    return bool(cuda_raster_filter_available())


def status() -> str:
    return str(cuda_raster_filter_status())


def screen_seed_population(
    fixed_inner: Sequence[int],
    fixed_w: int,
    fixed_h: int,
    candidate_inner: Sequence[int],
    candidate_w: int,
    candidate_h: int,
    offset_batches: Sequence[OffsetBatch],
    *,
    prefer_cuda: bool = True,
) -> dict[str, Any]:
    """
    API preferida para el piloto de semillas.

    Returns
    -------
    dict con:
      - rejected_per_seed: list[list[bool]]
      - stats: métricas H2D/kernel/D2H agregadas
      - cuda_active: bool
      - production_ready: False (siempre; gate externo decide promoción)
    """
    result = cuda_raster_screen_population(
        fixed_inner,
        fixed_w,
        fixed_h,
        candidate_inner,
        candidate_w,
        candidate_h,
        offset_batches,
        prefer_cuda=prefer_cuda,
    )
    out = dict(result)
    out.setdefault("cuda_active", bool((out.get("stats") or {}).get("cuda_used")))
    out["production_ready"] = False
    out["scope"] = (
        "cribado raster de población; supervivientes requieren Clipper2 "
        "y nesting exacto"
    )
    return out


def screen_seed_population_loop_baseline(
    fixed_inner: Sequence[int],
    fixed_w: int,
    fixed_h: int,
    candidate_inner: Sequence[int],
    candidate_w: int,
    candidate_h: int,
    offset_batches: Sequence[OffsetBatch],
    *,
    prefer_cuda: bool = False,
) -> dict[str, Any]:
    """Baseline chatty: una llamada por semilla (útil para A/B de overhead)."""
    session = None
    if prefer_cuda:
        session = create_cuda_raster_session(
            fixed_inner, fixed_w, fixed_h, prefer_cuda=True
        )
    rejected_per_seed: list[list[bool]] = []
    total_stats = {
        "cuda_available": available(),
        "cuda_used": False,
        "candidates_evaluated": 0,
        "safe_rejected": 0,
        "batches_evaluated": 0,
        "h2d_bytes": 0,
        "d2h_bytes": 0,
        "h2d_ms": 0.0,
        "kernel_ms": 0.0,
        "d2h_ms": 0.0,
    }
    for offsets in offset_batches:
        if session is not None:
            batch = session.safe_reject_batch(
                candidate_inner, candidate_w, candidate_h, offsets
            )
        else:
            batch = cuda_raster_safe_reject_batch(
                fixed_inner,
                fixed_w,
                fixed_h,
                candidate_inner,
                candidate_w,
                candidate_h,
                offsets,
                prefer_cuda=False,
            )
        rejected = [bool(v) for v in (batch.get("rejected") or [])]
        rejected_per_seed.append(rejected)
        stats = dict(batch.get("stats") or {})
        total_stats["cuda_used"] = total_stats["cuda_used"] or bool(
            stats.get("cuda_used")
        )
        total_stats["candidates_evaluated"] += int(
            stats.get("candidates_evaluated") or len(rejected)
        )
        total_stats["safe_rejected"] += sum(1 for v in rejected if v)
        total_stats["batches_evaluated"] += 1
        for key in ("h2d_bytes", "d2h_bytes"):
            total_stats[key] += int(stats.get(key) or 0)
        for key in ("h2d_ms", "kernel_ms", "d2h_ms"):
            total_stats[key] += float(stats.get(key) or 0.0)
    return {
        "rejected_per_seed": rejected_per_seed,
        "stats": total_stats,
        "cuda_active": bool(session.cuda_active()) if session is not None else False,
        "production_ready": False,
        "scope": "baseline chatty por semilla",
    }
