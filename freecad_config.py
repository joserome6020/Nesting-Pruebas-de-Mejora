# freecad_config.py
# Configuración central para la integración FreeCADCmd (headless)

from __future__ import annotations
from dataclasses import dataclass
import os

@dataclass
class FreeCADConfig:
    # Ruta al ejecutable FreeCADCmd.exe
    freecadcmd: str = r"C:\Program Files\FreeCAD 1.0\bin\FreeCADCmd.exe"

    # Ruta absoluta al script headless que convierte DXF -> STEP
    script_py: str = r".\freecad_batch_dxf_to_step.py"

    # Carpeta donde la app exporta DXF (entrada de FreeCAD)
    dxf_in_dir: str = r".\EXPORT_DXF"

    # Carpeta donde se van a guardar los STEP generados
    step_out_dir: str = r".\EXPORT_STEP"

    # Parámetros de conversión (ajusta a tu proceso)
    thickness_mm: float = 6.35          # 1/4\" = 6.35mm (cambia según tu placa)
    outer_layer: str = "OUTER_CUT"      # nombre de layer exterior (si aplica)
    inner_layer: str = "INNER_CUT"      # nombre de layer interior (si aplica)
    scale: float = 1.0                  # 25.4 si tu DXF viene en pulgadas y lo quieres en mm

    @staticmethod
    def from_env() -> "FreeCADConfig":
        """Permite configurar por variables de entorno sin tocar el código."""
        cfg = FreeCADConfig()
        cfg.freecadcmd = os.getenv("FREECAD_CMD", cfg.freecadcmd)
        cfg.script_py  = os.getenv("FREECAD_SCRIPT", cfg.script_py)
        cfg.dxf_in_dir = os.getenv("FREECAD_DXF_IN", cfg.dxf_in_dir)
        cfg.step_out_dir = os.getenv("FREECAD_STEP_OUT", cfg.step_out_dir)

        thk = os.getenv("FREECAD_THK_MM")
        if thk:
            try: cfg.thickness_mm = float(thk)
            except: pass

        cfg.outer_layer = os.getenv("FREECAD_LAYER_OUTER", cfg.outer_layer)
        cfg.inner_layer = os.getenv("FREECAD_LAYER_INNER", cfg.inner_layer)

        sc = os.getenv("FREECAD_SCALE")
        if sc:
            try: cfg.scale = float(sc)
            except: pass

        return cfg
