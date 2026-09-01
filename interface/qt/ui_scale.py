"""
Escala UI relativa a la pantalla (baseline = 1920×1080 @ 100%).

En PCs/VMs más chicas reduce botones, márgenes y diálogos para evitar solapes;
en pantallas ≥ diseño mantiene el look actual (factor ≤ 1.0 para tamaños fijos).
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

# Diseño de referencia (equipo de desarrollo / planta típica Full HD).
DESIGN_WIDTH = 1920
DESIGN_HEIGHT = 1080
# No agrandar controles fijos sobre el diseño (High-DPI de Qt ya escala tipografía).
MAX_UI_FACTOR = 1.0
MIN_UI_FACTOR = 0.72


def _screen_for(widget: QWidget | None = None):
    app = QApplication.instance()
    if widget is not None:
        try:
            win = widget.window().windowHandle() if widget.window() else None
            if win is not None and win.screen() is not None:
                return win.screen()
        except Exception:
            pass
        try:
            center = widget.rect().center()
            global_pt = widget.mapToGlobal(center)
            sc = QGuiApplication.screenAt(global_pt)
            if sc is not None:
                return sc
        except Exception:
            pass
    if app is not None:
        sc = app.primaryScreen()
        if sc is not None:
            return sc
    screens = QGuiApplication.screens()
    return screens[0] if screens else None


def available_size(widget: QWidget | None = None) -> tuple[int, int]:
    sc = _screen_for(widget)
    if sc is None:
        return DESIGN_WIDTH, DESIGN_HEIGHT
    geo = sc.availableGeometry()
    return int(geo.width()), int(geo.height())


def ui_factor(widget: QWidget | None = None) -> float:
    """
    Factor 0.72–1.0 según área usable vs 1920×1080.
    Usa el lado más restrictivo para que quepa alto y ancho.
    """
    aw, ah = available_size(widget)
    fx = aw / float(DESIGN_WIDTH)
    fy = ah / float(DESIGN_HEIGHT)
    f = min(fx, fy, MAX_UI_FACTOR)
    return max(MIN_UI_FACTOR, float(f))


def s(value: float | int, widget: QWidget | None = None, *, min_px: int = 0) -> int:
    """Escala un tamaño en px de diseño al factor de pantalla actual."""
    out = int(round(float(value) * ui_factor(widget)))
    if min_px > 0:
        return max(int(min_px), out)
    return max(1, out)


def sp(padding_css: str, widget: QWidget | None = None) -> str:
    """
    Escala valores en un padding CSS tipo '12px 20px'.
    Si no parsea, devuelve el original.
    """
    parts = str(padding_css or "").replace("px", "").split()
    nums: list[str] = []
    for p in parts:
        try:
            nums.append(f"{s(float(p), widget)}px")
        except ValueError:
            return padding_css
    return " ".join(nums) if nums else padding_css


def configure_high_dpi() -> None:
    """Llamar ANTES de crear QApplication (Qt6: rounding PassThrough)."""
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass


def fit_window(
    widget: QWidget,
    design_w: int,
    design_h: int,
    *,
    max_frac: float = 0.94,
    min_w: int = 320,
    min_h: int = 240,
) -> tuple[int, int]:
    """
    resize() acotado al availableGeometry (deja margen para taskbar).
    Devuelve (w, h) aplicados.
    """
    aw, ah = available_size(widget)
    max_w = max(min_w, int(aw * max_frac))
    max_h = max(min_h, int(ah * max_frac))
    # Escala el diseño si no cabe; no crecer sobre diseño en pantallas grandes
    # salvo que el caller pida un design mayor que la pantalla.
    w = min(int(design_w), max_w)
    h = min(int(design_h), max_h)
    # Si el diseño cabe a medias, aplicar factor uniforme para no deformar.
    f = ui_factor(widget)
    if design_w > max_w or design_h > max_h:
        w = max(min_w, min(max_w, int(round(design_w * f))))
        h = max(min_h, min(max_h, int(round(design_h * f))))
    widget.resize(w, h)
    return w, h


def set_scaled_min_size(
    widget: QWidget, design_w: int, design_h: int, *, floor_w: int = 640, floor_h: int = 480
) -> None:
    widget.setMinimumSize(
        max(floor_w, s(design_w, widget, min_px=floor_w)),
        max(floor_h, s(design_h, widget, min_px=floor_h)),
    )


def set_scaled_fixed_size(widget: QWidget, design_w: int, design_h: int) -> None:
    widget.setFixedSize(s(design_w, widget, min_px=40), s(design_h, widget, min_px=24))


def apply_main_window_chrome(window: QWidget) -> None:
    """Mínimos y geometría inicial seguros para cualquier PC/VM."""
    aw, ah = available_size(window)
    # En pantallas chicas baja el mínimo para no forzar overflow.
    min_w = min(1000, max(720, int(aw * 0.85)))
    min_h = min(650, max(480, int(ah * 0.80)))
    window.setMinimumSize(min_w, min_h)


def scale_info(widget: QWidget | None = None) -> dict[str, Any]:
    aw, ah = available_size(widget)
    f = ui_factor(widget)
    sc = _screen_for(widget)
    dpr = float(sc.devicePixelRatio()) if sc is not None else 1.0
    return {
        "available_w": aw,
        "available_h": ah,
        "factor": round(f, 4),
        "device_pixel_ratio": dpr,
        "design": f"{DESIGN_WIDTH}x{DESIGN_HEIGHT}",
    }
