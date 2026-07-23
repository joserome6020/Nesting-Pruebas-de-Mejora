"""Lab Qt: carga .arganest / nest activo → placas → preview actual vs simulación (4 motores)."""
from __future__ import annotations

import copy
import os
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGraphicsScene,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from interface.qt.layout_helpers import make_card, make_panel_dark, make_scroll, make_scroll_content
from interface.qt.nesting_graphics import (
    NestingDrawParams,
    NestLabGraphicsView,
    compute_fit_rect,
    populate_nesting_scene,
)
from interface.qt.theme import (
    COLOR_GRIS_DARK,
    COLOR_TEXTO_TITULO,
    apply_push_button,
    surface_dialog_stylesheet,
)
from interface.utils_nesting import format_clave_calibre_display
from modules.nesting_engine.efficiency_metrics import actualizar_eficiencias_hoja, formatear_eficiencias_placa
from modules.nesting_engine.engine_registry import is_engine_ready, list_engine_metas
from modules.nesting_engine.manager import enriquecer_hoja_export_desde_partes
from modules.nesting_engine.sim_lab import (
    dims_placa_desde_hoja,
    hoja_con_nombres_agrupados,
    listar_placas_desde_resultados,
    mm_to_inches,
    params_motor_desde_hoja,
    piezas_pack_desde_hoja,
    piezas_pack_limpias,
    run_plate_sim,
)


def _enriquecer_resultados_rutas(resultados: dict, datos_partes) -> int:
    """Completa pz['ruta'] en todas las hojas usando PARTS / datos_partes."""
    if not isinstance(resultados, dict) or not datos_partes:
        return 0
    total = 0
    for clave, info in resultados.items():
        if not isinstance(info, dict):
            continue
        for hoja in info.get("hojas") or []:
            if isinstance(hoja, dict):
                total += enriquecer_hoja_export_desde_partes(hoja, str(clave), datos_partes)
    return total


def _resultados_desde_payload(payload: dict) -> dict:
    """Extrae mapa clave→hojas desde workspace (.arganest)."""
    if not isinstance(payload, dict):
        return {}
    res = payload.get("resultados_nesting") or payload.get("resultados")
    if isinstance(res, dict) and res:
        return res
    multilote = payload.get("resultados_multilote") or []
    merged: dict = {}
    for lote in multilote:
        if not isinstance(lote, dict):
            continue
        data = lote.get("data")
        if not isinstance(data, dict):
            continue
        for clave, info in data.items():
            if not isinstance(info, dict):
                continue
            dst = merged.setdefault(clave, {"hojas": []})
            for hoja in info.get("hojas") or []:
                if isinstance(hoja, dict):
                    dst["hojas"].append(hoja)
    return merged


def abrir_nest_sim_lab(parent):
    dlg = getattr(parent, "_dlg_nest_sim_lab", None)
    if dlg is not None:
        try:
            dlg.raise_()
            dlg.activateWindow()
            return dlg
        except RuntimeError:
            parent._dlg_nest_sim_lab = None
    dlg = NestSimCompareDialog(parent)
    parent._dlg_nest_sim_lab = dlg
    dlg.showMaximized()
    return dlg


