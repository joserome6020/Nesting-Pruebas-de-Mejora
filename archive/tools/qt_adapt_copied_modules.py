#!/usr/bin/env python3
"""Adapta módulos copiados de Tk/CTk a imports y patrones Qt básicos."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REPLACEMENTS = [
    (r"import customtkinter as ctk\n", ""),
    (r"import tkinter as tk\n", ""),
    (r"from tkinter import messagebox\n", "from PySide6.QtWidgets import QMessageBox, QFileDialog, QMenu\n"),
    (r"from tkinter import ttk, messagebox\n", "from PySide6.QtWidgets import QMessageBox, QTreeWidget, QTreeWidgetItem, QHeaderView, QAbstractItemView\n"),
    (r"from tkinter import filedialog, messagebox, Menu\n", "from PySide6.QtWidgets import QMessageBox, QFileDialog, QMenu\n"),
    (r"from nesting_canvas import VisorNesting\n", "from interface.qt.nesting_canvas import VisorNesting\n"),
    (r"from modules\.visualizer import VisorDXF, generar_thumbnail\n", "from interface.qt.visualizer import VisorDXF, generar_thumbnail\n"),
    (r"from responsive_layout import configurar_contenedor_expandible\n", ""),
    (r"from nesting_modals import", "from interface.qt.dialogs.nesting_modals import"),
    (r"from nesting_lote_editor import", "from interface.qt.dialogs.lote_editor import"),
    (r"messagebox\.showerror\(", "QMessageBox.critical(None, "),
    (r"messagebox\.showwarning\(", "QMessageBox.warning(None, "),
    (r"messagebox\.showinfo\(", "QMessageBox.information(None, "),
    (r"messagebox\.askyesno\(", "_qt_askyesno("),
    (r"filedialog\.asksaveasfilename\(", "QFileDialog.getSaveFileName(None, "),
    (r"filedialog\.askopenfilename\(", "QFileDialog.getOpenFileName(None, "),
    (r"self\.after\((\d+),", r"QTimer.singleShot(\1,"),
    (r"Menu\(", "QMenu("),
]

HEADER_QT = '''"""Módulo adaptado a PySide6 — migración nativa Qt."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QWidget

def _qt_askyesno(title, message):
    return QMessageBox.question(None, title, message) == QMessageBox.StandardButton.Yes

'''


def adapt(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "PySide6" in text and "customtkinter" not in text:
        return
    for pat, repl in REPLACEMENTS:
        text = re.sub(pat, repl, text)
    text = text.replace("class TabNesting(ctk.CTkFrame):", "class TabNesting(QWidget):")
    text = text.replace("class TabParts(ctk.CTkFrame):", "class TabParts(QWidget):")
    text = text.replace("class TabSheets(ctk.CTkFrame):", "class TabSheets(QWidget):")
    text = text.replace("class VisorNesting(ctk.CTkFrame):", "class VisorNesting(QWidget):")
    if not text.startswith('"""Módulo adaptado'):
        text = HEADER_QT + text
    path.write_text(text, encoding="utf-8")
    print("adapted", path)


if __name__ == "__main__":
    for rel in [
        "interface/qt/nesting_canvas.py",
        "interface/qt/tabs/tab_nesting.py",
        "interface/qt/tabs/tab_parts.py",
        "interface/qt/tabs/tab_sheets.py",
    ]:
        adapt(ROOT / rel)
