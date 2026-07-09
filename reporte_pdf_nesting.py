import os
from collections import OrderedDict
from statistics import mean
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

MM_TO_IN = 1.0 / 25.4
SPECIAL_PREFIXES = ("REMANENTE", "RETAZO", "TATUAJE", "REF__", "CU_CORTE__", "RTZCU")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BRANDING_DIR = os.path.join(BASE_DIR, "assets", "branding")

LOGO_ICON1_PATH = os.path.join(BRANDING_DIR, "logo_icon1.png")
LOGO_MAIN_PATH = os.path.join(BRANDING_DIR, "logo_main.png")

def _to_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _fmt_in(mm_value):
    return f"{_to_float(mm_value) * MM_TO_IN:.2f}"


def _normalize_route(value):
    if not value:
        return ""
    try:
        return os.path.normcase(os.path.normpath(str(value)))
    except Exception:
        return str(value)


def _clean_piece_name(name):
    text = str(name or "-")
    for pref in ("REF__", "TATUAJE__", "RETAZO_GUILLOTINA__", "CU_CORTE__"):
        if text.startswith(pref):
            text = text.replace(pref, "", 1)
    return text.strip() or "-"


def _is_real_piece(piece):
    name = str(piece.get("nombre", ""))
    return not name.startswith(SPECIAL_PREFIXES)


def _piece_nombre(piece):
    return str((piece or {}).get("nombre", "") or "")


def _is_ref_overlay(piece):
    return _piece_nombre(piece).startswith("REF__")


def _is_cu_corte_overlay(piece):
    return _piece_nombre(piece).startswith("CU_CORTE__")


def _is_guillotina_overlay(piece):
    return _piece_nombre(piece).startswith("RETAZO_GUILLOTINA__")


def _is_tatuaje_overlay(piece):
    return _piece_nombre(piece).startswith("TATUAJE__")


def _is_cu_rtz_overlay_piece(piece):
    nom = _piece_nombre(piece)
    if nom.startswith("RETAZO_GUILLOTINA__") and "RTZCU" in nom.upper():
        return True
    if nom.startswith("TATUAJE__") and "RTZCU" in nom.upper():
        return True
    return False


def _is_rtz_overlay(piece):
    return (
        _is_ref_overlay(piece)
        or _is_guillotina_overlay(piece)
        or _is_tatuaje_overlay(piece)
        or _is_cu_rtz_overlay_piece(piece)
    )


def _mother_has_rtz_overlays(plate):
    if (plate or {}).get("es_retazo"):
        return False
    if (plate or {}).get("modo_largos_cu"):
        return bool((plate or {}).get("cu_rtz_activo"))
    return any(_is_rtz_overlay(p) for p in ((plate or {}).get("piezas") or []))


def _rtz_ids_en_placa(plate):
    if (plate or {}).get("modo_largos_cu") and (plate or {}).get("cu_rtz_activo"):
        rid = str((plate or {}).get("cu_rtz_id") or "").strip()
        return [rid] if rid else []
    ids = []
    for p in (plate or {}).get("piezas") or []:
        nom = _piece_nombre(p)
        if nom.startswith("TATUAJE__"):
            rid = nom.replace("TATUAJE__", "", 1).strip()
            if rid and rid not in ids:
                ids.append(rid)
        elif nom.startswith("RETAZO_GUILLOTINA__"):
            rid = nom.replace("RETAZO_GUILLOTINA__", "", 1).strip()
            if rid and rid not in ids:
                ids.append(rid)
    return ids


def _texto_retazo_rtz_placa(plate, rtz_ids):
    ids_txt = ", ".join(rtz_ids) if rtz_ids else "RTZCU asociado"
    if (plate or {}).get("modo_largos_cu"):
        largo_in = _to_float((plate or {}).get("cu_rtz_largo_in"), 0.0)
        extra = f" — tramo no cortable en láser ({largo_in:.2f}\")"
        return f"Retazo RTZCU de cobre: {ids_txt}{extra}"
    return (
        f"Retazo RTZ en uso: {ids_txt}  "
        "(zona punteada + piezas beige = referencia del mini-nest)"
    )


def _sync_rtz_overlays_para_pdf(resultados_nesting):
    try:
        from modules.nesting_engine.rtz_overlays import sincronizar_overlays_resultados

        sincronizar_overlays_resultados(resultados_nesting)
    except Exception:
        pass


def _bbox_from_points(points):
    if not points:
        return 0.0, 0.0, 0.0, 0.0
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _resolve_piece_meta(piece, meta_por_ruta=None, job_fallback="-"):
    ruta = _normalize_route(piece.get("ruta"))
    meta = (meta_por_ruta or {}).get(ruta, {}) if ruta else {}

    item = meta.get("item") or piece.get("item") or _clean_piece_name(piece.get("nombre"))
    job = meta.get("job") or piece.get("job") or job_fallback or "-"

    return {
        "job": str(job).strip() or "-",
        "item": str(item).strip() or "-",
    }


def _piece_dimensions_mm(piece):
    polys = piece.get("poligonos") or []
    if not polys:
        return 0.0, 0.0

    minx, miny, maxx, maxy = _bbox_from_points(polys[0])
    span_x = max(0.0, maxx - minx)
    span_y = max(0.0, maxy - miny)

    return max(span_x, span_y), min(span_x, span_y)


def _build_group_summary(pieces, meta_por_ruta=None, job_fallback="-"):
    piezas_reales = [p for p in pieces if _is_real_piece(p)]

    ordenadas = sorted(
        piezas_reales,
        key=lambda p: (
            _resolve_piece_meta(p, meta_por_ruta, job_fallback)["job"].upper(),
            _resolve_piece_meta(p, meta_por_ruta, job_fallback)["item"].upper(),
            _clean_piece_name(p.get("nombre", "")).upper(),
        ),
    )

    grupos = OrderedDict()
    ids_por_pieza = {}
    consecutivo = 1

    for pieza in ordenadas:
        meta = _resolve_piece_meta(pieza, meta_por_ruta, job_fallback)
        clave = (meta["job"].upper(), meta["item"].upper())

        largo_mm, ancho_mm = _piece_dimensions_mm(pieza)

        if clave not in grupos:
            grupos[clave] = {
                "displayId": f"({consecutivo})",
                "job": meta["job"],
                "item": meta["item"],
                "largos": [],
                "anchos": [],
                "cantidad": 0,
            }
            consecutivo += 1

        grupos[clave]["largos"].append(largo_mm)
        grupos[clave]["anchos"].append(ancho_mm)
        grupos[clave]["cantidad"] += 1
        ids_por_pieza[id(pieza)] = grupos[clave]["displayId"]

    rows = []
    for data in grupos.values():
        rows.append(
            {
                "displayId": data["displayId"],
                "job": data["job"],
                "item": data["item"],
                "largoIn": _fmt_in(mean(data["largos"]) if data["largos"] else 0),
                "anchoIn": _fmt_in(mean(data["anchos"]) if data["anchos"] else 0),
                "cantidad": data["cantidad"],
            }
        )

    return rows, ids_por_pieza



def _build_consolidated_piece_summary(plates, meta_por_ruta=None, job_fallback="-"):
    grupos = OrderedDict()

    for plate in plates or []:
        calibre = str((plate or {}).get("calibre") or "-").strip() or "-"
        piezas = (plate or {}).get("piezas") or []
        es_cu_madre = bool(plate.get("modo_largos_cu")) and not plate.get(
            "cu_rtz_virtual"
        ) and not plate.get("es_retazo")

        for pieza in piezas:
            if not _is_real_piece(pieza):
                continue
            if es_cu_madre and pieza.get("cu_zona_rtz"):
                continue

            meta = _resolve_piece_meta(pieza, meta_por_ruta, job_fallback)
            largo_mm, ancho_mm = _piece_dimensions_mm(pieza)

            largo_in = _fmt_in(largo_mm)
            ancho_in = _fmt_in(ancho_mm)

            clave = (
                calibre.upper(),
                meta["job"].upper(),
                meta["item"].upper(),
                largo_in,
                ancho_in,
            )

            if clave not in grupos:
                grupos[clave] = {
                    "calibre": calibre,
                    "job": meta["job"],
                    "item": meta["item"],
                    "largoIn": largo_in,
                    "anchoIn": ancho_in,
                    "cantidad": 0,
                }

            grupos[clave]["cantidad"] += 1

    rows = []
    for data in grupos.values():
        rows.append(
            {
                "calibre": data["calibre"],
                "job": data["job"],
                "item": data["item"],
                "largoIn": data["largoIn"],
                "anchoIn": data["anchoIn"],
                "cantidad": data["cantidad"],
            }
        )

    rows.sort(
        key=lambda r: (
            str(r.get("calibre", "")).upper(),
            str(r.get("job", "")).upper(),
            str(r.get("item", "")).upper(),
            str(r.get("largoIn", "")).upper(),
            str(r.get("anchoIn", "")).upper(),
        )
    )

    return rows


