"""Ítems QGraphics para cotas estilo AutoCAD — todo en unidades del dibujo (1:1)."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsItemGroup,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsSimpleTextItem,
)

Z_DIM = 20.0
Z_DIM_PREVIEW = 19.0
Z_SNAP = 30.0

CAD_MARK = "#0047AB"


def dim_text_height(span: float, factor_conversion: float) -> float:
    """Altura del texto de cota en unidades del DXF (DIMTXT ~0.12\")."""
    return max(float(factor_conversion) * 0.12, span * 0.018)


def dim_line_width(span: float, factor_conversion: float, *, preview: bool = False) -> float:
    base = max(float(factor_conversion) * 0.008, span * 0.00085)
    return base * (0.85 if preview else 1.0)


def dim_arrow_len(span: float, factor_conversion: float) -> float:
    return max(float(factor_conversion) * 0.10, span * 0.014)


class _ScreenFixedItem(QGraphicsItem):
    def __init__(self, sx: float, sy: float):
        super().__init__()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setPos(float(sx), float(sy))
        self.setZValue(Z_SNAP)


class ScreenDot(_ScreenFixedItem):
    def __init__(self, sx, sy, radius_px=4, fill="#FACC15", stroke="#713F12", stroke_w=0.8):
        super().__init__(sx, sy)
        self._r = float(radius_px)
        self._fill = QColor(fill)
        self._stroke = QPen(QColor(stroke), stroke_w)

    def boundingRect(self) -> QRectF:
        r = self._r + 1
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._stroke)
        painter.setBrush(QBrush(self._fill))
        painter.drawEllipse(QPointF(0, 0), self._r, self._r)


class ScreenCross(_ScreenFixedItem):
    def __init__(self, sx, sy, half_px=5, color="#A3E635", lw=1.4):
        super().__init__(sx, sy)
        self._h = float(half_px)
        self._pen = QPen(QColor(color), lw)
        self._pen.setCapStyle(Qt.PenCapStyle.RoundCap)

    def boundingRect(self) -> QRectF:
        h = self._h + 1
        return QRectF(-h, -h, 2 * h, 2 * h)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setPen(self._pen)
        painter.drawLine(QPointF(-self._h, -self._h), QPointF(self._h, self._h))
        painter.drawLine(QPointF(-self._h, self._h), QPointF(self._h, -self._h))


class ScreenSquare(_ScreenFixedItem):
    def __init__(self, sx, sy, half_px=4, fill="#38BDF8", stroke="#0369A1"):
        super().__init__(sx, sy)
        self._h = float(half_px)
        self._fill = QBrush(QColor(fill))
        self._stroke = QPen(QColor(stroke), 1.0)

    def boundingRect(self) -> QRectF:
        h = self._h + 1
        return QRectF(-h, -h, 2 * h, 2 * h)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._stroke)
        painter.setBrush(self._fill)
        painter.drawRect(QRectF(-self._h, -self._h, 2 * self._h, 2 * self._h))


def _color(preview: bool) -> QColor:
    return QColor("#94A3B8" if preview else "#E5E7EB")


def _text_color(preview: bool) -> QColor:
    return QColor("#CBD5E1" if preview else "#F1F5F9")


def _pen_scene(preview: bool, span: float, factor_conversion: float, *, dashed: bool = True) -> QPen:
    p = QPen(_color(preview))
    p.setWidthF(dim_line_width(span, factor_conversion, preview=preview))
    p.setStyle(Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine)
    p.setCapStyle(Qt.PenCapStyle.FlatCap)
    return p


def _line(x1, y1, x2, y2, preview, span, factor_conversion, dashed=True) -> QGraphicsLineItem:
    ln = QGraphicsLineItem(x1, y1, x2, y2)
    ln.setPen(_pen_scene(preview, span, factor_conversion, dashed=dashed))
    return ln


def _arrow_path(
    p1, p2, preview, span, factor_conversion, *, bidirectional: bool = True
) -> QGraphicsPathItem:
    x1, y1 = float(p1[0]), float(p1[1])
    x2, y2 = float(p2[0]), float(p2[1])
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    path = QPainterPath()
    item = QGraphicsPathItem(path)
    if L < 1e-12:
        item.setPen(_pen_scene(preview, span, factor_conversion, dashed=False))
        return item
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux
    ah = dim_arrow_len(span, factor_conversion)
    aw = ah * 0.30
    path.moveTo(QPointF(x1, y1))
    path.lineTo(QPointF(x2, y2))

    def _head(tip_x, tip_y, back_x, back_y):
        path.moveTo(QPointF(tip_x, tip_y))
        path.lineTo(QPointF(back_x + nx * aw, back_y + ny * aw))
        path.moveTo(QPointF(tip_x, tip_y))
        path.lineTo(QPointF(back_x - nx * aw, back_y - ny * aw))

    if bidirectional:
        _head(x1, y1, x1 + ux * ah, y1 + uy * ah)
        _head(x2, y2, x2 - ux * ah, y2 - uy * ah)
    else:
        _head(x2, y2, x2 - ux * ah, y2 - uy * ah)

    item.setPath(path)
    item.setPen(_pen_scene(preview, span, factor_conversion, dashed=False))
    return item


