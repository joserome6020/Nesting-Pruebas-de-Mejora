"""Motor gráfico Qt (QGraphicsView) para visor de nesting — geometría 1:1 en mm."""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, Qt, QRectF
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QTransform,
)
from interface.qt.curve_refine import refine_enabled, refine_ring
from interface.material_colors import (
    CAD_VIEW_BG,
    NEST_SEL_EDGE,
    NEST_SEL_FILL,
    es_contexto_cobre,
    paleta_pieza_nesting,
)
from reporte_pdf_nesting import _resolve_piece_meta

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
COLOR_CU_FILL = QColor("#B87333")
COLOR_CU_FILL_LIGHT = QColor("#D4956A")
COLOR_CU_HOLE = QColor("#6B4423")
COLOR_CU_EDGE = QColor("#4A2F1A")
COLOR_CU_SEL = QColor("#E8A55C")
COLOR_CU_SEL_EDGE = QColor("#FDE68A")
COLOR_HOLE_EDGE = QColor("#1E293B")
COLOR_PIECE_SEL = QColor("#3B82F6")
COLOR_PIECE_SEL_EDGE = QColor("#93C5FD")
COLOR_PIECE_HOVER = QColor("#E8EFF8")
COLOR_COMP_EDGE = QColor("#FF1A1A")  # rojo intenso — compensación plasma
COLOR_COMP_BASE = QColor(255, 30, 30, 230)
COLOR_MARK = QColor("#0047AB")  # azul rey — alto contraste sobre piezas claras
COLOR_MARK_TAT = QColor("#FACC15")
COLOR_CONSTRUCT_RTZ = QColor("#FF2222")  # líneas constructivas en overlays RTZ
COLOR_REF_FILL = QColor(96, 165, 250, 150)
COLOR_REF_EDGE = QColor("#1D4ED8")
COLOR_RTZ_PREVIEW_FILL = QColor(56, 189, 248, 120)
COLOR_RTZ_PREVIEW_EDGE = QColor("#0369A1")
COLOR_RTZ_REF_FILL = QColor(189, 176, 126, 220)  # beige RTZ (misma familia que PDF)
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

PIECE_ID_FONT_PT = 9
RTZ_LABEL_FONT_PT = 9  # ~15% menor que 11pt base
REM_LABEL_FONT_PT = 8
COLOR_RTZ_BADGE_BG = QColor("#061428")   # azul muy oscuro, opaco (no se mezcla con piezas RTZ)
COLOR_RTZ_BADGE_EDGE = QColor("#1E4976")
COLOR_RTZ_BADGE_TEXT = QColor("#F8FAFC")
RTZ_BADGE_RADIUS = 7.0
RTZ_BADGE_PAD_X = 8.5
RTZ_BADGE_PAD_Y = 4.0


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
    """Usa polígonos ya en memoria (refrescados al cargar nest, no en cada clic de placa)."""
    return piece_path_from_polys(poligonos, refine=refine)


def _es_virtual_nombre(nom: str) -> bool:
    n = str(nom or "")
    return (
        n.startswith("REF__")
        or n.startswith("TATUAJE__")
        or n.startswith("RETAZO_GUILLOTINA__")
        or n.startswith("CU_CORTE__")
        or n.startswith("REMANENTE__")
    )


def _es_guillotina_rtz_cu(nom: str) -> bool:
    n = str(nom or "")
    return n.startswith("RETAZO_GUILLOTINA__") and "RTZCU" in n.upper()


def _debe_mostrar_etiqueta_rtz(hoja) -> bool:
    """
    Etiquetas RTZ (badge) solo en placa madre.
    En mini-nest / retazo no se dibujan: estorban al reacomodar piezas.
    """
    if not isinstance(hoja, dict):
        return False
    if hoja.get("es_retazo"):
        return False
    if hoja.get("modo_largos_cu"):
        return bool(hoja.get("cu_rtz_activo"))
    if hoja.get("poly_borde_retazo"):
        return False
    pid = str(hoja.get("placa_id") or "").strip().upper()
    if pid.startswith("RTZ"):
        return False
    return True


def _es_vista_mini_retazo(hoja) -> bool:
    """True si la hoja que se está visualizando es un RTZ/mini-nest (no placa madre)."""
    return not _debe_mostrar_etiqueta_rtz(hoja)


