"""Motor ARGA APEX — calidad + velocidad (NFP fino + hole-fill, sin doble empaque)."""
from __future__ import annotations

import time

from .types import NestEngineMeta, PackSheetRequest, PackSheetResult


class ArgaApexEngine:
    META = NestEngineMeta(
        engine_id="arga_apex",
        display_name="ARGA APEX",
        description=(
            "APEX: OCCT + ML sembrado (colmena nests) + CUDA si hay + NFP/GA + Venom. "
            "Aprende de nests reales. No altera LITE/Ultra/Force."
        ),
        phase=5,
        status="ready",
        inspiration="Nesting industrial (NFP + hole-fill + refine) orquestado ARGA",
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
        from ..algorithm_bridge import empaquetar_una_hoja_arga_apex

        t0 = time.perf_counter()
        try:
            hoja, restos = empaquetar_una_hoja_arga_apex(
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
