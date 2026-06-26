"""
Exportación DXF segmentada por canal.

- laser: nesteo normal 1:1 (sin compensación plasma)
- cobre: largos CU (lógica específica)
- plasma: desfase compensado Robot Plasma
"""
from modules.dxf_export.dispatcher import export_piece

__all__ = ["export_piece"]
