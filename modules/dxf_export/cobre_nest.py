"""
Exportación DXF de hojas de cobre (madre + RTZCU).

Canal aislado del nesteo acero: sin Plate/BORDE_RETAZO, orientación vertical
sin_gap (CyPTube) y seccionado según madre/RTZ definidos en el nesting.
"""
from __future__ import annotations

import os
from typing import Any

import ezdxf

from modules.nest_exporter import (
    DxfExportValidationError,
    TOL_GEOM_MM,
    _bar_width_mm,
    _export_cu_bar_inicio_marker,
    _export_cu_bar_inicio_marker_vertical,
    _fail_export,
    _msp_count,
    _msp_snapshot,
    _purge_capas_no_produccion_cobre,
    _purge_entities_on_layers,
    _save_dxf_atomic,
    _setup_layers,
    _sheet_is_sin_gap,
    _sheet_cu_exporta_cortes_segmentados,
    _sheet_omits_cut_cu,
    _validate_dxf_document,
    _validate_full_sheet,
    _validate_production_entities,
)
from modules.nesting_engine.dxf_export_log import (
    format_placement_spec,
    log,
    log_entities_added,
    log_export_done,
    log_export_error,
    log_export_start,
    resolve_export_mode,
)


def _filtrar_placements_cobre(placements: list | None) -> list[dict]:
    """Quita artefactos del canal acero (Plate retazo, tatuajes REF)."""
    out: list[dict] = []
    for p in placements or []:
        if not isinstance(p, dict):
            continue
        nom = str(p.get("part_name") or p.get("name") or "")
        if nom.startswith("BORDE_RETAZO_"):
            continue
        if nom.startswith("TATUAJE_") or nom.startswith("REF__"):
            continue
        if nom.startswith("RETAZO_GUILLOTINA__") and "RTZCU" in nom.upper():
            continue
        if nom.startswith("TATUAJE__") and "RTZCU" in nom.upper():
            continue
        out.append(p)
    return out


def _preparar_sheet_cobre(sheet: dict | None) -> dict[str, Any]:
    s = dict(sheet or {})
    s.setdefault("modo_largos_cu", True)
    if _sheet_is_sin_gap(s):
        # Incluye RTZCU: misma orientación vertical que la madre.
        s["cu_export_vertical"] = True
    return s


def export_cobre_hoja_to_dxf(
    out_path: str,
    sheet: dict,
    placements: list,
    *,
    title: str = "NESTEOS DE COBRE",
    draw_holes: bool = True,
    draw_marks: bool = True,
    strict: bool = True,
) -> str:
    """
    Exporta una hoja de cobre (madre o RTZCU) sin mezclar lógica de acero.
    """
    sheet_work = _preparar_sheet_cobre(sheet)
    placements_work = _filtrar_placements_cobre(placements)
    sin_gap = _sheet_is_sin_gap(sheet_work)
    rtz_virtual = bool(sheet_work.get("cu_rtz_virtual"))
    canal_tag = "NESTEOS DE COBRE"

    log_export_start(
        out_path,
        sheet_work,
        placements_work,
        canal=canal_tag,
        title=title,
        strict=strict,
    )
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)

    sheet_len = float(sheet_work.get("length", sheet_work.get("Length", 0)) or 0)
    sheet_w = float(sheet_work.get("width", sheet_work.get("Width", 0)) or 0)

    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 4
    _setup_layers(doc, solo_cobre=True, omit_plate=True, omit_plate_text=True)

    msp = doc.modelspace()
    _sheet_bar_l = sheet_len
    _sheet_bar_w = sheet_w

    if not sin_gap and _sheet_bar_w > TOL_GEOM_MM:
        _export_cu_bar_inicio_marker(msp, _sheet_bar_w)

    cache_blocks: dict = {}
    exported_pieces = 0

    try:
        from modules.dxf_export.dispatcher import export_piece as dxf_export_piece

        for i, p in enumerate(placements_work, start=1):
            if bool(p.get("cu_largos_piece")):
                if not float(p.get("cu_bar_w_mm") or 0):
                    p["cu_bar_w_mm"] = _sheet_bar_w
                if not float(p.get("cu_bar_l_mm") or 0):
                    p["cu_bar_l_mm"] = _sheet_bar_l

            part_name = str(p.get("part_name", p.get("name", f"PART_{i}")))
            export_mode = resolve_export_mode(p)
            count_before = _msp_count(msp)
            log(f"PIEZA COBRE {i}/{len(placements_work)}: {format_placement_spec(p, index=i)}")
            log(f"  modo={export_mode}")

            mode, ok_channel = dxf_export_piece(
                msp,
                doc,
                p,
                draw_holes=draw_holes,
                draw_marks=draw_marks,
                strict=strict,
                solo_cobre=True,
                cache_blocks=cache_blocks,
                sheet=sheet_work,
                all_piece_bounds=None,
            )

            if bool(p.get("plasma_export")):
                if not ok_channel and strict:
                    _fail_export(part_name, "plasma: sin contorno exportable desde el nest")
                new_entities = _msp_snapshot(msp)[count_before:]
                log_entities_added(
                    part_name, new_entities, mode=export_mode, ok=bool(new_entities)
                )
                if new_entities:
                    exported_pieces += 1
                continue

            new_entities = _msp_snapshot(msp)[count_before:]
            if strict and new_entities:
                _validate_production_entities(
                    new_entities,
                    p,
                    sheet_len=sheet_len,
                    sheet_w=sheet_w,
                    solo_cobre=True,
                    sheet=sheet_work,
                )
                exported_pieces += 1
            elif new_entities:
                exported_pieces += 1

            log_entities_added(
                part_name,
                new_entities,
                mode=export_mode,
                ok=bool(new_entities),
            )

        if strict and exported_pieces == 0:
            raise DxfExportValidationError(
                "La hoja de cobre no exportó ninguna pieza con geometría de corte válida."
            )

        _purge_entities_on_layers(msp, {"Plate", "Plate_Text", "RTZ_LABEL"})

        if sin_gap:
            _export_cu_bar_inicio_marker_vertical(
                msp, _bar_width_mm(sheet_work, _sheet_bar_w)
            )
            if not rtz_virtual:
                sheet_len, sheet_w = float(sheet_w), float(sheet_len)

        _purge_capas_no_produccion_cobre(doc)

        if not _sheet_cu_exporta_cortes_segmentados(sheet_work):
            from modules.cobre_step_audit import (
                validate_cut_outer_piece_count,
                write_cu_piece_meta,
            )

            expected_cu = validate_cut_outer_piece_count(
                msp,
                placements_work,
                sheet_label=str(title or out_path),
                strict=strict,
            )
            write_cu_piece_meta(
                msp,
                expected_pieces=expected_cu,
                sheet_label=os.path.basename(out_path),
            )

        if strict:
            _validate_full_sheet(
                msp,
                sheet_len=sheet_len,
                sheet_w=sheet_w,
                solo_cobre=True,
                skip_bounds=sin_gap,
                sheet=sheet_work,
            )
            _validate_dxf_document(doc)

        _save_dxf_atomic(doc, out_path)
        log_export_done(out_path, canal=canal_tag, exported_pieces=exported_pieces)
    except DxfExportValidationError as exc:
        log_export_error(out_path, exc, canal=canal_tag)
        if os.path.isfile(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        raise

    return out_path
