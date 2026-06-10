from collections import OrderedDict
from io import BytesIO
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

KERF = 0.25
RECORTE_EXTREMO = 0.5
REM_MINIMO = 15.0

THEME = {
    "bg": colors.white,
    "ink": colors.HexColor("#0F172A"),
    "muted": colors.HexColor("#475569"),
    "line": colors.HexColor("#E2E8F0"),
    "card_bg": colors.HexColor("#F1F5F9"),
    "card_border": colors.HexColor("#DCE3EE"),
    "blue": colors.HexColor("#2563EB"),
    "blue_dark": colors.HexColor("#1A4EDB"),
    "trim_red": colors.HexColor("#DC2626"),
    "orange": colors.HexColor("#FB923C"),
    "orange_light": colors.HexColor("#FFEAD5"),
    "hatch": colors.HexColor("#F97316"),
}

FONT_REG = "Helvetica"
FONT_BOLD = "Helvetica-Bold"


def _fit_text(c, txt, max_width, font_name, font_size):
    if txt is None:
        return ""
    s = str(txt)
    c.setFont(font_name, font_size)
    if c.stringWidth(s, font_name, font_size) <= max_width:
        return s
    ell = "..."
    lo, hi = 0, len(s)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = s[:mid] + ell
        if c.stringWidth(candidate, font_name, font_size) <= max_width:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best if best else ell


def _round_rect(c, x, y, w, h, r=10, fill=1, stroke=0):
    c.roundRect(x, y, w, h, r, fill=fill, stroke=stroke)


def _hatch_rect(c, x, y, w, h, step=6):
    if w <= 0 or h <= 0:
        return

    c.saveState()
    path = c.beginPath()
    path.rect(x, y, w, h)
    c.clipPath(path, stroke=0, fill=0)

    c.setLineWidth(1)
    c.setStrokeColor(THEME["hatch"])

    t = -h
    end = w + h
    while t < end:
        c.line(x + t, y, x + t + h, y + h)
        t += step

    c.restoreState()


def _fmt_num(n):
    try:
        return f"{float(n):.2f}"
    except Exception:
        return "0.00"


def _fmt_fecha(value):
    if not value:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    try:
        dt = datetime.fromisoformat(str(value))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def _build_bar_legend_and_labels(bar):
    legend = OrderedDict()
    labels_for_cortes = []
    next_label = 1

    for p in bar.get("cortes", []):
        name = str(p.get("nombre", "")).strip()
        largo = float(p.get("largo", 0.0) or 0.0)
        key = (name, round(largo, 3))

        if key not in legend:
            legend[key] = {"label": next_label, "name": name, "largo": largo, "qty": 0}
            next_label += 1

        legend[key]["qty"] += 1
        labels_for_cortes.append(legend[key]["label"])

    legend_rows = []
    for _, info in legend.items():
        label = info["label"]
        line_txt = f'"{info["name"]} - {info["largo"]:.2f}"'
        legend_rows.append({"label": label, "text": line_txt, "qty": info["qty"]})

    return labels_for_cortes, legend_rows


def calcular_kpis(data_nesteo):
    barras_totales = 0
    piezas_totales = 0
    total_capacidad_util = 0.0
    total_usado = 0.0

    material_requerido = {}

    for mat, barras in (data_nesteo or {}).items():
        for b in (barras or []):
            barras_totales += 1

            source = str(b.get("source") or "STOCK").upper()
            stock = float(b.get("largo_stock", 0.0) or 0.0)
            rem_id = b.get("rem_id") if source == "REMANENTE" else None

            k = (str(mat), stock, source, rem_id)
            material_requerido[k] = material_requerido.get(k, 0) + 1

            util = stock - (RECORTE_EXTREMO * 2)
            total_capacidad_util += max(0.0, util)

            for p in (b.get("cortes") or []):
                piezas_totales += 1
                total_usado += float(p.get("largo", 0.0) or 0.0) + KERF

    eficiencia = (total_usado / total_capacidad_util * 100) if total_capacidad_util > 0 else 0.0

    tabla = []
    for (mat, stock, source, rem_id), qty in material_requerido.items():
        tabla.append({
            "material": mat,
            "stock": stock,
            "cantidad": qty,
            "source": source,
            "rem_id": rem_id,
        })

    tabla.sort(key=lambda r: (r["material"], 0 if r["source"] == "STOCK" else 1, r["stock"], (r["rem_id"] or "")))
    return barras_totales, piezas_totales, eficiencia, tabla


