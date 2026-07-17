"""Helpers para migrar patrones Tk/CTk a widgets Qt."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton, QLabel, QWidget, QScrollArea, QVBoxLayout

from interface.qt.layout_helpers import layout_ensure_bottom_stretch, layout_insert_before_stretch


class TimerHost:
    def __init__(self):
        self._qt_timers: dict[int, QTimer] = {}
        self._qt_timer_seq = 0

    def after(self, ms: int, callback):
        self._qt_timer_seq += 1
        tid = self._qt_timer_seq
        t = QTimer(self if isinstance(self, QWidget) else None)
        t.setSingleShot(True)
        t.timeout.connect(callback)
        t.start(max(0, int(ms)))
        self._qt_timers[tid] = t
        return tid

    def after_cancel(self, timer_id):
        t = self._qt_timers.pop(int(timer_id), None)
        if t:
            t.stop()


def q_configure(widget, *, text=None, state=None, values=None, fg_color=None, hover_color=None):
    if text is not None and hasattr(widget, "setText"):
        widget.setText(str(text))
    if state is not None and hasattr(widget, "setEnabled"):
        widget.setEnabled(str(state).lower() != "disabled")
    if values is not None and isinstance(widget, QComboBox):
        cur = widget.currentText()
        widget.blockSignals(True)
        widget.clear()
        widget.addItems([str(v) for v in values])
        if cur:
            idx = widget.findText(cur)
            if idx >= 0:
                widget.setCurrentIndex(idx)
        widget.blockSignals(False)


def clear_layout(layout):
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w:
            w.deleteLater()


def scroll_clear(scroll: QScrollArea):
    """Limpia el scroll y deja stretch final para que pocas filas no se alarguen."""
    w = scroll.widget()
    if w and w.layout():
        clear_layout(w.layout())
        lay = w.layout()
        if isinstance(lay, QVBoxLayout):
            lay.setAlignment(Qt.AlignmentFlag.AlignTop)
            layout_ensure_bottom_stretch(lay)


def scroll_add_widget(scroll: QScrollArea, widget: QWidget):
    """Añade un widget empaquetado arriba (antes del stretch final)."""
    inner = scroll.widget()
    if inner is None:
        inner = QWidget()
        scroll.setWidget(inner)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addStretch(1)
    lay = inner.layout()
    if isinstance(lay, QVBoxLayout):
        layout_insert_before_stretch(lay, widget)
    else:
        lay.addWidget(widget)
