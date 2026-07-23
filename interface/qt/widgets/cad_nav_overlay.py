"""ViewCube estilo Inventor (caras/aristas/esquinas + flechas 90°) y tríada XYZ."""
from __future__ import annotations

import math
from typing import Callable
import time

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

# Convención nest/Inventor: FRONT = cara de la chapa (+Z), TOP = +Y
_FACES = {
    "FRONT": (0.0, 0.0, 1.0),
    "BACK": (0.0, 0.0, -1.0),
    "RIGHT": (1.0, 0.0, 0.0),
    "LEFT": (-1.0, 0.0, 0.0),
    "TOP": (0.0, 1.0, 0.0),
    "BOTTOM": (0.0, -1.0, 0.0),
}

_CUBE_VERTS = [
    (-1, -1, -1),
    (1, -1, -1),
    (1, 1, -1),
    (-1, 1, -1),
    (-1, -1, 1),
    (1, -1, 1),
    (1, 1, 1),
    (-1, 1, 1),
]

_FACE_IDX = {
    "FRONT": (4, 5, 6, 7),
    "BACK": (1, 0, 3, 2),
    "RIGHT": (1, 2, 6, 5),
    "LEFT": (0, 4, 7, 3),
    "TOP": (3, 2, 6, 7),
    "BOTTOM": (0, 1, 5, 4),
}

_EDGES = [
    (4, 5, "FRONT", "BOTTOM"),
    (5, 6, "FRONT", "RIGHT"),
    (6, 7, "FRONT", "TOP"),
    (7, 4, "FRONT", "LEFT"),
    (0, 1, "BACK", "BOTTOM"),
    (1, 2, "BACK", "RIGHT"),
    (2, 3, "BACK", "TOP"),
    (3, 0, "BACK", "LEFT"),
    (1, 5, "RIGHT", "BOTTOM"),
    (2, 6, "RIGHT", "TOP"),
    (0, 4, "LEFT", "BOTTOM"),
    (3, 7, "LEFT", "TOP"),
]

_CORNERS = [
    (4, "FRONT", "LEFT", "BOTTOM"),
    (5, "FRONT", "RIGHT", "BOTTOM"),
    (6, "FRONT", "RIGHT", "TOP"),
    (7, "FRONT", "LEFT", "TOP"),
    (0, "BACK", "LEFT", "BOTTOM"),
    (1, "BACK", "RIGHT", "BOTTOM"),
    (2, "BACK", "RIGHT", "TOP"),
    (3, "BACK", "LEFT", "TOP"),
]

_ADJ = {
    "FRONT": {"up": "TOP", "down": "BOTTOM", "left": "LEFT", "right": "RIGHT"},
    "BACK": {"up": "TOP", "down": "BOTTOM", "left": "RIGHT", "right": "LEFT"},
    "TOP": {"up": "BACK", "down": "FRONT", "left": "LEFT", "right": "RIGHT"},
    "BOTTOM": {"up": "FRONT", "down": "BACK", "left": "LEFT", "right": "RIGHT"},
    "RIGHT": {"up": "TOP", "down": "BOTTOM", "left": "FRONT", "right": "BACK"},
    "LEFT": {"up": "TOP", "down": "BOTTOM", "left": "BACK", "right": "FRONT"},
}

_VIEW_UP = {
    "FRONT": (0.0, 1.0, 0.0),
    "BACK": (0.0, 1.0, 0.0),
    "TOP": (0.0, 0.0, -1.0),
    "BOTTOM": (0.0, 0.0, 1.0),
    "RIGHT": (0.0, 1.0, 0.0),
    "LEFT": (0.0, 1.0, 0.0),
}


def face_view_up(name: str):
    return _VIEW_UP.get(name, (0.0, 1.0, 0.0))


def face_normal(name: str):
    return _FACES[name]


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _norm(v):
    L = math.sqrt(max(1e-18, _dot(v, v)))
    return (v[0] / L, v[1] / L, v[2] / L)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def stable_view_up(look_dir_from_cam, preferred_up, current_up=None, fallback_up=None):
    """ViewUp perpendicular a la vista, con mínimo twist (evita flips en BOTTOM)."""
    fwd = _norm(look_dir_from_cam)
    candidates = []
    if current_up is not None:
        candidates.append(current_up)
    if fallback_up is not None:
        candidates.append(fallback_up)
    candidates.append(preferred_up)
    candidates.extend([(0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, -1.0, 0.0)])

    for cand in candidates:
        d = _dot(cand, fwd)
        u = (cand[0] - d * fwd[0], cand[1] - d * fwd[1], cand[2] - d * fwd[2])
        if _dot(u, u) < 1e-8:
            continue
        return _norm(u)
    return (0.0, 1.0, 0.0)


