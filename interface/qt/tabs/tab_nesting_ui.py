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

DEFAULT_KERF_IN = 0.3
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
    """Ancho del panel lista = borde derecho de la pestaña NESTING."""
    tabview = getattr(getattr(tab, "app", None), "tabview", None)
    if tabview is None:
        return NEST_SIDEBAR_WIDTH_FALLBACK_PX
    bar = tabview.tabBar()
    if bar.count() <= NEST_TAB_INDEX:
        return NEST_SIDEBAR_WIDTH_FALLBACK_PX
    rect = bar.tabRect(NEST_TAB_INDEX)
    if rect.width() <= 0:
        return NEST_SIDEBAR_WIDTH_FALLBACK_PX
    return max(280, int(rect.right()))


def apply_nest_sidebar_width(tab) -> None:
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
    root.setContentsMargins(0, 0, 0, 0)

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

    splitter.addWidget(panel_izq)

    panel_der_wrap = QWidget()
    der_wrap_lay = QVBoxLayout(panel_der_wrap)
    der_wrap_lay.setContentsMargins(0, 0, 0, 0)
    der_wrap_lay.setSpacing(6)

    tab.frame_header_der = QFrame()
    tab.frame_header_der.setObjectName("ToolbarStrip")
    hdr = QHBoxLayout(tab.frame_header_der)
    hdr.setContentsMargins(8, 6, 8, 6)
    hdr.setSpacing(6)

    def _btn(text, slot, bg=COLOR_GRIS_DARK, enabled=True):
        b = QPushButton(text)
        b.setFixedHeight(30)
        apply_push_button(b, bg, font_size=11, padding="6px 12px")
        b.setEnabled(enabled)
        b.clicked.connect(slot)
        hdr.addWidget(b)
        return b

    tab.btn_exportar = _btn("EXPORTAR DXF/STEP", tab.exportar_resultados_dxf)
    tab.btn_ver_lotes = _btn("HISTORIAL DE W.O.", tab.reabrir_modal_escenarios)
    tab.btn_costos = _btn("COSTOS DE ORDEN", lambda: __import__("interface.qt.dialogs.nesting_modals", fromlist=["abrir_modal_costos"]).abrir_modal_costos(tab))
    tab.btn_nesting_largos = _btn("NESTEO DE LARGOS", tab.abrir_nesting_largos, bg="#455E75")
    tab.btn_config = _btn("CONFIGURACIÓN", lambda: abrir_modal_configuracion(tab))
    tab.btn_pdf_nesting = _btn("PDF NESTING", tab.exportar_reporte_pdf_nesting)
    tab.btn_editar_lote = _btn("EDITAR LOTE", tab.editar_lote_activo)
    tab.btn_guardar_nest = _btn("GUARDAR NEST", tab.guardar_workspace_nesting)
    tab.btn_abrir_nest = _btn("ABRIR NEST", tab.abrir_workspace_nesting)
    hdr.addStretch()
    der_wrap_lay.addWidget(tab.frame_header_der)

    tab.panel_der = make_panel_dark()
    panel_lay = QVBoxLayout(tab.panel_der)
    panel_lay.setContentsMargins(10, 10, 10, 10)

    tab.visor_host = _VisorOverlayHost(tab, tab.panel_der)
    panel_lay.addWidget(tab.visor_host, 1)

    tab.visor = VisorNesting(tab.visor_host, tab.app, tab.on_piece_selected)
    tab.visor_host.set_visor(tab.visor)

    tab.frame_ajuste_container = QFrame(tab.visor_host)
    tab.frame_ajuste_container.setStyleSheet("background:transparent;border:none;")
    tab.frame_ajuste_container.hide()
    tab.ajuste_desplegado = False
    adj_lay = QVBoxLayout(tab.frame_ajuste_container)
    adj_lay.setContentsMargins(0, 0, 0, 0)
    adj_lay.setSpacing(4)

    tab.panel_ajuste_contenido = QFrame()
    tab.panel_ajuste_contenido.setMinimumWidth(PANEL_TOOLS_MIN_WIDTH)
    tab.panel_ajuste_contenido.setStyleSheet(
        "background:#0F172A;border:1px solid #475569;border-radius:10px;"
    )
    tab.panel_ajuste_contenido.hide()
    pc_lay = QVBoxLayout(tab.panel_ajuste_contenido)
    pc_lay.setContentsMargins(12, 10, 12, 10)
    pc_lay.setSpacing(6)

    scroll_panel = QScrollArea()
    scroll_panel.setObjectName("PanelToolsScroll")
    scroll_panel.setWidgetResizable(True)
    scroll_panel.setFrameShape(QFrame.Shape.NoFrame)
    scroll_panel.setStyleSheet(
        "QScrollArea#PanelToolsScroll{background:transparent;border:none;}"
        "QScrollArea#PanelToolsScroll QScrollBar:vertical{"
        "background:transparent;width:6px;margin:2px 0;}"
        "QScrollArea#PanelToolsScroll QScrollBar::handle:vertical{"
        "background:rgba(148,163,184,0.38);border-radius:3px;min-height:22px;}"
        "QScrollArea#PanelToolsScroll QScrollBar::handle:vertical:hover{"
        "background:rgba(148,163,184,0.62);}"
        "QScrollArea#PanelToolsScroll QScrollBar::add-line:vertical,"
        "QScrollArea#PanelToolsScroll QScrollBar::sub-line:vertical{height:0;}"
        "QScrollArea#PanelToolsScroll QScrollBar::add-page:vertical,"
        "QScrollArea#PanelToolsScroll QScrollBar::sub-page:vertical{background:transparent;}"
    )
    scroll_panel.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll_panel.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    tab._panel_tools_scroll = scroll_panel
    scroll_inner = QWidget()
    scroll_inner.setStyleSheet("background:transparent;")
    scroll_inner.setMinimumWidth(PANEL_TOOLS_MIN_WIDTH - 28)
    si_lay = QVBoxLayout(scroll_inner)
    si_lay.setContentsMargins(0, 0, 6, 0)
    si_lay.setSpacing(5)

    si_lay.addWidget(_section_title("PLACA ACTIVA"))
    tab.lbl_placa_resumen = QLabel("-")
    tab.lbl_placa_resumen.setStyleSheet(_STYLE_VAL)
    tab.lbl_placa_resumen.setWordWrap(True)
    si_lay.addWidget(tab.lbl_placa_resumen)
    tab.lbl_placa_stats = QLabel("-")
    tab.lbl_placa_stats.setStyleSheet(_STYLE_MUTED)
    tab.lbl_placa_stats.setWordWrap(True)
    si_lay.addWidget(tab.lbl_placa_stats)
    tab.lbl_placa_dims = QLabel("-")
    tab.lbl_placa_dims.setStyleSheet(_STYLE_MUTED)
    tab.lbl_placa_dims.setWordWrap(True)
    si_lay.addWidget(tab.lbl_placa_dims)

    _sep(si_lay)
    si_lay.addWidget(_section_title("PIEZA SELECCIONADA"))
    tab.frame_pieza_sel = QFrame()
    tab.frame_pieza_sel.setStyleSheet("background:transparent;border:none;")
    fps_lay = QVBoxLayout(tab.frame_pieza_sel)
    fps_lay.setContentsMargins(0, 0, 0, 0)
    fps_lay.setSpacing(6)
    tab.lbl_pieza_sel = QLabel("SIN SELECCIÓN — CLIC EN EL CANVAS")
    tab.lbl_pieza_sel.setStyleSheet(_STYLE_MUTED)
    tab.lbl_pieza_sel.setWordWrap(True)
    fps_lay.addWidget(tab.lbl_pieza_sel)

    tab.btn_transferir = _panel_action_btn("MUDAR A OTRA PLACA", padding="6px 12px")
    tab.btn_transferir.setEnabled(False)
    tab.btn_transferir.clicked.connect(lambda: abrir_modal_transferencia(tab))
    fps_lay.addWidget(tab.btn_transferir)

    rot_row = QHBoxLayout()
    rot_row.setSpacing(4)
    tab.btn_rot_90 = QPushButton("90°")
    tab.btn_rot_90.setEnabled(False)
    apply_push_button(tab.btn_rot_90, "#334155", font_size=10, padding="6px 8px")
    tab.btn_rot_90.clicked.connect(lambda: tab.visor.rotar_pieza_seleccionada(90))
    tab.btn_rot_m1 = QPushButton("-1°")
    tab.btn_rot_m1.setEnabled(False)
    apply_push_button(tab.btn_rot_m1, "#334155", font_size=10, padding="6px 8px")
    tab.btn_rot_m1.clicked.connect(lambda: tab.visor.rotar_pieza_seleccionada(-1))
    tab.btn_rot_p1 = QPushButton("+1°")
    tab.btn_rot_p1.setEnabled(False)
    apply_push_button(tab.btn_rot_p1, "#334155", font_size=10, padding="6px 8px")
    tab.btn_rot_p1.clicked.connect(lambda: tab.visor.rotar_pieza_seleccionada(1))
    tab.btn_limpiar_sel = QPushButton("LIMPIAR")
    apply_push_button(tab.btn_limpiar_sel, "#1E293B", font_size=10, padding="6px 8px")
    tab.btn_limpiar_sel.clicked.connect(tab.panel_limpiar_seleccion)
    rot_row.addWidget(tab.btn_rot_m1)
    rot_row.addWidget(tab.btn_rot_p1)
    rot_row.addWidget(tab.btn_rot_90)
    rot_row.addWidget(tab.btn_limpiar_sel)
    fps_lay.addLayout(rot_row)

    tab.switch_edicion_libre = HerinoxSwitch(
        label_on="EDICIÓN LIBRE ENTRE SELECCIÓN",
        label_off="EDICIÓN LIBRE (OFF)",
        checked=False,
    )
    tab.switch_edicion_libre.setEnabled(False)
    tab.switch_edicion_libre.toggled.connect(tab._on_toggle_edicion_libre)
    fps_lay.addWidget(tab.switch_edicion_libre)
    tab.lbl_edicion_libre = QLabel(
        "SOLO COLISIONA CON PLACA Y PIEZAS FUERA DEL GRUPO. EN MODO ACTIVO: MORADO."
    )
    tab.lbl_edicion_libre.setWordWrap(True)
    tab.lbl_edicion_libre.setStyleSheet("color:#94A3B8;font-size:10px;background:transparent;")
    fps_lay.addWidget(tab.lbl_edicion_libre)

    si_lay.addWidget(tab.frame_pieza_sel)

    _sep(si_lay)
    si_lay.addWidget(_section_title("ACCIONES DE PLACA"))
    tab.btn_panel_renest_placa = _panel_action_btn("RENESTEAR ESTA PLACA")
    tab.btn_panel_renest_placa.clicked.connect(tab.panel_renestear_placa)
    si_lay.addWidget(tab.btn_panel_renest_placa)

    tab.btn_panel_renest_calibre = _panel_action_btn("RENESTEAR CALIBRE COMPLETO")
    tab.btn_panel_renest_calibre.clicked.connect(tab.panel_renestear_calibre)
    si_lay.addWidget(tab.btn_panel_renest_calibre)

    tab.btn_panel_cambiar_placa = _panel_action_btn("CAMBIAR PLACA MADRE")
    tab.btn_panel_cambiar_placa.clicked.connect(tab.panel_cambiar_placa_madre)
    si_lay.addWidget(tab.btn_panel_cambiar_placa)

    scroll_panel.setWidget(scroll_inner)
    scroll_panel.setMinimumHeight(160)
    pc_lay.addWidget(scroll_panel, 1)

    util_row = QHBoxLayout()
    tab.btn_ajustar_vista = QPushButton("AJUSTAR VISTA")
    apply_push_button(tab.btn_ajustar_vista, "#1E293B", font_size=10, padding="5px 10px")
    tab.btn_ajustar_vista.clicked.connect(tab.panel_ajustar_vista)
    util_row.addWidget(tab.btn_ajustar_vista)
    util_row.addStretch()
    pc_lay.addLayout(util_row)

    # Kerf / optimización: widgets ocultos (CONFIGURACIÓN global + lógica de renesteo)
    tab._kern_opt_host = QWidget(tab)
    tab._kern_opt_host.hide()
    _ko = QVBoxLayout(tab._kern_opt_host)
    tab.ent_kerf = QLineEdit(str(DEFAULT_KERF_IN))
    tab.cmb_opt = QComboBox()
    tab.cmb_opt.addItems(["OPTIMIZAR LARGO Y ANCHO", "OPTIMIZAR LARGO", "OPTIMIZAR ANCHO"])
    _ko.addWidget(tab.ent_kerf)
    _ko.addWidget(tab.cmb_opt)

    adj_lay.addWidget(tab.panel_ajuste_contenido)
    tab.btn_toggle_ajuste = QPushButton("HERRAMIENTAS DE PLACA")
    tab.btn_toggle_ajuste.setMinimumWidth(PANEL_TOOLS_MIN_WIDTH)
    tab.btn_toggle_ajuste.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    apply_push_button(tab.btn_toggle_ajuste, "#FFFFFF", font_size=11)
    tab.btn_toggle_ajuste.clicked.connect(tab.toggle_ajuste_placa)
    adj_lay.addWidget(tab.btn_toggle_ajuste)
    tab.frame_ajuste_container.raise_()

    der_wrap_lay.addWidget(tab.panel_der, 1)

    splitter.addWidget(panel_der_wrap)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    finalize_splitter(splitter, min_left=280, min_right=480)
    apply_nest_sidebar_width(tab)
    schedule_nest_sidebar_sync(tab)
    root.addWidget(splitter)
