"""Marcaje stick para DXF de piezas Arga Nesting Suite / AutoDXF."""

from modules.dxf_mark.inject import (
    AUTODXF_MARK_LAYER,
    DEFAULT_CLEARANCE_IN,
    DEFAULT_TEXT_HEIGHT_IN,
    MARK_LAYER,
    MAX_MARK_HEIGHT_IN,
    MIN_MARK_HEIGHT_IN,
    PREFERRED_MIN_MARK_HEIGHT_IN,
    InjectResult,
    inject_mark_into_dxf,
    mark_text_from_dxf_path,
    tiene_marcaje_stick,
)
from modules.dxf_mark.pipeline import aplicar_marcaje_autodxf, aplicar_marcaje_nesting

__all__ = [
    "AUTODXF_MARK_LAYER",
    "DEFAULT_CLEARANCE_IN",
    "DEFAULT_TEXT_HEIGHT_IN",
    "MARK_LAYER",
    "MAX_MARK_HEIGHT_IN",
    "MIN_MARK_HEIGHT_IN",
    "PREFERRED_MIN_MARK_HEIGHT_IN",
    "InjectResult",
    "aplicar_marcaje_autodxf",
    "aplicar_marcaje_nesting",
    "inject_mark_into_dxf",
    "mark_text_from_dxf_path",
    "tiene_marcaje_stick",
]
