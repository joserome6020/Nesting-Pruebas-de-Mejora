"""Motor 1 — ARGA FORCE (ex ARGA Base / pizarrón)."""
from __future__ import annotations

import time

from .types import NestEngineMeta, PackSheetRequest, PackSheetResult


class ArgaForceEngine:
    META = NestEngineMeta(
        engine_id="arga_force",
        display_name="ARGA FORCE",
        description=(
            "Pizarrón ARGA: mayor In² → agrupar iguales → morfología y huecos "
            "entre piezas → cuántos pequeños caben. Toque Burke/NFP en cavidades "
            "cóncavas (p. ej. VFM)."
        ),
        phase=1,
        status="ready",
        inspiration="Diagrama operativo ARGA (pizarrón) + refuerzo cóncavo tipo motor 2",
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
                cancel_checker=request.cancel_checker,
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


# Alias de compatibilidad (imports antiguos).
ArgaBaseEngine = ArgaForceEngine
