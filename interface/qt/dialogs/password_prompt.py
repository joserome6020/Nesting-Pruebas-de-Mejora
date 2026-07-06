"""Diálogo simple de contraseña."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from interface.qt.theme import COLOR_TEXTO_TITULO, apply_push_button, surface_dialog_stylesheet


def solicitar_contrasena(
    parent,
    *,
    titulo: str = "Contraseña requerida",
    mensaje: str = "Ingrese la contraseña para continuar:",
) -> str | None:
    dlg = QDialog(parent)
    dlg.setWindowTitle(titulo)
    dlg.setModal(True)
    dlg.setMinimumWidth(420)
    dlg.setStyleSheet(surface_dialog_stylesheet())

    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(20, 18, 20, 18)
    lay.setSpacing(12)

    lbl = QLabel(mensaje)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};font-size:13px;")
    lay.addWidget(lbl)

    ent = QLineEdit()
    ent.setEchoMode(QLineEdit.EchoMode.Password)
    ent.setPlaceholderText("Contraseña")
    lay.addWidget(ent)

    btns = QHBoxLayout()
    btns.addStretch()
    btn_ok = QPushButton("Aceptar")
    btn_cancel = QPushButton("Cancelar")
    apply_push_button(btn_ok, "#334155", font_size=11, padding="6px 14px")
    apply_push_button(btn_cancel, "#FFFFFF", font_size=11, padding="6px 14px")
    btns.addWidget(btn_cancel)
    btns.addWidget(btn_ok)
    lay.addLayout(btns)

    resultado: dict[str, str | None] = {"clave": None}

    def aceptar():
        txt = ent.text()
        if not str(txt or "").strip():
            QMessageBox.warning(dlg, "Atención", "Debe ingresar la contraseña.")
            return
        resultado["clave"] = txt
        dlg.accept()

    btn_ok.clicked.connect(aceptar)
    btn_cancel.clicked.connect(dlg.reject)
    ent.returnPressed.connect(aceptar)
    ent.setFocus(Qt.FocusReason.OtherFocusReason)

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return str(resultado["clave"] or "")
