"""Canal cobre largos — sin_gap: CUT_OUTER + INNER + MARK + BAR_START (sin Plate/CUT_CU).

Marcaje stick (LINE) en sin_gap (CyPTube) y en canales con STEP (acero / cobre con_gap).
"""
from __future__ import annotations

import os

from modules.nesting_engine.dxf_export_log import log
from modules.nest_exporter import (
    _export_cu_largos_from_source,
    _export_placed_geometry,
    _fail_export,
    _msp_count,
    _msp_snapshot,
    _piece_label,
)


def export_piece(
    msp,
    doc,
    p: dict,
    *,
    draw_holes: bool = True,
    draw_marks: bool = True,
    strict: bool = True,
    sheet: dict | None = None,
) -> bool:
    """
    Cobre largos: DXF fuente 1:1 si hay ruta; si no, contorno del nest.
    Nunca pasa por el canal láser (evita omitir CUT_OUTER).
    """
    label = _piece_label(p)
    ruta = str(p.get("ruta") or "").strip()
    count_before = _msp_count(msp)

    if bool(p.get("cu_amada_pieza_export")) or (
        bool(sheet and sheet.get("cu_export_amada"))
        and bool(p.get("cu_especial_vertical"))
    ):
        from modules.dxf_export.amada_esp import export_amada_esp_piece

        ok_amada = export_amada_esp_piece(msp, p, draw_holes=draw_holes)
        return ok_amada or bool(_msp_snapshot(msp)[count_before:])

    used_source = False

    if bool(p.get("prefer_source_dxf")) and ruta and os.path.isfile(ruta):
        used_source = True
        _export_cu_largos_from_source(
            msp,
            doc,
            p,
            draw_holes=draw_holes,
            draw_marks=draw_marks,
            strict=strict,
            sheet=sheet,
        )
    else:
        if not _export_placed_geometry(
            msp, p, doc=doc, draw_holes=draw_holes, draw_marks=draw_marks, sheet=sheet
        ):
            if strict:
                _fail_export(label, "cobre: sin contorno colocado exportable en el nest")
            return False

    new_ents = _msp_snapshot(msp)[count_before:]
    outer_cuts = sum(
        1
        for e in new_ents
        if str(getattr(e.dxf, "layer", "") or "") == "CUT_OUTER"
    )
    # Madre Amada/sin_gap con 1 sola pieza: el DXF fuente no aporta
    # segmentos láser interiores. Insertar guillotina al fin de la pieza.
    if (
        used_source
        and outer_cuts == 0
        and bool(p.get("cu_largos_piece"))
        and not str(label).startswith("CU_CORTE__")
    ):
        from modules.nest_exporter import _export_cu_guillotina_fin_pieza

        log(
            f"    cobre[{label}]: sin CUT_OUTER desde fuente → "
            f"guillotina fin de pieza",
            level="WARN",
        )
        if _export_cu_guillotina_fin_pieza(msp, p, sheet=sheet):
            new_ents = _msp_snapshot(msp)[count_before:]
            outer_cuts = sum(
                1
                for e in new_ents
                if str(getattr(e.dxf, "layer", "") or "") == "CUT_OUTER"
            )

    if outer_cuts == 0 and not str(label).startswith("CU_CORTE__"):
        log(
            f"    cobre[{label}]: sin segmentos CUT_OUTER "
            f"(revisar DXF fuente o perfil del nest)",
            level="WARN",
        )
    return bool(new_ents)
