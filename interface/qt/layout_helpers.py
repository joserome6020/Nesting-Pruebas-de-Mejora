"""Helpers de layout Qt — paridad visual con responsive_layout.py (PanedWindow)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


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
    # Izquierda = ancho fijo; derecha absorbe el resto (evita 50/50 si initial_left >= 480).
    splitter.setSizes([initial_left, 10_000])
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


_HSCROLL_BAR_SS = """
QScrollArea#{name} QScrollBar:horizontal {{
    width: 0px;
    height: 0px;
    background: transparent;
    border: none;
}}
QScrollArea#{name} QScrollBar:vertical {{
    width: 0px;
    height: 0px;
}}
"""


def make_hscroll_toolbar(
    *,
    height_design: int = 48,
    min_height: int = 40,
    object_name: str = "HScrollToolbar",
    inner: QWidget | None = None,
) -> tuple[QScrollArea, QWidget, QHBoxLayout]:
    """
    Barra horizontal: si no cabe, rueda del mouse desplaza (sin barra visible).
    Devuelve (scroll_area, inner_widget, hbox_layout).
    """
    from interface.qt.ui_scale import s

    h = s(height_design, min_px=min_height)
    scroll = QScrollArea()
    scroll.setObjectName(object_name)
    scroll.setFixedHeight(h)
    scroll.setWidgetResizable(False)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setStyleSheet(_HSCROLL_BAR_SS.format(name=object_name))

    body = inner or QWidget()
    lay = QHBoxLayout(body)
    lay.setContentsMargins(
        s(12, min_px=8),
        s(6, min_px=4),
        s(12, min_px=8),
        s(6, min_px=4),
    )
    lay.setSpacing(s(10, min_px=6))
    lay.setSizeConstraint(QHBoxLayout.SizeConstraint.SetFixedSize)
    body.adjustSize()
    scroll.setWidget(body)

    def _wheel_hscroll(event):
        bar = scroll.horizontalScrollBar()
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta and bar.maximum() > 0:
            bar.setValue(bar.value() - int(delta / 2))
            event.accept()
            return
        event.ignore()

    scroll.wheelEvent = _wheel_hscroll  # type: ignore[method-assign]
    return scroll, body, lay


def finalize_hscroll_toolbar(scroll: QScrollArea, body: QWidget) -> None:
    """Llamar al terminar de llenar la barra: fija ancho real del contenido."""
    body.adjustSize()
    hint = body.sizeHint()
    body.setMinimumWidth(max(hint.width(), body.width()))
    scroll.setWidget(body)


def make_scroll(parent: QWidget | None = None) -> QScrollArea:
    scroll = QScrollArea(parent)
    scroll.setObjectName("AppScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return scroll


def make_scroll_content() -> tuple[QWidget, QVBoxLayout]:
    """Contenido de scroll empaquetado arriba (sin alargar filas cuando hay pocas)."""
    inner = QWidget()
    inner.setObjectName("ScrollContent")
    # Preferred vertical: el stretch final absorbe el sobrante del viewport.
    inner.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(4, 4, 8, 8)
    lay.setSpacing(4)
    lay.setAlignment(Qt.AlignmentFlag.AlignTop)
    lay.addStretch(1)
    return inner, lay


def layout_insert_before_stretch(layout: QVBoxLayout, widget: QWidget) -> None:
    """Inserta un widget antes del stretch final (crea uno si no existe)."""
    if layout is None or widget is None:
        return
    idx = layout.count() - 1
    if idx >= 0:
        item = layout.itemAt(idx)
        if item is not None and item.spacerItem() is not None:
            layout.insertWidget(idx, widget)
            return
    layout.addWidget(widget)
    layout.addStretch(1)


def layout_ensure_bottom_stretch(layout: QVBoxLayout) -> None:
    """Asegura un único stretch al final del layout (idempotente)."""
    if layout is None:
        return
    n = layout.count()
    if n > 0:
        item = layout.itemAt(n - 1)
        if item is not None and item.spacerItem() is not None:
            return
    layout.addStretch(1)