def _dim_text_label(
    text: str,
    tx: float,
    ty: float,
    angle_deg: float,
    preview: bool,
    span: float,
    factor_conversion: float,
) -> QGraphicsItemGroup:
    """Texto de cota escalado a unidades del dibujo (1:1 con el DXF)."""
    h_target = dim_text_height(span, factor_conversion)
    txt = QGraphicsSimpleTextItem(text)
    f = QFont("Consolas")
    f.setBold(True)
    f.setPointSize(72)
    txt.setFont(f)
    txt.setBrush(QBrush(_text_color(preview)))
    br = txt.boundingRect()
    scale = h_target / max(br.height(), 1e-9)
    txt.setPos(-br.width() * 0.5, -br.height() * 0.5)

    label = QGraphicsItemGroup()
    label.addToGroup(txt)
    xf = QTransform()
    xf.rotate(angle_deg)
    xf.scale(scale, -scale)
    label.setTransform(xf)
    label.setPos(tx, ty)
    return label


def make_center_mark(cx, cy, span, factor_conversion, preview=False) -> list:
    s = max(span * 0.007, factor_conversion * 0.025)
    pen = _pen_scene(preview, span, factor_conversion, dashed=preview)
    h = QGraphicsLineItem(cx - s, cy, cx + s, cy)
    v = QGraphicsLineItem(cx, cy - s, cx, cy + s)
    h.setPen(pen)
    v.setPen(pen)
    return [h, v]


def make_linear_dimension(
    e1, e2, nx, ny, off, texto, span, factor_conversion, preview=False
) -> QGraphicsItemGroup:
    grp = QGraphicsItemGroup()
    x1, y1 = float(e1[0]), float(e1[1])
    x2, y2 = float(e2[0]), float(e2[1])
    dx, dy = x2 - x1, y2 - y1
    Lm = math.hypot(dx, dy)
    if Lm < 1e-12:
        return grp
    ux, uy = dx / Lm, dy / Lm
    off_use = max(-span * 0.48, min(span * 0.48, float(off)))
    if abs(off_use) < span * 0.002:
        off_use = math.copysign(span * 0.05, off_use if off_use != 0 else 1.0)
    sg = 1.0 if off_use >= 0 else -1.0
    g0, g1 = span * 0.012, span * 0.022
    d1 = (x1 + nx * off_use, y1 + ny * off_use)
    d2 = (x2 + nx * off_use, y2 + ny * off_use)
    e1s = (x1 + nx * g0 * sg, y1 + ny * g0 * sg)
    e1e = (d1[0] + nx * g1 * sg, d1[1] + ny * g1 * sg)
    e2s = (x2 + nx * g0 * sg, y2 + ny * g0 * sg)
    e2e = (d2[0] + nx * g1 * sg, d2[1] + ny * g1 * sg)
    for ln in (
        _line(e1s[0], e1s[1], e1e[0], e1e[1], preview, span, factor_conversion),
        _line(e2s[0], e2s[1], e2e[0], e2e[1], preview, span, factor_conversion),
        _arrow_path(d1, d2, preview, span, factor_conversion),
    ):
        grp.addToGroup(ln)
    ang = math.degrees(math.atan2(uy, ux))
    if ang > 90:
        ang -= 180
    if ang < -90:
        ang += 180
    mtx = (d1[0] + d2[0]) * 0.5 + nx * sg * span * 0.028
    mty = (d1[1] + d2[1]) * 0.5 + ny * sg * span * 0.028
    grp.addToGroup(_dim_text_label(texto, mtx, mty, ang, preview, span, factor_conversion))
    grp.setZValue(Z_DIM_PREVIEW if preview else Z_DIM)
    return grp


