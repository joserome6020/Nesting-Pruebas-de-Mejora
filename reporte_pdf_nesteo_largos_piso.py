"""
PDF de consumo en piso para nesteo de largos.

Sección 1: tabla ACCESORIOS LARGOS (demanda CSV, cantidades por 1 unidad).
Sección 2: mapas de corte estilo lista de largos + leyenda con medidas explícitas.
"""
from __future__ import annotations

import os
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from reporte_pdf_lista_largos import (
    FONT_BOLD,
    FONT_REG,
    KERF,
    RECORTE_EXTREMO,
    THEME,
    _build_bar_legend_and_labels,
    _fit_text,
    _hatch_rect,
)
from reporte_pdf_nesting import LOGO_ICON1_PATH, _draw_image_fit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_MAIN_PATH = os.path.join(BASE_DIR, "assets", "branding", "logo_main.png")

PIEZA_COLORES = [
    "#2563EB", "#16A34A", "#D97706", "#9333EA", "#0891B2",
    "#EA580C", "#DB2777", "#65A30D", "#E11D48", "#0D9488",
]

HEADER_BG = colors.HexColor("#1E3A8A")
HEADER_FG = colors.white
NOTE_RED = colors.HexColor("#DC2626")
GRID_LINE = colors.HexColor("#1E293B")
ROW_H_DATA = 0.26 * inch
ROW_H_HEADER = 0.50 * inch
ROW_FILL_ALPHA = 0.38
CELL_PAD_X = 5
CELL_PAD_Y = 8
FRAME_LINE_W = 1.0

# Proporciones relativas al ancho útil de la hoja (suma = 1.0)
ACCESORIOS_COL_SPECS = (
    ("ITEM", 0.110, "L"),
    ("DESCRIPCION DEL\nMATERIAL", 0.255, "L"),
    ("TIPO\nMATERIAL", 0.085, "C"),
    ("LONGITUD", 0.090, "C"),
    ("CANTIDAD", 0.075, "C"),
    ("PROCESO", 0.100, "C"),
    ("STATUS", 0.075, "C"),
    ("ENTREGADO A", 0.210, "C"),
)


def _font_size_for_width(
    txt: str,
    max_width: float,
    font_name: str,
    max_size: float = 7.5,
    min_size: float = 5.5,
) -> float:
    """Reduce tamaño de fuente hasta que el texto quepa sin truncar."""
    s = str(txt or "")
    if not s:
        return max_size
    size = max_size
    while size >= min_size:
        if stringWidth(s, font_name, size) <= max_width:
            return size
        size -= 0.25
    return min_size


def _draw_watermark_piso(c, width: float, height: float, image_path: str) -> None:
    """Marca de agua centrada, un poco más grande que en otros reportes."""
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
            x=72,
            y=155,
            max_w=455,
            max_h=455,
            preserve_aspect=True,
            mask="auto",
        )
        c.restoreState()
    except Exception:
        try:
            c.restoreState()
        except Exception:
            pass


def _draw_scaled_title(
    c,
    txt: str,
    x0: float,
    width: float,
    y: float,
    color,
    max_size: float = 13.0,
    min_size: float = 8.0,
) -> None:
    """Título completo; reduce fuente si el job es muy largo (sin truncar)."""
    s = str(txt or "").strip()
    if not s:
        return
    size = _font_size_for_width(s, width, FONT_BOLD, max_size, min_size)
    c.setFont(FONT_BOLD, size)
    c.setFillColor(color)
    tw = stringWidth(s, FONT_BOLD, size)
    c.drawString(x0 + max(0.0, (width - tw) / 2), y, s)


def _draw_header_label(
    c,
    label: str,
    x: float,
    cw: float,
    hdr_top: float,
    align: str,
    max_size: float = 7.5,
    min_size: float = 5.5,
) -> None:
    """Etiqueta de encabezado de columna: texto completo, sin '...'."""
    inner_w = max(8.0, cw - 2 * CELL_PAD_X)
    lines = str(label).split("\n")
    c.setFillColor(HEADER_FG)
    if align == "C":
        for j, line in enumerate(lines):
            line_y = hdr_top - 0.20 * inch - j * 0.12 * inch
            if len(lines) == 1:
                line_y = hdr_top - 0.20 * inch
            sz = _font_size_for_width(line, inner_w, FONT_BOLD, max_size, min_size)
            c.setFont(FONT_BOLD, sz)
            c.drawCentredString(x + cw / 2, line_y, line)
    elif len(lines) == 1:
        sz = _font_size_for_width(lines[0], inner_w, FONT_BOLD, max_size, min_size)
        c.setFont(FONT_BOLD, sz)
        c.drawString(x + CELL_PAD_X, hdr_top - 0.20 * inch, lines[0])
    else:
        for j, line in enumerate(lines):
            line_y = hdr_top - 0.14 * inch - j * 0.12 * inch
            sz = _font_size_for_width(line, inner_w, FONT_BOLD, max_size, min_size)
            c.setFont(FONT_BOLD, sz)
            c.drawString(x + CELL_PAD_X, line_y, line)


