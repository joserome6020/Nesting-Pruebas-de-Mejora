"""Perfil de optimización del motor de nesting (iteraciones MC, lookahead)."""
from __future__ import annotations

import os

NEST_MODES = {
    "first": {
        "mc_iterations": 1,
        "mc_lookahead_iterations": 1,
        "lookahead": False,
        "refine_hoja": False,
        "accesorios_retries": 1,
        "refinar_intentos": 0,
    },
    "fast": {"mc_iterations": 5, "mc_lookahead_iterations": 3, "lookahead": False, "refine_hoja": False, "accesorios_retries": 3, "refinar_intentos": 0},
    "standard": {"mc_iterations": 15, "mc_lookahead_iterations": 5, "lookahead": True, "refine_hoja": True, "accesorios_retries": 14, "refinar_intentos": 12},
    "max": {"mc_iterations": 30, "mc_lookahead_iterations": 8, "lookahead": True, "refine_hoja": True, "accesorios_retries": 14, "refinar_intentos": 12},
}

_DEFAULT_MODE = "first"


def get_nest_profile() -> dict:
    """Perfil activo: env ARGA_NEST_MODE o ARGA_NEST_ITERATIONS."""
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

    # Última tanda en esta placa: castigo fuerte si la placa queda casi vacía
    if restos_count == 0 and efi < 0.55:
        penalizacion += (0.55 - efi) * 400.0

    # Castigo por piezas que quedarían sin colocar en esta placa
    penal_restos = float(restos_count) * 22.0
    if restos_count > 0:
        penal_restos += (precio / max(placa_area, 1.0)) * float(restos_count) * 140.0
        area_r = max(0.0, float(area_restos or 0.0))
        if area_r > 0.0:
            efi_cola = area_r / placa_area
            # Dejar mucha cola pequeña que llenará otra placa entera → malo
            if efi_cola < 0.30 and restos_count >= 2:
                penal_restos += (0.30 - efi_cola) * 350.0
            elif efi_cola < 0.45:
                penal_restos += (0.45 - efi_cola) * 120.0

    # Preferir colocar más piezas por iteración cuando aún hay pool grande
    if piezas_colocadas > 0 and restos_count > 0:
        total_sim = piezas_colocadas + restos_count
        ratio = piezas_colocadas / total_sim
        if ratio < 0.45:
            penalizacion += (0.45 - ratio) * 60.0

    # Muchas piezas pequeñas en placa casi vacía (ej. 59 pzas al 42% en 240x96)
    if piezas_colocadas >= 12 and efi < 0.50:
        penalizacion += (0.50 - efi) * float(piezas_colocadas) * 3.5

    return (costo_por_area * penalizacion) + penal_restos + float(lookahead_cost)
