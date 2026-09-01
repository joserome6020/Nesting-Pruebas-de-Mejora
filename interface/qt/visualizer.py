"""Visor CAD de pieza (QGraphicsView + ezdxf PyQtBackend) y thumbnail para listados."""
from __future__ import annotations

import io
import os
import threading

import ezdxf
import matplotlib

matplotlib.use("Agg")

from ezdxf import path
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Circle, Polygon
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from interface.qt.cad_graphics_view import CadPartGraphicsView
from interface.qt.dxf_part_loader import load_dxf_part
from interface.qt.layout_helpers import make_hscroll_toolbar
from interface.qt.thread_bridge import call_on_main
from interface.qt.theme import apply_push_button, COLOR_GRIS_DARK, TOOLTIP_OSCURO_QSS
from interface.qt.ui_scale import s

CAD_VIEW_BG = "#0B1220"
CAD_PIECE_EDGE = "#475569"


class VisorDXF:
    """Shell Qt: toolbar, visor QGraphics y panel de métricas."""

    def __init__(self, master_frame):
        master_lay = master_frame.layout()
        if master_lay is None:
            master_lay = QVBoxLayout(master_frame)
            master_lay.setContentsMargins(0, 0, 0, 0)

        self.frame_seccion_2 = QWidget()
        master_lay.addWidget(self.frame_seccion_2, 1)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#334155;border:none;")
        master_lay.addWidget(sep)

        self.frame_seccion_3 = QFrame()
        self.frame_seccion_3.setObjectName("VisorInfoPanel")
        self.frame_seccion_3.setFixedHeight(s(88, min_px=76))
        master_lay.addWidget(self.frame_seccion_3)

        sec2_lay = QVBoxLayout(self.frame_seccion_2)
        sec2_lay.setContentsMargins(0, 0, 0, 0)
        sec2_lay.setSpacing(0)

        toolbar_scroll, toolbar, tb_lay = make_hscroll_toolbar(
            height_design=44,
            min_height=38,
            object_name="VisorToolbarScroll",
        )
        toolbar.setStyleSheet("background:#0F172A;border-bottom:1px solid #334155;")
        toolbar_scroll.setStyleSheet(
            "QScrollArea#VisorToolbarScroll{background:#0F172A;border-bottom:1px solid #334155;}"
        )

        btn_fit = QPushButton("AJUSTAR VISTA")
        btn_fit.setFixedHeight(s(30, min_px=26))
        apply_push_button(
            btn_fit,
            COLOR_GRIS_DARK,
            font_size=s(10, min_px=9),
            padding=f"{s(4, min_px=3)}px {s(12, min_px=8)}px",
        )
        btn_fit.clicked.connect(self.ajustar_vista)
        tb_lay.addWidget(btn_fit)

        btn_rot = QPushButton("ROTAR 90°")
        btn_rot.setFixedHeight(s(30, min_px=26))
        apply_push_button(
            btn_rot,
            "#334155",
            font_size=s(10, min_px=9),
            padding=f"{s(4, min_px=3)}px {s(12, min_px=8)}px",
        )
        btn_rot.clicked.connect(self.rotar_vista_90)
        tb_lay.addWidget(btn_rot)

        lbl_hint = QLabel(
            "CLIC: COTA  ·  RUEDA: ZOOM  ·  CENTRAL: PAN  ·  DER: ROTAR  ·  ESC: CANCELAR"
        )
        lbl_hint.setStyleSheet(
            f"color:#64748B;font-size:{s(10, min_px=9)}px;background:transparent;"
        )
        lbl_hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        lbl_hint.setMinimumWidth(s(280, min_px=180))
        tb_lay.addWidget(lbl_hint)
        toolbar.adjustSize()
        toolbar_scroll.setWidget(toolbar)

        sec2_lay.addWidget(toolbar_scroll)

        self._cad = CadPartGraphicsView()
        self._cad.metrics_callback = self.actualizar_datos
        self._cad.rotate_requested.connect(self.rotar_vista_90)
        self.widget = self._cad
        sec2_lay.addWidget(self._cad, 1)

        self.factor_conversion = 25.4
        self._ruta_actual = None
        self._render_token = 0
        self._rotacion_vista_deg = 0
        self._persist_rotation_hook = None
        self._orientation_lock_hook = None
        self._material = ""
        self._plasma_offset_mm = 0.0
        self._plasma_base_metrics = None
        # Recuerdo del énfasis plasma (DXF ya compensado): el ROTAR limpia la
        # escena y reinstancia el modelo — sin estos, el highlight rojo del OUTER
        # y la etiqueta "+X"" desaparecían y parecía que se perdía el offset.
        self._plasma_emphasis_on = False
        self._plasma_emphasis_offset_in: float | None = None

        self.construir_tabla_3_columnas()
        self.mostrar_patron_prueba()

    def set_persist_rotation_hook(self, hook):
        self._persist_rotation_hook = hook

    def set_orientation_lock_hook(self, hook):
        self._orientation_lock_hook = hook

    def set_orientation_lock_checked(self, checked: bool):
        if not hasattr(self, "chk_orientacion_corte"):
            return
        self.chk_orientacion_corte.blockSignals(True)
        self.chk_orientacion_corte.setChecked(bool(checked))
        self.chk_orientacion_corte.blockSignals(False)

    def orientation_lock_checked(self) -> bool:
        if not hasattr(self, "chk_orientacion_corte"):
            return False
        return bool(self.chk_orientacion_corte.isChecked())

    def rotacion_vista_deg(self) -> int:
        return int(getattr(self, "_rotacion_vista_deg", 0) or 0) % 360

    def set_material(self, material: str | None = None):
        self._material = str(material or "").strip()
        self._cad.set_material(self._material)

    def construir_tabla_3_columnas(self):
        # El tooltip se declara aquí también: sobre panel oscuro heredaba el texto
        # oscuro del tema claro y quedaba ilegible.
        self.frame_seccion_3.setStyleSheet(
            "QFrame#VisorInfoPanel{background:#0F172A;border:none;}"
            + TOOLTIP_OSCURO_QSS
        )
        outer = QVBoxLayout(self.frame_seccion_3)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        metrics_scroll = QScrollArea()
        metrics_scroll.setObjectName("VisorMetricsScroll")
        metrics_scroll.setFrameShape(QFrame.Shape.NoFrame)
        metrics_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        metrics_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        metrics_scroll.setWidgetResizable(False)
        metrics_scroll.setStyleSheet(
            "QScrollArea#VisorMetricsScroll{background:#0F172A;border:none;}"
            "QScrollArea#VisorMetricsScroll QScrollBar:horizontal{"
            "background:#0F172A;height:6px;border:none;}"
            "QScrollArea#VisorMetricsScroll QScrollBar::handle:horizontal{"
            "background:#475569;border-radius:3px;min-width:20px;}"
        )

        metrics_host = QWidget()
        metrics_host.setStyleSheet("background:#0F172A;")
        row = QHBoxLayout(metrics_host)
        row.setContentsMargins(
            s(14, min_px=10),
            s(10, min_px=8),
            s(14, min_px=10),
            s(10, min_px=8),
        )
        row.setSpacing(s(16, min_px=10))
        row.setSizeConstraint(QHBoxLayout.SizeConstraint.SetFixedSize)

        def _metric(caption: str, attr_name: str):
            wrap = QWidget()
            wrap.setStyleSheet("background:transparent;")
            # Tamaño natural: evita que LARGO/ANCHO/etc. se estiren a todo el ancho.
            wrap.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(1)
            cap = QLabel(caption)
            cap.setStyleSheet(
                f"color:#64748B;font-size:{s(10, min_px=9)}px;font-weight:700;background:transparent;"
            )
            val = QLabel("-")
            val.setStyleSheet(
                f"color:#E2E8F0;font-size:{s(13, min_px=11)}px;font-weight:700;background:transparent;"
            )
            val.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            val.setMinimumWidth(s(72, min_px=56))
            wl.addWidget(cap)
            wl.addWidget(val)
            setattr(self, attr_name, val)
            row.addWidget(wrap, 0)

        _metric("LARGO (X)", "lbl_width")
        _metric("ANCHO (Y)", "lbl_height")
        _metric("AREA NETA", "lbl_area")
        _metric("PERIMETRO", "lbl_perim")
        _metric("REFERENCIA", "lbl_ref")
        self.lbl_ref.setMinimumWidth(s(120, min_px=90))

        plasma_wrap = QWidget()
        plasma_wrap.setStyleSheet("background:transparent;")
        plasma_wrap.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        pl = QVBoxLayout(plasma_wrap)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(1)
        self.lbl_plasma_cap = QLabel("PLASMA")
        self.lbl_plasma_cap.setStyleSheet(
            f"color:#64748B;font-size:{s(10, min_px=9)}px;font-weight:700;background:transparent;"
        )
        self.lbl_plasma = QLabel("—")
        self.lbl_plasma.setStyleSheet(
            f"color:#FCA5A5;font-size:{s(13, min_px=11)}px;font-weight:700;background:transparent;"
        )
        self.lbl_plasma.setMinimumWidth(s(100, min_px=72))
        pl.addWidget(self.lbl_plasma_cap)
        pl.addWidget(self.lbl_plasma)
        row.addWidget(plasma_wrap, 0)

        lock_wrap = QWidget()
        lock_wrap.setStyleSheet("background:transparent;")
        lock_wrap.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        ll = QVBoxLayout(lock_wrap)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(1)
        self.lbl_orientacion_cap = QLabel("ORIENTACIÓN")
        self.lbl_orientacion_cap.setStyleSheet(
            f"color:#64748B;font-size:{s(10, min_px=9)}px;font-weight:700;background:transparent;"
        )
        self.chk_orientacion_corte = QCheckBox("BLOQUEAR ORIENTACIÓN DE CORTE")
        self.chk_orientacion_corte.setStyleSheet(
            f"QCheckBox{{color:#E2E8F0;font-size:{s(11, min_px=9)}px;font-weight:700;background:transparent;}}"
            f"QCheckBox::indicator{{width:{s(14, min_px=12)}px;height:{s(14, min_px=12)}px;}}"
            + TOOLTIP_OSCURO_QSS
        )
        self.chk_orientacion_corte.setToolTip(
            "Si está activo, el nesting solo podrá usar la orientación visible "
            "(incluida la de ROTAR 90°). Al desmarcar, vuelven las rotaciones normales."
        )
        self.chk_orientacion_corte.toggled.connect(self._on_orientation_lock_toggled)
        ll.addWidget(self.lbl_orientacion_cap)
        ll.addWidget(self.chk_orientacion_corte)
        row.addWidget(lock_wrap, 0)

        metrics_host.adjustSize()
        metrics_scroll.setWidget(metrics_host)
        outer.addWidget(metrics_scroll, 1)

    def _on_orientation_lock_toggled(self, checked: bool):
        if callable(self._orientation_lock_hook):
            try:
                self._orientation_lock_hook(bool(checked), self._ruta_actual)
            except Exception:
                pass

    def actualizar_datos(self, min_x, max_x, min_y, max_y, perimetro, valido, area=None, referencia=""):
        if not valido:
            return
        self._plasma_base_metrics = {
            "min_x": float(min_x),
            "max_x": float(max_x),
            "min_y": float(min_y),
            "max_y": float(max_y),
            "perimetro": float(perimetro or 0.0),
            "area": float(area) if area is not None else None,
            "referencia": str(referencia or ""),
        }
        self._pintar_metricas(
            min_x, max_x, min_y, max_y, perimetro, area=area, referencia=referencia
        )
        if self._plasma_offset_mm > 0:
            self._reaplicar_overlay_plasma()

    def _pintar_metricas(
        self, min_x, max_x, min_y, max_y, perimetro, *, area=None, referencia="", compensada=False
    ):
        ancho_in = abs(max_x - min_x) / self.factor_conversion
        alto_in = abs(max_y - min_y) / self.factor_conversion
        perim_in = perimetro / self.factor_conversion
        area_in2 = (float(area) / (self.factor_conversion**2)) if area is not None else 0.0
        color = "#FCA5A5" if compensada else "#E2E8F0"
        for lbl in (self.lbl_width, self.lbl_height, self.lbl_area, self.lbl_perim):
            lbl.setStyleSheet(
                f"color:{color};font-size:13px;font-weight:700;background:transparent;"
            )
        self.lbl_width.setText(f'{round(ancho_in, 3):.3f}"')
        self.lbl_height.setText(f'{round(alto_in, 3):.3f}"')
        self.lbl_area.setText(f"{round(area_in2, 3):.3f} in²")
        self.lbl_perim.setText(f'{round(perim_in, 3):.3f}"')
        if referencia:
            self.lbl_ref.setText(str(referencia or "-"))

    def _reaplicar_overlay_plasma(self):
        off = float(self._plasma_offset_mm or 0.0)
        if off <= 0:
            self._cad.clear_plasma_overlay()
            if hasattr(self, "lbl_plasma"):
                self.lbl_plasma.setText("—")
                self.lbl_plasma.setStyleSheet(
                    "color:#64748B;font-size:13px;font-weight:700;background:transparent;"
                )
            return
        off_in = off / 25.4
        meta = self._cad.set_plasma_overlay(
            off,
            label=f"COMPENSADA +{off_in:.4f}\"",
        )
        if hasattr(self, "lbl_plasma"):
            self.lbl_plasma.setText(f'+{off_in:.4f}"')
            self.lbl_plasma.setStyleSheet(
                "color:#FCA5A5;font-size:13px;font-weight:700;background:transparent;"
            )
        if meta:
            self._pintar_metricas(
                meta["min_x"],
                meta["max_x"],
                meta["min_y"],
                meta["max_y"],
                meta["perimetro"],
                area=meta["area"],
                compensada=True,
            )

    def set_plasma_contour_emphasis(self, activo: bool, *, offset_in: float | None = None):
        """Resalta OUTER en rojo (pieza plasma). No altera la geometría del DXF.

        Se persiste el estado para que ``renderizar_dxf`` (llamado desde
        ROTAR 90°) lo reaplique tras recargar el modelo. Antes se perdía y
        parecía que el offset se había ido: la geometría seguía compensada
        en disco pero visualmente ya no había marca roja ni ``+X"`` en el
        panel inferior.
        """
        if not activo:
            self._plasma_emphasis_on = False
            self._plasma_emphasis_offset_in = None
            self._cad.clear_plasma_overlay()
            if hasattr(self, "lbl_plasma"):
                self.lbl_plasma.setText("—")
                self.lbl_plasma.setStyleSheet(
                    "color:#64748B;font-size:13px;font-weight:700;background:transparent;"
                )
            return
        label = None
        if offset_in is not None and float(offset_in) > 0:
            label = f"COMPENSADA +{float(offset_in):.4f}\""
            if hasattr(self, "lbl_plasma"):
                self.lbl_plasma.setText(f'+{float(offset_in):.4f}"')
                self.lbl_plasma.setStyleSheet(
                    "color:#FCA5A5;font-size:13px;font-weight:700;background:transparent;"
                )
        self._plasma_emphasis_on = True
        self._plasma_emphasis_offset_in = (
            float(offset_in) if offset_in is not None and float(offset_in) > 0 else None
        )
        self._cad.emphasize_plasma_outers(label=label)

    def set_plasma_offset_mm(self, offset_mm: float | None):
        self._plasma_offset_mm = float(offset_mm or 0.0)
        self._reaplicar_overlay_plasma()
        if self._plasma_offset_mm <= 0 and self._plasma_base_metrics:
            m = self._plasma_base_metrics
            self._pintar_metricas(
                m["min_x"],
                m["max_x"],
                m["min_y"],
                m["max_y"],
                m["perimetro"],
                area=m["area"],
                referencia=m.get("referencia") or "",
                compensada=False,
            )

    def actualizar_info_extra(self, area_in2=None, referencia=None):
        if area_in2 is not None:
            self.lbl_area.setText(f"{round(float(area_in2), 3):.3f} in²")
        if referencia is not None:
            self.lbl_ref.setText(str(referencia or "-"))

    def limpiar_lienzo(self):
        self._cad.clear()

    def mostrar_patron_prueba(self):
        self._cad.show_placeholder()
        self.lbl_width.setText("-")
        self.lbl_height.setText("-")
        self.lbl_area.setText("-")
        self.lbl_perim.setText("-")
        self.lbl_ref.setText("-")

    def _snapshot_metricas_ui(self):
        return (
            self.lbl_width.text(),
            self.lbl_height.text(),
            self.lbl_area.text(),
            self.lbl_perim.text(),
            self.lbl_ref.text(),
        )

    def _restaurar_metricas_ui(self, snap):
        self.lbl_width.setText(snap[0])
        self.lbl_height.setText(snap[1])
        self.lbl_area.setText(snap[2])
        self.lbl_perim.setText(snap[3])
        self.lbl_ref.setText(snap[4])

    def ajustar_vista(self):
        self._cad.fit_view()

    def rotar_vista_90(self):
        if not self._ruta_actual:
            return
        snap = self._snapshot_metricas_ui()
        self._rotacion_vista_deg = (self._rotacion_vista_deg + 90) % 360
        if callable(self._persist_rotation_hook):
            try:
                self._persist_rotation_hook(self._rotacion_vista_deg, self._ruta_actual)
            except Exception:
                pass
        self.renderizar_dxf(self._ruta_actual)
        self._restaurar_metricas_ui(snap)

    def renderizar_dxf(self, ruta_dxf, rotacion_vista_deg=None, plasma_offset_mm=None):
        self._render_token = int(getattr(self, "_render_token", 0)) + 1
        token = self._render_token
        ruta = str(ruta_dxf or "")
        cambio_pieza = bool(self._ruta_actual) and str(self._ruta_actual) != ruta
        if rotacion_vista_deg is not None:
            self._rotacion_vista_deg = int(rotacion_vista_deg) % 360
        elif cambio_pieza:
            self._rotacion_vista_deg = 0
        if plasma_offset_mm is not None:
            self._plasma_offset_mm = float(plasma_offset_mm or 0.0)
        self._ruta_actual = ruta_dxf
        if cambio_pieza:
            self._plasma_emphasis_on = False
            self._plasma_emphasis_offset_in = None

        self.limpiar_lienzo()
        if ruta and os.path.isfile(ruta):
            self._cad.show_placeholder("Cargando vista…")

        def _worker():
            model = None
            try:
                if ruta and os.path.isfile(ruta):
                    model = load_dxf_part(ruta, self._rotacion_vista_deg)
            except Exception:
                model = None
            call_on_main(
                self._aplicar_render_dxf,
                token,
                ruta_dxf,
                model,
                plasma_offset_mm,
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _aplicar_render_dxf(self, token, ruta_dxf, model, plasma_offset_mm=None):
        if token != getattr(self, "_render_token", 0):
            return
        self.limpiar_lienzo()
        try:
            if model is None:
                return False
            self._cad.set_material(self._material)
            self.factor_conversion = model.factor_conversion
            self._cad.load_model(model, fit=True)
            if self._plasma_offset_mm > 0:
                self._reaplicar_overlay_plasma()
            elif self._plasma_emphasis_on:
                self.set_plasma_contour_emphasis(
                    True, offset_in=self._plasma_emphasis_offset_in
                )
            else:
                self._cad.clear_plasma_overlay()
                if hasattr(self, "lbl_plasma"):
                    self.lbl_plasma.setText("—")
            return True
        except Exception:
            return False

    def _renderizar_dxf_sync(self, ruta_dxf, rotacion_vista_deg=None, plasma_offset_mm=None):
        """Ruta síncrona legacy (p. ej. tests)."""
        self.limpiar_lienzo()
        try:
            if self._ruta_actual and str(self._ruta_actual) != str(ruta_dxf):
                if rotacion_vista_deg is not None:
                    self._rotacion_vista_deg = int(rotacion_vista_deg) % 360
                else:
                    self._rotacion_vista_deg = 0
                self._plasma_emphasis_on = False
                self._plasma_emphasis_offset_in = None
            elif rotacion_vista_deg is not None:
                self._rotacion_vista_deg = int(rotacion_vista_deg) % 360
            self._ruta_actual = ruta_dxf
            if plasma_offset_mm is not None:
                self._plasma_offset_mm = float(plasma_offset_mm or 0.0)
            self._cad.set_material(self._material)
            model = load_dxf_part(ruta_dxf, self._rotacion_vista_deg)
            if model is None:
                return False
            self.factor_conversion = model.factor_conversion
            self._cad.load_model(model, fit=True)
            if self._plasma_offset_mm > 0:
                self._reaplicar_overlay_plasma()
            elif self._plasma_emphasis_on:
                self.set_plasma_contour_emphasis(
                    True, offset_in=self._plasma_emphasis_offset_in
                )
            else:
                self._cad.clear_plasma_overlay()
                if hasattr(self, "lbl_plasma"):
                    self.lbl_plasma.setText("—")
            return True
        except Exception:
            return False


def generar_thumbnail(ruta_dxf, size=(50, 50), material: str | None = None):
    try:
        from interface.material_colors import paleta_cad_hex
        from interface.qt.dxf_part_geometry import decimar_polyline_xy
        from modules.dxf_thread_lock import EZDXF_LOCK

        piece_fill, hole_fill, piece_edge = paleta_cad_hex(material)

        fig = Figure(figsize=(2, 2), dpi=50)
        FigureCanvasAgg(fig)
        fig.patch.set_facecolor(CAD_VIEW_BG)
        ax = fig.add_subplot(111)
        ax.axis("off")

        with EZDXF_LOCK:
            msp = ezdxf.readfile(ruta_dxf).modelspace()

        for e in msp:
            try:
                layer_u = e.dxf.layer.upper()
                typ = e.dxftype()
                if typ == "CIRCLE" and "CUT" in layer_u:
                    c = e.dxf.center
                    r = float(e.dxf.radius)
                    if "OUTER" in layer_u:
                        color = piece_fill
                    elif "INNER" in layer_u or "INTERIOR" in layer_u:
                        color = CAD_VIEW_BG
                    else:
                        color = piece_fill
                    ax.add_patch(
                        Circle(
                            (float(c.x), float(c.y)),
                            r,
                            facecolor=color,
                            edgecolor=piece_edge,
                            linewidth=0.5,
                        )
                    )
                    continue
                if typ == "LWPOLYLINE":
                    pts = [(float(x), float(y)) for x, y, *_ in e.get_points("xyb")]
                    if len(pts) > 120:
                        pts = decimar_polyline_xy(pts, max_pts=120)
                else:
                    p = path.make_path(e)
                    pts = [(v[0], v[1]) for v in p.flattening(0.05)]
                if len(pts) > 2:
                    if "OUTER" in layer_u:
                        color = piece_fill
                    elif "INNER" in layer_u or "INTERIOR" in layer_u:
                        color = CAD_VIEW_BG
                    else:
                        color = piece_fill
                    ax.add_patch(
                        Polygon(pts, closed=True, facecolor=color, edgecolor=piece_edge, linewidth=0.5)
                    )
            except Exception:
                pass

        ax.autoscale(enable=True)
        ax.set_aspect("equal")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=CAD_VIEW_BG)
        buf.seek(0)

        img = Image.open(buf).convert("RGBA")
        data = img.tobytes("raw", "RGBA")
        qimg = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
        pix = QPixmap.fromImage(qimg)
        return pix.scaled(
            size[0],
            size[1],
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception:
        return None
