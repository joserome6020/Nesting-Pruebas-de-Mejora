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
    QCheckBox,
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
        self.frame_seccion_3.setFixedHeight(84)
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
        self._orientation_lock_hook = None
        self._material = ""
        self._plasma_offset_mm = 0.0
        self._plasma_base_metrics = None

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

        plasma_wrap = QWidget()
        plasma_wrap.setStyleSheet("background:transparent;")
        plasma_wrap.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        pl = QVBoxLayout(plasma_wrap)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(1)
        self.lbl_plasma_cap = QLabel("PLASMA")
        self.lbl_plasma_cap.setStyleSheet(
            "color:#64748B;font-size:10px;font-weight:700;background:transparent;"
        )
        self.lbl_plasma = QLabel("—")
        self.lbl_plasma.setStyleSheet(
            "color:#FCA5A5;font-size:13px;font-weight:700;background:transparent;"
        )
        self.lbl_plasma.setMinimumWidth(100)
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
            "color:#64748B;font-size:10px;font-weight:700;background:transparent;"
        )
        self.chk_orientacion_corte = QCheckBox("BLOQUEAR ORIENTACIÓN DE CORTE")
        self.chk_orientacion_corte.setStyleSheet(
            "QCheckBox{color:#E2E8F0;font-size:11px;font-weight:700;background:transparent;}"
            "QCheckBox::indicator{width:14px;height:14px;}"
        )
        self.chk_orientacion_corte.setToolTip(
            "Si está activo, el nesting solo podrá usar la orientación visible "
            "(incluida la de ROTAR 90°). Al desmarcar, vuelven las rotaciones normales."
        )
        self.chk_orientacion_corte.toggled.connect(self._on_orientation_lock_toggled)
        ll.addWidget(self.lbl_orientacion_cap)
        ll.addWidget(self.chk_orientacion_corte)
        row.addWidget(lock_wrap, 0)
        row.addStretch(1)

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
        self.lbl_width.setText(f'{ancho_in:.2f}"')
        self.lbl_height.setText(f'{alto_in:.2f}"')
        self.lbl_area.setText(f"{area_in2:.2f} in²")
        self.lbl_perim.setText(f'{perim_in:.2f}"')
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
        """Resalta OUTER en rojo (pieza plasma). No altera la geometría del DXF."""
        if not activo:
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

    def renderizar_dxf(self, ruta_dxf, rotacion_vista_deg=None, plasma_offset_mm=None):
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
            if plasma_offset_mm is not None:
                self._plasma_offset_mm = float(plasma_offset_mm or 0.0)
            self._cad.set_material(self._material)
            model = load_dxf_part(ruta_dxf, self._rotacion_vista_deg)
            if model is None:
                return False
            self.factor_conversion = model.factor_conversion
            self._cad.load_model(model, fit=True)
            # load_model dispara metrics_callback → aplica overlay si hay offset.
            if self._plasma_offset_mm > 0:
                self._reaplicar_overlay_plasma()
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
