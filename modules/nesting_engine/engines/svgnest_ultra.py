"""Motor 4 — SVGNest Ultra (NFP + GA + optimización continua)."""
from __future__ import annotations

import time

from .types import NestEngineMeta, PackSheetRequest, PackSheetResult


class SvgnestUltraEngine:
    META = NestEngineMeta(
        engine_id="svgnest_ultra",
        display_name="SVGNest Ultra",
        description=(
            "Deepnest/SVGNest: NFP cacheado + GA + score bbox + "
            "part-in-part + cavidades VFM/C (perfil taller)."
        ),
        phase=4,
        status="ready",
        inspiration="Deepnest / SVGNest / NestFab + morfología ARGA",
        supports_continual_optimization=False,
    )

    @classmethod
    def meta(cls) -> NestEngineMeta:
        return cls.META

    @classmethod
    def is_ready(cls) -> bool:
        try:
            from .. import algorithm_cpp

            return hasattr(algorithm_cpp, "empaquetar_una_hoja_svgnest_ultra")
        except Exception:
            return False

    @classmethod
    def empaquetar(cls, request: PackSheetRequest) -> PackSheetResult:
        from ..algorithm_bridge import empaquetar_una_hoja_svgnest_ultra
        from ..nest_optimization import get_engine_profile

        profile = get_engine_profile(cls.META.engine_id)
        t0 = time.perf_counter()
        try:
            hoja, restos = empaquetar_una_hoja_svgnest_ultra(
                request.piezas,
                request.w_placa,
                request.h_placa,
                kerf_override=request.kerf_override,
                margin_override=request.margin_override,
                opt_override=request.opt_override,
                corner_override=request.corner_override,
                limite_poly=request.limite_poly,
                ga_generations=request.mc_iterations or profile.get("mc_iterations", 30),
                ga_population=profile.get("ga_population", 30),
                rotation_step_deg=profile.get("rotation_step_deg", 15.0),
                part_in_part=profile.get("part_in_part", True),
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
