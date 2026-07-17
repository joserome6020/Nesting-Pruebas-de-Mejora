"""Tema visual Qt — paleta alineada con tarjetones React-Herinox."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QPushButton

# Herinox / global.css
COLOR_FONDO_APP = "#F1F5F9"
COLOR_TARJETA = "#FFFFFF"
COLOR_TARJETA_INTERNA = "#FBFCFF"
COLOR_BORDE = "#D8DFEB"
COLOR_BORDE_INPUT = "#CFD7E6"
COLOR_BORDE_HOVER = "#90A8D6"
COLOR_BORDE_SUAVE = "#E8EDF5"
COLOR_GRIS_DARK = "#1E293B"
COLOR_GRIS_MED = "#475569"
COLOR_TEXTO_TITULO = "#111827"
COLOR_TEXTO_SUBTITULO = "#16345F"
COLOR_TEXTO_SECUNDARIO = "#607089"
COLOR_TEXTO_MUTED = "#6F7D93"
COLOR_ACENTO = "#2F6DEA"
COLOR_ACENTO_TEXTO = "#174493"
COLOR_BADGE_FONDO = "#E3EBFC"
COLOR_BADGE_TEXTO = "#1D4F9B"
COLOR_EXITO = "#10B981"
COLOR_ERROR = "#EF4444"

ARGB_BTN_1 = "#202A36"
ARGB_BTN_2 = "#334659"
ARGB_BTN_3 = "#455E75"
ARGB_BTN_4 = "#708DA9"

APP_STYLESHEET = f"""
QMainWindow, QWidget {{
    background: {COLOR_FONDO_APP};
    color: {COLOR_TEXTO_TITULO};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 12px;
}}

QLabel {{
    color: {COLOR_TEXTO_TITULO};
    background: transparent;
}}

QLabel#LabelCaption {{
    color: {COLOR_TEXTO_SECUNDARIO};
    font-size: 10px;
    font-weight: 700;
}}

QLabel#LabelMuted {{
    color: {COLOR_TEXTO_MUTED};
    font-size: 11px;
}}

/* —— Navbar —— */
QWidget#NavBar {{
    background: {COLOR_TARJETA};
    border: 1px solid {COLOR_BORDE_SUAVE};
    border-radius: 12px;
}}

/* —— Tarjetones Herinox —— */
QFrame#CardFrame {{
    background: {COLOR_TARJETA};
    border: 1px solid {COLOR_BORDE_SUAVE};
    border-radius: 12px;
}}
QFrame#DarkPanel {{
    background: #1E293B;
    border: 1px solid #3D4F63;
    border-radius: 12px;
}}
QFrame#DarkPanel QLabel {{
    color: #E2E8F0;
}}
QFrame#SheetRow {{
    background: {COLOR_TARJETA};
    border: 1px solid {COLOR_BORDE_SUAVE};
    border-radius: 12px;
}}
QFrame#SheetRow:hover {{
    border: 1px solid {COLOR_BORDE};
}}
QFrame#PartsRow {{
    background: transparent;
    border: none;
    border-radius: 8px;
}}
QFrame#PartsRowAlt {{
    background: {COLOR_TARJETA_INTERNA};
    border: none;
    border-radius: 8px;
}}
QFrame#TableHeader {{
    background: {COLOR_TARJETA_INTERNA};
    border: 1px solid {COLOR_BORDE_SUAVE};
    border-bottom: none;
    border-top-left-radius: 12px;
    border-top-right-radius: 12px;
}}
QFrame#TableHeader QLabel {{
    color: {COLOR_TEXTO_SECUNDARIO};
    font-weight: 700;
    font-size: 11px;
}}
QFrame#FilterBar {{
    background: {COLOR_TARJETA};
    border: 1px solid {COLOR_BORDE_SUAVE};
    border-radius: 12px;
}}
QFrame#HerinoxCard {{
    background: {COLOR_TARJETA};
    border: 1px solid {COLOR_BORDE_SUAVE};
    border-radius: 12px;
}}
QFrame#HerinoxCard QLabel {{
    color: {COLOR_TEXTO_TITULO};
    background: transparent;
}}

