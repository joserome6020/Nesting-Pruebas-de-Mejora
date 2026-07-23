"""Wrappers OCCT experimentales — no usar desde el flujo FreeCAD de producción."""

from .dxf_to_step import (
    export_dxf_to_step_freecad_batch,
    export_dxf_to_step_robot_camas,
    thickness_mm_from_dxf_name,
)
from .occt_runtime import ensure_ocp, write_step_shape, write_step_xcaf
from .step_io import (
    StepDisplayData,
    TriangleMesh,
    load_step_display,
    load_step_mesh,
    read_step_shape,
    tessellate_shape,
)
from .step_paths import descubrir_steps_para_app, listar_steps_en_dirs, step_dirs_bajo_export_cad

__all__ = [
    "ensure_ocp",
    "write_step_shape",
    "write_step_xcaf",
    "TriangleMesh",
    "StepDisplayData",
    "load_step_mesh",
    "load_step_display",
    "read_step_shape",
    "tessellate_shape",
    "descubrir_steps_para_app",
    "listar_steps_en_dirs",
    "step_dirs_bajo_export_cad",
    "export_dxf_to_step_freecad_batch",
    "export_dxf_to_step_robot_camas",
    "thickness_mm_from_dxf_name",
]
