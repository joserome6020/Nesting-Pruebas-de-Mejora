"""Cinta tipo AutoCAD para la pestaña Nesting (ancho completo, sin recortes)."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from interface.qt.ui_scale import s as _s

_ICON = QColor("#F8FAFC")
_ACCENT = QColor("#93C5FD")
_RIBBON_BG = "#1E293B"


def _ribbon_ss() -> str:
    return f"""
QFrame#NestingRibbon {{
    background: {_RIBBON_BG};
    border: 1px solid #0F172A;
    border-radius: 6px;
    color: #F8FAFC;
}}
QFrame#NestingRibbon QWidget {{
    background: transparent;
    color: #F8FAFC;
}}
QFrame#NestingRibbon QFrame#RibbonPanel {{
    background: transparent;
    border: none;
}}
QFrame#NestingRibbon QLabel {{
    background: transparent;
    color: #CBD5E1;
}}
QFrame#NestingRibbon QLabel#RibbonPanelTitle {{
    color: #94A3B8;
    font-size: {_s(10, min_px=9)}px;
    font-weight: 600;
    letter-spacing: 0.3px;
    padding: 0px 2px;
    margin: 0;
}}
QFrame#NestingRibbon QFrame#RibbonSep {{
    background: #475569;
    border: none;
    margin: {_s(4, min_px=2)}px {_s(4, min_px=2)}px;
    max-width: 1px;
}}
QFrame#NestingRibbon QToolButton {{
    background: transparent;
    color: #F8FAFC;
    border: 1px solid transparent;
    border-radius: 4px;
}}
QFrame#NestingRibbon QToolButton:hover {{
    background: #334155;
    border-color: #64748B;
    color: #FFFFFF;
}}
QFrame#NestingRibbon QToolButton:pressed {{
    background: #0F172A;
    border-color: #93C5FD;
}}
QFrame#NestingRibbon QToolButton:checked {{
    background: #334155;
    border-color: #93C5FD;
    color: #FFFFFF;
}}
QFrame#NestingRibbon QToolButton:disabled {{
    color: #64748B;
}}
QToolTip {{
    background-color: #FFFFFF;
    color: #111827;
    border: 1px solid #CFD7E6;
    padding: 4px 8px;
}}
"""


_MENU_SS = (
    "QMenu{background:#0F172A;color:#F1F5F9;border:1px solid #334155;"
    "border-radius:4px;padding:4px;}"
    "QMenu::item{padding:6px 18px;border-radius:2px;}"
    "QMenu::item:selected{background:#334155;}"
    "QMenu::separator{height:1px;background:#334155;margin:4px 8px;}"
)


def _pix(kind: str, size: int = 28) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(_ICON, 1.6)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    m = size * 0.18
    r = QRect(int(m), int(m), int(size - 2 * m), int(size - 2 * m))

    if kind == "open":
        p.drawRoundedRect(r.adjusted(0, 4, -2, 0), 2, 2)
        p.setPen(QPen(_ACCENT, 1.6))
        p.drawLine(r.left(), r.top() + 2, r.left() + 8, r.top() + 2)
        p.drawLine(r.left() + 8, r.top() + 2, r.left() + 11, r.top() + 6)
        p.drawLine(r.left() + 11, r.top() + 6, r.right(), r.top() + 6)
    elif kind == "save":
        p.drawRoundedRect(r, 2, 2)
        p.drawRect(r.adjusted(5, 2, -5, -r.height() // 2))
        p.setBrush(_ACCENT)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(r.adjusted(4, r.height() // 2 + 1, -4, -3))
    elif kind == "lote":
        p.drawRoundedRect(r.adjusted(0, 2, -6, -2), 2, 2)
        p.drawRoundedRect(r.adjusted(6, 0, 0, -4), 2, 2)
    elif kind == "hist":
        p.drawEllipse(r)
        c = r.center()
        p.drawLine(c, QPoint(c.x(), r.top() + 5))
        p.drawLine(c, QPoint(r.right() - 4, c.y() + 3))
    elif kind == "largos":
        y = r.center().y()
        for i, dy in enumerate((-5, 0, 5)):
            p.setPen(QPen(_ACCENT if i == 1 else _ICON, 1.8))
            p.drawLine(r.left(), y + dy, r.right(), y + dy)
    elif kind == "export":
        p.drawRoundedRect(r.adjusted(0, 2, -8, -2), 2, 2)
        p.setPen(QPen(_ACCENT, 1.8))
        cx, cy = r.right() - 4, r.center().y()
        p.drawLine(r.center().x() - 2, cy, cx, cy)
        p.drawLine(cx - 5, cy - 4, cx, cy)
        p.drawLine(cx - 5, cy + 4, cx, cy)
    elif kind == "step":
        p.drawRect(r.adjusted(1, 4, -1, -4))
        p.drawLine(r.left() + 1, r.top() + 4, r.center().x(), r.top())
        p.drawLine(r.center().x(), r.top(), r.right() - 1, r.top() + 4)
        p.drawLine(r.center().x(), r.top(), r.center().x(), r.bottom() - 4)
    elif kind == "crear_step":
        p.drawRect(r.adjusted(1, 5, -8, -5))
        p.drawLine(r.left() + 1, r.top() + 5, r.center().x() - 4, r.top() + 1)
        p.drawLine(r.center().x() - 4, r.top() + 1, r.right() - 9, r.top() + 5)
        p.setPen(QPen(_ACCENT, 1.8))
        cx, cy = r.right() - 4, r.center().y()
        p.drawLine(cx - 5, cy, cx + 5, cy)
        p.drawLine(cx, cy - 5, cx, cy + 5)
    elif kind == "pdf":
        p.drawRoundedRect(r, 2, 2)
        p.drawLine(r.left() + 5, r.top() + 7, r.right() - 5, r.top() + 7)
        p.drawLine(r.left() + 5, r.top() + 11, r.right() - 5, r.top() + 11)
        p.drawLine(r.left() + 5, r.top() + 15, r.right() - 8, r.top() + 15)
        p.setPen(QPen(_ACCENT, 1.5))
        p.drawText(r.adjusted(0, 2, 0, 0), Qt.AlignmentFlag.AlignCenter, "P")
    elif kind == "cost":
        p.drawEllipse(r)
        p.setPen(QPen(_ACCENT, 1.6))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, "$")
    elif kind == "cfg":
        p.drawEllipse(r.adjusted(6, 6, -6, -6))
        c = r.center()
        for ang in range(0, 360, 45):
            p.save()
            p.translate(c)
            p.rotate(ang)
            p.drawLine(0, -r.height() // 2 + 1, 0, -r.height() // 2 + 5)
            p.restore()
    elif kind == "lab":
        p.drawLine(r.left() + 4, r.top() + 3, r.right() - 4, r.top() + 3)
        p.drawLine(r.center().x() - 3, r.top() + 3, r.left() + 3, r.bottom() - 2)
        p.drawLine(r.center().x() + 3, r.top() + 3, r.right() - 3, r.bottom() - 2)
        p.setPen(QPen(_ACCENT, 1.6))
        p.drawLine(r.left() + 3, r.bottom() - 2, r.right() - 3, r.bottom() - 2)
    elif kind == "plate":
        p.drawRoundedRect(r.adjusted(1, 4, -1, -4), 2, 2)
        p.setPen(QPen(_ACCENT, 1.6))
        p.drawLine(r.left() + 4, r.center().y(), r.right() - 4, r.center().y())
        p.drawLine(r.center().x(), r.top() + 6, r.center().x(), r.bottom() - 6)
    elif kind == "renest":
        p.drawEllipse(r.adjusted(3, 3, -3, -3))
        p.setPen(QPen(_ACCENT, 1.8))
        c = r.center()
        p.drawArc(r.adjusted(5, 5, -5, -5), 40 * 16, 220 * 16)
        p.drawLine(c.x() + 6, r.top() + 5, c.x() + 2, r.top() + 10)
        p.drawLine(c.x() + 6, r.top() + 5, c.x() + 10, r.top() + 9)
    elif kind == "swap":
        p.drawRoundedRect(r.adjusted(2, 6, -10, -6), 2, 2)
        p.drawRoundedRect(r.adjusted(10, 2, -2, -10), 2, 2)
        p.setPen(QPen(_ACCENT, 1.6))
        p.drawLine(r.left() + 4, r.bottom() - 4, r.right() - 4, r.top() + 4)
    elif kind == "view":
        p.drawEllipse(r.adjusted(2, 6, -2, -6))
        p.setBrush(_ACCENT)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(r.adjusted(10, 10, -10, -10))
    elif kind == "move":
        p.drawRoundedRect(r.adjusted(4, 4, -4, -4), 2, 2)
        p.setPen(QPen(_ACCENT, 1.8))
        cx, cy = r.center().x(), r.center().y()
        p.drawLine(cx - 6, cy, cx + 6, cy)
        p.drawLine(cx + 6, cy, cx + 2, cy - 4)
        p.drawLine(cx + 6, cy, cx + 2, cy + 4)
    elif kind == "rotate":
        p.drawArc(r.adjusted(4, 4, -4, -4), 30 * 16, 280 * 16)
        p.setPen(QPen(_ACCENT, 1.8))
        p.drawLine(r.right() - 6, r.top() + 8, r.right() - 3, r.top() + 4)
        p.drawLine(r.right() - 6, r.top() + 8, r.right() - 10, r.top() + 5)
    elif kind == "clear":
        p.drawEllipse(r.adjusted(3, 3, -3, -3))
        p.setPen(QPen(_ACCENT, 1.8))
        p.drawLine(r.left() + 8, r.top() + 8, r.right() - 8, r.bottom() - 8)
        p.drawLine(r.right() - 8, r.top() + 8, r.left() + 8, r.bottom() - 8)
    elif kind == "free":
        p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 2, 2)
        p.setPen(QPen(_ACCENT, 1.6))
        p.drawLine(r.left() + 6, r.top() + 8, r.right() - 6, r.bottom() - 8)
        p.drawLine(r.left() + 6, r.bottom() - 8, r.right() - 6, r.top() + 8)
    else:
        p.drawRect(r)

    p.end()
    return pm


def ribbon_icon(kind: str, size: int = 28) -> QIcon:
    return QIcon(_pix(kind, size))


def make_cmd(
    text: str,
    *,
    icon: str,
    tip: str | None = None,
    menu: QMenu | None = None,
) -> QToolButton:
    """Botón de cinta: icono arriba + texto abajo, tamaño fijo (no se estira ni recorta)."""
    btn = QToolButton()
    btn.setText(text)
    ico = _s(22, min_px=18)
    btn.setIcon(ribbon_icon(icon, ico))
    btn.setIconSize(QSize(ico, ico))
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
    btn.setAutoRaise(True)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFixedSize(_s(72, min_px=64), _s(58, min_px=50))
    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    btn.setStyleSheet(
        f"""
        QToolButton {{
            background: transparent;
            color: #F8FAFC;
            border: 1px solid transparent;
            border-radius: 4px;
            padding: 2px 2px 0px 2px;
            font-size: {_s(10, min_px=9)}px;
            font-weight: 600;
        }}
        QToolButton:hover {{
            background: #334155;
            border-color: #64748B;
            color: #FFFFFF;
        }}
        QToolButton:pressed {{
            background: #0F172A;
            border-color: #93C5FD;
        }}
        QToolButton:checked {{
            background: #334155;
            border-color: #93C5FD;
        }}
        QToolButton:disabled {{
            color: #64748B;
        }}
        QToolButton::menu-indicator {{
            image: none;
            width: 0;
        }}
        """
    )
    if tip:
        btn.setToolTip(tip)
    if menu is not None:
        btn.setMenu(menu)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        menu.setStyleSheet(_MENU_SS)
    return btn


def make_panel(title: str, *buttons: QToolButton) -> QFrame:
    """Grupo horizontal de botones + título abajo (todo visible, sin apilar)."""
    panel = QFrame()
    panel.setObjectName("RibbonPanel")
    panel.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    root = QVBoxLayout(panel)
    root.setContentsMargins(_s(4, min_px=2), _s(4, min_px=2), _s(4, min_px=2), _s(2, min_px=1))
    root.setSpacing(_s(2, min_px=1))

    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(_s(2, min_px=1))
    for btn in buttons:
        row.addWidget(btn)
    root.addLayout(row)

    lbl = QLabel(title)
    lbl.setObjectName("RibbonPanelTitle")
    lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
    lbl.setFixedHeight(_s(16, min_px=14))
    root.addWidget(lbl)
    return panel


def make_vsep() -> QFrame:
    line = QFrame()
    line.setObjectName("RibbonSep")
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFixedWidth(1)
    line.setFixedHeight(_s(64, min_px=54))
    return line


def _ribbon_target_height(tab=None) -> int:
    # 2 filas × (botón 58 + título 16 + márgenes) ≈ 168 diseño.
    return _s(168, min_px=150)


def apply_nesting_ribbon_height(tab) -> None:
    ribbon = getattr(tab, "frame_header_der", None)
    if ribbon is None:
        return
    h = _ribbon_target_height(tab)
    ribbon.setFixedHeight(h)
    ribbon.setMinimumHeight(h)


def sync_nesting_ribbon_geometry(tab) -> None:
    apply_nesting_ribbon_height(tab)


def mount_nesting_ribbon_on_tabbar(tab) -> None:
    apply_nesting_ribbon_height(tab)


def build_nesting_ribbon(tab) -> QWidget:
    """
    Cinta a ANCHO COMPLETO (2 filas). Todos los comandos visibles sin scroll
    ni apilado vertical que recorte títulos.
    """
    h = _ribbon_target_height(tab)

    ribbon = QFrame()
    ribbon.setObjectName("NestingRibbon")
    ribbon.setFixedHeight(h)
    ribbon.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    ribbon.setAutoFillBackground(True)
    pal = ribbon.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(_RIBBON_BG))
    pal.setColor(QPalette.ColorRole.Base, QColor(_RIBBON_BG))
    pal.setColor(QPalette.ColorRole.Button, QColor(_RIBBON_BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#F8FAFC"))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#F8FAFC"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#111827"))
    ribbon.setPalette(pal)
    ribbon.setStyleSheet(_ribbon_ss())

    outer = QVBoxLayout(ribbon)
    outer.setContentsMargins(_s(6, min_px=4), _s(4, min_px=2), _s(6, min_px=4), _s(4, min_px=2))
    outer.setSpacing(_s(2, min_px=1))

    row1 = QHBoxLayout()
    row1.setContentsMargins(0, 0, 0, 0)
    row1.setSpacing(0)
    row1.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    row2 = QHBoxLayout()
    row2.setContentsMargins(0, 0, 0, 0)
    row2.setSpacing(0)
    row2.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    # —— Archivo ——
    tab.btn_abrir_nest = make_cmd("Abrir", icon="open", tip="Abrir nest (.arganest)")
    tab.btn_abrir_nest.clicked.connect(tab.abrir_workspace_nesting)
    tab.btn_guardar_nest = make_cmd("Guardar", icon="save", tip="Guardar nest")
    tab.btn_guardar_nest.clicked.connect(tab.guardar_workspace_nesting)
    row1.addWidget(make_panel("Archivo", tab.btn_abrir_nest, tab.btn_guardar_nest))
    row1.addWidget(make_vsep())

    # —— Orden ——
    tab.btn_editar_lote = make_cmd("Editar lote", icon="lote", tip="Editar lote activo")
    tab.btn_editar_lote.clicked.connect(tab.editar_lote_activo)
    tab.btn_ver_lotes = make_cmd("Historial", icon="hist", tip="Historial de W.O.")
    tab.btn_ver_lotes.clicked.connect(tab.reabrir_modal_escenarios)
    tab.btn_nesting_largos = make_cmd("Largos", icon="largos", tip="Nesteo de largos")
    tab.btn_nesting_largos.clicked.connect(tab.abrir_nesting_largos)
    row1.addWidget(
        make_panel("Orden", tab.btn_editar_lote, tab.btn_ver_lotes, tab.btn_nesting_largos)
    )
    row1.addWidget(make_vsep())

    # —— Salida ——
    tab.btn_exportar = make_cmd("Exportar", icon="export", tip="Exportar DXF/STEP")
    tab.btn_exportar.clicked.connect(tab.exportar_resultados_dxf)
    tab.btn_pdf_nesting = make_cmd("PDF", icon="pdf", tip="Exportar reporte PDF nesting")
    tab.btn_pdf_nesting.clicked.connect(tab.exportar_reporte_pdf_nesting)
    tab.btn_ver_step = make_cmd("Ver STEP", icon="step", tip="Abrir visor STEP")
    tab.btn_ver_step.clicked.connect(tab.abrir_visor_step)
    tab.btn_crear_steps = make_cmd(
        "Crear STEPs",
        icon="crear_step",
        tip="Generar STEP desde DXF ya exportados",
    )
    tab.btn_crear_steps.clicked.connect(tab.abrir_crear_steps)
    tab.btn_reanudar_sync = make_cmd(
        "Reanudar sync",
        icon="export",
        tip="Reintentar VSM/ContPAQ (requiere contraseña)",
    )
    tab.btn_reanudar_sync.clicked.connect(tab.reanudar_centralizacion_pendiente)
    row1.addWidget(
        make_panel(
            "Salida",
            tab.btn_exportar,
            tab.btn_pdf_nesting,
            tab.btn_ver_step,
            tab.btn_crear_steps,
            tab.btn_reanudar_sync,
        )
    )
    row1.addWidget(make_vsep())

    # —— Herramientas ——
    tab.btn_costos = make_cmd("Costos", icon="cost", tip="Costos de orden")
    tab.btn_costos.clicked.connect(
        lambda: __import__(
            "interface.qt.dialogs.nesting_modals", fromlist=["abrir_modal_costos"]
        ).abrir_modal_costos(tab)
    )
    tab.btn_config = make_cmd("Configuración", icon="cfg", tip="Configuración global")
    tab.btn_config.clicked.connect(
        lambda: __import__(
            "interface.qt.dialogs.nesting_modals",
            fromlist=["abrir_modal_configuracion"],
        ).abrir_modal_configuracion(tab)
    )
    tab.btn_nest_sim_lab = make_cmd("Lab", icon="lab", tip="Lab · Comparar motores")
    tab.btn_nest_sim_lab.clicked.connect(tab.abrir_nest_sim_lab)
    row1.addWidget(
        make_panel("Herramientas", tab.btn_costos, tab.btn_config, tab.btn_nest_sim_lab)
    )
    row1.addStretch(1)

    # —— Placa (fila 2: todo visible) ——
    tab.btn_panel_renest_placa = make_cmd(
        "Renestear", icon="renest", tip="Renestear la placa / barra activa"
    )
    tab.btn_panel_renest_placa.clicked.connect(tab.panel_renestear_placa)
    tab.btn_panel_renest_placa.setEnabled(False)

    tab.btn_panel_cambiar_placa = make_cmd(
        "Cambiar", icon="swap", tip="Cambiar placa madre"
    )
    tab.btn_panel_cambiar_placa.clicked.connect(tab.panel_cambiar_placa_madre)
    tab.btn_panel_cambiar_placa.setEnabled(False)

    tab.btn_panel_renest_calibre = make_cmd(
        "Calibre", icon="plate", tip="Renestear calibre completo"
    )
    tab.btn_panel_renest_calibre.clicked.connect(tab.panel_renestear_calibre)
    tab.btn_panel_renest_calibre.setEnabled(False)

    tab.btn_ajustar_vista = make_cmd("Vista", icon="view", tip="Ajustar vista de la placa")
    tab.btn_ajustar_vista.clicked.connect(tab.panel_ajustar_vista)

    tab.btn_transferir = make_cmd(
        "Mudar", icon="move", tip="Mudar pieza(s) a otra placa"
    )
    tab.btn_transferir.setEnabled(False)
    tab.btn_transferir.clicked.connect(
        lambda: __import__(
            "interface.qt.dialogs.nesting_modals",
            fromlist=["abrir_modal_transferencia"],
        ).abrir_modal_transferencia(tab)
    )

    tab.btn_limpiar_sel = make_cmd("Limpiar", icon="clear", tip="Limpiar selección")
    tab.btn_limpiar_sel.clicked.connect(tab.panel_limpiar_seleccion)

    tab.btn_rot_m1 = make_cmd("-1°", icon="rotate", tip="Rotar −1°")
    tab.btn_rot_m1.setEnabled(False)
    tab.btn_rot_m1.clicked.connect(lambda: tab.visor.rotar_pieza_seleccionada(-1))
    tab.btn_rot_p1 = make_cmd("+1°", icon="rotate", tip="Rotar +1°")
    tab.btn_rot_p1.setEnabled(False)
    tab.btn_rot_p1.clicked.connect(lambda: tab.visor.rotar_pieza_seleccionada(1))
    tab.btn_rot_90 = make_cmd("90°", icon="rotate", tip="Rotar 90°")
    tab.btn_rot_90.setEnabled(False)
    tab.btn_rot_90.clicked.connect(lambda: tab.visor.rotar_pieza_seleccionada(90))

    tab.switch_edicion_libre = make_cmd(
        "Ed. libre",
        icon="free",
        tip="Edición libre entre selección",
    )
    tab.switch_edicion_libre.setCheckable(True)
    tab.switch_edicion_libre.setEnabled(False)
    tab.switch_edicion_libre.toggled.connect(tab._on_toggle_edicion_libre)

    row2.addWidget(
        make_panel(
            "Placa",
            tab.btn_panel_renest_placa,
            tab.btn_panel_cambiar_placa,
            tab.btn_panel_renest_calibre,
            tab.btn_ajustar_vista,
            tab.btn_transferir,
            tab.btn_limpiar_sel,
            tab.btn_rot_m1,
            tab.btn_rot_p1,
            tab.btn_rot_90,
            tab.switch_edicion_libre,
        )
    )
    row2.addStretch(1)

    outer.addLayout(row1)
    outer.addLayout(row2)

    tab._nesting_ribbon_inner = ribbon
    tab.frame_header_der = ribbon
    return ribbon
