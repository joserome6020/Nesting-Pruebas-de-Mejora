"""Helpers para migrar patrones Tk/CTk a widgets Qt."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton, QLabel, QWidget, QScrollArea, QVBoxLayout


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
    w = scroll.widget()
    if w and w.layout():
        clear_layout(w.layout())


def scroll_add_widget(scroll: QScrollArea, widget: QWidget):
    inner = scroll.widget()
    if inner is None:
        inner = QWidget()
        scroll.setWidget(inner)
        inner.setLayout(QVBoxLayout())
        inner.layout().setContentsMargins(0, 0, 0, 0)
    inner.layout().addWidget(widget)
