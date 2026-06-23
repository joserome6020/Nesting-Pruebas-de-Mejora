"""
Inventario cobre (CU): barras largo 144" × 2–6" para nesting 1D (cu_largos_nesting).
"""
from __future__ import annotations

from typing import List, Tuple

from .sheet_integrity import validar_colocacion_completa

# Barras estándar de cobre en Herinox
LARGO_CU_LONGITUD_IN = 144.0
LARGO_CU_ANCHO_MIN_IN = 2.0
LARGO_CU_ANCHO_MAX_IN = 6.0
LARGO_CU_LONGITUD_TOL_IN = 0.5
LARGO_CU_ANCHO_TOL_IN = 0.02


def _dims_placa_in(placa: dict) -> Tuple[float, float]:
    w_mm = float(placa.get("w") or 0.0)
    h_mm = float(placa.get("h") or 0.0)
    w_in = w_mm / 25.4
    h_in = h_mm / 25.4
    return w_in, h_in


def es_placa_largo_cu(placa: dict) -> bool:
    """True si el stock es barra CU 144" × 2–6" (lógica largos 1D)."""
    if not isinstance(placa, dict):
        return False
    w_in, h_in = _dims_placa_in(placa)
    if w_in <= 0 or h_in <= 0:
        return False
    largo_in = max(w_in, h_in)
    ancho_in = min(w_in, h_in)
    return (
        abs(largo_in - LARGO_CU_LONGITUD_IN) <= LARGO_CU_LONGITUD_TOL_IN
        and (LARGO_CU_ANCHO_MIN_IN - LARGO_CU_ANCHO_TOL_IN)
        <= ancho_in
        <= (LARGO_CU_ANCHO_MAX_IN + LARGO_CU_ANCHO_TOL_IN)
    )


def inventario_barras_largos_cu(placas_ok: List[dict]) -> List[dict]:
    """Filtra inventario a barras CU 144\"×2–6\" (ignora placas comerciales u otros formatos)."""
    return [placa for placa in (placas_ok or []) if es_placa_largo_cu(placa)]


def validar_inventario_cu_resultado(
    piezas: List[dict],
    resultado: dict,
) -> Tuple[bool, str]:
    """True si todas las piezas del pack quedaron colocadas en hojas del resultado."""
    return validar_colocacion_completa(
        piezas,
        (resultado or {}).get("hojas") or [],
        piezas_pendientes=(resultado or {}).get("piezas_pendientes"),
    )
