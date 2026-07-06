"""Despacha callbacks al hilo principal de Qt (seguro desde threading.Thread)."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot


class MainThreadBridge(QObject):
    invoke = Signal(object, tuple)

    def __init__(self):
        super().__init__()
        self.invoke.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)

    @Slot(object, tuple)
    def _dispatch(self, fn, args):
        try:
            fn(*args)
        except Exception as exc:
            import traceback

            print(f"[UI bridge] Error en callback: {exc}")
            traceback.print_exc()


_bridge: MainThreadBridge | None = None


def init_thread_bridge() -> MainThreadBridge:
    global _bridge
    if _bridge is None:
        _bridge = MainThreadBridge()
    return _bridge


def call_on_main(fn, *args) -> None:
    init_thread_bridge().invoke.emit(fn, args)


def call_on_main_later(ms: int, fn, *args) -> None:
    def _schedule():
        QTimer.singleShot(max(0, int(ms)), lambda: fn(*args))

    call_on_main(_schedule)
