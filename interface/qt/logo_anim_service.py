"""Servicio persistente de animación del logo Arga (proceso aparte, precalentado al inicio)."""
from __future__ import annotations

import multiprocessing as mp
import os
import time
from typing import Any

_LOGO_FPS = 60
_LOGO_INTERVAL_MS = max(1, int(round(1000 / _LOGO_FPS)))
_BOX_W = 360
_BOX_H = 95

_CTX: mp.context.BaseContext | None = None
_CMD_QUEUE: mp.Queue | None = None
_READY_EVENT: mp.synchronize.Event | None = None
_SERVICE_PROC: mp.Process | None = None


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


def run_logo_anim_service(cmd_queue, ready_event) -> None:
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QWidget

    class _LogoLayer(QWidget):
        """Solo el logo rebotando; el marco lo pinta el diálogo principal."""

        def __init__(self):
            super().__init__(
                None,
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool,
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setFixedSize(_BOX_W, _BOX_H)

            self._pix = QPixmap()
            self._t0 = time.perf_counter()
            self._vx = 144.0
            self._vy = 108.0
            self._x0 = 8.0
            self._y0 = 8.0

            self._timer = QTimer(self)
            self._timer.setTimerType(Qt.TimerType.PreciseTimer)
            self._timer.timeout.connect(self.update)
            self._timer.start(_LOGO_INTERVAL_MS)

        def set_logo_path(self, logo_path: str) -> None:
            self._pix = QPixmap()
            if logo_path and os.path.exists(logo_path):
                self._pix = QPixmap(logo_path).scaled(
                    54,
                    54,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

        def show_at(self, screen_x: int, screen_y: int) -> None:
            self.move(int(screen_x), int(screen_y))
            self._t0 = time.perf_counter()
            self.show()
            self.raise_()

        def paintEvent(self, _event) -> None:
            if self._pix.isNull():
                return
            t = time.perf_counter() - self._t0
            lw = self._pix.width()
            lh = self._pix.height()
            x = _bounce_1d(self._x0, self._vx, 0.0, float(self.width() - lw), t)
            y = _bounce_1d(self._y0, self._vy, 0.0, float(self.height() - lh), t)

            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(int(x), int(y), self._pix)

    app = QApplication.instance() or QApplication([])
    layer: _LogoLayer | None = None

    def _poll_commands() -> None:
        nonlocal layer
        while True:
            try:
                cmd: dict[str, Any] = cmd_queue.get_nowait()
            except Exception:
                break

            op = str(cmd.get("op") or "")
            if op == "show":
                if layer is None:
                    layer = _LogoLayer()
                layer.set_logo_path(str(cmd.get("logo") or ""))
                layer.show_at(int(cmd.get("x") or 0), int(cmd.get("y") or 0))
            elif op == "hide":
                if layer is not None:
                    layer.hide()
            elif op == "quit":
                app.quit()
                return

    poll_timer = QTimer()
    poll_timer.timeout.connect(_poll_commands)
    poll_timer.start(30)

    try:
        ready_event.set()
    except Exception:
        pass
    app.exec()


def _ctx() -> mp.context.BaseContext:
    global _CTX
    if _CTX is None:
        _CTX = mp.get_context("spawn")
    return _CTX


def warm_logo_anim_service(timeout_s: float = 8.0) -> None:
    """Precalienta el proceso de animación (llamar al abrir la app)."""
    global _CMD_QUEUE, _READY_EVENT, _SERVICE_PROC

    if _SERVICE_PROC is not None and _SERVICE_PROC.is_alive():
        return

    ctx = _ctx()
    _CMD_QUEUE = ctx.Queue()
    _READY_EVENT = ctx.Event()
    _SERVICE_PROC = ctx.Process(
        target=run_logo_anim_service,
        args=(_CMD_QUEUE, _READY_EVENT),
        daemon=True,
    )
    _SERVICE_PROC.start()
    try:
        _READY_EVENT.wait(timeout=max(0.5, float(timeout_s)))
    except Exception:
        pass


def show_logo_anim(screen_x: int, screen_y: int, logo_path: str) -> None:
    warm_logo_anim_service()
    if _CMD_QUEUE is None:
        return
    try:
        _CMD_QUEUE.put(
            {
                "op": "show",
                "x": int(screen_x),
                "y": int(screen_y),
                "logo": str(logo_path or ""),
            }
        )
    except Exception:
        pass


def hide_logo_anim() -> None:
    if _CMD_QUEUE is None:
        return
    try:
        _CMD_QUEUE.put({"op": "hide"})
    except Exception:
        pass


def shutdown_logo_anim_service() -> None:
    global _CMD_QUEUE, _READY_EVENT, _SERVICE_PROC

    if _CMD_QUEUE is not None:
        try:
            _CMD_QUEUE.put({"op": "quit"})
        except Exception:
            pass

    proc = _SERVICE_PROC
    _CMD_QUEUE = None
    _READY_EVENT = None
    _SERVICE_PROC = None

    if proc is None:
        return
    try:
        proc.join(timeout=0.8)
    except Exception:
        pass
    if proc.is_alive():
        try:
            proc.terminate()
        except Exception:
            pass
