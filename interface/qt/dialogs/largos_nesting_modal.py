"""Ventana emergente — NESTEO DE LARGOS (visor + pedido MRL unitario)."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from interface.largos_nesting_service import (
    bar_key,
    cargar_plan_largos_contexto,
    guardar_exclusiones_mrl_unidades,
    iter_barras_plan,
    listar_unidades_mrl_plan,
    obtener_exclusiones_mrl_unidades,
    previsualizar_pedido_mrl,
    resumir_plan_largos,
    vista_barra_para_unidad_mrl,
    preparar_barra_para_canvas,
)
from interface.qt.layout_helpers import make_card, make_scroll, make_scroll_content
from interface.qt.thread_bridge import call_on_main
from interface.qt.theme import COLOR_TEXTO_TITULO, surface_dialog_stylesheet
from interface.qt.widgets.herinox_switch import HerinoxSwitch
from interface.qt.widgets.largos_tira_canvas import LargosTiraCanvas


def _truncar_etiqueta(txt: str, max_len: int = 78) -> str:
    t = str(txt or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


def _etiqueta_unidad_mrl(u: dict, catalogo) -> str:
    from catalogo_largos import descripcion_herinox_mrl, etiqueta_tipo_perfil_mrl

    mat_raw = str(u.get("material") or "")
    cod = str(u.get("codigo") or "—")
    tipo_lbl = etiqueta_tipo_perfil_mrl(mat_raw, cod, catalogo=catalogo)
    try:
        largo = float(u.get("largo") or 0)
    except Exception:
        largo = 0.0
    unit_idx = int(u.get("unit_idx") or 1)
    cant_grupo = int(u.get("cant_grupo") or 1)
    desc = descripcion_herinox_mrl(cod, mat_raw, catalogo=catalogo)
    base = f"#{unit_idx}/{cant_grupo}  ·  {tipo_lbl}  ·  {cod}  ·  {largo:.0f}\""
    if desc:
        return f"{base}  ·  {desc}"
    return base


def abrir_nesting_largos(tab):
    dlg = getattr(tab, "_dlg_nesting_largos", None)
    if dlg is not None:
        try:
            dlg.raise_()
            dlg.activateWindow()
            return
        except RuntimeError:
            tab._dlg_nesting_largos = None
    dlg = LargosNestingDialog(tab)
    tab._dlg_nesting_largos = dlg
    dlg.show()


class LargosNestingDialog(QDialog):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.app = tab.app
        self.plan: dict = {}
        self._mrl_unit_switches: dict[str, HerinoxSwitch] = {}
        self._mrl_unit_btns: dict[str, QPushButton] = {}
        self._incluir_mrl: dict[str, bool] = {}
        self._mrl_unidades: list[dict] = []
        self._barra_sel_key: str | None = None
        self._mrl_sel_key: str | None = None
        self._barra_vista_actual: dict | None = None
        self._material_vista_actual = ""
        self._barra_lookup: dict[str, tuple[str, dict]] = {}
        self._contexto: dict = {}

        self.setWindowTitle("Nesteo de largos — ARGA NESTING SUITE")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(False)
        self.setMinimumSize(960, 620)
        self.resize(1320, 820)
        self.setStyleSheet(surface_dialog_stylesheet())

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        hdr = QHBoxLayout()
        tit = QLabel("NESTEO DE LARGOS")
        tit.setStyleSheet(f"font-weight:700;font-size:15px;color:{COLOR_TEXTO_TITULO};")
        hdr.addWidget(tit)
        hdr.addStretch()
        self.lbl_orden = QLabel("")
        self.lbl_orden.setStyleSheet("color:#64748B;font-weight:600;font-size:11px;")
        hdr.addWidget(self.lbl_orden)
        root.addLayout(hdr)

        body = QSplitter(Qt.Orientation.Vertical)
        body.setChildrenCollapsible(False)

        # --- Superior: visor gráfico compacto ---
        visor_card = make_card()
        visor_lay = QVBoxLayout(visor_card)
        visor_lay.setContentsMargins(8, 6, 8, 6)
        visor_lay.setSpacing(2)
        visor_lbl = QLabel("DISTRIBUCIÓN EN TIRA")
        visor_lbl.setStyleSheet(
            f"font-weight:700;font-size:11px;color:{COLOR_TEXTO_TITULO};padding-bottom:0;"
        )
        visor_lay.addWidget(visor_lbl)
        self.canvas = LargosTiraCanvas()
        self.canvas.pieza_click.connect(self._on_pieza_canvas_click)
        visor_lay.addWidget(self.canvas)
        body.addWidget(visor_card)

        # --- Inferior: switches (izq) + tabla resumen (der) ---
        bottom = QSplitter(Qt.Orientation.Horizontal)
        bottom.setChildrenCollapsible(False)

        izq = make_card()
        izq_lay = QVBoxLayout(izq)
        izq_lay.setContentsMargins(10, 10, 10, 10)
        izq_lay.setSpacing(6)

        mrl_hdr = QHBoxLayout()
        mrl_tit = QLabel("MATERIAL REQUERIDO")
        mrl_tit.setStyleSheet(f"font-weight:700;font-size:13px;color:{COLOR_TEXTO_TITULO};")
        mrl_hdr.addWidget(mrl_tit)
        mrl_hdr.addStretch()
        self.lbl_mrl_total = QLabel("—")
        self.lbl_mrl_total.setStyleSheet(
            "font-weight:800;font-size:18px;color:#1D4ED8;background:#EFF6FF;"
            "padding:4px 12px;border-radius:8px;"
        )
        mrl_hdr.addWidget(self.lbl_mrl_total)
        izq_lay.addLayout(mrl_hdr)

        self.lbl_mrl_sub = QLabel(
            "Marca Pedir/No por barra · se guarda al cerrar · se envía al exportar nesting"
        )
        self.lbl_mrl_sub.setStyleSheet("color:#64748B;font-size:10px;")
        izq_lay.addWidget(self.lbl_mrl_sub)

        self.mrl_units_scroll = make_scroll()
        self._mrl_units_inner, self._mrl_units_layout = make_scroll_content()
        self._mrl_units_layout.setSpacing(3)
        self.mrl_units_scroll.setWidget(self._mrl_units_inner)
        izq_lay.addWidget(self.mrl_units_scroll, 1)
        bottom.addWidget(izq)

        der = make_card()
        der_lay = QVBoxLayout(der)
        der_lay.setContentsMargins(10, 10, 10, 10)
        der_lay.setSpacing(6)

        res_hdr = QHBoxLayout()
        res_tit = QLabel("RESUMEN AGREGADO")
        res_tit.setStyleSheet(f"font-weight:700;font-size:13px;color:{COLOR_TEXTO_TITULO};")
        res_hdr.addWidget(res_tit)
        res_hdr.addStretch()
        der_lay.addLayout(res_hdr)

        self.lbl_resumen_sub = QLabel("Barras totales a material_requerido_ldg")
        self.lbl_resumen_sub.setStyleSheet("color:#64748B;font-size:10px;")
        der_lay.addWidget(self.lbl_resumen_sub)

        self.tbl_mrl = QTableWidget(0, 4)
        self.tbl_mrl.setHorizontalHeaderLabels(["MATERIAL", "CÓDIGO", "LARGO", "CANT"])
        self.tbl_mrl.verticalHeader().setVisible(False)
        self.tbl_mrl.verticalHeader().setDefaultSectionSize(26)
        self.tbl_mrl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_mrl.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.tbl_mrl.setAlternatingRowColors(True)
        self.tbl_mrl.setShowGrid(True)
        self.tbl_mrl.setStyleSheet(
            "QTableWidget{background:#FFFFFF;color:#0F172A;alternate-background-color:#F8FAFC;"
            "border:1px solid #E2E8F0;border-radius:8px;font-size:11px;"
            "gridline-color:#E2E8F0;}"
            "QHeaderView::section{background:#F1F5F9;color:#475569;font-weight:700;"
            "border:none;border-bottom:1px solid #E2E8F0;border-right:1px solid #E2E8F0;"
            "padding:5px 8px;font-size:10px;}"
        )
        th = self.tbl_mrl.horizontalHeader()
        th.setStretchLastSection(False)
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        th.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        th.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        th.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.tbl_mrl.setColumnWidth(1, 76)
        self.tbl_mrl.setColumnWidth(2, 62)
        self.tbl_mrl.setColumnWidth(3, 46)
        der_lay.addWidget(self.tbl_mrl, 1)
        bottom.addWidget(der)

        bottom.setStretchFactor(0, 3)
        bottom.setStretchFactor(1, 2)
        body.addWidget(bottom)

        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setSizes([230, 520])
        root.addWidget(body, 1)

        self._cargar_plan_async()

    def _lote_idx(self) -> int:
        try:
            return int(getattr(self.tab, "lote_actual_idx", 0) or 0)
        except Exception:
            return 0

    def _persistir_exclusiones(self):
        guardar_exclusiones_mrl_unidades(self.app, self._lote_idx(), self._unidades_excluidas_mrl())

    def _unidades_excluidas_mrl(self) -> set[str]:
        excl = set()
        for key, incluir in self._incluir_mrl.items():
            if key in self._mrl_unit_switches and not incluir:
                excl.add(key)
        return excl

    def closeEvent(self, event):
        self._persistir_exclusiones()
        if getattr(self.tab, "_dlg_nesting_largos", None) is self:
            self.tab._dlg_nesting_largos = None
        super().closeEvent(event)

    def reject(self):
        self.close()

    def _cargar_plan_async(self):
        self.lbl_mrl_total.setText("…")
        self.lbl_orden.setText("")

        def worker():
            plan, contexto, err = {}, {}, None
            try:
                plan, contexto, err = cargar_plan_largos_contexto(self.app, self.tab)
            except Exception as e:
                err = str(e)
            call_on_main(self._aplicar_plan, plan, contexto, err)

        threading.Thread(target=worker, daemon=True).start()

    def _limpiar_mrl_units(self):
        while self._mrl_units_layout.count():
            item = self._mrl_units_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._mrl_unit_switches.clear()
        self._mrl_unit_btns.clear()
        self._incluir_mrl.clear()

    def _reconstruir_lookup_barras(self):
        self._barra_lookup.clear()
        for material, bar_idx, barra in iter_barras_plan(self.plan):
            key = bar_key(material, bar_idx)
            self._barra_lookup[key] = (material, barra)

    def _aplicar_plan(self, plan: dict, contexto: dict, err: str | None):
        self._contexto = dict(contexto or {})
        self.lbl_orden.setText(str(self._contexto.get("etiqueta") or ""))

        if err:
            self.lbl_mrl_total.setText("—")
            self.lbl_mrl_sub.setText(err)
            return

        self.plan = plan or {}
        if not (self.plan.get("data") or {}):
            self.lbl_mrl_sub.setText("Sin tiras de largo para esta orden.")
            return

        self._limpiar_mrl_units()
        self._reconstruir_lookup_barras()
        excl_mrl = obtener_exclusiones_mrl_unidades(self.app, self._lote_idx())
        self._mrl_unidades = listar_unidades_mrl_plan(self.plan)

        from catalogo_largos import _cargar_placas_largos_desde_herinox

        catalogo_mrl = _cargar_placas_largos_desde_herinox(solo_disponibles=False)

        for u in self._mrl_unidades:
            key = str(u.get("key") or "")
            etiqueta = _etiqueta_unidad_mrl(u, catalogo_mrl)

            fila = QFrame()
            fila.setStyleSheet("background:#F8FAFC;border-radius:8px;")
            fl = QHBoxLayout(fila)
            fl.setContentsMargins(8, 4, 8, 4)
            fl.setSpacing(6)

            btn = QPushButton(_truncar_etiqueta(etiqueta))
            btn.setToolTip(etiqueta)
            btn.setStyleSheet(
                "background:#1E293B;color:#F8FAFC;border:none;border-radius:6px;"
                "padding:6px 8px;font-weight:600;text-align:left;font-size:11px;"
            )
            btn.clicked.connect(lambda _c=False, k=key: self._seleccionar_unidad_mrl(k))
            fl.addWidget(btn, 1)
            self._mrl_unit_btns[key] = btn

            sw = HerinoxSwitch(label_on="Pedir", label_off="No", checked=key not in excl_mrl)
            sw.toggled.connect(lambda checked, k=key: self._toggle_unidad_mrl(k, checked))
            fl.addWidget(sw)
            self._mrl_unit_switches[key] = sw
            self._incluir_mrl[key] = key not in excl_mrl
            self._mrl_units_layout.addWidget(fila)

        self._actualizar_tabla_mrl()

        if self._mrl_unidades:
            self._seleccionar_unidad_mrl(str(self._mrl_unidades[0].get("key") or ""))
        elif self._barra_lookup:
            self._seleccionar_barra(next(iter(self._barra_lookup.keys())))

    def _actualizar_tabla_mrl(self):
        from catalogo_largos import _cargar_placas_largos_desde_herinox, etiqueta_tipo_perfil_mrl

        excl = self._unidades_excluidas_mrl()
        filas = previsualizar_pedido_mrl(self.plan, unidades_excluidas_mrl=excl) if self.plan else []
        catalogo = _cargar_placas_largos_desde_herinox(solo_disponibles=False)
        self.tbl_mrl.setRowCount(len(filas))
        total = 0
        align_c = Qt.AlignmentFlag.AlignCenter
        align_l = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        for ri, fila in enumerate(filas):
            mat_raw = str(fila.get("material") or "")
            cod = str(fila.get("codigo") or "—")
            tipo = etiqueta_tipo_perfil_mrl(mat_raw, cod, catalogo=catalogo)
            try:
                largo = float(fila.get("largo") or 0)
            except Exception:
                largo = 0.0
            cant = int(fila.get("cantidad") or 0)
            total += cant

            tipo_item = QTableWidgetItem(tipo)
            tipo_item.setTextAlignment(align_l)
            self.tbl_mrl.setItem(ri, 0, tipo_item)

            cod_item = QTableWidgetItem(cod)
            cod_item.setTextAlignment(align_c)
            self.tbl_mrl.setItem(ri, 1, cod_item)

            largo_item = QTableWidgetItem(f'{largo:.0f}"')
            largo_item.setTextAlignment(align_c)
            self.tbl_mrl.setItem(ri, 2, largo_item)

            qty_item = QTableWidgetItem(str(cant))
            qty_item.setTextAlignment(align_c)
            self.tbl_mrl.setItem(ri, 3, qty_item)

        total_posible = int(resumir_plan_largos(self.plan).get("mrl_barras_total") or total)
        self.lbl_mrl_total.setText(f"{total} / {total_posible}")
        self.lbl_mrl_sub.setText(
            f"Pedir {total} de {total_posible} barras · guardado al cerrar · envío al exportar nesting"
        )
        self.lbl_resumen_sub.setText(
            f"{len(filas)} tipo(s) de material · {total} barra(s) en pedido"
        )

    def _toggle_unidad_mrl(self, key: str, incluir: bool):
        self._incluir_mrl[key] = bool(incluir)
        self._persistir_exclusiones()
        self._actualizar_tabla_mrl()

    def _seleccionar_unidad_mrl(self, key: str):
        self._mrl_sel_key = key
        for k, btn in self._mrl_unit_btns.items():
            if k == key:
                btn.setStyleSheet(
                    "background:#1D4ED8;color:white;border:none;border-radius:6px;"
                    "padding:6px 8px;font-weight:600;text-align:left;font-size:11px;"
                )
            else:
                btn.setStyleSheet(
                    "background:#1E293B;color:#F8FAFC;border:none;border-radius:6px;"
                    "padding:6px 8px;font-weight:600;text-align:left;font-size:11px;"
                )
        nesting_key = None
        u_data = None
        for u in self._mrl_unidades:
            if str(u.get("key") or "") == key:
                nesting_key = u.get("nesting_key")
                u_data = u
                break
        if nesting_key and nesting_key in self._barra_lookup and u_data:
            material, barra = self._barra_lookup[nesting_key]
            vista = vista_barra_para_unidad_mrl(
                barra,
                float(u_data.get("largo") or 0),
                int(u_data.get("unit_idx") or 1),
                reparto_greedy=bool(u_data.get("reparto_greedy")),
                n_unidades_tira=int(u_data.get("n_unidades_tira") or 1),
                cant_grupo=int(u_data.get("cant_grupo") or 1),
            )
            cod = str(u_data.get("codigo") or "")
            if cod:
                vista["_vista_codigo"] = cod
            self._barra_sel_key = str(nesting_key)
            self._barra_vista_actual = vista
            self._material_vista_actual = material
            self.canvas.mostrar_barra(material, vista)
        elif self._barra_lookup:
            self._seleccionar_barra(next(iter(self._barra_lookup.keys())))

    def _on_pieza_canvas_click(self, idx: int):
        if self._barra_vista_actual is not None:
            self.canvas.mostrar_barra(
                self._material_vista_actual, self._barra_vista_actual, pieza_sel=idx
            )

    def _seleccionar_barra(self, key: str):
        self._barra_sel_key = key
        material, barra = self._barra_lookup.get(key, ("", None))
        cod = ""
        for u in self._mrl_unidades:
            if str(u.get("nesting_key") or "") == key:
                cod = str(u.get("codigo") or "")
                break
        vista = preparar_barra_para_canvas(barra, codigo=cod) if barra else None
        self._barra_vista_actual = vista
        self._material_vista_actual = material
        self.canvas.mostrar_barra(material, vista)
