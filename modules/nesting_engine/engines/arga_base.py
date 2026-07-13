"""Motor 1 — ARGA Base (pizarrón)."""
from __future__ import annotations

import time

from .types import NestEngineMeta, PackSheetRequest, PackSheetResult


class ArgaBaseEngine:
    META = NestEngineMeta(
        engine_id="arga_base",
        display_name="ARGA Base",
        description=(
            "Coloca por mayor área, agrupa piezas idénticas, aprovecha huecos "
            "y rellena con piezas pequeñas."
        ),
        phase=1,
        status="ready",
        inspiration="Diagrama operativo ARGA",
    )

    @classmethod
    def meta(cls) -> NestEngineMeta:
        return cls.META

    @classmethod
    def is_ready(cls) -> bool:
        return True

    @classmethod
    def empaquetar(cls, request: PackSheetRequest) -> PackSheetResult:
        from ..algorithm_bridge import empaquetar_una_hoja_arga_base

        t0 = time.perf_counter()
        try:
            hoja, restos = empaquetar_una_hoja_arga_base(
                request.piezas,
                request.w_placa,
                request.h_placa,
                kerf_override=request.kerf_override,
                margin_override=request.margin_override,
                opt_override=request.opt_override,
                corner_override=request.corner_override,
                limite_poly=request.limite_poly,
            )
            return PackSheetResult(
                hoja=hoja,
                restos=restos,
                engine_id=cls.META.engine_id,
                elapsed_s=time.perf_counter() - t0,
            )
        except Exception as exc:
            return PackSheetResult(
                hoja={"piezas": [], "area_usada": 0.0, "eficiencia": 0.0},
                restos=list(request.piezas or []),
                engine_id=cls.META.engine_id,
                elapsed_s=time.perf_counter() - t0,
                error=str(exc),
            )
