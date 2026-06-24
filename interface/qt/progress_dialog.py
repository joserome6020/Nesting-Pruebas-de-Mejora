"""Diálogo de progreso nativo Qt (reemplaza CTkToplevel de carga)."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
)

import config
from .theme import COLOR_GRIS_DARK, COLOR_TEXTO_SECUNDARIO, surface_dialog_stylesheet

_LOGO_FPS = 60
_LOGO_INTERVAL_MS = max(1, int(round(1000 / _LOGO_FPS)))


def _bounce_1d(p0: float, v: float, lo: float, hi: float, t: float) -> float:
    width = float(hi - lo)
    if width <= 1e-6 or abs(v) < 1e-6:
        return p0
    period = 2.0 * width
    q = (p0 - lo + v * t) % period
    if q < 0:
        q += period
    if q <= width:
        return lo + q
    return lo + (period - q)


class _BouncingLogoLabel(QLabel):
    """Logo Arga rebotando dentro del recuadro del diálogo (sin ventana flotante)."""

    def __init__(self, parent: QFrame, logo_path: str):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        pix = QPixmap()
        if logo_path and os.path.exists(logo_path):
            pix = QPixmap(logo_path).scaled(
                54,
                54,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self.setPixmap(pix)
        self.setFixedSize(54, 54)
        self.move(8, 8)

        self._t0 = time.perf_counter()
        self._vx = 144.0
        self._vy = 108.0
        self._x0 = 8.0
        self._y0 = 8.0

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._t0 = time.perf_counter()
        self._timer.start(_LOGO_INTERVAL_MS)

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        box = self.parentWidget()
        if box is None or self.pixmap() is None or self.pixmap().isNull():
            return
        t = time.perf_counter() - self._t0
        lw, lh = self.width(), self.height()
        max_x = max(0.0, float(box.width() - lw))
        max_y = max(0.0, float(box.height() - lh))
        x = _bounce_1d(self._x0, self._vx, 0.0, max_x, t)
        y = _bounce_1d(self._y0, self._vy, 0.0, max_y, t)
        self.move(int(x), int(y))


class ProgressDialog(QDialog):
    def __init__(self, parent, titulo: str = "Ejecutando Nesting"):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.setFixedSize(500, 250)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setStyleSheet(surface_dialog_stylesheet())

        self._inicio_ts = time.time()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick_tiempo)
        self._force_close = False
        self._logo_label: _BouncingLogoLabel | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)

        self.lbl_mensaje = QLabel("Procesando motor matemático...")
        self.lbl_mensaje.setStyleSheet(f"font-weight:700;color:{COLOR_GRIS_DARK};")
        self.lbl_mensaje.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_mensaje)

        self.lbl_porcentaje = QLabel("0%")
        self.lbl_porcentaje.setStyleSheet("font-weight:700;color:#3B82F6;font-size:14px;")
        self.lbl_porcentaje.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_porcentaje)

        self.lbl_tiempo = QLabel("Tiempo: 00:00:00")
        self.lbl_tiempo.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-weight:600;")
        self.lbl_tiempo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_tiempo)

        self.barra = QProgressBar()
        self.barra.setRange(0, 100)
        self.barra.setValue(0)
        self.barra.setFixedWidth(350)
        lay.addWidget(self.barra, alignment=Qt.AlignmentFlag.AlignCenter)

        self._logo_box = QFrame()
        self._logo_box.setFixedSize(360, 95)
        self._logo_box.setStyleSheet(
            "background:#FBFCFF;border:1px solid #D8DFEB;border-radius:12px;"
        )
        lay.addWidget(self._logo_box, alignment=Qt.AlignmentFlag.AlignCenter)
        self._logo_box.hide()

        self._usar_animacion = self._titulo_usa_animacion(titulo)
        if self._usar_animacion:
            self.barra.hide()
            self.lbl_porcentaje.hide()
            self.lbl_tiempo.hide()
            self._logo_box.show()
            QTimer.singleShot(0, self._iniciar_animacion_logo)
        else:
            self._timer.start(1000)

        self._centrar_sobre_padre(parent)

    def _centrar_sobre_padre(self, parent) -> None:
        if parent is None:
            return
        geo = parent.frameGeometry()
        x = geo.center().x() - self.width() // 2
        y = geo.center().y() - self.height() // 2
        self.move(x, y)

    def _titulo_usa_animacion(self, titulo: str) -> bool:
        t = str(titulo or "").upper()
        con_barra = (
            "EJECUTANDO NESTING",
            "OPTIMIZANDO LOTES",
            "RENESTEANDO LOTE ACTIVO",
            "RECALCULANDO PLACA",
        )
        return not any(tag in t for tag in con_barra)

    def _iniciar_animacion_logo(self) -> None:
        self._detener_animacion_logo()
        logo_path = config.ruta_recurso(os.path.join("assets", "branding", "logo_icon1.png"))
        self._logo_label = _BouncingLogoLabel(self._logo_box, logo_path)
        self._logo_label.show()
        self._logo_label.start()

    def _detener_animacion_logo(self) -> None:
        if self._logo_label is not None:
            self._logo_label.stop()
            self._logo_label.deleteLater()
            self._logo_label = None

    def _tick_tiempo(self) -> None:
        elapsed = max(0, int(time.time() - self._inicio_ts))
        hh, mm, ss = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        self.lbl_tiempo.setText(f"Tiempo: {hh:02d}:{mm:02d}:{ss:02d}")

    def actualizar(self, mensaje: str, porcentaje: float) -> None:
        if self._usar_animacion:
            return
        self.lbl_mensaje.setText(mensaje)
        self.barra.setValue(int(max(0, min(1, porcentaje)) * 100))
        self.lbl_porcentaje.setText(f"{int(porcentaje * 100)}%")

    def force_close(self) -> None:
        self._force_close = True
        self.close()

    def closeEvent(self, event) -> None:
        self._detener_animacion_logo()
        self._timer.stop()
        if self._force_close:
            self._force_close = False
            super().closeEvent(event)
            return
        parent = self.parent()
        if parent and hasattr(parent, "cancelar_tarea_actual"):
            res = QMessageBox.question(
                self,
                "Cancelar proceso",
                "¿Desea cancelar el proceso en curso?\n\nSe detendrá el cálculo y podrá volver a ejecutarlo.",
            )
            if res == QMessageBox.StandardButton.Yes:
                parent.cancelar_tarea_actual(desde_popup=True)
            else:
                event.ignore()
                return
        super().closeEvent(event)