def build_remanentes_resultantes(data_nesteo, rem_id_map=None):
    rem_id_map = rem_id_map or {}
    out = {}
    for mat, barras in (data_nesteo or {}).items():
        res = []
        for b in barras:
            if str(b.get("source") or "STOCK").upper() != "STOCK":
                continue

            rem = float(b.get("remanente_show", b.get("remanente_calc", 0.0)) or 0.0)
            if rem <= REM_MINIMO:
                continue

            barra_index = int(b.get("barra_index") or 0)
            rid = rem_id_map.get((str(mat), barra_index))
            res.append({
                "barra_origen": barra_index,
                "largo_stock": float(b.get("largo_stock", 0.0) or 0.0),
                "remanente_show": rem,
                "remanente_real": b.get("remanente_real"),
                "source": "REMANENTE_RESULTANTE",
                "rem_id": rid,
            })

        if res:
            out[str(mat)] = res
    return out


def generar_pdf_lista_largos(snapshot: dict):
    snapshot = snapshot or {}
    data_nesteo = snapshot.get("data_nesteo") or {}
    remanentes_resultantes = snapshot.get("remanentes_resultantes") or {}
    titulo_job = snapshot.get("titulo_job") or snapshot.get("orden_id") or "LISTA DE LARGOS"

    operador = snapshot.get("operador") or "-"
    bahia = snapshot.get("bahia") or "-"
    orden_id = snapshot.get("orden_id") or "-"
    tipo_orden = snapshot.get("tipo_orden") or "-"
    estado_plan = snapshot.get("estado_plan") or "-"
    fecha_emision = _fmt_fecha(snapshot.get("fecha_emision"))
    hora_inicio = _fmt_fecha(snapshot.get("hora_inicio"))
    hora_fin = _fmt_fecha(snapshot.get("hora_fin"))

    barras_totales, piezas_totales, eficiencia, tabla_mat = calcular_kpis(data_nesteo)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    W, H = letter

    margin = 0.55 * inch
    y = H - margin

    def new_page():
        nonlocal y
        c.showPage()
        y = H - margin

    def header(title_left, subtitle_left=None):
        nonlocal y
        band_h = 0.55 * inch
        c.setFillColor(colors.HexColor("#0B1220"))
        c.rect(0, H - band_h, W, band_h, fill=1, stroke=0)

        c.setFillColor(colors.white)
        c.setFont(FONT_BOLD, 12)
        c.drawString(margin, H - 0.38 * inch, title_left)

        if subtitle_left:
            c.setFont(FONT_REG, 8.5)
            c.setFillColor(colors.HexColor("#CBD5E1"))
            c.drawString(margin, H - 0.52 * inch, subtitle_left)

        y = H - band_h - 0.35 * inch

    def section_title(txt):
        nonlocal y
        c.setFont(FONT_BOLD, 11)
        c.setFillColor(THEME["ink"])
        c.drawString(margin, y, txt)
        y -= 0.28 * inch
        c.setStrokeColor(THEME["line"])
        c.setLineWidth(1)
        c.line(margin, y, W - margin, y)
        y -= 0.22 * inch

    def ensure_space(min_need_inch: float):
        nonlocal y
        if y < margin + (min_need_inch * inch):
            new_page()

    def draw_table(headers, rows, col_ws, row_h=0.24 * inch):
        nonlocal y

        ensure_space(0.55)
        c.setFont(FONT_BOLD, 9)
        c.setFillColor(THEME["muted"])

        x = margin
        for h, w in zip(headers, col_ws):
            c.drawString(x, y, str(h))
            x += w

        y -= 0.14 * inch
        c.setStrokeColor(THEME["line"])
        c.setLineWidth(1)
        c.line(margin, y, W - margin, y)
        y -= 0.18 * inch

        c.setFont(FONT_REG, 9)
        alt = False
        for r in rows:
            if y < margin + 0.8 * inch:
                new_page()
                c.setFont(FONT_BOLD, 9)
                c.setFillColor(THEME["muted"])
                x = margin
                for h, w in zip(headers, col_ws):
                    c.drawString(x, y, str(h))
                    x += w
                y -= 0.14 * inch
                c.setStrokeColor(THEME["line"])
                c.line(margin, y, W - margin, y)
                y -= 0.18 * inch
                c.setFont(FONT_REG, 9)

            if alt:
                c.setFillColor(colors.HexColor("#F8FAFC"))
                c.rect(margin, y - 0.06 * inch, W - 2 * margin, row_h, fill=1, stroke=0)
            alt = not alt

            c.setFillColor(THEME["ink"])
            x = margin
            for cell, w in zip(r, col_ws):
                txt = _fit_text(c, cell, w - 6, FONT_REG, 9)
                c.drawString(x, y, txt)
                x += w

            y -= row_h

        y -= 0.18 * inch

    def draw_kpi_cards():
        nonlocal y
        card_h = 0.55 * inch
        gap = 0.22 * inch
        card_w = (W - 2 * margin - 2 * gap) / 3

        def card(x, label, value):
            c.setFillColor(THEME["card_bg"])
            _round_rect(c, x, y - card_h, card_w, card_h, r=10, fill=1, stroke=0)
            c.setStrokeColor(THEME["card_border"])
            c.setLineWidth(1)
            _round_rect(c, x, y - card_h, card_w, card_h, r=10, fill=0, stroke=1)

            c.setFillColor(THEME["ink"])
            c.setFont(FONT_BOLD, 14)
            c.drawString(x + 0.18 * inch, y - 0.28 * inch, str(value))

            c.setFillColor(THEME["muted"])
            c.setFont(FONT_BOLD, 8)
            c.drawString(x + 0.18 * inch, y - 0.46 * inch, label.upper())

        x0 = margin
        card(x0, "Barras Totales", barras_totales)
        card(x0 + card_w + gap, "Piezas a Cortar", piezas_totales)
        card(x0 + 2 * (card_w + gap), "Eficiencia Global", f"{eficiencia:.1f}%")

        y -= (card_h + 0.28 * inch)

    def draw_meta():
        nonlocal y
        rows = [
            ("Orden", orden_id),
            ("Tipo", tipo_orden),
            ("Operador", operador),
            ("Bahia", bahia),
            ("Estado", estado_plan),
            ("Emision", fecha_emision),
            ("Inicio", hora_inicio),
            ("Fin", hora_fin),
        ]

        col_w = (W - 2 * margin) / 4
        box_h = 0.55 * inch

        for row_i in range(0, len(rows), 4):
            current = rows[row_i:row_i + 4]
            x = margin
            for label, value in current:
                c.setFillColor(colors.white)
                c.setStrokeColor(THEME["line"])
                c.roundRect(x, y - box_h, col_w - 0.08 * inch, box_h, 8, fill=1, stroke=1)

                c.setFillColor(THEME["muted"])
                c.setFont(FONT_BOLD, 7.5)
                c.drawString(x + 0.12 * inch, y - 0.18 * inch, label.upper())

                c.setFillColor(THEME["ink"])
                c.setFont(FONT_BOLD, 9)
                c.drawString(x + 0.12 * inch, y - 0.38 * inch, _fit_text(c, value, col_w - 0.28 * inch, FONT_BOLD, 9))
                x += col_w

            y -= (box_h + 0.10 * inch)

        y -= 0.08 * inch

    def table_material_requerido():
        nonlocal y

        c.setFont(FONT_BOLD, 9)
        c.setFillColor(THEME["muted"])
        c.drawString(margin, y, "MATERIAL")
        c.drawString(margin + 3.9 * inch, y, "LARGO DE BARRA")
        c.drawRightString(W - margin, y, "CANTIDAD")
        y -= 0.14 * inch
        c.setStrokeColor(THEME["line"])
        c.line(margin, y, W - margin, y)
        y -= 0.18 * inch

        c.setFont(FONT_REG, 9)
        alt = False
        row_h = 0.22 * inch
        for row in tabla_mat:
            if y < margin + 1.4 * inch:
                new_page()
                header("ARGA METALS - LISTA DE SURTIDO (PICKING)", f"Orden: {orden_id}")
                section_title("MATERIAL REQUERIDO")

                c.setFont(FONT_BOLD, 9)
                c.setFillColor(THEME["muted"])
                c.drawString(margin, y, "MATERIAL")
                c.drawString(margin + 3.9 * inch, y, "LARGO DE BARRA")
                c.drawRightString(W - margin, y, "CANTIDAD")
                y -= 0.14 * inch
                c.setStrokeColor(THEME["line"])
                c.line(margin, y, W - margin, y)
                y -= 0.18 * inch

            if alt:
                c.setFillColor(colors.HexColor("#F8FAFC"))
                c.rect(margin, y - 0.06 * inch, W - 2 * margin, row_h, fill=1, stroke=0)
            alt = not alt

            c.setFillColor(THEME["ink"])
            mat_txt = str(row["material"])
            if str(row.get("source", "")).upper() == "REMANENTE" and row.get("rem_id"):
                mat_txt += f'  |  ID: {row["rem_id"]}'
            c.drawString(margin, y, mat_txt)
            c.setFillColor(THEME["muted"])
            c.drawString(margin + 3.9 * inch, y, f'{row["stock"]}"')
            c.setFillColor(THEME["ink"])
            c.drawRightString(W - margin, y, f'{row["cantidad"]} Pzas')
            y -= row_h

        y -= 0.20 * inch

    def firmas():
        nonlocal y
        section_title("CONTROL DE ENTREGA DE MATERIAL")

        c.setFont(FONT_BOLD, 9)
        c.setFillColor(THEME["muted"])
        c.drawString(margin, y, "FIRMA ALMACEN (Entrega Material)")
        c.drawString(margin + 3.6 * inch, y, "FIRMA OPERADOR (Recibe y Verifica)")
        y -= 0.40 * inch

        c.setStrokeColor(THEME["line"])
        c.setLineWidth(1.2)
        c.line(margin, y, margin + 2.9 * inch, y)
        c.line(margin + 3.6 * inch, y, W - margin, y)
        y -= 0.30 * inch

        c.setFont(FONT_REG, 8.5)
        c.setFillColor(THEME["muted"])
        c.drawString(margin, y, f"Orden: {orden_id}")
        y -= 0.10 * inch

    def material_header(mat):
        nonlocal y
        ensure_space(2.05)

        c.setFont(FONT_BOLD, 16)
        c.setFillColor(THEME["ink"])
        c.drawString(margin, y, f"MATERIAL: {mat}")
        y -= 0.18 * inch
        c.setStrokeColor(THEME["line"])
        c.line(margin, y, W - margin, y)
        y -= 0.30 * inch

    def draw_bar(mat, idx, b):
        nonlocal y

        source = (b.get("source") or "STOCK").upper()
        stock = float(b.get("largo_stock", 0.0) or 0.0)
        util = stock - (RECORTE_EXTREMO * 2)

        rem_show = float(b.get("remanente_show", b.get("remanente_calc", 0.0)) or 0.0)
        ocupado = util - rem_show
        ef = (ocupado / util * 100.0) if util > 0 else 0.0

        min_need = 1.35 * inch
        if y < margin + min_need:
            new_page()
            material_header(mat)

        c.setFont(FONT_BOLD, 11)
        c.setFillColor(THEME["muted"])

        if source == "REMANENTE_RESULTANTE":
            barra_origen = b.get("barra_origen", idx)
            hdr_left = f'REMANENTE RESULTANTE (de BARRA #{barra_origen}) (Largo: {stock:.2f}")'
        else:
            hdr_left = f'BARRA #{idx} (Largo: {stock:.2f}")'
            if source == "REMANENTE" and b.get("rem_id"):
                hdr_left += f"  |  REM: {b.get('rem_id')}"

        right_col_w = 2.35 * inch
        gap = 0.18 * inch
        x_right = W - margin
        x_right_col_left = x_right - right_col_w

        left_max_w = max(0.0, x_right_col_left - gap - margin)
        hdr_left_fit = _fit_text(c, hdr_left, left_max_w, FONT_BOLD, 11)
        c.drawString(margin, y, hdr_left_fit)

        c.setFont(FONT_BOLD, 10)
        if source == "REMANENTE_RESULTANTE":
            rid = b.get("remanente_id") or b.get("rem_id")
            right_txt = f'Rem: {rem_show:.2f}"'
            if rid:
                right_txt += f" | ID: {rid}"
            c.setFillColor(THEME["hatch"])
            right_txt_fit = _fit_text(c, right_txt, right_col_w, FONT_BOLD, 10)
            c.drawRightString(x_right, y, right_txt_fit)

            c.setFillColor(THEME["muted"])
            c.drawRightString(x_right_col_left - gap, y, "Eficiencia: N/A")
        else:
            if source == "REMANENTE":
                rid = b.get("rem_id")
                right_txt = f'Rem: {rem_show:.2f}"'
                if rid:
                    right_txt += f" | ID: {rid}"
                c.setFillColor(THEME["hatch"])
            else:
                right_txt = f'Desp: {rem_show:.2f}"'
                c.setFillColor(THEME["hatch"])

            right_txt_fit = _fit_text(c, right_txt, right_col_w, FONT_BOLD, 10)
            c.drawRightString(x_right, y, right_txt_fit)

            c.setFillColor(THEME["muted"])
            ef_txt = f"Eficiencia: {ef:.1f}%"
            c.drawRightString(x_right_col_left - gap, y, ef_txt)

        y -= 0.30 * inch

        track_x = margin
        track_w = W - 2 * margin
        track_h = 0.35 * inch
        track_bottom = y - track_h

        c.setStrokeColor(colors.HexColor("#CBD5E1"))
        c.setLineWidth(1)
        c.setFillColor(colors.white)
        c.rect(track_x, track_bottom, track_w, track_h, fill=1, stroke=1)

        trim_w = track_w * (RECORTE_EXTREMO / stock) if stock > 0 else 0.0
        usable_x0 = track_x + trim_w
        usable_x1 = track_x + track_w - trim_w
        usable_w = max(0.0, usable_x1 - usable_x0)

        c.setFillColor(THEME["trim_red"])
        c.rect(track_x, track_bottom, trim_w, track_h, fill=1, stroke=0)
        c.rect(track_x + track_w - trim_w, track_bottom, trim_w, track_h, fill=1, stroke=0)

        if source == "REMANENTE_RESULTANTE":
            rem_len = max(0.0, min(rem_show, util if util > 0 else rem_show))
            cons_len = max(0.0, (util - rem_len)) if util > 0 else 0.0

            cons_w = (usable_w * (cons_len / util)) if util > 0 else 0.0
            rem_w = max(0.0, usable_w - cons_w)

            if cons_w > 1:
                c.setFillColor(colors.white)
                c.setStrokeColor(colors.HexColor("#FDBA74"))
                c.rect(usable_x0, track_bottom, cons_w, track_h, fill=1, stroke=1)
                _hatch_rect(c, usable_x0, track_bottom, cons_w, track_h, step=6)

            if rem_w > 1:
                c.setFillColor(THEME["trim_red"])
                c.setStrokeColor(THEME["trim_red"])
                c.rect(usable_x0 + cons_w, track_bottom, rem_w, track_h, fill=1, stroke=1)

            y = track_bottom - (0.30 * inch)
            return

        labels_for_cortes, legend_rows = _build_bar_legend_and_labels(b)

        cursor_x = usable_x0
        end_x = usable_x1
        scale = (usable_w / util) if util > 0 else 0.0

        for i_p, p in enumerate(b.get("cortes", [])):
            consumo = float(p.get("largo", 0.0) or 0.0) + KERF
            pw = max(0.0, consumo * scale)

            remaining = end_x - cursor_x
            if remaining <= 0:
                break

            pw_draw = min(pw, remaining)

            if source == "REMANENTE":
                c.setFillColor(colors.HexColor("#16A34A"))
            else:
                c.setFillColor(THEME["blue"])

            if pw_draw > 0:
                c.rect(cursor_x, track_bottom, pw_draw, track_h, fill=1, stroke=1)

            lab = labels_for_cortes[i_p]
            if pw_draw >= 18:
                c.setFillColor(colors.white)
                c.setFont(FONT_BOLD, 10)
                c.drawCentredString(cursor_x + pw_draw / 2, track_bottom + track_h / 2 - 4, f"({lab})")

            cursor_x += pw_draw

        rem_w = end_x - cursor_x
        if rem_w > 1:
            c.setFillColor(THEME["orange_light"])
            c.setStrokeColor(colors.HexColor("#FDBA74"))
            c.setLineWidth(1)
            c.rect(cursor_x, track_bottom, rem_w, track_h, fill=1, stroke=1)
            _hatch_rect(c, cursor_x, track_bottom, rem_w, track_h, step=6)

        y = track_bottom - (0.16 * inch)

        c.setFont(FONT_REG, 9)
        c.setFillColor(THEME["ink"])

        for row in legend_rows:
            if y < margin + 0.9 * inch:
                new_page()
                material_header(mat)
                c.setFont(FONT_BOLD, 11)
                c.setFillColor(THEME["muted"])
                c.drawString(margin, y, f'BARRA #{idx} (Largo: {stock:.2f}") (continuacion)')
                y -= 0.25 * inch

            label = row["label"]
            txt = f'({label})  {row["text"]}'
            qty = row["qty"]

            txt_fit = _fit_text(c, txt, (W - 2 * margin - 1.2 * inch), FONT_REG, 9)

            c.setFillColor(THEME["ink"])
            c.setFont(FONT_REG, 9)
            c.drawString(margin, y, txt_fit)

            c.setFillColor(THEME["muted"])
            c.setFont(FONT_BOLD, 9)
            c.drawRightString(W - margin, y, f"QTY {qty}")

            y -= 0.20 * inch

        y -= 0.22 * inch

    header("GRUPO ARGA | PRODUCCION", "Sistema: Lista de Largos")
    c.setFont(FONT_BOLD, 18)
    c.setFillColor(THEME["ink"])
    c.drawString(margin, y, "ARGA METALS - LISTA DE SURTIDO (PICKING)")
    y -= 0.35 * inch

    draw_meta()
    draw_kpi_cards()

    section_title("MATERIAL REQUERIDO")
    table_material_requerido()
    firmas()

    new_page()
    header("GRUPO ARGA | PRODUCCION", "Sistema: Lista de Largos")
    c.setFont(FONT_BOLD, 18)
    c.setFillColor(THEME["ink"])
    c.drawString(margin, y, "MAPAS DE CORTE - DETALLE")
    y -= 0.26 * inch
    c.setFont(FONT_REG, 10)
    c.setFillColor(THEME["muted"])
    c.drawString(margin, y, f"Orden: {orden_id}  |  Operador: {operador}  |  Bahia: {bahia}")
    y -= 0.30 * inch

    for mat in list(data_nesteo.keys()):
        barras = data_nesteo[mat]
        stock_barras = [b for b in barras if str(b.get("source") or "STOCK").upper() == "STOCK"]

        material_header(mat)
        for i, b in enumerate(stock_barras, start=1):
            draw_bar(mat, i, b)

    new_page()
    header("GRUPO ARGA | PRODUCCION", "Sistema: Lista de Largos")
    c.setFont(FONT_BOLD, 18)
    c.setFillColor(THEME["ink"])
    c.drawString(margin, y, "REMANENTES")
    y -= 0.26 * inch
    c.setFont(FONT_REG, 10)
    c.setFillColor(THEME["muted"])
    c.drawString(margin, y, "Agrupados por material")
    y -= 0.22 * inch
    c.setStrokeColor(THEME["line"])
    c.line(margin, y, W - margin, y)
    y -= 0.30 * inch

    for mat in list(data_nesteo.keys()):
        barras = data_nesteo[mat]
        rem_barras = [b for b in barras if str(b.get("source") or "").upper() == "REMANENTE"]
        if not rem_barras:
            continue

        material_header(mat)

        ensure_space(0.9)
        c.setFont(FONT_BOLD, 12)
        c.setFillColor(THEME["muted"])
        c.drawString(margin, y, "REMANENTES (USADOS EN ESTE NESTEO)")
        y -= 0.18 * inch
        c.setStrokeColor(THEME["line"])
        c.line(margin, y, W - margin, y)
        y -= 0.25 * inch

        rows = []
        for b in rem_barras:
            rid = b.get("rem_id") or ""
            largo_ini = float(b.get("largo_stock", 0.0) or 0.0)
            desp = float(b.get("remanente_show", b.get("remanente_calc", 0.0)) or 0.0)
            cortes_n = len(b.get("cortes") or [])
            rows.append([
                str(rid),
                f'{largo_ini:.2f}"',
                f'{desp:.2f}"',
                f"{cortes_n}"
            ])

        draw_table(
            headers=["ID", "Largo Inicial", "Desp Final", "Cortes"],
            rows=rows,
            col_ws=[1.55 * inch, 1.35 * inch, 1.25 * inch, 0.85 * inch]
        )

    for mat in list(data_nesteo.keys()):
        rr = (remanentes_resultantes or {}).get(str(mat), [])
        if not rr:
            continue

        material_header(mat)

        ensure_space(0.9)
        c.setFont(FONT_BOLD, 12)
        c.setFillColor(THEME["muted"])
        c.drawString(margin, y, "REMANENTES RESULTANTES (NUEVOS)")
        y -= 0.18 * inch
        c.setStrokeColor(THEME["line"])
        c.line(margin, y, W - margin, y)
        y -= 0.25 * inch

        rows = []
        for r in rr:
            origen = f'BARRA #{int(r.get("barra_origen") or 0)}'
            stock = float(r.get("largo_stock", 0.0) or 0.0)
            rem = float(r.get("remanente_show", 0.0) or 0.0)
            rid = r.get("rem_id") or ""
            rows.append([
                origen,
                f'{stock:.2f}"',
                f'{rem:.2f}"',
                str(rid)
            ])

        draw_table(
            headers=["Origen", "Largo", "Rem", "ID"],
            rows=rows,
            col_ws=[1.25 * inch, 1.15 * inch, 1.05 * inch, 2.55 * inch]
        )

    c.save()
    buffer.seek(0)
    return buffer