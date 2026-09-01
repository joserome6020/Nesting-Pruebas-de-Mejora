"""Editor de lote activo — PySide6 nativo."""
from __future__ import annotations

from interface.qt.ui_scale import fit_window

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from interface.qt.theme import apply_push_button, surface_dialog_stylesheet

ARGB_BTN_1 = "#202A36"
ARGB_BTN_2 = "#334659"
ARGB_BTN_3 = "#455E75"
ARGB_BTN_4 = "#708DA9"


class EditorLoteWindow(QDialog):
    def __init__(self, tab_nesting):
        super().__init__(tab_nesting)
        self.tab = tab_nesting
        self.setWindowTitle("Editar lote activo")
        fit_window(self, 1220, 620)
        self.setModal(True)
        self._build_ui()
        self._refrescar_tabla()

    def _build_ui(self):
        self.setStyleSheet(surface_dialog_stylesheet())
        root = QVBoxLayout(self)
        card = QWidget()
        card.setObjectName("HerinoxCard")
        lay = QVBoxLayout(card)

        header = QHBoxLayout()
        self.lbl_titulo = QLabel("EDITOR DE LOTE ACTIVO")
        self.lbl_titulo.setStyleSheet("font-size:20px;font-weight:700;color:#0F172A;")
        header.addWidget(self.lbl_titulo)
        header.addStretch()
        self.lbl_info = QLabel("")
        self.lbl_info.setStyleSheet("font-weight:700;color:#475569;")
        header.addWidget(self.lbl_info)
        lay.addLayout(header)

        ayuda = QLabel(
            "Agregar/Reemplazar DXF: intenta leer nombre/material/calibre/QTY desde la nomenclatura del archivo. "
            "Si falta algún dato, usa el renglón seleccionado como respaldo."
        )
        ayuda.setWordWrap(True)
        ayuda.setStyleSheet("color:#64748B;")
        lay.addWidget(ayuda)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["#", "PIEZA / REF", "MATERIAL", "QTY", "CALIBRE", "ESTADO", "RUTA DXF"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)

        footer = QHBoxLayout()
        self.btn_agregar = QPushButton("AGREGAR DXF")
        apply_push_button(self.btn_agregar, ARGB_BTN_2, font_size=11)
        self.btn_agregar.clicked.connect(self._agregar_dxfs)
        footer.addWidget(self.btn_agregar)

        self.btn_eliminar = QPushButton("ELIMINAR")
        apply_push_button(self.btn_eliminar, ARGB_BTN_1, font_size=11)
        self.btn_eliminar.clicked.connect(self._eliminar_seleccionados)
        footer.addWidget(self.btn_eliminar)

        self.btn_reemplazar = QPushButton("REEMPLAZAR DXF")
        apply_push_button(self.btn_reemplazar, ARGB_BTN_3, font_size=11)
        self.btn_reemplazar.clicked.connect(self._reemplazar_dxf)
        footer.addWidget(self.btn_reemplazar)

        footer.addStretch()
        self.btn_cerrar = QPushButton("CERRAR")
        apply_push_button(self.btn_cerrar, "#FFFFFF", font_size=11)
        self.btn_cerrar.clicked.connect(self.accept)
        footer.addWidget(self.btn_cerrar)

        self.btn_renestear = QPushButton("RENESTEAR LOTE")
        apply_push_button(self.btn_renestear, "#1E293B", font_size=11)
        self.btn_renestear.clicked.connect(self._renestear_lote)
        footer.addWidget(self.btn_renestear)
        lay.addLayout(footer)
        root.addWidget(card)

    def _datos_actuales(self):
        return list(getattr(self.tab.app, "editable_inputs_actuales", []) or [])

    def _refresh_info(self):
        datos = self._datos_actuales()
        idx = int(getattr(self.tab, "lote_actual_idx", 0) or 0) + 1
        self.lbl_info.setText(f"Work Order seleccionado: {idx} | Piezas editables: {len(datos)}")

    def _refrescar_tabla(self):
        self.table.setRowCount(0)
        datos = self._datos_actuales()
        for i, fila in enumerate(datos, start=1):
            nombre, material, qty, calibre, estado, ruta = fila
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, val in enumerate([i, nombre, material, qty, calibre, estado, ruta]):
                item = QTableWidgetItem(str(val))
                if col == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
        self._refresh_info()

    def _indices_seleccionados(self):
        return sorted({idx.row() for idx in self.table.selectionModel().selectedRows()})

    def _fila_base_seleccionada(self):
        indices = self._indices_seleccionados()
        datos = self._datos_actuales()
        if indices:
            idx = indices[0]
            if 0 <= idx < len(datos):
                return datos[idx]
        return datos[0] if datos else None

    def _agregar_dxfs(self):
        rutas, _ = QFileDialog.getOpenFileNames(
            self,
            "Seleccionar DXF para agregar al lote activo",
            "",
            "Archivos DXF (*.dxf);;Todos (*.*)",
        )
        if not rutas:
            return
        try:
            self.tab.agregar_dxfs_a_lote(list(rutas), fila_base=self._fila_base_seleccionada())
            self._refrescar_tabla()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudieron agregar los DXF:\n{e}")

    def _eliminar_seleccionados(self):
        indices = self._indices_seleccionados()
        if not indices:
            QMessageBox.warning(self, "Atención", "Selecciona una o más piezas para eliminar.")
            return
        ok = QMessageBox.question(
            self,
            "Confirmar eliminación",
            f"¿Eliminar {len(indices)} pieza(s) del lote activo?",
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            self.tab.eliminar_piezas_de_lote(indices)
            self._refrescar_tabla()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudieron eliminar las piezas:\n{e}")

    def _reemplazar_dxf(self):
        indices = self._indices_seleccionados()
        if len(indices) != 1:
            QMessageBox.warning(self, "Atención", "Selecciona exactamente una pieza para reemplazar su DXF.")
            return
        nueva_ruta, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar nuevo DXF para reemplazo",
            "",
            "Archivos DXF (*.dxf);;Todos (*.*)",
        )
        if not nueva_ruta:
            return
        try:
            self.tab.reemplazar_dxf_de_lote(indices[0], nueva_ruta)
            self._refrescar_tabla()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo reemplazar el DXF:\n{e}")

    def _renestear_lote(self):
        datos = self._datos_actuales()
        if not datos:
            QMessageBox.warning(self, "Atención", "El lote activo no tiene piezas para renestear.")
            return
        ok = QMessageBox.question(
            self,
            "Confirmar renesteo",
            "Se renesteará únicamente el lote activo.\n\n¿Deseas continuar?",
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            self.tab.renestear_lote_actual()
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo iniciar el renesteo:\n{e}")


def abrir_editor_lote(tab_nesting):
    dlg = EditorLoteWindow(tab_nesting)
    dlg.exec()
    return dlg
