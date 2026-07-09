"""
Exportación DXF segmentada por canal.

- laser: nesteo normal 1:1 (sin compensación plasma)
- cobre: largos CU (lógica específica)
- cobre_nest: hoja completa de cobre (madre / RTZCU)
- plasma: desfase compensado Robot Plasma
"""
from modules.dxf_export.cobre_nest import export_cobre_hoja_to_dxf
from modules.dxf_export.dispatcher import export_piece

__all__ = ["export_piece", "export_cobre_hoja_to_dxf"]