def _material_from_group_key(group_key):
    text = str(group_key or "").strip()
    if "_" in text:
        return text.split("_", 1)[1].replace("_", " ").strip() or text
    return text


def _plate_base_id(plate_id):
    text = str(plate_id or "").strip()
    if " P" in text:
        base, suf = text.rsplit(" P", 1)
        if suf.isdigit():
            return base.strip()
    return text

def _material_from_plate_id(plate_id):
    """
    Regla semántica correcta para el PDF:
    - Placa normal: conservar nombre visible completo, ej. 'PLC107 P1'
    - RTZ: mostrar solo el prefijo visible, ej. 'RTZ1'
    """
    text = str(plate_id or "").strip()
    if not text:
        return "-"

    if text.upper().startswith("RTZ"):
        return text.split("-", 1)[0].strip() or text

    return text

def _enumerate_plates(resultados_nesting):
    plates = []

    try:
        from modules.nesting_engine.efficiency_metrics import (
            calcular_eficiencias_grupo,
            contar_piezas_grupo,
            contar_piezas_hoja,
        )
    except ImportError:
        calcular_eficiencias_grupo = None
        contar_piezas_grupo = None
        contar_piezas_hoja = None

    try:
        from modules.nesting_engine.sheet_numbering import iterar_grupos_nesting_ordenados
    except ImportError:
        iterar_grupos_nesting_ordenados = None

    if iterar_grupos_nesting_ordenados is not None:
        grupos_iter = iterar_grupos_nesting_ordenados(resultados_nesting)
    else:
        try:
            from utils_nesting import clave_nesting_sort_key
        except ImportError:
            clave_nesting_sort_key = lambda k: str(k).upper()

        claves_ordenadas = sorted(
            (k for k in (resultados_nesting or {}) if isinstance((resultados_nesting or {}).get(k), dict)),
            key=clave_nesting_sort_key,
        )
        grupos_iter = (
            (k, (resultados_nesting or {}).get(k) or {})
            for k in claves_ordenadas
        )

    for grupo_calibre, datos_grupo in grupos_iter:
        if not isinstance(datos_grupo, dict) or "error" in datos_grupo:
            continue

        hojas = datos_grupo.get("hojas", []) or []
        grupo_ef = (
            calcular_eficiencias_grupo(hojas)
            if calcular_eficiencias_grupo is not None
            else {}
        )
        if grupo_ef:
            datos_grupo.update(grupo_ef)

        contador_placas = {}

        for idx, hoja in enumerate(hojas, start=1):
            display_previo = str(hoja.get("sheet_display_name") or "").strip()
            codigo_base = hoja.get("placa_id", f"P#{idx}")

            if display_previo:
                placa_id_final = display_previo
            elif "RTZ" in str(codigo_base).upper():
                placa_id_final = str(codigo_base)
            else:
                contador_placas[codigo_base] = contador_placas.get(codigo_base, 0) + 1
                placa_id_final = f"{codigo_base} P{contador_placas[codigo_base]}"

            pieces = hoja.get("piezas", [])
            if contar_piezas_hoja is not None:
                real_piece_count = contar_piezas_hoja(hoja)
            else:
                real_piece_count = len([p for p in pieces if _is_real_piece(p)])

            plates.append(
                {
                    "id": placa_id_final,
                    "base_id": _plate_base_id(placa_id_final),
                    "calibre": str(grupo_calibre),
                    "material": _material_from_group_key(grupo_calibre),
                    "placa_w": _to_float(hoja.get("placa_w"), 0.0),
                    "placa_h": _to_float(hoja.get("placa_h"), 0.0),
                    "piezas": pieces,
                    "real_piece_count": real_piece_count,
                    "es_retazo": bool(hoja.get("es_retazo")),
                    "modo_largos_cu": bool(hoja.get("modo_largos_cu")),
                    "cu_rtz_activo": bool(hoja.get("cu_rtz_activo")),
                    "cu_rtz_virtual": bool(hoja.get("cu_rtz_virtual")),
                    "cu_rtz_largo_in": _to_float(hoja.get("cu_rtz_largo_in"), 0.0),
                    "eficiencia_directa": _to_float(hoja.get("eficiencia_directa", hoja.get("eficiencia")), 0.0),
                    "eficiencia_real": _to_float(hoja.get("eficiencia_real", hoja.get("eficiencia")), 0.0),
                    "area_piezas_mm2": _to_float(hoja.get("area_piezas_mm2"), 0.0),
                    "area_piezas_con_rtz_mm2": _to_float(hoja.get("area_piezas_con_rtz_mm2"), 0.0),
                    "area_placa_mm2": _to_float(hoja.get("area_placa_mm2"), 0.0),
                    "eficiencia_tanque_directa": _to_float(grupo_ef.get("eficiencia_tanque_directa"), 0.0),
                    "eficiencia_tanque_real": _to_float(grupo_ef.get("eficiencia_tanque_real"), 0.0),
                    "ignorar_deduccion": bool(hoja.get("ignorar_deduccion")),
                    "sheet_code": str(hoja.get("sheet_code") or "").strip() or None,
                    "sheet_seq": hoja.get("sheet_seq"),
                }
            )

    return plates


def _fit_plate(draw_x, draw_y, draw_w, draw_h, plate_w, plate_h):
    pw = max(plate_w, 1.0)
    ph = max(plate_h, 1.0)

    margin = 6.0
    usable_w = max(draw_w - 2 * margin, 10)
    usable_h = max(draw_h - 2 * margin, 10)

    scale = min(usable_w / pw, usable_h / ph)

    rendered_w = pw * scale
    rendered_h = ph * scale

    offset_x = draw_x + (draw_w - rendered_w) / 2.0
    offset_y = draw_y + (draw_h - rendered_h) / 2.0

    return scale, offset_x, offset_y


def _draw_polygon(c, pts, scale, ox, oy, stroke_color, fill_color=None, stroke_width=0.8):
    if not pts or len(pts) < 2:
        return

    path = c.beginPath()
    x0 = ox + _to_float(pts[0][0]) * scale
    y0 = oy + _to_float(pts[0][1]) * scale
    path.moveTo(x0, y0)

    for x, y in pts[1:]:
        path.lineTo(ox + _to_float(x) * scale, oy + _to_float(y) * scale)

    if len(pts) >= 3:
        path.close()

    c.setLineWidth(stroke_width)
    c.setStrokeColor(stroke_color)

    if fill_color is not None:
        c.setFillColor(fill_color)
        c.drawPath(path, stroke=1, fill=1)
    else:
        c.drawPath(path, stroke=1, fill=0)


def _draw_dashed_polygon(c, pts, scale, ox, oy, stroke_color, stroke_width=1.4, dash=(5, 4)):
    if not pts or len(pts) < 2:
        return
    try:
        c.setDash(dash[0], dash[1])
    except Exception:
        pass
    _draw_polygon(c, pts, scale, ox, oy, stroke_color, fill_color=None, stroke_width=stroke_width)
    try:
        c.setDash()
    except Exception:
        pass


RTZ_REF_FILL = colors.HexColor("#BDB07E")
RTZ_REF_STROKE = colors.HexColor("#1F2937")
RTZ_GUILLOTINA_STROKE = colors.HexColor("#EF4444")
RTZ_TATUAJE_MARK = colors.HexColor("#FACC15")


