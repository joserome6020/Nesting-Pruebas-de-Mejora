"""Tab PARTS — PySide6 nativo."""
from __future__ import annotations

import os
import csv
import json
import re
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from interface.material_colors import fila_fondo_material
from interface.qt.thread_bridge import call_on_main
from interface.qt.visualizer import VisorDXF, generar_thumbnail
from interface.qt.ui_mixins import TimerHost, scroll_clear, scroll_add_widget
from interface.qt.layout_helpers import (
    finalize_splitter,
    make_card,
    make_herinox_card,
    make_horizontal_splitter,
    make_scroll,
    make_scroll_content,
)

from interface.qt.theme import (
    COLOR_BORDE,
    COLOR_GRIS_DARK,
    COLOR_GRIS_MED,
    COLOR_TEXTO_SUBTITULO,
    COLOR_TEXTO_TITULO,
    apply_push_button,
)

COLOR_TARJETA = "#FFFFFF"
COLOR_HOVER = "#E2E8F0"
ARGB_BTN_1 = "#202A36"
ARGB_BTN_2 = "#334659"
ARGB_BTN_3 = "#455E75"
ARGB_BTN_4 = "#708DA9"


class _NombrePiezaLabel(QLabel):
    """Nombre truncado con elipsis; clic para ver el nombre completo."""

    def __init__(self, texto: str, parent_row: QFrame, on_select=None, parent=None):
        super().__init__(parent)
        self._full = str(texto or "")
        self._expanded = False
        self._parent_row = parent_row
        self._on_select = on_select
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};")
        self._refresh()

    def _refresh(self):
        if self._expanded:
            self.setText(self._full)
            self.setWordWrap(True)
            self.setToolTip("Clic para contraer")
            self._parent_row.setMinimumHeight(max(48, self.sizeHint().height() + 10))
        else:
            fm = self.fontMetrics()
            self.setText(fm.elidedText(self._full, Qt.TextElideMode.ElideRight, 200))
            self.setWordWrap(False)
            self.setToolTip(f"{self._full}\n\nClic para ver completo")
            self._parent_row.setFixedHeight(48)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._expanded = not self._expanded
            self._refresh()
            if callable(self._on_select):
                self._on_select()
            event.accept()
            return
        super().mousePressEvent(event)


