"""Motor 2 — Burke BLF + NFP (Clipper2 Minkowski)."""
from __future__ import annotations

import time

from .types import NestEngineMeta, PackSheetRequest, PackSheetResult


class BurkeBlfEngine:
    META = NestEngineMeta(
        engine_id="burke_blf",
        display_name="Burke BLF + NFP",
        description="Bottom-left-fill con No-Fit Polygon (Burke 2006) y hill-climbing de orden.",
        phase=2,
        status="ready",
        inspiration="Burke 2006 / Clipper2 NFP",
    )

    @classmethod
    def meta(cls) -> NestEngineMeta:
        return cls.META

    @classmethod
    def is_ready(cls) -> bool:
        try:
            from .. import algorithm_cpp

            return hasattr(algorithm_cpp, "empaquetar_una_hoja_burke_blf")
        except Exception:
            return False

    @classmethod
    def empaquetar(cls, request: PackSheetRequest) -> PackSheetResult:
        from ..algorithm_bridge import empaquetar_una_hoja_burke_blf
        from ..nest_optimization import get_engine_profile

        profile = get_engine_profile(cls.META.engine_id)
        t0 = time.perf_counter()
        try:
            hoja, restos = empaquetar_una_hoja_burke_blf(
                request.piezas,
                request.w_placa,
                request.h_placa,
                kerf_override=request.kerf_override,
                margin_override=request.margin_override,
                opt_override=request.opt_override,
                corner_override=request.corner_override,
                limite_poly=request.limite_poly,
                hill_climb_iterations=request.mc_iterations or profile.get("mc_iterations", 10),
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
