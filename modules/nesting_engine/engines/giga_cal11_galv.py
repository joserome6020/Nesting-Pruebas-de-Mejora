"""Motor oculto Cal 11 Galvanizado — C++ packer_giga_cal11. No aparece en el selector."""
from __future__ import annotations

import time

from .types import NestEngineMeta, PackSheetRequest, PackSheetResult


class GigaCal11GalvEngine:
    META = NestEngineMeta(
        engine_id="giga_cal11_galv",
        display_name="GIGA Cal 11 Galv",
        description=(
            "Motor nativo 0.11811_GALVANIZADO: void-first VFM + MC mixto. "
            "No seleccionable."
        ),
        phase=0,
        status="ready",
        inspiration="BLF+NFP frame-first (Cal 11 Galv GIGA)",
    )

    @classmethod
    def meta(cls) -> NestEngineMeta:
        return cls.META

    @classmethod
    def is_ready(cls) -> bool:
        try:
            from .. import algorithm_cpp

            return hasattr(algorithm_cpp, "empaquetar_una_hoja_giga_cal11")
        except Exception:
            return False

    @classmethod
    def empaquetar(cls, request: PackSheetRequest) -> PackSheetResult:
        from ..algorithm_bridge import empaquetar_una_hoja_giga_cal11
        from ..giga_cal11_galv import (
            apply_giga_pasillo_fill,
            expand_giga_void_cargo,
            prefill_vfm_void_cargo,
            restore_unplaced_void_cargo,
            tabla_kerf_margin,
        )

        t0 = time.perf_counter()
        kerf, margin = tabla_kerf_margin()
        n_pool = len(request.piezas or [])
        print(
            f"[GIGA-CAL11] pack n={n_pool} void-first+MC "
            f"{float(request.w_placa):.0f}x{float(request.h_placa):.0f}mm",
            flush=True,
        )
        try:
            mc_pool, vf_stats = prefill_vfm_void_cargo(
                list(request.piezas or []), kerf
            )
            hoja, restos = empaquetar_una_hoja_giga_cal11(
                mc_pool,
                request.w_placa,
                request.h_placa,
                kerf_override=kerf,
                margin_override=margin,
                opt_override=request.opt_override,
                corner_override=request.corner_override,
                limite_poly=request.limite_poly,
                cancel_checker=request.cancel_checker,
            )
            hoja = dict(hoja or {})
            restos = list(restos or [])
            n_exp = expand_giga_void_cargo(hoja, mc_pool)
            n_back = restore_unplaced_void_cargo(hoja, mc_pool, restos)
            fill_stats = apply_giga_pasillo_fill(
                hoja, engine_id=cls.META.engine_id, pool=restos
            )
            fill_stats = dict(fill_stats)
            fill_stats["void_first"] = int(vf_stats.get("filled") or 0)
            fill_stats["void_expand"] = int(n_exp)
            fill_stats["cargo_restos"] = int(n_back)
            hoja["giga_cal11_galv"] = True
            hoja["giga_fill"] = dict(fill_stats)
            hoja["engine_pack"] = cls.META.engine_id
            hoja["kerf_usado"] = kerf
            hoja["margin_usado"] = margin
            hoja.setdefault("placa_w", float(request.w_placa or 0))
            hoja.setdefault("placa_h", float(request.h_placa or 0))
            n_ok = len(hoja.get("piezas") or [])
            print(
                f"[GIGA-CAL11] motor nativo colocadas={n_ok} restos={len(restos or [])} "
                f"kerf={kerf:.3f}in margin={margin:.3f}in fill={fill_stats} "
                f"t={time.perf_counter() - t0:.2f}s",
                flush=True,
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
