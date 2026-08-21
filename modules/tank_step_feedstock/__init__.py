"""Complemento feedstock STEP → DXF (dentro de AutoDXF), sin reemplazar Inventor.

Flujo:
  AutoDXF/*.stp | AutoDXF/STEP/*.stp
    → OCCT (placas planas MVP)
    → AutoDXF/FROM_STEP/Cal …/*.dxf
    → ProcesadorDXF / PARTS (mismo contrato de capas IV_*)
"""
from __future__ import annotations

from .discover import FROM_STEP_DIRNAME, STEP_SUBDIR_NAME, discover_steps_in_autodxf
from .pipeline import StepFeedstockResult, process_autodxf_step_feedstock

__all__ = [
    "FROM_STEP_DIRNAME",
    "STEP_SUBDIR_NAME",
    "StepFeedstockResult",
    "discover_steps_in_autodxf",
    "process_autodxf_step_feedstock",
]
