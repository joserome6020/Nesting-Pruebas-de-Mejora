"""Perfiles de optimización por motor de nesting (acero / placas)."""
from __future__ import annotations

import os

from .nest_engine_context import (
    DEFAULT_STEEL_ENGINE_ID,
    ENGINE_ARGA_FORCE,
    ENGINE_ARGA_LITE,
    ENGINE_BURKE_BLF,
    ENGINE_LIBNEST2D,
    ENGINE_SVGNEST_ULTRA,
    normalize_engine_id,
)

# Perfiles de intensidad (modo global ARGA_NEST_MODE) — se combinan con el motor activo.
NEST_MODES = {
    "first": {
        "mc_iterations": 1,
        "mc_lookahead_iterations": 1,
        "lookahead": False,
        "refine_hoja": False,
        "accesorios_retries": 1,
        "refinar_intentos": 0,
    },
    "fast": {
        "mc_iterations": 5,
        "mc_lookahead_iterations": 3,
        "lookahead": False,
        "refine_hoja": False,
        "accesorios_retries": 3,
        "refinar_intentos": 0,
    },
    "standard": {
        "mc_iterations": 15,
        "mc_lookahead_iterations": 5,
        "lookahead": True,
        "refine_hoja": True,
        "accesorios_retries": 14,
        "refinar_intentos": 12,
    },
    "max": {
        "mc_iterations": 30,
        "mc_lookahead_iterations": 8,
        "lookahead": True,
        "refine_hoja": True,
        "accesorios_retries": 14,
        "refinar_intentos": 12,
    },
}

# Ajustes base por motor (Fase 0: solo arga_force activo).
ENGINE_BASE_PROFILES: dict[str, dict] = {
    ENGINE_ARGA_FORCE: {
        "mc_iterations": 1,
        "mc_lookahead_iterations": 1,
        "lookahead": False,
        "refine_hoja": False,
        "accesorios_retries": 8,
        "refinar_intentos": 0,
        "continual_optimization": False,
        "rotation_step_deg": 90,  # ortogonal: 0/90/180/270
        "use_nfp": False,  # NFP solo en cavidades/orificios (C++)
        "use_nfp_cavities": True,
        "use_genetic_algorithm": False,
        "group_identical": True,
        "morphology_gap_fill": True,
        "part_in_part": True,
        "final_compact_slide": True,
        "leftover_small_retry": True,
        "lock_profile": True,
    },
    ENGINE_BURKE_BLF: {
        "mc_iterations": 10,
        "mc_lookahead_iterations": 4,
        "lookahead": True,
        "refine_hoja": True,
        "accesorios_retries": 8,
        "refinar_intentos": 6,
        "continual_optimization": False,
        "rotation_step_deg": 90,
        "use_nfp": True,
        "use_genetic_algorithm": False,
        "hill_climbing_order": True,
    },
    ENGINE_LIBNEST2D: {
        "mc_iterations": 8,
        "mc_lookahead_iterations": 3,
        "lookahead": True,
        "refine_hoja": True,
        "accesorios_retries": 6,
        "refinar_intentos": 4,
        "continual_optimization": False,
        "rotation_step_deg": 90,
        "use_nfp": True,
        "placer": "nfp",
        "selector": "largest_area_first",
    },
    ENGINE_SVGNEST_ULTRA: {
        # Prueba fast-first: nest usable en 1 gen / 90° / pop chica.
        # Si no convence: ARGA_ULTRA_FAST_FIRST=0 o quitar fast_first.
        "mc_iterations": 6,
        "mc_lookahead_iterations": 2,
        "lookahead": False,
        "refine_hoja": True,
        "accesorios_retries": 8,
        "refinar_intentos": 6,
        "continual_optimization": False,
        "continual_until_user_stops": False,
        "continual_stagnation_rounds": 2,
        "rotation_step_deg": 30,
        "use_nfp": True,
        "use_genetic_algorithm": True,
        "ga_population": 8,
        "ga_mutation_rate": 0.15,
        "part_in_part": True,
        "morphology_gap_fill": True,
        "open_cavity_fill": True,
        "common_line_lite": False,
        "fast_first": True,
        "fast_first_pop": 8,
        "fast_first_gens": 1,
        "fast_first_refine_gens": 2,
        "fast_first_rotation_deg": 90.0,
        "lock_profile": True,
        "pack_explore_then_refine": True,
    },
    # Respaldo: MC clásico 3 pases explore→refine (rápido, decente).
    ENGINE_ARGA_LITE: {
        "mc_iterations": 3,
        "mc_lookahead_iterations": 1,
        "lookahead": False,
        "refine_hoja": True,
        "accesorios_retries": 2,
        "refinar_intentos": 0,
        "continual_optimization": False,
        "rotation_step_deg": 90,
        "use_nfp": False,
        "use_genetic_algorithm": False,
        "force_parallel_seeds": 1,
        "lite_refine_passes": 3,
        "lock_profile": True,
    },
}