def _draw_mark_paths(c, marks, scale, ox, oy, stroke_color, stroke_width=0.9):
    c.setStrokeColor(stroke_color)
    c.setLineWidth(stroke_width)
    for mark in marks or []:
        if not mark or len(mark) < 2:
            continue
        path = c.beginPath()
        path.moveTo(
            ox + _to_float(mark[0][0]) * scale,
            oy + _to_float(mark[0][1]) * scale,
        )
        for x, y in mark[1:]:
            path.lineTo(
                ox + _to_float(x) * scale,
                oy + _to_float(y) * scale,
            )
        c.drawPath(path, stroke=1, fill=0)


def _draw_rtz_overlays(c, pieces, scale, ox, oy):
    """Dibuja zona RTZ en placa madre: REF + guillotina + tatuaje (como en el visor)."""
    overlays = [p for p in (pieces or []) if _is_rtz_overlay(p)]
    if not overlays:
        return

    for pieza in overlays:
        if not _is_ref_overlay(pieza):
            continue
        polys = pieza.get("poligonos") or []
        if not polys:
            continue
        _draw_polygon(c, polys[0], scale, ox, oy, RTZ_REF_STROKE, RTZ_REF_FILL, 0.9)
        for hole in polys[1:]:
            _draw_polygon(c, hole, scale, ox, oy, RTZ_REF_STROKE, colors.white, 0.7)

    for pieza in overlays:
        if not _is_guillotina_overlay(pieza) and not _is_cu_rtz_overlay_piece(pieza):
            continue
        polys = pieza.get("poligonos") or []
        if polys:
            if _is_cu_rtz_overlay_piece(pieza):
                _draw_polygon(
                    c, polys[0], scale, ox, oy, RTZ_REF_STROKE, RTZ_REF_FILL, 0.35
                )
            _draw_dashed_polygon(
                c, polys[0], scale, ox, oy, RTZ_GUILLOTINA_STROKE, stroke_width=1.6
            )

    for pieza in overlays:
        if not _is_tatuaje_overlay(pieza):
            continue
        _draw_mark_paths(c, pieza.get("marcas") or [], scale, ox, oy, RTZ_TATUAJE_MARK, 1.1)


def _draw_real_pieces_on_plate(c, piezas_reales, scale, ox, oy, ids_map, outer_stroke, outer_fill, mark_color):
    for pieza in piezas_reales:
        polys = pieza.get("poligonos") or []
        if not polys:
            continue

        _draw_polygon(c, polys[0], scale, ox, oy, outer_stroke, outer_fill, 0.8)

        for hole in polys[1:]:
            _draw_polygon(c, hole, scale, ox, oy, outer_stroke, colors.white, 0.7)

        _draw_mark_paths(c, pieza.get("marcas") or [], scale, ox, oy, mark_color, 0.7)

        label = ids_map.get(id(pieza))
        if label:
            minx, miny, maxx, maxy = _bbox_from_points(polys[0])
            cx = ox + ((minx + maxx) / 2.0) * scale
            ly = oy + (miny + 8.0) * scale
            c.setFont("Helvetica-Bold", 9)
            c.setFillColor(colors.HexColor("#E11D48"))
            c.drawCentredString(cx, ly, label)


def _draw_plate_nesting_geometry(
    c, plate, scale, ox, oy, ids_map, outer_stroke, outer_fill, mark_color
):
    piezas_reales = [p for p in (plate.get("piezas") or []) if _is_real_piece(p)]
    _draw_real_pieces_on_plate(
        c, piezas_reales, scale, ox, oy, ids_map, outer_stroke, outer_fill, mark_color
    )
    if plate.get("modo_largos_cu"):
        _draw_cu_cortes(c, plate.get("piezas") or [], scale, ox, oy)
        if plate.get("cu_rtz_activo"):
            _draw_rtz_overlays(c, plate.get("piezas") or [], scale, ox, oy)
    elif not plate.get("es_retazo"):
        _draw_rtz_overlays(c, plate.get("piezas") or [], scale, ox, oy)


def _draw_cu_cortes(c, piezas, scale, ox, oy):
    """Líneas CUT_OUTER de largos CU (divisores + relieves), sin lógica RTZ."""
    for pieza in piezas or []:
        if not _is_cu_corte_overlay(pieza):
            continue
        polys = pieza.get("poligonos") or []
        if not polys or not polys[0]:
            continue
        _draw_dashed_polygon(
            c, polys[0], scale, ox, oy, RTZ_GUILLOTINA_STROKE, stroke_width=1.6
        )


def _draw_standard_legend(c, leg_y, outer_fill, outer_stroke, mark_color, body_text):
    c.setFillColor(outer_fill)
    c.setStrokeColor(outer_stroke)
    c.rect(18, leg_y - 6, 8, 8, fill=1, stroke=1)
    c.setFillColor(body_text)
    c.setFont("Helvetica", 7.3)
    c.drawString(30, leg_y - 3, "Contorno exterior")

    c.setFillColor(colors.white)
    c.setStrokeColor(outer_stroke)
    c.rect(95, leg_y - 6, 8, 8, fill=1, stroke=1)
    c.setFillColor(body_text)
    c.drawString(107, leg_y - 3, "Corte interno")

    c.setStrokeColor(mark_color)
    c.setLineWidth(1.2)
    c.line(168, leg_y - 2, 176, leg_y - 2)
    c.setFillColor(body_text)
    c.drawString(180, leg_y - 3, "Marcaje")


def _draw_rtz_legend(c, leg_y, body_text, *, cobre: bool = False):
    c.setFont("Helvetica", 7.3)
    ref_label = "Pieza referencia RTZCU" if cobre else "Pieza referencia RTZ"
    zona_label = "Zona retazo RTZCU" if cobre else "Zona retazo RTZ"
    etiq_label = "Etiqueta RTZCU" if cobre else "Etiqueta RTZ"
    c.setFillColor(RTZ_REF_FILL)
    c.setStrokeColor(RTZ_REF_STROKE)
    c.rect(18, leg_y - 6, 8, 8, fill=1, stroke=1)
    c.setFillColor(body_text)
    c.drawString(30, leg_y - 3, ref_label)

    c.setStrokeColor(RTZ_GUILLOTINA_STROKE)
    c.setLineWidth(1.4)
    try:
        c.setDash(4, 3)
    except Exception:
        pass
    c.line(145, leg_y - 2, 153, leg_y - 2)
    try:
        c.setDash()
    except Exception:
        pass
    c.setFillColor(body_text)
    c.drawString(157, leg_y - 3, zona_label)

    c.setStrokeColor(RTZ_TATUAJE_MARK)
    c.setLineWidth(1.2)
    c.line(248, leg_y - 2, 256, leg_y - 2)
    c.setFillColor(body_text)
    c.drawString(260, leg_y - 3, etiq_label)


def _resolve_plate_page_layout(plate, tiene_rtz):
    """
    Posiciones Y de leyenda y techo del área de dibujo (sin solapar la leyenda).
  En ReportLab Y crece hacia arriba: leyenda arriba, dibujo abajo.
    """
    legend_row_h = 14
    legend_box_h = 8
    gap_legend_draw = 14

    if tiene_rtz:
        leg_std = 614 if plate.get("ignorar_deduccion") else 628
        leg_rtz = leg_std - legend_row_h
        lowest_legend_y = leg_rtz - legend_box_h
    elif plate.get("ignorar_deduccion"):
        leg_std = 642
        leg_rtz = None
        lowest_legend_y = leg_std - legend_box_h
    else:
        leg_std = 650
        leg_rtz = None
        lowest_legend_y = leg_std - legend_box_h

    draw_top = lowest_legend_y - gap_legend_draw
    return leg_std, leg_rtz, draw_top


def _fit_text(text, max_width, font_name, font_size):
    text = str(text or "")
    if stringWidth(text, font_name, font_size) <= max_width:
        return text

    base = text
    while base and stringWidth(base + "...", font_name, font_size) > max_width:
        base = base[:-1]

    return (base + "...") if base else "..."


def _draw_cell_text(c, text, x, y, w, align="left", font_name="Helvetica", font_size=7.4):
    c.setFont(font_name, font_size)
    text = _fit_text(text, w - 8, font_name, font_size)

    if align == "center":
        c.drawCentredString(x + w / 2.0, y, text)
    else:
        c.drawString(x + 4, y, text)