def _table_layout(table_x0: float, table_x1: float) -> tuple[list[float], list[float]]:
    """Calcula posiciones X y anchos de columna proporcionales al ancho de tabla."""
    total_w = table_x1 - table_x0
    xs = [table_x0]
    widths: list[float] = []
    for _label, frac, _align in ACCESORIOS_COL_SPECS:
        w = total_w * frac
        widths.append(w)
        xs.append(xs[-1] + w)
    xs[-1] = table_x1
    return xs, widths


def _proceso_desde_fila(raw: dict) -> str:
    """Solo usa PROCESO si viene en el CSV; no se inventa."""
    return str(raw.get("proceso") or "").strip()


def filas_csv_a_accesorios(filas_demanda: list[dict]) -> list[dict]:
    """Convierte filas de demanda (CSV / lista_largos) al formato tabla ACCESORIOS."""
    from catalogo_largos import (
        construir_etiqueta_clasificacion,
        normalizar_fila_lista_largos,
        parse_clasificacion_largo,
    )
    from interface.largos_nesting_service import nombre_pieza_largo_display

    out: list[dict] = []
    for raw in filas_demanda or []:
        norm = normalizar_fila_lista_largos(raw)
        clas = str(norm.get("clasificacion") or "").strip()
        parsed = parse_clasificacion_largo(clas)
        if clas:
            desc = clas.upper()
        else:
            desc = construir_etiqueta_clasificacion(
                {
                    "perfil_estructural": parsed.get("perfil_estructural"),
                    "ancho_in": parsed.get("ancho_in"),
                    "espesor_in": parsed.get("espesor_in"),
                    "codigo": parsed.get("codigo_herinox") or "",
                }
            ).upper()
        tipo_mat = str(
            raw.get("material_grade")
            or parsed.get("material_grade")
            or "A36"
        ).upper()
        try:
            largo = float(norm.get("largo_in") or raw.get("largo_in") or 0)
        except Exception:
            largo = 0.0
        try:
            cant = int(
                raw.get("cantidad_base")
                if raw.get("cantidad_base") is not None
                else raw.get("cantidad")
                or 0
            )
        except Exception:
            cant = 0
        if not str(norm.get("nombre") or "").strip():
            continue
        out.append(
            {
                "item": nombre_pieza_largo_display(str(norm.get("nombre") or "").strip()),
                "descripcion": desc,
                "tipo_material": tipo_mat,
                "longitud": f'{largo:g}"',
                "cantidad": cant,
                "proceso": _proceso_desde_fila(raw),
            }
        )
    return out


def _draw_wrapped_centered_in_box(
    c,
    x: float,
    y: float,
    w: float,
    h: float,
    lines: list[str],
    font: str,
    size: float,
    color,
) -> None:
    c.setFillColor(color)
    c.setFont(font, size)
    line_h = size + 2
    block_h = len(lines) * line_h
    start_y = y + (h - block_h) / 2 + line_h - 3
    for i, line in enumerate(lines):
        c.drawCentredString(x + w / 2, start_y - i * line_h, line)


def _draw_table_grid(
    c,
    x0: float,
    y_top: float,
    x1: float,
    y_bot: float,
    col_xs: list[float],
    ent_split_x: float | None = None,
    ent_split_y_min: float | None = None,
) -> None:
    c.setStrokeColor(GRID_LINE)
    c.setLineWidth(0.6)
    c.rect(x0, y_bot, x1 - x0, y_top - y_bot, fill=0, stroke=1)
    for cx in col_xs[1:-1]:
        c.line(cx, y_bot, cx, y_top)
    if ent_split_x:
        split_top = ent_split_y_min if ent_split_y_min is not None else y_top
        c.line(ent_split_x, y_bot, ent_split_x, split_top)


def _draw_main_frame(c, x0: float, y_top: float, x1: float, y_bot: float) -> None:
    """Recuadro principal que envuelve encabezado de página + tabla."""
    c.setStrokeColor(GRID_LINE)
    c.setLineWidth(FRAME_LINE_W)
    c.rect(x0, y_bot, x1 - x0, y_top - y_bot, fill=0, stroke=1)


