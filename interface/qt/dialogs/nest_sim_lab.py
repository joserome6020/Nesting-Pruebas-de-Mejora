"""Laboratorio de simulación — una placa, DXF reales, referencia visual opcional."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsScene,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from interface.qt.layout_helpers import make_card, make_panel_dark
from interface.qt.nesting_graphics import NestingDrawParams, NestingGraphicsView, compute_fit_rect, populate_nesting_scene
from interface.qt.theme import COLOR_ACENTO, COLOR_GRIS_DARK, apply_push_button, surface_dialog_stylesheet
from modules.nesting_engine.nest_optimization import NEST_MODES
from modules.nesting_engine.sim_lab import (
    SimPieceEntry,
    build_pieces_from_entries,
    inches_to_mm,
    load_scenario_json,
    run_single_sheet_sim,
    save_scenario_json,
    scenario_from_dict,
    scenario_to_dict,
)


def abrir_nest_sim_lab(parent):
    dlg = getattr(parent, "_dlg_nest_sim_lab", None)
    if dlg is not None:
        try:
            dlg.raise_()
            dlg.activateWindow()
            return dlg
        except RuntimeError:
            parent._dlg_nest_sim_lab = None
    dlg = NestSimLabDialog(parent)
    parent._dlg_nest_sim_lab = dlg
    dlg.show()
    return dlg


class NestSimLabDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.app = getattr(parent, "app", parent)
        self._entries: list[SimPieceEntry] = []
        self._last_result = None

        self.setWindowTitle("Lab de simulación — nesting en placa")
        self.setMinimumSize(1280, 780)
        self.resize(1420, 860)
        self.setStyleSheet(surface_dialog_stylesheet())

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        hdr = QLabel(
            "Simula el motor real (C++) en una sola placa. "
            "Geometría desde DXF; las capturas son solo referencia visual para comparar."
        )
        hdr.setWordWrap(True)
        hdr.setObjectName("LabelMuted")
        root.addWidget(hdr)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_center_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([360, 720, 320])
        root.addWidget(splitter, 1)

        self._refresh_piece_table()

    def _build_left_panel(self) -> QWidget:
        card = make_card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        lay.addWidget(self._caption("Placa"))
        row_wh = QHBoxLayout()
        self.spin_w_in = self._spin_inches(96.0)
        self.spin_h_in = self._spin_inches(240.0)
        row_wh.addWidget(QLabel("Ancho (\")"))
        row_wh.addWidget(self.spin_w_in)
        row_wh.addWidget(QLabel("Largo (\")"))
        row_wh.addWidget(self.spin_h_in)
        lay.addLayout(row_wh)

        lay.addWidget(self._caption("Parámetros motor"))
        row_km = QHBoxLayout()
        self.spin_kerf = self._spin_inches(0.2, step=0.01, decimals=3, max_val=1.0)
        self.spin_margin = self._spin_inches(0.15, step=0.01, decimals=3, max_val=2.0)
        row_km.addWidget(QLabel("Kerf"))
        row_km.addWidget(self.spin_kerf)
        row_km.addWidget(QLabel("Margen"))
        row_km.addWidget(self.spin_margin)
        lay.addLayout(row_km)

        self.cmb_corner = QComboBox()
        self.cmb_corner.addItems(
            ["INFERIOR IZQUIERDA", "INFERIOR DERECHA", "SUPERIOR IZQUIERDA", "SUPERIOR DERECHA"]
        )
        lay.addWidget(QLabel("Esquina de anclaje"))
        lay.addWidget(self.cmb_corner)

        self.cmb_opt = QComboBox()
        self.cmb_opt.addItems(["OPTIMIZAR LARGO Y ANCHO", "OPTIMIZAR LARGO", "OPTIMIZAR ANCHO"])
        lay.addWidget(QLabel("Optimización placa"))
        lay.addWidget(self.cmb_opt)

        row_mode = QHBoxLayout()
        self.cmb_mode = QComboBox()
        for key in ("first", "fast", "standard", "max"):
            mc = NEST_MODES[key]["mc_iterations"]
            self.cmb_mode.addItem(f"{key} ({mc} iter)", key)
        self.cmb_mode.setCurrentIndex(2)
        self.spin_mc = QSpinBox()
        self.spin_mc.setRange(1, 50)
        self.spin_mc.setValue(15)
        self.spin_mc.setToolTip("Override manual de iteraciones Monte Carlo")
        row_mode.addWidget(self.cmb_mode, 1)
        row_mode.addWidget(self.spin_mc)
        lay.addWidget(QLabel("Modo / iter. MC"))
        lay.addLayout(row_mode)
        self.cmb_mode.currentIndexChanged.connect(self._on_mode_changed)

        lay.addWidget(self._caption("Componentes (DXF)"))
        self.tbl_pieces = QTableWidget(0, 3)
        self.tbl_pieces.setHorizontalHeaderLabels(["Archivo", "Cant.", "Área mm²"])
        self.tbl_pieces.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tbl_pieces.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_pieces.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_pieces.setMaximumHeight(220)
        lay.addWidget(self.tbl_pieces)

        row_btns = QHBoxLayout()
        btn_add = QPushButton("Agregar DXF")
        btn_folder = QPushButton("Carpeta")
        btn_rm = QPushButton("Quitar")
        apply_push_button(btn_add, COLOR_ACENTO, font_size=11, padding="6px 10px")
        apply_push_button(btn_folder, "#455E75", font_size=11, padding="6px 10px")
        apply_push_button(btn_rm, "#64748B", font_size=11, padding="6px 10px")
        btn_add.clicked.connect(self._add_dxf_files)
        btn_folder.clicked.connect(self._add_dxf_folder)
        btn_rm.clicked.connect(self._remove_selected_pieces)
        row_btns.addWidget(btn_add)
        row_btns.addWidget(btn_folder)
        row_btns.addWidget(btn_rm)
        lay.addLayout(row_btns)

        self.btn_run = QPushButton("EJECUTAR SIMULACIÓN")
        self.btn_run.setFixedHeight(46)
        apply_push_button(self.btn_run, COLOR_GRIS_DARK, font_size=13, padding="8px 14px")
        self.btn_run.clicked.connect(self._run_simulation)
        lay.addWidget(self.btn_run)

        row_io = QHBoxLayout()
        btn_save = QPushButton("Guardar escenario")
        btn_load = QPushButton("Cargar escenario")
        apply_push_button(btn_save, "#334155", font_size=11, padding="6px 10px")
        apply_push_button(btn_load, "#334155", font_size=11, padding="6px 10px")
        btn_save.clicked.connect(self._save_scenario)
        btn_load.clicked.connect(self._load_scenario)
        row_io.addWidget(btn_save)
        row_io.addWidget(btn_load)
        lay.addLayout(row_io)

        self.btn_timeline = QPushButton("TIMELINE (paso a paso)")
        self.btn_timeline.setFixedHeight(40)
        apply_push_button(self.btn_timeline, "#7C3AED", font_size=12, padding="6px 12px")
        self.btn_timeline.clicked.connect(self._open_timeline)
        lay.addWidget(self.btn_timeline)

        lay.addStretch()
        return card

    def _build_center_panel(self) -> QWidget:
        panel = make_panel_dark()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self._caption_dark("Resultado del nesteo"))
        self.scene = QGraphicsScene(self)
        self.view = NestingGraphicsView(self)
        self.view.setScene(self.scene)
        lay.addWidget(self.view, 1)
        self.lbl_canvas_hint = QLabel("Ejecuta una simulación para ver el layout.")
        self.lbl_canvas_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_canvas_hint.setStyleSheet("color:#94A3B8;font-size:12px;")
        lay.addWidget(self.lbl_canvas_hint)
        return panel

    def _build_right_panel(self) -> QWidget:
        card = make_card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        lay.addWidget(self._caption("Captura de referencia"))
        self.lbl_ref = QLabel()
        self.lbl_ref.setMinimumHeight(160)
        self.lbl_ref.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_ref.setStyleSheet(
            f"background:{COLOR_GRIS_DARK};border:1px dashed #94A3B8;border-radius:8px;color:#CBD5E1;"
        )
        self.lbl_ref.setText("Sin imagen")
        lay.addWidget(self.lbl_ref)

        row_img = QHBoxLayout()
        btn_img = QPushButton("Cargar captura…")
        btn_clr = QPushButton("Limpiar")
        apply_push_button(btn_img, "#455E75", font_size=11, padding="6px 10px")
        apply_push_button(btn_clr, "#64748B", font_size=11, padding="6px 10px")
        btn_img.clicked.connect(self._load_ref_image)
        btn_clr.clicked.connect(self._clear_ref_image)
        row_img.addWidget(btn_img)
        row_img.addWidget(btn_clr)
        lay.addLayout(row_img)

        self.txt_notes = QLineEdit()
        self.txt_notes.setPlaceholderText("Notas del escenario (opcional)")
        lay.addWidget(self.txt_notes)

        lay.addWidget(self._caption("Diagnóstico"))
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setPlaceholderText("Log de la corrida…")
        lay.addWidget(self.txt_log, 1)

        self._ref_image_path = ""
        return card

    @staticmethod
    def _caption(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("LabelCaption")
        return lbl

    @staticmethod
    def _caption_dark(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#E2E8F0;font-size:10px;font-weight:700;")
        return lbl

    @staticmethod
    def _spin_inches(value: float, *, step: float = 0.5, decimals: int = 2, max_val: float = 600.0):
        from PySide6.QtWidgets import QDoubleSpinBox

        sp = QDoubleSpinBox()
        sp.setRange(0.0, max_val)
        sp.setDecimals(decimals)
        sp.setSingleStep(step)
        sp.setValue(float(value))
        return sp

    def _on_mode_changed(self):
        key = self.cmb_mode.currentData()
        if key in NEST_MODES:
            self.spin_mc.setValue(int(NEST_MODES[key]["mc_iterations"]))

    def _add_dxf_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Seleccionar DXF",
            "",
            "DXF (*.dxf);;Todos (*.*)",
        )
        for p in paths:
            if p and os.path.isfile(p):
                self._entries.append(SimPieceEntry(ruta=p, qty=1))
        self._refresh_piece_table()

    def _add_dxf_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Carpeta con DXF")
        if not folder:
            return
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".dxf"):
                self._entries.append(SimPieceEntry(ruta=os.path.join(folder, name), qty=1))
        self._refresh_piece_table()

    def _remove_selected_pieces(self):
        rows = sorted({idx.row() for idx in self.tbl_pieces.selectedIndexes()}, reverse=True)
        for r in rows:
            if 0 <= r < len(self._entries):
                del self._entries[r]
        self._refresh_piece_table()

    def _refresh_piece_table(self):
        self.tbl_pieces.setRowCount(len(self._entries))
        for i, ent in enumerate(self._entries):
            nom = os.path.basename(ent.ruta)
            item_name = QTableWidgetItem(nom)
            item_name.setToolTip(ent.ruta)
            self.tbl_pieces.setItem(i, 0, item_name)

            spin = QSpinBox()
            spin.setRange(1, 999)
            spin.setValue(int(ent.qty or 1))
            spin.valueChanged.connect(lambda v, row=i: self._set_qty(row, v))
            self.tbl_pieces.setCellWidget(i, 1, spin)

            area_txt = "—"
            try:
                from modules.nesting_engine.sim_lab import piece_from_dxf

                batch, err = piece_from_dxf(ent.ruta, nombre=ent.display_name(), qty=1)
                if batch and not err:
                    area_txt = f"{float(batch[0].get('area', 0) or 0):,.0f}"
                elif err:
                    area_txt = "ERR"
            except Exception:
                area_txt = "?"
            self.tbl_pieces.setItem(i, 2, QTableWidgetItem(area_txt))

    def _set_qty(self, row: int, value: int):
        if 0 <= row < len(self._entries):
            self._entries[row].qty = int(value)

    def _load_ref_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Captura de referencia",
            "",
            "Imágenes (*.png *.jpg *.jpeg *.webp *.bmp);;Todos (*.*)",
        )
        if not path:
            return
        pix = QPixmap(path)
        if pix.isNull():
            QMessageBox.warning(self, "Imagen", "No se pudo cargar la imagen.")
            return
        self._ref_image_path = path
        scaled = pix.scaled(
            self.lbl_ref.width() - 8,
            200,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.lbl_ref.setPixmap(scaled)
        self.lbl_ref.setText("")

    def _clear_ref_image(self):
        self._ref_image_path = ""
        self.lbl_ref.clear()
        self.lbl_ref.setText("Sin imagen")

    def _collect_params(self) -> dict:
        return {
            "plate_w_in": float(self.spin_w_in.value()),
            "plate_h_in": float(self.spin_h_in.value()),
            "kerf_in": float(self.spin_kerf.value()),
            "margin_in": float(self.spin_margin.value()),
            "corner": self.cmb_corner.currentText(),
            "opt": self.cmb_opt.currentText(),
            "nest_mode": str(self.cmb_mode.currentData() or "standard"),
            "mc_iterations": int(self.spin_mc.value()),
            "ref_image": self._ref_image_path,
            "notes": self.txt_notes.text().strip(),
        }

    def _run_simulation(self):
        params = self._collect_params()
        piezas, errores = build_pieces_from_entries(self._entries)
        if errores:
            QMessageBox.warning(
                self,
                "DXF con errores",
                "Algunos archivos fallaron:\n\n" + "\n".join(errores[:12]),
            )
        if not piezas:
            QMessageBox.information(self, "Sin piezas", "Agrega al menos un DXF válido.")
            return

        os.environ["ARGA_NEST_MODE"] = str(params["nest_mode"])
        result = run_single_sheet_sim(
            piezas,
            w_mm=inches_to_mm(params["plate_w_in"]),
            h_mm=inches_to_mm(params["plate_h_in"]),
            kerf_in=params["kerf_in"],
            margin_in=params["margin_in"],
            corner=params["corner"],
            opt=params["opt"],
            mc_iterations=params["mc_iterations"],
            nest_mode=params["nest_mode"],
        )
        self._last_result = result
        self.txt_log.setPlainText(result.summary_text())

        if not result.hoja or not result.hoja.get("piezas"):
            self.lbl_canvas_hint.setText("Sin piezas colocadas — revisa medidas o geometría.")
            self.scene.clear()
            return

        self.lbl_canvas_hint.setText("")
        draw_params = NestingDrawParams(hoja=result.hoja, clave="SIM_LAB", app=self.app)
        meta = populate_nesting_scene(self.scene, draw_params)
        rect = compute_fit_rect(
            result.hoja,
            meta,
            max(400, self.view.viewport().width()),
            max(300, self.view.viewport().height()),
        )
        if rect:
            self.view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def _save_scenario(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Guardar escenario",
            "",
            "Escenario sim (*.nestsim.json);;JSON (*.json)",
        )
        if not path:
            return
        params = self._collect_params()
        payload = scenario_to_dict(entries=self._entries, **params)
        save_scenario_json(path, payload)
        QMessageBox.information(self, "Guardado", f"Escenario guardado:\n{path}")

    def _load_scenario_file(self, path: str, *, silent: bool = False) -> bool:
        try:
            data = load_scenario_json(path)
            params, entries = scenario_from_dict(data)
        except Exception as exc:
            if not silent:
                QMessageBox.critical(self, "Error", f"No se pudo cargar:\n{exc}")
            return False

        self._entries = entries
        self.spin_w_in.setValue(params["plate_w_in"])
        self.spin_h_in.setValue(params["plate_h_in"])
        self.spin_kerf.setValue(params["kerf_in"])
        self.spin_margin.setValue(params["margin_in"])
        idx_corner = self.cmb_corner.findText(params["corner"])
        if idx_corner >= 0:
            self.cmb_corner.setCurrentIndex(idx_corner)
        idx_opt = self.cmb_opt.findText(params["opt"])
        if idx_opt >= 0:
            self.cmb_opt.setCurrentIndex(idx_opt)
        idx_mode = self.cmb_mode.findData(params["nest_mode"])
        if idx_mode >= 0:
            self.cmb_mode.setCurrentIndex(idx_mode)
        self.spin_mc.setValue(int(params["mc_iterations"]))
        self.txt_notes.setText(params.get("notes") or "")
        ref = str(params.get("ref_image") or "")
        if ref and os.path.isfile(ref):
            self._ref_image_path = ref
            pix = QPixmap(ref)
            if not pix.isNull():
                self.lbl_ref.setPixmap(
                    pix.scaled(280, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
        self._refresh_piece_table()
        if not silent:
            QMessageBox.information(self, "Cargado", f"Escenario cargado ({len(entries)} DXF).")
        return True

    def _load_scenario(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Cargar escenario",
            "",
            "Escenario sim (*.nestsim.json *.json);;Todos (*.*)",
        )
        if not path:
            return
        self._load_scenario_file(path)

    def _open_timeline(self):
        from interface.qt.dialogs.nest_sim_timeline import abrir_reproductor

        params = self._collect_params()
        piezas, errores = build_pieces_from_entries(self._entries)
        if errores:
            QMessageBox.warning(self, "DXF", "\n".join(errores[:8]))
        if not piezas:
            QMessageBox.information(self, "Reproductor", "Agrega DXF primero.")
            return

        import tempfile

        path = os.path.join(tempfile.gettempdir(), "arga_nest_live.nestsim.json")
        save_scenario_json(path, scenario_to_dict(entries=self._entries, **params))
        abrir_reproductor(path, parent=self, mc_iterations=1)
