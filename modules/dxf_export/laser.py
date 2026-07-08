"""Canal láser normal — DXF fuente 1:1 o polígono nest."""
from __future__ import annotations

from modules.nest_exporter import (
    _export_block_at_placement,
    _export_placed_geometry,
    _export_source_dxf_at_placement,
)


def export_piece(
    msp,
    doc,
    p: dict,
    *,
    draw_holes: bool = True,
    draw_marks: bool = True,
    strict: bool = True,
    cache_blocks: dict | None = None,
    sheet: dict | None = None,
) -> None:
    ruta = str(p.get("ruta") or "").strip()
    prefer_source = bool(p.get("prefer_source_dxf"))
    compensated = bool(p.get("compensated"))
    cache_blocks = cache_blocks if cache_blocks is not None else {}

    if prefer_source and not compensated and ruta:
        _export_source_dxf_at_placement(
            msp, doc, p, draw_marks=draw_marks, strict=strict
        )
    elif not prefer_source and not compensated and ruta:
        from modules.nest_exporter import _export_block_at_placement

        _export_block_at_placement(msp, doc, cache_blocks, p)
    else:
        _export_placed_geometry(
            msp, p, doc=doc, draw_holes=draw_holes, draw_marks=draw_marks, sheet=sheet
        )
