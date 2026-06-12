"""Utilidades Matplotlib + Qt."""
from PySide6.QtCore import QTimer


def bind_figure_resize(canvas_widget, fig, on_resize=None, debounce_ms=90):
    state = {"timer": None, "last": (0, 0)}

    def _apply(w, h):
        if w < 12 or h < 12:
            return
        if state["last"] == (w, h):
            return
        state["last"] = (w, h)
        dpi = fig.get_dpi()
        fig.set_size_inches(w / dpi, h / dpi, forward=False)
        if on_resize:
            on_resize()
        elif getattr(fig, "canvas", None):
            fig.canvas.draw_idle()

    def _on_resize():
        w, h = canvas_widget.width(), canvas_widget.height()
        if state["timer"]:
            state["timer"].stop()
        state["timer"] = QTimer()
        state["timer"].setSingleShot(True)
        state["timer"].timeout.connect(lambda ww=w, hh=h: _apply(ww, hh))
        state["timer"].start(debounce_ms)

    _orig = canvas_widget.resizeEvent

    def _wrap(ev):
        _on_resize()
        if _orig:
            _orig(ev)

    canvas_widget.resizeEvent = _wrap
