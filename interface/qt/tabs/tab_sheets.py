"""Tab SHEETS — PySide6 nativo."""
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
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import config
from interface.material_colors import paleta_material
from interface.qt.ui_mixins import TimerHost, scroll_clear, scroll_add_widget
from interface.qt.layout_helpers import make_card, make_scroll, make_scroll_content
from interface.qt.theme import (
    apply_push_button,
    surface_dialog_stylesheet,
    COLOR_ACENTO as COLOR_AZUL_ACENTO,
    COLOR_BORDE,
    COLOR_FONDO_APP,
    COLOR_GRIS_DARK,
    COLOR_GRIS_MED,
    COLOR_TARJETA,
    COLOR_TEXTO_SECUNDARIO,
    COLOR_TEXTO_TITULO,
)


class TabSheets(QWidget, TimerHost):
    def __init__(self, master, app_principal):
        QWidget.__init__(self, master)
        TimerHost.__init__(self)
        self.app = app_principal
        
        # --- CONFIGURACIÓN LOCAL DE COLUMNAS (9 en total) ---
        self.local_col_config = [
            {"weight": 1, "min": 90},  # Calibre nominal (0)
            {"weight": 1, "min": 60},  # Thickness (1)
            {"weight": 2, "min": 120}, # Material (2)
            {"weight": 2, "min": 100}, # Arga Code (3)
            {"weight": 1, "min": 60},  # Length (4)
            {"weight": 1, "min": 60},  # Width (5)
            {"weight": 1, "min": 60},  # LB (6)
            {"weight": 1, "min": 80},  # $$/LB (7)
            {"weight": 2, "min": 110}, # PRECIO TOTAL PLACA USD (8)
            {"weight": 1, "min": 90},  # Stock (9)
        ]
        self._selector_combos = {}
        self._selector_callbacks = {}
        self.setup_ui()
        # Carga automática de placas al abrir la pestaña SHEETS.
        QTimer.singleShot(80, self.actualizar_inventario)
        self._last_qt_viewer_error = ""

    _SHEET_GRID_MARGIN_H = 10
    _SHEET_SWATCH_PX = 14
    _SHEET_SWATCH_GAP = 6

    def _apply_sheet_grid_columns(self, grid: QGridLayout) -> None:
        grid.setHorizontalSpacing(4)
        for i, conf in enumerate(self.local_col_config):
            grid.setColumnStretch(i, conf["weight"])
            grid.setColumnMinimumWidth(i, conf["min"])

    def _celda_calibre_nominal(self, texto: str, color_material: str | None = None) -> QWidget:
        """Misma estructura en encabezado (spacer) y filas (swatch de color)."""
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(self._SHEET_SWATCH_GAP)
        if color_material:
            swatch = QFrame()
            swatch.setFixedSize(self._SHEET_SWATCH_PX, self._SHEET_SWATCH_PX)
            swatch.setStyleSheet(
                f"background:{color_material};border:1px solid #94A3B8;border-radius:3px;"
            )
            lay.addWidget(swatch, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            spacer = QWidget()
            spacer.setFixedSize(self._SHEET_SWATCH_PX, self._SHEET_SWATCH_PX)
            lay.addWidget(spacer, 0, Qt.AlignmentFlag.AlignVCenter)
        lbl = QLabel(str(texto))
        lbl.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(lbl, 1)
        return cell

    def setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        cont = make_card()
        cont_lay = QVBoxLayout(cont)
        cont_lay.setContentsMargins(0, 0, 0, 12)
        cont_lay.setSpacing(0)

        filtros = QFrame()
        filtros.setObjectName("FilterBar")
        filtros.setFrameShape(QFrame.Shape.NoFrame)
        filtros.setFixedHeight(78)
        filtros_lay = QHBoxLayout(filtros)
        filtros_lay.setContentsMargins(20, 6, 20, 4)

        def _filtro(lbl, key, width, values, on_change):
            grp = QWidget()
            gl = QVBoxLayout(grp)
            gl.setContentsMargins(0, 0, 0, 0)
            lbl_w = QLabel(lbl)
            lbl_w.setObjectName("LabelCaption")
            gl.addWidget(lbl_w)
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
        self.btn_remanentes = QPushButton("REMANENTES DISPONIBLES")
        apply_push_button(self.btn_remanentes, COLOR_GRIS_MED, font_size=11)
        self.btn_remanentes.clicked.connect(self.abrir_inventario_remanentes)
        filtros_lay.addWidget(self.btn_remanentes)
        cont_lay.addWidget(filtros)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("StockTabs")
        self.tabs.setDocumentMode(True)
        self.tab_empresa = QWidget()
        self.tab_proveedor = QWidget()
        self.tabs.addTab(self.tab_empresa, "STOCK EMPRESA")
        self.tabs.addTab(self.tab_proveedor, "STOCK PROVEEDOR")
        self.tabs.currentChanged.connect(lambda _i: self.aplicar_filtros())

        for tab_w, lista_attr in [(self.tab_empresa, "lista_empresa"), (self.tab_proveedor, "lista_proveedor")]:
            tl = QVBoxLayout(tab_w)
            tl.setContentsMargins(10, 4, 10, 8)
            tl.setSpacing(6)
            self.crear_encabezados(tab_w)
            scroll = make_scroll()
            inner, inner_lay = make_scroll_content()
            inner_lay.setSpacing(8)
            inner_lay.setContentsMargins(0, 6, 0, 10)
            scroll.setWidget(inner)
            setattr(self, lista_attr, scroll)
            tl.addWidget(scroll, 1)

        cont_lay.addWidget(self.tabs, 1)
        acciones = QHBoxLayout()
        acciones.setSpacing(0)
        self.btn_sync_herinox = QPushButton("Sincronizar con Herinox")
        apply_push_button(self.btn_sync_herinox, COLOR_GRIS_MED, font_size=11)
        self.btn_sync_herinox.clicked.connect(self.sincronizar_con_herinox)
        acciones.addWidget(self.btn_sync_herinox)
        acciones.addSpacing(20)
        self.btn_sync = QPushButton("▣ Ver cambios de sincronizacion")
        apply_push_button(self.btn_sync, COLOR_GRIS_DARK, font_size=11)
        self.btn_sync.clicked.connect(self.mostrar_cambios_sincronizacion)
        acciones.addWidget(self.btn_sync)
        acciones.addStretch()
        cont_lay.addLayout(acciones)
        outer.addWidget(cont)

    def crear_encabezados(self, parent_frame):
        lay = parent_frame.layout()
        h_sheet = QFrame()
        h_sheet.setObjectName("TableHeader")
        h_sheet.setFrameShape(QFrame.Shape.NoFrame)
        h_sheet.setFixedHeight(38)
        grid = QGridLayout(h_sheet)
        grid.setContentsMargins(self._SHEET_GRID_MARGIN_H, 0, self._SHEET_GRID_MARGIN_H, 0)
        self._apply_sheet_grid_columns(grid)
        titles = ["CALIBRE NOMINAL", "THICKNESS", "MATERIAL", "ARGA CODE", "LENGTH", "WIDTH", "LB", "$$/LB", "PRECIO TOTAL", "STOCK"]
        for i, t in enumerate(titles):
            if i == 0:
                grid.addWidget(self._celda_calibre_nominal(t), 0, i)
            else:
                lbl = QLabel(t)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                grid.addWidget(lbl, 0, i)
        lay.insertWidget(0, h_sheet)

    def _nominal_from_row(self, row):
        code = str(row[2]).strip()
        return str(getattr(self.app, "herinox_nominal_by_code", {}).get(code, "N/A") or "N/A").strip()

    def _normalize_thickness(self, value) -> str:
        txt = str(value or "").strip()
        if not txt or txt.lower() == "nan":
            return ""

        clean = txt.replace(",", ".").replace("-", " ").strip()
        compact = clean.replace(" ", "")

        # Formato mixto válido: 1 1/2
        mixed = re.match(r"^(\d+)\s+(\d+)\/(\d+)$", clean)
        if mixed:
            den = int(mixed.group(3))
            if den == 0:
                return ""
            val = int(mixed.group(1)) + (int(mixed.group(2)) / den)
        # Fracción simple válida: 3/16
        elif "/" in compact:
            m = re.match(r"^(\d+)\/(\d+)$", compact)
            if not m:
                return ""
            den = int(m.group(2))
            if den == 0:
                return ""
            val = int(m.group(1)) / den
        else:
            # Descarta texto no numérico (ej. "cero.25").
            if re.search(r"[A-Za-z]", compact):
                return ""
            try:
                val = float(compact)
            except Exception:
                return ""

        # Evita que calibres nominales (10, 11, 14, 16...) entren al filtro thickness.
        if val >= 6 and "." not in compact and "/" not in compact:
            return ""
        if val <= 0:
            return ""

        return f"{val:.4f}".rstrip("0").rstrip(".")

    def al_cambiar_nominal(self, *args):
        self._on_filter_change()

    def al_cambiar_thickness(self, *args):
        self._on_filter_change()

    def al_cambiar_material(self, *args):
        self._on_filter_change()

    def _on_filter_change(self, *args):
        self._actualizar_opciones_dependientes()
        self.aplicar_filtros()

    def _refresh_tabs_text_color(self):
        pass

    def _datos_base_activos(self):
        if self.tabs.currentIndex() == 0:
            return list(self.app.datos_placas_empresa or [])
        return list(self.app.datos_placas_proveedor or [])

    def _row_value(self, row, key: str) -> str:
        if key == "nominal":
            return self._nominal_from_row(row)
        if key == "thickness":
            return self._normalize_thickness(row[0])
        if key == "material":
            txt = str(row[1]).strip()
            return "" if txt.lower() == "nan" else txt
        if key == "arga_code":
            return str(row[2]).strip()
        if key == "stock":
            return self._stock_estado(str(row[8]))
        return ""

    def _row_matches_filters(self, row, filtros, ignore_key: str = "") -> bool:
        for key in ("nominal", "thickness", "material", "arga_code", "stock"):
            if key == ignore_key:
                continue
            v = str(filtros.get(key, "TODOS")).strip()
            if v != "TODOS" and self._row_value(row, key) != v:
                return False
        return True


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

    def _set_arga_code(self, value: str):
        self._selector_set("arga_code", value)

    def _get_arga_code(self) -> str:
        return self._selector_get("arga_code")

    def _set_arga_code_values(self, values):
        self._selector_set_values("arga_code", values)

    def _actualizar_opciones_dependientes(self):
        datos = self._datos_base_activos()
        filtros = {
            "nominal": self._selector_get("nominal"),
            "thickness": self._selector_get("thickness"),
            "material": self._selector_get("material"),
            "arga_code": self._selector_get("arga_code"),
            "stock": self._selector_get("stock"),
        }

        nominales = sorted(
            list(
                {
                    self._row_value(r, "nominal")
                    for r in datos
                    if self._row_value(r, "nominal") and self._row_matches_filters(r, filtros, ignore_key="nominal")
                }
            )
        )
        thicknesses = sorted(
            list(
                {
                    self._row_value(r, "thickness")
                    for r in datos
                    if self._row_value(r, "thickness") and self._row_matches_filters(r, filtros, ignore_key="thickness")
                }
            ),
            key=lambda x: float(x),
        )
        materiales = sorted(
            list(
                {
                    self._row_value(r, "material")
                    for r in datos
                    if self._row_value(r, "material") and self._row_matches_filters(r, filtros, ignore_key="material")
                }
            )
        )
        codigos = sorted(
            list(
                {
                    self._row_value(r, "arga_code")
                    for r in datos
                    if self._row_value(r, "arga_code") and self._row_matches_filters(r, filtros, ignore_key="arga_code")
                }
            )
        )
        stock_orden = ["DISPONIBLE", "NO DISPONIBLE", "NO EXISTENTE"]
        stock_presentes = [
            s for s in stock_orden
            if any(self._row_value(r, "stock") == s and self._row_matches_filters(r, filtros, ignore_key="stock") for r in datos)
        ]

        self._selector_set_values("nominal", nominales)
        self._selector_set_values("thickness", thicknesses)
        self._selector_set_values("material", materiales)
        self._set_arga_code_values(codigos)
        self._selector_set_values("stock", stock_presentes)

    def aplicar_filtros(self, *args):
        self._refresh_tabs_text_color()
        if self.tabs.currentIndex() == 0:
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
            row = QFrame()
            row.setObjectName("SheetRow")
            row.setFrameShape(QFrame.Shape.NoFrame)
            row.setFixedHeight(54)
            row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            row_lay = QGridLayout(row)
            row_lay.setContentsMargins(self._SHEET_GRID_MARGIN_H, 8, self._SHEET_GRID_MARGIN_H, 8)
            self._apply_sheet_grid_columns(row_lay)
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
            mat_txt = str(fila[1] if str(fila[1]) != "nan" else "").strip()
            color_mat = paleta_material(mat_txt).fill
            for i in range(10):
                if i == 0:
                    row_lay.addWidget(
                        self._celda_calibre_nominal(valores_mostrar[i], color_mat),
                        0,
                        i,
                    )
                elif i == 8:
                    lbl = QLabel(valores_mostrar[i])
                    lbl.setStyleSheet("color:#2563EB;font-weight:700;")
                    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    row_lay.addWidget(lbl, 0, i)
                elif i == 9:
                    estado = self._stock_estado(valores_mostrar[i])
                    c = "#16A34A" if estado == "DISPONIBLE" else ("#CA8A04" if estado == "NO DISPONIBLE" else "#DC2626")
                    lbl = QLabel(estado)
                    lbl.setStyleSheet(f"color:{c};font-weight:700;")
                    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    row_lay.addWidget(lbl, 0, i)
                else:
                    lbl = QLabel(valores_mostrar[i])
                    lbl.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};")
                    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    row_lay.addWidget(lbl, 0, i)
            scroll_add_widget(lista_activa, row)

    def actualizar_inventario(self):
        self.app.datos_placas_empresa, self.app.datos_placas_proveedor = self.app.plates_manager.obtener_datos_placas_divididos()
        
        if not self.app.datos_placas_empresa and not self.app.datos_placas_proveedor: return
        
        datos_totales = self.app.datos_placas_empresa + self.app.datos_placas_proveedor
        nominales = sorted(list(set(self._nominal_from_row(row) for row in datos_totales if self._nominal_from_row(row))))
        calibres = sorted(
            list(
                set(
                    self._normalize_thickness(row[0])
                    for row in datos_totales
                    if self._normalize_thickness(row[0])
                )
            ),
            key=lambda x: float(x),
        )
        materiales = sorted(list(set(str(row[1]).strip() for row in datos_totales if str(row[1]).strip().lower() != "nan")))
        codigos = sorted(list(set(str(row[2]).strip() for row in datos_totales if str(row[2]).strip())))
        
        self._selector_set_values("nominal", nominales)
        self._selector_set_values("thickness", calibres)
        self._selector_set_values("material", materiales)
        self._set_arga_code_values(codigos)
        self._selector_set_values("stock", ["TODOS", "DISPONIBLE", "NO DISPONIBLE", "NO EXISTENTE"])
        self._selector_set_values("precio", ["TODOS", "MENOR PRECIO", "MAYOR PRECIO"])
        self._actualizar_opciones_dependientes()
        self.aplicar_filtros()

    @staticmethod
    def _stock_estado(valor: str) -> str:
        txt = str(valor or "").strip().upper()
        if txt in {"DISPONIBLE", "NO DISPONIBLE", "NO EXISTENTE"}:
            return txt
        return "NO EXISTENTE"

    def sincronizar_con_herinox(self):
        self.btn_sync_herinox.setEnabled(False); self.btn_sync_herinox.setText("Sincronizando...")
        QApplication.processEvents()
        try:
            resultado = self.app.plates_manager.sincronizar_desde_react_herinox()
            self.app.ultimo_resultado_sync_herinox = resultado
            self.app.herinox_tc_dof = float(getattr(resultado, "dof_rate", 18.50) or 18.50)
            self.app.herinox_tc_fuente = str(getattr(resultado, "dof_source", "FALLBACK") or "FALLBACK")
            self.app.herinox_nominal_by_code = dict(getattr(resultado, "nominal_by_code", {}) or {})
            self.actualizar_inventario()

            if resultado.ok:
                QMessageBox.information(self, 
                    "Sync Herinox completada",
                    (
                        f"Origen: {resultado.source}\n"
                        f"TC DOF: {resultado.dof_rate:,.4f} ({resultado.dof_source})\n"
                        f"Hojas revisadas: {resultado.sheet_count}\n"
                        f"Codigos coincidentes: {resultado.matched_codes}\n"
                        f"Filas actualizadas: {resultado.updated_rows}"
                    ),
                )
            else:
                QMessageBox.warning(self, 
                    "Sync Herinox omitida",
                    (
                        f"{resultado.message}\n\n"
                        f"Config persistente: {config.HERINOX_SYNC_SETTINGS_FILE}"
                    ),
                )
        except Exception as e:
            QMessageBox.critical(self, "Error en Sync Herinox", str(e))
        finally:
            self.btn_sync_herinox.setEnabled(True); self.btn_sync_herinox.setText("Sincronizar con Herinox")

    def mostrar_cambios_sincronizacion(self):
        resultado = getattr(self.app, "ultimo_resultado_sync_herinox", None)
        if resultado is None:
            QMessageBox.information(self, "Sin datos", "Todavia no hay una sincronizacion registrada en esta sesion.")
            return

        # Intentamos primero visor Qt para render más fluido en resize/move/minimize.
        if self._abrir_viewer_qt(resultado):
            return

        if self._last_qt_viewer_error:
            QMessageBox.warning(self, 
                "Visor Qt no disponible",
                f"No se pudo abrir el visor Qt.\n\nDetalle:\n{self._last_qt_viewer_error}\n\nSe abrira visor compatible (Tk).",
            )

        self._mostrar_cambios_sincronizacion_qt_inline(resultado)

    def _abrir_viewer_qt(self, resultado) -> bool:
        self._last_qt_viewer_error = ""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            config.ruta_recurso(os.path.join("modules", "herinox_sync_qt_viewer.py")),
            os.path.join(base_dir, "modules", "herinox_sync_qt_viewer.py"),
        ]
        viewer_script = ""
        for p in candidates:
            if p and os.path.exists(p):
                viewer_script = p
                break
        if not viewer_script:
            self._last_qt_viewer_error = "No se encontro modules/herinox_sync_qt_viewer.py"
            return False

        qt_python = self._resolver_python_con_pyside6()
        if not qt_python:
            self._last_qt_viewer_error = (
                "Ningun interprete con PySide6 disponible.\n"
                "Instala PySide6 en el Python que ejecuta Arga Nesting Suite."
            )
            return False

        payload = {
            "ok": bool(getattr(resultado, "ok", False)),
            "updated_rows": int(getattr(resultado, "updated_rows", 0) or 0),
            "matched_codes": int(getattr(resultado, "matched_codes", 0) or 0),
            "sheet_count": int(getattr(resultado, "sheet_count", 0) or 0),
            "source": str(getattr(resultado, "source", "none") or "none"),
            "dof_rate": float(getattr(resultado, "dof_rate", 18.5) or 18.5),
            "dof_source": str(getattr(resultado, "dof_source", "FALLBACK") or "FALLBACK"),
            "message": str(getattr(resultado, "message", "") or ""),
            "updated_items": list(getattr(resultado, "updated_items", []) or []),
        }

        try:
            fd, json_path = tempfile.mkstemp(prefix="herinox_sync_", suffix=".json")
            os.close(fd)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)

            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.Popen(
                [qt_python, viewer_script, json_path],
                cwd=os.path.dirname(viewer_script),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creationflags,
            )

            # Si Qt falla al arrancar (ej. falta dependencia), hacemos fallback inmediato a Tk.
            time.sleep(0.35)
            if proc.poll() is not None:
                out, err = proc.communicate()
                detalle = (err or out or "").strip()
                if len(detalle) > 500:
                    detalle = detalle[:500] + "..."
                self._last_qt_viewer_error = (
                    detalle
                    or f"Proceso Qt finalizo con code {proc.returncode}. Python usado: {qt_python}"
                )
                return False
            return True
        except Exception as e:
            self._last_qt_viewer_error = str(e)
            return False

    def _resolver_python_con_pyside6(self):
        candidatos = []
        if sys.executable:
            candidatos.append(sys.executable)

        base_dir = os.path.dirname(os.path.abspath(__file__))
        venv_python = os.path.join(base_dir, ".venv", "Scripts", "python.exe")
        if os.path.exists(venv_python):
            candidatos.append(venv_python)

        user_profile = os.environ.get("USERPROFILE", "")
        if user_profile:
            alt_python = os.path.join(
                user_profile, "AppData", "Local", "Python", "pythoncore-3.14-64", "python.exe"
            )
            if os.path.exists(alt_python):
                candidatos.append(alt_python)

        path_python = shutil.which("python")
        if path_python:
            candidatos.append(path_python)
        path_py = shutil.which("py")
        if path_py:
            candidatos.append(path_py)

        # Preservar orden y quitar duplicados.
        vistos = set()
        candidatos_unicos = []
        for c in candidatos:
            key = str(c).lower()
            if key in vistos:
                continue
            vistos.add(key)
            candidatos_unicos.append(c)

        for exe in candidatos_unicos:
            try:
                proc = subprocess.run(
                    [exe, "-c", "import PySide6;print('OK')"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                )
                if proc.returncode == 0:
                    return exe
            except Exception:
                continue
        return None

    def _mostrar_cambios_sincronizacion_qt_inline(self, resultado):
        dlg = QDialog(self)
        dlg.setWindowTitle("Cambios de sincronización Herinox")
        dlg.resize(1120, 680)
        dlg.setStyleSheet(surface_dialog_stylesheet())
        lay = QVBoxLayout(dlg)
        tit = QLabel("ULTIMA SINCRONIZACION DE PLACAS")
        tit.setStyleSheet(f"font-weight:700;font-size:18px;color:{COLOR_TEXTO_TITULO};")
        lay.addWidget(tit)
        resumen = (
            f"Coincidencias: {getattr(resultado, 'matched_codes', 0)} | "
            f"Filas actualizadas: {getattr(resultado, 'updated_rows', 0)}"
        )
        lay.addWidget(QLabel(resumen))
        if getattr(resultado, "message", ""):
            det = QLabel(f"Detalle: {resultado.message}")
            det.setWordWrap(True)
            det.setStyleSheet(f"color:{COLOR_GRIS_MED};")
            lay.addWidget(det)

        items = list(getattr(resultado, "updated_items", []) or [])
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        if not items:
            inner_lay.addWidget(QLabel("No hubo cambios de campos en placas para mostrar."))
        else:
            parametros = ["Thickness", "Material", "Length", "Width", "LB", "MXN", "$$/LB", "Stock"]
            max_rows = 500
            mostrados = items[:max_rows]
            if len(items) > max_rows:
                inner_lay.addWidget(QLabel(f"Mostrando {max_rows} de {len(items)} actualizaciones."))
            for row in mostrados:
                codigo = str(row.get("arga_code", "")).strip() or "SIN_CODIGO"
                sheet = str(row.get("sheet", "")).strip()
                changes = row.get("changes") or []
                fields = row.get("fields") or []
                if not changes and fields:
                    changes = [{"field": f, "before": "-", "after": "-"} for f in fields]
                cambios_map = {str(c.get("field", "")).strip(): c for c in changes}

                card = QFrame()
                card.setObjectName("HerinoxCard")
                card.setStyleSheet(f"QFrame#HerinoxCard{{background:#FBFCFF;border:1px solid {COLOR_BORDE};}}")
                card_lay = QVBoxLayout(card)
                hdr = QHBoxLayout()
                code_lbl = QLabel(f"{codigo}  ({sheet})")
                code_lbl.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
                hdr.addWidget(code_lbl)
                hdr.addStretch()
                cnt = QLabel(f"Cambios: {len(changes)}")
                cnt.setStyleSheet(f"font-weight:700;color:{COLOR_AZUL_ACENTO};")
                hdr.addWidget(cnt)
                card_lay.addLayout(hdr)

                grid = QGridLayout()
                headers = ["CAMPO"] + parametros
                for col, txt_h in enumerate(headers):
                    h = QLabel(txt_h)
                    h.setStyleSheet(f"background:{COLOR_GRIS_MED};color:white;font-weight:700;padding:4px;")
                    h.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    grid.addWidget(h, 0, col)
                for r_i, (row_lbl, key) in enumerate((("ANTES", "before"), ("DESPUÉS", "after")), start=1):
                    rl = QLabel(row_lbl)
                    rl.setStyleSheet("font-weight:700;")
                    grid.addWidget(rl, r_i, 0)
                    for c_i, param in enumerate(parametros, start=1):
                        val = str((cambios_map.get(param) or {}).get(key, "-"))
                        cell = QLabel(val)
                        cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
                        grid.addWidget(cell, r_i, c_i)
                card_lay.addLayout(grid)
                inner_lay.addWidget(card)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        btn = QPushButton("Cerrar")
        apply_push_button(btn, COLOR_GRIS_DARK, font_size=11)
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn)
        dlg.exec()

    def abrir_inventario_remanentes(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Inventario de Remanentes (> 400 in²)")
        dlg.resize(750, 550)
        dlg.setModal(True)
        dlg.setStyleSheet(surface_dialog_stylesheet())
        lay = QVBoxLayout(dlg)
        tit = QLabel("HISTORIAL DE REMANENTES DISPONIBLES")
        tit.setStyleSheet(f"font-weight:700;font-size:18px;color:{COLOR_TEXTO_TITULO};")
        tit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(tit)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        import config as app_config

        ruta_csv = app_config.asegurar_archivo_persistente("inventario_remanentes.csv")
        if not os.path.exists(ruta_csv):
            inner_lay.addWidget(QLabel("No hay remanentes registrados todavía."))
        else:
            try:
                with open(ruta_csv, mode="r", encoding="utf-8") as f:
                    data = list(csv.reader(f))
                if len(data) <= 1:
                    inner_lay.addWidget(QLabel("Inventario vacío."))
                else:
                    for fila in reversed(data[1:]):
                        card = QFrame()
                        card.setObjectName("HerinoxCard")
                        card_lay = QHBoxLayout(card)
                        info = QVBoxLayout()
                        id_lbl = QLabel(f"ID: {fila[1]}")
                        id_lbl.setStyleSheet(f"font-weight:700;color:{COLOR_AZUL_ACCENTO};")
                        info.addWidget(id_lbl)
                        info.addWidget(QLabel(f"{fila[0]}  |  {fila[2]}  •  CAL: {fila[3]}"))
                        card_lay.addLayout(info, 1)
                        area_lbl = QLabel(f"{fila[4]} in²")
                        area_lbl.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
                        card_lay.addWidget(area_lbl)
                        inner_lay.addWidget(card)
            except Exception as e:
                inner_lay.addWidget(QLabel(f"Error al leer base de datos: {e}"))
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        dlg.exec()
