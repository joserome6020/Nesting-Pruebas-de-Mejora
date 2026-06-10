import json
import os
import sys

from PySide6 import QtCore, QtGui, QtWidgets


PARAMS = ["Thickness", "Material", "Length", "Width", "LB", "MXN", "$$/LB", "Stock"]


def _load_payload(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _color_item(text: str, color: str, bold: bool = False) -> QtWidgets.QTableWidgetItem:
    item = QtWidgets.QTableWidgetItem(str(text))
    item.setForeground(QtGui.QBrush(QtGui.QColor(color)))
    if bold:
        font = item.font()
        font.setBold(True)
        item.setFont(font)
    return item


class SyncViewerWindow(QtWidgets.QMainWindow):
    def __init__(self, payload: dict):
        super().__init__()
        self.payload = payload or {}
        self.setWindowTitle("Cambios de sincronizacion Herinox")
        self.resize(1200, 760)
        self._build_ui()

    def _build_ui(self):
        root = QtWidgets.QWidget()
        root.setStyleSheet("background:#FFFFFF;")
        root_layout = QtWidgets.QVBoxLayout(root)
        root_layout.setContentsMargins(14, 12, 14, 12)
        root_layout.setSpacing(10)

        header_row = QtWidgets.QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)
        logo_label = QtWidgets.QLabel()
        logo_pix = self._load_logo_pixmap()
        if logo_pix is not None:
            logo_label.setPixmap(logo_pix)
            logo_label.setFixedHeight(40)
            logo_label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
            header_row.addWidget(logo_label, 0)
        else:
            fallback_logo = QtWidgets.QLabel("GRUPO ARGA")
            fallback_logo.setStyleSheet("color:#0F172A;font-weight:700;font-size:14px;")
            header_row.addWidget(fallback_logo, 0)

        sep = QtWidgets.QLabel("|")
        sep.setStyleSheet("color:#64748B;font-weight:700;font-size:16px;")
        header_row.addWidget(sep, 0)

        title = QtWidgets.QLabel("ULTIMA SINCRONIZACION DE PLACAS")
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(15)
        title.setFont(title_font)
        title.setStyleSheet("color:#0F172A;")
        header_row.addWidget(title, 1, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
        header_row.addStretch(1)
        root_layout.addLayout(header_row)

        matched = int(self.payload.get("matched_codes", 0) or 0)
        updated = int(self.payload.get("updated_rows", 0) or 0)
        dof_rate = float(self.payload.get("dof_rate", 18.5) or 18.5)
        dof_source = str(self.payload.get("dof_source", "FALLBACK") or "FALLBACK")
        summary = QtWidgets.QLabel(
            f"Coincidencias: {matched} | Filas actualizadas: {updated} | TC DOF: {dof_rate:,.4f} ({dof_source})"
        )
        summary.setStyleSheet("color:#475569;font-size:12px;")
        summary.setAlignment(QtCore.Qt.AlignLeft)
        root_layout.addWidget(summary)

        detail_text = str(self.payload.get("message", "") or "")
        detail = QtWidgets.QLabel(f"Detalle: {detail_text}")
        detail.setStyleSheet("color:#475569;font-size:12px;")
        detail.setWordWrap(True)
        root_layout.addWidget(detail)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:#FFFFFF;border:none;} QScrollBar:vertical{width:10px;}")
        content = QtWidgets.QWidget()
        content.setStyleSheet("background:#FFFFFF;")
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        items = list(self.payload.get("updated_items", []) or [])
        if not items:
            empty = QtWidgets.QLabel("No hubo cambios de campos en placas para mostrar.")
            empty.setStyleSheet("color:#64748B;font-style:italic;font-size:12px;")
            content_layout.addWidget(empty)
        else:
            for row in items:
                content_layout.addWidget(self._build_plate_group(row))
            content_layout.addStretch(1)

        scroll.setWidget(content)
        root_layout.addWidget(scroll, 1)
        self.setCentralWidget(root)

    def _load_logo_pixmap(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(base_dir, "..", "assets", "branding", "logo_horizontal.png"),
            os.path.join(base_dir, "..", "assets", "branding", "logo_icon1.png"),
            os.path.join(base_dir, "..", "grupo_arga_cover.jpeg"),
            os.path.join(base_dir, "..", "assets", "grupo_arga_cover.jpeg"),
        ]
        for p in candidates:
            full = os.path.abspath(p)
            if not os.path.exists(full):
                continue
            pix = QtGui.QPixmap(full)
            if pix.isNull():
                continue
            return pix.scaledToHeight(34, QtCore.Qt.SmoothTransformation)
        return None

    def _build_plate_group(self, row: dict) -> QtWidgets.QGroupBox:
        code = str(row.get("arga_code", "")).strip() or "SIN_CODIGO"
        sheet = str(row.get("sheet", "")).strip()
        changes = list(row.get("changes", []) or [])
        changes_map = {str(x.get("field", "")).strip(): x for x in changes}

        group = QtWidgets.QGroupBox(f"{code}  ({sheet})")
        group.setStyleSheet(
            "QGroupBox{font-weight:600;color:#0F172A;border:1px solid #CBD5E1;border-radius:8px;margin-top:8px;}"
            "QGroupBox::title{subcontrol-origin: margin;left:10px;padding:0 4px;}"
        )
        lay = QtWidgets.QVBoxLayout(group)
        lay.setContentsMargins(10, 12, 10, 10)

        table = QtWidgets.QTableWidget(3, len(PARAMS) + 1)
        table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(False)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setStyleSheet(
            "QTableWidget{background:#FFFFFF;alternate-background-color:#F8FAFC;gridline-color:#E2E8F0;}"
            "QHeaderView::section{background:#475569;color:white;font-weight:600;padding:4px;border:none;}"
        )

        headers = ["CAMPO"] + PARAMS
        table.setHorizontalHeaderLabels(headers)
        table.setVerticalHeaderLabels([])
        header = table.horizontalHeader()
        header.setDefaultAlignment(QtCore.Qt.AlignCenter)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        for c in range(1, len(headers)):
            header.setSectionResizeMode(c, QtWidgets.QHeaderView.Stretch)
        table.setColumnWidth(0, 96)

        table.setItem(0, 0, _color_item("ANTES", "#FFFFFF", True))
        table.item(0, 0).setBackground(QtGui.QColor("#DC2626"))
        table.setItem(1, 0, _color_item("DESPUES", "#FFFFFF", True))
        table.item(1, 0).setBackground(QtGui.QColor("#16A34A"))
        table.setItem(2, 0, _color_item("ESTATUS", "#0F172A", True))
        table.item(2, 0).setBackground(QtGui.QColor("#E2E8F0"))

        for idx, param in enumerate(PARAMS, start=1):
            item = changes_map.get(param)
            changed = item is not None
            before = str(item.get("before", "")).strip() if changed else "N/A"
            after = str(item.get("after", "")).strip() if changed else "N/A"
            before = before or "N/A"
            after = after or "N/A"
            status = "CAMBIO" if changed else "N/A"

            table.setItem(0, idx, _color_item(before, "#DC2626" if changed else "#64748B"))
            table.setItem(1, idx, _color_item(after, "#16A34A" if changed else "#64748B"))
            table.setItem(2, idx, _color_item(status, "#16A34A" if changed else "#64748B", True))
            for r in (0, 1, 2):
                it = table.item(r, idx)
                if it:
                    it.setTextAlignment(QtCore.Qt.AlignCenter)
            # Fondo tenue para resaltar antes/despues sin competir con el bloque lateral.
            if changed:
                b = table.item(0, idx)
                a = table.item(1, idx)
                if b:
                    b.setBackground(QtGui.QColor("#FEE2E2"))  # rojo suave
                if a:
                    a.setBackground(QtGui.QColor("#DCFCE7"))  # verde suave

        for r in (0, 1, 2):
            lead = table.item(r, 0)
            if lead:
                lead.setTextAlignment(QtCore.Qt.AlignCenter)
            table.setRowHeight(r, 30)

        table.setMinimumHeight(132)
        table.setMaximumHeight(150)
        lay.addWidget(table)
        return group


def main():
    if len(sys.argv) < 2:
        return 1

    payload_path = sys.argv[1]
    payload = _load_payload(payload_path)

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(_light_palette())
    app.setStyleSheet(
        "QMainWindow{background:#FFFFFF;color:#0F172A;}"
        "QLabel{background:transparent;}"
    )
    window = SyncViewerWindow(payload)
    window.show()
    code = app.exec()

    try:
        if payload_path and os.path.exists(payload_path):
            os.remove(payload_path)
    except Exception:
        pass
    return code


def _light_palette() -> QtGui.QPalette:
    p = QtGui.QPalette()
    p.setColor(QtGui.QPalette.Window, QtGui.QColor("#FFFFFF"))
    p.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#0F172A"))
    p.setColor(QtGui.QPalette.Base, QtGui.QColor("#FFFFFF"))
    p.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor("#F8FAFC"))
    p.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor("#FFFFFF"))
    p.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor("#0F172A"))
    p.setColor(QtGui.QPalette.Text, QtGui.QColor("#0F172A"))
    p.setColor(QtGui.QPalette.Button, QtGui.QColor("#FFFFFF"))
    p.setColor(QtGui.QPalette.ButtonText, QtGui.QColor("#0F172A"))
    p.setColor(QtGui.QPalette.BrightText, QtGui.QColor("#EF4444"))
    p.setColor(QtGui.QPalette.Highlight, QtGui.QColor("#3B82F6"))
    p.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#FFFFFF"))
    return p


if __name__ == "__main__":
    raise SystemExit(main())
