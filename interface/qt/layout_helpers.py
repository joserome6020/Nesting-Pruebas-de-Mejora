"""Helpers de layout Qt — paridad visual con responsive_layout.py (PanedWindow)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QScrollArea, QSplitter, QVBoxLayout, QWidget


def _soft_shadow(widget: QWidget, blur: int = 20, y_offset: int = 3, alpha: int = 10) -> None:
    """Sombra tipo Herinox: contorno suave sin línea dura."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y_offset)
    effect.setColor(QColor(15, 23, 42, alpha))
    widget.setGraphicsEffect(effect)


def make_card(parent: QWidget | None = None, shadow: bool = True) -> QFrame:
    card = QFrame(parent)
    card.setObjectName("CardFrame")
    card.setFrameShape(QFrame.Shape.NoFrame)
    if shadow:
        _soft_shadow(card)
    return card


def make_herinox_card(parent: QWidget | None = None, shadow: bool = True) -> QFrame:
    """Tarjetón con borde Herinox (#d8dfeb) para filas/modales."""
    card = QFrame(parent)
    card.setObjectName("HerinoxCard")
    card.setFrameShape(QFrame.Shape.NoFrame)
    if shadow:
        _soft_shadow(card, blur=18, y_offset=3, alpha=12)
    return card


def make_panel_dark(parent: QWidget | None = None) -> QFrame:
    panel = QFrame(parent)
    panel.setObjectName("DarkPanel")
    panel.setFrameShape(QFrame.Shape.NoFrame)
    return panel


def make_horizontal_splitter(initial_left: int = 680) -> QSplitter:
    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setObjectName("MainSplitter")
    splitter.setChildrenCollapsible(False)
    splitter.setHandleWidth(8)
    splitter.setOpaqueResize(True)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([initial_left, max(480, initial_left)])
    return splitter


def finalize_splitter(splitter: QSplitter, min_left: int = 280, min_right: int = 360) -> None:
    """Aplica mínimos y cursor de resize tras añadir los paneles."""
    if splitter.count() >= 1:
        splitter.widget(0).setMinimumWidth(min_left)
    if splitter.count() >= 2:
        splitter.widget(1).setMinimumWidth(min_right)
    for i in range(1, splitter.count()):
        handle = splitter.handle(i)
        if handle is not None:
            handle.setCursor(Qt.CursorShape.SplitHCursor)


def make_scroll(parent: QWidget | None = None) -> QScrollArea:
    scroll = QScrollArea(parent)
    scroll.setObjectName("AppScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return scroll


def make_scroll_content() -> tuple[QWidget, QVBoxLayout]:
    inner = QWidget()
    inner.setObjectName("ScrollContent")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(4, 4, 8, 8)
    lay.setSpacing(4)
    return inner, lay