def make_parallel_separation_dimension(
    e1, e2, u, n, W, mx, my, texto, span, factor_conversion, preview=False
) -> QGraphicsItemGroup:
    grp = QGraphicsItemGroup()
    nx, ny = n[0], n[1]
    ux, uy = u[0], u[1]
    x1, y1 = float(e1[0]), float(e1[1])
    x2, y2 = float(e2[0]), float(e2[1])
    mxf, myf = float(mx), float(my)
    if W < 1e-15:
        return grp
    t1 = (mxf - x1) * ux + (myf - y1) * uy
    t2 = (mxf - x2) * ux + (myf - y2) * uy
    q1 = (x1 + ux * t1, y1 + uy * t1)
    q2 = (x2 + ux * t2, y2 + uy * t2)
    g0, g1 = span * 0.012, span * 0.022

    def _seg_short(a, b, ga, gb):
        dx, dy = b[0] - a[0], b[1] - a[1]
        le = math.hypot(dx, dy)
        if le < 1e-12:
            return a, b
        dx, dy = dx / le, dy / le
        return (
            (a[0] + dx * ga, a[1] + dy * ga),
            (b[0] - dx * gb, b[1] - dy * gb),
        )

    e1s, e1e = _seg_short((x1, y1), q1, g0, g1)
    e2s, e2e = _seg_short((x2, y2), q2, g0, g1)
    for ln in (
        _line(e1s[0], e1s[1], e1e[0], e1e[1], preview, span, factor_conversion),
        _line(e2s[0], e2s[1], e2e[0], e2e[1], preview, span, factor_conversion),
        _arrow_path(q1, q2, preview, span, factor_conversion),
    ):
        grp.addToGroup(ln)
    dqx, dqy = q2[0] - q1[0], q2[1] - q1[1]
    Lq = math.hypot(dqx, dqy)
    ang = math.degrees(math.atan2(dqy / Lq, dqx / Lq)) if Lq > 1e-12 else math.degrees(math.atan2(ny, nx))
    if ang > 90:
        ang -= 180
    if ang < -90:
        ang += 180
    qmx, qmy = (q1[0] + q2[0]) * 0.5, (q1[1] + q2[1]) * 0.5
    dnp = (mxf - qmx) * nx + (myf - qmy) * ny
    s_txt = 1.0 if dnp >= 0 else -1.0
    lab = span * 0.03
    mtx = qmx + nx * s_txt * lab
    mty = qmy + ny * s_txt * lab
    grp.addToGroup(_dim_text_label(texto, mtx, mty, ang, preview, span, factor_conversion))
    grp.setZValue(Z_DIM_PREVIEW if preview else Z_DIM)
    return grp


def make_diameter_dimension(
    cx, cy, r, ux, uy, off_n, factor_conversion, span, centro_pieza, preview=False
) -> QGraphicsItemGroup:
    grp = QGraphicsItemGroup()
    nx, ny = -uy, ux
    cmx, cmy = centro_pieza
    if nx * (cx - cmx) + ny * (cy - cmy) < 0:
        nx, ny = -nx, -ny
    p_a = (cx - ux * r, cy - uy * r)
    p_b = (cx + ux * r, cy + uy * r)
    grp.addToGroup(_arrow_path(p_a, p_b, preview, span, factor_conversion))
    for mk in make_center_mark(cx, cy, span, factor_conversion, preview):
        grp.addToGroup(mk)
    diam_in = (2.0 * r) / factor_conversion
    texto = f'Ø{diam_in:.4f}"'
    off_use = max(-span * 0.48, min(span * 0.48, float(off_n)))
    if abs(off_use) < span * 0.003:
        off_use = math.copysign(max(r, span * 0.04) * 0.4, off_use if off_use != 0 else 1.0)
    rim_x = cx + ux * r
    rim_y = cy + uy * r
    txp = cx + nx * off_use + ux * span * 0.012
    typ = cy + ny * off_use + uy * span * 0.012
    grp.addToGroup(
        _line(rim_x, rim_y, cx + nx * off_use * 0.55, cy + ny * off_use * 0.55, preview, span, factor_conversion)
    )
    ang = math.degrees(math.atan2(ny, nx))
    if ang > 90:
        ang -= 180
    if ang < -90:
        ang += 180
    grp.addToGroup(_dim_text_label(texto, txp, typ, ang, preview, span, factor_conversion))
    grp.setZValue(Z_DIM_PREVIEW if preview else Z_DIM)
    return grp


