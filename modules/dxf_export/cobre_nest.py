"""
Exportación DXF de hojas de cobre (madre + RTZCU).

Canal aislado del nesteo acero: sin Plate/BORDE_RETAZO.
- Madre sin_gap: orientación vertical CyPTube + cortes segmentados.
- RTZCU / con_gap: horizontal, CUT_OUTER cerrado por pieza → STEP (misma lógica).
"""
from __future__ import annotations

import os
from typing import Any

import ezdxf

from modules.nest_exporter import (
    DxfExportValidationError,
    TOL_GEOM_MM,
    _bar_width_mm,
    _ensure_cierre_corte_madre_cu,
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


def _filtrar_placements_cobre(
    placements: list | None,
    sheet: dict | None = None,
    *,
    include_rtz_pieces: bool = False,
) -> list[dict]:
    """Quita artefactos del canal acero y piezas/cortes de zona RTZCU en madre."""
    sheet = sheet if isinstance(sheet, dict) else {}
    madre_sin_rtz = bool(sheet.get("modo_largos_cu")) and not sheet.get("cu_rtz_virtual")
    try:
        inicio_rtz = float(sheet.get("cu_rtz_inicio_mm") or 0.0)
    except (TypeError, ValueError):
        inicio_rtz = 0.0
    if madre_sin_rtz and sheet.get("cu_rtz_activo") and inicio_rtz <= 0.5:
        from modules.nesting_engine.cu_rtz_sin_gap import rtz_zona_inicio_mm

        inicio_rtz = float(rtz_zona_inicio_mm())

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
        if p.get("cu_zona_rtz") and not include_rtz_pieces:
            # En hoja virtual RTZCU las piezas llevan cu_zona_rtz: no filtrarlas.
            if not sheet.get("cu_rtz_virtual"):
                continue
        if (
            madre_sin_rtz
            and inicio_rtz > 0.5
            and not include_rtz_pieces
        ):
            # Defensa: geom/placement cuyo X empieza en zona RTZ.
            outer = p.get("outer") or []
            if outer:
                try:
                    minx = min(float(pt[0]) for pt in outer)
                    if minx >= inicio_rtz - 0.5:
                        continue
                except Exception:
                    pass
        out.append(p)
    return out


def _preparar_sheet_cobre(
    sheet: dict | None,
    *,
    force_horizontal: bool = False,
) -> dict[str, Any]:
    s = dict(sheet or {})
    s.setdefault("modo_largos_cu", True)
    try:
        from modules.nesting_engine.nest_runtime_prefs import is_cu_force_dxf_step_enabled

        if is_cu_force_dxf_step_enabled():
            s["cu_modo_separacion_barra"] = "con_gap"
            s["export_3d_format"] = "step"
            s.pop("cu_export_vertical", None)
            s.pop("cu_export_amada", None)
            s["cu_rtz_activo"] = False
            return s
    except Exception:
        pass
    if force_horizontal or s.get("cu_export_amada"):
        # AMADA: barra horizontal pieza completa (incluye RTZCU con gap).
        s["cu_modo_separacion_barra"] = "con_gap"
        s["export_3d_format"] = "dxf"
        s.pop("cu_export_vertical", None)
        s["cu_export_amada"] = True
        return s
    if s.get("cu_rtz_virtual"):
        # RTZCU: misma lógica STEP que con_gap (horizontal, contorno cerrado).
        s["cu_modo_separacion_barra"] = "con_gap"
        s["export_3d_format"] = "step"
        s.pop("cu_export_vertical", None)
    elif _sheet_is_sin_gap(s):
        # Solo madre sin_gap → CyPTube vertical.
        s["cu_export_vertical"] = True
        # Si hay RTZCU, acotar largo DXF al tramo madre.
        try:
            from modules.nesting_engine.cu_rtz_sin_gap import largo_export_madre_cu_mm

            largo_m = largo_export_madre_cu_mm(s)
            if largo_m is not None and largo_m > 0.5:
                s["length"] = float(largo_m)
                s["cu_rtz_inicio_mm"] = float(s.get("cu_rtz_inicio_mm") or largo_m)
                s["cu_rtz_activo"] = True
        except Exception:
            pass
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
    include_rtz_pieces: bool = False,
    force_horizontal: bool = False,
) -> str:
    """
    Exporta una hoja de cobre (madre o RTZCU) sin mezclar lógica de acero.
    """
    sheet_work = _preparar_sheet_cobre(sheet, force_horizontal=force_horizontal)
    placements_work = _filtrar_placements_cobre(
        placements,
        sheet_work,
        include_rtz_pieces=include_rtz_pieces or bool(sheet_work.get("cu_export_amada")),
    )
    sin_gap = _sheet_is_sin_gap(sheet_work) and not force_horizontal and not sheet_work.get(
        "cu_export_amada"
    )
    rtz_virtual = bool(sheet_work.get("cu_rtz_virtual"))
    canal_tag = str(title or "NESTEOS DE COBRE")

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

            # VERTICAL especial: sin MARK ni CUT_INNER (solo líneas de corte).
            piece_holes = draw_holes
            piece_marks = draw_marks
            if bool(p.get("cu_especial_vertical")) and not sheet_work.get("cu_export_amada"):
                piece_holes = False
                piece_marks = False

            mode, ok_channel = dxf_export_piece(
                msp,
                doc,
                p,
                draw_holes=piece_holes,
                draw_marks=piece_marks,
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

        # Madre sin_gap + RTZ: corte de cierre ANTES del check vacío
        # (caso 1 sola pieza Amada: sin CU_CORTE intermedias).
        if (
            sin_gap
            and not rtz_virtual
            and not sheet_work.get("cu_export_amada")
            and sheet_work.get("cu_rtz_activo")
        ):
            if _ensure_cierre_corte_madre_cu(msp, sheet_work):
                exported_pieces += 1
                log("  -> cierre madre RTZ: CUT_OUTER insertado")

        if strict and exported_pieces == 0:
            raise DxfExportValidationError(
                "La hoja de cobre no exportó ninguna pieza con geometría de corte válida."
            )

        # Amada VERTICAL / CyPTube: nunca dejar barrenos ni marcaje colados.
        purge_layers = {"Plate", "Plate_Text", "RTZ_LABEL"}
        if not draw_holes:
            purge_layers.add("CUT_INNER")
        if not draw_marks:
            purge_layers.add("MARK")
        _purge_entities_on_layers(msp, purge_layers)

        if sheet_work.get("cu_export_amada"):
            from modules.dxf_export.amada_fixture import draw_amada_fixture_provisional

            fid = str(sheet_work.get("cu_amada_fixtura_id") or "").strip() or None
            draw_amada_fixture_provisional(
                msp,
                _sheet_bar_l,
                _sheet_bar_w,
                fixture_id=fid,
                largo_pieza_in=float(_sheet_bar_l) / 25.4,
            )

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
