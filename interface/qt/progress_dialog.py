"""Diálogo de progreso nativo Qt (reemplaza CTkToplevel de carga)."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

import config
from .theme import (
    COLOR_ACENTO,
    COLOR_GRIS_DARK,
    COLOR_TEXTO_SECUNDARIO,
    apply_push_button,
    surface_dialog_stylesheet,
)

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
    def __init__(
        self,
        parent,
        titulo: str = "Ejecutando Nesting",
        *,
        ultra_accept: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self._usar_animacion = self._titulo_usa_animacion(titulo)
        self._ultra_accept = bool(ultra_accept)
        # Logo rebotando + tiempo (+ botón Ultra si aplica). Más alto si hay botón.
        if self._usar_animacion and self._ultra_accept:
            alto = 350
        elif self._usar_animacion:
            alto = 280
        elif self._ultra_accept:
            alto = 320
        else:
            alto = 250
        self.setFixedSize(500, alto)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setStyleSheet(surface_dialog_stylesheet())

        self._inicio_ts = time.time()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick_tiempo)
        self._force_close = False
        self._logo_label: _BouncingLogoLabel | None = None
        self._aceptando = False

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

        self.btn_aceptar_mejor = QPushButton("Aceptar mejor actual")
        apply_push_button(self.btn_aceptar_mejor, COLOR_ACENTO, font_size=11, padding="8px 14px")
        self.btn_aceptar_mejor.setEnabled(False)
        self.btn_aceptar_mejor.setToolTip(
            "Se activa cuando Ultra ya tiene un acomodo completo. "
            "Mientras esperas, sigue refinando ese nest (estilo NestFab) y "
            "el contador de mejoras sube cada vez que encuentra uno mejor. "
            "Al aceptar se aplica el mejor actual sin esperar el round en curso."
        )
        self.btn_aceptar_mejor.clicked.connect(self._on_aceptar_mejor)
        self.btn_aceptar_mejor.hide()
        self.lbl_mejoras = QLabel("Mejoras aplicadas: 0")
        self.lbl_mejoras.setStyleSheet(
            f"color:{COLOR_TEXTO_SECUNDARIO};font-weight:700;font-size:12px;"
        )
        self.lbl_mejoras.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_mejoras.hide()
        self._mejoras_count = 0
        if self._ultra_accept:
            self.btn_aceptar_mejor.show()
            self.lbl_mejoras.show()
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(self.btn_aceptar_mejor)
            row.addStretch(1)
            lay.addLayout(row)
            lay.addWidget(self.lbl_mejoras)

        if self._usar_animacion:
            # Modo renesteo/carga: logo Arga rebotando + cronómetro.
            self.barra.hide()
            self.lbl_porcentaje.hide()
            self._logo_box.show()
            QTimer.singleShot(0, self._iniciar_animacion_logo)
        self.lbl_tiempo.show()
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
        # Solo nest "completo" usa barra/%. Renesteo/compensación: logo + tiempo.
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
        if self._aceptando:
            return
        if not self._usar_animacion:
            self.barra.setValue(int(max(0, min(1, porcentaje)) * 100))
            self.lbl_porcentaje.setText(f"{int(porcentaje * 100)}%")
        self.lbl_mensaje.setText(mensaje)

    def habilitar_aceptar_mejor(self, resumen: str = "") -> None:
        if not self._ultra_accept or self._aceptando:
            return
        self.btn_aceptar_mejor.setEnabled(True)
        # Resumen tipico: "... · mejoras=3"
        mejoras = self._mejoras_count
        texto = str(resumen or "")
        if "mejoras=" in texto:
            try:
                mejoras = int(texto.rsplit("mejoras=", 1)[-1].strip().split()[0])
            except Exception:
                pass
            texto = texto.rsplit(" · mejoras=", 1)[0].strip()
        self._mejoras_count = max(0, int(mejoras))
        self.lbl_mejoras.setText(f"Mejoras aplicadas: {self._mejoras_count}")
        if texto:
            self.lbl_mensaje.setText(f"Mejor acomodo listo · {texto}")
        else:
            self.lbl_mensaje.setText(
                "Mejor acomodo listo · puedes aceptarlo o seguir optimizando"
            )

    def marcar_aceptando(self) -> None:
        self._aceptando = True
        self.btn_aceptar_mejor.setEnabled(False)
        self.btn_aceptar_mejor.setText("Aceptando…")
        self.lbl_mensaje.setText(
            "Aceptando mejor acomodo actual (sin esperar el round en curso)…"
        )

    def _on_aceptar_mejor(self) -> None:
        parent = self.parent()
        if parent and hasattr(parent, "aceptar_mejor_actual"):
            parent.aceptar_mejor_actual()
            self.marcar_aceptando()

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
        if self._aceptando:
            # Cierre en curso tras aceptar: no preguntar abortar.
            event.ignore()
            return
        parent = self.parent()
        if parent and hasattr(parent, "cancelar_tarea_actual"):
            msg = (
                "¿Desea cancelar el proceso en curso?\n\n"
                "Se detendrá el cálculo sin aplicar el resultado."
            )
            if self._ultra_accept:
                msg = (
                    "¿Cancelar Ultra sin aplicar el acomodo?\n\n"
                    "Si ya hay un mejor layout, usa «Aceptar mejor actual» "
                    "para conservarlo."
                )
            res = QMessageBox.question(self, "Cancelar proceso", msg)
            if res == QMessageBox.StandardButton.Yes:
                parent.cancelar_tarea_actual(desde_popup=True)
            else:
                event.ignore()
                return
        super().closeEvent(event)
