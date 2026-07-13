"""Motor 3 — libnest2d-style (NfpPlacer + FirstFit)."""
from __future__ import annotations

import time

from .types import NestEngineMeta, PackSheetRequest, PackSheetResult


class Libnest2dEngine:
    META = NestEngineMeta(
        engine_id="libnest2d",
        display_name="libnest2d",
        description="NfpPlacer + FirstFit con selectores (área, perímetro, ancho, alto).",
        phase=3,
        status="ready",
        inspiration="tamasmeszaros/libnest2d (Clipper2 adapter)",
    )

    @classmethod
    def meta(cls) -> NestEngineMeta:
        return cls.META

    @classmethod
    def is_ready(cls) -> bool:
        try:
            from .. import algorithm_cpp

            return hasattr(algorithm_cpp, "empaquetar_una_hoja_libnest2d")
        except Exception:
            return False

    @classmethod
    def empaquetar(cls, request: PackSheetRequest) -> PackSheetResult:
        from ..algorithm_bridge import empaquetar_una_hoja_libnest2d
        from ..nest_optimization import get_engine_profile

        profile = get_engine_profile(cls.META.engine_id)
        t0 = time.perf_counter()
        try:
            hoja, restos = empaquetar_una_hoja_libnest2d(
                request.piezas,
                request.w_placa,
                request.h_placa,
                kerf_override=request.kerf_override,
                margin_override=request.margin_override,
                opt_override=request.opt_override,
                corner_override=request.corner_override,
                limite_poly=request.limite_poly,
                selector_iterations=request.mc_iterations or profile.get("mc_iterations", 8),
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
