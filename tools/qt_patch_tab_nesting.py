#!/usr/bin/env python3
"""Parches mecánicos en tab_nesting copiado para Qt."""
import re
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "interface/qt/tabs/tab_nesting.py"
text = path.read_text(encoding="utf-8")

header = '''"""Tab NESTING — PySide6 nativo (lógica 1:1 con oficial)."""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

import ezdxf
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QMenu,
)

import config
from interface.qt.nesting_canvas import VisorNesting
from interface.qt.ui_mixins import TimerHost, q_configure, scroll_clear, scroll_add_widget
from interface.qt.dialogs.nesting_modals import (
    abrir_modal_configuracion,
    abrir_modal_costos,
    mostrar_modal_escenarios,
    abrir_modal_transferencia,
    abrir_modal_transferencia_masiva,
)
from interface.qt.dialogs.lote_editor import abrir_editor_lote
from nesting_workspace import guardar_workspace, cargar_workspace_desde_archivo, aplicar_workspace
from postgres_connector import guardar_nesting_en_postgresql
from reporte_pdf_nesting import exportar_pdf_nesting
from utils_nesting import (
    obtener_siguiente_consecutivo,
    crear_estructura_carpetas,
    generar_combinaciones_lotes,
    escalar_piezas,
    ensamblar_escenario,
    generar_csv_compras,
)
from modules.processed_layers import ProcesadorDXF
from modules.plasma_compensator import compute_plasma_offset_mm
from modules.nesting_engine.efficiency_metrics import (
    actualizar_eficiencias_resultados,
    eficiencia_para_umbral_ignorar,
    formatear_eficiencias_placa,
    formatear_eficiencias_tanque,
    hoja_cuenta_para_deduccion,
    placa_debe_mostrar_opcion_ignorar,
)
from modules.nesting_engine.rtz_overlays import sincronizar_overlays_resultados

COLOR_TARJETA = "#FFFFFF"
COLOR_BORDE = "#CBD5E1"
COLOR_GRIS_DARK = "#1E293B"
COLOR_GRIS_MED = "#475569"
COLOR_TEXTO_TITULO = "#0F172A"
COLOR_TEXTO_SECUNDARIO = "#64748B"
DEFAULT_KERF_IN = 0.3
DEFAULT_MARGIN_IN = 0.15

'''

# strip old imports until class
idx = text.find("class TabNesting")
text = header + text[idx:]
text = text.replace("class TabNesting(ctk.CTkFrame):", "class TabNesting(QWidget, TimerHost):")
text = text.replace(
    "class TabNesting(QWidget, TimerHost):\n    def __init__(self, master, app_principal):\n        super().__init__(master, fg_color=\"transparent\")",
    "class TabNesting(QWidget, TimerHost):\n    def __init__(self, master, app_principal):\n        QWidget.__init__(self, master)\n        TimerHost.__init__(self)",
)

repls = [
    (r"messagebox\.showerror\(([^)]+)\)", r"QMessageBox.critical(self, \1)"),
    (r"messagebox\.showwarning\(([^)]+)\)", r"QMessageBox.warning(self, \1)"),
    (r"messagebox\.showinfo\(([^)]+)\)", r"QMessageBox.information(self, \1)"),
    (r"return messagebox\.showwarning", "QMessageBox.warning(self,"),
    (r"return messagebox\.showerror", "QMessageBox.critical(self,"),
    (r"return messagebox\.showinfo", "QMessageBox.information(self,"),
    (r"self\.after\((\d+),", r"QTimer.singleShot(\1,"),
    (r"filedialog\.asksaveasfilename\(", "self._ask_save_file("),
    (r"filedialog\.askopenfilename\(", "self._ask_open_file("),
    (r"self\.btn_run_nest\.configure\(state=\"disabled\"\)", "self.btn_run_nest.setEnabled(False)"),
    (r"self\.btn_run_nest\.configure\(state=\"normal\"\)", "self.btn_run_nest.setEnabled(True)"),
    (r"self\.btn_ver_lotes\.configure\(state=\"disabled\"\)", "self.btn_ver_lotes.setEnabled(False)"),
    (r"self\.btn_ver_lotes\.configure\(state=\"normal\"\)", "self.btn_ver_lotes.setEnabled(True)"),
    (r"self\.lbl_cantidad\.configure\(text=", "self.lbl_cantidad.setText("),
    (r"self\.lbl_id_hud\.configure\(text=", "self.lbl_id_hud.setText("),
    (r"self\.ent_kerf\.delete\(0, 'end'\)", "self.ent_kerf.clear()"),
    (r"self\.ent_kerf\.insert\(0,", "self.ent_kerf.setText("),
    (r"self\.btn_transferir\.configure\(state=estado_transfer\)", "self.btn_transferir.setEnabled(estado_transfer == 'normal')"),
    (r"self\.btn_rot_90\.configure\(state=estado_rot\)", "self.btn_rot_90.setEnabled(estado_rot == 'normal')"),
    (r"self\.btn_rot_m1\.configure\(state=estado_rot\)", "self.btn_rot_m1.setEnabled(estado_rot == 'normal')"),
    (r"self\.btn_rot_p1\.configure\(state=estado_rot\)", "self.btn_rot_p1.setEnabled(estado_rot == 'normal')"),
    (r"self\.btn_transferir\.configure\(text=", "self.btn_transferir.setText("),
    (r"self\.cmb_lotes\.configure\(values=", "# TODO cmb values "),
    (r"self\.cmb_lotes\.set\(", "self.cmb_lotes.setCurrentText("),
    (r"self\.cmb_lotes\.cget\(\"values\"\)", "[self.cmb_lotes.itemText(i) for i in range(self.cmb_lotes.count())]"),
    (r"for w in self\.lista_hojas\.winfo_children\(\):\s*\n\s*w\.destroy\(\)",
     "scroll_clear(self.lista_hojas)"),
]
for a, b in repls:
    text = re.sub(a, b, text)

path.write_text(text, encoding="utf-8")
print("patched", path)