def _marcas_para_display(nom: str, marcas) -> list:
    if _es_virtual_nombre(nom):
        return []
    return list(marcas or [])


def _centro_zona_rtz(hoja: dict, rtz_id: str) -> tuple[float, float] | None:
    """Centro del rectángulo guillotina del RTZ (mejor ancla que el dummy 2×2 mm)."""
    for pref in (f"RETAZO_GUILLOTINA__{rtz_id}", f"RTZCU_ZONA__{rtz_id}"):
        for p in (hoja or {}).get("piezas") or []:
            if str(p.get("nombre", "") or "") != pref:
                continue
            pols = p.get("poligonos") or []
            if not pols or not pols[0]:
                continue
            xs = [t[0] for t in pols[0]]
            ys = [t[1] for t in pols[0]]
            if not xs or not ys:
                continue
            return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    return None


def _rtz_label_anchor(
    hoja: dict, nom: str, pols, w_mm: float, h_mm: float
) -> tuple[float, float]:
    """Misma ancla que generar_texto_vectorial (centro guillotina / dummy TATUAJE)."""
    rid = nom.split("__", 1)[1] if "__" in nom else nom
    anchor_rtz = _centro_zona_rtz(hoja, rid)
    if anchor_rtz is not None:
        return anchor_rtz
    if pols and pols[0]:
        xs = [t[0] for t in pols[0]]
        ys = [t[1] for t in pols[0]]
        return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    return w_mm / 2.0, h_mm / 2.0


def _add_marks(
    scene: QGraphicsScene,
    lineas,
    *,
    es_tat: bool,
    bucket: list | None = None,
    construct_rtz: bool = False,
):
    if not lineas:
        return
    if construct_rtz:
        col = COLOR_CONSTRUCT_RTZ
        pen_w = 5.5
    else:
        col = COLOR_MARK_TAT if es_tat else COLOR_MARK
        pen_w = 1.25 if es_tat else 2.0
    pen = QPen(col, pen_w)
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

    def center_at(self, cx: float, cy: float) -> None:
        br = self.boundingRect()
        self.setPos(cx - br.width() * 0.5, cy - br.height() * 0.5)


class OutlinedUprightTextItem(UprightTextItem):
    """ID de pieza: relleno negro y contorno blanco para contraste sobre la placa."""

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        path = QPainterPath()
        path.addText(0, 0, self.font(), self.text())
        stroke = QPen(QColor("#FFFFFF"), 2.0)
        stroke.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        stroke.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.strokePath(path, stroke)
        painter.fillPath(path, QBrush(QColor("#0F172A")))


class SceneFixedLabel(OutlinedUprightTextItem):
    """Etiqueta ID en pieza: tamaño fijo al zoom, agrupada en piece_gfx para arrastre."""


class RtzBadgeLabel(QGraphicsItem):
    """Nombre RTZ: chip oscuro redondeado, texto blanco, tamaño fijo al hacer zoom."""

    def __init__(self, text: str = ""):
        super().__init__()
        self._text = str(text or "")
        self._font = QFont("Segoe UI", int(RTZ_LABEL_FONT_PT))
        self._font.setBold(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)

    def boundingRect(self) -> QRectF:
        fm = QFontMetrics(self._font)
        tw = float(fm.horizontalAdvance(self._text))
        th = float(fm.height())
        return QRectF(
            -tw * 0.5 - RTZ_BADGE_PAD_X,
            -th * 0.5 - RTZ_BADGE_PAD_Y,
            tw + RTZ_BADGE_PAD_X * 2.0,
            th + RTZ_BADGE_PAD_Y * 2.0,
        )

    def center_at(self, cx: float, cy: float) -> None:
        self.setPos(cx, cy)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        rect = self.boundingRect()
        path = QPainterPath()
        path.addRoundedRect(rect, RTZ_BADGE_RADIUS, RTZ_BADGE_RADIUS)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(COLOR_RTZ_BADGE_BG)
        painter.drawPath(path)
        edge = QPen(COLOR_RTZ_BADGE_EDGE, 1.0)
        painter.setPen(edge)
        painter.drawPath(path)
        painter.setFont(self._font)
        painter.setPen(COLOR_RTZ_BADGE_TEXT)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)