def make_radius_dimension(
    cx, cy, r, rim_x, rim_y, off_n, factor_conversion, span, centro_pieza, preview=False
) -> QGraphicsItemGroup:
    grp = QGraphicsItemGroup()
    ux = (rim_x - cx) / max(r, 1e-12)
    uy = (rim_y - cy) / max(r, 1e-12)
    lu = math.hypot(ux, uy)
    if lu > 1e-12:
        ux, uy = ux / lu, uy / lu
    nx, ny = -uy, ux
    cmx, cmy = centro_pieza
    if nx * (cx - cmx) + ny * (cy - cmy) < 0:
        nx, ny = -nx, -ny
    for mk in make_center_mark(cx, cy, span, factor_conversion, preview):
        grp.addToGroup(mk)
    ix = cx + ux * max(r - span * 0.004, r * 0.02)
    iy = cy + uy * max(r - span * 0.004, r * 0.02)
    grp.addToGroup(_arrow_path((ix, iy), (rim_x, rim_y), preview, span, factor_conversion, bidirectional=False))
    rad_in = r / factor_conversion
    texto = f'R{rad_in:.4f}"'
    off_use = max(-span * 0.48, min(span * 0.48, float(off_n)))
    if abs(off_use) < span * 0.003:
        off_use = math.copysign(span * 0.06, off_use if off_use != 0 else 1.0)
    mpx = (cx + rim_x) * 0.5 + nx * off_use * 0.35
    mpy = (cy + rim_y) * 0.5 + ny * off_use * 0.35
    txp = mpx + nx * (abs(off_use) * 0.4 + span * 0.014)
    typ = mpy + ny * (abs(off_use) * 0.4 + span * 0.014)
    grp.addToGroup(_line(mpx, mpy, txp, typ, preview, span, factor_conversion))
    ang = math.degrees(math.atan2(ny, nx))
    if ang > 90:
        ang -= 180
    if ang < -90:
        ang += 180
    grp.addToGroup(_dim_text_label(texto, txp, typ, ang, preview, span, factor_conversion))
    grp.setZValue(Z_DIM_PREVIEW if preview else Z_DIM)
    return grp


def make_angular_dimension(
    vtx, u1, u2, mx, my, span, factor_conversion, preview=False
) -> QGraphicsItemGroup:
    grp = QGraphicsItemGroup()
    vx, vy = float(vtx[0]), float(vtx[1])
    a1 = math.atan2(u1[1], u1[0]) % (2.0 * math.pi)
    a2 = math.atan2(u2[1], u2[0]) % (2.0 * math.pi)
    ccw = (a2 - a1) % (2.0 * math.pi)
    cwx = (a1 - a2) % (2.0 * math.pi)
    ac = math.atan2(float(my) - vy, float(mx) - vx) % (2.0 * math.pi)
    in_ccw = ((ac - a1) % (2.0 * math.pi)) <= ccw
    use_ccw = in_ccw
    if use_ccw:
        a_start = a1
        sweep = ccw
    else:
        a_start = a2
        sweep = cwx
    r = math.hypot(float(mx) - vx, float(my) - vy)
    r = max(span * 0.04, min(span * 0.45, r))
    ext = r * 1.08
    grp.addToGroup(_line(vx, vy, vx + u1[0] * ext, vy + u1[1] * ext, preview, span, factor_conversion))
    grp.addToGroup(_line(vx, vy, vx + u2[0] * ext, vy + u2[1] * ext, preview, span, factor_conversion))
    path = QPainterPath()
    rect = QRectF(vx - r, vy - r, 2 * r, 2 * r)
    start_deg = math.degrees(a_start)
    span_deg = math.degrees(sweep)
    path.arcMoveTo(rect, start_deg)
    path.arcTo(rect, start_deg, span_deg)
    arc_item = QGraphicsPathItem(path)
    arc_item.setPen(_pen_scene(preview, span, factor_conversion, dashed=False))
    grp.addToGroup(arc_item)
    mid = (a_start + sweep * 0.5) % (2.0 * math.pi)
    txr = r + span * 0.03
    tx, ty = vx + txr * math.cos(mid), vy + txr * math.sin(mid)
    deg = math.degrees(sweep)
    grp.addToGroup(_dim_text_label(f"{deg:.4f}°", tx, ty, math.degrees(mid), preview, span, factor_conversion))
    grp.setZValue(Z_DIM_PREVIEW if preview else Z_DIM)
    return grp


def make_snap_overlay(sc: dict, span: float) -> list:
    items = []
    tipo = sc.get("tipo")
    sk = sc.get("snap_kind")
    pt = sc["pt"]
    px, py = float(pt[0]), float(pt[1])

    if (
        tipo == "arista"
        and sk in ("arista_cuerpo", "midpoint")
        and all(k in sc for k in ("x1", "y1", "x2", "y2"))
    ):
        x1, y1, x2, y2 = sc["x1"], sc["y1"], sc["x2"], sc["y2"]
        mx, my = (x1 + x2) * 0.5, (y1 + y2) * 0.5
        for gx, gy in ((x1, y1), (x2, y2), (mx, my)):
            items.append(ScreenSquare(gx, gy, half_px=3.5))

    if sk == "arista_cuerpo":
        items.append(ScreenDot(px, py, radius_px=3.5, fill="#FACC15"))

    if sk in ("endpoint", "vertice"):
        items.append(ScreenCross(px, py, half_px=5))
    elif sk in ("rim",) or tipo in ("circulo", "arco"):
        items.append(ScreenDot(px, py, radius_px=4.5, fill="#FDE047", stroke="#854D0E"))
    elif tipo == "libre" or sk is None:
        items.append(ScreenDot(px, py, radius_px=3, fill="#94A3B8", stroke="#475569", stroke_w=0.6))
    return items
