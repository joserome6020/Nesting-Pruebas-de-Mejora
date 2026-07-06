"""
Despachador de exportación por pieza — un canal por módulo, sin mezclar lógica.
"""
from __future__ import annotations

from modules.nesting_engine.dxf_export_log import resolve_export_mode


def export_piece(
    msp,
    doc,
    p: dict,
    *,
    draw_holes: bool = True,
    draw_marks: bool = True,
    strict: bool = True,
    solo_cobre: bool = False,
    cache_blocks: dict | None = None,
    sheet: dict | None = None,
    all_piece_bounds: list | None = None,
) -> tuple[str, bool]:
    """
    Exporta una pieza al modelspace.
    Devuelve (modo, ok).
    """
    cache_blocks = cache_blocks if cache_blocks is not None else {}
    mode = resolve_export_mode(p)

    if bool(p.get("plasma_export")):
        from modules.dxf_export.plasma import export_plasma_placement

        ok = export_plasma_placement(
            msp,
            doc,
            p,
            draw_holes=draw_holes,
            draw_marks=draw_marks,
            sheet=sheet,
            all_piece_bounds=all_piece_bounds,
        )
        return mode, ok

    if bool(p.get("compensated_plasma_source")) and str(p.get("ruta") or "").strip():
        from modules.dxf_export.plasma import export_compensated_plasma_from_source
        from modules.nest_exporter import _export_placed_geometry

        stats = export_compensated_plasma_from_source(
            msp,
            doc,
            p,
            draw_holes=draw_holes,
            draw_marks=draw_marks,
        )
        ok = int(stats.get("outer", 0) or 0) > 0
        if not ok:
            ok = bool(
                _export_placed_geometry(
                    msp, p, draw_holes=draw_holes, draw_marks=draw_marks
                )
            )
        return mode, ok

    if bool(p.get("cu_largos_piece")):
        from modules.dxf_export.cobre import export_piece as export_cobre

        ok = export_cobre(
            msp,
            doc,
            p,
            draw_holes=draw_holes,
            draw_marks=draw_marks,
            strict=strict,
            sheet=sheet,
        )
        return mode, ok

    from modules.dxf_export.laser import export_piece as export_laser

    export_laser(
        msp,
        doc,
        p,
        draw_holes=draw_holes,
        draw_marks=draw_marks,
        strict=strict,
        cache_blocks=cache_blocks,
    )
    return mode, True