def _split_rows(rows, chunk_size):
    if chunk_size <= 0:
        return [rows]
    return [rows[i:i + chunk_size] for i in range(0, len(rows), chunk_size)]

def _draw_image_fit(c, image_path, x, y, max_w, max_h, preserve_aspect=True, mask='auto'):
    if not image_path or not os.path.exists(image_path):
        return False

    try:
        img = ImageReader(image_path)
        iw, ih = img.getSize()
        iw = float(iw)
        ih = float(ih)

        if iw <= 0 or ih <= 0:
            return False

        if preserve_aspect:
            scale = min(max_w / iw, max_h / ih)
            draw_w = iw * scale
            draw_h = ih * scale
        else:
            draw_w = max_w
            draw_h = max_h

        draw_x = x + (max_w - draw_w) / 2.0
        draw_y = y + (max_h - draw_h) / 2.0

        c.drawImage(
            image_path,
            draw_x,
            draw_y,
            width=draw_w,
            height=draw_h,
            preserveAspectRatio=True,
            mask=mask,
        )
        return True
    except Exception:
        return False


def _draw_watermark_logo(c, width, height, image_path):
    """
    Marca de agua centrada y tenue.
    """
    if not image_path or not os.path.exists(image_path):
        return

    try:
        c.saveState()
        try:
            c.setFillAlpha(0.08)
            c.setStrokeAlpha(0.08)
        except Exception:
            pass

        _draw_image_fit(
            c,
            image_path=image_path,
            x=120,
            y=190,
            max_w=372,
            max_h=372,
            preserve_aspect=True,
            mask='auto',
        )
        c.restoreState()
    except Exception:
        try:
            c.restoreState()
        except Exception:
            pass


def _draw_cover_logo_main(c, width, image_path):
    """
    Logo principal en portada, en la zona derecha superior/media.
    """
    if not image_path or not os.path.exists(image_path):
        return

    _draw_image_fit(
        c,
        image_path=image_path,
        x=300,
        y=595,
        max_w=260,
        max_h=110,
        preserve_aspect=True,
        mask='auto',
    )


def _draw_header_logo_left(c, image_path):
    """
    Logo superior izquierdo para portada y hojas internas.
    """
    if not image_path or not os.path.exists(image_path):
        return

    _draw_image_fit(
        c,
        image_path=image_path,
        x=18,
        y=734,
        max_w=145,
        max_h=46,
        preserve_aspect=True,
        mask='auto',
    )

def _format_work_order_label(work_order_label=None, work_order_num=1):
    if work_order_label is not None and str(work_order_label).strip():
        return str(work_order_label).strip()
    return f"W.O.{int(work_order_num or 1)}"

def _build_cover_summary(plates, resultados_nesting=None):
    total_plates = sum(1 for p in plates if not p.get("es_retazo"))
    total_plates_deducibles = sum(
        1
        for p in plates
        if not p.get("es_retazo") and not p.get("ignorar_deduccion")
    )
    total_sheets = len(plates)
    total_real_pieces = 0
    if resultados_nesting:
        try:
            from modules.nesting_engine.efficiency_metrics import contar_piezas_grupo

            for datos in (resultados_nesting or {}).values():
                if isinstance(datos, dict) and "error" not in datos:
                    total_real_pieces += contar_piezas_grupo(datos)
        except ImportError:
            total_real_pieces = sum(p.get("real_piece_count", 0) for p in plates)
    else:
        total_real_pieces = sum(p.get("real_piece_count", 0) for p in plates)

    by_calibre = OrderedDict()
    by_plate = OrderedDict()

    try:
        from utils_nesting import clave_nesting_sort_key
    except ImportError:
        clave_nesting_sort_key = lambda k: str(k).upper()

    plates_sorted = sorted(
        plates,
        key=lambda p: clave_nesting_sort_key(str(p.get("calibre", ""))),
    )

    for plate in plates_sorted:
        calibre = str(plate.get("calibre", "-"))
        material = str(plate.get("material", "-"))
        base_id = str(plate.get("base_id", "-"))
        dims = f"{_fmt_in(plate.get('placa_w', 0))} x {_fmt_in(plate.get('placa_h', 0))} in"
        es_mother = not plate.get("es_retazo")

        if calibre not in by_calibre:
            by_calibre[calibre] = {
                "calibre": calibre,
                "material": material,
                "cantidad": 0,
                "nomenclaturas": OrderedDict(),
                "area_solo_madre": 0.0,
                "area_total_fisica": 0.0,
                "area_madre": 0.0,
            }

        if es_mother:
            by_calibre[calibre]["cantidad"] += 1
        by_calibre[calibre]["nomenclaturas"][base_id] = True
        if es_mother:
            area_madre = _to_float(plate.get("area_placa_mm2"), 0.0)
            if area_madre <= 0.0:
                area_madre = _to_float(plate.get("placa_w")) * _to_float(plate.get("placa_h"))
            area_m = _to_float(plate.get("area_piezas_mm2"), 0.0)
            area_con_rtz = _to_float(plate.get("area_piezas_con_rtz_mm2"), 0.0)
            if area_con_rtz <= 0.0:
                area_con_rtz = area_m
            by_calibre[calibre]["area_madre"] += area_madre
            by_calibre[calibre]["area_solo_madre"] += area_m
            by_calibre[calibre]["area_total_fisica"] += area_con_rtz

        key = (base_id, calibre, dims)
        if key not in by_plate:
            by_plate[key] = {
                "nomenclatura": base_id,
                "calibre": calibre,
                "dimensiones": dims,
                "cantidad": 0,
            }

        by_plate[key]["cantidad"] += 1

    calibre_rows = []
    for calibre_key in sorted(by_calibre.keys(), key=clave_nesting_sort_key):
        row = by_calibre[calibre_key]
        area_madre = row.get("area_madre", 0.0) or 0.0
        area_solo_madre = row.get("area_solo_madre", 0.0) or 0.0
        area_total_fisica = row.get("area_total_fisica", 0.0) or 0.0
        efi_tanque_directa = (area_solo_madre / area_madre * 100.0) if area_madre > 0.0 else 0.0
        efi_tanque_real = (area_total_fisica / area_madre * 100.0) if area_madre > 0.0 else 0.0
        efi_tanque_real = min(100.0, efi_tanque_real)
        calibre_rows.append(
            [
                row["calibre"],
                row["material"],
                str(row["cantidad"]),
                f"{efi_tanque_directa:.1f}%",
                f"{efi_tanque_real:.1f}%",
                ", ".join(row["nomenclaturas"].keys()),
            ]
        )

    plate_rows = []
    for row in by_plate.values():
        plate_rows.append(
            [
                row["nomenclatura"],
                row["calibre"],
                row["dimensiones"],
                str(row["cantidad"]),
            ]
        )

    return {
        "total_plates": total_plates,
        "total_plates_deducibles": total_plates_deducibles,
        "total_sheets": total_sheets,
        "total_real_pieces": total_real_pieces,
        "calibre_rows": calibre_rows,
        "plate_rows": plate_rows,
    }


def _draw_table(c, x, y_top, col_widths, headers, rows,
                header_fill, line_color, title_color, body_text,
                row_h=18, header_h=18, font_size=7.4):
    table_w = sum(col_widths)

    c.setStrokeColor(line_color)
    c.setLineWidth(1)

    # Header
    y_header = y_top - header_h
    c.setFillColor(header_fill)
    c.rect(x, y_header, table_w, header_h, fill=1, stroke=1)

    current_x = x
    c.setFont("Helvetica-Bold", 7.2)
    c.setFillColor(title_color)
    for w_col, head in zip(col_widths, headers):
        c.rect(current_x, y_header, w_col, header_h, fill=0, stroke=1)
        c.drawCentredString(current_x + w_col / 2.0, y_header + 5.2, str(head))
        current_x += w_col

    # Body
    y = y_header
    for row in rows:
        y -= row_h
        current_x = x

        for idx, (w_col, value) in enumerate(zip(col_widths, row)):
            c.rect(current_x, y, w_col, row_h, fill=0, stroke=1)
            c.setFillColor(body_text)

            align = "left"
            if idx == 0:
                align = "center"
            if idx >= len(row) - 1:
                align = "center"

            _draw_cell_text(
                c,
                str(value),
                current_x,
                y + 5.0,
                w_col,
                align=align,
                font_name="Helvetica",
                font_size=font_size,
            )
            current_x += w_col

    return y


