# integration_freecad.py
# Punto de integración: llamar después de exportar DXF (desde tu app)

from __future__ import annotations
from freecad_config import FreeCADConfig
from freecad_runner import run_freecad_batch

def make_steps_from_exported_dxfs(
    dxf_folder: str,
    step_folder: str,
    *,
    thickness_mm: float = 6.35,
    outer_layer: str = "OUTER_CUT",
    inner_layer: str = "INNER_CUT",
    scale: float = 1.0,
) -> None:
    """Convierte DXF->STEP en lote.

    - dxf_folder: carpeta donde tu app ya exportó los DXF (uno por hoja, o varios).
    - step_folder: carpeta destino para STEP.
    """
    cfg = FreeCADConfig.from_env()
    res = run_freecad_batch(
        dxf_folder,
        step_folder,
        thickness_mm=thickness_mm,
        outer_layer=outer_layer,
        inner_layer=inner_layer,
        scale=scale,
        cfg=cfg,
    )
    if not res.ok:
        raise RuntimeError(
            "FreeCADCmd falló.\n"
            f"ReturnCode: {res.returncode}\n"
            f"CMD: {' '.join(res.cmd)}\n"
            f"STDERR:\n{res.stderr}\n"
            f"STDOUT:\n{res.stdout}\n"
        )
