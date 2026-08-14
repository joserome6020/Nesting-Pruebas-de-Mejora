"""Tipos compartidos del registro de motores de nesting."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..cut_gaps_table import PLATE_TO_PIECE_DEFAULT_IN


@dataclass(frozen=True)
class NestEngineMeta:
    engine_id: str
    display_name: str
    description: str
    phase: int
    status: str  # ready | pending | error
    inspiration: str = ""
    supports_continual_optimization: bool = False


@dataclass
class PackSheetRequest:
    piezas: list
    w_placa: float
    h_placa: float
    kerf_override: float = 0.15
    # Placa→pieza es constante de planta (foto TABLA GAPS DE CORTE = 0.250").
    # El fallback debe respetarla — antes daba 0.15" y bajaba el margen final.
    margin_override: float = PLATE_TO_PIECE_DEFAULT_IN
    opt_override: str = "OPTIMIZAR LARGO Y ANCHO"
    corner_override: str = "INFERIOR IZQUIERDA"
    limite_poly: Any = None
    mc_iterations: Optional[int] = None
    cancel_checker: Optional[Callable[[], bool]] = None


@dataclass
class PackSheetResult:
    hoja: dict
    restos: list
    engine_id: str
    elapsed_s: float = 0.0
    error: Optional[str] = None
    runtime: Optional[dict] = None


@dataclass
class EngineJobMetrics:
    engine_id: str
    display_name: str
    status: str
    elapsed_s: float = 0.0
    hojas: int = 0
    piezas_colocadas: int = 0
    piezas_pendientes: int = 0
    eficiencia_promedio: float = 0.0
    costo_total: float = 0.0
    error: Optional[str] = None
    advertencias: list[str] = field(default_factory=list)


class NestEngineNotReadyError(RuntimeError):
    """Motor registrado pero aún no implementado en su fase correspondiente."""