def _draw_cover_pages(c, width, height, plates, nombre_orden, work_order_label,
                      title_color, subtitle_color, line_color, table_head, table_line, body_text,
                      resultados_nesting=None):

    summary = _build_cover_summary(plates, resultados_nesting=resultados_nesting)
    calibre_rows = summary["calibre_rows"] or [["-", "-", "0", "-", "-", "-"]]
    plate_rows = summary["plate_rows"] or [["-", "-", "-", "0"]]

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    cover_code = f"{work_order_label}-PORTADA"

    first_page_plate_chunks = _split_rows(plate_rows, 16)
    if not first_page_plate_chunks:
        first_page_plate_chunks = [[]]

    first_chunk = first_page_plate_chunks[0]
    remaining_rows = plate_rows[len(first_chunk):]
    continuation_chunks = _split_rows(remaining_rows, 26)

    # -------- First cover page --------
    c.setFont("Helvetica-Bold", 18)
    _draw_watermark_logo(c, width, height, LOGO_ICON1_PATH)
    c.setFillColor(title_color)
    c.drawCentredString(width / 2, 760, "High Performance Nesting Report")

    c.setFont("Helvetica", 9.5)
    c.setFillColor(subtitle_color)
    c.drawCentredString(
        width / 2,
        742,
        f"Orden: {nombre_orden or '-'} | Work Order: {work_order_label}"
    )

    c.setStrokeColor(line_color)
    c.setLineWidth(1)
    c.line(18, 730, width - 18, 730)
    # Logo superior izquierdo en portada
    _draw_header_logo_left(c, LOGO_MAIN_PATH)

    c.setFillColor(title_color)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(28, 700, "Resumen general")

    c.setFont("Helvetica", 9)
    c.setFillColor(body_text)
    c.drawString(28, 682, f"Fecha de generación: {generated_at}")
    c.drawString(28, 666, f"Total de hojas del reporte: {summary['total_sheets']}")
    c.drawString(28, 650, f"Total de placas utilizadas: {summary['total_plates']}")
    if summary.get("total_plates_deducibles", summary["total_plates"]) != summary["total_plates"]:
        c.drawString(
            28,
            634,
            f"Placas que deducen inventario: {summary['total_plates_deducibles']}",
        )
        c.drawString(28, 618, f"Total de piezas reales colocadas: {summary['total_real_pieces']}")
        y_resumen_calibre = 590
    else:
        c.drawString(28, 634, f"Total de piezas reales colocadas: {summary['total_real_pieces']}")
        y_resumen_calibre = 606

    c.setFillColor(title_color)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(28, y_resumen_calibre, "Resumen por calibre")

    y_after_calibres = _draw_table(
        c=c,
        x=28,
        y_top=y_resumen_calibre - 14,
        col_widths=[88, 88, 52, 58, 58, 192],
        headers=["Calibre", "Material", "Placas", "Efi. Dir.", "Efi. Real", "Nomenclaturas"],
        rows=calibre_rows,
        header_fill=table_head,
        line_color=table_line,
        title_color=title_color,
        body_text=body_text,
        row_h=18,
        header_h=18,
        font_size=7.2,
    )

    c.setFillColor(title_color)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(28, y_after_calibres - 24, "Detalle de placas utilizadas")

    _draw_table(
        c=c,
        x=28,
        y_top=y_after_calibres - 34,
        col_widths=[150, 110, 190, 70],
        headers=["Nomenclatura", "Calibre", "Dimensiones", "Cantidad"],
        rows=first_chunk or [["-", "-", "-", "0"]],
        header_fill=table_head,
        line_color=table_line,
        title_color=title_color,
        body_text=body_text,
        row_h=18,
        header_h=18,
        font_size=7.4,
    )

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(subtitle_color)
    c.drawRightString(width - 24, 24, cover_code)
    c.showPage()

    # -------- Continuation pages if needed --------
    for cont_idx, chunk in enumerate(continuation_chunks, start=2):
        c.setFont("Helvetica-Bold", 16)
        _draw_watermark_logo(c, width, height, LOGO_ICON1_PATH)
        c.setFillColor(title_color)
        c.drawCentredString(width / 2, 760, "High Performance Nesting Report")

        c.setFont("Helvetica", 9.5)
        c.setFillColor(subtitle_color)
        c.drawCentredString(
            width / 2,
            742,
            f"Portada - Continuación | Orden: {nombre_orden or '-'} | Work Order: {work_order_label}"
        )

        c.setStrokeColor(line_color)
        c.setLineWidth(1)
        c.line(18, 730, width - 18, 730)
        _draw_header_logo_left(c, LOGO_MAIN_PATH)

        c.setFillColor(title_color)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(28, 700, "Detalle de placas utilizadas")

        _draw_table(
            c=c,
            x=28,
            y_top=684,
            col_widths=[150, 110, 190, 70],
            headers=["Nomenclatura", "Calibre", "Dimensiones", "Cantidad"],
            rows=chunk,
            header_fill=table_head,
            line_color=table_line,
            title_color=title_color,
            body_text=body_text,
            row_h=18,
            header_h=18,
            font_size=7.4,
        )

        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(subtitle_color)
        c.drawRightString(width - 24, 24, f"{cover_code}-{cont_idx}")
        c.showPage()



def _draw_consolidated_piece_pages(
    c,
    width,
    height,
    plates,
    nombre_orden,
    work_order_label,
    title_color,
    subtitle_color,
    line_color,
    table_head,
    table_line,
    body_text,
    meta_por_ruta=None,
    job_fallback="-",
):
    rows_data = _build_consolidated_piece_summary(
        plates,
        meta_por_ruta=meta_por_ruta,
        job_fallback=job_fallback,
    )

    rows = [
        [
            row["calibre"],
            row["job"],
            row["item"],
            row["largoIn"],
            row["anchoIn"],
            str(row["cantidad"]),
        ]
        for row in rows_data
    ] or [["-", "-", "-", "-", "-", "0"]]

    chunks = _split_rows(rows, 28)
    resumen_code = f"{work_order_label}-RESUMEN-PIEZAS"

    for page_idx, chunk in enumerate(chunks, start=1):
        c.setFont("Helvetica-Bold", 16)
        _draw_watermark_logo(c, width, height, LOGO_ICON1_PATH)
        c.setFillColor(title_color)
        c.drawCentredString(width / 2, 760, "Resumen Consolidado de Piezas")

        c.setFont("Helvetica", 9.5)
        c.setFillColor(subtitle_color)
        c.drawCentredString(
            width / 2,
            742,
            f"Orden: {nombre_orden or '-'} | Work Order: {work_order_label}",
        )

        c.setStrokeColor(line_color)
        c.setLineWidth(1)
        c.line(18, 730, width - 18, 730)
        _draw_header_logo_left(c, LOGO_MAIN_PATH)

        c.setFillColor(title_color)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(28, 700, "Tabla global de piezas del nesteo")

        c.setFont("Helvetica", 9)
        c.setFillColor(body_text)
        c.drawString(
            28,
            684,
            "Conteo total consolidado por calibre, job, item y dimensiones.",
        )

        _draw_table(
            c=c,
            x=28,
            y_top=664,
            col_widths=[95, 110, 175, 65, 65, 46],
            headers=["Calibre", "JOB", "ITEM", "L (in)", "W (in)", "Cant."],
            rows=chunk,
            header_fill=table_head,
            line_color=table_line,
            title_color=title_color,
            body_text=body_text,
            row_h=18,
            header_h=18,
            font_size=7.2,
        )

        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(subtitle_color)
        footer_code = resumen_code if page_idx == 1 else f"{resumen_code}-{page_idx}"
        c.drawRightString(width - 24, 24, footer_code)
        c.showPage()

