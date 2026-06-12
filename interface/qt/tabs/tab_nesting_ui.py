"""Construcción UI Qt para TabNesting."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from interface.qt.nesting_canvas import VisorNesting
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


class _VisorOverlayHost(QWidget):
    """Contenedor del visor; el panel de ajuste flota encima sin reducir el canvas."""

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


def build_tab_nesting_ui(tab) -> None:
    root = QVBoxLayout(tab)
    root.setContentsMargins(0, 0, 0, 0)

    splitter = make_horizontal_splitter(520)

    panel_izq = make_card()
    izq_lay = QVBoxLayout(panel_izq)
    izq_lay.setContentsMargins(20, 20, 12, 20)

    tab.lbl_cantidad = QLabel("Cantidad: -")
    tab.lbl_cantidad.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
    izq_lay.addWidget(tab.lbl_cantidad)

    tab.cmb_lotes = QComboBox()
    apply_herinox_combo(tab.cmb_lotes)
    tab.cmb_lotes.addItem("SIN ÓRDENES")
    tab.cmb_lotes.setEnabled(False)
    tab.cmb_lotes.currentTextChanged.connect(tab.on_lote_selected)
    izq_lay.addWidget(tab.cmb_lotes)

    tab.btn_run_nest = QPushButton("🚀 EJECUTAR NESTING")
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

    tab.btn_exportar = _btn("💾 EXPORTAR DXF/STEP", tab.exportar_resultados_dxf)
    tab.btn_ver_lotes = _btn("📄 HISTORIAL DE W.O.", tab.reabrir_modal_escenarios)
    tab.btn_costos = _btn("💲 COSTOS DE ORDEN", lambda: __import__("interface.qt.dialogs.nesting_modals", fromlist=["abrir_modal_costos"]).abrir_modal_costos(tab))
    tab.btn_config = _btn("⚙️ CONFIGURACIÓN", lambda: abrir_modal_configuracion(tab))
    tab.btn_pdf_nesting = _btn("🧾 PDF NESTING", tab.exportar_reporte_pdf_nesting)
    tab.btn_editar_lote = _btn("✏️ EDITAR LOTE", tab.editar_lote_activo)
    tab.btn_guardar_nest = _btn("💾 GUARDAR NEST", tab.guardar_workspace_nesting)
    tab.btn_abrir_nest = _btn("📂 ABRIR NEST", tab.abrir_workspace_nesting)
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

    tab.panel_ajuste_contenido = QFrame()
    tab.panel_ajuste_contenido.setStyleSheet(
        "background:#0F172A;border:1px solid #475569;border-radius:8px;"
    )
    tab.panel_ajuste_contenido.hide()
    pc_lay = QVBoxLayout(tab.panel_ajuste_contenido)

    tab.lbl_id_hud = QLabel("ID: -")
    tab.lbl_id_hud.setStyleSheet("color:#94A3B8;font-weight:700;")
    pc_lay.addWidget(tab.lbl_id_hud)

    kerf_row = QHBoxLayout()
    lbl_kerf = QLabel("Kerf:")
    lbl_kerf.setStyleSheet("color:white;")
    kerf_row.addWidget(lbl_kerf)
    tab.ent_kerf = QLineEdit(str(DEFAULT_KERF_IN))
    tab.ent_kerf.setFixedWidth(60)
    kerf_row.addWidget(tab.ent_kerf)
    pc_lay.addLayout(kerf_row)

    opt_row = QHBoxLayout()
    lbl_opt = QLabel("Opt:")
    lbl_opt.setStyleSheet("color:white;")
    opt_row.addWidget(lbl_opt)
    tab.cmb_opt = QComboBox()
    tab.cmb_opt.addItems(["OPTIMIZAR LARGO Y ANCHO", "OPTIMIZAR LARGO", "OPTIMIZAR ANCHO"])
    opt_row.addWidget(tab.cmb_opt)
    pc_lay.addLayout(opt_row)

    tab.btn_recalc = QPushButton("RECALCULAR PLACA")
    apply_push_button(tab.btn_recalc, "#334155", font_size=11)
    tab.btn_recalc.clicked.connect(tab.aplicar_cambios_locales)
    pc_lay.addWidget(tab.btn_recalc)

    tab.btn_transferir = QPushButton("MUDAR PIEZA")
    tab.btn_transferir.setEnabled(False)
    apply_push_button(tab.btn_transferir, "#334155", font_size=11)
    tab.btn_transferir.clicked.connect(lambda: abrir_modal_transferencia(tab))
    pc_lay.addWidget(tab.btn_transferir)

    rot_row = QHBoxLayout()
    tab.btn_rot_90 = QPushButton("⟳ 90°")
    tab.btn_rot_90.setEnabled(False)
    apply_push_button(tab.btn_rot_90, "#334155", font_size=11, padding="6px 10px")
    tab.btn_rot_90.clicked.connect(lambda: tab.visor.rotar_pieza_seleccionada(90))
    tab.btn_rot_m1 = QPushButton("- 1°")
    tab.btn_rot_m1.setEnabled(False)
    apply_push_button(tab.btn_rot_m1, "#334155", font_size=11, padding="6px 10px")
    tab.btn_rot_m1.clicked.connect(lambda: tab.visor.rotar_pieza_seleccionada(-1))
    tab.btn_rot_p1 = QPushButton("+ 1°")
    tab.btn_rot_p1.setEnabled(False)
    apply_push_button(tab.btn_rot_p1, "#334155", font_size=11, padding="6px 10px")
    tab.btn_rot_p1.clicked.connect(lambda: tab.visor.rotar_pieza_seleccionada(1))
    rot_row.addWidget(tab.btn_rot_90)
    rot_row.addWidget(tab.btn_rot_m1)
    rot_row.addWidget(tab.btn_rot_p1)
    pc_lay.addLayout(rot_row)

    adj_lay.addWidget(tab.panel_ajuste_contenido)
    tab.btn_toggle_ajuste = QPushButton("⚙️ AJUSTE DE PLACA 🔼")
    apply_push_button(tab.btn_toggle_ajuste, "#FFFFFF", font_size=11)
    tab.btn_toggle_ajuste.clicked.connect(tab.toggle_ajuste_placa)
    adj_lay.addWidget(tab.btn_toggle_ajuste)
    tab.frame_ajuste_container.raise_()

    der_wrap_lay.addWidget(tab.panel_der, 1)

    splitter.addWidget(panel_der_wrap)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    finalize_splitter(splitter, min_left=380, min_right=520)
    splitter.setSizes([520, 880])
    root.addWidget(splitter)