class TabParts(QWidget, TimerHost):
    def __init__(self, master, app_principal):
        QWidget.__init__(self, master)
        TimerHost.__init__(self)
        self.app = app_principal
        self._row_widgets = {}

        self.local_col_config = [
            {"weight": 3, "min": 150},
            {"weight": 2, "min": 90},
            {"weight": 1, "min": 45},
            {"weight": 1, "min": 70},
            {"weight": 1, "min": 65},
            {"weight": 1, "min": 70},
            {"weight": 1, "min": 52},
            {"weight": 1, "min": 60},
        ]

        # Estado para lista de largos
        self.btn_lista_largos = None
        self.ventana_lista_largos = None

        self.rutas_dxf_actuales = []

        self._PARTS_GRID_MARGIN_H = 10

        self.setup_ui()

    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = make_horizontal_splitter(720)
        frame_tabla = make_card()
        tabla_lay = QVBoxLayout(frame_tabla)
        tabla_lay.setContentsMargins(16, 16, 12, 16)

        frame_header = QWidget()
        hdr = QHBoxLayout(frame_header)
        self.lbl_tanques = QLabel("TANQUES DEL PROYECTO:")
        self.lbl_tanques.setStyleSheet(f"font-weight:700;color:{COLOR_GRIS_DARK};font-size:15px;")
        hdr.addWidget(self.lbl_tanques)
        self.ent_tanques = QLineEdit("X1")
        self.ent_tanques.setFixedWidth(70)
        self.ent_tanques.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.addWidget(self.ent_tanques)
        self.btn_aplicar_tanques = QPushButton("APLICAR")
        apply_push_button(self.btn_aplicar_tanques, ARGB_BTN_2, font_size=11)
        self.btn_aplicar_tanques.clicked.connect(self.aplicar_cantidad_tanques)
        hdr.addWidget(self.btn_aplicar_tanques)
        self.ent_tanques.returnPressed.connect(self.aplicar_cantidad_tanques)

        self._dxf_audit_token = 0
        self._dxf_audit_actual: dict = {"total": 0, "ok": 0, "omitidos": []}
        hdr.addStretch()
        self.btn_reprocesar_autodxf = QPushButton("REPROCESAR AUTODXF")
        apply_push_button(self.btn_reprocesar_autodxf, ARGB_BTN_2, font_size=11)
        self.btn_reprocesar_autodxf.setToolTip(
            "Vuelve a limpiar/procesar los DXF crudos de AutoDXF → Processed Files "
            "y actualiza PARTS. Luego RENESTEAR ESTA PLACA en la placa afectada."
        )
        self.btn_reprocesar_autodxf.clicked.connect(self.reprocesar_autodxf_partes)
        hdr.addWidget(self.btn_reprocesar_autodxf)
        self.btn_lista_largos = QPushButton("DEMANDA DE LARGOS")
        apply_push_button(self.btn_lista_largos, ARGB_BTN_3, font_size=11)
        self.btn_lista_largos.clicked.connect(self.abrir_ventana_lista_largos)
        hdr.addWidget(self.btn_lista_largos)
        tabla_lay.addWidget(frame_header)

        header_wrap = QWidget()
        header_wrap_lay = QHBoxLayout(header_wrap)
        header_wrap_lay.setContentsMargins(0, 0, 0, 0)
        header_wrap_lay.setSpacing(0)

        self._parts_head = QFrame()
        self._parts_head.setObjectName("TableHeader")
        self._parts_head.setFrameShape(QFrame.Shape.NoFrame)
        self._parts_head.setFixedHeight(42)
        self._parts_head_grid = QGridLayout(self._parts_head)
        self._parts_head_grid.setContentsMargins(self._PARTS_GRID_MARGIN_H, 0, self._PARTS_GRID_MARGIN_H, 0)
        self._apply_parts_grid_columns(self._parts_head_grid)
        titulos = ["PIEZA / REF", "MATERIAL", "QTY", "TOTAL QTY", "CALIBRE", "ESTADO", "ESP.", "VISTA"]
        for i, txt in enumerate(titulos):
            lbl = QLabel(txt)
            if i == 0:
                lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            else:
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if txt == "ESP.":
                lbl.setToolTip(
                    "Cobre: Amada 5\" (VERTICAL + FIXTURA más justa).\n"
                    "Fixtura 2 (~28.95\") u original (~35.33\") según el largo.\n"
                    "Acero/otros: marcar para compensación plasma "
                    "(reprocesa geometría y nestea en placas solo-plasma)."
                )
            self._parts_head_grid.addWidget(lbl, 0, i)
        header_wrap_lay.addWidget(self._parts_head, 1)

        self._parts_scroll_spacer = QWidget()
        self._parts_scroll_spacer.setFixedWidth(0)
        header_wrap_lay.addWidget(self._parts_scroll_spacer)
        tabla_lay.addWidget(header_wrap)

        self.lista_scroll = make_scroll()
        self._lista_inner, self._lista_layout = make_scroll_content()
        self._lista_layout.setSpacing(2)
        self._lista_layout.setContentsMargins(0, 4, 0, 8)
        self.lista_scroll.setWidget(self._lista_inner)
        sb = self.lista_scroll.verticalScrollBar()
        sb.rangeChanged.connect(lambda *_: self._sync_parts_header_scrollbar())
        sb.valueChanged.connect(lambda *_: self._sync_parts_header_scrollbar())
        tabla_lay.addWidget(self.lista_scroll, 1)

        # Pie: conteos DXF / piezas a la izquierda (debajo de la tabla).
        frame_footer = QWidget()
        foot = QHBoxLayout(frame_footer)
        foot.setContentsMargins(0, 8, 0, 0)
        foot.setSpacing(12)
        self.lbl_dxf_conteo = QLabel("DXF NESTEO: —")
        self.lbl_dxf_conteo.setStyleSheet(self._estilo_lbl_dxf_conteo())
        foot.addWidget(self.lbl_dxf_conteo)
        self.btn_dxf_omitidos = QPushButton("VER OMITIDOS")
        apply_push_button(self.btn_dxf_omitidos, ARGB_BTN_4, font_size=10)
        self.btn_dxf_omitidos.setEnabled(False)
        self.btn_dxf_omitidos.clicked.connect(self.abrir_dialogo_dxf_omitidos)
        foot.addWidget(self.btn_dxf_omitidos)
        self.lbl_piezas_nestear = QLabel("PIEZAS A NESTEAR: —")
        self.lbl_piezas_nestear.setStyleSheet(self._estilo_lbl_dxf_conteo())
        self.lbl_piezas_nestear.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        foot.addWidget(self.lbl_piezas_nestear)
        foot.addStretch()
        tabla_lay.addWidget(frame_footer)

        splitter.addWidget(frame_tabla)

        frame_visor_bg = make_card()
        vis_lay = QVBoxLayout(frame_visor_bg)
        vis_lay.setContentsMargins(12, 12, 12, 12)
        vis_lay.setSpacing(8)

        hdr_row = QHBoxLayout()
        tit = QLabel("DETALLE DE PIEZA")
        tit.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};font-size:14px;")
        hdr_row.addWidget(tit)
        self.lbl_job_activo = QLabel("—")
        self.lbl_job_activo.setStyleSheet(
            f"font-weight:600;color:{COLOR_TEXTO_SUBTITULO};font-size:13px;padding-left:6px;"
        )
        hdr_row.addWidget(self.lbl_job_activo)
        hdr_row.addStretch()
        lbl_sub = QLabel("VISTA CAD CON COTAS INTERACTIVAS")
        lbl_sub.setStyleSheet(f"color:{COLOR_GRIS_MED};font-size:11px;")
        hdr_row.addWidget(lbl_sub)
        vis_lay.addLayout(hdr_row)

        self.frame_black_visor = QFrame()
        self.frame_black_visor.setStyleSheet(
            "background:#0B1220;border-radius:10px;border:1px solid #334155;"
        )
        fbl = QVBoxLayout(self.frame_black_visor)
        fbl.setContentsMargins(0, 0, 0, 0)
        fbl.setSpacing(0)
        self.visor = VisorDXF(self.frame_black_visor)
        self._material_fila_actual = None
        self.visor.set_persist_rotation_hook(self._persistir_orientacion_cobre)
        vis_lay.addWidget(self.frame_black_visor, 1)
        splitter.addWidget(frame_visor_bg)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        finalize_splitter(splitter, min_left=420, min_right=340)
        root.addWidget(splitter)

    def _apply_parts_grid_columns(self, grid: QGridLayout) -> None:
        grid.setHorizontalSpacing(4)
        for i, conf in enumerate(self.local_col_config):
            grid.setColumnStretch(i, conf["weight"])
            grid.setColumnMinimumWidth(i, conf["min"])

    def _sync_parts_header_scrollbar(self) -> None:
        sb = self.lista_scroll.verticalScrollBar()
        ancho = sb.width() if sb.isVisible() else 0
        self._parts_scroll_spacer.setFixedWidth(ancho)
        self._parts_head_grid.setContentsMargins(
            self._PARTS_GRID_MARGIN_H,
            0,
            self._PARTS_GRID_MARGIN_H,
            0,
        )

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._al_mostrar_pestana)

    def _al_mostrar_pestana(self):
        self._sync_parts_header_scrollbar()
        self._actualizar_lbl_job_activo()

    def _actualizar_lbl_job_activo(self):
        if not hasattr(self, "lbl_job_activo"):
            return
        job = str(getattr(self.app, "job_activo", "") or "").strip()
        if not job or job.upper() in ("NESTING", "PENDIENTE", "JOB"):
            self.lbl_job_activo.setText("—")
            self.lbl_job_activo.setToolTip("")
            return
        self.lbl_job_activo.setText(f"· {job}")
        self.lbl_job_activo.setToolTip(job)

    def refrescar_tabla(self, datos, *, thumbnails_async: bool = False):
        multiplicador = getattr(self.app, "multiplicador_tanques", 1)
        self.lbl_tanques.setText("TANQUES DEL PROYECTO:")
        self._actualizar_lbl_job_activo()
        try:
            self.ent_tanques.setText(f"X{int(multiplicador)}")
        except Exception:
            pass

        self.rutas_dxf_actuales = []
        scroll_clear(self.lista_scroll)
        self._row_widgets = {}
        thumb_queue: list[tuple] = []

        for idx, item in enumerate(datos):
            pieza, mat, qty_total, cal, st, ruta = item
            if ruta:
                self.rutas_dxf_actuales.append(str(ruta))
            try:
                tot_val = int(qty_total)
                qty_unidad = max(1, tot_val // multiplicador)
            except Exception:
                tot_val, qty_unidad = qty_total, qty_total

            color_fondo = fila_fondo_material(mat, idx)
            row = QFrame()
            row.setObjectName("PartsRowAlt" if idx % 2 else "PartsRow")
            row.setFrameShape(QFrame.Shape.NoFrame)
            row.setFixedHeight(48)
            row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            row.orig_name = row.objectName()
            row.orig_color = color_fondo
            row_lay = QGridLayout(row)
            row_lay.setContentsMargins(self._PARTS_GRID_MARGIN_H, 2, self._PARTS_GRID_MARGIN_H, 2)
            self._apply_parts_grid_columns(row_lay)

            valores = [pieza, mat, str(qty_unidad), str(tot_val), cal, st]
            es_cu = self._es_material_cobre(mat)
            es_plasma = bool(ruta) and not es_cu and self._plasma_guardada(ruta)
            if es_plasma:
                valores[5] = "PLASMA"
            for i, conf in enumerate(self.local_col_config):
                if i < 6:
                    if i == 0:
                        lbl = _NombrePiezaLabel(
                            pieza,
                            row,
                            on_select=lambda r=ruta, f=row, p=pieza, m=mat: self.seleccionar_fila(r, f, p, m),
                        )
                    else:
                        lbl = QLabel(valores[i])
                        if i == 5 and es_plasma:
                            lbl.setStyleSheet("color:#2563EB;font-weight:700;")
                        else:
                            lbl.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};")
                        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                        lbl.mousePressEvent = lambda ev, r=ruta, f=row, p=pieza, m=mat: self.seleccionar_fila(r, f, p, m)
                    row_lay.addWidget(lbl, 0, i)
                elif i == 6:
                    chk_wrap = QWidget()
                    chk_lay = QHBoxLayout(chk_wrap)
                    chk_lay.setContentsMargins(0, 0, 0, 0)
                    chk_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    chk = QCheckBox()
                    if es_cu and ruta:
                        chk.setEnabled(True)
                        chk.setVisible(True)
                        chk.setToolTip(
                            "Amada 5\": AMADA/VERTICAL + AMADA/FIXTURA (sin gap)"
                        )
                        chk.setChecked(self._cu_especial_guardada(ruta))
                        chk.toggled.connect(
                            lambda checked, r=ruta, c=chk: self._persistir_cu_especial(
                                r, checked, checkbox=c
                            )
                        )
                    elif ruta and not es_cu:
                        chk.setEnabled(True)
                        chk.setVisible(True)
                        chk.setToolTip(
                            "Plasma: compensar esta pieza y nestearla en placas solo-plasma"
                        )
                        chk.setChecked(self._plasma_guardada(ruta))
                        chk.toggled.connect(
                            lambda checked, r=ruta, calibre=cal, c=chk: self._persistir_plasma(
                                r, calibre, checked, checkbox=c
                            )
                        )
                    else:
                        chk.setEnabled(False)
                        chk.setVisible(False)
                        chk.setToolTip("")
                    chk_lay.addWidget(chk)
                    row_lay.addWidget(chk_wrap, 0, i)
                elif i == 7:
                    if thumbnails_async:
                        ph = QLabel("…")
                        ph.setFixedSize(32, 32)
                        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
                        ph.setStyleSheet("color:#94A3B8;font-size:9px;background:transparent;")
                        ph.mousePressEvent = lambda ev, r=ruta, f=row, p=pieza, m=mat: self.seleccionar_fila(r, f, p, m)
                        row_lay.addWidget(ph, 0, i)
                        if ruta:
                            thumb_queue.append((ph, str(ruta), mat))
                    else:
                        try:
                            thumb = generar_thumbnail(ruta, size=(32, 32), material=mat)
                            if thumb:
                                l_t = QLabel()
                                l_t.setPixmap(thumb)
                                l_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
                                l_t.mousePressEvent = lambda ev, r=ruta, f=row, p=pieza, m=mat: self.seleccionar_fila(r, f, p, m)
                                row_lay.addWidget(l_t, 0, i)
                        except Exception:
                            pass

            row.mousePressEvent = lambda ev, r=ruta, f=row, p=pieza, m=mat: self.seleccionar_fila(r, f, p, m)
            scroll_add_widget(self.lista_scroll, row)

        if thumbnails_async and thumb_queue:
            self._iniciar_thumbnails_async(thumb_queue)
        self._actualizar_lbl_piezas_nestear(datos)
        self._iniciar_auditoria_dxf_async(datos)
        QTimer.singleShot(0, self._sync_parts_header_scrollbar)

    def _iniciar_auditoria_dxf_async(self, datos):
        self._dxf_audit_token = int(getattr(self, "_dxf_audit_token", 0)) + 1
        token = self._dxf_audit_token
        snapshot = [tuple(x) for x in (datos or [])]
        self.app.dxf_audit_pending = True
        self.lbl_dxf_conteo.setText("DXF NESTEO: validando…")
        self.lbl_dxf_conteo.setStyleSheet(self._estilo_lbl_dxf_conteo())
        self.btn_dxf_omitidos.setEnabled(False)
        threading.Thread(
            target=self._thread_auditar_dxfs,
            args=(token, snapshot),
            daemon=True,
        ).start()

    def _thread_auditar_dxfs(self, token: int, datos: list):
        audit = {"total": 0, "ok": 0, "omitidos": []}
        try:
            from modules.nesting_engine.dxf_nesting_audit import auditar_lista_partes

            audit = auditar_lista_partes(datos)
        except Exception as exc:
            audit = {
                "total": len(datos),
                "ok": 0,
                "omitidos": [
                    {
                        "pieza": "(auditoría)",
                        "ruta": "",
                        "archivo": "",
                        "error": f"No se pudo auditar DXF: {exc}",
                    }
                ],
            }
        call_on_main(self._aplicar_auditoria_dxf, token, audit)

    def _aplicar_auditoria_dxf(self, token: int, audit: dict):
        if token != getattr(self, "_dxf_audit_token", 0):
            return
        self._dxf_audit_actual = {
            "total": int(audit.get("total", 0) or 0),
            "ok": int(audit.get("ok", 0) or 0),
            "omitidos": list(audit.get("omitidos") or []),
        }
        self.app.dxf_nesting_audit = dict(self._dxf_audit_actual)
        self.app.dxf_audit_pending = False
        self._actualizar_widget_resumen_dxf()

    def actualizar_resumen_dxf(self, audit: dict | None = None):
        """Actualiza contador desde auditoría externa (p. ej. tras intento de nesting)."""
        if audit is not None:
            self._dxf_audit_actual = {
                "total": int(audit.get("total", 0) or 0),
                "ok": int(audit.get("ok", 0) or 0),
                "omitidos": list(audit.get("omitidos") or []),
            }
            self.app.dxf_nesting_audit = dict(self._dxf_audit_actual)
            self.app.dxf_audit_pending = False
        self._actualizar_widget_resumen_dxf()

    def _estilo_lbl_dxf_conteo(self) -> str:
        return f"font-weight:700;color:{COLOR_GRIS_DARK};font-size:13px;padding:0 8px;"

    def _claves_omitidas_dxf(self) -> set[tuple[str, str]]:
        claves: set[tuple[str, str]] = set()
        for item in self._dxf_audit_actual.get("omitidos") or []:
            pieza = str(item.get("pieza") or "").strip()
            ruta = str(item.get("ruta") or "").strip()
            claves.add((pieza, ruta))
        return claves

    def _calcular_piezas_nestear(self, datos, *, excluir_omitidos: bool = False) -> int:
        omitidas = self._claves_omitidas_dxf() if excluir_omitidos else set()
        total = 0
        for item in datos or []:
            try:
                pieza, _mat, qty_total, _cal, _st, ruta = item
                if excluir_omitidos and (
                    str(pieza or "").strip(),
                    str(ruta or "").strip(),
                ) in omitidas:
                    continue
                total += max(0, int(str(qty_total).strip()))
            except Exception:
                pass
        return total

    def _actualizar_lbl_piezas_nestear(self, datos=None, *, excluir_omitidos: bool = False):
        if not hasattr(self, "lbl_piezas_nestear"):
            return
        datos = datos if datos is not None else getattr(self.app, "datos_partes_actuales", []) or []
        if not datos:
            self.lbl_piezas_nestear.setText("PIEZAS A NESTEAR: —")
            return
        total = self._calcular_piezas_nestear(datos, excluir_omitidos=excluir_omitidos)
        self.lbl_piezas_nestear.setText(f"PIEZAS A NESTEAR: {total}")

    def _actualizar_widget_resumen_dxf(self):
        total = int(self._dxf_audit_actual.get("total", 0) or 0)
        ok = int(self._dxf_audit_actual.get("ok", 0) or 0)
        omitidos = list(self._dxf_audit_actual.get("omitidos") or [])
        n_omit = len(omitidos)

        if total <= 0:
            self.lbl_dxf_conteo.setText("DXF NESTEO: —")
            self.lbl_dxf_conteo.setStyleSheet(self._estilo_lbl_dxf_conteo())
            self.btn_dxf_omitidos.setEnabled(False)
            self.btn_dxf_omitidos.setText("VER OMITIDOS")
            return

        self.lbl_dxf_conteo.setText(f"DXF NESTEO: {ok}/{total}")
        self.lbl_dxf_conteo.setStyleSheet(self._estilo_lbl_dxf_conteo())
        self.btn_dxf_omitidos.setText(
            f"VER OMITIDOS ({n_omit})" if n_omit else "VER OMITIDOS"
        )
        self.btn_dxf_omitidos.setEnabled(n_omit > 0)
        self._actualizar_lbl_piezas_nestear(excluir_omitidos=True)

    def abrir_dialogo_dxf_omitidos(self):
        omitidos = list(self._dxf_audit_actual.get("omitidos") or [])
        if not omitidos:
            QMessageBox.information(
                self,
                "DXF para nesteo",
                "Todos los DXF del listado tienen geometría válida para nestear.",
            )
            return
        self._mostrar_dialogo_dxf_omitidos(omitidos)

    def _mostrar_dialogo_dxf_omitidos(self, omitidos: list):
        dlg = QDialog(self)
        dlg.setWindowTitle("DXF omitidos del nesteo")
        dlg.resize(1100, 560)
        dlg.setModal(True)
        lay = QVBoxLayout(dlg)
        card = make_herinox_card()
        card_lay = QVBoxLayout(card)

        tit = QLabel("DXF NO TOMADOS EN CUENTA PARA EL NESTEO")
        tit.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};font-size:16px;")
        card_lay.addWidget(tit)

        total = int(self._dxf_audit_actual.get("total", 0) or 0)
        ok = int(self._dxf_audit_actual.get("ok", 0) or 0)
        card_lay.addWidget(
            QLabel(
                f"Procesados para nesteo: {ok}/{total}   |   "
                f"Omitidos: {len(omitidos)}"
            )
        )

        table = QTableWidget(len(omitidos), 3)
        table.setHorizontalHeaderLabels(["PIEZA / REF", "ARCHIVO DXF", "MOTIVO"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setAlternatingRowColors(True)
        table.setWordWrap(True)
        table.setStyleSheet(
            "QTableWidget{background:#FFFFFF;color:#0F172A;alternate-background-color:#F8FAFC;"
            "gridline-color:#E2E8F0;border:1px solid #E2E8F0;border-radius:8px;}"
            "QTableWidget::item{color:#0F172A;padding:4px;}"
            "QHeaderView::section{background:#F1F5F9;color:#475569;font-weight:700;border:none;"
            "border-bottom:1px solid #E2E8F0;padding:6px;}"
        )
        hdr_tbl = table.horizontalHeader()
        hdr_tbl.resizeSection(0, 220)
        hdr_tbl.resizeSection(1, 320)
        hdr_tbl.setStretchLastSection(True)

        for ri, item in enumerate(omitidos):
            pieza = str(item.get("pieza") or "(sin nombre)")
            archivo = str(item.get("archivo") or os.path.basename(str(item.get("ruta") or "")))
            error = str(item.get("error") or "Sin detalle")
            table.setItem(ri, 0, QTableWidgetItem(pieza))
            table.setItem(ri, 1, QTableWidgetItem(archivo))
            err_item = QTableWidgetItem(error)
            err_item.setToolTip(error)
            table.setItem(ri, 2, err_item)

        table.resizeRowsToContents()
        card_lay.addWidget(table, 1)
        lay.addWidget(card)
        dlg.exec()

    def _iniciar_thumbnails_async(self, thumb_queue: list[tuple]):
        self._thumb_gen_token = int(getattr(self, "_thumb_gen_token", 0)) + 1
        token = self._thumb_gen_token
        threading.Thread(
            target=self._thread_generar_thumbnails,
            args=(token, list(thumb_queue)),
            daemon=True,
        ).start()

    def _thread_generar_thumbnails(self, token: int, thumb_queue: list[tuple]):
        resultados = []
        total = len(thumb_queue)
        for i, (ph, ruta, mat) in enumerate(thumb_queue, start=1):
            pix = None
            if ruta and os.path.exists(ruta):
                try:
                    pix = generar_thumbnail(ruta, size=(32, 32), material=mat)
                except Exception:
                    pix = None
            resultados.append((ph, pix))
            app = getattr(self, "app", None)
            if app is not None and hasattr(app, "actualizar_progreso") and total > 8:
                try:
                    app.actualizar_progreso(
                        f"Miniaturas PARTS {i}/{total}…",
                        0.05 + 0.15 * (i / max(1, total)),
                    )
                except Exception:
                    pass
        call_on_main(self._aplicar_thumbnails_async, token, resultados)

    def _aplicar_thumbnails_async(self, token: int, resultados: list[tuple]):
        if token != getattr(self, "_thumb_gen_token", 0):
            return
        for ph, pix in resultados:
            try:
                if ph is None or not hasattr(ph, "setPixmap"):
                    continue
                if pix is not None:
                    ph.setPixmap(pix)
                    ph.setText("")
                else:
                    ph.setText("-")
            except RuntimeError:
                pass

    def _resolver_job_data_csv_actual(self):
        rutas = [str(r[5]) for r in (getattr(self.app, "datos_partes_actuales", []) or []) if len(r) > 5 and r[5]]
        if not rutas:
            return None

        job = str(getattr(self.app, "job_activo", "") or "").strip()
        for ruta in rutas:
            p = Path(ruta)
            candidatos = []
            for actual in [p.parent, *p.parents]:
                if job:
                    candidatos.append(actual / f"job_data_{job}.csv")
                candidatos.extend(sorted(actual.glob("job_data_*.csv")))
            for c in candidatos:
                if c.exists() and c.is_file():
                    return c
        return None

    def _persistir_multiplicador_en_job_data(self, nuevo_mult: int):
        ruta_csv = self._resolver_job_data_csv_actual()
        actualizo_algo = False
        detalle = []

        if ruta_csv is not None:
            try:
                with open(ruta_csv, newline="", encoding="utf-8", errors="ignore") as f:
                    rows = list(csv.reader(f))
                if rows:
                    while len(rows) < 2:
                        rows.append([])
                    while len(rows[1]) <= 3:
                        rows[1].append("")
                    rows[1][3] = str(int(nuevo_mult))
                    with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
                        writer = csv.writer(f)
                        writer.writerows(rows)
                    actualizo_algo = True
                    detalle.append(ruta_csv.name)
            except Exception:
                pass

        # Compatibilidad con archivo legacy: job_data_job / .txt / .json
        rutas = [str(r[5]) for r in (getattr(self.app, "datos_partes_actuales", []) or []) if len(r) > 5 and r[5]]
        for ruta in rutas:
            p = Path(ruta)
            for actual in [p.parent, *p.parents]:
                for nombre in ("job_data_job.json", "job_data_job.txt", "job_data_job"):
                    legacy = actual / nombre
                    if not legacy.exists() or not legacy.is_file():
                        continue
                    try:
                        txt = legacy.read_text(encoding="utf-8", errors="ignore")
                        if nombre.endswith(".json") or txt.strip().startswith("{"):
                            data = json.loads(txt) if txt.strip() else {}
                            if not isinstance(data, dict):
                                data = {}
                            data["cantidad_tanques"] = int(nuevo_mult)
                            data["multiplicador_tanques"] = int(nuevo_mult)
                            legacy.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                        else:
                            nuevo = re.sub(
                                r"(?im)^(\s*(?:cantidad_tanques|multiplicador_tanques)\s*[:=]\s*)\d+\s*$",
                                rf"\g<1>{int(nuevo_mult)}",
                                txt,
                            )
                            if nuevo == txt:
                                nuevo = txt.rstrip() + f"\nmultiplicador_tanques={int(nuevo_mult)}\n"
                            legacy.write_text(nuevo, encoding="utf-8")
                        actualizo_algo = True
                        detalle.append(legacy.name)
                    except Exception:
                        continue
                if actualizo_algo:
                    break
            if actualizo_algo:
                break

        if actualizo_algo:
            return True, ", ".join(sorted(set(detalle)))
        return False, "No se encontró job_data_*.csv ni job_data_job del proyecto actual."

    def aplicar_cantidad_tanques(self):
        valor = str(self.ent_tanques.text() or "").strip().upper()
        if valor.startswith("X"):
            valor = valor[1:].strip()
        if not valor.isdigit() or int(valor) <= 0:
            QMessageBox.critical(self, "Valor inválido", "Ingresa una cantidad válida, por ejemplo: X10")
            return

        nuevo_mult = int(valor)
        mult_actual = max(1, int(getattr(self.app, "multiplicador_tanques", 1) or 1))

        ok, msg = self._persistir_multiplicador_en_job_data(nuevo_mult)
        if not ok:
            QMessageBox.critical(self, "No se pudo actualizar", msg)
            return

        nuevos_datos = []
        for fila in getattr(self.app, "datos_partes_actuales", []) or []:
            try:
                pieza, mat, qty_total, cal, st, ruta = fila
                qty_total_int = int(str(qty_total).strip())
                qty_base = max(1, qty_total_int // mult_actual)
                nuevos_total = qty_base * nuevo_mult
                nuevos_datos.append((pieza, mat, str(nuevos_total), cal, st, ruta))
            except Exception:
                nuevos_datos.append(fila)

        self.app.multiplicador_tanques = nuevo_mult
        self.app.cargar_datos_parts(nuevos_datos)
        QMessageBox.information(self, "Actualizado", f"Cantidad de tanques actualizada a X{nuevo_mult}.")

    def _es_material_cobre(self, material) -> bool:
        from interface.utils_nesting import es_material_cobre
        return es_material_cobre(material)

    def _cu_especial_guardada(self, ruta_dxf) -> bool:
        from interface.utils_nesting import clave_orientacion_cobre_ruta
        especiales = getattr(self.app, "cu_especial_por_ruta", None) or {}
        return bool(especiales.get(clave_orientacion_cobre_ruta(ruta_dxf), False))

    def _plasma_guardada(self, ruta_dxf) -> bool:
        from interface.utils_nesting import clave_orientacion_cobre_ruta
        marcas = getattr(self.app, "plasma_compensada_por_ruta", None) or {}
        return bool(marcas.get(clave_orientacion_cobre_ruta(ruta_dxf), False))

    def _offset_plasma_desde_calibre(self, calibre) -> float | None:
        from modules.plasma_compensator import compute_plasma_offset_mm

        try:
            parse_thk = getattr(self.app.motor_nesting, "_parse_thickness_value", None)
            thk = parse_thk(calibre) if callable(parse_thk) else None
            if thk is None:
                thk = float(self.app.motor_nesting._extraer_numero(calibre))
        except Exception:
            thk = 0.0
        if thk is None or float(thk) <= 0:
            return None
        return float(compute_plasma_offset_mm(float(thk)))

    def _validar_compensacion_plasma_dxf(self, ruta_dxf, offset_mm: float) -> tuple[bool, str]:
        """Genera DXF compensado (mismo pipeline OUTER+/INNER−) y valida que exista."""
        from modules.plasma_compensator import asegurar_dxf_plasma_compensado

        out, err = asegurar_dxf_plasma_compensado(ruta_dxf, float(offset_mm), forzar=True)
        if not out:
            return False, err or "No se pudo generar DXF compensado."
        return True, out

    def _persistir_plasma(self, ruta_dxf, calibre, checked: bool, checkbox=None):
        if not ruta_dxf:
            return
        from interface.utils_nesting import clave_orientacion_cobre_ruta

        if (
            not hasattr(self.app, "plasma_compensada_por_ruta")
            or self.app.plasma_compensada_por_ruta is None
        ):
            self.app.plasma_compensada_por_ruta = {}
        if (
            not hasattr(self.app, "plasma_dxf_por_ruta")
            or self.app.plasma_dxf_por_ruta is None
        ):
            self.app.plasma_dxf_por_ruta = {}
        clave = clave_orientacion_cobre_ruta(ruta_dxf)

        def _revert_check():
            if checkbox is not None:
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)

        if checked:
            offset_mm = self._offset_plasma_desde_calibre(calibre)
            if offset_mm is None:
                QMessageBox.warning(
                    self,
                    "Plasma",
                    "No se pudo leer el calibre para calcular la compensación.",
                )
                _revert_check()
                return
            ok, out_or_msg = self._validar_compensacion_plasma_dxf(ruta_dxf, offset_mm)
            if not ok:
                QMessageBox.warning(self, "Plasma — DXF no usable", out_or_msg)
                _revert_check()
                return
            ruta_comp = str(out_or_msg)
            self.app.plasma_compensada_por_ruta[clave] = True
            self.app.plasma_dxf_por_ruta[clave] = ruta_comp
            # Visor: DXF ya compensado (mismo parse que nesting), no overlay fake.
            try:
                if getattr(self, "visor", None) is not None:
                    self.visor.renderizar_dxf(ruta_comp, plasma_offset_mm=0.0)
                    off_in = float(offset_mm) / 25.4
                    self.visor.set_plasma_contour_emphasis(True, offset_in=off_in)
            except Exception:
                pass
            QTimer.singleShot(
                0,
                lambda r=ruta_dxf: self._refrescar_parts_tras_plasma(r),
            )
            return

        self.app.plasma_compensada_por_ruta.pop(clave, None)
        self.app.plasma_dxf_por_ruta.pop(clave, None)
        try:
            if getattr(self, "visor", None) is not None:
                self.visor.renderizar_dxf(ruta_dxf, plasma_offset_mm=0.0)
                self.visor.set_plasma_contour_emphasis(False)
        except Exception:
            pass
        QTimer.singleShot(
            0,
            lambda r=ruta_dxf: self._refrescar_parts_tras_plasma(r),
        )

    def _refrescar_parts_tras_plasma(self, ruta_dxf):
        datos = getattr(self.app, "datos_partes_actuales", []) or []
        self.app.cargar_datos_parts(datos, thumbnails_async=True)
        from interface.utils_nesting import clave_orientacion_cobre_ruta

        for item in datos:
            try:
                pieza, mat, _q, _cal, _st, ruta = item
            except Exception:
                continue
            if str(ruta or "") != str(ruta_dxf or ""):
                continue
            self.visor.set_material(mat)
            vista = ruta
            if self._plasma_guardada(ruta):
                clave = clave_orientacion_cobre_ruta(ruta)
                vista = str(
                    (getattr(self.app, "plasma_dxf_por_ruta", {}) or {}).get(clave)
                    or ruta
                )
            self.visor.renderizar_dxf(vista, plasma_offset_mm=0.0)
            if self._plasma_guardada(ruta):
                off = float(self._offset_plasma_desde_calibre(_cal) or 0.0)
                self.visor.set_plasma_contour_emphasis(
                    True, offset_in=(off / 25.4) if off > 0 else None
                )
            else:
                self.visor.set_plasma_contour_emphasis(False)
            self.visor.actualizar_info_extra(referencia=pieza)
            return

    def _dims_pieza_cobre_in(self, ruta_dxf, rot_deg: int) -> tuple[float, float] | None:
        """(largo X in, ancho Y in) con la orientación indicada."""
        try:
            from interface.qt.dxf_part_loader import load_dxf_part

            model = load_dxf_part(str(ruta_dxf), int(rot_deg) % 360)
            if model is None:
                return None
            fc = float(model.factor_conversion) or 25.4
            snap = model.snap_ctx
            if snap is not None and getattr(snap, "vertices", None) is not None:
                verts = snap.vertices
                if len(verts):
                    xs = verts[:, 0]
                    ys = verts[:, 1]
                    return (
                        float(abs(float(xs.max()) - float(xs.min())) / fc),
                        float(abs(float(ys.max()) - float(ys.min())) / fc),
                    )
            return (
                float(abs(model.max_x_raw - model.min_x_raw) / fc),
                float(abs(model.max_y_raw - model.min_y_raw) / fc),
            )
        except Exception:
            return None

    def _validar_amada_fixtura(self, ruta_dxf) -> tuple[bool, str, int | None]:
        """
        Fixtura Amada (catálogo):
          - ancho Y exacto 5\" (±tol)
          - largo X <= al menos una fixtura (se elige la más justa al exportar)
        Returns (ok, mensaje, rot_sugerida_o_None).
        """
        from modules.nesting_engine.cu_largos_nesting import (
            AMADA_FIXTURA_ANCHO_IN,
            TOL_ANCHO_IN_MIN,
            amada_fixtura_elegir,
            amada_fixtura_largo_max_in,
        )

        rot_actual = self._orientacion_cobre_guardada(ruta_dxf)
        dims = self._dims_pieza_cobre_in(ruta_dxf, rot_actual)
        if dims is None:
            return (
                False,
                "No se pudo medir el DXF. No se puede marcar ESP. Amada.",
                None,
            )

        largo_x, ancho_y = dims
        largo_max = float(amada_fixtura_largo_max_in())
        rot_usar = None

        def _msg_fixtura_elegida(largo: float) -> str:
            elec = amada_fixtura_elegir(largo)
            if not elec:
                return ""
            return (
                f' Se usará {elec.get("label")} '
                f'(canal {float(elec.get("canal_in") or 0):.2f}").'
            )

        if abs(ancho_y - AMADA_FIXTURA_ANCHO_IN) > TOL_ANCHO_IN_MIN:
            rot_alt = (rot_actual + 90) % 360
            dims_alt = self._dims_pieza_cobre_in(ruta_dxf, rot_alt)
            if dims_alt is not None:
                largo_alt, ancho_alt = dims_alt
                if abs(ancho_alt - AMADA_FIXTURA_ANCHO_IN) <= TOL_ANCHO_IN_MIN:
                    if float(largo_alt) > largo_max + TOL_ANCHO_IN_MIN:
                        return (
                            False,
                            (
                                f"Ese DXF no se puede usar en fixtura Amada.\n\n"
                                f'Aunque al girar 90° el ancho quede en '
                                f'{AMADA_FIXTURA_ANCHO_IN:.0f}", el largo sería '
                                f'{largo_alt:.3f}" y ninguna fixtura admite más de '
                                f'{largo_max:.3f}" entre topes.\n\n'
                                f"No se puede marcar ESP."
                            ),
                            None,
                        )
                    return (
                        False,
                        (
                            f"La fixtura Amada solo admite ancho exacto de "
                            f'{AMADA_FIXTURA_ANCHO_IN:.0f}" (actual: {ancho_y:.3f}").\n\n'
                            f'Al girar 90° el ancho quedaría en {ancho_alt:.3f}" '
                            f'y el largo en {largo_alt:.3f}" '
                            f'(máx. catálogo {largo_max:.3f}").'
                            f"{_msg_fixtura_elegida(float(largo_alt))}\n"
                            "¿Girar la pieza y marcar ESP.?"
                        ),
                        rot_alt,
                    )

            return (
                False,
                (
                    f"Ese DXF no se puede usar en fixtura Amada.\n\n"
                    f'Las fixturas solo admiten exactamente {AMADA_FIXTURA_ANCHO_IN:.0f}" de ancho '
                    f"(ni más ancho ni más angosto).\n"
                    f'Ancho actual (Y): {ancho_y:.3f}".'
                ),
                None,
            )

        if float(largo_x) > largo_max + TOL_ANCHO_IN_MIN:
            return (
                False,
                (
                    f"Ese DXF no se puede usar en fixtura Amada.\n\n"
                    f'La pieza mide {largo_x:.3f}" de largo y ninguna fixtura '
                    f'admite más de {largo_max:.3f}" entre topes.\n\n'
                    f"No se puede marcar ESP."
                ),
                None,
            )

        return True, "", rot_usar

    def _validar_amada_ancho_5in(self, ruta_dxf) -> tuple[bool, str, int | None]:
        """Compat: delega a validación completa de fixtura Amada."""
        return self._validar_amada_fixtura(ruta_dxf)

    def _persistir_cu_especial(self, ruta_dxf, checked: bool, checkbox=None):
        if not ruta_dxf:
            return
        from interface.utils_nesting import clave_orientacion_cobre_ruta
        if not hasattr(self.app, "cu_especial_por_ruta") or self.app.cu_especial_por_ruta is None:
            self.app.cu_especial_por_ruta = {}
        clave = clave_orientacion_cobre_ruta(ruta_dxf)

        def _revert_check():
            if checkbox is not None:
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)

        if checked:
            ok, msg, rot_sugerida = self._validar_amada_fixtura(ruta_dxf)
            if ok:
                self.app.cu_especial_por_ruta[clave] = True
                return
            if rot_sugerida is not None:
                resp = QMessageBox.question(
                    self,
                    "Amada — orientación",
                    msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if resp == QMessageBox.StandardButton.Yes:
                    self._persistir_orientacion_cobre(rot_sugerida, ruta_dxf)
                    try:
                        if getattr(self, "visor", None) is not None:
                            self.visor.renderizar_dxf(
                                ruta_dxf, rotacion_vista_deg=rot_sugerida
                            )
                    except Exception:
                        pass
                    # Revalidar tras giro (ancho + largo).
                    ok2, msg2, _rot2 = self._validar_amada_fixtura(ruta_dxf)
                    if not ok2:
                        QMessageBox.warning(self, "Amada — DXF no usable", msg2)
                        _revert_check()
                        return
                    self.app.cu_especial_por_ruta[clave] = True
                    return
                _revert_check()
                return
            QMessageBox.warning(self, "Amada — DXF no usable", msg)
            _revert_check()
            return

        self.app.cu_especial_por_ruta.pop(clave, None)

    def _orientacion_cobre_guardada(self, ruta_dxf) -> int:
        from interface.utils_nesting import clave_orientacion_cobre_ruta
        orientaciones = getattr(self.app, "orientacion_cobre_por_ruta", None) or {}
        return int(orientaciones.get(clave_orientacion_cobre_ruta(ruta_dxf), 0)) % 360

    def _persistir_orientacion_cobre(self, grados, ruta_dxf):
        if not self._es_material_cobre(self._material_fila_actual):
            return
        if not ruta_dxf:
            return
        from interface.utils_nesting import clave_orientacion_cobre_ruta
        if not hasattr(self.app, "orientacion_cobre_por_ruta") or self.app.orientacion_cobre_por_ruta is None:
            self.app.orientacion_cobre_por_ruta = {}
        self.app.orientacion_cobre_por_ruta[clave_orientacion_cobre_ruta(ruta_dxf)] = int(grados) % 360

    def seleccionar_fila(self, ruta_dxf, frame_fila, nombre_pieza, material=None):
        inner = self.lista_scroll.widget()
        if inner:
            for i in range(self._lista_layout.count()):
                w = self._lista_layout.itemAt(i).widget()
                if w and hasattr(w, "orig_name"):
                    w.setObjectName(w.orig_name)
                    w.setStyleSheet("")
        frame_fila.setObjectName("PartsRow")
        frame_fila.setStyleSheet("background:#DBEAFE;border-radius:6px;")

        if os.path.exists(ruta_dxf):
            self._material_fila_actual = material
            self.visor.set_material(material)
            rot_vista = self._orientacion_cobre_guardada(ruta_dxf) if self._es_material_cobre(material) else 0
            vista_dxf = ruta_dxf
            if (not self._es_material_cobre(material)) and self._plasma_guardada(ruta_dxf):
                from interface.utils_nesting import clave_orientacion_cobre_ruta

                clave = clave_orientacion_cobre_ruta(ruta_dxf)
                vista_dxf = str(
                    (getattr(self.app, "plasma_dxf_por_ruta", {}) or {}).get(clave)
                    or ruta_dxf
                )
                if not os.path.isfile(vista_dxf):
                    # Regenera si faltó el archivo compensado.
                    calibre = None
                    for item in getattr(self.app, "datos_partes_actuales", []) or []:
                        try:
                            _p, _m, _q, cal, _st, ruta = item
                            if str(ruta or "") == str(ruta_dxf):
                                calibre = cal
                                break
                        except Exception:
                            continue
                    off = self._offset_plasma_desde_calibre(calibre)
                    if off:
                        from modules.plasma_compensator import asegurar_dxf_plasma_compensado

                        out, _err = asegurar_dxf_plasma_compensado(ruta_dxf, off)
                        if out:
                            self.app.plasma_dxf_por_ruta[clave] = out
                            vista_dxf = out
            self.visor.renderizar_dxf(
                vista_dxf,
                rotacion_vista_deg=rot_vista,
                plasma_offset_mm=0.0,
            )
            if (not self._es_material_cobre(material)) and self._plasma_guardada(ruta_dxf):
                calibre = None
                for item in getattr(self.app, "datos_partes_actuales", []) or []:
                    try:
                        _p, _m, _q, cal, _st, ruta = item
                        if str(ruta or "") == str(ruta_dxf):
                            calibre = cal
                            break
                    except Exception:
                        continue
                off = float(self._offset_plasma_desde_calibre(calibre) or 0.0)
                self.visor.set_plasma_contour_emphasis(
                    True, offset_in=(off / 25.4) if off > 0 else None
                )
            else:
                self.visor.set_plasma_contour_emphasis(False)
            # Mantener una sola fuente de verdad para medidas: el propio render del visor (con detección de unidades).
            self.visor.actualizar_info_extra(referencia=nombre_pieza)

    # =========================================================
    # HELPERS GENERALES AUTODXF
    # =========================================================
    def _resolver_autodxf_desde_ruta(self, ruta_archivo: str):
        try:
            p = Path(str(ruta_archivo))
        except Exception:
            return None

        candidatos = [p]
        candidatos.extend(p.parents)

        for actual in candidatos:
            nombre = actual.name.strip().lower()

            if nombre == "autodxf":
                return actual

            if nombre == "processed files":
                padre = actual.parent
                if padre.name.strip().lower() == "autodxf":
                    return padre

        return None

    def _resolver_job_desde_autodxf(self, ruta_autodxf: Path) -> str:
        """
        Intenta sacar el nombre del job desde la ruta:
        .../<JOB>/MODEL CORE FILES/AutoDXF
        """
        try:
            actual = ruta_autodxf
            while actual.parent != actual:
                if actual.name.strip().lower() == "model core files":
                    return actual.parent.name
                actual = actual.parent
        except Exception:
            pass

        try:
            return ruta_autodxf.parent.name
        except Exception:
            return "JOB_DESCONOCIDO"

    def _normalizar_key_csv(self, value: str) -> str:
        text = str(value or "").strip().lower().lstrip("\ufeff")
        text = (
            text.replace("á", "a")
            .replace("é", "e")
            .replace("í", "i")
            .replace("ó", "o")
            .replace("ú", "u")
        )
        return text

    def _safe_int(self, value, default=0):
        try:
            if value is None or value == "":
                return default
            return int(float(value))
        except Exception:
            return default

    def _normalizar_nombre_dxf(self, value: str) -> str:
        txt = str(value or "").replace("\\", "/").strip().lower()
        txt = os.path.basename(txt)
        txt = " ".join(txt.split())
        return txt

    # =========================================================
    # LISTA DE LARGOS DESDE CSV EN AUTODXF
    # =========================================================
    def _resolver_csv_lista_largos(self, ruta_autodxf: Path):
        candidatos_exactos = [
            "Lista_Perfiles_Clasificados.csv",
            "materiales_input.csv",
            "Lista_Largos.csv",
        ]

        for nombre in candidatos_exactos:
            ruta = ruta_autodxf / nombre
            if ruta.exists() and ruta.is_file():
                return ruta

        try:
            for archivo in sorted(ruta_autodxf.glob("*.csv")):
                nombre = archivo.name.lower()
                if "lista" in nombre and ("perfil" in nombre or "larg" in nombre):
                    return archivo
        except Exception:
            pass

        return None

    def _mapear_columnas_lista_largos(self, fieldnames):
        mapa = {self._normalizar_key_csv(c): c for c in (fieldnames or [])}
        return {
            "nombre": mapa.get("nombre"),
            "clasificacion": mapa.get("clasificacion") or mapa.get("clasificación"),
            "largo_in": mapa.get("largo (in)") or mapa.get("largo"),
            "cantidad": mapa.get("cantidad") or mapa.get("qty"),
            "proceso": mapa.get("proceso"),
        }

    def _leer_csv_lista_largos(self, csv_path: Path):
        encodings = ("utf-8-sig", "cp1252", "latin-1")
        ultimo_error = None

        for enc in encodings:
            try:
                with csv_path.open("r", encoding=enc, newline="") as f:
                    reader = csv.DictReader(f)
                    columnas = self._mapear_columnas_lista_largos(reader.fieldnames or [])

                    if not columnas["nombre"] or not columnas["cantidad"]:
                        raise ValueError(
                            f"CSV sin columnas mínimas esperadas. Detectadas: {reader.fieldnames}"
                        )

                    rows = []
                    for raw in reader:
                        nombre = str(raw.get(columnas["nombre"], "")).strip()
                        clasificacion = str(raw.get(columnas["clasificacion"], "")).strip() if columnas["clasificacion"] else ""
                        largo_txt = str(raw.get(columnas["largo_in"], "0")).strip() if columnas["largo_in"] else "0"
                        cantidad_txt = str(raw.get(columnas["cantidad"], "0")).strip()

                        if not nombre:
                            continue

                        try:
                            largo_in = round(float(largo_txt or 0), 3)
                        except Exception:
                            largo_in = 0.0

                        try:
                            cantidad = int(float(cantidad_txt or 0))
                        except Exception:
                            cantidad = 0

                        proceso = ""
                        if columnas.get("proceso"):
                            proceso = str(raw.get(columnas["proceso"], "")).strip()

                        rows.append({
                            "nombre": nombre,
                            "clasificacion": clasificacion,
                            "largo_in": largo_in,
                            "cantidad": cantidad,
                            "cantidad_base": cantidad,
                            "proceso": proceso,
                        })

                    return rows

            except Exception as e:
                ultimo_error = e

        raise RuntimeError(f"No se pudo leer el CSV '{csv_path}'. Error: {ultimo_error}")

    def _cargar_listas_largos_desde_rutas(self):
        """
        Regresa un grupo por cada AutoDXF detectado en el contexto.
        Si el job no tiene CSV, también se agrega para poder mostrarlo explícitamente.
        """
        if not self.rutas_dxf_actuales:
            return []

        grupos = {}
        vistos_autodxf = set()

        for ruta in self.rutas_dxf_actuales:
            ruta_autodxf = self._resolver_autodxf_desde_ruta(ruta)
            if not ruta_autodxf:
                continue

            clave_autodxf = str(ruta_autodxf).lower()
            if clave_autodxf in vistos_autodxf:
                continue
            vistos_autodxf.add(clave_autodxf)

            job = self._resolver_job_desde_autodxf(ruta_autodxf)
            csv_path = self._resolver_csv_lista_largos(ruta_autodxf)

            grupo = {
                "job": job,
                "ruta_autodxf": str(ruta_autodxf),
                "csv_path": str(csv_path) if csv_path else "",
                "rows": [],
                "status": "sin_csv",
                "mensaje": "No se encontró lista de largos para este job.",
            }

            if csv_path:
                try:
                    rows = self._leer_csv_lista_largos(csv_path)
                    grupo["rows"] = rows
                    grupo["status"] = "ok"
                    grupo["mensaje"] = f"CSV encontrado: {csv_path.name}"
                except Exception as e:
                    grupo["status"] = "error_csv"
                    grupo["mensaje"] = f"No se pudo leer el CSV: {e}"
                    print(f"[TAB_PARTS][LISTA_LARGOS][WARN] No se pudo leer '{csv_path}': {e}")

            grupos[clave_autodxf] = grupo

        return sorted(list(grupos.values()), key=lambda g: str(g.get("job", "")).lower())

    def _crear_bloque_job(self, contenedor, grupo, columnas, encabezados, anchos):
        status = grupo.get("status", "sin_csv")
        if status == "ok":
            color_titulo, texto_status, color_status, color_fondo = "#2563EB", "CON DEMANDA DE LARGOS", "#16A34A", "#F8FAFC"
        elif status == "sin_csv":
            color_titulo, texto_status, color_status, color_fondo = "#DC2626", "SIN DEMANDA DE LARGOS", "#DC2626", "#FEF2F2"
        else:
            color_titulo, texto_status, color_status, color_fondo = "#D97706", "ERROR AL LEER CSV", "#D97706", "#FFFBEB"

        frame_job = make_herinox_card(shadow=False)
        frame_job.setStyleSheet(
            f"QFrame#HerinoxCard{{background:{color_fondo};border:1px solid {COLOR_BORDE};border-radius:12px;}}"
        )
        fj_lay = QVBoxLayout(frame_job)
        hdr = QHBoxLayout()
        lbl_job = QLabel(f"JOB: {grupo['job']}")
        lbl_job.setStyleSheet(f"font-weight:700;color:{color_titulo};")
        hdr.addWidget(lbl_job)
        hdr.addStretch()
        lbl_st = QLabel(texto_status)
        lbl_st.setStyleSheet(f"font-weight:700;color:{color_status};")
        hdr.addWidget(lbl_st)
        fj_lay.addLayout(hdr)
        if status != "ok":
            msg_lbl = QLabel(grupo.get("mensaje", ""))
            msg_lbl.setStyleSheet(f"color:{color_status};")
            fj_lay.addWidget(msg_lbl)
            contenedor.layout().addWidget(frame_job)
            return
        n_rows = len(grupo["rows"])
        table = QTableWidget(n_rows, len(columnas))
        table.setHorizontalHeaderLabels([encabezados[c] for c in columnas])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setAlternatingRowColors(True)
        table.setStyleSheet(
            "QTableWidget{background:#FFFFFF;color:#0F172A;alternate-background-color:#F8FAFC;"
            "gridline-color:#E2E8F0;border:1px solid #E2E8F0;border-radius:8px;}"
            "QTableWidget::item{color:#0F172A;}"
            "QHeaderView::section{background:#F1F5F9;color:#475569;font-weight:700;border:none;"
            "border-bottom:1px solid #E2E8F0;padding:6px;}"
        )
        table.setWordWrap(False)
        hdr = table.horizontalHeader()
        for ci, col in enumerate(columnas):
            hdr.resizeSection(ci, anchos.get(col, 120))
        row_px = 30
        header_px = max(32, hdr.height())
        content_h = n_rows * row_px + header_px + 6
        table.setMinimumHeight(content_h)
        table.setMaximumHeight(content_h)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for ri, row in enumerate(grupo["rows"]):
            from interface.largos_nesting_service import nombre_pieza_largo_display

            table.setItem(
                ri, 0, QTableWidgetItem(nombre_pieza_largo_display(str(row.get("nombre", ""))))
            )
            table.setItem(ri, 1, QTableWidgetItem(str(row.get("clasificacion", ""))))
            table.setItem(ri, 2, QTableWidgetItem(f"{float(row.get('largo_in', 0) or 0):.3f}"))
            table.setItem(ri, 3, QTableWidgetItem(str(row.get("cantidad", 0))))
            table.setItem(ri, 4, QTableWidgetItem(str(row.get("proceso", ""))))
        table.resizeRowsToContents()
        fj_lay.addWidget(table)
        contenedor.layout().addWidget(frame_job)

    def reprocesar_autodxf_partes(self):
        """Reprocesa AutoDXF → Processed Files y refresca PARTS con geometría corregida."""
        vista_files = getattr(self.app, "vista_files", None)
        if vista_files is None or not hasattr(vista_files, "escanear_partes_desde_ruta"):
            QMessageBox.warning(self, "Atención", "No hay módulo FILES disponible para reprocesar.")
            return
        autodxf = None
        if hasattr(vista_files, "_resolver_autodxf_desde_datos_actuales"):
            autodxf = vista_files._resolver_autodxf_desde_datos_actuales()
        if not autodxf:
            QMessageBox.information(
                self,
                "Reprocesar AutoDXF",
                "No se encontró la carpeta AutoDXF del job/lote actual.\n"
                "Importe el job desde FILES o asegúrese de que PARTS tenga rutas DXF válidas.",
            )
            return
        if (
            QMessageBox.question(
                self,
                "Reprocesar AutoDXF",
                "Se volverán a procesar los DXF crudos de:\n"
                f"{autodxf}\n\n"
                "Se actualizará Processed Files y la lista PARTS.\n"
                "El nesting existente no cambia hasta que renestee la placa "
                "con «RENESTEAR ESTA PLACA».\n\n¿Continuar?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        if hasattr(self.app, "abrir_ventana_carga"):
            self.app.abrir_ventana_carga("Reprocesando AutoDXF…")

        def _progress(msg, pct):
            if hasattr(self.app, "actualizar_progreso"):
                call_on_main(self.app.actualizar_progreso, str(msg), float(pct))

        def worker():
            payload = err = None
            try:
                payload = vista_files.escanear_partes_desde_ruta(progress_cb=_progress)
                if not payload:
                    raise RuntimeError(
                        "No se pudo escanear AutoDXF (carpeta vacía o sin DXF válidos)."
                    )
            except Exception as exc:
                err = exc
            call_on_main(self._finalizar_reprocesar_autodxf, payload, err)

        threading.Thread(target=worker, daemon=True).start()

    def _finalizar_reprocesar_autodxf(self, payload, err=None):
        if hasattr(self.app, "cerrar_ventana_carga"):
            self.app.cerrar_ventana_carga()
        if err:
            QMessageBox.critical(self, "Error", f"Error al reprocesar AutoDXF:\n{err}")
            return
        vista_files = getattr(self.app, "vista_files", None)
        if vista_files is None or not payload:
            QMessageBox.critical(self, "Error", "No se pudo completar el reproceso.")
            return
        total = vista_files.aplicar_partes_resincronizadas(payload, thumbnails_async=True)
        # Marca de sesión: el renest de placa puede preferir DXF regenerado.
        self.app.autodxf_reprocesado_pendiente = True
        # Evitar que el cache DXF (mtime Windows) sirva geometría espejada vieja.
        try:
            from modules.nesting_engine.display_geometry import invalidar_cache_dxf

            rutas = {
                str(r[5]).strip()
                for r in (payload.get("items") or [])
                if isinstance(r, (list, tuple)) and len(r) > 5 and r[5]
            }
            n_cache = invalidar_cache_dxf(rutas) if rutas else invalidar_cache_dxf()
            print(f"[AUTODXF] cache DXF invalidado: {n_cache} entradas", flush=True)
        except Exception:
            pass
        QMessageBox.information(
            self,
            "AutoDXF reprocesado",
            f"Se actualizaron {total} pieza(s) en PARTS desde AutoDXF.\n\n"
            "Para corregir un nest afectado:\n"
            "NESTING → cinta Placa → Renestear.\n"
            "El renest usa automáticamente el DXF regenerado.",
        )

    def abrir_ventana_lista_largos(self):
        grupos = self._cargar_listas_largos_desde_rutas()
        if not grupos:
            QMessageBox.information(
                self,
                "Demanda de largos",
                "No se encontraron rutas AutoDXF válidas en el contexto actual.",
            )
            return
        self._mostrar_dialogo_lista_largos(grupos)

    def _mostrar_dialogo_lista_largos(self, grupos):
        dlg = QDialog(self)
        dlg.setWindowTitle("Demanda de largos")
        dlg.resize(1260, 680)
        dlg.setModal(True)
        lay = QVBoxLayout(dlg)
        card = make_herinox_card()
        card_lay = QVBoxLayout(card)
        tit_lbl = QLabel("DEMANDA DE LARGOS")
        tit_lbl.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};font-size:16px;")
        card_lay.addWidget(tit_lbl)
        total_grupos = len(grupos)
        total_ok = sum(1 for g in grupos if g.get("status") == "ok")
        total_sin_csv = sum(1 for g in grupos if g.get("status") == "sin_csv")
        total_error = sum(1 for g in grupos if g.get("status") == "error_csv")
        total_rows = sum(len(g["rows"]) for g in grupos if g.get("status") == "ok")
        card_lay.addWidget(QLabel(
            f"JOBS DETECTADOS: {total_grupos}   |   CON DEMANDA: {total_ok}   |   "
            f"SIN DEMANDA: {total_sin_csv}   |   ERROR LECTURA: {total_error}   |   REGISTROS TOTALES: {total_rows}"
        ))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        scroll.setWidget(inner)
        columnas = ("nombre", "clasificacion", "largo_in", "cantidad", "proceso")
        encabezados = {
            "nombre": "NOMBRE",
            "clasificacion": "CLASIFICACIÓN",
            "largo_in": "LARGO (in)",
            "cantidad": "CANTIDAD",
            "proceso": "PROCESO",
        }
        anchos = {
            "nombre": 320,
            "clasificacion": 180,
            "largo_in": 110,
            "cantidad": 90,
            "proceso": 220,
        }
        for grupo in grupos:
            self._crear_bloque_job(inner, grupo, columnas, encabezados, anchos)
        card_lay.addWidget(scroll, 1)
        lay.addWidget(card)
        self.ventana_lista_largos = dlg
        dlg.exec()