def _draw_sheet_table_continuation_pages(
    c,
    width,
    height,
    plate,
    nombre_orden,
    job_fallback,
    sheet_code,
    rows_overflow,
    title_color,
    subtitle_color,
    line_color,
    table_head,
    table_line,
    body_text,
):
    if not rows_overflow:
        return

    headers = ["ID", "JOB", "ITEM", "L (in)", "W (in)", "Cant."]
    col_widths = [48, 120, 220, 55, 55, 50]

    chunks = _split_rows(rows_overflow, 28)

    for cont_idx, chunk in enumerate(chunks, start=1):
        _draw_watermark_logo(c, width, height, LOGO_ICON1_PATH)
        _draw_header_logo_left(c, LOGO_MAIN_PATH)

        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(title_color)
        c.drawCentredString(width / 2, 760, "Reporte de Producción NestingPro")

        c.setFont("Helvetica", 8.5)
        c.setFillColor(subtitle_color)
        subt = (
            f"Orden: {nombre_orden or job_fallback or 'N/A'} | "
            f"Placa: {plate['id']} | Hoja: {sheet_code} | "
            f"Tabla continuación {cont_idx}"
        )
        c.drawCentredString(width / 2, 744, subt)

        c.setStrokeColor(line_color)
        c.setLineWidth(1)
        c.line(18, 732, width - 18, 732)

        c.setFillColor(title_color)
        c.setFont("Helvetica-Bold", 10.5)
        c.drawString(18, 698, f"Material: {plate['material']} | Calibre: {plate['calibre']}")

        c.setFont("Helvetica", 8.3)
        c.setFillColor(subtitle_color)
        c.drawString(
            18,
            684,
            f"Detalle de componentes agrupados de la hoja {sheet_code}",
        )

        table_rows = [
            [
                row["displayId"],
                row["job"],
                row["item"],
                str(row["largoIn"]),
                str(row["anchoIn"]),
                str(row["cantidad"]),
            ]
            for row in chunk
        ]

        _draw_table(
            c=c,
            x=32,
            y_top=664,
            col_widths=col_widths,
            headers=headers,
            rows=table_rows,
            header_fill=table_head,
            line_color=table_line,
            title_color=title_color,
            body_text=body_text,
            row_h=18,
            header_h=18,
            font_size=7.4,
        )

        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(subtitle_color)
        c.drawRightString(width - 24, 24, f"{sheet_code}-DET-{cont_idx}")
        c.showPage()

def _resolver_order_label_pdf(nombre_orden="", work_order_label=None, work_order_num=1):
    orden = str(nombre_orden or "").strip()
    if orden.upper().startswith("SWO"):
        return orden
    return _format_work_order_label(work_order_label, work_order_num)