class NestSimCompareDialog(QDialog):
    """Comparador: nest actual (placa) vs re-nesteo con motor seleccionado."""

    _sig_sim_done = Signal(object)
    _sig_sim_err = Signal(str)
    _sig_load_done = Signal(object)
    _sig_load_err = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.app = getattr(parent, "app", parent)
        self._tab = parent
        self._resultados: dict = {}
        self._placas: list[dict] = []
        self._hoja_actual: dict | None = None
        self._hoja_sim: dict | None = None
        self._timeline = None
        self._ruta_arganest = ""
        self._datos_partes: list = []
        self._placa_btns: list[QPushButton] = []
        self._placa_sel = None
        self._sim_t0 = 0.0
        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(1000)
        self._heartbeat.timeout.connect(self._on_sim_heartbeat)

        self.setWindowTitle("LAB · Comparar placa (actual vs motor)")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.setMinimumSize(1100, 700)
        self.resize(1480, 880)
        self.setStyleSheet(surface_dialog_stylesheet())

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        hdr = QLabel(
            "Carga un .arganest (o usa el nest abierto), elige calibre/placa y motor, "
            "y compara el acomodo actual con el re-nesteo de esa sola placa."
        )
        hdr.setWordWrap(True)
        hdr.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};font-size:12px;")
        root.addWidget(hdr)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_center())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 1100])
        root.addWidget(splitter, 1)

        self._sig_sim_done.connect(self._on_sim_done)
        self._sig_sim_err.connect(self._on_sim_err)
        self._sig_load_done.connect(self._on_load_done)
        self._sig_load_err.connect(self._on_load_err)

        self._try_load_from_app()

    def _build_left(self) -> QWidget:
        card = make_card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        lay.addWidget(self._cap("Fuente"))
        row = QHBoxLayout()
        self.btn_abrir = QPushButton("Abrir .arganest…")
        self.btn_usar = QPushButton("Usar nest abierto")
        apply_push_button(self.btn_abrir, COLOR_GRIS_DARK, font_size=11, padding="6px 10px")
        apply_push_button(self.btn_usar, COLOR_GRIS_DARK, font_size=11, padding="6px 10px")
        self.btn_abrir.clicked.connect(self._abrir_arganest)
        self.btn_usar.clicked.connect(self._try_load_from_app)
        row.addWidget(self.btn_abrir)
        row.addWidget(self.btn_usar)
        lay.addLayout(row)

        self.lbl_fuente = QLabel("Sin nest cargado")
        self.lbl_fuente.setWordWrap(True)
        self.lbl_fuente.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};font-size:12px;")
        lay.addWidget(self.lbl_fuente)

        lay.addWidget(self._cap("Calibre / material"))
        self.lst_calibre = QListWidget()
        self.lst_calibre.setMaximumHeight(160)
        self.lst_calibre.setStyleSheet(
            "QListWidget{background:#FFFFFF;border:1px solid #D8DFEB;border-radius:8px;"
            "color:#0F172A;padding:4px;}"
            "QListWidget::item{padding:6px 8px;}"
            "QListWidget::item:selected{background:#E3EBFC;color:#0F172A;"
            "border:1px solid #90A8D6;border-radius:4px;}"
        )
        self.lst_calibre.currentRowChanged.connect(self._on_calibre_changed)
        lay.addWidget(self.lst_calibre)

        lay.addWidget(self._cap("Placas"))
        self.scroll_placas = make_scroll()
        self._placas_inner, self._placas_layout = make_scroll_content()
        self._placas_layout.setSpacing(2)
        self.scroll_placas.setWidget(self._placas_inner)
        self.scroll_placas.setMinimumHeight(180)
        lay.addWidget(self.scroll_placas, 1)

        self.lbl_placa_info = QLabel("-")
        self.lbl_placa_info.setWordWrap(True)
        self.lbl_placa_info.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};font-size:12px;font-weight:600;")
        lay.addWidget(self.lbl_placa_info)

        lay.addWidget(self._cap("Motor de nesteo"))
        self.cmb_motor = QComboBox()
        for meta in list_engine_metas():
            ready = is_engine_ready(meta.engine_id)
            label = meta.display_name if ready else f"{meta.display_name} (no listo)"
            self.cmb_motor.addItem(label, meta.engine_id)
            idx = self.cmb_motor.count() - 1
            if not ready:
                self.cmb_motor.model().item(idx).setEnabled(False)
        # Preferir primer listo
        for i in range(self.cmb_motor.count()):
            eid = self.cmb_motor.itemData(i)
            if is_engine_ready(eid):
                self.cmb_motor.setCurrentIndex(i)
                break
        lay.addWidget(self.cmb_motor)

        self.btn_simular = QPushButton("Simular placa con motor")
        apply_push_button(self.btn_simular, COLOR_GRIS_DARK, font_size=12, padding="10px 12px")
        self.btn_simular.clicked.connect(self._simular)
        lay.addWidget(self.btn_simular)

        self.btn_detener_sim = QPushButton("Detener (aceptar mejor)")
        apply_push_button(self.btn_detener_sim, COLOR_GRIS_DARK, font_size=11, padding="8px 12px")
        self.btn_detener_sim.setEnabled(False)
        self.btn_detener_sim.setToolTip(
            "SVGNest Ultra / NestFab: deja de optimizar y conserva el mejor layout."
        )
        self.btn_detener_sim.clicked.connect(self._detener_sim)
        lay.addWidget(self.btn_detener_sim)
        self._sim_cancel = threading.Event()

        self.btn_timeline = QPushButton("Ver paso a paso (timeline)")
        apply_push_button(self.btn_timeline, COLOR_GRIS_DARK, font_size=11, padding="8px 10px")
        self.btn_timeline.setEnabled(False)
        self.btn_timeline.clicked.connect(self._abrir_timeline)
        lay.addWidget(self.btn_timeline)

        self.btn_fit = QPushButton("Ajustar vistas")
        apply_push_button(self.btn_fit, COLOR_GRIS_DARK, font_size=11, padding="6px 10px")
        self.btn_fit.clicked.connect(self._ajustar_vistas)
        lay.addWidget(self.btn_fit)

        lay.addWidget(self._cap("Log"))
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(140)
        lay.addWidget(self.txt_log)
        return card

    def _build_center(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.tabs_vista = QTabWidget()
        self.tabs_vista.setObjectName("LabNestTabs")
        self.tabs_vista.setDocumentMode(True)
        self.tabs_vista.setStyleSheet(
            "QTabWidget::pane{"
            "  border:1px solid #D8DFEB;border-radius:10px;background:#FFFFFF;"
            "  top:-1px;}"
            "QTabBar::tab{"
            "  background:#FFFFFF;color:#475569;font-weight:700;font-size:12px;"
            "  padding:10px 18px;margin-right:4px;border:1px solid #D8DFEB;"
            "  border-bottom:none;border-top-left-radius:8px;border-top-right-radius:8px;}"
            "QTabBar::tab:selected{"
            "  background:#1E293B;color:#FFFFFF;}"
            "QTabBar::tab:hover:!selected{"
            "  background:#E3EBFC;color:#174493;}"
        )

        # —— Pestaña ACTUAL ——
        page_act = QWidget()
        tl = QVBoxLayout(page_act)
        tl.setContentsMargins(10, 10, 10, 10)
        tl.setSpacing(6)
        self.lbl_act = QLabel("Nesteo actual (placa del .arganest)")
        self.lbl_act.setStyleSheet(
            f"color:{COLOR_TEXTO_TITULO};font-weight:700;font-size:13px;background:transparent;"
        )
        tl.addWidget(self.lbl_act)
        panel_act = make_panel_dark()
        pl = QVBoxLayout(panel_act)
        pl.setContentsMargins(0, 0, 0, 0)
        self.scene_act = QGraphicsScene(self)
        self.view_act = NestLabGraphicsView(self)
        self.view_act.setScene(self.scene_act)
        pl.addWidget(self.view_act)
        tl.addWidget(panel_act, 1)
        self.lbl_efi_act = QLabel("DIR -")
        self.lbl_efi_act.setStyleSheet(
            f"color:{COLOR_TEXTO_TITULO};font-weight:600;font-size:12px;background:transparent;"
        )
        tl.addWidget(self.lbl_efi_act)

        # —— Pestaña SIMULACIÓN ——
        page_sim = QWidget()
        bl = QVBoxLayout(page_sim)
        bl.setContentsMargins(10, 10, 10, 10)
        bl.setSpacing(6)
        self.lbl_sim = QLabel("Simulación (motor)")
        self.lbl_sim.setStyleSheet(
            f"color:{COLOR_TEXTO_TITULO};font-weight:700;font-size:13px;background:transparent;"
        )
        bl.addWidget(self.lbl_sim)
        panel_sim = make_panel_dark()
        ps = QVBoxLayout(panel_sim)
        ps.setContentsMargins(0, 0, 0, 0)
        self.scene_sim = QGraphicsScene(self)
        self.view_sim = NestLabGraphicsView(self)
        self.view_sim.setScene(self.scene_sim)
        ps.addWidget(self.view_sim)
        bl.addWidget(panel_sim, 1)
        self.lbl_efi_sim = QLabel("DIR -")
        self.lbl_efi_sim.setStyleSheet(
            f"color:{COLOR_TEXTO_TITULO};font-weight:600;font-size:12px;background:transparent;"
        )
        bl.addWidget(self.lbl_efi_sim)

        self.tabs_vista.addTab(page_act, "ACTUAL")
        self.tabs_vista.addTab(page_sim, "SIMULACIÓN")
        self.tabs_vista.currentChanged.connect(self._on_tab_vista)
        lay.addWidget(self.tabs_vista, 1)
        return wrap

    def _on_tab_vista(self, idx: int):
        # Reajustar la vista visible al cambiar de pestaña (viewport ya con tamaño real).
        if idx == 0 and self._hoja_actual:
            self._paint(
                self.scene_act,
                self.view_act,
                self._hoja_actual,
                clave=str((self._placa_sel or {}).get("clave") or "LAB"),
            )
        elif idx == 1 and self._hoja_sim:
            self._paint(self.scene_sim, self.view_sim, self._hoja_sim, clave="SIM")

    @staticmethod
    def _cap(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};font-size:11px;font-weight:700;")
        return lbl

    def _log(self, msg: str):
        self.txt_log.append(msg)

    def _ss_btn_placa(self, *, seleccionada: bool) -> str:
        if seleccionada:
            bg, fg, hover, border = "#FFFFFF", COLOR_TEXTO_TITULO, "#F1F5F9", "#CBD5E1"
        else:
            bg, fg, hover, border = "#323741", "#FFFFFF", "#3F4854", "#1E293B"
        return (
            f"QPushButton{{background:{bg};color:{fg};border:1px solid {border};"
            f"border-radius:8px;padding:8px 10px;font-weight:600;font-size:12px;text-align:left;}}"
            f"QPushButton:hover{{background:{hover};}}"
        )

    def _try_load_from_app(self):
        res = getattr(self.app, "resultados_nesting", None) or {}
        if not res:
            self.lbl_fuente.setText("No hay nest abierto. Usa «Abrir .arganest…».")
            return
        self._datos_partes = list(
            getattr(self.app, "editable_inputs_actuales", None)
            or getattr(self.app, "datos_partes_actuales", None)
            or []
        )
        res_copy = copy.deepcopy(res)
        n_rutas = _enriquecer_resultados_rutas(res_copy, self._datos_partes)
        self._apply_resultados(res_copy, fuente="Nest abierto en ARGA")
        if n_rutas:
            self._log(f"Rutas DXF enlazadas desde PARTS: {n_rutas}.")

    def _abrir_arganest(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Abrir nest",
            "",
            "Nest ARGA (*.arganest *.navanest);;Todos (*.*)",
        )
        if not path:
            return
        self.btn_abrir.setEnabled(False)
        self.btn_usar.setEnabled(False)
        self._log(f"Cargando {os.path.basename(path)}…")

        def worker():
            try:
                from interface.nesting_workspace import (
                    cargar_workspace_desde_archivo,
                    enlazar_rutas_en_payload,
                )

                payload = cargar_workspace_desde_archivo(path)
                try:
                    enlazar_rutas_en_payload(payload)
                except Exception:
                    pass
                res = _resultados_desde_payload(payload)
                if not res:
                    raise RuntimeError(
                        "El archivo no trae resultados_nesting / resultados_multilote."
                    )
                datos = list(payload.get("datos_partes_actuales") or [])
                if not datos:
                    for lote in payload.get("editable_inputs_by_lote") or []:
                        if lote:
                            datos = list(lote)
                            break
                # enlazar_rutas solo toca multilote; resultados_nesting puede venir sin ruta.
                n_rutas = _enriquecer_resultados_rutas(res, datos)
                self._sig_load_done.emit(
                    {
                        "path": path,
                        "res": copy.deepcopy(res),
                        "datos_partes": copy.deepcopy(datos),
                        "n_rutas": n_rutas,
                    }
                )
            except Exception as exc:
                self._sig_load_err.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_load_done(self, data: dict):
        self.btn_abrir.setEnabled(True)
        self.btn_usar.setEnabled(True)
        self._ruta_arganest = str(data.get("path") or "")
        self._datos_partes = list(data.get("datos_partes") or [])
        self._apply_resultados(
            data.get("res") or {},
            fuente=f"Archivo: {os.path.basename(self._ruta_arganest)}",
        )
        n_rutas = int(data.get("n_rutas") or 0)
        if n_rutas:
            self._log(f"Rutas DXF enlazadas desde PARTS: {n_rutas}.")
        elif not self._datos_partes:
            self._log("WARN: el .arganest no trae PARTS → re-nesteo usa polígonos sin interiores.")

    def _on_load_err(self, msg: str):
        self.btn_abrir.setEnabled(True)
        self.btn_usar.setEnabled(True)
        QMessageBox.critical(self, "LAB", f"No se pudo cargar el nest:\n{msg}")

    def _apply_resultados(self, resultados: dict, *, fuente: str):
        self._resultados = resultados or {}
        self._placas = listar_placas_desde_resultados(self._resultados)
        self.lbl_fuente.setText(fuente)
        self.lst_calibre.blockSignals(True)
        self.lst_calibre.clear()
        self.lst_calibre.addItem(QListWidgetItem("(todas)"))
        self.lst_calibre.item(0).setData(Qt.ItemDataRole.UserRole, "")
        claves = sorted({p["clave"] for p in self._placas})
        for c in claves:
            # Misma clave cruda que el nest (como en la lista de materiales)
            it = QListWidgetItem(str(c))
            it.setData(Qt.ItemDataRole.UserRole, c)
            it.setToolTip(format_clave_calibre_display(c) or c)
            self.lst_calibre.addItem(it)
        self.lst_calibre.setCurrentRow(0)
        self.lst_calibre.blockSignals(False)
        self._reload_lista_placas()
        self._log(f"{fuente} · {len(self._placas)} placa(s) madre.")

    def _filtro_clave(self) -> str:
        item = self.lst_calibre.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _reload_lista_placas(self):
        while self._placas_layout.count():
            item = self._placas_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._placa_btns.clear()
        self._placa_sel = None
        filtro = self._filtro_clave()

        from collections import OrderedDict

        by_clave: OrderedDict[str, list] = OrderedDict()
        for p in self._placas:
            if filtro and p["clave"] != filtro:
                continue
            by_clave.setdefault(p["clave"], []).append(p)

        for clave, placas in by_clave.items():
            if not filtro:
                header = QLabel(str(clave))
                header.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};padding-top:8px;")
                header.setToolTip(format_clave_calibre_display(clave) or clave)
                self._placas_layout.addWidget(header)
            for meta in placas:
                btn = QPushButton(meta["label"])
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                btn.setStyleSheet(self._ss_btn_placa(seleccionada=False))
                btn._lab_meta = meta  # type: ignore[attr-defined]
                btn.clicked.connect(lambda _=False, b=btn: self._on_placa_btn(b))
                self._placas_layout.addWidget(btn)
                self._placa_btns.append(btn)

        self._placas_layout.addStretch(1)
        if self._placa_btns:
            self._on_placa_btn(self._placa_btns[0])
        else:
            self._hoja_actual = None
            self.scene_act.clear()
            self.lbl_placa_info.setText("-")

    def _on_calibre_changed(self, _row: int = -1):
        self._reload_lista_placas()

    def _on_placa_btn(self, btn: QPushButton):
        meta = getattr(btn, "_lab_meta", None)
        if not isinstance(meta, dict):
            return
        self._placa_sel = meta
        for b in self._placa_btns:
            b.setStyleSheet(self._ss_btn_placa(seleccionada=(b is btn)))
            if b is btn:
                pal = b.palette()
                pal.setColor(QPalette.ColorRole.ButtonText, QColor(COLOR_TEXTO_TITULO))
                b.setPalette(pal)
            else:
                pal = b.palette()
                pal.setColor(QPalette.ColorRole.ButtonText, QColor("#FFFFFF"))
                b.setPalette(pal)

        self._hoja_sim = None
        self._timeline = None
        self.btn_timeline.setEnabled(False)
        self.scene_sim.clear()
        self.lbl_efi_sim.setText("DIR - (sin simular)")

        hoja = copy.deepcopy(meta.get("hoja") or {})
        clave = str(meta.get("clave") or "")
        if self._datos_partes:
            enriquecer_hoja_export_desde_partes(hoja, clave, self._datos_partes)
        actualizar_eficiencias_hoja(hoja)
        self._hoja_actual = hoja
        w, h = dims_placa_desde_hoja(hoja)
        n = int(meta.get("n_piezas") or 0)
        self.lbl_placa_info.setText(
            f"{meta.get('placa_id')} · {mm_to_inches(w):.1f}\" × {mm_to_inches(h):.1f}\" · "
            f"{n} piezas · {formatear_eficiencias_placa(hoja)}"
        )
        self.lbl_efi_act.setText(formatear_eficiencias_placa(hoja))
        self._paint(self.scene_act, self.view_act, hoja, clave=clave or "LAB")
        pack = piezas_pack_desde_hoja(hoja)
        src = (pack[0].get("_lab_geom_src") if pack else None) or {}
        src_txt = (
            f" (DXF={src.get('dxf', 0)} · poly={src.get('poly', 0)})"
            if src
            else ""
        )
        self._log(f"Placa activa: pool re-nesteo = {len(pack)} piezas{src_txt}.")
        if src and int(src.get("dxf") or 0) == 0 and int(src.get("poly") or 0) > 0:
            self._log(
                "WARN: sin DXF en disco — Base re-nesteará polígonos serializados "
                "(suelen perder barrenos/cavidades). Reabre el nest con PARTS o usa «Nest abierto»."
            )

    def _paint(self, scene: QGraphicsScene, view: NestLabGraphicsView, hoja: dict | None, *, clave: str = "LAB"):
        scene.clear()
        if not hoja:
            return
        w, h = dims_placa_desde_hoja(hoja)
        if w <= 0 or h <= 0:
            return
        hoja = hoja_con_nombres_agrupados(dict(hoja)) or {}
        hoja["placa_w"] = w
        hoja["placa_h"] = h
        draw = NestingDrawParams(hoja=hoja, clave=clave, app=self.app, selected_indices=set())
        meta = populate_nesting_scene(scene, draw)
        fit = compute_fit_rect(
            hoja,
            meta,
            max(400, view.viewport().width()),
            max(300, view.viewport().height()),
        )
        if fit:
            view.fit_nest_rect(fit)

    def _ajustar_vistas(self):
        if self._hoja_actual:
            clave = str((self._placa_sel or {}).get("clave") or "LAB")
            self._paint(self.scene_act, self.view_act, self._hoja_actual, clave=clave)
        if self._hoja_sim:
            self._paint(self.scene_sim, self.view_sim, self._hoja_sim, clave="SIM")

    def _detener_sim(self):
        self._sim_cancel.set()
        self._log("Stop NestFab: aceptando el mejor resultado encontrado…")

    def _simular(self):
        if not self._hoja_actual:
            QMessageBox.warning(self, "LAB", "Selecciona una placa.")
            return
        engine_id = str(self.cmb_motor.currentData() or "arga_force")
        if not is_engine_ready(engine_id):
            QMessageBox.warning(self, "LAB", f"El motor '{engine_id}' no está listo.")
            return
        piezas = piezas_pack_limpias(piezas_pack_desde_hoja(self._hoja_actual))
        if not piezas:
            QMessageBox.warning(self, "LAB", "La placa no tiene piezas reales para simular.")
            return
        w, h = dims_placa_desde_hoja(self._hoja_actual)
        params = params_motor_desde_hoja(self._hoja_actual)
        self.btn_simular.setEnabled(False)
        self._sim_t0 = __import__("time").perf_counter()
        self.lbl_efi_sim.setText(f"Calculando… {self._fmt_elapsed(0)}")
        self._heartbeat.start()
        self._sim_cancel.clear()

        # NestFab Ultra: en hilo + Detener. Otros NFP/GA: proceso aparte.
        nestfab = engine_id == "svgnest_ultra"
        use_proc = (not nestfab) and engine_id in ("burke_blf", "libnest2d")
        self._sim_use_proc = use_proc
        self.btn_detener_sim.setEnabled(nestfab)
        aviso = ""
        if engine_id == "burke_blf":
            aviso = " (Burke/NFP: ~2–4 min con piezas reales)"
        elif nestfab:
            aviso = " (NestFab continuo: usa Detener para aceptar el mejor)"
        elif engine_id == "libnest2d":
            aviso = " (puede tardar varios minutos)"
        elif engine_id in ("arga_force", "arga_base"):
            aviso = " (en hilo; esperado ~5–15 s)"
        self._log(
            f"Simulando {self.cmb_motor.currentText()}… {len(piezas)} piezas · "
            f"placa {mm_to_inches(w):.1f}x{mm_to_inches(h):.1f}\" · kerf={params['kerf_in']}\"{aviso}"
        )

        def worker():
            try:
                tl = run_plate_sim(
                    piezas,
                    w_mm=w,
                    h_mm=h,
                    kerf_in=float(params["kerf_in"]),
                    margin_in=float(params["margin_in"]),
                    corner=str(params["corner"]),
                    opt=str(params["opt"]),
                    mc_iterations=30 if nestfab else 1,
                    engine_id=engine_id,
                    isolate_process=use_proc,
                    timeout_s=600.0,
                    cancel_checker=(self._sim_cancel.is_set if nestfab else None),
                )
                if tl.error and not tl.hoja:
                    self._sig_sim_err.emit(tl.error)
                    return
                self._sig_sim_done.emit(tl)
            except Exception as exc:
                self._sig_sim_err.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _fmt_elapsed(seconds: float) -> str:
        s = max(0, int(seconds))
        if s < 60:
            return f"{s}s"
        m, sec = divmod(s, 60)
        if m < 60:
            return f"{m} min {sec:02d}s"
        h, m = divmod(m, 60)
        return f"{h} h {m:02d} min {sec:02d}s"

    def _on_sim_heartbeat(self):
        elapsed = max(0.0, __import__("time").perf_counter() - float(self._sim_t0 or 0.0))
        modo = "proceso aparte" if getattr(self, "_sim_use_proc", False) else "en hilo"
        self.lbl_efi_sim.setText(f"Calculando… {self._fmt_elapsed(elapsed)} ({modo})")

    def _on_sim_done(self, tl):
        self._heartbeat.stop()
        self.btn_simular.setEnabled(True)
        self.btn_detener_sim.setEnabled(False)
        self._sim_cancel.clear()
        self._timeline = tl
        hoja = tl.hoja or {"piezas": [], "placa_w": tl.w_mm, "placa_h": tl.h_mm}
        hoja.setdefault("placa_w", tl.w_mm)
        hoja.setdefault("placa_h", tl.h_mm)
        actualizar_eficiencias_hoja(hoja)
        self._hoja_sim = hoja
        n = len(hoja.get("piezas") or [])
        efi = float(hoja.get("eficiencia_directa") or hoja.get("eficiencia") or 0)
        motor = tl.engine_id or self.cmb_motor.currentText()
        self.lbl_efi_sim.setText(
            f"{formatear_eficiencias_placa(hoja)} · {self._fmt_elapsed(tl.elapsed_ms/1000.0)} · "
            f"restos={len(tl.restos or [])} · {motor}"
        )
        self.lbl_sim.setText(f"Simulación ({motor})")
        self._paint(self.scene_sim, self.view_sim, hoja, clave="SIM")
        self.tabs_vista.setTabText(1, f"SIMULACIÓN · {motor}")
        self.tabs_vista.setCurrentIndex(1)
        self.btn_timeline.setEnabled(bool(tl.pasos))
        efi_act = float(
            (self._hoja_actual or {}).get("eficiencia_directa")
            or (self._hoja_actual or {}).get("eficiencia")
            or 0
        )
        self._log(
            f"Listo [{motor}]. Actual DIR {efi_act:.1f}% → Sim DIR {efi:.1f}% "
            f"(Δ {efi - efi_act:+.1f} pp). ok={tl.ok} piezas={n}"
        )

    def _on_sim_err(self, msg: str):
        self._heartbeat.stop()
        self.btn_simular.setEnabled(True)
        self.btn_detener_sim.setEnabled(False)
        self._sim_cancel.clear()
        self._log(f"ERROR: {msg}")
        QMessageBox.critical(self, "LAB", f"Falló la simulación:\n{msg}")

    def _abrir_timeline(self):
        if not self._timeline or not self._timeline.pasos:
            QMessageBox.information(self, "LAB", "Primero ejecuta una simulación.")
            return
        from interface.qt.dialogs.nest_sim_timeline import NestReplayer

        w, h = dims_placa_desde_hoja(self._hoja_actual or {})
        motor = getattr(self._timeline, "engine_id", "") or self.cmb_motor.currentText()
        titulo = (
            f"[SIM {motor}] Re-nesteo {mm_to_inches(w):.0f}\" x {mm_to_inches(h):.0f}\""
        )
        win = NestReplayer(self._timeline, titulo=titulo)
        win.showMaximized()
        win.raise_()
        win.activateWindow()
        self._dlg_timeline = win
