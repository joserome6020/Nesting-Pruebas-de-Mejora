"""Canal láser normal — DXF fuente 1:1 (fail-closed si la fuente está corrupta)."""
from __future__ import annotations

from modules.nest_exporter import (
    DxfExportValidationError,
    PRODUCTION_LAYERS,
    _entities_bbox,
    _export_block_at_placement,
    _export_placed_geometry,
    _export_source_dxf_at_placement,
    _inside_sheet_bounds,
    _piece_label,
    _poly_bounds,
)


def _destroy_entities(msp, entities) -> None:
    for ent in list(entities or []):
        try:
            msp.delete_entity(ent)
        except Exception:
            pass


def _cut_entities(entities) -> list:
    out = []
    for e in entities or []:
        layer = str(getattr(e.dxf, "layer", "") or "").upper()
        if layer in PRODUCTION_LAYERS or layer.startswith("CUT_"):
            out.append(e)
    return out


def _source_cut_fits_nest_and_sheet(
    cut_ents,
    p: dict,
    sheet: dict | None,
) -> tuple[bool, str]:
    """(ok, motivo). False si el CUT se sale del nest/placa → DXF fuente comprometido."""
    if not cut_ents:
        return True, ""
    bbox = _entities_bbox(cut_ents)
    if not bbox:
        return True, ""
    nest_b = _poly_bounds(p.get("outer") or p.get("outer_poly") or [])
    if nest_b:
        nw = max(float(nest_b[2]) - float(nest_b[0]), 1.0)
        nh = max(float(nest_b[3]) - float(nest_b[1]), 1.0)
        cw = float(bbox[2]) - float(bbox[0])
        ch = float(bbox[3]) - float(bbox[1])
        if cw > nw * 1.35 or ch > nh * 1.35:
            return (
                False,
                f"CUT {cw:.1f}×{ch:.1f} mm ≫ nest {nw:.1f}×{nh:.1f} mm",
            )
        pad = max(25.0, 0.15 * max(nw, nh))
        if (
            float(bbox[0]) < float(nest_b[0]) - pad
            or float(bbox[1]) < float(nest_b[1]) - pad
            or float(bbox[2]) > float(nest_b[2]) + pad
            or float(bbox[3]) > float(nest_b[3]) + pad
        ):
            return (
                False,
                f"CUT bbox {tuple(round(v, 1) for v in bbox)} fuera del nest",
            )
    if isinstance(sheet, dict):
        sl = float(sheet.get("length") or sheet.get("Length") or 0.0)
        sw = float(sheet.get("width") or sheet.get("Width") or 0.0)
        if sl > 0 and sw > 0 and not _inside_sheet_bounds(bbox, sl, sw):
            return (
                False,
                f"CUT fuera de la placa ({sl:.1f}×{sw:.1f} mm): "
                f"{tuple(round(v, 1) for v in bbox)}",
            )
    return True, ""


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
    label = _piece_label(p)

    if prefer_source and not compensated and ruta:
        before = list(msp)
        ok_src = _export_source_dxf_at_placement(
            msp, doc, p, draw_marks=draw_marks, strict=False
        )
        motivo = ""
        if ok_src:
            after = list(msp)
            before_ids = {id(e) for e in before}
            new_ents = [e for e in after if id(e) not in before_ids]
            cut = _cut_entities(new_ents)
            ok_fit, motivo = _source_cut_fits_nest_and_sheet(cut, p, sheet)
            if not ok_fit:
                _destroy_entities(msp, new_ents)
                ok_src = False
        if ok_src:
            return
        # Fail-closed: no sustituir con polígono nest (ocultaría DXF corrupto).
        detail = motivo or "DXF fuente no alinea con el nest / sin geometría 1:1 usable"
        raise DxfExportValidationError(
            f"{label}: DXF fuente corrupto o inconsistente ({detail}). "
            "Regenere el DXF en AutoDXF/Processed y vuelva a nestear; "
            "no exportar con geometría comprometida."
        )

    if not prefer_source and not compensated and ruta:
        _export_block_at_placement(msp, doc, cache_blocks, p)
    else:
        _export_placed_geometry(
            msp, p, doc=doc, draw_holes=draw_holes, draw_marks=draw_marks, sheet=sheet
        )