class _CameraAwareGadget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._get_camera: Callable[[], tuple] | None = None

    def set_camera_provider(self, fn: Callable[[], tuple]) -> None:
        self._get_camera = fn

    def camera_basis(self):
        if self._get_camera:
            try:
                return self._get_camera()
            except Exception:
                pass
        return (0.0, 0.0, -1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)

    def project(self, p, origin: QPointF, scale: float) -> QPointF:
        _fwd, right, up = self.camera_basis()
        x = _dot(p, right)
        y = _dot(p, up)
        return QPointF(origin.x() + x * scale, origin.y() - y * scale)


class ViewCubeWidget(_CameraAwareGadget):
    """ViewCube Inventor-like: caras / aristas / esquinas / flechas / roll en anillo."""

    viewClicked = Signal(str, object, bool)  # kind, payload, recenter (doble clic)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(168, 188)
        self.setMouseTracking(True)
        self.setToolTip("ViewCube — cara / arista / esquina / flechas 90°\nDoble clic: enfocar y centrar")
        self._hover = None
        self._face_polys: dict[str, QPolygonF] = {}
        self._face_order: list[str] = []
        self._edge_segs: list[tuple] = []
        self._corner_pts: list[tuple] = []
        self._arrow_hits: list[tuple] = []
        self._ortho_face: str | None = None
        self._origin = QPointF(84, 100)
        self._scale = 32.0
        self._last_basis = None
        self._ring_c = QPointF(84, 90)
        self._ring_r = 78.0
        self._last_pick_key = None
        self._last_pick_ms = 0

    def sync_from_camera(self) -> None:
        basis = self.camera_basis()
        if basis != self._last_basis:
            self._last_basis = basis
            self.update()

    def _ring_geom(self) -> tuple[QPointF, float]:
        margin = 6
        r = (self.width() - 2 * margin) * 0.5
        c = QPointF(self.width() * 0.5, margin + 8 + r)
        return c, r

    def _dominant_face(self, fwd) -> str | None:
        best = None
        best_a = 0.92
        for name, n in _FACES.items():
            a = -_dot(n, fwd)
            if a > best_a:
                best_a = a
                best = name
        return best

    def _rebuild(self) -> None:
        self._origin = QPointF(self.width() * 0.5, self.height() * 0.52)
        self._scale = min(self.width(), self.height()) * 0.20
        self._ring_c, self._ring_r = self._ring_geom()
        fwd, _, _ = self.camera_basis()
        self._ortho_face = self._dominant_face(fwd)

        scored = []
        for name, n in _FACES.items():
            vis = -_dot(n, fwd)
            if vis > 0.05:
                scored.append((vis, name))
        scored.sort(reverse=True)
        self._face_order = [n for _, n in scored]
        self._face_polys = {}
        for name in self._face_order:
            pts = [self.project(_CUBE_VERTS[i], self._origin, self._scale) for i in _FACE_IDX[name]]
            self._face_polys[name] = QPolygonF(pts)

        self._edge_segs = []
        for v0, v1, fa, fb in _EDGES:
            n_avg = _norm(_add(_FACES[fa], _FACES[fb]))
            if -_dot(n_avg, fwd) < 0.02:
                continue
            p0 = self.project(_CUBE_VERTS[v0], self._origin, self._scale)
            p1 = self.project(_CUBE_VERTS[v1], self._origin, self._scale)
            mid = QPointF((p0.x() + p1.x()) * 0.5, (p0.y() + p1.y()) * 0.5)
            key = tuple(sorted((fa, fb)))
            self._edge_segs.append((key, p0, p1, mid, (fa, fb)))

        self._corner_pts = []
        for vi, f1, f2, f3 in _CORNERS:
            n_avg = _norm(_add(_add(_FACES[f1], _FACES[f2]), _FACES[f3]))
            if -_dot(n_avg, fwd) < 0.05:
                continue
            pt = self.project(_CUBE_VERTS[vi], self._origin, self._scale)
            key = tuple(sorted((f1, f2, f3)))
            self._corner_pts.append((key, pt, (f1, f2, f3)))

        self._arrow_hits = []
        if self._ortho_face:
            self._build_nav_arrows()

    def _tri_arrow(self, tip: QPointF, direction: str, size: float = 10.0) -> QPolygonF:
        if direction == "up":
            return QPolygonF(
                [
                    tip,
                    QPointF(tip.x() - size * 0.7, tip.y() + size),
                    QPointF(tip.x() + size * 0.7, tip.y() + size),
                ]
            )
        if direction == "down":
            return QPolygonF(
                [
                    tip,
                    QPointF(tip.x() - size * 0.7, tip.y() - size),
                    QPointF(tip.x() + size * 0.7, tip.y() - size),
                ]
            )
        if direction == "left":
            return QPolygonF(
                [
                    tip,
                    QPointF(tip.x() + size, tip.y() - size * 0.7),
                    QPointF(tip.x() + size, tip.y() + size * 0.7),
                ]
            )
        return QPolygonF(
            [
                tip,
                QPointF(tip.x() - size, tip.y() - size * 0.7),
                QPointF(tip.x() - size, tip.y() + size * 0.7),
            ]
        )

    def _build_nav_arrows(self) -> None:
        o = self._origin
        r = self._scale * 2.15
        adj = _ADJ.get(self._ortho_face or "", {})
        places = {
            "up": QPointF(o.x(), o.y() - r),
            "down": QPointF(o.x(), o.y() + r),
            "left": QPointF(o.x() - r, o.y()),
            "right": QPointF(o.x() + r, o.y()),
        }
        for d, tip in places.items():
            face = adj.get(d)
            if not face:
                continue
            poly = self._tri_arrow(tip, d, size=9.5)
            # payload = cara destino (las flechas ya funcionan; no tocar lógica de tumble)
            self._arrow_hits.append(("nav", face, poly))

        self._arrow_hits.append(("roll", "ccw", (100.0, 155.0)))
        self._arrow_hits.append(("roll", "cw", (25.0, 80.0)))

    def _draw_roll_arrow_on_ring(
        self,
        p: QPainter,
        *,
        a_lo: float,
        a_hi: float,
        cw: bool,
        hover: bool,
    ) -> None:
        """Flecha curva pegada al anillo exterior, con punta clara."""
        center, radius = self._ring_c, self._ring_r * 0.97
        col = QColor(195, 218, 248) if hover else QColor(228, 232, 238)
        pen = QPen(col, 2.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        rect = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)

        if cw:
            # Arco superior-derecho: de a_hi → a_lo (horario), punta en a_lo
            start, tip_deg = a_hi, a_lo
            span = a_lo - a_hi  # negativo
        else:
            # Arco superior-izquierdo: de a_lo → a_hi (antihorario), punta en a_hi
            start, tip_deg = a_lo, a_hi
            span = a_hi - a_lo  # positivo

        p.drawArc(rect, int(start * 16), int(span * 16))

        a1 = math.radians(tip_deg)
        tip = QPointF(center.x() + radius * math.cos(a1), center.y() - radius * math.sin(a1))
        if cw:
            tx, ty = math.sin(a1), math.cos(a1)
        else:
            tx, ty = -math.sin(a1), -math.cos(a1)
        L = math.hypot(tx, ty) or 1.0
        tx, ty = tx / L, ty / L
        nx, ny = -ty, tx
        size = 7.2
        base = QPointF(tip.x() - tx * size, tip.y() - ty * size)
        p.setBrush(col)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(
            QPolygonF(
                [
                    tip,
                    QPointF(base.x() + nx * size * 0.55, base.y() + ny * size * 0.55),
                    QPointF(base.x() - nx * size * 0.55, base.y() - ny * size * 0.55),
                ]
            )
        )

    def paintEvent(self, _event) -> None:
        self._rebuild()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        c, r = self._ring_c, self._ring_r
        p.setPen(QPen(QColor(120, 130, 145, 110), 1.5))
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(QRectF(c.x() - r, c.y() - r, r * 2, r * 2))

        edge_pen = QColor(40, 45, 55)
        text = QColor(22, 26, 34)

        for name in reversed(self._face_order):
            poly = self._face_polys[name]
            hover = self._hover == ("face", name)
            fill = QColor(150, 188, 230) if hover else QColor(218, 222, 228)
            p.setBrush(fill)
            p.setPen(QPen(edge_pen, 1.5))
            p.drawPolygon(poly)

            cen = QPointF(0, 0)
            for i in range(poly.count()):
                cen += poly.at(i)
            cen /= float(max(1, poly.count()))
            p.setFont(QFont("Segoe UI", 7, QFont.Weight.DemiBold))
            p.setPen(text)
            br = p.fontMetrics().boundingRect(name)
            p.drawText(QPointF(cen.x() - br.width() * 0.5, cen.y() + br.height() * 0.32), name)

        for key, p0, p1, mid, _faces in self._edge_segs:
            hover = self._hover == ("edge", key)
            col = QColor(120, 170, 230, 210) if hover else QColor(90, 100, 120, 35)
            p.setPen(QPen(col, 4.5 if hover else 2.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(p0, p1)

        for key, pt, _faces in self._corner_pts:
            hover = self._hover == ("corner", key)
            p.setPen(QPen(QColor(40, 45, 55), 1.0))
            p.setBrush(QColor(130, 180, 235) if hover else QColor(200, 205, 212))
            rr = 5.5 if hover else 4.2
            p.drawEllipse(pt, rr, rr)

        for kind, payload, geom in self._arrow_hits:
            if kind == "nav":
                hover = self._hover == ("nav", payload)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(210, 225, 245) if hover else QColor(235, 238, 242))
                p.drawPolygon(geom)
                p.setPen(QPen(QColor(60, 70, 85), 1.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPolygon(geom)
            elif kind == "roll":
                hover = self._hover == ("roll", payload)
                a_lo, a_hi = geom
                self._draw_roll_arrow_on_ring(
                    p, a_lo=a_lo, a_hi=a_hi, cw=(payload == "cw"), hover=hover
                )

        p.end()

    def _hit(self, pos) -> tuple | None:
        for kind, payload, geom in self._arrow_hits:
            if kind == "nav" and geom.containsPoint(QPointF(pos), Qt.FillRule.OddEvenFill):
                return ("nav", payload, payload)
            if kind == "roll":
                a_lo, a_hi = geom
                dx = pos.x() - self._ring_c.x()
                dy = self._ring_c.y() - pos.y()
                dist = math.hypot(dx, dy)
                if abs(dist - self._ring_r) <= 14.0:
                    ang = math.degrees(math.atan2(dy, dx)) % 360.0
                    lo, hi = min(a_lo, a_hi) - 6.0, max(a_lo, a_hi) + 6.0
                    if lo <= ang <= hi:
                        return ("roll", payload, payload)

        for key, pt, faces in self._corner_pts:
            dx = pos.x() - pt.x()
            dy = pos.y() - pt.y()
            if dx * dx + dy * dy <= 8.0 * 8.0:
                return ("corner", key, faces)
        for key, p0, p1, mid, faces in self._edge_segs:
            vx, vy = p1.x() - p0.x(), p1.y() - p0.y()
            wx, wy = pos.x() - p0.x(), pos.y() - p0.y()
            L2 = vx * vx + vy * vy
            t = 0.0 if L2 < 1e-9 else max(0.0, min(1.0, (wx * vx + wy * vy) / L2))
            px, py = p0.x() + t * vx, p0.y() + t * vy
            dx, dy = pos.x() - px, pos.y() - py
            if dx * dx + dy * dy <= 6.5 * 6.5:
                return ("edge", key, faces)
        for name in self._face_order:
            poly = self._face_polys.get(name)
            if poly and poly.containsPoint(QPointF(pos), Qt.FillRule.OddEvenFill):
                return ("face", name, name)
        return None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        hit = self._hit(event.position())
        hover = None if hit is None else (hit[0], hit[1])
        if hover != self._hover:
            self._hover = hover
            self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._hit(event.position())
        if not hit:
            return
        kind, _key, payload = hit
        now_ms = int(time.monotonic() * 1000)
        if kind in ("edge", "corner") and isinstance(payload, (list, tuple)):
            pick_key = (kind, tuple(sorted(payload)))
        else:
            pick_key = (kind, payload)

        recenter = (
            self._last_pick_key == pick_key
            and (now_ms - self._last_pick_ms) <= 450
        )
        self._last_pick_key = pick_key
        self._last_pick_ms = now_ms
        self.viewClicked.emit(kind, payload, recenter)


class AxesTriadWidget(_CameraAwareGadget):
    """Tríada XYZ (solo visual)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(96, 96)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._last_basis = None

    def sync_from_camera(self) -> None:
        basis = self.camera_basis()
        if basis != self._last_basis:
            self._last_basis = basis
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        origin = QPointF(self.width() * 0.40, self.height() * 0.68)
        scale = min(self.width(), self.height()) * 0.44

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 80))
        p.drawEllipse(QRectF(4, 4, self.width() - 8, self.height() - 8))

        fwd, _, _ = self.camera_basis()
        axes = (
            ((1.0, 0.0, 0.0), QColor(235, 75, 75), "X"),
            ((0.0, 1.0, 0.0), QColor(70, 205, 95), "Y"),
            ((0.0, 0.0, 1.0), QColor(80, 140, 245), "Z"),
        )
        for dir_w, color, label in sorted(axes, key=lambda a: _dot(a[0], fwd)):
            tip = self.project(dir_w, origin, scale)
            p.setPen(QPen(color, 3.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(origin, tip)
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(tip, 4.8, 4.8)
            p.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            p.setPen(color.lighter(125))
            p.drawText(tip + QPointF(6, -3), label)
        p.setBrush(QColor(245, 245, 250))
        p.drawEllipse(origin, 3.4, 3.4)
        p.end()
