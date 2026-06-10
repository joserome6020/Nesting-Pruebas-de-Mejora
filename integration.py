# -*- coding: utf-8 -*-
"""
Puente de integración (tu app -> FreeCAD).

Llamar después de que tu app exporte DXF (nesteo) a una carpeta.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from freecad_runner import run_freecad_batch


def convert_exported_dxf_to_step(
    dxf_folder: str,
    *,
    step_folder: Optional[str] = None,
    thickness_mm: float = 6.35,
    scale: float = 1.0,
    outer_layer: str = "OUTER_CUT",
    inner_layer: str = "INTER_CUT",
    ignore_layers_csv: str = "MARK,Mark,PLATE,Plate",
    one_step: bool = True,
) -> int:
    """
    Convierte todos los DXF en dxf_folder a STEP.
    Retorna el exit code de FreeCADCmd.
    """
    dxf_folder = str(Path(dxf_folder))
    step_folder = str(Path(step_folder) if step_folder else Path(dxf_folder))

    return run_freecad_batch(
        dxf_folder,
        step_folder,
        thk_mm=thickness_mm,
        scale=scale,
        outer_layer=outer_layer,
        inner_layer=inner_layer,
        ignore_layers_csv=ignore_layers_csv,
        one_step=one_step,
    )