def _wrap_item_lines(
    c,
    txt: str,
    max_width: float,
    font: str,
    size: float,
    max_lines: int = 3,
) -> list[str]:
    """Parte nombres de ítem en líneas, cortando preferentemente en guiones."""
    s = str(txt or "").strip()
    if not s:
        return [""]
    if _text_w(c, s, font, size) <= max_width:
        return [s]

    lines: list[str] = []
    rest = s
    while rest and len(lines) < max_lines:
        if _text_w(c, rest, font, size) <= max_width:
            lines.append(rest)
            rest = ""
            break

        best_hyphen = 0
        for i in range(1, len(rest)):
            if rest[i - 1] not in "-_":
                continue
            chunk = rest[:i]
            if _text_w(c, chunk, font, size) <= max_width:
                best_hyphen = i

        if best_hyphen > 0:
            lines.append(rest[:best_hyphen])
            rest = rest[best_hyphen:]
            continue

        lo, hi = 1, len(rest)
        best = 1
        while lo <= hi:
            mid = (lo + hi) // 2
            cand = rest[:mid] + "-"
            if _text_w(c, cand, font, size) <= max_width:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        lines.append(rest[:best] + "-")
        rest = rest[best:]

    if rest and len(lines) < max_lines:
        lines.append(rest)
    return lines if lines else [s]


def _wrap_proceso_lines(
    c,
    txt: str,
    max_width: float,
    font: str,
    size: float,
    max_lines: int = 2,
) -> list[str]:
    """Parte texto de proceso (p. ej. CORTE/MAQ, CORTAR EN 3\"X3\")."""
    s = str(txt or "").strip()
    if not s:
        return [""]
    if _text_w(c, s, font, size) <= max_width:
        return [s]
    if ", " in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        lines: list[str] = []
        cur = ""
        for part in parts:
            test = f"{cur}, {part}".lstrip(", ").strip() if cur else part
            if _text_w(c, test, font, size) <= max_width:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = part
                if len(lines) >= max_lines:
                    break
        if cur and len(lines) < max_lines:
            lines.append(cur)
        if lines:
            return lines
    return _wrap_item_lines(c, s, max_width, font, size, max_lines=max_lines)


def _item_text_block_height(n_lines: int, size: float) -> float:
    """Altura visual del bloque de texto (puntos) para n líneas de ítem."""
    n = max(1, n_lines)
    line_leading = size + 4
    cap = size * 0.75
    return cap + (n - 1) * line_leading


def _draw_cell_text_wrapped(
    c,
    lines: list[str],
    x: float,
    w: float,
    row_top: float,
    row_bot: float,
    font: str = FONT_REG,
    size: float = 7.5,
    align: str = "L",
) -> None:
    c.setFont(font, size)
    c.setFillColor(THEME["ink"])
    cell_h = row_top - row_bot
    line_leading = size + 4
    n = max(1, len(lines))
    text_block_h = _item_text_block_height(n, size)
    cap = size * 0.75
    top_y = row_bot + (cell_h + text_block_h) / 2
    first_baseline = top_y - cap
    for i, line in enumerate(lines):
        if align == "C":
            tw = _text_w(c, line, font, size)
            lx = x + (w - tw) / 2
        else:
            lx = x + CELL_PAD_X
        c.drawString(lx, first_baseline - i * line_leading, line)


def _draw_cell_text(
    c,
    txt: str,
    x: float,
    w: float,
    y_mid: float,
    align: str,
    font: str = FONT_REG,
    size: float = 7.5,
) -> None:
    c.setFont(font, size)
    c.setFillColor(THEME["ink"])
    inner_w = max(4, w - 2 * CELL_PAD_X)
    fitted = _fit_text(c, txt, inner_w, font, size)
    if align == "C":
        tw = _text_w(c, fitted, font, size)
        c.drawString(x + (w - tw) / 2, y_mid, fitted)
    else:
        c.drawString(x + CELL_PAD_X, y_mid, fitted)


def _fmt_largo_in(val: float) -> str:
    return f'{float(val):.2f}"'


def _text_w(c, txt: str, font: str, size: float) -> float:
    return stringWidth(str(txt), font, size)


def _draw_stock_total_dimension(
    c,
    track_x: float,
    track_w: float,
    track_bottom: float,
    stock: float,
    dim_base_y: float,
) -> float:
    """Cota única del largo comercial total bajo la pista (sin medidas por corte)."""
    rule_y = dim_base_y + 0.07 * inch
    c.setStrokeColor(colors.HexColor("#64748B"))
    c.setLineWidth(0.55)
    c.line(track_x, track_bottom, track_x, rule_y + 0.01 * inch)
    c.line(track_x + track_w, track_bottom, track_x + track_w, rule_y + 0.01 * inch)
    c.line(track_x, rule_y, track_x + track_w, rule_y)
    c.setFont(FONT_BOLD, 8)
    c.setFillColor(THEME["ink"])
    dim_txt = f"LARGO A CORTAR {_fmt_largo_in(stock)}"
    c.drawCentredString(track_x + track_w / 2, dim_base_y - 0.02 * inch, dim_txt)
    return dim_base_y - 0.24 * inch


