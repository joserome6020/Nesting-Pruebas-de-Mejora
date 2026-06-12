"""Diagnóstico del visor nesting: errores, operaciones lentas y detección de UI trabada."""
from __future__ import annotations

import faulthandler
import os
import sys
import time
import traceback
from contextlib import contextmanager
from functools import wraps

# Activo por defecto. Desactivar: ARGA_VISOR_DIAG=0
_ENABLED = os.getenv("ARGA_VISOR_DIAG", "1").strip().lower() not in {"0", "false", "no", "off"}
_SLOW_MS = float(os.getenv("ARGA_VISOR_SLOW_MS", "80"))
_SLOW_INTERACTION_MS = float(os.getenv("ARGA_VISOR_SLOW_INTERACTION_MS", "16"))
_FREEZE_MS = float(os.getenv("ARGA_VISOR_FREEZE_MS", "350"))
_faulthandler_armed = False
_freeze_detector = None


class _FreezeDetector:
    """Hilo auxiliar: detecta operaciones que bloquean el hilo UI (sin depender del event loop)."""

    def __init__(self):
        import threading

        self._lock = threading.Lock()
        self._busy_label = ""
        self._busy_since = 0.0
        self._last_alert = 0.0
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="visor-freeze-detect")
        self._thread.start()

    def set_busy(self, label: str) -> None:
        with self._lock:
            self._busy_label = label
            self._busy_since = time.perf_counter()

    def clear_busy(self) -> None:
        with self._lock:
            self._busy_label = ""
            self._busy_since = 0.0

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        import threading

        while self._running:
            time.sleep(0.2)
            with self._lock:
                label = self._busy_label
                since = self._busy_since
                last_alert = self._last_alert
            if not label or since <= 0:
                continue
            elapsed_ms = (time.perf_counter() - since) * 1000.0
            if elapsed_ms < _FREEZE_MS:
                continue
            if time.perf_counter() - last_alert < 1.5:
                continue
            with self._lock:
                if self._busy_label != label:
                    continue
                self._last_alert = time.perf_counter()
            log_freeze(label, elapsed_ms)


def freeze_detector() -> _FreezeDetector:
    global _freeze_detector
    if _freeze_detector is None and _ENABLED:
        _freeze_detector = _FreezeDetector()
    return _freeze_detector


@contextmanager
def ui_busy(label: str):
    """Marca bloque UI; hilo auxiliar avisa si supera FREEZE_MS."""
    det = freeze_detector()
    if det:
        det.set_busy(label)
    try:
        yield
    finally:
        if det:
            det.clear_busy()


def enabled() -> bool:
    return _ENABLED


def freeze_threshold_ms() -> float:
    return _FREEZE_MS


def log(msg: str) -> None:
    if _ENABLED:
        print(msg, flush=True)


def log_error(context: str, exc: BaseException) -> None:
    if not _ENABLED:
        return
    print(f"[VISOR ERROR] {context}: {exc}", flush=True)
    traceback.print_exc()


def log_slow(label: str, elapsed_ms: float, detail: str = "", *, interaction: bool = False) -> None:
    if not _ENABLED:
        return
    threshold = _SLOW_INTERACTION_MS if interaction else _SLOW_MS
    if elapsed_ms < threshold:
        return
    suffix = f" | {detail}" if detail else ""
    print(f"[VISOR LENTO] {label} tardó {elapsed_ms:.0f}ms{suffix}", flush=True)
    if elapsed_ms >= 500:
        print(
            "[VISOR AVISO] La UI puede sentirse trabada. "
            "Causa habitual: rebuild completo de escena o validación geométrica pesada.",
            flush=True,
        )


def log_freeze(label: str, elapsed_ms: float) -> None:
    if not _ENABLED:
        return
    print(
        f"[VISOR TRABA] UI bloqueada ~{elapsed_ms:.0f}ms en «{label}» "
        "(el hilo principal no terminó la operación)",
        flush=True,
    )
    print(
        "[VISOR TRABA] Si no ves [VISOR LENTO], el bloqueo ocurre DENTRO de esa operación "
        "(p. ej. pintado Qt, Shapely, o ratón capturado).",
        flush=True,
    )
    _arm_faulthandler()


def log_stuck_state(detail: str) -> None:
    if not _ENABLED:
        return
    print(f"[VISOR RECUPERACIÓN] {detail}", flush=True)


def _arm_faulthandler() -> None:
    global _faulthandler_armed
    if _faulthandler_armed or not _ENABLED:
        return
    _faulthandler_armed = True
    try:
        faulthandler.enable(file=sys.stderr, all_threads=True)
        # Un solo volcado (repeat=True inundaba la terminal en hojas grandes).
        faulthandler.dump_traceback_later(int(max(4, _FREEZE_MS / 500)), repeat=False, file=sys.stderr)
        log("[VISOR DIAG] faulthandler activo (un volcado si la UI sigue bloqueada)")

    except Exception:
        pass


@contextmanager
def measure(label: str, detail: str = "", *, interaction: bool = False):
    t0 = time.perf_counter()
    try:
        yield
    except Exception as exc:
        log_error(label, exc)
        raise
    finally:
        log_slow(label, (time.perf_counter() - t0) * 1000.0, detail, interaction=interaction)


@contextmanager
def busy_watch(label: str):
    """Marca operación en curso; si supera FREEZE_MS sin salir, log_freeze la reporta."""
    t0 = time.perf_counter()
    try:
        yield
    except Exception as exc:
        log_error(label, exc)
        raise
    finally:
        elapsed = (time.perf_counter() - t0) * 1000.0
        log_slow(label, elapsed, interaction=True)
        if elapsed >= _FREEZE_MS:
            log_freeze(label, elapsed)


def guard(label: str):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                log_error(label, exc)
                raise
            finally:
                log_slow(label, (time.perf_counter() - t0) * 1000.0)

        return wrapper

    return deco
