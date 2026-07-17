"""Switch estilo React-Herinox con thumb deslizante animado."""
from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from interface.qt.theme import COLOR_ACENTO, COLOR_ACENTO_TEXTO, COLOR_TEXTO_MUTED


class _SwitchTrack(QWidget):
    """Pista pill + círculo blanco que se desliza al cambiar de estado."""

    clicked = Signal()

    TRACK_W = 46
    TRACK_H = 26
    THUMB_D = 20
    PAD = 3

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._checked = bool(checked)
        self._thumb_pos = 1.0 if checked else 0.0
        self.setFixedSize(self.TRACK_W, self.TRACK_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._anim = QPropertyAnimation(self, b"thumbPosition")
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def getThumbPosition(self) -> float:
        return self._thumb_pos

    def setThumbPosition(self, value: float) -> None:
        self._thumb_pos = float(value)
        self.update()

    thumbPosition = Property(float, getThumbPosition, setThumbPosition)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool, *, animate: bool = True) -> None:
        checked = bool(checked)
        if self._checked == checked and (
            (checked and self._thumb_pos >= 0.99) or (not checked and self._thumb_pos <= 0.01)
        ):
            return
        self._checked = checked
        target = 1.0 if checked else 0.0
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._thumb_pos)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._thumb_pos = target
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        track = QColor(COLOR_ACENTO) if self._checked else QColor("#C9D4E6")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(0, 0, self.TRACK_W, self.TRACK_H, self.TRACK_H / 2, self.TRACK_H / 2)

        min_cx = self.PAD + self.THUMB_D / 2
        max_cx = self.TRACK_W - self.PAD - self.THUMB_D / 2
        cx = min_cx + (max_cx - min_cx) * self._thumb_pos
        cy = self.TRACK_H / 2
        r = self.THUMB_D / 2

        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(int(cx - r), int(cy - r), self.THUMB_D, self.THUMB_D)


class HerinoxSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(
        self,
        parent=None,
        *,
        label_on: str = "Activo",
        label_off: str = "Inactivo",
        checked: bool = True,
    ):
        super().__init__(parent)
        self._label_on = label_on
        self._label_off = label_off
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self._track = _SwitchTrack(checked=checked)
        self._track.clicked.connect(self._toggle)
        lay.addWidget(self._track)

        self._lbl = QLabel()
        self._lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lbl.mousePressEvent = lambda _e: self._toggle()
        lay.addWidget(self._lbl)

        self._refresh_label(checked)

    def _toggle(self):
        nuevo = not self._track.isChecked()
        self._track.setChecked(nuevo, animate=True)
        self._refresh_label(nuevo)
        self.toggled.emit(nuevo)

    def _refresh_label(self, checked: bool):
        text = self._label_on if checked else self._label_off
        color = COLOR_ACENTO_TEXTO if checked else COLOR_TEXTO_MUTED
        self._lbl.setText(text)
        self._lbl.setStyleSheet(f"font-weight:600;font-size:12px;color:{color};")

    def isChecked(self) -> bool:
        return self._track.isChecked()

    def setChecked(self, checked: bool) -> None:
        checked = bool(checked)
        if self._track.isChecked() == checked:
            return
        self._track.setChecked(checked, animate=True)
        self._refresh_label(checked)