_DEFAULT_MODE = "first"


def _mode_overrides() -> dict:
    mode = str(os.environ.get("ARGA_NEST_MODE", _DEFAULT_MODE)).strip().lower()
    if mode in NEST_MODES:
        return dict(NEST_MODES[mode])

    custom = os.environ.get("ARGA_NEST_ITERATIONS")
    if custom:
        try:
            n = max(1, min(50, int(custom)))
        except ValueError:
            n = NEST_MODES[_DEFAULT_MODE]["mc_iterations"]
        return {
            "mc_iterations": n,
            "mc_lookahead_iterations": max(1, n // 3),
            "lookahead": n > 1,
            "refine_hoja": n > 1,
            "accesorios_retries": 1 if n <= 1 else min(14, n),
            "refinar_intentos": 0 if n <= 1 else 12,
        }
    return dict(NEST_MODES[_DEFAULT_MODE])


def get_engine_profile(engine_id: str | None = None) -> dict:
    """Perfil efectivo del motor (base del motor + overrides ARGA_NEST_MODE + hardware)."""
    from .nest_hardware import apply_nest_thread_env, hardware_nest_budget

    eid = normalize_engine_id(engine_id or DEFAULT_STEEL_ENGINE_ID)
    base = dict(ENGINE_BASE_PROFILES.get(eid, ENGINE_BASE_PROFILES[ENGINE_ARGA_FORCE]))
    mode = _mode_overrides()

    # El motor define el comportamiento principal; el modo global solo escala iteraciones
    # cuando el motor no fija continual_optimization ni lock_profile.
    if not base.get("continual_optimization") and not base.get("lock_profile"):
        for key in (
            "mc_iterations",
            "mc_lookahead_iterations",
            "lookahead",
            "refine_hoja",
            "accesorios_retries",
            "refinar_intentos",
        ):
            if key in mode:
                base[key] = mode[key]

    budget = hardware_nest_budget()
    apply_nest_thread_env(budget)
    base["nest_threads"] = budget["nest_threads"]
    base["force_parallel_seeds"] = budget["force_parallel_seeds"]
    base["plate_pool_workers"] = budget["plate_pool_workers"]
    base["logical_cpus"] = budget["logical_cpus"]
    base["ram_gb"] = budget["ram_gb"]

    # Ultra: en fast-first no inflar pop con hardware (era causa de lentitud).
    if eid == ENGINE_SVGNEST_ULTRA:
        if bool(base.get("fast_first", True)):
            cap = int(base.get("fast_first_pop", 8) or 8)
            base["ga_population"] = max(4, min(int(base.get("ga_population", 8) or 8), cap))
        else:
            base_pop = int(base.get("ga_population", 12) or 12)
            base["ga_population"] = max(base_pop, int(budget["ultra_population"]))

    # Burke / Libnest: más iteraciones si hay CPU (sin pasar de 40).
    if eid in (ENGINE_BURKE_BLF, ENGINE_LIBNEST2D) and budget["nest_threads"] >= 12:
        base["mc_iterations"] = max(int(base.get("mc_iterations", 8) or 8), min(24, budget["nest_threads"] // 2))

    base["engine_id"] = eid
    return base


def get_nest_profile() -> dict:
    """Compatibilidad: perfil del motor activo (env ARGA_NEST_ENGINE o arga_base)."""
    from .nest_engine_context import get_active_engine_id

    return get_engine_profile(get_active_engine_id())


def score_placa_simulacion(
    candidato_placa: dict,
    hoja_sim: dict,
    *,
    restos_count: int = 0,
    area_restos: float = 0.0,
    piezas_colocadas: int = 0,
    lookahead_cost: float = 0.0,
) -> float:
    """
    Menor score = mejor candidata.
    Combina costo/área útil, eficiencia, cola de restos y placa sobredimensionada.
    """
    piezas = hoja_sim.get("piezas") or []
    if not piezas:
        return float("inf")

    area = float(hoja_sim.get("area_usada", 0.0) or 0.0)
    placa_area = float(candidato_placa.get("w", 0) or 0) * float(candidato_placa.get("h", 0) or 0)
    if area <= 0 or placa_area <= 0:
        return float("inf")

    precio = float(candidato_placa.get("precio", 0.0) or 0.0)
    efi = area / placa_area
    costo_por_area = precio / area

    if efi < 0.35:
        penalizacion = 250.0 + ((1.0 - efi) ** 2) * 500.0
    elif efi < 0.55:
        penalizacion = 80.0 + ((1.0 - efi) * 120.0)
    elif efi < 0.60:
        penalizacion = 30.0 + ((1.0 - efi) * 40.0)
    else:
        penalizacion = 1.0 + ((1.0 - efi) ** 2) * 5.0

    if restos_count == 0 and efi < 0.55:
        penalizacion += (0.55 - efi) * 400.0

    penal_restos = float(restos_count) * 22.0
    if restos_count > 0:
        penal_restos += (precio / max(placa_area, 1.0)) * float(restos_count) * 140.0
        area_r = max(0.0, float(area_restos or 0.0))
        if area_r > 0.0:
            efi_cola = area_r / placa_area
            if efi_cola < 0.30 and restos_count >= 2:
                penal_restos += (0.30 - efi_cola) * 350.0
            elif efi_cola < 0.45:
                penal_restos += (0.45 - efi_cola) * 120.0

    if piezas_colocadas > 0 and restos_count > 0:
        total_sim = piezas_colocadas + restos_count
        ratio = piezas_colocadas / total_sim
        if ratio < 0.45:
            penalizacion += (0.45 - ratio) * 60.0

    if piezas_colocadas >= 12 and efi < 0.50:
        penalizacion += (0.50 - efi) * float(piezas_colocadas) * 3.5

    return (costo_por_area * penalizacion) + penal_restos + float(lookahead_cost)


def score_placa_lower_bound(
    candidato_placa: dict,
    *,
    area_piezas_pendientes: float,
) -> float:
    """
    Cota inferior segura del score (mejor caso imaginable).
    Asume: se usa min(área piezas, área placa), efi=100%, sin restos ni lookahead.
    Si esta cota >= mejor_score actual, nestear la candidata no puede ganar.
    """
    placa_area = float(candidato_placa.get("w", 0) or 0) * float(
        candidato_placa.get("h", 0) or 0
    )
    precio = float(candidato_placa.get("precio", 0.0) or 0.0)
    area_pend = max(0.0, float(area_piezas_pendientes or 0.0))
    if placa_area <= 0.0 or area_pend <= 0.0:
        return float("inf")
    # Mejor área útil posible (sin scrap forzado por forma).
    area_max = min(area_pend, placa_area)
    if area_max <= 1e-9:
        return float("inf")
    # Con efi=1.0 la penalización mínima del score es 1.0 y restos/lookahead=0.
    return precio / area_max
