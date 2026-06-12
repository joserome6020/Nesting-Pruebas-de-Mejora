"""Motor gráfico Qt (QGraphicsView) para visor de nesting — geometría 1:1 en mm."""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QTransform,
)
from interface.qt.curve_refine import refine_enabled, refine_ring

from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsItemGroup,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

# —— Paleta premium (dark CAD) ——
COLOR_BG = QColor("#0B1220")
COLOR_PLATE_TOP = QColor("#2A3444")
COLOR_PLATE_BOTTOM = QColor("#141C28")
COLOR_PLATE_EDGE = QColor("#64748B")
COLOR_PIECE_FILL = QColor("#DDE4EC")
COLOR_PIECE_HOLE = QColor("#0B1220")
COLOR_PIECE_EDGE = QColor("#475569")
COLOR_HOLE_EDGE = QColor("#1E293B")
COLOR_PIECE_SEL = QColor("#3B82F6")
COLOR_PIECE_SEL_EDGE = QColor("#93C5FD")
COLOR_PIECE_HOVER = QColor("#E8EFF8")
COLOR_COMP_EDGE = QColor("#FF1A1A")  # rojo intenso — compensación plasma
COLOR_COMP_BASE = QColor(255, 30, 30, 230)
COLOR_MARK = QColor("#0047AB")  # azul rey — alto contraste sobre piezas claras
COLOR_MARK_TAT = QColor("#FACC15")
COLOR_REF_FILL = QColor("#C4B87A")
COLOR_REM_EDGE = QColor("#94A3B8")
COLOR_GUILL = QColor("#EF4444")
COLOR_DIM = QColor(255, 255, 255, 200)
COLOR_TABLE_HDR = QColor("#94A3B8")
COLOR_TABLE_ROW_A = QColor("#F8FAFC")
COLOR_TABLE_ROW_B = QColor("#E2E8F0")
COLOR_TABLE_EDGE = QColor("#CBD5E1")
COLOR_TABLE_TEXT = QColor("#0F172A")

Z_PLATE = 0
Z_COMP = 6
Z_PIECE = 10
Z_MARK = 20
Z_LABEL = 30
Z_DIM = 40
Z_TABLE = 50
Z_HUD = 60


def _ring_to_polygon(ring) -> QPolygonF:
    poly = QPolygonF()
    for pt in ring or []:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            poly.append(QPointF(float(pt[0]), float(pt[1])))
        elif isinstance(pt, dict):
            x, y = pt.get("x", pt.get("X")), pt.get("y", pt.get("Y"))
            if x is not None and y is not None:
                poly.append(QPointF(float(x), float(y)))
    return poly


def _ring_to_path(ring, *, refine: bool | None = None) -> QPainterPath:
    use_refine = refine_enabled() if refine is None else refine
    pts = refine_ring(ring) if use_refine else _ring_points_list(ring)
    path = QPainterPath()
    if len(pts) < 3:
        o = _ring_to_polygon(ring)
        if o.size() >= 3:
            path.addPolygon(o)
        return path
    path.moveTo(pts[0][0], pts[0][1])
    for x, y in pts[1:]:
        path.lineTo(x, y)
    path.closeSubpath()
    return path


def _ring_points_list(ring) -> list[tuple[float, float]]:
    out = []
    for pt in ring or []:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            out.append((float(pt[0]), float(pt[1])))
        elif isinstance(pt, dict):
            x, y = pt.get("x", pt.get("X")), pt.get("y", pt.get("Y"))
            if x is not None and y is not None:
                out.append((float(x), float(y)))
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def polygon_path(outer, holes=None, *, refine: bool | None = None) -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    outer_path = _ring_to_path(outer, refine=refine)
    if not outer_path.isEmpty():
        path.addPath(outer_path)
    for hole in holes or []:
        hole_path = _ring_to_path(hole, refine=refine)
        if not hole_path.isEmpty():
            path.addPath(hole_path)
    return path


def piece_path_from_polys(poligonos, *, refine: bool | None = None) -> QPainterPath:
    """Un solo path con OddEvenFill: exterior + barrenos fieles al DXF."""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    for poli in poligonos or []:
        sub = _ring_to_path(poli, refine=refine)
        if not sub.isEmpty():
            path.addPath(sub)
    return path


