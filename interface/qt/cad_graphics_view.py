"""Visor QGraphicsView para detalle de pieza con cotas interactivas."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, QPointF, QRectF, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from interface.material_colors import paleta_cad_hex
from interface.qt.cad_dimension_items import (
    CAD_MARK,
    Z_DIM,
    Z_DIM_PREVIEW,
    make_angular_dimension,
    make_diameter_dimension,
    make_linear_dimension,
    make_parallel_separation_dimension,
    make_radius_dimension,
    make_snap_overlay,
)
from interface.qt.cad_snap import SnapContext, snap_cota
from interface.qt.dxf_part_geometry import (
    aristas_misma_geometria,
    aristas_paralelas,
    normal_cota_desde_cuerda,
    resolver_angulo_aristas,
    snap_en_borde_para_lineal,
    vector_unitario_arista,
)
from interface.qt.dxf_part_loader import DxfPartModel
from interface.qt.dxf_qt_renderer import render_modelspace

CAD_VIEW_BG = "#0B1220"
Z_GEOM_FILL = 1.0
Z_GEOM_STROKE = 5.0
Z_HOLE_FILL = 2.0
Z_HOLE_STROKE = 6.0
Z_MARK = 15.0
Z_PLASMA = 12.0


def _stroke_pen(color: str, span: float, factor_conversion: float, *, dashed: bool = False, cosmetic: bool = False) -> QPen:
    if cosmetic:
        pen = QPen(QColor(color))
        pen.setWidthF(1.0)
        pen.setCosmetic(True)
    else:
        lw = max(float(factor_conversion) * 0.010, span * 0.0010)
        pen = QPen(QColor(color))
        pen.setWidthF(lw)
    if dashed:
        pen.setStyle(Qt.PenStyle.DashLine)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


class CadPartGraphicsView(QGraphicsView):
    """Viewport CAD con snap, cotas lineales/Ø/R/ángulo y zoom tipo AutoCAD."""

    rotate_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.TextAntialiasing
        )
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QBrush(QColor(CAD_VIEW_BG)))
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.scale(1.0, -1.0)

        self.metrics_callback = None
        self._model: DxfPartModel | None = None
        self.factor_conversion = 25.4
        self._fit_rect: tuple[float, float, float, float] | None = None
        self._content_rect: QRectF | None = None
        self._model_piece_span = 1.0
        self._centro_pieza = (0.0, 0.0)
        self._snap_ctx = SnapContext()
        self._dim_estado = "idle"
        self._dim: dict = {}
        self._material = ""
        self._geom_root_items: list = []
        self._dim_items: list = []
        self._preview_items: list = []
        self._snap_items: list = []
        self._plasma_items: list = []
        self._placeholder: QGraphicsSimpleTextItem | None = None
        self._is_panning = False
        self._pan_last = QPointF()
        self._cursor_mode = "normal"
        self._user_view_adjusted = False

    def set_material(self, material: str | None = None) -> None:
        self._material = str(material or "").strip()

    def _paleta(self):
        return paleta_cad_hex(self._material)

    def _piece_span(self) -> float:
        if self._model_piece_span > 1e-9:
            return self._model_piece_span
        if self._fit_rect:
            x0, y0, x1, y1 = self._fit_rect
            return max(x1 - x0, y1 - y0, 1e-9)
        return self._visible_span()

    def _visible_span(self) -> float:
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        return max(float(rect.width()), float(rect.height()), 1e-9)

    def _scene_xy(self, event) -> tuple[float, float]:
        p = self.mapToScene(event.position().toPoint())
        return float(p.x()), float(p.y())

    def clear(self) -> None:
        self._scene.clear()
        self._geom_root_items.clear()
        self._dim_items.clear()
        self._preview_items.clear()
        self._snap_items.clear()
        self._plasma_items.clear()
        self._placeholder = None
        self._model = None
        self._fit_rect = None
        self._content_rect = None
        self._centro_pieza = (0.0, 0.0)
        self._snap_ctx = SnapContext()
        self._dim_estado = "idle"
        self._dim = {}

    def show_placeholder(self, text: str = "SELECCIONE UNA PIEZA DE LA LISTA") -> None:
        self.clear()
        txt = QGraphicsSimpleTextItem(text)
        txt.setBrush(QBrush(QColor("#64748B")))
        f = txt.font()
        f.setPointSize(11)
        txt.setFont(f)
        br = txt.boundingRect()
        txt.setPos(-br.width() * 0.5, -br.height() * 0.5)
        txt.setTransform(QTransform.fromScale(1.0, -1.0))
        txt.setZValue(0.5)
        self._scene.addItem(txt)
        self._placeholder = txt
        self.resetTransform()
        self.scale(1.0, -1.0)

    def _clear_preview(self) -> None:
        for it in self._preview_items:
            self._scene.removeItem(it)
        self._preview_items.clear()

    def _clear_snap_overlay(self) -> None:
        for it in self._snap_items:
            self._scene.removeItem(it)
        self._snap_items.clear()

    def _add_preview(self, item) -> None:
        self._scene.addItem(item)
        self._preview_items.append(item)

    def _path_from_pts(self, pts, closed: bool = False) -> QPainterPath:
        path = QPainterPath()
        if not pts:
            return path
        path.moveTo(QPointF(pts[0][0], pts[0][1]))
        for x, y in pts[1:]:
            path.lineTo(QPointF(x, y))
        if closed:
            path.closeSubpath()
        return path

    def load_model(self, model: DxfPartModel, *, fit: bool = True) -> None:
        self.clear()
        self._model = model
        self.factor_conversion = model.factor_conversion
        self._centro_pieza = model.centro_pieza
        self._snap_ctx = model.snap_ctx if model.snap_ctx is not None else SnapContext()
        self._model_piece_span = max(
            model.max_x_raw - model.min_x_raw,
            model.max_y_raw - model.min_y_raw,
            1e-9,
        )
        self._user_view_adjusted = False

        if model.doc is not None and model.msp is not None:
            rect = render_modelspace(model.doc, model.msp, self._scene, bg_color=CAD_VIEW_BG)
            self._content_rect = rect if rect and not rect.isEmpty() else None
            self._geom_root_items = [
                it for it in self._scene.items() if it.zValue() < Z_DIM
            ]

        if fit:
            QTimer.singleShot(0, self.fit_view)

        if callable(self.metrics_callback):
            self.metrics_callback(
                model.min_x_raw,
                model.max_x_raw,
                model.min_y_raw,
                model.max_y_raw,
                model.perimetro_total,
                True,
                area=model.area_neta,
            )

    def clear_plasma_overlay(self) -> None:
        for it in self._plasma_items:
            try:
                self._scene.removeItem(it)
            except Exception:
                pass
        self._plasma_items.clear()

    def set_plasma_overlay(
        self,
        offset_mm: float,
        *,
        label: str | None = None,
    ) -> dict | None:
        """
        Dibuja contorno compensado (rojo) sobre OUTER del modelo cargado.
        offset_mm es el buffer plasma en mm (misma regla que nesting).
        Retorna métricas compensadas en unidades de escena o None.
        """
        self.clear_plasma_overlay()
        model = self._model
        if model is None or float(offset_mm or 0.0) <= 0:
            return None
        rings = list(getattr(model, "outer_rings", None) or [])
        if not rings:
            return None

        from shapely.geometry import Polygon

        fc = float(model.factor_conversion or 25.4) or 25.4
        off_scene = float(offset_mm) / fc
        minx = miny = float("inf")
        maxx = maxy = float("-inf")
        area_comp = 0.0
        perim_comp = 0.0

        pen = QPen(QColor("#FF1A1A"))
        pen.setWidthF(2.4)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        for ring in rings:
            if len(ring) < 3:
                continue
            try:
                poly = Polygon(ring)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.is_empty:
                    continue
                buff = poly.buffer(off_scene, join_style=1, quad_segs=16)
                if buff is None or buff.is_empty:
                    continue
                geoms = list(buff.geoms) if hasattr(buff, "geoms") else [buff]
                for g in geoms:
                    if not hasattr(g, "exterior"):
                        continue
                    coords = list(g.exterior.coords)
                    if len(coords) < 3:
                        continue
                    path = self._path_from_pts(coords, closed=True)
                    item = QGraphicsPathItem(path)
                    item.setPen(pen)
                    item.setBrush(Qt.BrushStyle.NoBrush)
                    item.setZValue(Z_PLASMA)
                    self._scene.addItem(item)
                    self._plasma_items.append(item)
                    xs = [p[0] for p in coords]
                    ys = [p[1] for p in coords]
                    minx = min(minx, min(xs))
                    maxx = max(maxx, max(xs))
                    miny = min(miny, min(ys))
                    maxy = max(maxy, max(ys))
                    area_comp += float(g.area)
                    perim_comp += float(g.length)
            except Exception:
                continue

        if label:
            cx_label = (minx + maxx) * 0.5 if minx < maxx else float(self._centro_pieza[0])
            top_label = maxy if maxy > miny else float(self._centro_pieza[1])
            self._agregar_label_plasma(str(label), cx_label, top_label)

        if minx >= maxx:
            return None
        return {
            "min_x": minx,
            "max_x": maxx,
            "min_y": miny,
            "max_y": maxy,
            "area": area_comp,
            "perimetro": perim_comp,
            "offset_mm": float(offset_mm),
        }

    def emphasize_plasma_outers(self, *, label: str | None = None) -> None:
        """Contorno rojo grueso sobre OUTER del modelo (DXF ya compensado)."""
        self.clear_plasma_overlay()
        model = self._model
        if model is None:
            return
        rings = list(getattr(model, "outer_rings", None) or [])
        if not rings:
            return

        pen = QPen(QColor("#FF1A1A"))
        pen.setWidthF(3.2)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        minx = miny = float("inf")
        maxx = maxy = float("-inf")
        for ring in rings:
            if len(ring) < 3:
                continue
            path = self._path_from_pts(ring, closed=True)
            item = QGraphicsPathItem(path)
            item.setPen(pen)
            item.setBrush(Qt.BrushStyle.NoBrush)
            item.setZValue(Z_PLASMA)
            self._scene.addItem(item)
            self._plasma_items.append(item)
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            minx = min(minx, min(xs))
            maxx = max(maxx, max(xs))
            miny = min(miny, min(ys))
            maxy = max(maxy, max(ys))

        if label and minx < maxx:
            self._agregar_label_plasma(str(label), (minx + maxx) * 0.5, maxy)

    def _agregar_label_plasma(self, label: str, cx_scene: float, top_scene: float) -> None:
        """Coloca el texto '+X"' sobre la pieza con tamaño cosmético.

        Sin ``ItemIgnoresTransformations`` el font-size (10pt) se interpreta en
        unidades de escena (pulgadas → literalmente 10 pulgadas de alto), lo que
        obliga a ``fit_view`` a hacer zoom-out extremo: el label ocupa media
        pantalla y la pieza queda del tamaño de un sello. Con el flag activo el
        texto se pinta siempre a la misma cantidad de píxeles independientemente
        del zoom, como cualquier tooltip nativo.
        """
        txt = QGraphicsSimpleTextItem(label)
        txt.setBrush(QBrush(QColor("#FCA5A5")))
        f = txt.font()
        f.setPointSize(10)
        f.setBold(True)
        txt.setFont(f)
        # Ignora el scale(1,-1) del view: el texto se pinta erguido y a tamaño
        # constante en píxeles → no infla el bounding rect de la escena y
        # ``fit_view`` sigue enmarcando solo la pieza.
        txt.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True
        )
        # Anclamos justo arriba de la pieza en coord de escena; los offsets del
        # texto respecto al ancla se resuelven internamente en píxeles.
        txt.setPos(float(cx_scene), float(top_scene))
        txt.setZValue(Z_PLASMA + 1)
        self._scene.addItem(txt)
        self._plasma_items.append(txt)

    def fit_view(self) -> None:
        rect = self._content_rect
        if rect is None or rect.isEmpty():
            if self._fit_rect:
                x0, y0, x1, y1 = self._fit_rect
                rect = QRectF(x0, y0, x1 - x0, y1 - y0)
            else:
                return
        pad = max(rect.width(), rect.height()) * 0.06
        fit = rect.adjusted(-pad, -pad, pad, pad)
        self.resetTransform()
        self.scale(1.0, -1.0)
        self.fitInView(fit, Qt.AspectRatioMode.KeepAspectRatio)
        self.centerOn(fit.center())
        buf = max(fit.width(), fit.height()) * 2.0
        self._scene.setSceneRect(fit.adjusted(-buf, -buf, buf, buf))
        self._fit_rect = (fit.left(), fit.bottom(), fit.right(), fit.top())
        self._user_view_adjusted = False

    def _cancel_dim(self) -> None:
        self._clear_preview()
        self._clear_snap_overlay()
        self._dim_estado = "idle"
        self._dim = {}

    def _commit_dim_item(self, item) -> None:
        self._scene.addItem(item)
        self._dim_items.append(item)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel_dim()
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        if event.angleDelta().y() == 0:
            return
        self._user_view_adjusted = True
        # Misma dirección y factor que el visor de nesting (rueda arriba = acercar).
        factor = 1.12 if event.angleDelta().y() > 0 else 1.0 / 1.12
        self.scale(factor, factor)
        event.accept()

    def _set_pan_cursor(self, mode: str) -> None:
        if mode == self._cursor_mode:
            return
        self._cursor_mode = mode
        if mode == "panning":
            self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        else:
            self.unsetCursor()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = True
            self._pan_last = event.position()
            self._set_pan_cursor("panning")
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.rotate_requested.emit()
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        mx, my = self._scene_xy(event)
        snap_span = self._visible_span()
        pspan = self._piece_span()
        fc = self.factor_conversion
        sc = snap_cota(mx, my, snap_span, self._snap_ctx)
        pt = sc["pt"]
        eps = max(pspan * 1e-7, 1e-9)

        if self._dim_estado == "idle":
            if sc["tipo"] == "circulo":
                cx, cy, r = sc["cx"], sc["cy"], sc["r"]
                ux = (pt[0] - cx) / r
                uy = (pt[1] - cy) / r
                lu = math.hypot(ux, uy)
                if lu > 1e-12:
                    ux, uy = ux / lu, uy / lu
                self._dim = {"cx": cx, "cy": cy, "r": r, "ux": ux, "uy": uy}
                self._dim_estado = "dia_p2"
            elif sc["tipo"] == "arco":
                self._dim = {"cx": sc["cx"], "cy": sc["cy"], "r": sc["r"], "rim": (pt[0], pt[1])}
                self._dim_estado = "rad_p2"
            elif snap_en_borde_para_lineal(sc):
                if sc["tipo"] == "arista" and not sc.get("arc_seg"):
                    x1, y1, x2, y2 = sc["x1"], sc["y1"], sc["x2"], sc["y2"]
                    p_a, p_b = (x1, y1), (x2, y2)
                    nr = normal_cota_desde_cuerda(p_a, p_b, self._centro_pieza)
                    if nr is None:
                        return
                    nx, ny, _ux, _uy, _L = nr
                    self._dim = {
                        "seg1": (x1, y1, x2, y2),
                        "first_pt": (float(pt[0]), float(pt[1])),
                        "p1": p_a,
                        "p2": p_b,
                        "chord_p1": p_a,
                        "chord_p2": p_b,
                        "mode": "chord_pending",
                        "nx": nx,
                        "ny": ny,
                        "midx": (p_a[0] + p_b[0]) * 0.5,
                        "midy": (p_a[1] + p_b[1]) * 0.5,
                    }
                    self._dim_estado = "lin_p2_after_edge"
                else:
                    d0 = {"p1": (pt[0], pt[1])}
                    if sc["tipo"] == "arista":
                        d0["seg1"] = (sc["x1"], sc["y1"], sc["x2"], sc["y2"])
                    self._dim = d0
                    self._dim_estado = "lin_p1"
            event.accept()
            return

        if self._dim_estado == "lin_p2_after_edge":
            seg1 = self._dim["seg1"]
            first_pt = self._dim["first_pt"]
            chord_p1 = self._dim["chord_p1"]
            chord_p2 = self._dim["chord_p2"]
            nx0, ny0 = self._dim["nx"], self._dim["ny"]
            midx0, midy0 = self._dim["midx"], self._dim["midy"]

            if sc["tipo"] == "arista" and not sc.get("arc_seg") and seg1 is not None:
                u1 = vector_unitario_arista(seg1)
                u2 = vector_unitario_arista((sc["x1"], sc["y1"], sc["x2"], sc["y2"]))
                if u1 and u2 and aristas_paralelas(u1, u2) and not aristas_misma_geometria(seg1, sc):
                    nxp, nyp = -u1[1], u1[0]
                    vx, vy = pt[0] - first_pt[0], pt[1] - first_pt[1]
                    raw = vx * nxp + vy * nyp
                    if raw < 0:
                        nxp, nyp = -nxp, -nyp
                        raw = -raw
                    W = raw
                    if W >= max(eps * 500, pspan * 1e-8):
                        midx = (first_pt[0] + pt[0]) * 0.5
                        midy = (first_pt[1] + pt[1]) * 0.5
                        self._dim = {
                            "mode": "parallel",
                            "e1": first_pt,
                            "e2": (pt[0], pt[1]),
                            "u": u1,
                            "n": (nxp, nyp),
                            "W": W,
                            "nx": nxp,
                            "ny": nyp,
                            "midx": midx,
                            "midy": midy,
                            "seg1": seg1,
                        }
                        self._dim_estado = "lin_p3"
                        self._clear_preview()
                        event.accept()
                        return
                if (
                    u1
                    and u2
                    and (not aristas_paralelas(u1, u2))
                    and (not aristas_misma_geometria(seg1, sc))
                ):
                    seg2 = (sc["x1"], sc["y1"], sc["x2"], sc["y2"])
                    ang_data = resolver_angulo_aristas(seg1, seg2, first_pt, pt, pspan)
                    if ang_data is not None:
                        self._dim = {
                            "mode": "angle",
                            "vtx": ang_data["vtx"],
                            "u1": ang_data["u1"],
                            "u2": ang_data["u2"],
                        }
                        self._dim_estado = "ang_p3"
                        self._clear_preview()
                        event.accept()
                        return

            off = (mx - midx0) * nx0 + (my - midy0) * ny0
            off = max(-pspan * 0.48, min(pspan * 0.48, off))
            self._clear_preview()
            L = math.hypot(chord_p2[0] - chord_p1[0], chord_p2[1] - chord_p1[1])
            dist_in = L / self.factor_conversion
            self._commit_dim_item(
                make_linear_dimension(
                    chord_p1, chord_p2, nx0, ny0, off, f'{dist_in:.4f}"', pspan, fc, preview=False
                )
            )
            self._dim_estado = "idle"
            self._dim = {}
            event.accept()
            return

        if self._dim_estado == "lin_p1":
            if not snap_en_borde_para_lineal(sc):
                return
            p1 = self._dim["p1"]
            if math.hypot(pt[0] - p1[0], pt[1] - p1[1]) < eps:
                return
            seg1 = self._dim.get("seg1")
            if seg1 is not None and sc["tipo"] == "arista":
                u1 = vector_unitario_arista(seg1)
                u2 = vector_unitario_arista((sc["x1"], sc["y1"], sc["x2"], sc["y2"]))
                if u1 and u2 and aristas_paralelas(u1, u2):
                    midx = (p1[0] + pt[0]) * 0.5
                    midy = (p1[1] + pt[1]) * 0.5
                    nxp, nyp = -u1[1], u1[0]
                    vx, vy = pt[0] - p1[0], pt[1] - p1[1]
                    raw = vx * nxp + vy * nyp
                    if raw < 0:
                        nxp, nyp = -nxp, -nyp
                        raw = -raw
                    W = raw
                    if W < max(eps * 500, pspan * 1e-8):
                        return
                    self._dim.update(
                        {
                            "mode": "parallel",
                            "e1": p1,
                            "e2": pt,
                            "u": u1,
                            "n": (nxp, nyp),
                            "W": W,
                            "nx": nxp,
                            "ny": nyp,
                            "midx": midx,
                            "midy": midy,
                            "p2": pt,
                        }
                    )
                    self._dim_estado = "lin_p3"
                    event.accept()
                    return
                if (
                    u1
                    and u2
                    and (not aristas_paralelas(u1, u2))
                    and (not aristas_misma_geometria(seg1, sc))
                    and (not sc.get("arc_seg"))
                ):
                    seg2 = (sc["x1"], sc["y1"], sc["x2"], sc["y2"])
                    ang_data = resolver_angulo_aristas(seg1, seg2, p1, pt, pspan)
                    if ang_data is not None:
                        self._dim = {
                            "mode": "angle",
                            "vtx": ang_data["vtx"],
                            "u1": ang_data["u1"],
                            "u2": ang_data["u2"],
                        }
                        self._dim_estado = "ang_p3"
                        event.accept()
                        return
                if aristas_misma_geometria(seg1, sc):
                    s = seg1
                    p_a = (s[0], s[1])
                    p_b = (s[2], s[3])
                    nr = normal_cota_desde_cuerda(p_a, p_b, self._centro_pieza)
                    if nr is None:
                        return
                    nx, ny, _ux, _uy, _L = nr
                    self._dim["p1"], self._dim["p2"] = p_a, p_b
                    self._dim["mode"] = "chord"
                    self._dim["nx"], self._dim["ny"] = nx, ny
                    self._dim["midx"] = (p_a[0] + p_b[0]) * 0.5
                    self._dim["midy"] = (p_a[1] + p_b[1]) * 0.5
                    self._dim_estado = "lin_p3"
                    event.accept()
                    return
            nr = normal_cota_desde_cuerda(p1, pt, self._centro_pieza)
            if nr is None:
                return
            nx, ny, _ux, _uy, _L = nr
            self._dim["p2"] = pt
            self._dim["nx"], self._dim["ny"] = nx, ny
            self._dim["midx"] = (p1[0] + pt[0]) * 0.5
            self._dim["midy"] = (p1[1] + pt[1]) * 0.5
            self._dim["mode"] = "chord"
            self._dim_estado = "lin_p3"
            event.accept()
            return

        if self._dim_estado == "ang_p3":
            vtx = self._dim.get("vtx")
            u1 = self._dim.get("u1")
            u2 = self._dim.get("u2")
            if not (vtx and u1 and u2):
                self._dim_estado = "idle"
                self._dim = {}
                return
            self._clear_preview()
            self._commit_dim_item(
                make_angular_dimension(vtx, u1, u2, mx, my, pspan, fc, preview=False)
            )
            self._dim_estado = "idle"
            self._dim = {}
            event.accept()
            return

        if self._dim_estado == "lin_p3":
            nx, ny = self._dim["nx"], self._dim["ny"]
            midx, midy = self._dim["midx"], self._dim["midy"]
            off = (mx - midx) * nx + (my - midy) * ny
            off = max(-pspan * 0.48, min(pspan * 0.48, off))
            self._clear_preview()
            if self._dim.get("mode") == "parallel":
                u = self._dim["u"]
                e1, e2 = self._dim["e1"], self._dim["e2"]
                W = self._dim["W"]
                dist_in = W / self.factor_conversion
                self._commit_dim_item(
                    make_parallel_separation_dimension(
                        e1, e2, u, (nx, ny), W, mx, my, f'{dist_in:.4f}"', pspan, fc, preview=False
                    )
                )
            else:
                p1, p2 = self._dim["p1"], self._dim["p2"]
                L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                dist_in = L / self.factor_conversion
                self._commit_dim_item(
                    make_linear_dimension(p1, p2, nx, ny, off, f'{dist_in:.4f}"', pspan, fc, preview=False)
                )
            self._dim_estado = "idle"
            self._dim = {}
            event.accept()
            return

        if self._dim_estado == "dia_p2":
            cx, cy, r = self._dim["cx"], self._dim["cy"], self._dim["r"]
            ux, uy = self._dim["ux"], self._dim["uy"]
            nx, ny = -uy, ux
            cmx, cmy = self._centro_pieza
            if nx * (cx - cmx) + ny * (cy - cmy) < 0:
                nx, ny = -nx, -ny
            off_n = (mx - cx) * nx + (my - cy) * ny
            off_n = max(-pspan * 0.48, min(pspan * 0.48, off_n))
            self._clear_preview()
            self._commit_dim_item(
                make_diameter_dimension(
                    cx, cy, r, ux, uy, off_n, fc, pspan, self._centro_pieza, preview=False
                )
            )
            self._dim_estado = "idle"
            self._dim = {}
            event.accept()
            return

        if self._dim_estado == "rad_p2":
            cx, cy, r = self._dim["cx"], self._dim["cy"], self._dim["r"]
            rim_x, rim_y = self._dim["rim"]
            ux = (rim_x - cx) / max(r, 1e-12)
            uy = (rim_y - cy) / max(r, 1e-12)
            lu = math.hypot(ux, uy)
            if lu > 1e-12:
                ux, uy = ux / lu, uy / lu
            nx, ny = -uy, ux
            cmx, cmy = self._centro_pieza
            if nx * (cx - cmx) + ny * (cy - cmy) < 0:
                nx, ny = -nx, -ny
            off_n = (mx - cx) * nx + (my - cy) * ny
            off_n = max(-pspan * 0.48, min(pspan * 0.48, off_n))
            self._clear_preview()
            self._commit_dim_item(
                make_radius_dimension(
                    cx, cy, r, rim_x, rim_y, off_n, fc, pspan, self._centro_pieza, preview=False
                )
            )
            self._dim_estado = "idle"
            self._dim = {}
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = False
            self._set_pan_cursor("normal")
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_panning:
            self._user_view_adjusted = True
            delta = event.position() - self._pan_last
            self._pan_last = event.position()
            self.horizontalScrollBar().setValue(
                int(self.horizontalScrollBar().value() - delta.x())
            )
            self.verticalScrollBar().setValue(
                int(self.verticalScrollBar().value() - delta.y())
            )
            event.accept()
            return

        mx, my = self._scene_xy(event)
        snap_span = self._visible_span()
        pspan = self._piece_span()
        fc = self.factor_conversion
        self._clear_snap_overlay()

        if self._dim_estado != "idle":
            sc = snap_cota(mx, my, snap_span, self._snap_ctx)
            for it in make_snap_overlay(sc, snap_span):
                self._scene.addItem(it)
                self._snap_items.append(it)

        self._clear_preview()
        est = self._dim_estado

        if est == "lin_p1":
            p1 = self._dim.get("p1")
            if p1:
                from interface.qt.cad_dimension_items import _line

                rb = _line(p1[0], p1[1], mx, my, True, pspan, fc)
                rb.setZValue(Z_DIM_PREVIEW)
                self._add_preview(rb)

        elif est == "ang_p3":
            vtx = self._dim.get("vtx")
            u1 = self._dim.get("u1")
            u2 = self._dim.get("u2")
            if vtx and u1 and u2:
                self._add_preview(make_angular_dimension(vtx, u1, u2, mx, my, pspan, fc, preview=True))

        elif est == "lin_p2_after_edge":
            nx, ny = self._dim["nx"], self._dim["ny"]
            midx, midy = self._dim["midx"], self._dim["midy"]
            p1, p2 = self._dim["chord_p1"], self._dim["chord_p2"]
            off = (mx - midx) * nx + (my - midy) * ny
            off = max(-pspan * 0.48, min(pspan * 0.48, off))
            L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            dist_in = L / self.factor_conversion
            self._add_preview(
                make_linear_dimension(p1, p2, nx, ny, off, f'{dist_in:.4f}"', pspan, fc, preview=True)
            )

        elif est == "lin_p3":
            nx, ny = self._dim["nx"], self._dim["ny"]
            midx, midy = self._dim["midx"], self._dim["midy"]
            off = (mx - midx) * nx + (my - midy) * ny
            off = max(-pspan * 0.48, min(pspan * 0.48, off))
            if self._dim.get("mode") == "parallel":
                u = self._dim["u"]
                e1, e2 = self._dim["e1"], self._dim["e2"]
                W = self._dim["W"]
                dist_in = W / self.factor_conversion
                self._add_preview(
                    make_parallel_separation_dimension(
                        e1, e2, u, (nx, ny), W, mx, my, f'{dist_in:.4f}"', pspan, fc, preview=True
                    )
                )
            else:
                p1, p2 = self._dim["p1"], self._dim["p2"]
                L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                dist_in = L / self.factor_conversion
                self._add_preview(
                    make_linear_dimension(p1, p2, nx, ny, off, f'{dist_in:.4f}"', pspan, fc, preview=True)
                )

        elif est == "dia_p2":
            cx, cy, r = self._dim["cx"], self._dim["cy"], self._dim["r"]
            ux, uy = self._dim["ux"], self._dim["uy"]
            nx, ny = -uy, ux
            cmx, cmy = self._centro_pieza
            if nx * (cx - cmx) + ny * (cy - cmy) < 0:
                nx, ny = -nx, -ny
            off_n = (mx - cx) * nx + (my - cy) * ny
            off_n = max(-pspan * 0.48, min(pspan * 0.48, off_n))
            self._add_preview(
                make_diameter_dimension(
                    cx, cy, r, ux, uy, off_n, fc, pspan, self._centro_pieza, preview=True
                )
            )

        elif est == "rad_p2":
            cx, cy, r = self._dim["cx"], self._dim["cy"], self._dim["r"]
            rim_x, rim_y = self._dim["rim"]
            ux = (rim_x - cx) / max(r, 1e-12)
            uy = (rim_y - cy) / max(r, 1e-12)
            lu = math.hypot(ux, uy)
            if lu > 1e-12:
                ux, uy = ux / lu, uy / lu
            nx, ny = -uy, ux
            cmx, cmy = self._centro_pieza
            if nx * (cx - cmx) + ny * (cy - cmy) < 0:
                nx, ny = -nx, -ny
            off_n = (mx - cx) * nx + (my - cy) * ny
            off_n = max(-pspan * 0.48, min(pspan * 0.48, off_n))
            self._add_preview(
                make_radius_dimension(
                    cx, cy, r, rim_x, rim_y, off_n, fc, pspan, self._centro_pieza, preview=True
                )
            )

        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._clear_snap_overlay()
        self._clear_preview()
        super().leaveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_rect and not self._user_view_adjusted and self._model is not None:
            QTimer.singleShot(0, self.fit_view)