class TableCellTextItem(QGraphicsSimpleTextItem):
    """Texto de tabla: escala con el zoom y queda centrado en la celda (mm)."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setTransform(QTransform.fromScale(1.0, -1.0))

    def set_font_for_row(self, row_h_mm: float, *, bold: bool = False):
        pt = max(11.0, min(26.0, row_h_mm * 0.62))
        f = QFont("Segoe UI", int(round(pt)))
        f.setBold(bold)
        self.setFont(f)


def _piece_label_center(pols) -> tuple[float, float] | None:
    """Centro visual de la pieza (bbox del contorno colocado en placa)."""
    path = piece_path_from_polys(pols)
    if path.isEmpty():
        return None
    c = path.boundingRect().center()
    return c.x(), c.y()


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
    """Viewport con antialiasing, zoom suave, pan (arrastre) y fondo CAD."""

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
        self._is_panning = False
        self._pan_last = None

    def fit_nest_rect(self, rect):
        """Ajuste igual que el nest normal: Y invertida + fitInView."""
        from PySide6.QtCore import QRectF

        if rect is None:
            return
        if not isinstance(rect, QRectF):
            try:
                x0, x1, y0, y1 = rect
                rect = QRectF(
                    float(x0),
                    float(min(y0, y1)),
                    float(abs(x1 - x0)),
                    float(abs(y1 - y0)),
                )
            except Exception:
                return
        if rect.width() <= 1 or rect.height() <= 1:
            return
        self.resetTransform()
        self.scale(1.0, -1.0)
        self.setSceneRect(rect.adjusted(-50, -50, 50, 50))
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event):
        if event.angleDelta().y() == 0:
            return
        factor = 1.12 if event.angleDelta().y() > 0 else 1.0 / 1.12
        self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = True
            self._pan_last = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_panning and self._pan_last is not None:
            cur = event.position()
            dx = float(cur.x() - self._pan_last.x())
            dy = float(cur.y() - self._pan_last.y())
            self._pan_last = cur
            p0 = self.mapToScene(0, 0)
            p1 = self.mapToScene(int(round(dx)), int(round(dy)))
            self.translate(p1.x() - p0.x(), p1.y() - p0.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = False
            self._pan_last = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class NestLabGraphicsView(NestingGraphicsView):
    """Vista LAB: zoom con rueda + arrastre con botón izquierdo/medio."""

    def mousePressEvent(self, event):
        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self._is_panning = True
            self._pan_last = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self._is_panning = False
            self._pan_last = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)


@dataclass
class NestingDrawParams:
    hoja: dict
    clave: str
    app: object
    selected_indices: set = field(default_factory=set)
    drag_preview: bool = False


def _is_copper_context(pieza: dict | None, hoja: dict | None, clave: str = "") -> bool:
    return es_contexto_cobre(pieza, hoja, clave)


def _translate_poligonos_mm(poligonos, gx: float, gy: float) -> list:
    out: list = []
    for pol_coords in poligonos or []:
        ring = []
        for pt in pol_coords or []:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                ring.append((float(pt[0]) + gx, float(pt[1]) + gy))
            else:
                ring.append(pt)
        if ring:
            out.append(ring)
    return out


def _translate_marcas_mm(marcas, gx: float, gy: float) -> list:
    out: list = []
    for linea in marcas or []:
        ring = []
        for pt in linea or []:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                ring.append((float(pt[0]) + gx, float(pt[1]) + gy))
            else:
                ring.append(pt)
        if len(ring) >= 2:
            out.append(ring)
    return out


def _embed_rtz_previews_on_mother(scene, hoja: dict, params: "NestingDrawParams") -> None:
    """Dibuja piezas reales del mini-nest RTZ sobre la placa madre (fallback visual)."""
    if hoja.get("es_retazo") or hoja.get("modo_largos_cu"):
        return
    app = getattr(params, "app", None)
    clave = str(getattr(params, "clave", "") or "")
    if not app or not clave:
        return
    grp = (getattr(app, "resultados_nesting", None) or {}).get(clave) or {}
    hojas = grp.get("hojas") or []
    if not isinstance(hojas, list):
        return

    idx_madre = -1
    for i, h in enumerate(hojas):
        if h is hoja:
            idx_madre = i
            break
    if idx_madre < 0:
        return

    ref_noms = {
        str(p.get("nombre") or "")
        for p in (hoja.get("piezas") or [])
        if str(p.get("nombre") or "").startswith("REF__")
    }

    j = idx_madre + 1
    while j < len(hojas) and (hojas[j] or {}).get("es_retazo"):
        rtz = hojas[j] or {}
        gx = float(rtz.get("global_x") or 0.0)
        gy = float(rtz.get("global_y") or 0.0)
        for p in rtz.get("piezas") or []:
            nom = str(p.get("nombre") or "")
            if _es_virtual_nombre(nom):
                continue
            pols = _translate_poligonos_mm(p.get("poligonos") or [], gx, gy)
            combined = piece_path_from_polys(pols, refine=False)
            if combined.isEmpty():
                continue
            ref_nom = f"REF__{nom}"
            z = Z_PIECE + (5 if ref_nom in ref_noms else 4)
            item = QGraphicsPathItem(combined)
            item.setBrush(QBrush(COLOR_RTZ_PREVIEW_FILL))
            item.setPen(QPen(COLOR_RTZ_PREVIEW_EDGE, 1.0))
            item.setZValue(z)
            scene.addItem(item)
        j += 1


def _piece_style(
    nom: str,
    idx: int,
    ring_i: int,
    selected: bool,
    compensada: bool,
    *,
    pieza: dict | None = None,
    hoja: dict | None = None,
    clave: str = "",
):
    es_rem = nom.startswith("REMANENTE__")
    es_ref = nom.startswith("REF__")
    es_guill = nom.startswith("RETAZO_GUILLOTINA__") or nom.startswith("CU_CORTE__")
    es_guill_rtz_cu = _es_guillotina_rtz_cu(nom)
    es_tat = nom.startswith("TATUAJE__")

    if es_rem:
        return None, QPen(COLOR_REM_EDGE, 1.0, Qt.PenStyle.DashLine), Qt.BrushStyle.NoBrush
    if es_ref:
        return QBrush(COLOR_REF_FILL), QPen(QColor("#1E293B"), 1.0), Qt.BrushStyle.SolidPattern
    if es_guill:
        return None, QPen(COLOR_GUILL, 4.5, Qt.PenStyle.DashDotLine), Qt.BrushStyle.NoBrush
    if es_tat:
        return None, QPen(Qt.PenStyle.NoPen), Qt.BrushStyle.NoBrush

    pal = paleta_pieza_nesting(pieza, hoja, clave)
    if compensada:
        edge_color = COLOR_COMP_EDGE
    else:
        edge_color = QColor(pal.edge)

    if selected and not compensada:
        fill = QBrush(QColor(NEST_SEL_FILL))
        edge = QPen(QColor(NEST_SEL_EDGE), 1.6)
    elif selected and compensada:
        # Selección azul + contorno rojo plasma bien visible
        fill = QBrush(QColor(NEST_SEL_FILL))
        edge = QPen(COLOR_COMP_EDGE, 2.8)
        edge.setCosmetic(True)
    elif ring_i == 0:
        fill = QBrush(QColor(pal.fill))
        if compensada:
            edge = QPen(COLOR_COMP_EDGE, 2.6)
            edge.setCosmetic(True)
        else:
            edge = QPen(edge_color, 0.75)
    else:
        fill = QBrush(QColor(CAD_VIEW_BG))
        if compensada:
            edge = QPen(COLOR_COMP_EDGE, 1.8)
            edge.setCosmetic(True)
        else:
            edge = QPen(edge_color, 0.65)
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


def _add_dimension(scene, x1, y1, x2, y2, label, ox=0.0, oy=0.0, dim_labels=None):
    pen = QPen(COLOR_DIM, 1.0)
    pen.setCosmetic(True)
    scene.addLine(x1, y1, x1 + ox * 0.5, y1 + oy * 0.5, pen).setZValue(Z_DIM)
    scene.addLine(x2, y2, x2 + ox * 0.5, y2 + oy * 0.5, pen).setZValue(Z_DIM)
    scene.addLine(x1 + ox * 0.35, y1 + oy * 0.35, x2 + ox * 0.35, y2 + oy * 0.35, pen).setZValue(Z_DIM)
    if dim_labels is not None:
        dim_labels.append(
            {
                "text": label,
                "line_x": (x1 + x2) * 0.5 + ox * 0.35,
                "line_y": (y1 + y2) * 0.5 + oy * 0.35,
                "out_x": 1.0 if ox > 0 else (-1.0 if ox < 0 else 0.0),
                "out_y": 1.0 if oy > 0 else (-1.0 if oy < 0 else 0.0),
            }
        )


def _add_table_impl(scene, hoja, resumen, dims_nom, w_mm, h_mm, job_cell: str):
    rows = []
    for nom, data in sorted(resumen.items(), key=lambda kv: kv[1]["id"]):
        dim = dims_nom.get(nom, {"L": 0.0, "W": 0.0, "plasma": False})
        L_in = float(dim.get("L", 0.0) or 0.0)
        W_in = float(dim.get("W", 0.0) or 0.0)
        item = nom if len(nom) <= 34 else (nom[:31] + "…")
        if dim.get("plasma"):
            item = f"{item} (PLASMA)"
        job_val = str(data.get("job") or job_cell or "-").strip()
        if len(job_val) > 30:
            job_val = job_val[:27] + "…"
        rows.append(
            (data["id"], job_val, item, f"{L_in:.2f}", f"{W_in:.2f}", int(data["qty"]))
        )

    nrows = len(rows)
    gap_mm = max(28.0, min(48.0, h_mm * 0.035))
    frac_tabla = min(0.36, max(0.20, 0.065 * float(nrows + 1)))
    tbl_h = h_mm * frac_tabla
    es_rtz = bool(hoja.get("es_retazo"))
    modo_cu = bool(hoja.get("modo_largos_cu"))
    if es_rtz or w_mm < 920.0:
        tbl_w = 1750.0
        tbl_h = max(tbl_h, 340.0)
    else:
        tbl_w = w_mm * 0.90
    if modo_cu or h_mm < 220.0 or w_mm > h_mm * 6.0:
        tbl_w = max(tbl_w, 2400.0)
        tbl_h = max(420.0, 58.0 * float(nrows + 1))
    tbl_x0 = (w_mm - tbl_w) * 0.5
    y_tbl_top = -gap_mm
    row_h = max(54.0, tbl_h / max(1, nrows + 1))
    tbl_h = row_h * (nrows + 1)
    y_tbl_bottom = y_tbl_top - tbl_h

    col_labels = ["ID", "JOB", "ITEM", "L (in)", "W (in)", "Cant."]
    col_fracs = [0.07, 0.19, 0.34, 0.12, 0.12, 0.16]
    col_w = [tbl_w * f for f in col_fracs]

    def cell_rect(col, row):
        x = tbl_x0 + sum(col_w[:col])
        y = y_tbl_bottom + row * row_h
        return x, y, col_w[col], row_h

    edge_pen = QPen(COLOR_TABLE_EDGE, 1.2)
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
                rtz.setPen(QPen(COLOR_REM_EDGE, 2.8, Qt.PenStyle.DashLine))
                rtz.setBrush(Qt.BrushStyle.NoBrush)
                rtz.setZValue(Z_PLATE + 1)
                scene.addItem(rtz)
        except Exception:
            pass

    resumen = {}
    dims_nom = {}
    rem_data = None
    scene_fixed_labels: list = []
    offset_comp = float(hoja.get("plasma_offset_mm_manual", 0.0) or 0.0)
    piece_gfx: dict[int, list] = {}
    mostrar_etiquetas_rtz = _debe_mostrar_etiqueta_rtz(hoja)
    es_rtz_hoja = _es_vista_mini_retazo(hoja)

    meta_por_ruta = {}
    job_fallback = "-"
    if params.app is not None:
        meta_por_ruta = getattr(params.app, "meta_pdf_por_ruta", {}) or {}
        job_activo = str(getattr(params.app, "job_activo", "") or "").strip()
        if job_activo and not job_activo.upper().startswith("SWO"):
            job_fallback = job_activo

    for idx_pieza, p in enumerate(hoja.get("piezas", [])):
        gfx_items: list = []
        nom = p.get("nombre", "DXF")
        es_rem = nom.startswith("REMANENTE__")
        es_ref = nom.startswith("REF__")
        es_guill = nom.startswith("RETAZO_GUILLOTINA__") or nom.startswith("CU_CORTE__")
        es_guill_rtz_cu = _es_guillotina_rtz_cu(nom)
        es_tat = nom.startswith("TATUAJE__")
        if es_tat and not mostrar_etiquetas_rtz:
            continue
        compensada = bool(p.get("plasma_compensada_manual"))

        if not (es_rem or es_ref or es_guill or es_tat or es_guill_rtz_cu):
            piece_meta = _resolve_piece_meta(p, meta_por_ruta, job_fallback)
            if nom not in resumen:
                resumen[nom] = {
                    "id": len(resumen) + 1,
                    "qty": 0,
                    "job": piece_meta["job"],
                }
            resumen[nom]["qty"] += 1
            pols = p.get("poligonos") or []
            if pols and pols[0] and len(pols[0]) >= 2:
                xs = [t[0] for t in pols[0]]
                ys = [t[1] for t in pols[0]]
                dx_mm = max(xs) - min(xs)
                dy_mm = max(ys) - min(ys)
                # El polígono del nest es el perfil SIN compensar. La pieza que
                # de verdad sale de la mesa crece `offset` por lado, y es la
                # medida que PARTS reporta; sin esto la tabla decía 42.48 donde
                # PARTS decía 42.51 y no empataban.
                off_pieza = float(
                    p.get("plasma_offset_mm_manual") or offset_comp or 0.0
                )
                if compensada and off_pieza <= 0.0:
                    # La pieza llega marcada como compensada pero sin el mm
                    # del desfase (se pierde en el empaquetado). Misma regla
                    # que usa el export: compute_plasma_offset_mm(calibre).
                    try:
                        from modules.plasma_compensator import compute_plasma_offset_mm

                        cal = (
                            p.get("calibre")
                            or hoja.get("placa_cal")
                            or hoja.get("thickness")
                            or 0.25
                        )
                        off_pieza = float(compute_plasma_offset_mm(float(cal)))
                    except Exception:
                        off_pieza = 0.0125 * 25.4
                if compensada and off_pieza > 0.0:
                    dx_mm += 2.0 * off_pieza
                    dy_mm += 2.0 * off_pieza
                L_in = max(dx_mm, dy_mm) / 25.4
                W_in = min(dx_mm, dy_mm) / 25.4
                actual = dims_nom.get(nom, {"L": 0.0, "W": 0.0, "plasma": False})
                actual["L"] = max(float(actual["L"]), float(L_in))
                actual["W"] = max(float(actual["W"]), float(W_in))
                actual["plasma"] = bool(actual["plasma"]) or compensada
                dims_nom[nom] = actual

        pols = p.get("poligonos") or []
        if pols and not es_tat:
            if es_ref or es_guill_rtz_cu:
                combined = piece_path_from_polys(pols, refine=False)
                if not combined.isEmpty():
                    item = QGraphicsPathItem(combined)
                    if es_guill_rtz_cu:
                        item.setBrush(QBrush(COLOR_RTZ_REF_FILL))
                        pen = QPen(COLOR_GUILL, 4.5, Qt.PenStyle.DashDotLine)
                    else:
                        item.setBrush(QBrush(COLOR_REF_FILL))
                        pen = QPen(QColor("#1E293B"), 1.2)
                    item.setPen(pen)
                    item.setZValue(Z_PIECE + 2)
                    scene.addItem(item)
                    gfx_items.append(item)
            else:
                fill, pen, brush_style = _piece_style(
                    nom,
                    idx_pieza,
                    0,
                    idx_pieza in selected,
                    compensada,
                    pieza=p,
                    hoja=hoja,
                    clave=params.clave,
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
                    if es_guill:
                        item.setZValue(Z_PIECE + 5)
                    scene.addItem(item)
                    gfx_items.append(item)

        if (
            compensada
            and not drag_preview
            and not (es_rem or es_ref or es_guill or es_tat or es_guill_rtz_cu)
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

        if es_ref:
            pass
        elif es_tat and mostrar_etiquetas_rtz:
            rid = nom.split("__", 1)[1] if "__" in nom else nom
            cx, cy = _rtz_label_anchor(hoja, nom, pols, w_mm, h_mm)
            badge = RtzBadgeLabel(rid)
            badge.center_at(cx, cy)
            badge.setZValue(Z_LABEL + 3)
            scene.addItem(badge)
            scene_fixed_labels.append(badge)
        else:
            marcas_disp = _marcas_para_display(nom, p.get("marcas"))
            if marcas_disp:
                _add_marks(
                    scene,
                    marcas_disp,
                    es_tat=False,
                    bucket=gfx_items,
                    construct_rtz=es_rtz_hoja,
                )

        pols = p.get("poligonos") or []
        if pols and not drag_preview:
            anchor = _piece_label_center(pols)
            if anchor is not None:
                cx, cy = anchor
                if es_rem:
                    minx_r = min(pt[0] for pt in (pols[0][:-1] if len(pols[0]) > 1 else pols[0]))
                    maxx_r = max(pt[0] for pt in (pols[0][:-1] if len(pols[0]) > 1 else pols[0]))
                    miny_r = min(pt[1] for pt in (pols[0][:-1] if len(pols[0]) > 1 else pols[0]))
                    maxy_r = max(pt[1] for pt in (pols[0][:-1] if len(pols[0]) > 1 else pols[0]))
                    rem_data = (minx_r, miny_r, maxx_r - minx_r, maxy_r - miny_r)
                    id_rem = nom.split("__")[1] if "__" in nom else nom
                    t = SceneFixedLabel(id_rem)
                    t.set_font(REM_LABEL_FONT_PT, bold=True)
                    t.center_at(cx, cy)
                    t.setZValue(Z_LABEL)
                    scene.addItem(t)
                    gfx_items.append(t)
                elif not es_ref and not es_guill and not es_tat and nom in resumen:
                    t = SceneFixedLabel(str(resumen[nom]["id"]))
                    t.set_font(PIECE_ID_FONT_PT, bold=True)
                    t.center_at(cx, cy)
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

    if not drag_preview:
        _embed_rtz_previews_on_mother(scene, hoja, params)

    meta = {
        "resumen": resumen,
        "dims_nom": dims_nom,
        "w_mm": w_mm,
        "h_mm": h_mm,
        "piece_gfx": piece_gfx,
        "scene_fixed_labels": scene_fixed_labels,
    }

    if not drag_preview:
        dim_labels: list[dict] = []
        _add_dimension(scene, 0, h_mm, w_mm, h_mm, f'Largo: {w_mm / 25.4:.1f}"', oy=90, dim_labels=dim_labels)
        _add_dimension(scene, w_mm, 0, w_mm, h_mm, f'Ancho: {h_mm / 25.4:.1f}"', ox=90, dim_labels=dim_labels)
        if rem_data:
            rx, ry, rw, rh = rem_data
            if rx > 0.1:
                _add_dimension(scene, 0, 0, rx, 0, f"Uso: {rx / 25.4:.1f}\"", oy=-90, dim_labels=dim_labels)
                _add_dimension(scene, rx, 0, w_mm, 0, f"Sob: {rw / 25.4:.1f}\"", oy=-90, dim_labels=dim_labels)
            elif ry > 0.1:
                _add_dimension(scene, 0, 0, 0, ry, f"Uso: {ry / 25.4:.1f}\"", ox=-90, dim_labels=dim_labels)
                _add_dimension(scene, 0, ry, 0, h_mm, f"Sob: {rh / 25.4:.1f}\"", ox=-90, dim_labels=dim_labels)
        meta["dim_labels"] = dim_labels

        job_raw = ""
        if params.app is not None:
            job_activo = str(getattr(params.app, "job_activo", "") or "").strip()
            if job_activo and not job_activo.upper().startswith("SWO"):
                job_raw = job_activo
        if not job_raw and resumen:
            jobs_tabla = sorted(
                {str(v.get("job") or "").strip() for v in resumen.values() if str(v.get("job") or "").strip()}
            )
            if len(jobs_tabla) == 1:
                job_raw = jobs_tabla[0]
            elif len(jobs_tabla) > 1:
                job_raw = "VARIOS"
        job_raw = job_raw or "-"
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