def exportar_pdf_nesting(
    resultados_nesting,
    ruta_pdf,
    nombre_orden="",
    meta_por_ruta=None,
    job_fallback="-",
    work_order_num=1,
    work_order_label=None,
):
    _sync_rtz_overlays_para_pdf(resultados_nesting)

    wo_label = _resolver_order_label_pdf(nombre_orden, work_order_label, work_order_num)

    try:
        from modules.nesting_engine.sheet_numbering import (
            asignar_numeracion_global_hojas,
            numeracion_hojas_es_consistente,
        )

        if not numeracion_hojas_es_consistente(resultados_nesting, wo_label):
            asignar_numeracion_global_hojas(resultados_nesting, wo_label, sobrescribir=True)
        from modules.nesting_engine.cu_rtz_sin_gap import asignar_rtz_cu_sin_gap_ids

        asignar_rtz_cu_sin_gap_ids(resultados_nesting)
    except ImportError:
        pass

    plates = _enumerate_plates(resultados_nesting)
    if not plates:
        raise ValueError("No hay placas para exportar a PDF.")

    c = canvas.Canvas(ruta_pdf, pagesize=LETTER)
    width, height = LETTER

    title_color = colors.HexColor("#0F172A")
    subtitle_color = colors.HexColor("#64748B")
    line_color = colors.HexColor("#CBD5E1")
    outer_fill = colors.HexColor("#BFDBFE")
    outer_stroke = colors.HexColor("#1E3A8A")
    mark_color = colors.HexColor("#DC2626")
    table_head = colors.HexColor("#E2E8F0")
    table_line = colors.HexColor("#CBD5E1")
    body_text = colors.HexColor("#334155")

    wo_label = _resolver_order_label_pdf(nombre_orden, work_order_label, work_order_num)

    # ---------------- PORTADA ----------------
    _draw_cover_pages(
        c=c,
        width=width,
        height=height,
        plates=plates,
        nombre_orden=nombre_orden,
        work_order_label=wo_label,
        title_color=title_color,
        subtitle_color=subtitle_color,
        line_color=line_color,
        table_head=table_head,
        table_line=table_line,
        body_text=body_text,
        resultados_nesting=resultados_nesting,
    )

    _draw_consolidated_piece_pages(
        c=c,
        width=width,
        height=height,
        plates=plates,
        nombre_orden=nombre_orden,
        work_order_label=wo_label,
        title_color=title_color,
        subtitle_color=subtitle_color,
        line_color=line_color,
        table_head=table_head,
        table_line=table_line,
        body_text=body_text,
        meta_por_ruta=meta_por_ruta,
        job_fallback=job_fallback,
    )

    # ---------------- HOJAS DE PLACA ----------------
    for page_idx, plate in enumerate(plates, start=1):
        if plate.get("cu_rtz_virtual"):
            continue
        piezas_reales = [p for p in plate["piezas"] if _is_real_piece(p)]
        rows, ids_map = _build_group_summary(piezas_reales, meta_por_ruta, job_fallback)
        sheet_code = plate.get("sheet_code") or f"{wo_label}-H{page_idx}"
        # Marca de agua
        _draw_watermark_logo(c, width, height, LOGO_ICON1_PATH)
        _draw_header_logo_left(c, LOGO_MAIN_PATH)

        # ---------------- HEADER ----------------
        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(title_color)
        c.drawCentredString(width / 2, 760, "Reporte de Producción NestingPro")

        c.setFont("Helvetica", 8.5)
        c.setFillColor(subtitle_color)
        subt = f"Orden: {nombre_orden or job_fallback or 'N/A'} | Placa: {plate['id']} | Hoja: {sheet_code}"
        c.drawCentredString(width / 2, 744, subt)

        c.setStrokeColor(line_color)
        c.setLineWidth(1)
        c.line(18, 732, width - 18, 732)

        # ---------------- BLOQUE META ----------------
        c.setFillColor(title_color)
        c.setFont("Helvetica-Bold", 10.5)
        c.drawString(18, 698, f"Material: {plate['material']} | Calibre: {plate['calibre']}")

        c.setFont("Helvetica", 8.3)
        c.setFillColor(subtitle_color)
        c.drawString(
            18,
            684,
            f"Dimensiones de placa: {_fmt_in(plate['placa_w'])} in x {_fmt_in(plate['placa_h'])} in | Piezas en placa: {len(piezas_reales)}",
        )
        efi_dir = _to_float(plate.get("eficiencia_directa"), 0.0)
        efi_real = _to_float(plate.get("eficiencia_real"), efi_dir)
        efi_rtz_label = "RTZCU" if plate.get("modo_largos_cu") else "RTZ"
        c.drawString(
            18,
            670,
            (
                f"Eficiencia placa: Directa {efi_dir:.1f}% (piezas en madre) | "
                f"Real {efi_real:.1f}% (madre + {efi_rtz_label})"
            ),
        )
        if plate.get("ignorar_deduccion"):
            c.setFillColor(colors.HexColor("#B45309"))
            c.drawString(18, 656, "Placa excluida de deducción de inventario (uso de sobrante en piso).")

        tiene_rtz = _mother_has_rtz_overlays(plate)
        rtz_ids = _rtz_ids_en_placa(plate) if tiene_rtz else []
        meta_y = 656 if plate.get("ignorar_deduccion") else 670
        if tiene_rtz:
            c.setFillColor(colors.HexColor("#B45309"))
            c.setFont("Helvetica-Bold", 8.0)
            ids_txt = ", ".join(rtz_ids) if rtz_ids else "RTZ asociado"
            c.drawString(
                18,
                meta_y - 14,
                _texto_retazo_rtz_placa(plate, rtz_ids),
            )

        # ---------------- LEYENDA ----------------
        leg_y, leg_y_rtz, draw_top = _resolve_plate_page_layout(plate, tiene_rtz)
        _draw_standard_legend(c, leg_y, outer_fill, outer_stroke, mark_color, body_text)
        if leg_y_rtz is not None:
            _draw_rtz_legend(c, leg_y_rtz, body_text, cobre=bool(plate.get("modo_largos_cu")))

        # ---------------- TABLA ----------------
        rows_to_draw = rows or [
            {
                "displayId": "-",
                "job": "No hay piezas para listar.",
                "item": "",
                "largoIn": "",
                "anchoIn": "",
                "cantidad": "",
            }
        ]
        col_widths = [48, 120, 220, 55, 55, 50]
        table_w = sum(col_widths)
        table_x = (width - table_w) / 2.0
        header_h = 18
        row_h = 18

        # Zonas fijas de la página (draw_top ya reserva espacio bajo la leyenda)
        table_bottom = 72       # margen inferior seguro para tabla
        gap_between = 14        # separación entre dibujo y tabla
        min_draw_h = 120        # altura mínima legible para el nesteo

        # Cuántas filas máximas caben en la primera página
        max_table_h_first = max(0, draw_top - table_bottom - gap_between - min_draw_h)
        max_rows_first = max(1, int((max_table_h_first - header_h) // row_h))

        first_page_rows = rows_to_draw[:max_rows_first]
        overflow_rows = rows_to_draw[max_rows_first:]

        total_h = header_h + row_h * len(first_page_rows)
        table_y = table_bottom

        # La zona de dibujo ahora se acomoda arriba de la tabla
        draw_x = 18
        draw_y = table_y + total_h + gap_between
        draw_w = width - 36
        available_h = max(0.0, draw_top - draw_y)
        draw_h = available_h

        # ---------------- ZONA DE DIBUJO ----------------
        c.setFillColor(colors.white)
        c.setStrokeColor(table_line)
        c.setLineWidth(1)
        c.rect(draw_x, draw_y, draw_w, draw_h, fill=1, stroke=1)

        c.saveState()
        clip = c.beginPath()
        clip.rect(draw_x + 0.5, draw_y + 0.5, max(0, draw_w - 1), max(0, draw_h - 1))
        c.clipPath(clip, stroke=0, fill=0)

        scale, ox, oy = _fit_plate(
            draw_x,
            draw_y,
            draw_w,
            draw_h,
            plate["placa_w"],
            plate["placa_h"],
        )

        c.setStrokeColor(colors.HexColor("#94A3B8"))
        c.setLineWidth(0.8)
        c.rect(
            ox,
            oy,
            max(plate["placa_w"], 1) * scale,
            max(plate["placa_h"], 1) * scale,
            fill=0,
            stroke=1,
        )

        _draw_plate_nesting_geometry(
            c, plate, scale, ox, oy, ids_map, outer_stroke, outer_fill, mark_color
        )
        c.restoreState()

        # ---------------- TABLA ----------------
        c.setStrokeColor(table_line)
        c.setLineWidth(1)
        c.setFillColor(colors.white)
        c.rect(table_x, table_y, table_w, total_h, fill=1, stroke=1)

        c.setFillColor(table_head)
        c.rect(
            table_x,
            table_y + row_h * len(first_page_rows),
            table_w,
            header_h,
            fill=1,
            stroke=0
        )

        headers = ["ID", "JOB", "ITEM", "L (in)", "W (in)", "Cant."]

        x = table_x
        c.setFont("Helvetica-Bold", 7.2)
        c.setFillColor(title_color)

        for w_col, head in zip(col_widths, headers):
            c.rect(
                x,
                table_y + row_h * len(first_page_rows),
                w_col,
                header_h,
                fill=0,
                stroke=1
            )
            c.drawCentredString(
                x + w_col / 2.0,
                table_y + row_h * len(first_page_rows) + 5.3,
                head
            )
            x += w_col

        for ridx, row in enumerate(first_page_rows):
            y = table_y + row_h * (len(first_page_rows) - 1 - ridx)
            x = table_x

            values = [
                row["displayId"],
                row["job"],
                row["item"],
                str(row["largoIn"]),
                str(row["anchoIn"]),
                str(row["cantidad"]),
            ]
            aligns = ["center", "left", "left", "center", "center", "center"]

            for w_col, value, align in zip(col_widths, values, aligns):
                c.rect(x, y, w_col, row_h, fill=0, stroke=1)
                c.setFillColor(body_text)
                _draw_cell_text(
                    c,
                    value,
                    x,
                    y + 5.1,
                    w_col,
                    align=align,
                    font_name="Helvetica",
                    font_size=7.4,
                )
                x += w_col

        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(subtitle_color)
        c.drawRightString(width - 24, 24, sheet_code)
        c.showPage()

        # Páginas extra solo si sobran filas
        _draw_sheet_table_continuation_pages(
            c=c,
            width=width,
            height=height,
            plate=plate,
            nombre_orden=nombre_orden,
            job_fallback=job_fallback,
            sheet_code=sheet_code,
            rows_overflow=overflow_rows,
            title_color=title_color,
            subtitle_color=subtitle_color,
            line_color=line_color,
            table_head=table_head,
            table_line=table_line,
            body_text=body_text,
        )
    
    c.save()

def _pt_tuple_dict(pt):
    if isinstance(pt, dict):
        return (_to_float(pt.get("x")), _to_float(pt.get("y")))
    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
        return (_to_float(pt[0]), _to_float(pt[1]))
    return None


def _paths_from_unknown(data):
    if not data:
        return []

    if isinstance(data, dict):
        data = data.get("puntos") or data.get("points") or data.get("path") or []

    if isinstance(data, list):
        if data and all(isinstance(p, dict) for p in data):
            path = [_pt_tuple_dict(p) for p in data]
            path = [p for p in path if p is not None]
            return [path] if len(path) >= 2 else []

        paths = []
        for entry in data:
            if isinstance(entry, dict):
                pts = entry.get("puntos") or entry.get("points") or entry.get("path") or []
                path = [_pt_tuple_dict(p) for p in pts]
                path = [p for p in path if p is not None]
                if len(path) >= 2:
                    paths.append(path)
            elif isinstance(entry, list):
                path = [_pt_tuple_dict(p) for p in entry]
                path = [p for p in path if p is not None]
                if len(path) >= 2:
                    paths.append(path)
        return paths

    return []


def _radiografia_to_pdf_plates(detalle_radiografia):
    plates = []

    for idx, placa in enumerate((detalle_radiografia or {}).get("placas") or [], start=1):
        piezas_convertidas = []

        for pieza in (placa.get("piezas") or []):
            geom = pieza.get("geometria") or {}

            exterior = [_pt_tuple_dict(p) for p in (geom.get("puntos") or [])]
            exterior = [p for p in exterior if p is not None]

            if len(exterior) < 2:
                continue

            interiores = _paths_from_unknown(geom.get("interiores") or geom.get("interior"))
            marcas = _paths_from_unknown(
                geom.get("marcaje") or geom.get("marcajes") or geom.get("lineasMarcaje")
            )

            poligonos = [exterior] + [path for path in interiores if len(path) >= 2]

            piezas_convertidas.append({
                "nombre": pieza.get("mark") or pieza.get("item") or "-",
                "item": pieza.get("item") or "-",
                "job": pieza.get("job") or "-",
                "poligonos": poligonos,
                "marcas": marcas,
            })

        plates.append({
            "id": placa.get("id"),
            "base_id": _plate_base_id(placa.get("id")),
            "calibre": placa.get("calibre"),
            "material": placa.get("material"),
            "placa_w": _to_float(placa.get("anchoPlaca"), 0.0),
            "placa_h": _to_float(placa.get("largoPlaca"), 0.0),
            "piezas": piezas_convertidas,
            "real_piece_count": len(piezas_convertidas),
            "sheet_code": placa.get("sheet_code") or f"{(detalle_radiografia or {}).get('nombre', 'ORDEN')}-H{idx}",
        })

    return plates

def exportar_pdf_radiografia_web(
    detalle_radiografia,
    ruta_pdf,
    nombre_orden="",
    work_order_label=None,
):
    plates = _radiografia_to_pdf_plates(detalle_radiografia)
    if not plates:
        raise ValueError("No hay placas válidas para exportar a PDF.")

    c = canvas.Canvas(ruta_pdf, pagesize=LETTER)
    width, height = LETTER

    title_color = colors.HexColor("#0F172A")
    subtitle_color = colors.HexColor("#64748B")
    line_color = colors.HexColor("#CBD5E1")
    outer_fill = colors.HexColor("#BFDBFE")
    outer_stroke = colors.HexColor("#1E3A8A")
    mark_color = colors.HexColor("#DC2626")
    table_head = colors.HexColor("#E2E8F0")
    table_line = colors.HexColor("#CBD5E1")
    body_text = colors.HexColor("#334155")

    orden_label = nombre_orden or (detalle_radiografia or {}).get("nombre", "")
    job_fallback = (detalle_radiografia or {}).get("nombre", "-")

    wo_label = _format_work_order_label(
        work_order_label or (detalle_radiografia or {}).get("nombre"),
        1,
    )

    _draw_cover_pages(
        c=c,
        width=width,
        height=height,
        plates=plates,
        nombre_orden=orden_label,
        work_order_label=wo_label,
        title_color=title_color,
        subtitle_color=subtitle_color,
        line_color=line_color,
        table_head=table_head,
        table_line=table_line,
        body_text=body_text,
    )

    _draw_consolidated_piece_pages(
        c=c,
        width=width,
        height=height,
        plates=plates,
        nombre_orden=orden_label,
        work_order_label=wo_label,
        title_color=title_color,
        subtitle_color=subtitle_color,
        line_color=line_color,
        table_head=table_head,
        table_line=table_line,
        body_text=body_text,
        meta_por_ruta=None,
        job_fallback=job_fallback,
    )

    for page_idx, plate in enumerate(plates, start=1):
        if plate.get("cu_rtz_virtual"):
            continue
        piezas_reales = [p for p in plate["piezas"] if _is_real_piece(p)]
        rows, ids_map = _build_group_summary(
            piezas_reales,
            meta_por_ruta=None,
            job_fallback=job_fallback,
        )

        sheet_code = plate.get("sheet_code") or f"{wo_label}-H{page_idx}"

        _draw_watermark_logo(c, width, height, LOGO_ICON1_PATH)
        _draw_header_logo_left(c, LOGO_MAIN_PATH)

        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(title_color)
        c.drawCentredString(width / 2, 760, "Reporte de Producción NestingPro")

        c.setFont("Helvetica", 8.5)
        c.setFillColor(subtitle_color)
        subt = f"Orden: {orden_label or 'N/A'} | Placa: {plate['id']} | Hoja: {sheet_code}"
        c.drawCentredString(width / 2, 744, subt)

        c.setStrokeColor(line_color)
        c.setLineWidth(1)
        c.line(18, 732, width - 18, 732)

        c.setFillColor(title_color)
        c.setFont("Helvetica-Bold", 10.5)
        c.drawString(18, 698, f"Material: {plate['material']} | Calibre: {plate['calibre']}")

        c.setFont("Helvetica", 8.3)
        c.setFillColor(subtitle_color)
        c.drawString(
            18,
            684,
            f"Dimensiones de placa: {_fmt_in(plate['placa_w'])} in x {_fmt_in(plate['placa_h'])} in | Piezas en placa: {len(piezas_reales)}",
        )

        tiene_rtz = _mother_has_rtz_overlays(plate)
        rtz_ids = _rtz_ids_en_placa(plate) if tiene_rtz else []
        if tiene_rtz:
            c.setFillColor(colors.HexColor("#B45309"))
            c.setFont("Helvetica-Bold", 8.0)
            c.drawString(
                18,
                670,
                _texto_retazo_rtz_placa(plate, rtz_ids),
            )

        leg_y, leg_y_rtz, draw_top = _resolve_plate_page_layout(plate, tiene_rtz)
        _draw_standard_legend(c, leg_y, outer_fill, outer_stroke, mark_color, body_text)
        if leg_y_rtz is not None:
            _draw_rtz_legend(c, leg_y_rtz, body_text, cobre=bool(plate.get("modo_largos_cu")))

        rows_to_draw = rows or [
            {
                "displayId": "-",
                "job": "No hay piezas para listar.",
                "item": "",
                "largoIn": "",
                "anchoIn": "",
                "cantidad": "",
            }
        ]
        col_widths = [48, 120, 220, 55, 55, 50]
        table_w = sum(col_widths)
        table_x = (width - table_w) / 2.0
        header_h = 18
        row_h = 18

        table_bottom = 72
        gap_between = 14
        min_draw_h = 120

        max_table_h_first = max(0, draw_top - table_bottom - gap_between - min_draw_h)
        max_rows_first = max(1, int((max_table_h_first - header_h) // row_h))

        first_page_rows = rows_to_draw[:max_rows_first]
        overflow_rows = rows_to_draw[max_rows_first:]

        total_h = header_h + row_h * len(first_page_rows)
        table_y = table_bottom

        draw_x = 18
        draw_y = table_y + total_h + gap_between
        draw_w = width - 36
        draw_h = max(0.0, draw_top - draw_y)

        # Zona de dibujo
        c.setFillColor(colors.white)
        c.setStrokeColor(table_line)
        c.setLineWidth(1)
        c.rect(draw_x, draw_y, draw_w, draw_h, fill=1, stroke=1)

        c.saveState()
        clip = c.beginPath()
        clip.rect(draw_x + 0.5, draw_y + 0.5, max(0, draw_w - 1), max(0, draw_h - 1))
        c.clipPath(clip, stroke=0, fill=0)

        scale, ox, oy = _fit_plate(
            draw_x,
            draw_y,
            draw_w,
            draw_h,
            plate["placa_w"],
            plate["placa_h"],
        )

        c.setStrokeColor(colors.HexColor("#94A3B8"))
        c.setLineWidth(0.8)
        c.rect(
            ox,
            oy,
            max(plate["placa_w"], 1) * scale,
            max(plate["placa_h"], 1) * scale,
            fill=0,
            stroke=1,
        )

        _draw_plate_nesting_geometry(
            c, plate, scale, ox, oy, ids_map, outer_stroke, outer_fill, mark_color
        )
        c.restoreState()

        # Tabla
        c.setStrokeColor(table_line)
        c.setLineWidth(1)
        c.setFillColor(colors.white)
        c.rect(table_x, table_y, table_w, total_h, fill=1, stroke=1)

        c.setFillColor(table_head)
        c.rect(
            table_x,
            table_y + row_h * len(first_page_rows),
            table_w,
            header_h,
            fill=1,
            stroke=0,
        )

        headers = ["ID", "JOB", "ITEM", "L (in)", "W (in)", "Cant."]

        x = table_x
        c.setFont("Helvetica-Bold", 7.2)
        c.setFillColor(title_color)

        for w_col, head in zip(col_widths, headers):
            c.rect(
                x,
                table_y + row_h * len(first_page_rows),
                w_col,
                header_h,
                fill=0,
                stroke=1,
            )
            c.drawCentredString(
                x + w_col / 2.0,
                table_y + row_h * len(first_page_rows) + 5.3,
                head,
            )
            x += w_col

        for ridx, row in enumerate(first_page_rows):
            y = table_y + row_h * (len(first_page_rows) - 1 - ridx)
            x = table_x

            values = [
                row["displayId"],
                row["job"],
                row["item"],
                str(row["largoIn"]),
                str(row["anchoIn"]),
                str(row["cantidad"]),
            ]
            aligns = ["center", "left", "left", "center", "center", "center"]

            for w_col, value, align in zip(col_widths, values, aligns):
                c.rect(x, y, w_col, row_h, fill=0, stroke=1)
                c.setFillColor(body_text)
                _draw_cell_text(
                    c,
                    value,
                    x,
                    y + 5.1,
                    w_col,
                    align=align,
                    font_name="Helvetica",
                    font_size=7.4,
                )
                x += w_col

        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(subtitle_color)
        c.drawRightString(width - 24, 24, sheet_code)
        c.showPage()

        # Continuaciones si la tabla no cabe
        _draw_sheet_table_continuation_pages(
            c=c,
            width=width,
            height=height,
            plate=plate,
            nombre_orden=orden_label,
            job_fallback=job_fallback,
            sheet_code=sheet_code,
            rows_overflow=overflow_rows,
            title_color=title_color,
            subtitle_color=subtitle_color,
            line_color=line_color,
            table_head=table_head,
            table_line=table_line,
            body_text=body_text,
        )

    c.save()