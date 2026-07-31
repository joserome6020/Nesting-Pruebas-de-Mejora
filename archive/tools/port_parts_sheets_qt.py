"""Porta tab_parts.py y tab_sheets.py a Qt (uso único)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def port_parts():
    src = (ROOT / "tab_parts.py").read_text(encoding="utf-8")
    out = src

    header = '''"""Tab PARTS — PySide6 nativo."""
from __future__ import annotations

import os
import csv
import json
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from interface.qt.visualizer import VisorDXF, generar_thumbnail
from interface.qt.ui_mixins import TimerHost, scroll_clear, scroll_add_widget

'''

    # strip old imports through class colors
    out = re.sub(r"^import os.*?^ARGB_BTN_4 = .*?\n\n", header, out, count=1, flags=re.S | re.M)
    out = out.replace("class TabParts(ctk.CTkFrame):", "class TabParts(QWidget, TimerHost):")
    out = out.replace("        super().__init__(master, fg_color=\"transparent\")\n        self.app = app_principal", """        QWidget.__init__(self, master)
        TimerHost.__init__(self)
        self.app = app_principal
        self._row_widgets = {}""")
    out = out.replace("from tkinter import ttk, messagebox\n", "")
    out = out.replace("import customtkinter as ctk\n", "")
    out = out.replace("from responsive_layout import configurar_contenedor_expandible\n", "")
    out = out.replace("from modules.visualizer import VisorDXF, generar_thumbnail\n", "")

    # messagebox
    out = out.replace("messagebox.showerror(", "QMessageBox.critical(self, ")
    out = out.replace("messagebox.showinfo(", "QMessageBox.information(self, ")
    out = out.replace(", parent=ventana)", ")")

    # ent_tanques
    out = out.replace("self.ent_tanques.get()", "self.ent_tanques.text()")
    out = out.replace("self.ent_tanques.delete(0, \"end\")\n            self.ent_tanques.insert(0,", "self.ent_tanques.setText(")
    out = out.replace("self.lbl_tanques.configure(text=", "self.lbl_tanques.setText(")

    # Replace setup_ui block - find and replace entire method
    setup_new = '''    def setup_ui(self):
        root = QGridLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setColumnStretch(0, 2)
        root.setColumnStretch(1, 3)
        root.setColumnMinimumWidth(0, 680)
        root.setColumnMinimumWidth(1, 320)

        frame_tabla = QFrame()
        frame_tabla.setStyleSheet(f"background:{COLOR_TARJETA};border:1px solid {COLOR_BORDE};border-radius:15px;")
        tabla_lay = QVBoxLayout(frame_tabla)
        tabla_lay.setContentsMargins(5, 5, 5, 5)

        frame_header = QWidget()
        hdr = QHBoxLayout(frame_header)
        self.lbl_tanques = QLabel("⚙️ TANQUES DEL PROYECTO:")
        self.lbl_tanques.setStyleSheet("font-weight:700;color:#3B82F6;font-size:15px;")
        hdr.addWidget(self.lbl_tanques)
        self.ent_tanques = QLineEdit("X1")
        self.ent_tanques.setFixedWidth(70)
        self.ent_tanques.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.addWidget(self.ent_tanques)
        self.btn_aplicar_tanques = QPushButton("Aplicar")
        self.btn_aplicar_tanques.setStyleSheet(f"background:{ARGB_BTN_2};font-weight:700;")
        self.btn_aplicar_tanques.clicked.connect(self.aplicar_cantidad_tanques)
        hdr.addWidget(self.btn_aplicar_tanques)
        self.ent_tanques.returnPressed.connect(self.aplicar_cantidad_tanques)
        hdr.addStretch()
        self.btn_lista_largos = QPushButton("Lista de largos")
        self.btn_lista_largos.setStyleSheet(f"background:{ARGB_BTN_3};font-weight:700;")
        self.btn_lista_largos.clicked.connect(self.abrir_ventana_lista_largos)
        hdr.addWidget(self.btn_lista_largos)
        tabla_lay.addWidget(frame_header)

        head = QFrame()
        head.setFixedHeight(45)
        head.setStyleSheet(f"background:{COLOR_GRIS_MED};")
        head_grid = QGridLayout(head)
        titulos = ["PIEZA / REF", "MATERIAL", "QTY", "TOTAL QTY", "CALIBRE", "ESTADO", "VISTA"]
        for i, txt in enumerate(titulos):
            lbl = QLabel(txt)
            lbl.setStyleSheet("color:white;font-weight:700;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            head_grid.addWidget(lbl, 0, i)
        tabla_lay.addWidget(head)

        self.lista_scroll = QScrollArea()
        self.lista_scroll.setWidgetResizable(True)
        self._lista_inner = QWidget()
        self._lista_layout = QVBoxLayout(self._lista_inner)
        self._lista_layout.setContentsMargins(0, 0, 0, 0)
        self.lista_scroll.setWidget(self._lista_inner)
        tabla_lay.addWidget(self.lista_scroll, 1)
        root.addWidget(frame_tabla, 0, 0)

        self.frame_derecho = QWidget()
        der_lay = QVBoxLayout(self.frame_derecho)
        frame_visor_bg = QFrame()
        frame_visor_bg.setStyleSheet(f"background:{COLOR_TARJETA};border:1px solid {COLOR_BORDE};border-radius:15px;")
        vis_lay = QVBoxLayout(frame_visor_bg)
        tit = QLabel("DETALLE DE PIEZA")
        tit.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
        tit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vis_lay.addWidget(tit)
        self.frame_black_visor = QFrame()
        self.frame_black_visor.setStyleSheet("background:#0F172A;border-radius:10px;")
        vis_inner = QVBoxLayout(self.frame_black_visor)
        self.visor = VisorDXF(self.frame_black_visor)
        vis_inner.addWidget(self.visor)
        vis_lay.addWidget(self.frame_black_visor, 1)
        der_lay.addWidget(frame_visor_bg, 1)
        root.addWidget(self.frame_derecho, 0, 1)
'''

    out = re.sub(r"    def setup_ui\(self\):.*?    def refrescar_tabla", setup_new + "\n    def refrescar_tabla", out, count=1, flags=re.S)

    refrescar_patch = '''    def refrescar_tabla(self, datos):
        multiplicador = getattr(self.app, "multiplicador_tanques", 1)
        self.lbl_tanques.setText("⚙️ TANQUES DEL PROYECTO:")
        try:
            self.ent_tanques.setText(f"X{int(multiplicador)}")
        except Exception:
            pass

        self.rutas_dxf_actuales = []
        scroll_clear(self.lista_scroll)
        self._row_widgets = {}

        for idx, item in enumerate(datos):
            pieza, mat, qty_total, cal, st, ruta = item
            if ruta:
                self.rutas_dxf_actuales.append(str(ruta))
            try:
                tot_val = int(qty_total)
                qty_unidad = max(1, tot_val // multiplicador)
            except Exception:
                tot_val, qty_unidad = qty_total, qty_total

            color_fondo = "#FFFFFF" if idx % 2 == 0 else "#F8FAFC"
            row = QFrame()
            row.setFixedHeight(48)
            row.setStyleSheet(f"background:{color_fondo};")
            row.orig_color = color_fondo
            row_lay = QGridLayout(row)
            row_lay.setContentsMargins(4, 2, 4, 2)

            valores = [pieza, mat, str(qty_unidad), str(tot_val), cal, st]
            for i, conf in enumerate(self.local_col_config):
                if i < 6:
                    lbl = QLabel(valores[i])
                    lbl.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};")
                    if i == 0:
                        lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    else:
                        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    lbl.mousePressEvent = lambda ev, r=ruta, f=row, p=pieza: self.seleccionar_fila(r, f, p)
                    row_lay.addWidget(lbl, 0, i)
                elif i == 6:
                    try:
                        thumb = generar_thumbnail(ruta, size=(32, 32))
                        if thumb:
                            l_t = QLabel()
                            l_t.setPixmap(thumb)
                            l_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
                            l_t.mousePressEvent = lambda ev, r=ruta, f=row, p=pieza: self.seleccionar_fila(r, f, p)
                            row_lay.addWidget(l_t, 0, i)
                    except Exception:
                        pass

            row.mousePressEvent = lambda ev, r=ruta, f=row, p=pieza: self.seleccionar_fila(r, f, p)
            scroll_add_widget(self.lista_scroll, row)
            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setStyleSheet(f"background:{COLOR_BORDE};")
            scroll_add_widget(self.lista_scroll, sep)
'''

    out = re.sub(r"    def refrescar_tabla\(self, datos\):.*?    def _resolver_job_data_csv_actual", refrescar_patch + "\n    def _resolver_job_data_csv_actual", out, count=1, flags=re.S)

    out = out.replace("""    def seleccionar_fila(self, ruta_dxf, frame_fila, nombre_pieza):
        for child in self.lista_scroll.winfo_children():
            if hasattr(child, "orig_color"):
                child.configure(fg_color=child.orig_color)
        frame_fila.configure(fg_color="#DBEAFE")""", """    def seleccionar_fila(self, ruta_dxf, frame_fila, nombre_pieza):
        inner = self.lista_scroll.widget()
        if inner:
            for i in range(self._lista_layout.count()):
                w = self._lista_layout.itemAt(i).widget()
                if w and hasattr(w, "orig_color"):
                    w.setStyleSheet(f"background:{w.orig_color};")
        frame_fila.setStyleSheet("background:#DBEAFE;")""")

    # _crear_bloque_job - replace ttk with QTableWidget
    bloque_new = '''    def _crear_bloque_job(self, contenedor, grupo, columnas, encabezados, anchos):
        status = grupo.get("status", "sin_csv")
        if status == "ok":
            color_titulo, texto_status, color_status, color_fondo = "#2563EB", "CON LISTA DE LARGOS", "#16A34A", "#F8FAFC"
        elif status == "sin_csv":
            color_titulo, texto_status, color_status, color_fondo = "#DC2626", "SIN LISTA DE LARGOS", "#DC2626", "#FEF2F2"
        else:
            color_titulo, texto_status, color_status, color_fondo = "#D97706", "ERROR AL LEER CSV", "#D97706", "#FFFBEB"

        frame_job = QFrame()
        frame_job.setStyleSheet(f"background:{color_fondo};border:1px solid {COLOR_BORDE};border-radius:10px;")
        fj_lay = QVBoxLayout(frame_job)
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel(f"JOB: {grupo['job']}", styleSheet=f"font-weight:700;color:{color_titulo};"))
        hdr.addStretch()
        hdr.addWidget(QLabel(texto_status, styleSheet=f"font-weight:700;color:{color_status};"))
        fj_lay.addLayout(hdr)
        if status != "ok":
            fj_lay.addWidget(QLabel(grupo.get("mensaje", ""), styleSheet=f"color:{color_status};"))
            contenedor.layout().addWidget(frame_job)
            return
        table = QTableWidget(min(max(len(grupo["rows"]), 3), 10), len(columnas))
        table.setHorizontalHeaderLabels([encabezados[c] for c in columnas])
        for ri, row in enumerate(grupo["rows"]):
            table.setItem(ri, 0, QTableWidgetItem(str(row.get("nombre", ""))))
            table.setItem(ri, 1, QTableWidgetItem(str(row.get("clasificacion", ""))))
            table.setItem(ri, 2, QTableWidgetItem(f"{float(row.get('largo_in', 0) or 0):.3f}"))
            table.setItem(ri, 3, QTableWidgetItem(str(row.get("cantidad", 0))))
        fj_lay.addWidget(table)
        contenedor.layout().addWidget(frame_job)
'''

    out = re.sub(r"    def _crear_bloque_job\(self, contenedor, grupo, columnas, encabezados, anchos\):.*?    def abrir_ventana_lista_largos", bloque_new + "\n    def abrir_ventana_lista_largos", out, count=1, flags=re.S)

    dialog_new = '''    def abrir_ventana_lista_largos(self):
        grupos = self._cargar_listas_largos_desde_rutas()
        if not grupos:
            QMessageBox.information(self, "Lista de largos", "No se encontraron rutas AutoDXF válidas en el contexto actual.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Lista de largos")
        dlg.resize(1260, 680)
        dlg.setModal(True)
        lay = QVBoxLayout(dlg)
        card = QFrame()
        card.setStyleSheet(f"background:{COLOR_TARJETA};border:1px solid {COLOR_BORDE};border-radius:12px;")
        card_lay = QVBoxLayout(card)
        card_lay.addWidget(QLabel("LISTA DE LARGOS", styleSheet=f"font-weight:700;color:{COLOR_TEXTO_TITULO};font-size:16px;"))
        total_grupos = len(grupos)
        total_ok = sum(1 for g in grupos if g.get("status") == "ok")
        total_sin_csv = sum(1 for g in grupos if g.get("status") == "sin_csv")
        total_error = sum(1 for g in grupos if g.get("status") == "error_csv")
        total_rows = sum(len(g["rows"]) for g in grupos if g.get("status") == "ok")
        card_lay.addWidget(QLabel(
            f"Jobs detectados: {total_grupos}   |   Con lista: {total_ok}   |   "
            f"Sin lista: {total_sin_csv}   |   Error lectura: {total_error}   |   Registros totales: {total_rows}"
        ))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        scroll.setWidget(inner)
        columnas = ("nombre", "clasificacion", "largo_in", "cantidad")
        encabezados = {"nombre": "NOMBRE", "clasificacion": "CLASIFICACIÓN", "largo_in": "LARGO (in)", "cantidad": "CANTIDAD"}
        anchos = {"nombre": 360, "clasificacion": 180, "largo_in": 120, "cantidad": 120}
        for grupo in grupos:
            self._crear_bloque_job(inner, grupo, columnas, encabezados, anchos)
        card_lay.addWidget(scroll, 1)
        lay.addWidget(card)
        self.ventana_lista_largos = dlg
        dlg.exec()
'''

    out = re.sub(r"    def abrir_ventana_lista_largos\(self\):.*$", dialog_new, out, count=1, flags=re.S)

    (ROOT / "interface" / "qt" / "tabs" / "tab_parts.py").write_text(out, encoding="utf-8")
    print("tab_parts.py ported")


def port_sheets():
    src = (ROOT / "tab_sheets.py").read_text(encoding="utf-8")
    out = src

    header = '''"""Tab SHEETS — PySide6 nativo."""
from __future__ import annotations

import re
import os
import csv
import sys
import json
import time
import tempfile
import subprocess
import shutil

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import config
from interface.qt.ui_mixins import TimerHost, scroll_clear, scroll_add_widget

'''

    out = re.sub(r"^import re.*?^COLOR_AZUL_ACCENTO = .*?\n\n", header, out, count=1, flags=re.S | re.M)
    out = out.replace("class TabSheets(ctk.CTkFrame):", "class TabSheets(QWidget, TimerHost):")
    out = out.replace(
        "        super().__init__(master, fg_color=\"transparent\")\n        self.app = app_principal",
        "        QWidget.__init__(self, master)\n        TimerHost.__init__(self)\n        self.app = app_principal",
    )
    out = out.replace("import customtkinter as ctk\n", "")
    out = out.replace("import tkinter as tk\n", "")
    out = out.replace("from tkinter import messagebox\n", "")

    out = out.replace("messagebox.showinfo(", "QMessageBox.information(self, ")
    out = out.replace("messagebox.showwarning(", "QMessageBox.warning(self, ")
    out = out.replace("messagebox.showerror(", "QMessageBox.critical(self, ")

    out = out.replace("self.after(80, self.actualizar_inventario)", "QTimer.singleShot(80, self.actualizar_inventario)")
    out = out.replace("self.btn_sync_herinox.configure(state=\"disabled\", text=\"Sincronizando...\")", "self.btn_sync_herinox.setEnabled(False); self.btn_sync_herinox.setText(\"Sincronizando...\")")
    out = out.replace("self.btn_sync_herinox.configure(state=\"normal\", text=\"⟳ Sincronizar con Herinox\")", "self.btn_sync_herinox.setEnabled(True); self.btn_sync_herinox.setText(\"⟳ Sincronizar con Herinox\")")

    # Remove selector popup state - use combos
    out = re.sub(
        r"        self\._selector_vars = \{\}\n        self\._selector_values = \{\}\n        self\._selector_anchors = \{\}\n        self\._selector_callbacks = \{\}\n        self\._selector_popup = None\n        self\._selector_popup_key = None\n        self\._selector_listbox = None\n        \n",
        "        self._selector_combos = {}\n        self._selector_callbacks = {}\n        ",
        out,
    )

    setup_new = '''    def setup_ui(self):
        outer = QVBoxLayout(self)
        cont = QFrame()
        cont.setStyleSheet(f"background:{COLOR_TARJETA};border:1px solid {COLOR_BORDE};border-radius:15px;")
        cont_lay = QVBoxLayout(cont)

        filtros = QFrame()
        filtros.setFixedHeight(75)
        filtros.setStyleSheet("background:#F8FAFC;")
        filtros_lay = QHBoxLayout(filtros)
        filtros_lay.setContentsMargins(20, 6, 20, 4)

        def _filtro(lbl, key, width, values, on_change):
            grp = QWidget()
            gl = QVBoxLayout(grp)
            gl.setContentsMargins(0, 0, 0, 0)
            gl.addWidget(QLabel(lbl, styleSheet=f"font-weight:700;color:{COLOR_TEXTO_SECUNDARIO};font-size:10px;"))
            cmb = QComboBox()
            cmb.setFixedWidth(width)
            cmb.addItems(values or ["TODOS"])
            cmb.currentTextChanged.connect(lambda _t, k=key: on_change())
            self._selector_combos[key] = cmb
            self._selector_callbacks[key] = on_change
            gl.addWidget(cmb)
            filtros_lay.addWidget(grp)

        _filtro("FILTRAR CALIBRE NOMINAL", "nominal", 120, ["TODOS"], self.al_cambiar_nominal)
        _filtro("FILTRAR THICKNESS", "thickness", 120, ["TODOS"], self.al_cambiar_thickness)
        _filtro("FILTRAR MATERIAL", "material", 140, ["TODOS"], self.al_cambiar_material)
        _filtro("FILTRAR ARGA CODE", "arga_code", 120, ["TODOS"], self._on_filter_change)
        _filtro("STOCK HERINOX", "stock", 140, ["TODOS", "DISPONIBLE", "NO DISPONIBLE", "NO EXISTENTE"], self._on_filter_change)
        _filtro("FILTRO $$/LB", "precio", 140, ["TODOS", "MENOR PRECIO", "MAYOR PRECIO"], self.aplicar_filtros)
        filtros_lay.addStretch()
        self.btn_remanentes = QPushButton("📦 REMANENTES DISPONIBLES")
        self.btn_remanentes.setStyleSheet(f"background:{COLOR_GRIS_MED};font-weight:700;")
        self.btn_remanentes.clicked.connect(self.abrir_inventario_remanentes)
        filtros_lay.addWidget(self.btn_remanentes)
        cont_lay.addWidget(filtros)

        self.tabs = QTabWidget()
        self.tab_empresa = QWidget()
        self.tab_proveedor = QWidget()
        self.tabs.addTab(self.tab_empresa, "🏢 STOCK EMPRESA")
        self.tabs.addTab(self.tab_proveedor, "🚚 STOCK PROVEEDOR")
        self.tabs.currentChanged.connect(lambda _i: self.aplicar_filtros())

        for tab_w, lista_attr in [(self.tab_empresa, "lista_empresa"), (self.tab_proveedor, "lista_proveedor")]:
            tl = QVBoxLayout(tab_w)
            tl.setContentsMargins(0, 0, 0, 0)
            self.crear_encabezados(tab_w)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            inner = QWidget()
            inner.setLayout(QVBoxLayout())
            inner.layout().setContentsMargins(0, 0, 0, 0)
            scroll.setWidget(inner)
            setattr(self, lista_attr, scroll)
            tl.addWidget(scroll, 1)

        cont_lay.addWidget(self.tabs, 1)
        acciones = QHBoxLayout()
        self.btn_sync_herinox = QPushButton("⟳ Sincronizar con Herinox")
        self.btn_sync_herinox.setStyleSheet(f"background:{COLOR_GRIS_MED};font-weight:700;color:white;")
        self.btn_sync_herinox.clicked.connect(self.sincronizar_con_herinox)
        acciones.addWidget(self.btn_sync_herinox)
        self.btn_sync = QPushButton("▣ Ver cambios de sincronizacion")
        self.btn_sync.clicked.connect(self.mostrar_cambios_sincronizacion)
        acciones.addWidget(self.btn_sync)
        acciones.addStretch()
        cont_lay.addLayout(acciones)
        outer.addWidget(cont)
'''

    out = re.sub(r"    def setup_ui\(self\):.*?    def crear_encabezados", setup_new + "\n    def crear_encabezados", out, count=1, flags=re.S)

    encabezados_new = '''    def crear_encabezados(self, parent_frame):
        lay = parent_frame.layout()
        h_sheet = QFrame()
        h_sheet.setFixedHeight(35)
        h_sheet.setStyleSheet(f"background:{COLOR_GRIS_MED};")
        grid = QGridLayout(h_sheet)
        titles = ["CALIBRE NOMINAL", "THICKNESS", "MATERIAL", "ARGA CODE", "LENGTH", "WIDTH", "LB", "$$/LB", "PRECIO TOTAL", "STOCK"]
        for i, t in enumerate(titles):
            lbl = QLabel(t)
            lbl.setStyleSheet("color:white;font-weight:700;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(lbl, 0, i)
        lay.insertWidget(0, h_sheet)
'''

    out = re.sub(r"    def crear_encabezados\(self, parent_frame\):.*?    def _nominal_from_row", encabezados_new + "\n    def _nominal_from_row", out, count=1, flags=re.S)

    # Replace selector helpers
    selector_helpers = '''
    def _build_selector(self, parent, key, width, values, on_change):
        pass  # legacy — combos creados en setup_ui

    def _selector_get(self, key: str) -> str:
        cmb = self._selector_combos.get(key)
        return str(cmb.currentText() if cmb else "TODOS").strip() or "TODOS"

    def _selector_set(self, key: str, value: str, trigger: bool = False):
        cmb = self._selector_combos.get(key)
        if not cmb:
            return
        clean = str(value or "TODOS").strip() or "TODOS"
        idx = cmb.findText(clean)
        if idx < 0:
            clean = "TODOS"
            idx = cmb.findText("TODOS")
        cmb.blockSignals(True)
        if idx >= 0:
            cmb.setCurrentIndex(idx)
        cmb.blockSignals(False)
        if trigger:
            cb = self._selector_callbacks.get(key)
            if cb:
                cb()

    def _selector_set_values(self, key: str, values):
        cmb = self._selector_combos.get(key)
        if not cmb:
            return
        vals = [str(v).strip() for v in list(values or []) if str(v).strip()]
        if "TODOS" not in vals:
            vals = ["TODOS"] + vals
        cur = cmb.currentText()
        cmb.blockSignals(True)
        cmb.clear()
        cmb.addItems(vals)
        if cur in vals:
            cmb.setCurrentText(cur)
        else:
            cmb.setCurrentIndex(0)
        cmb.blockSignals(False)

    def _toggle_selector_dropdown(self, key: str):
        pass

    def _ensure_selector_popup(self):
        pass

    def _open_selector_dropdown(self, key: str):
        pass

    def _on_selector_focus_out(self, _event=None):
        pass

    def _select_active_listbox_item(self, event=None):
        pass

    def _close_selector_dropdown(self, key: str = None):
        pass
'''
    out = re.sub(
        r"    def _build_selector\(self, parent, key, width, values, on_change\):.*?    def _set_arga_code\(self, value: str\):",
        selector_helpers + "\n    def _set_arga_code(self, value: str):",
        out,
        count=1,
        flags=re.S,
    )

    out = out.replace("    def _refresh_tabs_text_color(self):\n        # Compatibilidad con versiones viejas de CustomTkinter:\n        # coloreamos texto por botón usando la referencia interna.\n        try:\n            seg = getattr(self.tabs, \"_segmented_button\", None)\n            btns = getattr(seg, \"_buttons_dict\", {}) or {}\n            selected = str(self.tabs.get() or \"\").strip()\n            for name, btn in btns.items():\n                if str(name) == selected:\n                    btn.configure(text_color=\"#FFFFFF\")\n                else:\n                    btn.configure(text_color=COLOR_GRIS_DARK)\n        except Exception:\n            pass\n", "    def _refresh_tabs_text_color(self):\n        pass\n")

    out = out.replace('        pestaña_actual = self.tabs.get()\n        if pestaña_actual == "🏢 STOCK EMPRESA":', '        pestaña_actual = self.tabs.tabText(self.tabs.currentIndex())\n        if pestaña_actual == "🏢 STOCK EMPRESA":')
    out = out.replace('        pestaña_actual = self.tabs.get()\n        \n        if pestaña_actual == "🏢 STOCK EMPRESA":', '        pestaña_actual = self.tabs.tabText(self.tabs.currentIndex())\n        \n        if pestaña_actual == "🏢 STOCK EMPRESA":')

    # aplicar_filtros row building
    aplicar_patch = '''    def aplicar_filtros(self, *args):
        self._refresh_tabs_text_color()
        pestaña_actual = self.tabs.tabText(self.tabs.currentIndex())
        if pestaña_actual == "🏢 STOCK EMPRESA":
            datos_base = self.app.datos_placas_empresa
            lista_activa = self.lista_empresa
        else:
            datos_base = self.app.datos_placas_proveedor
            lista_activa = self.lista_proveedor

        scroll_clear(lista_activa)
        filtros = {
            "nominal": self._selector_get("nominal"),
            "thickness": self._selector_get("thickness"),
            "material": self._selector_get("material"),
            "arga_code": self._get_arga_code(),
            "stock": self._selector_get("stock"),
        }
        precio_val = self._selector_get("precio")
        filtrados = list(datos_base)
        if not filtrados:
            return
        filtrados = [r for r in filtrados if self._row_matches_filters(r, filtros)]
        if precio_val == "MENOR PRECIO":
            filtrados.sort(key=lambda x: self.app._extractor_numerico(x[7]))
        elif precio_val == "MAYOR PRECIO":
            filtrados.sort(key=lambda x: self.app._extractor_numerico(x[7]), reverse=True)
        else:
            filtrados.sort(key=lambda x: self.app._extractor_numerico(x[0]))

        for idx, fila in enumerate(filtrados):
            color = "#FFFFFF" if idx % 2 == 0 else "#F8FAFC"
            row = QFrame()
            row.setFixedHeight(55)
            row.setStyleSheet(f"background:{color};")
            row_lay = QGridLayout(row)
            try:
                precio_por_libra = self.app._extractor_numerico(fila[7])
                mxn_placa = self.app._extractor_numerico(fila[6])
                lb_placa = self.app._extractor_numerico(fila[5])
                tc_dof = float(getattr(self.app, "herinox_tc_dof", 18.50) or 18.50)
                if precio_por_libra > 0 and lb_placa > 0:
                    costo_placa_usd = precio_por_libra * lb_placa
                else:
                    costo_placa_usd = (mxn_placa / tc_dof) if tc_dof > 0 else 0.0
                str_costo_placa = f"${costo_placa_usd:,.2f}" if costo_placa_usd > 0 else "$0.00"
                str_precio_libra = f"${precio_por_libra:,.2f}" if precio_por_libra > 0 else "-"
            except Exception:
                str_costo_placa, str_precio_libra = "---", "---"
            nominal = str(getattr(self.app, "herinox_nominal_by_code", {}).get(str(fila[2]).strip(), "N/A") or "N/A")
            thickness_mostrar = self._normalize_thickness(fila[0]) or str(fila[0] if str(fila[0]) != "nan" else "-")
            valores_mostrar = [
                nominal, thickness_mostrar,
                str(fila[1] if str(fila[1]) != "nan" else "-"),
                str(fila[2] if str(fila[2]) != "nan" else "-"),
                str(fila[3] if str(fila[3]) != "nan" else "-"),
                str(fila[4] if str(fila[4]) != "nan" else "-"),
                str(fila[5] if str(fila[5]) != "nan" else "-"),
                str_precio_libra, str_costo_placa,
                str(fila[8] if str(fila[8]) != "nan" else "-"),
            ]
            for i in range(10):
                if i == 8:
                    lbl = QLabel(valores_mostrar[i])
                    lbl.setStyleSheet("color:#2563EB;font-weight:700;")
                elif i == 9:
                    estado = self._stock_estado(valores_mostrar[i])
                    c = "#16A34A" if estado == "DISPONIBLE" else ("#CA8A04" if estado == "NO DISPONIBLE" else "#DC2626")
                    lbl = QLabel(estado)
                    lbl.setStyleSheet(f"color:{c};font-weight:700;")
                else:
                    lbl = QLabel(valores_mostrar[i])
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                row_lay.addWidget(lbl, 0, i)
            scroll_add_widget(lista_activa, row)
'''

    out = re.sub(r"    def aplicar_filtros\(self, \*args\):.*?    def actualizar_inventario", aplicar_patch + "\n    def actualizar_inventario", out, count=1, flags=re.S)

    # Replace tk sync viewer fallback with Qt dialog
    out = re.sub(
        r"    def _mostrar_cambios_sincronizacion_tk\(self, resultado\):.*",
        '''    def _mostrar_cambios_sincronizacion_qt_inline(self, resultado):
        dlg = QDialog(self)
        dlg.setWindowTitle("Cambios de sincronización Herinox")
        dlg.resize(900, 600)
        lay = QVBoxLayout(dlg)
        txt = QTextEdit()
        txt.setReadOnly(True)
        items = list(getattr(resultado, "updated_items", []) or [])
        lines = [
            f"OK: {getattr(resultado, 'ok', False)}",
            f"Filas actualizadas: {getattr(resultado, 'updated_rows', 0)}",
            f"Codigos: {getattr(resultado, 'matched_codes', 0)}",
            "",
        ]
        for it in items[:500]:
            lines.append(str(it))
        txt.setPlainText("\\n".join(lines))
        lay.addWidget(txt)
        btn = QPushButton("Cerrar")
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn)
        dlg.exec()
''',
        out,
        flags=re.S,
    )

    out = out.replace(
        "        # Fallback seguro a ventana Tkinter.\n        self._mostrar_cambios_sincronizacion_tk(resultado)",
        "        self._mostrar_cambios_sincronizacion_qt_inline(resultado)",
    )

    (ROOT / "interface" / "qt" / "tabs" / "tab_sheets.py").write_text(out, encoding="utf-8")
    print("tab_sheets.py ported")


if __name__ == "__main__":
    port_parts()
    port_sheets()