def _barra_block_height_in(
    c,
    n_legend: int,
) -> float:
    """Altura estimada del bloque completo de una barra (pulgadas)."""
    h = 0.14 + 0.18 + 0.20 + 0.06 + 0.40 + 0.30 + 0.16
    h += 0.24 + 0.22 * max(1, n_legend)
    h += 0.38
    return h + 0.12


def _draw_barras_divider(c, x0: float, x1: float, y: float) -> float:
    """Línea divisoria horizontal (mismo estilo que bajo el título MAPAS DE CORTE)."""
    y -= 0.10 * inch
    c.setStrokeColor(THEME["line"])
    c.setLineWidth(0.8)
    c.line(x0, y, x1, y)
    return y - 0.22 * inch


BARRA_GAP_FIRST_IN = 0.14
BARRA_GAP_DIVIDER_IN = 0.32


def _draw_barras_section_banner(c, x0: float, x1: float, y: float, job: str, n_barras: int) -> float:
    """Encabezado de la sección de mapas de corte."""
    c.setFont(FONT_BOLD, 14)
    c.setFillColor(THEME["ink"])
    c.drawString(x0, y, "MAPAS DE CORTE")
    y -= 0.20 * inch
    c.setFont(FONT_REG, 9)
    c.setFillColor(THEME["muted"])
    c.drawString(x0, y, f"Job {job} · {n_barras} barra(s) en pedido")
    return _draw_barras_divider(c, x0, x1, y)


def _parse_legend_row_text(text: str) -> tuple[str, str]:
    from interface.largos_nesting_service import nombre_pieza_largo_display

    s = str(text or "").strip().strip('"')
    if " - " in s:
        name, largo = s.rsplit(" - ", 1)
        largo = largo.strip().rstrip('"')
        if largo and not largo.endswith('"'):
            largo = f'{largo}"'
        return nombre_pieza_largo_display(name.strip()), largo
    return nombre_pieza_largo_display(s), ""


def _draw_cortes_detail_table(
    c,
    x0: float,
    x1: float,
    y: float,
    legend_rows: list[dict],
) -> float:
    """Tabla de detalle de cortes con ID coloreado, pieza, largo y cantidad."""
    if not legend_rows:
        return y

    content_w = x1 - x0
    col_fracs = (0.09, 0.61, 0.16, 0.14)
    col_xs = [x0]
    col_widths: list[float] = []
    for frac in col_fracs:
        w = content_w * frac
        col_widths.append(w)
        col_xs.append(col_xs[-1] + w)
    col_xs[-1] = x1

    row_h = 0.22 * inch
    hdr_h = 0.24 * inch
    hdr_top = y
    hdr_bot = y - hdr_h

    c.setFillColor(HEADER_BG)
    c.rect(x0, hdr_bot, content_w, hdr_h, fill=1, stroke=0)
    c.setFillColor(HEADER_FG)
    c.setFont(FONT_BOLD, 7.5)
    headers = ("#", "PIEZA", "LARGO", "CANT.")
    aligns = ("C", "L", "C", "C")
    for i, (lbl, align) in enumerate(zip(headers, aligns)):
        cx = col_xs[i] + col_widths[i] / 2
        if align == "C":
            c.drawCentredString(cx, hdr_bot + 0.08 * inch, lbl)
        else:
            c.drawString(col_xs[i] + CELL_PAD_X, hdr_bot + 0.08 * inch, lbl)
    _draw_table_grid(c, x0, hdr_top, x1, hdr_bot, col_xs)
    y -= hdr_h

    for row in legend_rows:
        label = int(row.get("label") or 0)
        pieza, largo = _parse_legend_row_text(str(row.get("text") or ""))
        qty = int(row.get("qty") or 1)

        row_top = y
        row_bot = y - row_h
        y_mid = row_bot + (row_h - 8) / 2 - 1

        c.setFillColor(colors.HexColor("#FAFAFA"))
        c.rect(x0, row_bot, content_w, row_h, fill=1, stroke=0)

        dot_x = col_xs[0] + col_widths[0] / 2 - 5
        c.setFillColor(colors.HexColor(PIEZA_COLORES[(label - 1) % len(PIEZA_COLORES)]))
        c.circle(dot_x, y_mid + 1, 3.5, fill=1, stroke=0)
        c.setFillColor(THEME["ink"])
        c.setFont(FONT_BOLD, 8)
        c.drawString(dot_x + 7, y_mid, f"({label})")

        _draw_cell_text(c, pieza, col_xs[1], col_widths[1], y_mid, "L", size=8)
        _draw_cell_text(c, largo, col_xs[2], col_widths[2], y_mid, "C", size=8)
        _draw_cell_text(c, str(qty), col_xs[3], col_widths[3], y_mid, "C", size=8, font=FONT_BOLD)

        _draw_table_grid(c, x0, row_top, x1, row_bot, col_xs)
        y -= row_h

    return y