/* —— Tabs principales —— */
QTabWidget#mainTabs::pane {{
    border: none;
    background: transparent;
    top: 0;
    margin-top: 0;
    padding: 0;
}}
QTabWidget#mainTabs QTabBar {{
    border: none;
    background: transparent;
    qproperty-drawBase: 0;
}}
QTabWidget#mainTabs QTabBar::base {{
    border: none;
    background: transparent;
    height: 0;
    max-height: 0;
    margin: 0;
    padding: 0;
}}
QTabWidget#mainTabs QTabBar::scroller,
QTabWidget#mainTabs QTabBar::tear {{
    border: none;
    background: transparent;
}}
QTabWidget#mainTabs QTabBar::tab {{
    background: {COLOR_TARJETA};
    color: {COLOR_TEXTO_SECUNDARIO};
    border: 1px solid {COLOR_BORDE_SUAVE};
    border-bottom: none;
    padding: 11px 26px;
    margin-right: 6px;
    margin-bottom: 0;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    font-weight: 600;
    min-width: 90px;
}}
QTabWidget#mainTabs QTabBar::tab:selected {{
    background: {COLOR_GRIS_DARK};
    color: #FFFFFF;
    border-color: {COLOR_GRIS_DARK};
    margin-bottom: -1px;
    padding-bottom: 12px;
}}
QTabWidget#mainTabs QTabBar::tab:hover:!selected {{
    background: #EAF0FD;
    color: {COLOR_ACENTO_TEXTO};
    border-color: {COLOR_BORDE};
}}

/* —— Sub-tabs SHEETS —— */
QTabWidget#StockTabs::pane {{
    border: none;
    background: transparent;
}}
QTabWidget#StockTabs QTabBar::tab {{
    background: {COLOR_GRIS_MED};
    color: #FFFFFF;
    border: 1px solid {COLOR_GRIS_MED};
    padding: 9px 20px;
    margin-right: 8px;
    border-radius: 8px;
    font-weight: 600;
}}
QTabWidget#StockTabs QTabBar::tab:selected {{
    background: {COLOR_GRIS_DARK};
    color: #FFFFFF;
    border-color: {COLOR_GRIS_DARK};
}}
QTabWidget#StockTabs QTabBar::tab:hover:!selected {{
    background: #334155;
    color: #FFFFFF;
    border-color: #334155;
}}

/* —— Splitter —— */
QSplitter#MainSplitter {{
    background: transparent;
    border: none;
}}
QSplitter#MainSplitter::handle {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 transparent,
        stop:0.4 {COLOR_BORDE_SUAVE},
        stop:0.5 #D8DFEB,
        stop:0.6 {COLOR_BORDE_SUAVE},
        stop:1 transparent
    );
    border: none;
    border-radius: 4px;
    margin: 12px 0;
}}
QSplitter#MainSplitter::handle:horizontal {{
    width: 6px;
}}
QSplitter#MainSplitter::handle:horizontal:hover {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 transparent,
        stop:0.35 #E2E8F0,
        stop:0.5 #C9D4E6,
        stop:0.65 #E2E8F0,
        stop:1 transparent
    );
}}

/* —— Scroll —— */
QScrollArea#AppScroll {{
    border: none;
    background: transparent;
}}
QWidget#ScrollContent {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px 2px 4px 0;
}}
QScrollBar::handle:vertical {{
    background: #CFD7E6;
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: #90A8D6;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    height: 0;
}}

/* —— Controles —— */
QPushButton {{
    background: {COLOR_GRIS_DARK};
    color: #FFFFFF;
    border: 1px solid {COLOR_GRIS_DARK};
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {COLOR_GRIS_MED};
    border-color: {COLOR_GRIS_MED};
}}
QPushButton:disabled {{
    background: #E2E8F0;
    color: #475569;
    border: 1px solid #94A3B8;
}}

