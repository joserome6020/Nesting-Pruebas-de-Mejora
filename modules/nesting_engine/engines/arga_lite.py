"""Motor ARGA LITE — MC clásico rápido (respaldo del nest pre-FORCE/Ultra)."""
from __future__ import annotations

import time

from .types import NestEngineMeta, PackSheetRequest, PackSheetResult


class ArgaLiteEngine:
    META = NestEngineMeta(
        engine_id="arga_lite",
        display_name="ARGA LITE",
        description=(
            "MC clásico en 3 pases explore→refine (parte del mejor anterior). "
            "Rápido y decente: respaldo urgente sin quedar en nest ‘malo malo’."
        ),
        phase=0,
        status="ready",
        inspiration="Motor pre-engines + refine-from-best tipo Ultra light",
    )

    @classmethod
    def meta(cls) -> NestEngineMeta:
        return cls.META

    @classmethod
    def is_ready(cls) -> bool:
        try:
            from .. import algorithm_cpp

            return hasattr(algorithm_cpp, "empaquetar_una_hoja_mc")
        except Exception:
            return False

    @classmethod
    def empaquetar(cls, request: PackSheetRequest) -> PackSheetResult:
        from ..algorithm_bridge import empaquetar_una_hoja_arga_lite

        t0 = time.perf_counter()
        try:
            hoja, restos = empaquetar_una_hoja_arga_lite(
                request.piezas,
                request.w_placa,
                request.h_placa,
                kerf_override=request.kerf_override,
                margin_override=request.margin_override,
                opt_override=request.opt_override,
                corner_override=request.corner_override,
                limite_poly=request.limite_poly,
                mc_iterations=request.mc_iterations,
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