def _draw_barras_continuation_banner(c, x0: float, x1: float, y: float) -> float:
    c.setFont(FONT_BOLD, 10)
    c.setFillColor(THEME["muted"])
    c.drawString(x0, y, "MAPAS DE CORTE (continuación)")
    return _draw_barras_divider(c, x0, x1, y)


def generar_pdf_nesteo_largos_piso(snapshot: dict, ruta_pdf: str | None = None) -> BytesIO:
    snapshot = snapshot or {}
    filas = snapshot.get("filas_accesorios") or filas_csv_a_accesorios(
        snapshot.get("filas_demanda") or []
    )
    barras = snapshot.get("barras_piso") or []
    job = str(snapshot.get("job") or snapshot.get("titulo_job") or "JOB").strip()
    titulo = str(snapshot.get("titulo") or f"ACCESORIOS LARGOS TANK ({job})").strip()

    buffer = BytesIO()
    c = canvas.Canvas(ruta_pdf or buffer, pagesize=letter)
    W, H = letter
    margin = 0.30 * inch
    table_x0 = margin
    table_x1 = W - margin
    y = H - margin
    page_frame_top: float | None = None

    def _close_page_frame(bottom_y: float) -> None:
        nonlocal page_frame_top
        if page_frame_top is not None:
            _draw_main_frame(c, table_x0, page_frame_top, table_x1, bottom_y)
            page_frame_top = None

    def _watermark():
        _draw_watermark_piso(c, W, H, LOGO_ICON1_PATH)

    def new_page():
        nonlocal y, page_frame_top
        _close_page_frame(y)
        c.showPage()
        c.setPageSize(letter)
        Wn, Hn = letter
        _watermark()
        y = Hn - margin
        page_frame_top = None
        return Wn, Hn

    def _draw_logo_in_header(
        c,
        x: float,
        zone_top: float,
        zone_bot: float,
        max_w: float = 1.55 * inch,
        max_h: float = 0.58 * inch,
    ) -> float:
        """Logo centrado verticalmente en la franja blanca del encabezado. Retorna ancho reservado."""
        zone_h = max(0.1 * inch, zone_top - zone_bot)
        max_h = min(max_h, zone_h - 0.08 * inch)
        if os.path.isfile(LOGO_MAIN_PATH):
            try:
                img = ImageReader(LOGO_MAIN_PATH)
                iw, ih = img.getSize()
                scale = min(max_w / float(iw), max_h / float(ih))
                draw_w = iw * scale
                draw_h = ih * scale
                draw_y = zone_bot + (zone_h - draw_h) / 2
                c.drawImage(img, x, draw_y, draw_w, draw_h, mask="auto")
                return draw_w
            except Exception:
                pass
        c.setFont(FONT_BOLD, 12)
        c.setFillColor(THEME["ink"])
        tw = _text_w(c, "GRUPO ARGA", FONT_BOLD, 12)
        draw_y = zone_bot + (zone_h - 12) / 2
        c.drawString(x, draw_y, "GRUPO ARGA")
        return tw

    def _draw_accesorios_header():
        nonlocal y, page_frame_top
        top = y
        header_h = 0.72 * inch
        header_bot = top - header_h
        if page_frame_top is None:
            page_frame_top = top

        pad_x = 0.10 * inch
        logo_slot_w = 1.55 * inch
        logo_drawn_w = _draw_logo_in_header(
            c,
            table_x0 + pad_x,
            top,
            header_bot,
            max_w=logo_slot_w,
            max_h=0.58 * inch,
        )
        logo_w = max(logo_slot_w, logo_drawn_w + 0.06 * inch)

        note_w = min(1.95 * inch, (table_x1 - table_x0) * 0.22)
        note_h = 0.40 * inch
        note_x = table_x1 - pad_x - note_w
        note_y = header_bot + (header_h - note_h) / 2
        c.setStrokeColor(NOTE_RED)
        c.setLineWidth(1.0)
        c.setFillColor(colors.white)
        c.rect(note_x, note_y, note_w, note_h, fill=1, stroke=1)
        _draw_wrapped_centered_in_box(
            c,
            note_x,
            note_y,
            note_w,
            note_h,
            ["NOTA: CANTIDADES APLICAN", "UNICAMENTE PARA 1 UNIDAD"],
            FONT_BOLD,
            6.0,
            NOTE_RED,
        )

        title_x0 = table_x0 + logo_w + pad_x + 0.08 * inch
        title_x1 = note_x - 0.14 * inch
        title_w = max(1.0 * inch, title_x1 - title_x0)
        _draw_scaled_title(
            c,
            titulo,
            title_x0,
            title_w,
            header_bot + header_h * 0.38,
            colors.HexColor("#2563EB"),
            max_size=11.0,
            min_size=7.5,
        )

        y = header_bot
        c.setStrokeColor(GRID_LINE)
        c.setLineWidth(0.6)
        c.line(table_x0, y, table_x1, y)

    def _draw_accesorios_table(start_y: float) -> float:
        nonlocal y, page_frame_top
        y = start_y
        col_xs, col_widths = _table_layout(table_x0, table_x1)
        ent_x = col_xs[-2]
        ent_w = col_widths[-1]
        ent_mid = ent_x + ent_w / 2
        data_font_size = 8.0

        def draw_header_row():
            nonlocal y, page_frame_top
            if page_frame_top is None:
                page_frame_top = y
            hdr_top = y
            hdr_bot = y - ROW_H_HEADER
            c.setFillColor(HEADER_BG)
            c.setFillAlpha(1.0)
            c.rect(table_x0, hdr_bot, table_x1 - table_x0, ROW_H_HEADER, fill=1, stroke=0)
            c.setFillAlpha(1.0)

            c.setFillColor(HEADER_FG)
            for i, (label, _frac, align) in enumerate(ACCESORIOS_COL_SPECS[:-1]):
                _draw_header_label(
                    c, label, col_xs[i], col_widths[i], hdr_top, align,
                )

            ent_inner_w = max(8.0, ent_w - 2 * CELL_PAD_X)
            c.setFont(FONT_BOLD, 7.5)
            c.drawCentredString(ent_mid, hdr_top - 0.12 * inch, "ENTREGADO A:")
            ent_cap_sz = _font_size_for_width(
                "Firma y fecha de persona que recibe",
                ent_inner_w,
                FONT_REG,
                5.8,
                4.8,
            )
            c.setFont(FONT_REG, ent_cap_sz)
            c.drawCentredString(ent_mid, hdr_top - 0.23 * inch, "Firma y fecha de persona que recibe")
            sep_y = hdr_top - 0.30 * inch
            c.setStrokeColor(colors.HexColor("#93C5FD"))
            c.setLineWidth(0.55)
            c.line(ent_x, sep_y, table_x1, sep_y)
            sub_w = ent_w / 2
            c.setFont(FONT_BOLD, 7.0)
            c.drawCentredString(ent_x + sub_w / 2, hdr_bot + 0.09 * inch, "MAQUINADOS")
            c.drawCentredString(ent_x + sub_w + sub_w / 2, hdr_bot + 0.09 * inch, "PRODUCCION")

            _draw_table_grid(
                c, table_x0, hdr_top, table_x1, hdr_bot, col_xs,
                ent_split_x=ent_mid, ent_split_y_min=sep_y,
            )
            y -= ROW_H_HEADER

        draw_header_row()
        row_idx = 0
        for row in filas:
            item_inner_w = max(8.0, col_widths[0] - 2 * CELL_PAD_X)
            item_lines = _wrap_item_lines(
                c,
                str(row.get("item") or ""),
                item_inner_w,
                FONT_REG,
                data_font_size,
                max_lines=4,
            )
            proceso_inner_w = max(8.0, col_widths[5] - 2 * CELL_PAD_X)
            proceso_lines = _wrap_proceso_lines(
                c,
                str(row.get("proceso") or ""),
                proceso_inner_w,
                FONT_REG,
                data_font_size,
                max_lines=2,
            )
            row_h = max(
                ROW_H_DATA,
                _item_text_block_height(len(item_lines), data_font_size) + 2 * CELL_PAD_Y,
                _item_text_block_height(len(proceso_lines), data_font_size) + 2 * CELL_PAD_Y,
            )

            if y < margin + row_h + 0.35 * inch:
                _close_page_frame(y)
                new_page()
                draw_header_row()

            row_top = y
            row_bot = y - row_h
            y_mid = row_bot + (row_h - data_font_size) / 2 - 1

            c.saveState()
            fill = colors.HexColor("#F1F5F9") if row_idx % 2 else colors.white
            c.setFillColor(fill)
            c.setFillAlpha(ROW_FILL_ALPHA)
            c.rect(table_x0, row_bot, table_x1 - table_x0, row_h, fill=1, stroke=0)
            c.restoreState()

            cells = [
                row.get("item", ""),
                row.get("descripcion", ""),
                row.get("tipo_material", ""),
                row.get("longitud", ""),
                str(row.get("cantidad", "")),
                row.get("proceso", ""),
                "",
            ]
            for i, (val, (_lbl, _frac, align)) in enumerate(
                zip(cells, ACCESORIOS_COL_SPECS[:-1])
            ):
                if i == 0:
                    _draw_cell_text_wrapped(
                        c, item_lines, col_xs[i], col_widths[i], row_top, row_bot,
                        size=data_font_size,
                        align="C",
                    )
                elif i == 5:
                    _draw_cell_text_wrapped(
                        c, proceso_lines, col_xs[i], col_widths[i], row_top, row_bot,
                        size=data_font_size,
                        align="C",
                    )
                else:
                    _draw_cell_text(
                        c, str(val), col_xs[i], col_widths[i], y_mid, align, size=data_font_size
                    )

            _draw_table_grid(c, table_x0, row_top, table_x1, row_bot, col_xs, ent_split_x=ent_mid)
            y -= row_h
            row_idx += 1

        _close_page_frame(y)
        return y

    # --- Página 1: demanda CSV ---
    _watermark()
    _draw_accesorios_header()
    y = _draw_accesorios_table(y)

    # --- Mapas de corte (estilo nesteo lista de largos) ---
    if barras:
        new_page()
        ctx = {
            "c": c,
            "margin": margin,
            "content_x0": table_x0,
            "content_x1": table_x1,
            "W": W,
            "H": H,
            "job": job,
            "new_page": new_page,
        }
        y = _draw_barras_section_banner(c, table_x0, table_x1, y, job, len(barras))
        for idx, item in enumerate(barras, start=1):
            y, W, H = _draw_barra_piso(ctx, item, y, idx)

    c.save()
    if ruta_pdf:
        return BytesIO()
    buffer.seek(0)
    return buffer


