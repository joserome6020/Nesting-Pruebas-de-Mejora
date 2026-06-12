# ==========================================
# nesting_canvas.py — PySide6 + QGraphicsView (render CAD premium, geom 1:1 mm)
# ==========================================
import time
import copy

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QBrush, QCursor, QKeyEvent, QMouseEvent, QPen, QTransform, QWheelEvent
from PySide6.QtWidgets import (
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from shapely import wkt as shapely_wkt
from shapely.affinity import translate
from shapely.geometry import Polygon, box, Point, LineString

from interface.qt.nesting_graphics import (
    COLOR_PIECE_EDGE,
    COLOR_PIECE_FILL,
    COLOR_PIECE_SEL,
    COLOR_PIECE_SEL_EDGE,
    NestingDrawParams,
    NestingGraphicsView,
    compute_fit_rect,
    populate_nesting_scene,
)
from interface.qt import visor_diag
from interface.qt.visor_diag import (
    freeze_threshold_ms,
    log_error,
    log_slow,
    log_stuck_state,
    measure,
    ui_busy,
)


class _AxNestShim:
    """Compatibilidad mínima con código que aún referencia ax_nest."""

    def __init__(self, visor):
        self._visor = visor

    def clear(self):
        self._visor.clear_scene()

    def get_xlim(self):
        x0, x1, _, _ = self._visor._view_box
        return (x0, x1)

    def set_xlim(self, lim):
        x0, x1, yb, yt = self._visor._view_box
        self._visor._view_box = (float(lim[0]), float(lim[1]), yb, yt)
        self._visor._apply_view_box()

    def get_ylim(self):
        _, _, yb, yt = self._visor._view_box
        return (yb, yt)

    def set_ylim(self, lim):
        x0, x1, _, _ = self._visor._view_box
        self._visor._view_box = (x0, x1, float(lim[0]), float(lim[1]))
        self._visor._apply_view_box()


class _NestingView(NestingGraphicsView):
    def __init__(self, visor):
        super().__init__(visor)
        self._visor = visor
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)

    def wheelEvent(self, event: QWheelEvent):
        if not self._visor.hoja_actual_data:
            return
        self._visor._zoom_wheel(event)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent):
        self._visor._on_mouse_press(event)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._visor._on_mouse_release(event)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        self._visor._on_mouse_move(event)
        event.accept()

    def leaveEvent(self, event: QEvent):
        self._visor._on_mouse_leave()
        super().leaveEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        self._visor._on_key_press(event)
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent):
        self._visor._on_key_release(event)
        super().keyReleaseEvent(event)

    def draw_idle(self):
        self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._visor._position_coord_hud()