def piece_display_path(pieza: dict, poligonos, *, refine: bool | None = None) -> QPainterPath:
    """Dibuja exactamente los polígonos colocados por el motor (coords de placa)."""
    return piece_path_from_polys(poligonos, refine=refine)


def _add_marks(scene: QGraphicsScene, lineas, *, es_tat: bool, bucket: list | None = None):
    col = COLOR_MARK_TAT if es_tat else COLOR_MARK
    pen = QPen(col, 2.0 if not es_tat else 1.25)
    pen.setCosmetic(True)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    for linea in lineas or []:
        if not linea or len(linea) < 2:
            continue
        for a, b in zip(linea, linea[1:]):
            item = scene.addLine(
                float(a[0]), float(a[1]), float(b[0]), float(b[1]), pen
            )
            item.setZValue(Z_MARK)
            if bucket is not None:
                bucket.append(item)


class UprightTextItem(QGraphicsSimpleTextItem):
    """Texto legible con vista Y invertida (coords CAD: Y hacia arriba)."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)

    def set_font(self, size: float, bold: bool = False):
        f = QFont("Segoe UI", int(max(7, size)))
        f.setBold(bold)
        self.setFont(f)


class TableCellTextItem(QGraphicsSimpleTextItem):
    """Texto de tabla: escala con el zoom y queda centrado en la celda (mm)."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setTransform(QTransform.fromScale(1.0, -1.0))

    def set_font_for_row(self, row_h_mm: float, *, bold: bool = False):
        pt = max(4.5, min(13.0, row_h_mm * 0.42))
        f = QFont("Segoe UI", int(round(pt)))
        f.setBold(bold)
        self.setFont(f)


def _place_text_centered_in_cell(
    txt: QGraphicsSimpleTextItem,
    x: float,
    y: float,
    cw: float,
    rh: float,
) -> None:
    br = txt.boundingRect()
    tw = br.width()
    th = abs(br.height())
    txt.setPos(x + (cw - tw) * 0.5, y + (rh + th) * 0.5)


