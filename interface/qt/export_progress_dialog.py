"""Pantalla de carga de exportación DXF + STEP (doble barra + cronómetro)."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)

from .theme import (
    COLOR_GRIS_DARK,
    COLOR_TEXTO_SECUNDARIO,
    surface_dialog_stylesheet,
)


class ExportProgressDialog(QDialog):
    """
    Carga de exportación con:
    - cronómetro (mismo espíritu que ProgressDialog)
    - barra DXF (n/total + %)
    - barra STEP opcional (solo si hay STEP en este export)
    """

    def __init__(self, parent, titulo: str = "Exportando DXF / STEP"):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        from interface.qt.ui_scale import set_scaled_fixed_size

        self._size_with_step = (520, 320)
        self._size_dxf_only = (520, 248)
        set_scaled_fixed_size(self, *self._size_with_step)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setStyleSheet(surface_dialog_stylesheet())

        self._inicio_ts = time.time()
        self._force_close = False
        self._dxf_total = 0
        self._step_total = 0
        self._dxf_done = 0
        self._step_done = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(10)

        self.lbl_titulo = QLabel(titulo)
        self.lbl_titulo.setStyleSheet(f"font-weight:800;font-size:14px;color:{COLOR_GRIS_DARK};")
        self.lbl_titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_titulo)

        self.lbl_tiempo = QLabel("Tiempo: 00:00:00")
        self.lbl_tiempo.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-weight:700;")
        self.lbl_tiempo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_tiempo)

        self.lbl_mensaje = QLabel("Preparando exportación…")
        self.lbl_mensaje.setWordWrap(True)
        self.lbl_mensaje.setStyleSheet(f"font-weight:600;color:{COLOR_GRIS_DARK};")
        self.lbl_mensaje.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_mensaje)

        self.lbl_dxf = QLabel("DXF  0 / 0  (0%)")
        self.lbl_dxf.setStyleSheet("font-weight:700;color:#1D4ED8;")
        lay.addWidget(self.lbl_dxf)
        self.barra_dxf = QProgressBar()
        self.barra_dxf.setRange(0, 100)
        self.barra_dxf.setValue(0)
        self.barra_dxf.setFixedHeight(22)
        lay.addWidget(self.barra_dxf)

        self.lbl_step = QLabel("STEP  0 / 0  (0%)")
        self.lbl_step.setStyleSheet("font-weight:700;color:#0F766E;")
        lay.addWidget(self.lbl_step)
        self.barra_step = QProgressBar()
        self.barra_step.setRange(0, 100)
        self.barra_step.setValue(0)
        self.barra_step.setFixedHeight(22)
        lay.addWidget(self.barra_step)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick_tiempo)
        self._timer.start(1000)

        if parent is not None:
            geo = parent.frameGeometry()
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )

    def _tick_tiempo(self) -> None:
        elapsed = max(0, int(time.time() - self._inicio_ts))
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        self.lbl_tiempo.setText(f"Tiempo: {h:02d}:{m:02d}:{s:02d}")

    @staticmethod
    def _pct(done: int, total: int) -> int:
        if total <= 0:
            return 100 if done > 0 else 0
        return max(0, min(100, int(round(100.0 * float(done) / float(total)))))

    def _set_step_visible(self, visible: bool) -> None:
        self.lbl_step.setVisible(visible)
        self.barra_step.setVisible(visible)
        from interface.qt.ui_scale import set_scaled_fixed_size

        set_scaled_fixed_size(
            self,
            *(self._size_with_step if visible else self._size_dxf_only),
        )

    def set_totals(self, n_dxf: int, n_step: int) -> None:
        self._dxf_total = max(0, int(n_dxf))
        self._step_total = max(0, int(n_step))
        show_step = self._step_total > 0
        self._set_step_visible(show_step)
        self._refresh_labels()
        if not show_step:
            self._step_done = 0
            self.barra_step.setValue(0)

    def update_progress(
        self,
        *,
        dxf_done: int | None = None,
        step_done: int | None = None,
        mensaje: str = "",
    ) -> None:
        if dxf_done is not None:
            self._dxf_done = max(0, int(dxf_done))
            # Si el estimado se quedó corto (p. ej. Amada = 2 DXF), ajusta el total.
            if self._dxf_done > self._dxf_total:
                self._dxf_total = self._dxf_done
        if step_done is not None and self._step_total > 0:
            self._step_done = max(0, int(step_done))
            if self._step_done > self._step_total:
                self._step_total = self._step_done
        if mensaje:
            self.lbl_mensaje.setText(str(mensaje))
        self._refresh_labels()

    def _refresh_labels(self) -> None:
        pd = self._pct(self._dxf_done, self._dxf_total)
        self.lbl_dxf.setText(
            f"DXF  {self._dxf_done} / {self._dxf_total}  ({pd}%)"
        )
        self.barra_dxf.setValue(pd)
        if self._step_total > 0:
            ps = self._pct(self._step_done, self._step_total)
            self.lbl_step.setText(
                f"STEP  {self._step_done} / {self._step_total}  ({ps}%)"
            )
            self.barra_step.setValue(ps)
            self.barra_step.setEnabled(True)

    def force_close(self) -> None:
        self._force_close = True
        try:
            self._timer.stop()
        except Exception:
            pass
        self.close()

    def closeEvent(self, event) -> None:
        if self._force_close:
            event.accept()
            return
        # Evitar cierre accidental con la X durante exportación
        event.ignore()