class VisorNesting(QWidget):
    def __init__(self, master, app_principal, callback_seleccion):
        super().__init__(master)
        self.app = app_principal
        self.callback_seleccion = callback_seleccion
        self._timers: dict[int, QTimer] = {}
        self._timer_seq = 0
        
        self.hoja_actual_data = None
        self.clave_actual = ""
        self.idx_pieza_seleccionada = -1
        self.info_pieza_seleccionada = None
        self.piezas_seleccionadas_indices = set()
        
        self._is_panning = False
        self._btn1_down = False
        self._dragging_piece = False
        self._drag_last_data = None
        self._drag_total_dx = 0.0
        self._drag_total_dy = 0.0
        self._drag_marks_base = None
        self._pan_moved = False
        self._pan_last = None
        self._pending_clear_selection = False
        self._space_pan = False
        self._hover_idx = -1
        self._cursor_mode = "normal"  # normal | hover | dragging | panning
        self._manual_piece_indices = []
        self._manual_piece_bounds = {}
        self._obstacle_bounds = {}
        self._view_box = (0.0, 1000.0, -200.0, 1000.0)
        self._scene_meta = {}
        self._piece_gfx: dict[int, list] = {}
        self._drag_visual_offset = (0.0, 0.0)
        self._drag_base_polys: dict[int, Polygon] = {}
        self._hover_check_ts = 0.0
        self._hover_interval_s = 0.12
        self._interaction_mode = "idle"  # idle | pan | drag
        self._scene_rebuilding = False

        self.setup_canvas()
        if visor_diag.enabled() and not getattr(VisorNesting, "_diag_banner", False):
            VisorNesting._diag_banner = True
            visor_diag.freeze_detector()
            visor_diag.log(
                "[VISOR DIAG] Activo: errores, lentitud y trabas del visor en terminal. "
                "Desactivar: ARGA_VISOR_DIAG=0"
            )

    def after(self, ms, callback):
        self._timer_seq += 1
        tid = self._timer_seq
        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(callback)
        t.start(max(0, int(ms)))
        self._timers[tid] = t
        return tid

    def after_cancel(self, timer_id):
        t = self._timers.pop(int(timer_id), None)
        if t:
            t.stop()

    def setup_canvas(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self._scene = QGraphicsScene(self)
        self._view = _NestingView(self)
        self._view.setScene(self._scene)
        self.canvas_widget = self._view
        self.canvas_nest = self._view
        self.ax_nest = _AxNestShim(self)
        lay.addWidget(self._view, 1)

        self._coord_label = QLabel(self._view.viewport())
        self._coord_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._coord_label.setStyleSheet(
            "color:rgba(255,255,255,0.88);font-family:Consolas,monospace;font-size:11px;"
            "background:rgba(15,23,42,0.72);padding:4px 8px;border-radius:6px;"
        )
        self._position_coord_hud()

    def _position_coord_hud(self):
        if not getattr(self, "_coord_label", None):
            return
        self._coord_label.adjustSize()
        vp = self._view.viewport()
        margin = 8
        x = max(margin, vp.width() - self._coord_label.width() - margin)
        self._coord_label.move(x, margin)
        self._coord_label.raise_()

    def clear_scene(self):
        self._scene.clear()
        self.hoja_actual_data = None

    def _apply_view_box(self):
        x0, x1, yb, yt = self._view_box
        rect = QRectF(x0, min(yb, yt), max(1.0, x1 - x0), max(1.0, abs(yt - yb)))
        self._view.setSceneRect(rect.adjusted(-50, -50, 50, 50))
        self._view.resetTransform()
        self._view.scale(1.0, -1.0)
        self._view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def _pan_translate_pixels(self, dx_px: float, dy_px: float):
        """Pan fluido vía transform (estándar CAD/Qt), sin rebuild ni fitInView."""
        p0 = self._view.mapToScene(QPoint(0, 0))
        p1 = self._view.mapToScene(QPoint(int(round(dx_px)), int(round(dy_px))))
        self._view.translate(p1.x() - p0.x(), p1.y() - p0.y())

    def _sync_view_box_from_viewport(self):
        vp = self._view.viewport().rect()
        tl = self._view.mapToScene(vp.topLeft())
        br = self._view.mapToScene(vp.bottomRight())
        self._view_box = (
            float(min(tl.x(), br.x())),
            float(max(tl.x(), br.x())),
            float(min(tl.y(), br.y())),
            float(max(tl.y(), br.y())),
        )

    def _zoom_wheel(self, event: QWheelEvent):
        if event.angleDelta().y() == 0:
            return
        t0 = time.perf_counter()
        try:
            factor = 1.12 if event.angleDelta().y() > 0 else 1.0 / 1.12
            view = self._view
            anchor = view.mapToScene(event.position().toPoint())
            view.scale(factor, factor)
            new_anchor = view.mapToScene(event.position().toPoint())
            delta = new_anchor - anchor
            view.translate(delta.x(), delta.y())
        except Exception as exc:
            log_error("zoom_wheel", exc)
        finally:
            log_slow("zoom_wheel", (time.perf_counter() - t0) * 1000.0)

    def _scene_point(self, event: QMouseEvent):
        sp = self._view.mapToScene(event.position().toPoint())
        return float(sp.x()), float(sp.y())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_coord_hud()
        if self.hoja_actual_data:
            self._apply_view_box()

    def _es_pieza_seleccionable(self, nombre):
        n = str(nombre or "")
        return not (
            n.startswith("REMANENTE__")
            or n.startswith("REF__")
            or n.startswith("RETAZO_")
            or n.startswith("TATUAJE__")
        )

    def _indices_seleccion_activos(self, selected_idx=-1, selected_indices=None):
        if selected_indices is not None:
            return set(selected_indices)
        if self.piezas_seleccionadas_indices:
            return set(self.piezas_seleccionadas_indices)
        if selected_idx >= 0:
            return {selected_idx}
        return set()

    def _piezas_list(self) -> list:
        if not self.hoja_actual_data:
            return []
        piezas = self.hoja_actual_data.get("piezas")
        return piezas if isinstance(piezas, list) else []

    def _pieza_at(self, idx: int):
        piezas = self._piezas_list()
        if not isinstance(idx, int) or idx < 0 or idx >= len(piezas):
            return None
        return piezas[idx]

    def _index_valido(self, idx: int) -> bool:
        return self._pieza_at(idx) is not None

    def _sanitizar_seleccion(self) -> None:
        n = len(self._piezas_list())
        self.piezas_seleccionadas_indices = {
            i for i in self.piezas_seleccionadas_indices if isinstance(i, int) and 0 <= i < n
        }
        if not (0 <= self.idx_pieza_seleccionada < n):
            self.idx_pieza_seleccionada = -1
            self.info_pieza_seleccionada = None

    @property
    def piezas_seleccionadas(self):
        piezas_out = []
        indices = sorted(self.piezas_seleccionadas_indices)
        if not indices and self.idx_pieza_seleccionada >= 0:
            indices = [self.idx_pieza_seleccionada]
        for idx in indices:
            p = self._pieza_at(idx)
            if p and self._es_pieza_seleccionable(p.get("nombre", "")):
                piezas_out.append(p)
        return piezas_out

    def limpiar_seleccion_piezas(self):
        self.piezas_seleccionadas_indices = set()
        self.idx_pieza_seleccionada = -1
        self.info_pieza_seleccionada = None

    def _ctrl_presionado(self, event):
        if isinstance(event, QMouseEvent):
            return bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        key = str(getattr(event, "key", "") or "").lower()
        if "control" in key or "ctrl" in key:
            return True
        gui = getattr(event, "guiEvent", None)
        if gui is not None:
            state = int(getattr(gui, "state", 0) or 0)
            return bool(state & 0x0004)
        return False

    def _indices_para_arrastre(self):
        if self.piezas_seleccionadas_indices:
            indices = []
            for idx in sorted(self.piezas_seleccionadas_indices):
                p = self._pieza_at(idx)
                if p and self._es_pieza_seleccionable(p.get("nombre", "")):
                    indices.append(idx)
            if indices:
                return indices
        if self._index_valido(self.idx_pieza_seleccionada):
            return [self.idx_pieza_seleccionada]
        return []

    def _notificar_seleccion(self):
        piezas = self.piezas_seleccionadas
        self.info_pieza_seleccionada = piezas[-1] if piezas else None
        if piezas:
            self.idx_pieza_seleccionada = max(self.piezas_seleccionadas_indices or {0})
        else:
            self.idx_pieza_seleccionada = -1
        try:
            self.callback_seleccion(self.info_pieza_seleccionada)
        except TypeError:
            self.callback_seleccion()

    def dibujar_hoja_full(
        self,
        hoja,
        clave,
        selected_idx=-1,
        selected_indices=None,
        preserve_view=False,
        drag_preview=False,
    ):
        n_piezas = len(hoja.get("piezas", [])) if hoja else 0
        with ui_busy("dibujar_hoja_full"), measure(
            "dibujar_hoja_full",
            f"piezas={n_piezas} preserve={preserve_view} preview={drag_preview}",
        ):
            self._scene_rebuilding = True
            try:
                if self._dragging_piece or self._is_panning:
                    self._recover_stuck_interaction()
                self._manual_piece_indices = []
                self._manual_piece_bounds = {}
                self._obstacle_bounds = {}
                self._piece_gfx = {}
                self.hoja_actual_data = hoja
                self.clave_actual = clave
                selected_set = self._indices_seleccion_activos(selected_idx, selected_indices)
                saved_transform = QTransform(self._view.transform()) if preserve_view else None
                prev_box = self._view_box if preserve_view else None

                params = NestingDrawParams(
                    hoja=hoja,
                    clave=clave,
                    app=self.app,
                    selected_indices=selected_set,
                    drag_preview=drag_preview,
                )
                self._scene_meta = populate_nesting_scene(
                    self._scene,
                    params,
                    poly_from_pieza=self._poly_from_pieza,
                )
                self._piece_gfx = dict(self._scene_meta.get("piece_gfx") or {})
                with measure("_rebuild_manual_piece_index", f"piezas={n_piezas}"):
                    self._rebuild_manual_piece_index()
                self._sanitizar_seleccion()

                if preserve_view and saved_transform is not None:
                    self._view.setTransform(saved_transform)
                elif preserve_view and prev_box:
                    self._view_box = prev_box
                    self._apply_view_box()
                else:
                    vw = max(1, self._view.width())
                    vh = max(1, self._view.height())
                    x_lo, x_hi, ymin, ymax = compute_fit_rect(hoja, self._scene_meta, vw, vh)
                    self._view_box = (x_lo, x_hi, ymin, ymax)
                    self._apply_view_box()
            finally:
                self._scene_rebuilding = False

    def _on_key_press(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self._recover_stuck_interaction()
            return

        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan = True
            self._set_canvas_cursor("panning")
            return

        if self.idx_pieza_seleccionada == -1 or not self.hoja_actual_data:
            return

        mods = event.modifiers()
        paso_deg = 1.0
        if mods & Qt.KeyboardModifier.ShiftModifier:
            paso_deg = 5.0
        elif mods & Qt.KeyboardModifier.ControlModifier:
            paso_deg = 0.5

        key = event.key()
        if key == Qt.Key.Key_Left:
            self.rotar_pieza_seleccionada(-paso_deg)
        elif key == Qt.Key.Key_Right:
            self.rotar_pieza_seleccionada(paso_deg)
        elif key == Qt.Key.Key_Up:
            self.rotar_pieza_seleccionada(paso_deg * 5.0)
        elif key == Qt.Key.Key_Down:
            self.rotar_pieza_seleccionada(-paso_deg * 5.0)
        elif key in (Qt.Key.Key_R,):
            self.rotar_pieza_seleccionada(90)

    def _on_key_release(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan = False
            if not self._is_panning:
                self._set_canvas_cursor("normal")
            
    def rotar_pieza_seleccionada(self, grados):
        if self.idx_pieza_seleccionada == -1 or not self.hoja_actual_data:
            return
            
        hoja = self.hoja_actual_data
        idx = self.idx_pieza_seleccionada
        pieza_data = self._pieza_at(idx)
        if pieza_data is None:
            return
        w_placa, h_placa = hoja['placa_w'], hoja['placa_h']
        
        poly_actual = self._poly_from_pieza(pieza_data)
        centro = poly_actual.centroid
        
        from shapely.affinity import rotate
        poly_nuevo = rotate(poly_actual, grados, origin=centro)

        clearance = self._clearance_mm(hoja)
        plate_inset = self._plate_inset_mm(hoja)
        caja_util = box(
            plate_inset,
            plate_inset,
            w_placa - plate_inset,
            h_placa - plate_inset,
        )
        if not caja_util.contains(poly_nuevo):
            return

        for i in self._candidate_indices_for_poly(poly_nuevo, clearance, exclude_idx=idx):
            p_otra = self._pieza_at(i)
            if p_otra is None or not self._es_pieza_obstaculo_colision(p_otra.get("nombre")):
                continue
            poly_otro = self._poly_from_pieza(p_otra)
            if not self._collision_allows_move(
                poly_actual,
                poly_nuevo,
                poly_otro,
                clearance,
                nom_otro=str(p_otra.get("nombre", "")),
            ):
                return 

        hoja['piezas'][idx]['poligonos'] = [list(poly_nuevo.exterior.coords)] + [list(hole.coords) for hole in poly_nuevo.interiors]
        hoja['piezas'][idx]['_poly_cache'] = poly_nuevo
        hoja['piezas'][idx]['_bounds_cache'] = poly_nuevo.bounds
        self._manual_piece_bounds[idx] = poly_nuevo.bounds
        self._obstacle_bounds[idx] = poly_nuevo.bounds

        if 'marcas' in pieza_data and pieza_data['marcas']:
            hoja['piezas'][idx]['marcas'] = [list(rotate(LineString(m), grados, origin=centro).coords) for m in pieza_data['marcas']]

        self.dibujar_hoja_full(
            hoja,
            self.clave_actual,
            selected_indices=self.piezas_seleccionadas_indices,
            preserve_view=True,
        )
        self._notificar_cambio_manual()

    @staticmethod
    def _bounds_offset(bounds, dx: float, dy: float):
        return (bounds[0] + dx, bounds[1] + dy, bounds[2] + dx, bounds[3] + dy)

    @staticmethod
    def _aabb_closer_than(a, b, clearance: float) -> bool:
        return not (
            a[2] + clearance < b[0]
            or b[2] + clearance < a[0]
            or a[3] + clearance < b[1]
            or b[3] + clearance < a[1]
        )

    def _es_obstaculo_deslizable_rtz(self, nombre) -> bool:
        n = str(nombre or "")
        return n.startswith("REF__") or n.startswith("RETAZO_GUILLOTINA__")

    def _collision_allows_move(
        self, poly_current, poly_target, poly_otro, clearance: float, *, nom_otro: str = ""
    ) -> bool:
        """
        Piezas reales: separación >= kerf (como el motor C++).
        RTZ/guillotina: permite deslizar por el borde sin penetrar.
        """
        eps = 1e-6
        inter_cur = poly_current.intersection(poly_otro).area
        inter_new = poly_target.intersection(poly_otro).area
        if inter_new > inter_cur + eps:
            return False

        d_new = poly_target.distance(poly_otro)
        if d_new >= clearance - eps:
            return True

        if self._es_obstaculo_deslizable_rtz(nom_otro):
            d_cur = poly_current.distance(poly_otro)
            return d_new + eps >= d_cur

        return False

    def _validate_drag_offset(self, total_dx: float, total_dy: float, indices, *, full: bool = False) -> bool:
        hoja = self.hoja_actual_data
        if not hoja or not indices:
            return False
        w_placa, h_placa = hoja["placa_w"], hoja["placa_h"]
        clearance = self._clearance_mm(hoja)
        plate_inset = self._plate_inset_mm(hoja)
        indices_set = set(indices)
        cur_ox, cur_oy = self._drag_visual_offset
        polys_target = {}
        polys_current = {}

        for idx in indices:
            if not self._index_valido(idx):
                return False
            base = self._drag_base_polys.get(idx)
            if base is None:
                pieza = self._pieza_at(idx)
                if pieza is None:
                    return False
                base = self._poly_from_pieza(pieza)
            poly_target = translate(base, xoff=total_dx, yoff=total_dy)
            caja_util = box(
                plate_inset,
                plate_inset,
                w_placa - plate_inset,
                h_placa - plate_inset,
            )
            if not caja_util.contains(poly_target):
                return False
            polys_target[idx] = poly_target
            polys_current[idx] = translate(base, xoff=cur_ox, yoff=cur_oy)

        piezas = self._piezas_list()
        for idx, poly_target in polys_target.items():
            poly_current = polys_current[idx]
            for other_idx, p_otra in enumerate(piezas):
                if other_idx in indices_set or other_idx == idx:
                    continue
                if p_otra is None or not self._es_pieza_obstaculo_colision(p_otra.get("nombre")):
                    continue
                poly_otro = self._poly_from_pieza(p_otra)
                if not self._collision_allows_move(
                    poly_current,
                    poly_target,
                    poly_otro,
                    clearance,
                    nom_otro=str(p_otra.get("nombre", "")),
                ):
                    return False
        return True

    def _apply_visual_drag_delta(self, dx: float, dy: float) -> None:
        for idx in self._indices_para_arrastre():
            for item in self._piece_gfx.get(idx, []):
                item.moveBy(dx, dy)

    def _try_drag_visual(self, dx: float, dy: float) -> bool:
        indices = self._indices_para_arrastre()
        if not indices:
            return False
        ox, oy = self._drag_visual_offset
        # Movimiento completo, luego por ejes (deslizar a lo largo de piezas/bordes).
        candidates = (
            (ox + dx, oy + dy, dx, dy),
            (ox + dx, oy, dx, 0.0),
            (ox, oy + dy, 0.0, dy),
        )
        for tdx, tdy, adx, ady in candidates:
            if abs(adx) < 1e-12 and abs(ady) < 1e-12:
                continue
            if self._validate_drag_offset(tdx, tdy, indices, full=False):
                self._apply_visual_drag_delta(adx, ady)
                self._drag_visual_offset = (tdx, tdy)
                self._view.viewport().update()
                return True
        return False

    def _revert_visual_drag(self):
        tdx, tdy = self._drag_visual_offset
        if abs(tdx) < 1e-9 and abs(tdy) < 1e-9:
            return
        for idx in self._indices_para_arrastre():
            for item in self._piece_gfx.get(idx, []):
                item.moveBy(-tdx, -tdy)
        self._drag_visual_offset = (0.0, 0.0)

    @staticmethod
    def _main_path_item(items):
        for it in items or []:
            if isinstance(it, QGraphicsPathItem):
                return it
            for ch in getattr(it, "childItems", lambda: [])():
                if isinstance(ch, QGraphicsPathItem):
                    return ch
        return None

    def _apply_selection_gfx(self):
        selected = set(self.piezas_seleccionadas_indices or [])
        if self.idx_pieza_seleccionada >= 0:
            selected.add(self.idx_pieza_seleccionada)
        for idx, items in self._piece_gfx.items():
            main = self._main_path_item(items)
            if main is None:
                continue
            if idx in selected:
                main.setBrush(QBrush(COLOR_PIECE_SEL))
                main.setPen(QPen(COLOR_PIECE_SEL_EDGE, 1.6))
            else:
                main.setBrush(QBrush(COLOR_PIECE_FILL))
                main.setPen(QPen(COLOR_PIECE_EDGE, 0.65))

    def _commit_piece_drag(self) -> bool:
        tdx, tdy = self._drag_visual_offset
        if abs(tdx) < 1e-9 and abs(tdy) < 1e-9:
            return True
        hoja = self.hoja_actual_data
        indices = self._indices_para_arrastre()
        if not hoja or not indices:
            return False
        t0 = time.perf_counter()
        if not self._validate_drag_offset(tdx, tdy, indices, full=True):
            log_error(
                "commit_arrastre",
                RuntimeError(
                    f"Colisión o fuera de placa al soltar (dx={tdx:.2f}mm dy={tdy:.2f}mm)"
                ),
            )
            self._revert_visual_drag()
            return False
        log_slow("validar_commit", (time.perf_counter() - t0) * 1000.0)
        for idx in indices:
            pieza = self._pieza_at(idx)
            base = self._drag_base_polys.get(idx)
            if pieza is None or base is None:
                continue
            poly_nuevo = translate(base, xoff=tdx, yoff=tdy)
            pieza["poligonos"] = [list(poly_nuevo.exterior.coords)] + [
                list(hole.coords) for hole in poly_nuevo.interiors
            ]
            pieza["_poly_cache"] = poly_nuevo
            pieza["_bounds_cache"] = poly_nuevo.bounds
            self._manual_piece_bounds[idx] = poly_nuevo.bounds
            self._obstacle_bounds[idx] = poly_nuevo.bounds
            marcas_base = self._drag_marks_base.get(idx) if isinstance(self._drag_marks_base, dict) else None
            if marcas_base:
                pieza["marcas"] = [
                    list(translate(LineString(m), xoff=tdx, yoff=tdy).coords) for m in marcas_base
                ]
        self._drag_total_dx = tdx
        self._drag_total_dy = tdy
        self._drag_visual_offset = (0.0, 0.0)
        self._drag_base_polys = {}
        return True

    def mover_pieza_seleccionada(self, dx, dy):
        self._try_drag_visual(dx, dy)

    def _es_pieza_manual(self, nombre):
        n = str(nombre or "")
        return not (
            n.startswith("REMANENTE__")
            or n.startswith("REF__")
            or n.startswith("RETAZO_")
            or n.startswith("TATUAJE__")
        )

    def _es_pieza_obstaculo_colision(self, nombre):
        """
        Obstáculos reales de colisión: piezas manuales, RTZ (REF__) y guillotina.
        REMANENTE__ es solo visual (zona reservada); TATUAJE__ es marcaje.
        """
        n = str(nombre or "")
        return not (n.startswith("TATUAJE__") or n.startswith("REMANENTE__"))

    def _pieza_en_punto(self, x, y, *, solo_interactivas: bool = True):
        """Hit-test en mm. Ignora REMANENTE/REF/TATUAJE (cubren la placa pero no se arrastran)."""
        if x is None or y is None or self._scene_rebuilding or not self.hoja_actual_data:
            return -1
        pt = Point(x, y)
        candidates = self._candidate_indices_for_point(x, y)
        for idx in reversed(candidates):
            p = self._pieza_at(idx)
            if p is None:
                continue
            if solo_interactivas and not self._es_pieza_seleccionable(p.get("nombre", "")):
                continue
            try:
                if self._poly_from_pieza(p).contains(pt):
                    return idx
            except Exception as exc:
                log_error(f"hit_test idx={idx}", exc)
        return -1

    def _normalizar_anillo_para_poly(self, ring):
        if not ring:
            return []
        out = []
        for pt in ring:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                out.append((float(pt[0]), float(pt[1])))
            elif isinstance(pt, dict):
                x = pt.get("x", pt.get("X"))
                y = pt.get("y", pt.get("Y"))
                if x is not None and y is not None:
                    out.append((float(x), float(y)))
        return out

    def _poly_from_pieza(self, pieza):
        poly = pieza.get("_poly_cache")
        if isinstance(poly, Polygon):
            return poly
        if isinstance(poly, str) and poly.strip():
            try:
                geom = shapely_wkt.loads(poly)
                if isinstance(geom, Polygon) and not geom.is_empty:
                    if not geom.is_valid:
                        geom = geom.buffer(0)
                    pieza["_poly_cache"] = geom
                    pieza["_bounds_cache"] = geom.bounds
                    return geom
            except Exception:
                pass
        pieza.pop("_poly_cache", None)
        pieza.pop("_bounds_cache", None)

        pols = pieza.get("poligonos") or []
        if not pols or not pols[0]:
            return Polygon()
        outer = self._normalizar_anillo_para_poly(pols[0])
        holes = [self._normalizar_anillo_para_poly(h) for h in pols[1:]]
        holes = [h for h in holes if len(h) >= 3]
        if len(outer) < 3:
            return Polygon()
        poly = Polygon(outer, holes)
        if not poly.is_valid:
            poly = poly.buffer(0)
        pieza["_poly_cache"] = poly
        pieza["_bounds_cache"] = poly.bounds
        return poly

    def _rebuild_manual_piece_index(self):
        self._manual_piece_indices = []
        self._manual_piece_bounds = {}
        self._obstacle_bounds = {}
        if not self.hoja_actual_data:
            return
        for idx, p in enumerate(self.hoja_actual_data.get("piezas", [])):
            nom = p.get("nombre")
            poly = self._poly_from_pieza(p)
            bounds = p.get("_bounds_cache") or poly.bounds
            if self._es_pieza_obstaculo_colision(nom):
                self._obstacle_bounds[idx] = bounds
            if not self._es_pieza_manual(nom):
                continue
            self._manual_piece_indices.append(idx)
            self._manual_piece_bounds[idx] = bounds

    def _candidate_indices_for_point(self, x, y):
        out = []
        n = len(self._piezas_list())
        for idx in self._manual_piece_indices:
            if idx < 0 or idx >= n:
                continue
            b = self._manual_piece_bounds.get(idx)
            if not b or len(b) < 4:
                continue
            if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
                out.append(idx)
        return out

    def _candidate_indices_for_poly(self, poly_nuevo, clearance, exclude_idx=-1):
        b = poly_nuevo.bounds
        x0 = b[0] - clearance
        y0 = b[1] - clearance
        x1 = b[2] + clearance
        y1 = b[3] + clearance
        out = []
        for idx, b2 in self._obstacle_bounds.items():
            if idx == exclude_idx or not b2:
                continue
            if b2[2] < x0 or b2[0] > x1 or b2[3] < y0 or b2[1] > y1:
                continue
            out.append(idx)
        return out

    def _clearance_mm(self, hoja):
        # Entre piezas: kerf completo (2 × kerf_radio del motor C++).
        return float(hoja.get("kerf_usado", 0.3) or 0.3) * 25.4

    def _plate_margin_mm(self, hoja):
        return float(hoja.get("margin_usado", 0.15) or 0.15) * 25.4

    def _plate_inset_mm(self, hoja):
        """
        Distancia mínima del contorno real de la pieza al borde de la placa madre.
        Replica el criterio del packer C++: bbox con buffer kerf/2 dentro del margen global.
        """
        return self._plate_margin_mm(hoja) + self._clearance_mm(hoja) * 0.5

    def _set_canvas_cursor(self, mode):
        if mode == self._cursor_mode:
            return
        self._cursor_mode = mode
        if mode == "hover":
            self.canvas_widget.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        elif mode in ("dragging", "panning"):
            self.canvas_widget.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        else:
            self.canvas_widget.unsetCursor()

    def _notificar_cambio_manual(self):
        try:
            vista = getattr(self.app, "vista_nesting", None)
            if vista and hasattr(vista, "_replicar_lote_activo_a_gemelos"):
                vista._replicar_lote_activo_a_gemelos()
        except Exception:
            pass

    def _start_pan(self, px: float, py: float):
        self._interaction_mode = "pan"
        self._is_panning = True
        self._pan_moved = False
        self._pan_last = (px, py)
        self._set_canvas_cursor("panning")
        self._view.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.NoViewportUpdate
        )

    def _recover_stuck_interaction(self):
        """Restaura estado si pan/arrastre quedó colgado (sin grabMouse para no bloquear eventos)."""
        was_stuck = self._is_panning or self._dragging_piece or self._btn1_down
        if not was_stuck:
            return
        log_stuck_state(
            f"reset interacción (pan={self._is_panning} drag={self._dragging_piece} btn1={self._btn1_down})"
        )
        if self._dragging_piece and (
            abs(self._drag_visual_offset[0]) > 1e-9 or abs(self._drag_visual_offset[1]) > 1e-9
        ):
            self._revert_visual_drag()
        self._is_panning = False
        self._dragging_piece = False
        self._btn1_down = False
        self._pan_last = None
        self._pan_moved = False
        self._drag_last_data = None
        self._drag_base_polys = {}
        self._drag_marks_base = None
        self._interaction_mode = "idle"
        self._set_canvas_cursor("normal")
        self._view.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate
        )
        self._view.viewport().update()

    def _on_mouse_press(self, event: QMouseEvent):
        t0 = time.perf_counter()
        with ui_busy("mouse_press"):
            try:
                self._on_mouse_press_impl(event)
            except Exception as exc:
                log_error("mouse_press", exc)
        log_slow("mouse_press", (time.perf_counter() - t0) * 1000.0, self._interaction_mode, interaction=True)

    def _on_mouse_press_impl(self, event: QMouseEvent):
        if self._scene_rebuilding:
            return
        self._view.setFocus()
        xdata, ydata = self._scene_point(event)
        px = event.position().x()
        py = event.position().y()

        if event.button() == Qt.MouseButton.RightButton:
            idx = self._pieza_en_punto(xdata, ydata)
            if idx >= 0:
                pieza = self._pieza_at(idx)
                if pieza is None:
                    return
                self.idx_pieza_seleccionada = idx
                self.info_pieza_seleccionada = pieza
                self.callback_seleccion(self.info_pieza_seleccionada)
                self.rotar_pieza_seleccionada(90)
            return

        if event.button() == Qt.MouseButton.MiddleButton:
            self._start_pan(px, py)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            self._btn1_down = True
            self._dragging_piece = False
            self._drag_last_data = None
            self._pending_clear_selection = False
            if not self.hoja_actual_data:
                return

            if self._space_pan:
                self._start_pan(px, py)
                return

            pieza_tocada = self._pieza_en_punto(xdata, ydata)
            ctrl = self._ctrl_presionado(event)

            if pieza_tocada >= 0 and ctrl:
                pieza_ctrl = self._pieza_at(pieza_tocada)
                if pieza_ctrl is None:
                    return
                nombre = pieza_ctrl.get("nombre", "")
                if self._es_pieza_seleccionable(nombre):
                    if pieza_tocada in self.piezas_seleccionadas_indices:
                        self.piezas_seleccionadas_indices.discard(pieza_tocada)
                    else:
                        self.piezas_seleccionadas_indices.add(pieza_tocada)
                self._is_panning = False
                self._dragging_piece = False
                self._notificar_seleccion()
                self._apply_selection_gfx()
                return

            if pieza_tocada >= 0:
                pieza_hit = self._pieza_at(pieza_tocada)
                if pieza_hit is None:
                    return
                nombre = pieza_hit.get("nombre", "")
                if self._es_pieza_seleccionable(nombre):
                    en_grupo = (
                        pieza_tocada in self.piezas_seleccionadas_indices
                        and len(self.piezas_seleccionadas_indices) > 1
                    )
                    if not en_grupo:
                        self.piezas_seleccionadas_indices = {pieza_tocada}
                else:
                    self.piezas_seleccionadas_indices = set()
                self.idx_pieza_seleccionada = pieza_tocada
                self._is_panning = False
                self._dragging_piece = True
                self._interaction_mode = "drag"
                self._drag_last_data = (xdata, ydata)
                self._view.setViewportUpdateMode(
                    QGraphicsView.ViewportUpdateMode.NoViewportUpdate
                )
                self._drag_total_dx = 0.0
                self._drag_total_dy = 0.0
                self._drag_visual_offset = (0.0, 0.0)
                self._drag_base_polys = {}
                self._drag_marks_base = {}
                for idx_drag in self._indices_para_arrastre():
                    pieza_drag = self._pieza_at(idx_drag)
                    if pieza_drag is None:
                        continue
                    self._drag_base_polys[idx_drag] = self._poly_from_pieza(pieza_drag)
                    marcas0 = pieza_drag.get("marcas", [])
                    self._drag_marks_base[idx_drag] = copy.deepcopy(marcas0) if marcas0 else []
                self._set_canvas_cursor("dragging")
                self._notificar_seleccion()
                self._apply_selection_gfx()
                return

            self._pending_clear_selection = True
            self._start_pan(px, py)

    def _on_mouse_release(self, event: QMouseEvent):
        t0 = time.perf_counter()
        try:
            self._on_mouse_release_impl(event)
        except Exception as exc:
            log_error("mouse_release", exc)
        finally:
            log_slow("mouse_release", (time.perf_counter() - t0) * 1000.0, self._interaction_mode)
            self._interaction_mode = "idle"

    def _on_mouse_release_impl(self, event: QMouseEvent):
        self._view.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate
        )
        btn = event.button()
        was_pan = self._is_panning and self._pan_moved
        piece_drag = self._dragging_piece

        if btn in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._btn1_down = False
            if piece_drag:
                self._dragging_piece = False
                self._drag_last_data = None
                moved = abs(self._drag_visual_offset[0]) > 1e-9 or abs(self._drag_visual_offset[1]) > 1e-9
                committed = self._commit_piece_drag() if moved else True
                self._drag_marks_base = None
                if moved and committed and self.hoja_actual_data:
                    self._rebuild_manual_piece_index()
                    self._apply_selection_gfx()
                    self._view.viewport().update()
                    self._notificar_cambio_manual()
                elif self.hoja_actual_data:
                    self._apply_selection_gfx()
            elif (
                btn == Qt.MouseButton.LeftButton
                and self._pending_clear_selection
                and not was_pan
                and self.hoja_actual_data
            ):
                self.limpiar_seleccion_piezas()
                self._notificar_seleccion()
                self.dibujar_hoja_full(
                    self.hoja_actual_data,
                    self.clave_actual,
                    selected_indices=self.piezas_seleccionadas_indices,
                    preserve_view=True,
                )

        if was_pan:
            self._sync_view_box_from_viewport()
        self._is_panning = False
        self._pan_moved = False
        self._pan_last = None
        self._pending_clear_selection = False
        self._set_canvas_cursor("normal")

    def _on_mouse_move(self, event: QMouseEvent):
        label = f"mouse_move.{self._interaction_mode}"
        t0 = time.perf_counter()
        try:
            self._on_mouse_move_impl(event)
        except Exception as exc:
            log_error("mouse_move", exc)
        elapsed = (time.perf_counter() - t0) * 1000.0
        if self._interaction_mode == "pan":
            log_slow(label, elapsed, interaction=True)
            if elapsed >= freeze_threshold_ms():
                self._recover_stuck_interaction()
        elif self._interaction_mode == "drag" and elapsed >= 120:
            log_slow(label, elapsed, interaction=True)

    def _on_mouse_move_impl(self, event: QMouseEvent):
        if self._scene_rebuilding:
            return
        xdata, ydata = self._scene_point(event)
        in_x = xdata / 25.4
        in_y = ydata / 25.4
        self._coord_label.setText(
            f'X: {in_x:.3f}" ({xdata:.1f} mm) | Y: {in_y:.3f}" ({ydata:.1f} mm)'
        )

        if (
            self._dragging_piece
            and self._btn1_down
            and self.idx_pieza_seleccionada >= 0
            and self._drag_last_data is not None
        ):
            dx = xdata - self._drag_last_data[0]
            dy = ydata - self._drag_last_data[1]
            if dx != 0 or dy != 0:
                if self._try_drag_visual(dx, dy):
                    self._drag_last_data = (xdata, ydata)
            self._set_canvas_cursor("dragging")
        elif self._is_panning and not self._dragging_piece and self._pan_last is not None and (
            event.buttons() & Qt.MouseButton.LeftButton
            or event.buttons() & Qt.MouseButton.MiddleButton
        ):
            px, py = event.position().x(), event.position().y()
            dx_px = px - self._pan_last[0]
            dy_px = py - self._pan_last[1]
            if abs(dx_px) > 0.5 or abs(dy_px) > 0.5:
                self._pan_moved = True
                self._pan_translate_pixels(dx_px, dy_px)
                self._pan_last = (px, py)
                self._view.viewport().update()
            self._set_canvas_cursor("panning")
        elif self.hoja_actual_data:
            now = time.perf_counter()
            if now - self._hover_check_ts >= self._hover_interval_s:
                self._hover_check_ts = now
                idx_hover = self._pieza_en_punto(xdata, ydata)
                if idx_hover != self._hover_idx:
                    self._hover_idx = idx_hover
                    self._set_canvas_cursor("hover" if idx_hover >= 0 else "normal")
        else:
            self._hover_idx = -1
            self._set_canvas_cursor("normal")

    def _on_mouse_leave(self):
        if not (self._is_panning or self._dragging_piece):
            self._coord_label.setText("")
            self._hover_idx = -1
            if not self._btn1_down:
                self._set_canvas_cursor("normal")