/* —— Diálogos / QMessageBox —— */
QMessageBox {{
    background: {COLOR_TARJETA};
}}
QMessageBox QLabel {{
    color: {COLOR_TEXTO_TITULO};
    background: transparent;
}}
QMessageBox QPushButton {{
    background: {COLOR_TARJETA};
    color: {COLOR_TEXTO_TITULO};
    border: 1px solid #94A3B8;
    border-radius: 8px;
    padding: 6px 18px;
    font-weight: 600;
    min-width: 72px;
}}
QMessageBox QPushButton:hover {{
    background: #EAF0FD;
    border-color: {COLOR_ACENTO};
    color: {COLOR_TEXTO_TITULO};
}}
QDialog {{
    background: {COLOR_FONDO_APP};
}}
QDialog QLabel {{
    color: {COLOR_TEXTO_TITULO};
}}
QDialog QPushButton {{
    background: {COLOR_TARJETA};
    color: {COLOR_TEXTO_TITULO};
    border: 1px solid #94A3B8;
    border-radius: 8px;
    padding: 6px 14px;
    font-weight: 600;
}}
QDialog QPushButton:hover {{
    background: #EAF0FD;
    border-color: {COLOR_ACENTO};
}}
QLineEdit, QComboBox {{
    background: {COLOR_TARJETA_INTERNA};
    color: {COLOR_TEXTO_TITULO};
    border: 1px solid {COLOR_BORDE_INPUT};
    border-radius: 8px;
    padding: 6px 10px;
}}
QLineEdit:focus, QComboBox:focus {{
    border: 1px solid {COLOR_ACENTO};
}}
QComboBox QAbstractItemView {{
    background: {COLOR_TARJETA};
    color: {COLOR_TEXTO_TITULO};
    border: 1px solid {COLOR_BORDE};
    selection-background-color: #EAF0FD;
    selection-color: {COLOR_ACENTO_TEXTO};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QTableWidget {{
    background: {COLOR_TARJETA};
    color: {COLOR_TEXTO_TITULO};
    border: 1px solid {COLOR_BORDE_SUAVE};
    border-radius: 8px;
    gridline-color: {COLOR_BORDE_SUAVE};
}}
QHeaderView::section {{
    background: {COLOR_TARJETA_INTERNA};
    color: {COLOR_TEXTO_SECUNDARIO};
    border: none;
    border-bottom: 1px solid {COLOR_BORDE};
    padding: 6px;
    font-weight: 700;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QProgressBar {{
    border: none;
    background: #E2E8F0;
    height: 10px;
    border-radius: 5px;
    text-align: center;
    color: {COLOR_TEXTO_TITULO};
}}
QProgressBar::chunk {{
    background: {COLOR_ACENTO};
    border-radius: 5px;
}}

/* —— Visor PARTS (panel oscuro) —— */
QFrame#VisorInfoPanel {{
    background: #1E293B;
    border: none;
    border-radius: 0 0 10px 10px;
}}
QFrame#VisorInfoPanel QLabel {{
    color: #38BDF8;
}}

/* —— Checkboxes nesting —— */
QCheckBox {{
    spacing: 6px;
    color: #CBD5E1;
    font-weight: 600;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #475569;
    background: #1E293B;
}}
QCheckBox::indicator:checked {{
    background: {COLOR_ACENTO};
    border-color: {COLOR_ACENTO};
}}

QComboBox#DarkCombo {{
    background: {COLOR_GRIS_DARK};
    color: #FFFFFF;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 8px 10px;
    font-weight: 600;
}}
QComboBox#DarkCombo::drop-down {{
    border: none;
}}
QComboBox#DarkCombo QAbstractItemView {{
    background: {COLOR_GRIS_DARK};
    color: #FFFFFF;
    selection-background-color: {COLOR_GRIS_MED};
    border: 1px solid #334155;
}}
QComboBox#DarkCombo:disabled {{
    background: #E2E8F0;
    color: {COLOR_TEXTO_TITULO};
    border: 1px solid #94A3B8;
}}

/* —— Combos en tarjetas claras (Herinox) —— */
QComboBox#HerinoxCombo {{
    background: {COLOR_TARJETA_INTERNA};
    color: {COLOR_TEXTO_TITULO};
    border: 1px solid {COLOR_BORDE_INPUT};
    border-radius: 8px;
    padding: 8px 10px;
    font-weight: 600;
    min-height: 20px;
}}
QComboBox#HerinoxCombo:hover {{
    border-color: {COLOR_BORDE_HOVER};
}}
QComboBox#HerinoxCombo:focus {{
    border-color: {COLOR_ACENTO};
}}
QComboBox#HerinoxCombo:disabled {{
    background: #E8EDF5;
    color: {COLOR_TEXTO_TITULO};
    border: 1px solid #94A3B8;
}}
QComboBox#HerinoxCombo::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox#HerinoxCombo QAbstractItemView {{
    background: {COLOR_TARJETA};
    color: {COLOR_TEXTO_TITULO};
    border: 1px solid {COLOR_BORDE};
    selection-background-color: #EAF0FD;
    selection-color: {COLOR_ACENTO_TEXTO};
    outline: none;
}}

/* —— Campo búsqueda Herinox —— */
QLineEdit#HerinoxSearch {{
    background: {COLOR_TARJETA};
    color: {COLOR_TEXTO_TITULO};
    border: 1px solid {COLOR_BORDE_INPUT};
    border-radius: 10px;
    padding: 10px 12px 10px 36px;
    font-size: 13px;
}}
QLineEdit#HerinoxSearch:focus {{
    border-color: {COLOR_ACENTO};
}}

/* —— Botones toolbar claros —— */
QPushButton#BtnToolbarLight {{
    color: {COLOR_TEXTO_TITULO};
    border: 1px solid #94A3B8;
}}
QPushButton#BtnToolbarLight:hover {{
    color: {COLOR_TEXTO_TITULO};
}}

