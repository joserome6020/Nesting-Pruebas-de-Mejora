"""Visor CAD de pieza (QGraphicsView + ezdxf PyQtBackend) y thumbnail para listados."""
from __future__ import annotations

import io

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
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from interface.qt.cad_graphics_view import CadPartGraphicsView
from interface.qt.dxf_part_loader import load_dxf_part
from interface.qt.theme import apply_push_button, COLOR_GRIS_DARK

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
        self.frame_seccion_3.setFixedHeight(76)
        master_lay.addWidget(self.frame_seccion_3)

        sec2_lay = QVBoxLayout(self.frame_seccion_2)
        sec2_lay.setContentsMargins(0, 0, 0, 0)
        sec2_lay.setSpacing(0)

        toolbar = QFrame()
        toolbar.setStyleSheet("background:#0F172A;border-bottom:1px solid #334155;")
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(10, 6, 10, 6)
        tb_lay.setSpacing(8)

        btn_fit = QPushButton("AJUSTAR VISTA")
        btn_fit.setFixedHeight(28)
        apply_push_button(btn_fit, COLOR_GRIS_DARK, font_size=10, padding="4px 12px")
        btn_fit.clicked.connect(self.ajustar_vista)
        tb_lay.addWidget(btn_fit)

        btn_rot = QPushButton("ROTAR 90°")
        btn_rot.setFixedHeight(28)
        apply_push_button(btn_rot, "#334155", font_size=10, padding="4px 12px")
        btn_rot.clicked.connect(self.rotar_vista_90)
        tb_lay.addWidget(btn_rot)

        lbl_hint = QLabel(
            "CLIC: COTA  ·  RUEDA: ZOOM  ·  CENTRAL: PAN  ·  DER: ROTAR  ·  ESC: CANCELAR"
        )
        lbl_hint.setStyleSheet("color:#64748B;font-size:10px;background:transparent;")
        lbl_hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tb_lay.addStretch()
        tb_lay.addWidget(lbl_hint, 1)

        sec2_lay.addWidget(toolbar)

        self._cad = CadPartGraphicsView()
        self._cad.metrics_callback = self.actualizar_datos
        self._cad.rotate_requested.connect(self.rotar_vista_90)
        self.widget = self._cad
        sec2_lay.addWidget(self._cad, 1)

        self.factor_conversion = 25.4
        self._ruta_actual = None
        self._rotacion_vista_deg = 0
        self._persist_rotation_hook = None
        self._material = ""

        self.construir_tabla_3_columnas()
        self.mostrar_patron_prueba()

    def set_persist_rotation_hook(self, hook):
        self._persist_rotation_hook = hook

    def set_material(self, material: str | None = None):
        self._material = str(material or "").strip()
        self._cad.set_material(self._material)

    def construir_tabla_3_columnas(self):
        self.frame_seccion_3.setStyleSheet(
            "QFrame#VisorInfoPanel{background:#0F172A;border:none;}"
        )
        row = QHBoxLayout(self.frame_seccion_3)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(18)

        def _metric(caption: str, attr_name: str):
            wrap = QWidget()
            wrap.setStyleSheet("background:transparent;")
            # Tamaño natural: evita que LARGO/ANCHO/etc. se estiren a todo el ancho.
            wrap.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(1)
            cap = QLabel(caption)
            cap.setStyleSheet("color:#64748B;font-size:10px;font-weight:700;background:transparent;")
            val = QLabel("-")
            val.setStyleSheet("color:#E2E8F0;font-size:13px;font-weight:700;background:transparent;")
            val.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            val.setMinimumWidth(72)
            wl.addWidget(cap)
            wl.addWidget(val)
            setattr(self, attr_name, val)
            row.addWidget(wrap, 0)

        _metric("LARGO (X)", "lbl_width")
        _metric("ANCHO (Y)", "lbl_height")
        _metric("AREA NETA", "lbl_area")
        _metric("PERIMETRO", "lbl_perim")
        _metric("REFERENCIA", "lbl_ref")
        self.lbl_ref.setMinimumWidth(120)
        row.addStretch(1)

    def actualizar_datos(self, min_x, max_x, min_y, max_y, perimetro, valido, area=None, referencia=""):
        if not valido:
            return
        ancho_in = abs(max_x - min_x) / self.factor_conversion
        alto_in = abs(max_y - min_y) / self.factor_conversion
        perim_in = perimetro / self.factor_conversion
        area_in2 = (float(area) / (self.factor_conversion**2)) if area is not None else 0.0
        self.lbl_width.setText(f'{ancho_in:.2f}"')
        self.lbl_height.setText(f'{alto_in:.2f}"')
        self.lbl_area.setText(f"{area_in2:.2f} in²")
        self.lbl_perim.setText(f'{perim_in:.2f}"')
        self.lbl_ref.setText(str(referencia or "-"))

    def actualizar_info_extra(self, area_in2=None, referencia=None):
        if area_in2 is not None:
            self.lbl_area.setText(f"{float(area_in2):.2f} in²")
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

    def renderizar_dxf(self, ruta_dxf, rotacion_vista_deg=None):
        self.limpiar_lienzo()
        try:
            if self._ruta_actual and str(self._ruta_actual) != str(ruta_dxf):
                if rotacion_vista_deg is not None:
                    self._rotacion_vista_deg = int(rotacion_vista_deg) % 360
                else:
                    self._rotacion_vista_deg = 0
            elif rotacion_vista_deg is not None:
                self._rotacion_vista_deg = int(rotacion_vista_deg) % 360
            self._ruta_actual = ruta_dxf
            self._cad.set_material(self._material)
            model = load_dxf_part(ruta_dxf, self._rotacion_vista_deg)
            if model is None:
                return False
            self.factor_conversion = model.factor_conversion
            self._cad.load_model(model, fit=True)
            return True
        except Exception:
            return False


def generar_thumbnail(ruta_dxf, size=(50, 50), material: str | None = None):
    try:
        from interface.material_colors import paleta_cad_hex

        piece_fill, hole_fill, piece_edge = paleta_cad_hex(material)

        fig = Figure(figsize=(2, 2), dpi=50)
        FigureCanvasAgg(fig)
        fig.patch.set_facecolor(CAD_VIEW_BG)
        ax = fig.add_subplot(111)
        ax.axis("off")

        msp = ezdxf.readfile(ruta_dxf).modelspace()

        for e in msp:
            try:
                layer_u = e.dxf.layer.upper()
                if e.dxftype() == "CIRCLE" and "CUT" in layer_u:
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
                p = path.make_path(e)
                pts = [(v[0], v[1]) for v in p.flattening(0.01)]
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
