"""Diálogo de progreso nativo Qt (reemplaza CTkToplevel de carga)."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QTimer, Qt
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
        self._logo_timer = QTimer(self)
        self._logo_pos = [8.0, 8.0]
        self._logo_vel = [2.4, 1.8]
        self._logo_running = False
        self._force_close = False

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
        self._logo_box.setStyleSheet("background:#FBFCFF;border:1px solid #D8DFEB;border-radius:12px;")
        self._logo_label = QLabel(self._logo_box)
        self._logo_label.setFixedSize(54, 54)
        self._logo_label.move(8, 8)
        lay.addWidget(self._logo_box, alignment=Qt.AlignmentFlag.AlignCenter)
        self._logo_box.hide()

        self._usar_animacion = self._titulo_usa_animacion(titulo)
        if self._usar_animacion:
            self.barra.hide()
            self.lbl_porcentaje.hide()
            self.lbl_tiempo.hide()
            self._logo_box.show()
            self._iniciar_animacion_logo()
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
        logo_path = config.ruta_recurso(os.path.join("assets", "branding", "logo_icon1.png"))
        if os.path.exists(logo_path):
            pix = QPixmap(logo_path).scaled(54, 54, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._logo_label.setPixmap(pix)
        self._logo_running = True
        self._logo_timer.timeout.connect(self._animar_logo)
        self._logo_timer.start(16)

    def _animar_logo(self) -> None:
        if not self._logo_running:
            return
        bw, bh = self._logo_box.width(), self._logo_box.height()
        lw, lh = self._logo_label.width(), self._logo_label.height()
        nx = self._logo_pos[0] + self._logo_vel[0]
        ny = self._logo_pos[1] + self._logo_vel[1]
        if nx <= 0:
            nx, self._logo_vel[0] = 0, abs(self._logo_vel[0])
        elif nx + lw >= bw:
            nx, self._logo_vel[0] = max(0, bw - lw), -abs(self._logo_vel[0])
        if ny <= 0:
            ny, self._logo_vel[1] = 0, abs(self._logo_vel[1])
        elif ny + lh >= bh:
            ny, self._logo_vel[1] = max(0, bh - lh), -abs(self._logo_vel[1])
        self._logo_pos = [nx, ny]
        self._logo_label.move(int(nx), int(ny))

    def _tick_tiempo(self) -> None:
        elapsed = max(0, int(time.time() - self._inicio_ts))
        hh, mm, ss = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        self.lbl_tiempo.setText(f"Tiempo: {hh:02d}:{mm:02d}:{ss:02d}")

    def actualizar(self, mensaje: str, porcentaje: float) -> None:
        self.lbl_mensaje.setText(mensaje)
        self.barra.setValue(int(max(0, min(1, porcentaje)) * 100))
        self.lbl_porcentaje.setText(f"{int(porcentaje * 100)}%")

    def force_close(self) -> None:
        self._force_close = True
        self.close()

    def closeEvent(self, event) -> None:
        if self._logo_running:
            self._logo_timer.stop()
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
