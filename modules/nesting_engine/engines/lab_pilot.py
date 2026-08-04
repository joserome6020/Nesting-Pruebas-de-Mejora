"""Piloto rápido basado en la ruta timeline validada del LAB (experimental)."""
from __future__ import annotations

import time

from .types import NestEngineMeta, PackSheetRequest, PackSheetResult


class ArgaLabPilotEngine:
    """Motor experimental: nunca se selecciona como predeterminado."""

    META = NestEngineMeta(
        engine_id="arga_lab_pilot",
        display_name="ARGA PILOT RÁPIDO",
        description=(
            "Piloto timeline experimental. No operativo; no es el camino CUDA."
        ),
        phase=1,
        status="ready",
        inspiration="LAB timeline + shortlist de anclas exactas",
    )

    @classmethod
    def meta(cls) -> NestEngineMeta:
        return cls.META

    @classmethod
    def is_ready(cls) -> bool:
        from ..lab_pilot_adapter import is_ready

        return is_ready()

    @classmethod
    def empaquetar(cls, request: PackSheetRequest) -> PackSheetResult:
        from ..lab_pilot_adapter import pack_one_sheet

        started = time.perf_counter()
        try:
            if request.cancel_checker and request.cancel_checker():
                return PackSheetResult(
                    hoja={"piezas": [], "area_usada": 0.0, "eficiencia": 0.0},
                    restos=list(request.piezas or []),
                    engine_id=cls.META.engine_id,
                    elapsed_s=time.perf_counter() - started,
                )
            hoja, restos = pack_one_sheet(
                list(request.piezas or []),
                plate_w_mm=float(request.w_placa),
                plate_h_mm=float(request.h_placa),
                kerf_in=float(request.kerf_override),
                margin_in=float(request.margin_override),
                opt=str(request.opt_override),
                corner=str(request.corner_override),
                mc_iterations=max(1, int(request.mc_iterations or 1)),
                limite_poly=request.limite_poly,
            )
            return PackSheetResult(
                hoja=hoja,
                restos=restos,
                engine_id=cls.META.engine_id,
                elapsed_s=time.perf_counter() - started,
            )
        except Exception as exc:
            return PackSheetResult(
                hoja={"piezas": [], "area_usada": 0.0, "eficiencia": 0.0},
                restos=list(request.piezas or []),
                engine_id=cls.META.engine_id,
                elapsed_s=time.perf_counter() - started,
                error=str(exc),
            )
