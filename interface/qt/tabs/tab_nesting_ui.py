"""Construcción UI Qt para TabNesting."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from interface.qt.nesting_canvas import VisorNesting
from interface.qt.widgets.herinox_switch import HerinoxSwitch
from interface.qt.widgets.nesting_ribbon import build_nesting_ribbon
from interface.qt.dialogs.nesting_modals import abrir_modal_configuracion, abrir_modal_transferencia
from interface.qt.layout_helpers import (
    finalize_splitter,
    make_card,
    make_horizontal_splitter,
    make_panel_dark,
    make_scroll,
    make_scroll_content,
)
from interface.qt.theme import COLOR_GRIS_DARK, COLOR_TEXTO_TITULO, apply_herinox_combo, apply_push_button

DEFAULT_KERF_IN = 0.15
PANEL_TOOLS_MIN_WIDTH = 234


def _panel_action_btn(text: str, bg: str = "#334155", **kwargs) -> QPushButton:
    btn = QPushButton(text)
    btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    style = {"font_size": 10, "padding": "7px 12px"}
    style.update(kwargs)
    apply_push_button(btn, bg, **style)
    return btn

_STYLE_HDR = "color:#64748B;font-size:10px;font-weight:700;letter-spacing:0.4px;background:transparent;"
_STYLE_VAL = "color:#E2E8F0;font-size:12px;font-weight:600;background:transparent;"
_STYLE_MUTED = "color:#94A3B8;font-size:11px;background:transparent;"
_STYLE_SEC = "color:#64748B;font-size:10px;font-weight:700;background:transparent;padding-top:4px;"


def _sep(parent_lay):
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("background:#334155;max-height:1px;border:none;")
    parent_lay.addWidget(line)


def _section_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_STYLE_SEC)
    return lbl


class _VisorOverlayHost(QWidget):
    """Contenedor del visor; el panel de herramientas flota encima sin reducir el canvas."""

    def __init__(self, tab, parent=None):
        super().__init__(parent)
        self._tab = tab
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)

    def set_visor(self, visor):
        self._lay.addWidget(visor)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        repos = getattr(self._tab, "_reposicionar_panel_ajuste", None)
        if callable(repos):
            repos()


# Fallback si aún no hay geometría del QTabBar (p. ej. primer frame).
NEST_SIDEBAR_WIDTH_FALLBACK_PX = 520
NEST_TAB_INDEX = 3  # FILES=0, PARTS=1, SHEETS=2, NESTING=3


def _nest_sidebar_width_from_tabbar(tab) -> int:
    """Ancho del panel lista = borde derecho de la pestaña NESTING (sin pad de colisión)."""
    tabview = getattr(getattr(tab, "app", None), "tabview", None)
    if tabview is None:
        return NEST_SIDEBAR_WIDTH_FALLBACK_PX
    bar = tabview.tabBar()
    if bar.count() <= NEST_TAB_INDEX:
        return NEST_SIDEBAR_WIDTH_FALLBACK_PX
    rect = bar.tabRect(NEST_TAB_INDEX)
    if rect.width() <= 0:
        return NEST_SIDEBAR_WIDTH_FALLBACK_PX
    pad = getattr(tab, "_nest_tab_width_pad", None)
    pad_w = int(pad.width()) if pad is not None else 0
    return max(280, int(rect.right()) - max(0, pad_w))


def apply_nest_sidebar_width(tab) -> None:
    if getattr(tab, "_nest_sidebar_user_resized", False):
        return
    splitter = getattr(tab, "_nest_splitter", None)
    if splitter is None:
        return
    w = _nest_sidebar_width_from_tabbar(tab)
    splitter.blockSignals(True)
    try:
        splitter.setSizes([w, 10_000])
    finally:
        splitter.blockSignals(False)


def schedule_nest_sidebar_sync(tab) -> None:
    for delay_ms in (0, 80, 300):
        QTimer.singleShot(delay_ms, lambda t=tab: apply_nest_sidebar_width(t))


def build_tab_nesting_ui(tab) -> None:
    root = QVBoxLayout(tab)
    root.setContentsMargins(8, 4, 8, 0)
    root.setSpacing(6)

    # Cinta a ANCHO COMPLETO (arriba del splitter): todos los comandos caben.
    tab.frame_header_der = build_nesting_ribbon(tab)
    root.addWidget(tab.frame_header_der)

    splitter = make_horizontal_splitter(NEST_SIDEBAR_WIDTH_FALLBACK_PX)
    tab._nest_splitter = splitter
    tab._nest_sidebar_user_resized = False

    def _on_splitter_moved(_pos, _index):
        tab._nest_sidebar_user_resized = True

    splitter.splitterMoved.connect(_on_splitter_moved)

    panel_izq = make_card()
    izq_lay = QVBoxLayout(panel_izq)
    izq_lay.setContentsMargins(20, 20, 12, 20)

    fila_cantidad = QHBoxLayout()
    fila_cantidad.setContentsMargins(0, 0, 0, 0)
    tab.lbl_cantidad = QLabel("CANTIDAD: -")
    tab.lbl_cantidad.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
    fila_cantidad.addWidget(tab.lbl_cantidad)
    fila_cantidad.addStretch()
    tab.lbl_piezas_totales = QLabel("PIEZAS TOTALES: -")
    tab.lbl_piezas_totales.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
    tab.lbl_piezas_totales.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    fila_cantidad.addWidget(tab.lbl_piezas_totales)
    izq_lay.addLayout(fila_cantidad)

    tab.cmb_lotes = QComboBox()
    apply_herinox_combo(tab.cmb_lotes)
    tab.cmb_lotes.addItem("SIN ÓRDENES")
    tab.cmb_lotes.setEnabled(False)
    tab.cmb_lotes.currentTextChanged.connect(tab.on_lote_selected)
    izq_lay.addWidget(tab.cmb_lotes)

    tab.btn_run_nest = QPushButton("EJECUTAR NESTING")
    tab.btn_run_nest.setFixedHeight(55)
    apply_push_button(tab.btn_run_nest, COLOR_GRIS_DARK, font_size=14, padding="10px 16px")
    tab.btn_run_nest.clicked.connect(tab.ejecutar_nesting)
    izq_lay.addWidget(tab.btn_run_nest)

    tab.lista_hojas = make_scroll()
    tab._lista_hojas_inner, tab._lista_hojas_layout = make_scroll_content()
    tab._lista_hojas_layout.setSpacing(2)
    tab.lista_hojas.setWidget(tab._lista_hojas_inner)
    izq_lay.addWidget(tab.lista_hojas, 1)

    fila_placas = QHBoxLayout()
    fila_placas.setContentsMargins(0, 6, 0, 0)
    tab.lbl_placas_totales = QLabel("PLACAS: -")
    tab.lbl_placas_totales.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
    tab.lbl_placas_totales.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    fila_placas.addWidget(tab.lbl_placas_totales)
    fila_placas.addStretch()
    izq_lay.addLayout(fila_placas)

    splitter.addWidget(panel_izq)

    panel_der_wrap = QWidget()
    der_wrap_lay = QVBoxLayout(panel_der_wrap)
    der_wrap_lay.setContentsMargins(0, 0, 0, 0)
    der_wrap_lay.setSpacing(0)

    tab.panel_der = make_panel_dark()
    panel_lay = QVBoxLayout(tab.panel_der)
    panel_lay.setContentsMargins(10, 10, 10, 10)

    tab.visor_host = _VisorOverlayHost(tab, tab.panel_der)
    panel_lay.addWidget(tab.visor_host, 1)

    tab.visor = VisorNesting(tab.visor_host, tab.app, tab.on_piece_selected)
    tab.visor_host.set_visor(tab.visor)

    # Acciones de placa/pieza viven en la cinta («Placa»). Stubs ocultos por compat.
    tab.frame_ajuste_container = QFrame(tab.visor_host)
    tab.frame_ajuste_container.hide()
    tab.ajuste_desplegado = False
    tab.panel_ajuste_contenido = QWidget(tab)
    tab.panel_ajuste_contenido.hide()
    tab.btn_toggle_ajuste = QPushButton(tab)
    tab.btn_toggle_ajuste.hide()
    tab.lbl_placa_resumen = QLabel("-", tab)
    tab.lbl_placa_resumen.hide()
    tab.lbl_placa_stats = QLabel("-", tab)
    tab.lbl_placa_stats.hide()
    tab.lbl_placa_dims = QLabel("-", tab)
    tab.lbl_placa_dims.hide()
    tab.lbl_pieza_sel = QLabel("SIN SELECCIÓN", tab)
    tab.lbl_pieza_sel.hide()
    tab.lbl_edicion_libre = QLabel("", tab)
    tab.lbl_edicion_libre.hide()
    tab.frame_pieza_sel = QWidget(tab)
    tab.frame_pieza_sel.hide()
    tab._panel_tools_scroll = None

    # Kerf / optimización: widgets ocultos (CONFIGURACIÓN global + lógica de renesteo)
    tab._kern_opt_host = QWidget(tab)
    tab._kern_opt_host.hide()
    _ko = QVBoxLayout(tab._kern_opt_host)
    tab.ent_kerf = QLineEdit(str(DEFAULT_KERF_IN))
    tab.cmb_opt = QComboBox()
    tab.cmb_opt.addItems(["OPTIMIZAR LARGO Y ANCHO", "OPTIMIZAR LARGO", "OPTIMIZAR ANCHO"])
    _ko.addWidget(tab.ent_kerf)
    _ko.addWidget(tab.cmb_opt)

    der_wrap_lay.addWidget(tab.panel_der, 1)

    splitter.addWidget(panel_der_wrap)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    finalize_splitter(splitter, min_left=280, min_right=480)
    apply_nest_sidebar_width(tab)
    schedule_nest_sidebar_sync(tab)
    root.addWidget(splitter, 1)
