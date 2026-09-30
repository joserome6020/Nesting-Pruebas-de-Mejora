"""Reglas del marcaje stick que pone el ANS y área neta de pieza.

Solo aplica al stick del ANS (entidades con XDATA ARGA_STICK). El marcaje que
ya trae el DXF de origen no se toca.

- Pieza compensada para plasma: su DXF compensado sale sin stick ANS.
- Toda otra pieza lleva stick, sin importar su área (el tope de área decide
  RTZ en nesting: ``modules.nesting_engine.regla_area_rtz``).
"""
from __future__ import annotations


def area_neta_in2(doc) -> float:
    """Área del contorno exterior mayor menos sus interiores, en in²."""
    from modules.dxf_mark.inject import (
        collect_geometry,
        drawing_units_per_inch,
        point_in_largest,
    )

    upi = drawing_units_per_inch(doc)
    # Cuerda fina: el polígono inscrito pierde ~perímetro·flecha (<0.01 in²).
    outers, inners, _ = collect_geometry(doc.modelspace(), max(0.0001 * upi, 1e-7))
    if not outers:
        return 0.0
    outer = max(outers, key=lambda o: abs(o.area))
    area = abs(outer.area) - sum(abs(h.area) for h in inners if point_in_largest(h, outer))
    return max(0.0, area) / (upi * upi)


def quitar_marcaje_ans(doc) -> int:
    """Borra del modelspace solo las entidades stick del ANS. Devuelve cuántas."""
    from modules.dxf_mark.inject import _entity_has_stick_tag

    msp = doc.modelspace()
    borrar = [e for e in msp if _entity_has_stick_tag(e)]
    for e in borrar:
        msp.delete_entity(e)
    return len(borrar)