def _draw_barra_piso(ctx: dict, item: dict, y: float, idx: int) -> tuple[float, float, float]:
    """
    Barra estilo reporte lista de largos: pista blanca, recortes rojos, leyenda con medidas.
    Retorna (y, W, H) actualizados.
    """
    c = ctx["c"]
    x0 = ctx.get("content_x0", ctx["margin"])
    x1 = ctx.get("content_x1", ctx["W"] - ctx["margin"])
    content_w = x1 - x0
    margin = ctx["margin"]
    W = ctx["W"]
    H = ctx["H"]
    barra = item.get("barra") or {}
    etiqueta = str(item.get("etiqueta") or f"Barra #{idx}")
    material = str(item.get("material") or item.get("codigo") or "").strip()
    cortes = list(barra.get("cortes") or [])
    stock = float(barra.get("largo_stock") or barra.get("_vista_largo_comercial") or 0)

    legend_rows: list[dict] = []
    labels_for_cortes: list[int] = []
    if cortes:
        labels_for_cortes, legend_rows = _build_bar_legend_and_labels(barra)

    n_legend = max(1, len(legend_rows))
    gap_in = BARRA_GAP_DIVIDER_IN if idx > 1 else BARRA_GAP_FIRST_IN
    block_need = (_barra_block_height_in(c, n_legend) + gap_in) * inch
    fresh_page = False
    if y < margin + block_need:
        W, H = ctx["new_page"]()
        ctx["W"], ctx["H"] = W, H
        y = H - margin
        y = _draw_barras_continuation_banner(c, x0, x1, y)
        fresh_page = True

    if idx > 1 and not fresh_page:
        y = _draw_barras_divider(c, x0, x1, y)
    elif not fresh_page:
        y -= BARRA_GAP_FIRST_IN * inch
    rem_show = float(barra.get("remanente_show", barra.get("remanente_calc", 0)) or 0)
    util = max(0.0, stock - 2 * RECORTE_EXTREMO)
    ocupado = util - rem_show
    ef = (ocupado / util * 100.0) if util > 0 else 0.0

    # Encabezado barra — izquierda etiqueta, derecha eficiencia (sobrante ya en la pista)
    right_col_w = 1.05 * inch
    hdr_gap = 0.12 * inch
    left_max_w = max(0.0, content_w - right_col_w - hdr_gap)
    c.setFont(FONT_BOLD, 10)
    c.setFillColor(THEME["ink"])
    hdr_left = _fit_text(c, f"BARRA #{idx}  ·  {etiqueta}", left_max_w, FONT_BOLD, 10)
    c.drawString(x0, y, hdr_left)
    c.setFont(FONT_BOLD, 9)
    c.setFillColor(THEME["muted"])
    ef_txt = f"Eficiencia {ef:.1f}%"
    c.drawRightString(x1, y, _fit_text(c, ef_txt, right_col_w, FONT_BOLD, 9))
    y -= 0.18 * inch

    if material:
        c.setFont(FONT_REG, 8)
        c.setFillColor(THEME["muted"])
        mat_line = _fit_text(
            c, f"Material: {material}  ·  Stock {_fmt_largo_in(stock)}", content_w, FONT_REG, 8
        )
        c.drawString(x0, y, mat_line)
        y -= 0.20 * inch
    else:
        c.setFont(FONT_REG, 8)
        c.setFillColor(THEME["muted"])
        c.drawString(x0, y, f"Stock comercial {_fmt_largo_in(stock)}")
        y -= 0.20 * inch

    y -= 0.06 * inch
    track_x = x0
    track_w = content_w
    track_h = 0.40 * inch
    track_bottom = y - track_h

    c.setFillColor(colors.white)
    c.rect(track_x, track_bottom, track_w, track_h, fill=1, stroke=0)

    if stock <= 0:
        c.setFillColor(THEME["ink"])
        c.setFont(FONT_REG, 9)
        c.drawString(track_x, track_bottom + track_h / 2 - 4, "Sin largo válido")
        return track_bottom - 0.30 * inch, W, H

    trim_w = track_w * (RECORTE_EXTREMO / stock) if stock > 0 else 0.0
    usable_x0 = track_x + trim_w
    usable_x1 = track_x + track_w - trim_w
    usable_w = max(0.0, usable_x1 - usable_x0)
    scale = (usable_w / util) if util > 0 else 0.0

    c.setFillColor(THEME["trim_red"])
    c.rect(track_x, track_bottom, trim_w, track_h, fill=1, stroke=0)
    c.rect(track_x + track_w - trim_w, track_bottom, trim_w, track_h, fill=1, stroke=0)

    cursor_x = usable_x0
    end_x = usable_x1

    for i_p, pieza in enumerate(cortes):
        largo = float(pieza.get("largo") or 0)
        if largo <= 0:
            continue
        consumo = largo + (KERF if i_p > 0 else 0.0)
        pw = max(0.0, consumo * scale)
        remaining = end_x - cursor_x
        if remaining <= 0:
            break
        pw_draw = min(pw, remaining)

        lab = labels_for_cortes[i_p] if i_p < len(labels_for_cortes) else (i_p + 1)
        col_hex = PIEZA_COLORES[(int(lab) - 1) % len(PIEZA_COLORES)]
        c.setFillColor(colors.HexColor(col_hex))
        c.setStrokeColor(colors.HexColor("#1E3A8A"))
        c.setLineWidth(0.8)
        if pw_draw > 0:
            c.rect(cursor_x, track_bottom, pw_draw, track_h, fill=1, stroke=1)

        cx = cursor_x + pw_draw / 2

        if pw_draw >= 20:
            c.setFillColor(colors.white)
            c.setFont(FONT_BOLD, 7.5)
            c.drawCentredString(cx, track_bottom + track_h / 2 - 2, f"({lab})")

        cursor_x += pw_draw

    rem_w = end_x - cursor_x
    if rem_w > 1:
        c.setFillColor(THEME["orange_light"])
        c.setStrokeColor(colors.HexColor("#FDBA74"))
        c.setLineWidth(1)
        c.rect(cursor_x, track_bottom, rem_w, track_h, fill=1, stroke=1)
        _hatch_rect(c, cursor_x, track_bottom, rem_w, track_h, step=6)
        if rem_w > 55:
            c.setFillColor(colors.HexColor("#1E3A8A"))
            c.setFont(FONT_BOLD, 8)
            rem_txt = f"SOBRANTE {_fmt_largo_in(rem_show)}"
            c.drawCentredString(cursor_x + rem_w / 2, track_bottom + track_h / 2 - 3, rem_txt)

    dim_base_y = track_bottom - 0.14 * inch
    y = _draw_stock_total_dimension(c, track_x, track_w, track_bottom, stock, dim_base_y)
    y -= 0.08 * inch

    c.setFont(FONT_BOLD, 8)
    c.setFillColor(THEME["muted"])
    c.drawString(x0, y, f"DETALLE DE CORTES — Kerf {KERF}\"")
    y -= 0.16 * inch

    y = _draw_cortes_detail_table(c, x0, x1, y, legend_rows)

    return y - 0.24 * inch, W, H