class NestingGraphicsView(QGraphicsView):
    """Viewport con antialiasing, zoom suave y fondo CAD."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.TextAntialiasing
        )
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setOptimizationFlags(
            QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing
            | QGraphicsView.OptimizationFlag.DontSavePainterState
        )
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setBackgroundBrush(QBrush(COLOR_BG))
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Coordenadas CAD: origen abajo-izq, Y hacia arriba (igual que DXF/mm del motor).
        self.scale(1.0, -1.0)

    def wheelEvent(self, event):
        if event.angleDelta().y() == 0:
            return
        factor = 1.12 if event.angleDelta().y() > 0 else 1.0 / 1.12
        self.scale(factor, factor)
        event.accept()


@dataclass
class NestingDrawParams:
    hoja: dict
    clave: str
    app: object
    selected_indices: set = field(default_factory=set)
    drag_preview: bool = False


def _piece_style(nom: str, idx: int, ring_i: int, selected: bool, compensada: bool):
    es_rem = nom.startswith("REMANENTE__")
    es_ref = nom.startswith("REF__")
    es_guill = nom.startswith("RETAZO_GUILLOTINA__")
    es_tat = nom.startswith("TATUAJE__")

    if es_rem:
        return None, QPen(COLOR_REM_EDGE, 1.0, Qt.PenStyle.DashLine), Qt.BrushStyle.NoBrush
    if es_ref:
        return QBrush(COLOR_REF_FILL), QPen(QColor("#1E293B"), 1.0), Qt.BrushStyle.SolidPattern
    if es_guill:
        return None, QPen(COLOR_GUILL, 2.0, Qt.PenStyle.DashDotLine), Qt.BrushStyle.NoBrush
    if es_tat:
        return None, QPen(Qt.PenStyle.NoPen), Qt.BrushStyle.NoBrush

    if selected:
        fill = QBrush(COLOR_PIECE_SEL)
        edge = QPen(COLOR_PIECE_SEL_EDGE, 1.6)
    else:
        fill = QBrush(COLOR_PIECE_FILL if ring_i == 0 else COLOR_PIECE_HOLE)
        edge = QPen(QColor("#FF2222") if compensada else COLOR_PIECE_EDGE, 1.5 if compensada and ring_i == 0 else 0.65)
    return fill, edge, Qt.BrushStyle.SolidPattern


def _make_comp_band_items(poly_comp, poly_base):
    """Crea items de contorno compensado; el caller los agrupa con la pieza para arrastre."""
    items = []

    def _add_poly(g, pen):
        if g is None or getattr(g, "is_empty", True):
            return
        geoms = list(g.geoms) if hasattr(g, "geoms") else [g]
        for geom in geoms:
            if not hasattr(geom, "exterior"):
                continue
            ext = list(geom.exterior.coords)
            if len(ext) < 3:
                continue
            item = QGraphicsPathItem(polygon_path(ext))
            item.setPen(pen)
            item.setBrush(Qt.BrushStyle.NoBrush)
            item.setZValue(Z_COMP)
            items.append(item)
            for hole in getattr(geom, "interiors", []):
                ring = list(hole.coords)
                if len(ring) >= 3:
                    hi = QGraphicsPathItem(polygon_path(ring))
                    hi.setPen(pen)
                    hi.setBrush(Qt.BrushStyle.NoBrush)
                    hi.setZValue(Z_COMP)
                    items.append(hi)

    try:
        pen_comp = QPen(COLOR_COMP_EDGE, 1.35)
        pen_comp.setCosmetic(True)
        pen_base = QPen(COLOR_COMP_BASE, 1.1, Qt.PenStyle.DashLine)
        pen_base.setCosmetic(True)
        _add_poly(poly_comp, pen_comp)
        _add_poly(poly_base, pen_base)
    except Exception:
        pass
    return items


def _add_dimension(scene, x1, y1, x2, y2, label, ox=0.0, oy=0.0):
    pen = QPen(COLOR_DIM, 1.0)
    pen.setCosmetic(True)
    scene.addLine(x1, y1, x1 + ox * 0.5, y1 + oy * 0.5, pen).setZValue(Z_DIM)
    scene.addLine(x2, y2, x2 + ox * 0.5, y2 + oy * 0.5, pen).setZValue(Z_DIM)
  # línea de cota
    scene.addLine(x1 + ox * 0.35, y1 + oy * 0.35, x2 + ox * 0.35, y2 + oy * 0.35, pen).setZValue(Z_DIM)
    txt = UprightTextItem(label)
    txt.set_font(9)
    txt.setBrush(QBrush(COLOR_DIM))
    txt.setPos((x1 + x2) * 0.5 + ox * 0.85, (y1 + y2) * 0.5 + oy * 0.85)
    txt.setZValue(Z_DIM)
    scene.addItem(txt)


def _add_table_impl(scene, hoja, resumen, dims_nom, w_mm, h_mm, job_cell: str):
    rows = []
    for nom, data in sorted(resumen.items(), key=lambda kv: kv[1]["id"]):
        dim = dims_nom.get(nom, {"L": 0.0, "W": 0.0, "plasma": False})
        L_in = float(dim.get("L", 0.0) or 0.0)
        W_in = float(dim.get("W", 0.0) or 0.0)
        item = nom if len(nom) <= 34 else (nom[:31] + "…")
        if dim.get("plasma"):
            item = f"{item} (PLASMA)"
        rows.append((data["id"], job_cell, item, f"{L_in:.2f}", f"{W_in:.2f}", int(data["qty"])))

    nrows = len(rows)
    gap_mm = max(10.0, min(32.0, h_mm * 0.022))
    frac_tabla = min(0.42, max(0.24, 0.075 * float(nrows + 1)))
    tbl_h = h_mm * frac_tabla
    es_rtz = bool(hoja.get("es_retazo"))
    if es_rtz or w_mm < 920.0:
        tbl_w = 1750.0
        tbl_h = max(tbl_h, 340.0)
    else:
        tbl_w = w_mm * 0.90
    tbl_x0 = (w_mm - tbl_w) * 0.5
    y_tbl_top = -gap_mm
    y_tbl_bottom = y_tbl_top - tbl_h

    col_labels = ["ID", "JOB", "ITEM", "L (in)", "W (in)", "Cant."]
    col_fracs = [0.07, 0.19, 0.34, 0.12, 0.12, 0.16]
    col_w = [tbl_w * f for f in col_fracs]
    row_h = tbl_h / max(1, nrows + 1)

    def cell_rect(col, row):
        x = tbl_x0 + sum(col_w[:col])
        y = y_tbl_bottom + row * row_h
        return x, y, col_w[col], row_h

    edge_pen = QPen(COLOR_TABLE_EDGE, 0.5)
    edge_pen.setCosmetic(True)

    for c, lbl in enumerate(col_labels):
        x, y, cw, rh = cell_rect(c, nrows)
        rect = QGraphicsRectItem(x, y, cw, rh)
        rect.setBrush(QBrush(COLOR_TABLE_HDR))
        rect.setPen(edge_pen)
        rect.setZValue(Z_TABLE)
        scene.addItem(rect)
        t = TableCellTextItem(lbl)
        t.set_font_for_row(rh, bold=True)
        t.setBrush(QBrush(COLOR_TABLE_TEXT))
        _place_text_centered_in_cell(t, x, y, cw, rh)
        t.setZValue(Z_TABLE + 1)
        scene.addItem(t)

    for ri, row in enumerate(rows):
        r = nrows - 1 - ri  # ID 1 arriba, mayor ID abajo
        bg = COLOR_TABLE_ROW_A if r % 2 else COLOR_TABLE_ROW_B
        for c, val in enumerate(row):
            x, y, cw, rh = cell_rect(c, r)
            rect = QGraphicsRectItem(x, y, cw, rh)
            rect.setBrush(QBrush(bg))
            rect.setPen(edge_pen)
            rect.setZValue(Z_TABLE)
            scene.addItem(rect)
            txt = TableCellTextItem(str(val))
            txt.set_font_for_row(rh)
            txt.setBrush(QBrush(COLOR_TABLE_TEXT))
            _place_text_centered_in_cell(txt, x, y, cw, rh)
            txt.setZValue(Z_TABLE + 1)
            scene.addItem(txt)

    return tbl_x0, tbl_w, y_tbl_bottom


def populate_nesting_scene(
    scene: QGraphicsScene,
    params: NestingDrawParams,
    *,
    poly_from_pieza=None,
) -> dict:
    """Reconstruye la escena. Devuelve metadatos de encuadre (resumen, dims, tabla)."""
    scene.clear()
    hoja = params.hoja
    w_mm = float(hoja["placa_w"])
    h_mm = float(hoja["placa_h"])
    selected = set(params.selected_indices or [])
    drag_preview = bool(params.drag_preview)

    # Placa con gradiente
    plate = QGraphicsRectItem(0, 0, w_mm, h_mm)
    grad = QLinearGradient(0, 0, 0, h_mm)
    grad.setColorAt(0.0, COLOR_PLATE_TOP)
    grad.setColorAt(1.0, COLOR_PLATE_BOTTOM)
    plate.setBrush(QBrush(grad))
    plate.setPen(QPen(COLOR_PLATE_EDGE, 2.5))
    plate.setZValue(Z_PLATE)
    scene.addItem(plate)

    if hoja.get("poly_borde_retazo"):
        try:
            borde = hoja["poly_borde_retazo"]
            if borde and len(borde) >= 3:
                rtz = QGraphicsPathItem(polygon_path(borde))
                rtz.setPen(QPen(COLOR_REM_EDGE, 1.2, Qt.PenStyle.DashLine))
                rtz.setBrush(Qt.BrushStyle.NoBrush)
                rtz.setZValue(Z_PLATE + 1)
                scene.addItem(rtz)
        except Exception:
            pass

    resumen = {}
    dims_nom = {}
    rem_data = None
    offset_comp = float(hoja.get("plasma_offset_mm_manual", 0.0) or 0.0)
    piece_gfx: dict[int, list] = {}

    for idx_pieza, p in enumerate(hoja.get("piezas", [])):
        gfx_items: list = []
        nom = p.get("nombre", "DXF")
        es_rem = nom.startswith("REMANENTE__")
        es_ref = nom.startswith("REF__")
        es_guill = nom.startswith("RETAZO_GUILLOTINA__")
        es_tat = nom.startswith("TATUAJE__")
        compensada = bool(p.get("plasma_compensada_manual"))

        if not (es_rem or es_ref or es_guill or es_tat):
            if nom not in resumen:
                resumen[nom] = {"id": len(resumen) + 1, "qty": 0}
            resumen[nom]["qty"] += 1
            pols = p.get("poligonos") or []
            if pols and pols[0] and len(pols[0]) >= 2:
                xs = [t[0] for t in pols[0]]
                ys = [t[1] for t in pols[0]]
                dx_mm = max(xs) - min(xs)
                dy_mm = max(ys) - min(ys)
                L_in = max(dx_mm, dy_mm) / 25.4
                W_in = min(dx_mm, dy_mm) / 25.4
                actual = dims_nom.get(nom, {"L": 0.0, "W": 0.0, "plasma": False})
                actual["L"] = max(float(actual["L"]), float(L_in))
                actual["W"] = max(float(actual["W"]), float(W_in))
                actual["plasma"] = bool(actual["plasma"]) or compensada
                dims_nom[nom] = actual

        pols = p.get("poligonos") or []
        if pols and not es_tat:
            fill, pen, brush_style = _piece_style(
                nom, idx_pieza, 0, idx_pieza in selected, compensada
            )
            combined = piece_display_path(p, pols)
            if not combined.isEmpty():
                item = QGraphicsPathItem(combined)
                if fill is not None:
                    item.setBrush(fill)
                item.setPen(pen)
                if brush_style == Qt.BrushStyle.NoBrush:
                    item.setBrush(Qt.BrushStyle.NoBrush)
                item.setZValue(Z_PIECE + (1 if idx_pieza in selected else 0))
                scene.addItem(item)
                gfx_items.append(item)

        if (
            compensada
            and not drag_preview
            and not (es_rem or es_ref or es_guill or es_tat)
            and offset_comp > 1e-6
            and callable(poly_from_pieza)
        ):
            try:
                poly_comp = poly_from_pieza(p)
                if poly_comp is not None and not poly_comp.is_empty:
                    poly_base = poly_comp.buffer(-offset_comp, join_style=1, quad_segs=16)
                    for comp_it in _make_comp_band_items(poly_comp, poly_base):
                        scene.addItem(comp_it)
                        gfx_items.append(comp_it)
            except Exception:
                pass

        _add_marks(scene, p.get("marcas"), es_tat=es_tat, bucket=gfx_items)

        pols = p.get("poligonos") or []
        if pols and not drag_preview:
            v = pols[0][:-1] if len(pols[0]) > 1 else pols[0]
            if v:
                cx = sum(pt[0] for pt in v) / len(v)
                cy = sum(pt[1] for pt in v) / len(v)
                if es_rem:
                    minx_r = min(pt[0] for pt in v)
                    maxx_r = max(pt[0] for pt in v)
                    miny_r = min(pt[1] for pt in v)
                    maxy_r = max(pt[1] for pt in v)
                    rem_data = (minx_r, miny_r, maxx_r - minx_r, maxy_r - miny_r)
                    id_rem = nom.split("__")[1] if "__" in nom else nom
                    t = UprightTextItem(id_rem)
                    t.set_font(8, bold=True)
                    t.setBrush(QBrush(QColor("#0F172A")))
                    t.setPos(cx - 8, cy)
                    t.setZValue(Z_LABEL)
                    scene.addItem(t)
                    gfx_items.append(t)
                elif not es_ref and not es_guill and not es_tat and nom in resumen:
                    t = UprightTextItem(str(resumen[nom]["id"]))
                    t.set_font(9, bold=True)
                    t.setBrush(QBrush(QColor("#0F172A")))
                    t.setPos(cx - 4, cy)
                    t.setZValue(Z_LABEL)
                    scene.addItem(t)
                    gfx_items.append(t)

        if gfx_items:
            if len(gfx_items) == 1:
                piece_gfx[idx_pieza] = gfx_items
            else:
                grp = QGraphicsItemGroup()
                scene.addItem(grp)
                for gi in gfx_items:
                    grp.addToGroup(gi)
                piece_gfx[idx_pieza] = [grp]

    meta = {
        "resumen": resumen,
        "dims_nom": dims_nom,
        "w_mm": w_mm,
        "h_mm": h_mm,
        "piece_gfx": piece_gfx,
    }

    if not drag_preview:
        _add_dimension(scene, 0, h_mm, w_mm, h_mm, f'Largo: {w_mm / 25.4:.1f}"', oy=90)
        _add_dimension(scene, w_mm, 0, w_mm, h_mm, f'Ancho: {h_mm / 25.4:.1f}"', ox=90)
        if rem_data:
            rx, ry, rw, rh = rem_data
            if rx > 0.1:
                _add_dimension(scene, 0, 0, rx, 0, f"Uso: {rx / 25.4:.1f}\"", oy=-90)
                _add_dimension(scene, rx, 0, w_mm, 0, f"Sob: {rw / 25.4:.1f}\"", oy=-90)
            elif ry > 0.1:
                _add_dimension(scene, 0, 0, 0, ry, f"Uso: {ry / 25.4:.1f}\"", ox=-90)
                _add_dimension(scene, 0, ry, 0, h_mm, f"Sob: {rh / 25.4:.1f}\"", ox=-90)

        job_raw = ""
        if params.app is not None:
            job_raw = str(getattr(params.app, "job_activo", "") or "").strip() or "-"
        if len(job_raw) > 30:
            job_raw = job_raw[:27] + "…"
        tbl_meta = _add_table_impl(scene, hoja, resumen, dims_nom, w_mm, h_mm, job_raw)
        meta["tbl_x0"], meta["tbl_w"], meta["y_tbl_bottom"] = tbl_meta

    disable_scene_mouse_picking(scene)
    return meta


def compute_fit_rect(hoja: dict, meta: dict, view_w: int, view_h: int, preserve: tuple | None = None):
    """Calcula rect de escena para fit inicial o preserva vista."""
    if preserve:
        return preserve
    w_mm = meta["w_mm"]
    h_mm = meta["h_mm"]
    padding_x = max(150, w_mm * 0.1)
    padding_y = max(150, h_mm * 0.1)
    x_lo, x_hi = -padding_x, w_mm + padding_x
    y_tbl_bottom = meta.get("y_tbl_bottom")
    tbl_w = meta.get("tbl_w") or 0
    tbl_x0 = meta.get("tbl_x0") or 0
    es_rtz = bool(hoja.get("es_retazo")) or w_mm < 920.0

    if y_tbl_bottom is not None and tbl_w > w_mm - 1:
        spill = (tbl_w - w_mm) * 0.5 + 55.0
        if es_rtz:
            spill += 130.0
        x_lo = min(x_lo, -spill)
        x_hi = max(x_hi, w_mm + spill)

    if y_tbl_bottom is not None:
        ymin = min(-padding_y - 300, y_tbl_bottom - max(72.0, h_mm * 0.06))
    else:
        ymin = -padding_y - 200
    ymax = h_mm + padding_y

    if es_rtz and y_tbl_bottom is not None:
        c_x0 = min(0.0, tbl_x0) - 40.0
        c_x1 = max(w_mm, tbl_x0 + tbl_w) + 40.0
        c_y0 = min(0.0, y_tbl_bottom) - 28.0
        c_y1 = max(h_mm, 0.0) + 48.0
        c_w = max(1.0, c_x1 - c_x0) * 1.05
        c_h = max(1.0, c_y1 - c_y0) * 1.08
        cx = 0.5 * (c_x0 + c_x1)
        cy = 0.5 * (c_y0 + c_y1)
        if view_w > 1 and view_h > 1:
            target_ratio = view_w / view_h
            content_ratio = c_w / c_h
            if content_ratio < target_ratio:
                c_w = c_h * target_ratio
            else:
                c_h = c_w / target_ratio
        x_lo = cx - 0.5 * c_w
        x_hi = cx + 0.5 * c_w
        ymin = cy - 0.5 * c_h
        ymax = cy + 0.5 * c_h

    return x_lo, x_hi, ymin, ymax


def disable_scene_mouse_picking(scene: QGraphicsScene):
    """La vista maneja ratón/teclado; los ítems no deben robar eventos."""
    for item in scene.items():
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, False)