/* —— Superficies claras: tipografía oscura por defecto —— */
QDialog QLabel,
QMessageBox QLabel {{
    color: {COLOR_TEXTO_TITULO};
    background: transparent;
}}
QRadioButton {{
    color: {COLOR_TEXTO_TITULO};
    font-weight: 600;
}}
QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid #94A3B8;
    border-radius: 8px;
    background: {COLOR_TARJETA};
}}
QRadioButton::indicator:checked {{
    background: {COLOR_ACENTO};
    border-color: {COLOR_ACENTO};
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QWidget#LightCard {{
    background: {COLOR_TARJETA};
    border: 1px solid {COLOR_BORDE};
    border-radius: 12px;
}}
QWidget#LightCard QLabel {{
    color: {COLOR_TEXTO_TITULO};
}}
QFrame#ToolbarStrip {{
    background: #EEF2F8;
    border: 1px solid {COLOR_BORDE_SUAVE};
    border-radius: 10px;
}}
"""


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = str(hex_color or "").lstrip("#")
    if len(h) == 6:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 30, 41, 59


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (x / 255.0 for x in rgb)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _shade(hex_color: str, factor: float) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    if factor >= 0:
        r = int(r + (255 - r) * factor)
        g = int(g + (255 - g) * factor)
        b = int(b + (255 - b) * factor)
    else:
        f = 1.0 + factor
        r, g, b = int(r * f), int(g * f), int(b * f)
    return f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"


def push_button_stylesheet(
    bg: str,
    *,
    hover: str | None = None,
    font_size: int = 12,
    font_weight: int | str = 600,
    padding: str = "8px 14px",
    radius: int = 8,
) -> str:
    """Botón con borde visible y texto legible (oscuro en fondos claros)."""
    rgb = _hex_to_rgb(bg)
    light_bg = _relative_luminance(rgb) > 0.52
    if light_bg:
        fg = COLOR_TEXTO_TITULO
        border = "#CBD5E1"
        hover_bg = hover or _shade(bg, -0.06)
        hover_border = "#94A3B8"
        pressed_bg = _shade(bg, -0.12)
    else:
        fg = "#FFFFFF"
        border = _shade(bg, -0.2)
        hover_bg = hover or _shade(bg, 0.12)
        hover_border = _shade(bg, 0.05)
        pressed_bg = _shade(bg, -0.1)

    fw = str(font_weight).strip() or "600"
    base = (
        f"background:{bg};color:{fg};border:1px solid {border};"
        f"border-radius:{radius}px;padding:{padding};font-weight:{fw};font-size:{font_size}px;"
    )
    return (
        f"QPushButton{{{base}}}"
        f"QPushButton:enabled{{{base}}}"
        f"QPushButton:hover{{background:{hover_bg};border-color:{hover_border};color:{fg};}}"
        f"QPushButton:pressed{{background:{pressed_bg};color:{fg};border-color:{hover_border};}}"
        f"QPushButton:focus{{color:{fg};border-color:{COLOR_ACENTO};}}"
        f"QPushButton:disabled{{background:#E2E8F0;color:#475569;border:1px solid #94A3B8;}}"
    )


def apply_push_button(btn: QPushButton, bg: str, **kwargs) -> None:
    """Aplica estilo + paleta para que el texto sea legible en Windows."""
    ss = push_button_stylesheet(bg, **kwargs)
    btn.setStyleSheet(ss)
    rgb = _hex_to_rgb(bg)
    light_bg = _relative_luminance(rgb) > 0.52
    fg = COLOR_TEXTO_TITULO if light_bg else "#FFFFFF"
    pal = btn.palette()
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(fg))
    pal.setColor(QPalette.ColorRole.Button, QColor(bg))
    btn.setPalette(pal)
    btn.setAutoFillBackground(True)
    if light_bg:
        btn.setProperty("light", True)
        btn.setObjectName("BtnToolbarLight")


def surface_dialog_stylesheet() -> str:
    return f"background:{COLOR_FONDO_APP}; color:{COLOR_TEXTO_TITULO};"


def apply_herinox_combo(combo) -> None:
    """Combo legible en tarjetas claras (evita texto blanco sobre fondo claro)."""
    combo.setObjectName("HerinoxCombo")
    pal = combo.palette()
    pal.setColor(QPalette.ColorRole.Text, QColor(COLOR_TEXTO_TITULO))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(COLOR_TEXTO_TITULO))
    pal.setColor(QPalette.ColorRole.Base, QColor(COLOR_TARJETA_INTERNA))
    combo.setPalette(pal)
    combo.setAutoFillBackground(True)


def apply_theme(app) -> None:
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    app.setStyleSheet(APP_STYLESHEET)
