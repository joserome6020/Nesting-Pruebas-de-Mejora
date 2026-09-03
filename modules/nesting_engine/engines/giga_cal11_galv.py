"""Motor oculto Cal 11 Galvanizado — C++ packer_giga_cal11. No aparece en el selector."""
from __future__ import annotations

import time

from .types import NestEngineMeta, PackSheetRequest, PackSheetResult


class GigaCal11GalvEngine:
    META = NestEngineMeta(
        engine_id="giga_cal11_galv",
        display_name="GIGA Cal 11 Galv",
        description=(
            "Motor nativo Cal 11 Galvanizado (cualquier decimal Cal 11 + GALV): "
            "void-first VFM + MC mixto. No seleccionable."
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
            plate_too_small_for_vfm,
            prefill_vfm_void_cargo,
            restore_unplaced_void_cargo,
            tabla_kerf_margin,
        )

        t0 = time.perf_counter()
        kerf, margin = tabla_kerf_margin()
        n_pool = len(request.piezas or [])
        w_mm = float(request.w_placa or 0)
        h_mm = float(request.h_placa or 0)
        skip_void = plate_too_small_for_vfm(w_mm, h_mm)
        print(
            f"[GIGA-CAL11] pack n={n_pool} "
            f"{'MC' if skip_void else 'void-first+MC'} "
            f"{w_mm:.0f}x{h_mm:.0f}mm",
            flush=True,
        )
        try:
            if skip_void:
                mc_pool = list(request.piezas or [])
                vf_stats = {"filled": 0}
            else:
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
            hoja.setdefault("placa_w", float(request.w_placa or 0))
            hoja.setdefault("placa_h", float(request.h_placa or 0))
            hoja.setdefault("kerf_usado", kerf)
            hoja.setdefault("margin_usado", margin)
            if skip_void:
                n_exp = 0
                n_back = 0
                fill_stats = {}
            else:
                n_exp = expand_giga_void_cargo(hoja, mc_pool)
                n_back = restore_unplaced_void_cargo(hoja, mc_pool, restos)
                try:
                    from ..giga_cal11_galv import close_stacked_vfm_pairs

                    close_stats = close_stacked_vfm_pairs(hoja, kerf)
                except Exception as _cp_exc:
                    close_stats = {"error": str(_cp_exc)}
                    print(f"[GIGA-CAL11] close_pair skip: {_cp_exc}", flush=True)
                try:
                    from ..giga_cal11_galv import zigzag_vfm_tower_stack

                    zig_stats = zigzag_vfm_tower_stack(hoja, restos, kerf)
                except Exception as _zg_exc:
                    zig_stats = {"error": str(_zg_exc)}
                    print(f"[GIGA-CAL11] zigzag skip: {_zg_exc}", flush=True)
                fill_stats = apply_giga_pasillo_fill(
                    hoja, engine_id=cls.META.engine_id, pool=restos
                )
                fill_stats = dict(fill_stats)
                fill_stats["close_pair"] = int(close_stats.get("closed") or 0)
                fill_stats["zigzag"] = int(zig_stats.get("staggered") or 0)
                fill_stats["zigzag_pulled"] = int(zig_stats.get("pulled") or 0)
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
