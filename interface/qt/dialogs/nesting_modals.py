# nesting_modals.py — diálogos Qt (paridad con interface/nesting_modals.py)
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from interface.nesting_costos import aplicar_totales_a_tab, calcular_reporte_costos
from interface.qt.theme import (
    COLOR_ACENTO,
    COLOR_BORDE,
    COLOR_EXITO,
    COLOR_GRIS_DARK,
    COLOR_TEXTO_SECUNDARIO,
    COLOR_TEXTO_TITULO,
    apply_push_button,
    surface_dialog_stylesheet,
)
from interface.utils_nesting import (
    _espesor_pulgadas_desde_texto,
    es_material_cobre,
    format_clave_calibre_display,
)


def _centrar_dialogo(dlg: QDialog, parent: QWidget) -> None:
    if parent is None:
        return
    geo = parent.frameGeometry()
    center = geo.center()
    fg = dlg.frameGeometry()
    fg.moveCenter(center)
    dlg.move(fg.topLeft())


def preguntar_separacion_cobre_renest(
    parent,
    valor_sep: float = 0.375,
    valor_largo_sin: float = 0.0,
) -> tuple[float, float] | None:
    """
    Separación CU antes de renestear. El umbral por largo ya no aplica
    (gap siempre salvo Z/especial). Retorna (separacion_in, 0.0) o None.
    """
    _ = valor_largo_sin
    dlg = QDialog(parent)
    dlg.setWindowTitle("Separación entre piezas de cobre")
    dlg.setModal(True)
    dlg.setFixedSize(360, 140)
    dlg.setStyleSheet(surface_dialog_stylesheet())

    lay = QVBoxLayout(dlg)
    tit = QLabel("SEPARACIÓN ENTRE PIEZAS CU", alignment=Qt.AlignmentFlag.AlignCenter)
    tit.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
    lay.addWidget(tit)

    hint = QLabel("Gap 3/8\" fijo (salvo Z/especial PARTS). Solo ajusta el gap.")
    hint.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-size:11px;")
    hint.setWordWrap(True)
    lay.addWidget(hint)

    row_sep = QHBoxLayout()
    row_sep.addWidget(QLabel("Separación entre piezas (in):"))
    spin_sep = QDoubleSpinBox()
    spin_sep.setRange(0.0, 24.0)
    spin_sep.setDecimals(4)
    spin_sep.setSingleStep(0.0625)
    spin_sep.setValue(max(0.0, float(valor_sep or 0.375)))
    spin_sep.setFixedWidth(100)
    row_sep.addWidget(spin_sep)
    row_sep.addStretch(1)
    lay.addLayout(row_sep)

    btns = QHBoxLayout()
    btn_cancel = QPushButton("Cancelar")
    btn_ok = QPushButton("Continuar")
    apply_push_button(btn_ok, COLOR_ACENTO, font_size=11)
    apply_push_button(btn_cancel, COLOR_GRIS_DARK, font_size=11)
    btns.addStretch(1)
    btns.addWidget(btn_cancel)
    btns.addWidget(btn_ok)
    lay.addLayout(btns)

    btn_cancel.clicked.connect(dlg.reject)
    btn_ok.clicked.connect(dlg.accept)
    _centrar_dialogo(dlg, parent)

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return float(spin_sep.value()), 0.0


def preguntar_barras_cobre_renest(
    parent,
    barras: list[dict],
) -> list[int] | None:
    """
    Selector de barras madre CU para renesteo parcial.
    `barras` = [{idx, label}, ...]. Retorna índices seleccionados o None.
    """
    if not barras:
        return None

    dlg = QDialog(parent)
    dlg.setWindowTitle("Renestear por barra")
    dlg.setModal(True)
    dlg.setMinimumWidth(420)
    dlg.setMinimumHeight(280)
    dlg.setStyleSheet(surface_dialog_stylesheet())

    lay = QVBoxLayout(dlg)
    tit = QLabel("SELECCIONE BARRAS A RENESTEAR", alignment=Qt.AlignmentFlag.AlignCenter)
    tit.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
    lay.addWidget(tit)
    hint = QLabel(
        "Misma configuración de gap/umbral que el renesteo de calibre, "
        "aplicada solo a las barras elegidas."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-size:11px;")
    lay.addWidget(hint)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    inner = QWidget()
    inner_lay = QVBoxLayout(inner)
    inner_lay.setContentsMargins(4, 4, 4, 4)
    checks: list[tuple[QCheckBox, int]] = []
    for b in barras:
        idx = int(b.get("idx", -1))
        label = str(b.get("label") or f"Barra #{idx + 1}")
        cb = QCheckBox(label)
        cb.setChecked(False)
        inner_lay.addWidget(cb)
        checks.append((cb, idx))
    inner_lay.addStretch(1)
    scroll.setWidget(inner)
    lay.addWidget(scroll, 1)

    row_sel = QHBoxLayout()
    btn_all = QPushButton("Todas")
    btn_none = QPushButton("Ninguna")
    apply_push_button(btn_all, COLOR_GRIS_DARK, font_size=10)
    apply_push_button(btn_none, COLOR_GRIS_DARK, font_size=10)
    row_sel.addWidget(btn_all)
    row_sel.addWidget(btn_none)
    row_sel.addStretch(1)
    lay.addLayout(row_sel)

    btns = QHBoxLayout()
    btn_cancel = QPushButton("Cancelar")
    btn_ok = QPushButton("Continuar")
    apply_push_button(btn_ok, COLOR_ACENTO, font_size=11)
    apply_push_button(btn_cancel, COLOR_GRIS_DARK, font_size=11)
    btns.addStretch(1)
    btns.addWidget(btn_cancel)
    btns.addWidget(btn_ok)
    lay.addLayout(btns)

    btn_all.clicked.connect(lambda: [c.setChecked(True) for c, _ in checks])
    btn_none.clicked.connect(lambda: [c.setChecked(False) for c, _ in checks])
    btn_cancel.clicked.connect(dlg.reject)
    btn_ok.clicked.connect(dlg.accept)
    _centrar_dialogo(dlg, parent)

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    selected = [idx for cb, idx in checks if cb.isChecked() and idx >= 0]
    return selected or None


def abrir_modal_configuracion(parent):
    dlg = QDialog(parent)
    dlg.setWindowTitle("Configuración Global")
    dlg.setModal(True)
    dlg.setFixedSize(390, 270)
    dlg.setStyleSheet(surface_dialog_stylesheet())

    lay = QVBoxLayout(dlg)
    tit = QLabel("CONFIGURACIÓN GLOBAL", alignment=Qt.AlignmentFlag.AlignCenter)
    tit.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
    lay.addWidget(tit)

    kerf_actual = str(getattr(parent, "_kerf_efectivo", lambda: getattr(parent, "global_kerf_val", 0.15))())
    try:
        kf = float(kerf_actual)
        if kf <= 0:
            kerf_actual = "0.15"
    except Exception:
        kerf_actual = str(getattr(parent, "global_kerf_val", 0.15) or 0.15)

    row_k = QHBoxLayout()
    row_k.addWidget(QLabel("Kerf (in):"))
    ent_kerf = QLineEdit(kerf_actual)
    ent_kerf.setFixedWidth(90)
    row_k.addWidget(ent_kerf)
    lay.addLayout(row_k)

    row_m = QHBoxLayout()
    row_m.addWidget(QLabel("Margen de borde (in):"))
    ent_margin = QLineEdit(str(parent.global_margin_val))
    ent_margin.setFixedWidth(90)
    row_m.addWidget(ent_margin)
    lay.addLayout(row_m)

    def guardar_y_aplicar():
        try:
            from modules.nesting_engine.nest_poka_yoke import (
                validar_kerf_in,
                validar_margin_in,
            )

            kerf_val, err_k = validar_kerf_in(ent_kerf.text())
            margin_val, err_m = validar_margin_in(ent_margin.text())
            if err_k or err_m:
                QMessageBox.critical(
                    dlg,
                    "Configuración inválida (poka-yoke)",
                    "\n".join(x for x in (err_k, err_m) if x),
                )
                return
            parent.global_margin_val = margin_val
            parent.global_kerf_val = kerf_val
            if hasattr(parent, "_sync_kerf_widget"):
                parent._sync_kerf_widget()
            else:
                try:
                    parent.ent_kerf.setText(str(kerf_val))
                except Exception:
                    pass
            dlg.accept()
            parent.ejecutar_nesting()
        except Exception:
            QMessageBox.critical(dlg, "Error", "Kerf y Margen deben ser valores numéricos.")

    btn = QPushButton("GUARDAR Y APLICAR")
    apply_push_button(btn, COLOR_GRIS_DARK, font_size=12)
    btn.clicked.connect(guardar_y_aplicar)
    lay.addWidget(btn)

    _centrar_dialogo(dlg, parent)
    dlg.exec()


class CostosOrdenDialog(QDialog):
    """Ventana no modal: costos del nesteo actual (mismo estilo que escenarios WO)."""

    def __init__(self, parent, reporte: dict, tc: float, meta_tc: dict):
        super().__init__(parent)
        self._parent_tab = parent
        self.setWindowTitle("Costos del nesteo actual")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(False)
        self.resize(480, 560)
        self.setStyleSheet(surface_dialog_stylesheet())

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(10)

        tit = QLabel("COSTOS DEL NESTEO ACTUAL", alignment=Qt.AlignmentFlag.AlignCenter)
        tit.setStyleSheet(f"font-weight:700;font-size:15px;color:{COLOR_TEXTO_TITULO};")
        root.addWidget(tit)
        sub = QLabel(
            "Refleja movimientos, renest y switches de compra · no es el historial WO",
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-size:11px;")
        root.addWidget(sub)

        total_mxn = float(reporte.get("total_mxn") or 0.0)
        total_usd = (total_mxn / tc) if tc > 0 else 0.0
        card = QWidget()
        card.setObjectName("LightCard")
        card.setStyleSheet(
            f"QWidget#LightCard{{background:#FFFFFF;border:2px solid #3B82F6;"
            f"border-radius:12px;}}"
        )
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(16, 14, 16, 14)
        lbl_mxn = QLabel(f"${total_mxn:,.2f} MXN", alignment=Qt.AlignmentFlag.AlignCenter)
        lbl_mxn.setStyleSheet(f"font-size:24px;font-weight:800;color:{COLOR_EXITO};")
        lbl_usd = QLabel(f"${total_usd:,.2f} USD", alignment=Qt.AlignmentFlag.AlignCenter)
        lbl_usd.setStyleSheet(f"font-size:14px;font-weight:600;color:{COLOR_TEXTO_SECUNDARIO};")
        card_l.addWidget(lbl_mxn)
        card_l.addWidget(lbl_usd)
        root.addWidget(card)

        row_split = QHBoxLayout()
        row_split.setSpacing(8)
        emp = float(reporte.get("total_empresa_mxn") or 0.0)
        placas = int(reporte.get("placas_total") or 0)
        barras_lg = int(reporte.get("barras_largos_total") or 0)
        for txt, val, col, es_moneda in (
            ("Stock interno", emp, "#0EA5E9", True),
            ("Placas", placas, COLOR_ACENTO, False),
            ("Perfiles MRL", barras_lg, "#EA580C", False),
        ):
            val_txt = f"${val:,.0f}" if es_moneda else str(int(val))
            box = QLabel(f"{txt}\n{val_txt}")
            box.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.setStyleSheet(
                f"background:#FFFFFF;border:1px solid {COLOR_BORDE};border-radius:10px;"
                f"padding:10px;font-size:11px;font-weight:600;color:{col};"
            )
            row_split.addWidget(box)
        root.addLayout(row_split)

        tc_txt = f"TC {tc:,.4f} ({meta_tc.get('fuente', 'FALLBACK')})"
        if meta_tc.get("actualizado"):
            tc_txt += f" · {meta_tc['actualizado']}"
        lbl_tc = QLabel(tc_txt, alignment=Qt.AlignmentFlag.AlignCenter)
        lbl_tc.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-size:10px;")
        root.addWidget(lbl_tc)

        lbl_des = QLabel("Por calibre / material y largos")
        lbl_des.setStyleSheet(f"font-weight:700;font-size:12px;color:{COLOR_TEXTO_TITULO};")
        root.addWidget(lbl_des)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(8)
        lineas = reporte.get("lineas") or []
        if lineas:
            for item in lineas:
                row_w = QWidget()
                row_w.setObjectName("LightCard")
                row_w.setStyleSheet(
                    f"QWidget#LightCard{{background:#FFFFFF;border:1px solid {COLOR_BORDE};"
                    f"border-radius:12px;}}"
                )
                rl = QVBoxLayout(row_w)
                rl.setContentsMargins(14, 12, 14, 12)
                top = QHBoxLayout()
                etiqueta = str(item.get("etiqueta") or "")
                if etiqueta == "LARGOS":
                    badge_style = (
                        "font-size:10px;font-weight:700;color:#C2410C;background:#FFF7ED;"
                        "padding:2px 6px;border-radius:4px;"
                    )
                else:
                    badge_style = (
                        "font-size:10px;font-weight:700;color:#1D4ED8;background:#EFF6FF;"
                        "padding:2px 6px;border-radius:4px;"
                    )
                badge = QLabel(f"[{etiqueta}]")
                badge.setStyleSheet(badge_style)
                top.addWidget(badge)
                nom = QLabel(str(item.get("clave_display") or item.get("clave") or ""))
                nom.setWordWrap(True)
                nom.setStyleSheet(f"font-weight:600;font-size:12px;color:{COLOR_TEXTO_TITULO};")
                top.addWidget(nom, 1)
                rl.addLayout(top)
                n_pl = int(item.get("placas") or 0)
                costo = float(item.get("costo_mxn") or 0.0)
                usd = (costo / tc) if tc > 0 else 0.0
                unidad = "barra(s)" if item.get("tipo_linea") == "largos" else "placa(s)"
                det = QLabel(f"{n_pl} {unidad} · ${costo:,.2f} MXN · ${usd:,.2f} USD")
                det.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-weight:600;font-size:11px;")
                rl.addWidget(det)
                inner_lay.addWidget(row_w)
        else:
            vacio = QLabel("Sin placas con costo en el nesteo actual.", alignment=Qt.AlignmentFlag.AlignCenter)
            vacio.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};padding:16px;")
            inner_lay.addWidget(vacio)
        inner_lay.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        btn_ref = QPushButton("ACTUALIZAR")
        apply_push_button(btn_ref, COLOR_GRIS_DARK, font_size=11, padding="10px 16px")
        btn_ref.clicked.connect(self._refrescar)
        root.addWidget(btn_ref)

        _centrar_dialogo(self, parent)

    def _refrescar(self):
        p = self._parent_tab
        if p is None:
            return
        self.close()
        abrir_modal_costos(p)

    def closeEvent(self, event):
        p = self._parent_tab
        if p is not None and getattr(p, "_dlg_costos", None) is self:
            p._dlg_costos = None
        super().closeEvent(event)


def abrir_modal_costos(parent):
    """Muestra costos en vivo del nesteo actual (independiente de HISTORIAL DE W.O.)."""
    dlg = getattr(parent, "_dlg_costos", None)
    if dlg is not None:
        try:
            dlg.raise_()
            dlg.activateWindow()
            return
        except RuntimeError:
            parent._dlg_costos = None

    if hasattr(parent, "_actualizar_tipo_cambio"):
        parent._actualizar_tipo_cambio()

    resultados = getattr(parent.app, "resultados_nesting", None) or {}
    reporte = calcular_reporte_costos(
        resultados,
        app=parent.app,
        lote_idx=int(getattr(parent, "lote_actual_idx", 0) or 0),
        tab=parent,
    )
    tc = float(getattr(parent, "tipo_cambio_usdmxn", 18.50) or 18.50)
    aplicar_totales_a_tab(parent, reporte, tc)

    meta_tc = {
        "fuente": str(getattr(parent, "tipo_cambio_fuente", "FALLBACK")),
        "actualizado": str(getattr(parent, "tipo_cambio_actualizado", "") or ""),
    }

    dlg = CostosOrdenDialog(parent, reporte, tc, meta_tc)
    parent._dlg_costos = dlg
    dlg.show()


def mostrar_modal_escenarios(parent, escenarios_resultados):
    if hasattr(parent.app, "cerrar_ventana_carga"):
        parent.app.cerrar_ventana_carga()
    parent.btn_run_nest.setEnabled(True)

    dlg = QDialog(parent)
    dlg.setWindowTitle("Análisis MES de Lotes")
    dlg.setModal(True)
    dlg.resize(750, 570)
    dlg.setStyleSheet(surface_dialog_stylesheet())

    lay = QVBoxLayout(dlg)
    tit = QLabel("ANÁLISIS DE RENDIMIENTO - WORK ORDERS", alignment=Qt.AlignmentFlag.AlignCenter)
    tit.setStyleSheet(f"font-weight:700;font-size:15px;color:{COLOR_TEXTO_TITULO};")
    lay.addWidget(tit)
    sub = QLabel(
        "Estrategias de corte optimizadas para minimizar el costo operativo.",
        alignment=Qt.AlignmentFlag.AlignCenter,
    )
    sub.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};")
    lay.addWidget(sub)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    inner = QWidget()
    inner_lay = QVBoxLayout(inner)

    for idx, item in enumerate(escenarios_resultados[:5]):
        lotes_str = " + ".join([f"{mult} Lote(s) de {k}X" for k, mult in item["config"]])
        card = QWidget()
        card.setObjectName("LightCard")
        card.setStyleSheet(
            f"QWidget#LightCard{{background:#FFFFFF;border:{'2' if idx == 0 else '1'}px solid "
            f"{'#3B82F6' if idx == 0 else COLOR_BORDE};border-radius:12px;}}"
        )
        card_lay = QHBoxLayout(card)
        txt = QWidget()
        txt_lay = QVBoxLayout(txt)
        title_prefix = "RECOMENDADO: " if idx == 0 else f"Opción {idx+1}: "
        lbl_t = QLabel(f"{title_prefix}{lotes_str}")
        lbl_t.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
        txt_lay.addWidget(lbl_t)
        lbl_d = QLabel(f"Eficiencia: {item['efi']:.1f}%  |  Costo Estimado: ${item['costo']:,.2f}")
        lbl_d.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-weight:600;")
        txt_lay.addWidget(lbl_d)
        card_lay.addWidget(txt, 1)

        def crear_comando(resultado_aislado):
            resultado_clonado = copy.deepcopy(resultado_aislado)
            return lambda: parent.aplicar_escenario_seleccionado(resultado_clonado, dlg)

        btn_sel = QPushButton("SELECCIONAR")
        apply_push_button(btn_sel, COLOR_GRIS_DARK, font_size=11, padding="8px 16px")
        btn_sel.clicked.connect(crear_comando(item["resultados"]))
        card_lay.addWidget(btn_sel)
        inner_lay.addWidget(card)

    scroll.setWidget(inner)
    lay.addWidget(scroll, 1)

    _centrar_dialogo(dlg, parent)
    dlg.exec()


def _sufijo_placa_duplicada(hojas_disp, hoja, idx):
    pid = str(hoja.get("placa_id", "") or "")
    if not pid or hoja.get("es_retazo"):
        return ""
    iguales = [
        j for j, h in enumerate(hojas_disp)
        if str(h.get("placa_id", "") or "") == pid and not h.get("es_retazo")
    ]
    if len(iguales) <= 1:
        return ""
    return f" · P{iguales.index(idx) + 1}"


def _poblar_destinos_transferencia(inner_lay, group, entries, var_destino, *, excluir_hoja=None):
    destinos = 0
    for i, entry in enumerate(entries):
        hoja = entry["hoja"]
        if excluir_hoja is not None and hoja is excluir_hoja:
            continue
        hojas_ctx = entry.get("hojas_ctx") or []
        h_idx = int(entry.get("hoja_idx", i))
        wo = str(entry.get("wo_label") or "").strip()
        efi_dir = float(hoja.get("eficiencia_directa", hoja.get("eficiencia", 0)) or 0)
        efi_real = float(hoja.get("eficiencia_real", efi_dir) or 0)
        nombre_placa = hoja.get("placa_id", f"Placa #{h_idx + 1}")
        if hoja.get("es_retazo", False):
            nombre_placa = f"{nombre_placa} (RTZ)"
        w_in = float(hoja.get("placa_w", 0) or 0) / 25.4
        h_in = float(hoja.get("placa_h", 0) or 0) / 25.4
        sufijo_dup = _sufijo_placa_duplicada(hojas_ctx, hoja, h_idx)
        prefijo_wo = f"{wo} · " if wo else ""
        texto_principal = f"{prefijo_wo}{nombre_placa}{sufijo_dup}  ({w_in:.0f}\" x {h_in:.0f}\")"
        color_eficiencia = "#10B981" if efi_dir > 70 else ("#F59E0B" if efi_dir > 40 else "#EF4444")

        row = QHBoxLayout()
        rb = QRadioButton(texto_principal)
        group.addButton(rb, i)
        rb.toggled.connect(lambda checked, idx=i: var_destino.update({"idx": idx}) if checked else None)
        row.addWidget(rb)
        lbl = QLabel(f"Dir {efi_dir:.1f}% | Real {efi_real:.1f}%")
        lbl.setStyleSheet(f"color:{color_eficiencia};font-weight:700;")
        row.addWidget(lbl)
        inner_lay.addLayout(row)
        destinos += 1
    return destinos


def _build_transfer_dialog(parent, piezas_sel, entries, titulo, on_confirm):
    multi = len(piezas_sel) > 1
    dlg = QDialog(parent)
    dlg.setWindowTitle("Mudar Piezas" if multi else "Mudar Pieza")
    dlg.setModal(True)
    dlg.resize(520, 580 if multi else 550)
    dlg.setStyleSheet(surface_dialog_stylesheet())

    lay = QVBoxLayout(dlg)
    lbl_tit = QLabel(titulo, alignment=Qt.AlignmentFlag.AlignCenter)
    lbl_tit.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
    lay.addWidget(lbl_tit)

    if multi:
        lay.addWidget(QLabel("Piezas seleccionadas (Ctrl + clic):", alignment=Qt.AlignmentFlag.AlignCenter))
        lista_nombres = "\n".join(f"• {p.get('nombre', 'Pieza')}" for p in piezas_sel[:8])
        if len(piezas_sel) > 8:
            lista_nombres += f"\n• ... y {len(piezas_sel) - 8} más"
        lay.addWidget(QLabel(lista_nombres, alignment=Qt.AlignmentFlag.AlignCenter))
    else:
        lay.addWidget(QLabel("Pieza seleccionada:", alignment=Qt.AlignmentFlag.AlignCenter))
        lay.addWidget(QLabel(piezas_sel[0].get("nombre", ""), alignment=Qt.AlignmentFlag.AlignCenter))

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    inner = QWidget()
    inner_lay = QVBoxLayout(inner)
    group = QButtonGroup(dlg)
    var_destino = {"idx": -1}

    _poblar_destinos_transferencia(
        inner_lay,
        group,
        entries,
        var_destino,
        excluir_hoja=getattr(parent, "hoja_actual_data", None),
    )

    scroll.setWidget(inner)
    lay.addWidget(scroll, 1)

    btn_conf = QPushButton("✅ CONFIRMAR TRANSFERENCIA")
    apply_push_button(btn_conf, COLOR_GRIS_DARK, font_size=11)
    btn_conf.clicked.connect(lambda: on_confirm(var_destino["idx"], entries, dlg))
    lay.addWidget(btn_conf)

    _centrar_dialogo(dlg, parent)
    dlg.exec()


def abrir_modal_transferencia(parent):
    piezas_sel = getattr(parent, "piezas_seleccionadas", None) or []
    if not piezas_sel and not parent.info_pieza_seleccionada:
        return
    if not piezas_sel and parent.info_pieza_seleccionada:
        piezas_sel = [parent.info_pieza_seleccionada]

    clave = getattr(parent, "clave_actual", None)
    hoja_origen = parent.hoja_actual_data
    if not clave or not hoja_origen:
        return
    entries = parent._destinos_transferencia_placa(clave, hoja_origen)
    if not entries:
        QMessageBox.information(
            parent,
            "Aviso",
            "No hay otras placas (ni en otras Work Orders) de este material para realizar la transferencia.",
        )
        return

    multi = len(piezas_sel) > 1
    titulo = (
        f"MUDAR {len(piezas_sel)} PIEZAS A OTRA PLACA"
        if multi
        else "MUDAR PIEZA A OTRA PLACA"
    )
    _build_transfer_dialog(parent, piezas_sel, entries, titulo, parent.ejecutar_transferencia)


def abrir_modal_transferencia_masiva(parent, clave, hoja_origen):
    if not hoja_origen or hoja_origen.get("es_retazo", False):
        QMessageBox.information(
            parent,
            "Aviso",
            "Esta acción solo aplica a placas madre (no RTZ / mini-nest).",
        )
        return

    bloque = parent._desglosar_bloque_placa_mini(clave, hoja_origen)
    resumen = bloque.get("resumen_base") or {}
    total_piezas = sum(int(v) for v in resumen.values())
    if total_piezas <= 0:
        QMessageBox.warning(parent, "Atención", "La placa seleccionada no tiene piezas reales para mover.")
        return

    entries = parent._destinos_transferencia_placa(clave, hoja_origen)
    if not entries:
        QMessageBox.information(
            parent,
            "Aviso",
            "No hay otras placas madre (ni en otras Work Orders) de este material para recibir piezas.",
        )
        return

    placa_origen = str(hoja_origen.get("placa_id", "Placa") or "Placa")
    titulo = f"CAMBIAR PIEZAS A OTRA PLACA\nOrigen: {placa_origen}  |  Piezas: {total_piezas}"

    dlg = QDialog(parent)
    dlg.setWindowTitle("Cambiar piezas a otra placa")
    dlg.setModal(True)
    dlg.resize(520, 580)
    dlg.setStyleSheet(surface_dialog_stylesheet())

    lay = QVBoxLayout(dlg)
    lbl_tit = QLabel(titulo, alignment=Qt.AlignmentFlag.AlignCenter)
    lbl_tit.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
    lay.addWidget(lbl_tit)
    hint = "Se moverán todas las piezas que quepan en la placa destino."
    if len(getattr(parent.app, "resultados_multilote", None) or []) > 1:
        hint += " También puedes elegir placas de otra Work Order activa."
    lay.addWidget(QLabel(hint, alignment=Qt.AlignmentFlag.AlignCenter))

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    inner = QWidget()
    inner_lay = QVBoxLayout(inner)
    group = QButtonGroup(dlg)
    var_destino = {"idx": -1}

    destinos = _poblar_destinos_transferencia(inner_lay, group, entries, var_destino)

    scroll.setWidget(inner)
    lay.addWidget(scroll, 1)

    btn_conf = QPushButton("✅ MOVER PIEZAS POSIBLES")
    apply_push_button(btn_conf, COLOR_GRIS_DARK, font_size=11)
    btn_conf.clicked.connect(
        lambda: parent.ejecutar_transferencia_masiva(
            var_destino["idx"], entries, hoja_origen, clave, dlg
        )
    )
    lay.addWidget(btn_conf)

    _centrar_dialogo(dlg, parent)
    dlg.exec()


def mostrar_modal_comparacion_motores(parent, bundle) -> str | None:
    """
    Opción B: tabla comparativa de motores. Devuelve engine_id elegido o None si cancela.
    """
    from modules.nesting_engine.engine_compare import comparison_rows_for_ui
    from modules.nesting_engine.nest_engine_context import (
        ENGINE_ARGA_BASE,
        normalize_engine_id,
    )

    rows = comparison_rows_for_ui(bundle)
    if not rows:
        return normalize_engine_id(ENGINE_ARGA_BASE)

    dlg = QDialog(parent)
    dlg.setWindowTitle("Comparación de motores de nesting")
    dlg.setMinimumSize(820, 420)
    dlg.setStyleSheet(surface_dialog_stylesheet())

    lay = QVBoxLayout(dlg)
    titulo = QLabel("Seleccione el motor a usar para este nesteo (acero / placas)")
    titulo.setStyleSheet(
        f"color:{COLOR_TEXTO_TITULO};font-size:14px;font-weight:700;background:transparent;"
    )
    lay.addWidget(titulo)

    hint = QLabel(
        "Se ejecutaron todos los motores en paralelo. El cobre sigue en su módulo externo. "
        "Los motores pendientes aparecen deshabilitados hasta su fase."
    )
    hint.setWordWrap(True)
    hint.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-size:11px;background:transparent;")
    lay.addWidget(hint)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    inner = QWidget()
    inner_lay = QVBoxLayout(inner)

    group = QButtonGroup(dlg)
    selected_id = {"value": normalize_engine_id(bundle.selected_engine_id)}

    def _status_label(status: str, ready: bool) -> str:
        if status == "ok":
            return "Listo"
        if status == "pending":
            return "Pendiente"
        if status == "error":
            return "Error"
        return "No disponible"

    for row in rows:
        eid = row["engine_id"]
        enabled = bool(row.get("ready")) and row.get("status") == "ok"
        radio = QRadioButton(
            f"{row['display_name']}  —  {_status_label(row.get('status'), row.get('ready'))}"
        )
        radio.setEnabled(enabled)
        radio.setStyleSheet("color:#0F172A;font-size:12px;font-weight:600;")
        if row.get("selected") and enabled:
            radio.setChecked(True)
            selected_id["value"] = eid
        elif not any(r.get("selected") for r in rows) and eid == ENGINE_ARGA_BASE and enabled:
            radio.setChecked(True)
            selected_id["value"] = eid

        detail = (
            f"Fase {row.get('phase', '?')}  |  "
            f"Hojas: {row.get('hojas', 0)}  |  "
            f"Efi. prom: {row.get('eficiencia_promedio', 0.0):.1f}%  |  "
            f"Piezas: {row.get('piezas_colocadas', 0)}  |  "
            f"Pend.: {row.get('piezas_pendientes', 0)}  |  "
            f"Costo: ${row.get('costo_total', 0.0):,.2f}  |  "
            f"Tiempo: {row.get('elapsed_s', 0.0):.1f}s"
        )
        if row.get("error"):
            detail += f"\n{row['error']}"

        sub = QLabel(detail)
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-size:10px;margin-left:22px;")

        group.addButton(radio)
        radio.toggled.connect(
            lambda checked, engine=eid: selected_id.update({"value": engine}) if checked else None
        )
        inner_lay.addWidget(radio)
        inner_lay.addWidget(sub)

    inner_lay.addStretch()
    scroll.setWidget(inner)
    lay.addWidget(scroll, 1)

    btn_row = QHBoxLayout()
    btn_cancel = QPushButton("Cancelar")
    apply_push_button(btn_cancel, "#64748B", font_size=11)
    btn_ok = QPushButton("Usar motor seleccionado")
    apply_push_button(btn_ok, COLOR_EXITO, font_size=11, font_weight=700)
    btn_row.addStretch()
    btn_row.addWidget(btn_cancel)
    btn_row.addWidget(btn_ok)
    lay.addLayout(btn_row)

    result = {"engine_id": None}

    def _accept():
        result["engine_id"] = normalize_engine_id(selected_id["value"])
        dlg.accept()

    def _reject():
        dlg.reject()

    btn_ok.clicked.connect(_accept)
    btn_cancel.clicked.connect(_reject)
    _centrar_dialogo(dlg, parent)

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return result["engine_id"]


def _format_key_in(w_in: float, h_in: float) -> str:
    a, b = sorted((round(float(w_in), 3), round(float(h_in), 3)))
    return f"{a:.3f}x{b:.3f}"


def _coinciden_placa(val1, val2) -> bool:
    try:
        from modules.nesting_engine.manager import MotorNesting

        return bool(MotorNesting._coinciden(val1, val2))
    except Exception:
        return str(val1 or "").strip().upper() == str(val2 or "").strip().upper()


def agrupar_formatos_placas_inventario(
    datos_placas: list,
    *,
    calibre: str | None = None,
    material: str | None = None,
) -> list[dict]:
    """Agrupa inventario por formato (pulgadas). Opcionalmente filtra por calibre/material."""
    grupos: dict[str, dict] = {}
    cal_f = str(calibre or "").strip()
    mat_f = str(material or "").strip()
    for placa in datos_placas or []:
        try:
            if len(placa) < 5:
                continue
            cal = str(placa[0] or "").strip()
            mat = str(placa[1] or "").strip()
            if cal_f and not _coinciden_placa(cal_f, cal):
                continue
            if mat_f and not _coinciden_placa(mat_f, mat):
                continue
            w_in = float(placa[3])
            h_in = float(placa[4])
            if w_in <= 0 or h_in <= 0:
                continue
            key = _format_key_in(w_in, h_in)
            precio = float(placa[6] or 0) if len(placa) > 6 else 0.0
            g = grupos.get(key)
            if g is None:
                grupos[key] = {
                    "key": key,
                    "w_in": w_in,
                    "h_in": h_in,
                    "count": 1,
                    "precio_min": precio if precio > 0 else 0.0,
                    "calibres": {cal} if cal else set(),
                    "materiales": {mat} if mat else set(),
                    "rows": [placa],
                }
            else:
                g["count"] += 1
                g["rows"].append(placa)
                if precio > 0 and (g["precio_min"] <= 0 or precio < g["precio_min"]):
                    g["precio_min"] = precio
                if cal:
                    g["calibres"].add(cal)
                if mat:
                    g["materiales"].add(mat)
        except Exception:
            continue

    out = list(grupos.values())
    out.sort(key=lambda g: (g["w_in"] * g["h_in"], g["precio_min"]))
    return out


def _grupos_requeridos_piezas(datos_partes: list | None) -> list[dict]:
    """Agrupa piezas por calibre+material (excluye cobre/largos)."""
    grupos: dict[str, dict] = {}
    for fila in datos_partes or []:
        try:
            if len(fila) < 4:
                continue
            material = str(fila[1] or "").strip()
            if es_material_cobre(material):
                continue
            calibre = str(fila[3] or "").strip()
            if not calibre and not material:
                continue
            try:
                qty = int(fila[2] or 0)
            except Exception:
                qty = 1
            key = f"{calibre}|{material}".upper()
            g = grupos.get(key)
            if g is None:
                grupos[key] = {
                    "calibre": calibre,
                    "material": material,
                    "qty_piezas": max(0, qty),
                    "n_skus": 1,
                }
            else:
                g["qty_piezas"] += max(0, qty)
                g["n_skus"] += 1
        except Exception:
            continue

    out = list(grupos.values())

    def _sort_key(g: dict):
        esp = _espesor_pulgadas_desde_texto(g.get("calibre") or "")
        return (
            esp if esp is not None else float("inf"),
            str(g.get("material") or "").upper(),
            str(g.get("calibre") or "").upper(),
        )

    out.sort(key=_sort_key)
    return out


def _etiqueta_formato_catalogo(g: dict) -> str:
    precio = float(g.get("precio_min") or 0)
    precio_txt = f"desde ${precio:,.0f}" if precio > 0 else "sin precio"
    return (
        f'{g["w_in"]:.1f}" × {g["h_in"]:.1f}"'
        f'  ·  stock {g["count"]}  ·  {precio_txt}'
    )


def filtrar_datos_placas_nest_selection(datos_placas: list, selection: dict | None) -> list:
    """
    selection:
      {"mode": "auto"}
      {"mode": "manual", "items": [{"key", "w_in", "h_in", "qty", "calibre?", "material?"}, ...]}
    """
    if not selection or selection.get("mode") == "auto":
        return list(datos_placas or [])
    items = selection.get("items") or []
    if not items:
        return list(datos_placas or [])

    by_key: dict[str, list] = {}
    for placa in datos_placas or []:
        try:
            key = _format_key_in(float(placa[3]), float(placa[4]))
            by_key.setdefault(key, []).append(placa)
        except Exception:
            continue

    filtradas: list = []
    for item in items:
        key = str(item.get("key") or _format_key_in(item.get("w_in", 0), item.get("h_in", 0)))
        rows = list(by_key.get(key) or [])
        cal = str(item.get("calibre") or "").strip()
        mat = str(item.get("material") or "").strip()
        if cal or mat:
            filtered = []
            for placa in rows:
                try:
                    if cal and not _coinciden_placa(cal, placa[0]):
                        continue
                    if mat and not _coinciden_placa(mat, placa[1]):
                        continue
                    filtered.append(placa)
                except Exception:
                    continue
            rows = filtered
        if not rows:
            continue
        # Catálogo: una o más filas del formato bastan. NUNCA limitar hojas por qty.
        filtradas.extend(rows)
    return filtradas


def preguntar_seleccion_placas_nesting(
    parent,
    datos_placas: list,
    *,
    engine_label: str = "SVGNest Ultra",
    datos_partes: list | None = None,
) -> dict | None:
    """
    Selector de placas al Ejecutar Nesting (Ultra).

    Primero lista los calibres/materiales que exigen las piezas; en cada uno,
    un desplegable con placas compatibles del catálogo.
    Retorna dict selection, o None si cancela.
    """
    if not agrupar_formatos_placas_inventario(datos_placas):
        QMessageBox.warning(
            parent,
            "Sin placas",
            "No hay placas DISPONIBLE en inventario para este nesting.",
        )
        return None

    requerimientos = _grupos_requeridos_piezas(datos_partes)
    if not requerimientos:
        # Sin piezas tipables: un bloque genérico con todo el catálogo.
        requerimientos = [{"calibre": "", "material": "", "qty_piezas": 0, "n_skus": 0}]

    dlg = QDialog(parent)
    dlg.setWindowTitle("Selección de placas — Nesting")
    dlg.setModal(True)
    dlg.resize(680, 580)
    dlg.setStyleSheet(surface_dialog_stylesheet())

    lay = QVBoxLayout(dlg)
    tit = QLabel(
        f"PLACAS PARA {engine_label.upper()}",
        alignment=Qt.AlignmentFlag.AlignCenter,
    )
    tit.setStyleSheet(f"font-weight:800;font-size:14px;color:{COLOR_TEXTO_TITULO};")
    lay.addWidget(tit)

    info = QLabel(
        "Calibres y materiales que exigen las piezas. En cada uno elige "
        "placa(s) del catálogo (más rápido). O usa Selección Auto "
        "(inventario completo por precio/acomodo), más lento."
    )
    info.setWordWrap(True)
    info.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-size:11px;")
    lay.addWidget(info)

    scroll = QScrollArea()
    scroll.setObjectName("AppScroll")
    scroll.setWidgetResizable(True)
    inner = QWidget()
    inner_lay = QVBoxLayout(inner)
    inner_lay.setSpacing(10)

    group_ui: list[dict] = []

    def _add_plate_pick_row(host_lay: QVBoxLayout, formatos: list[dict], picks: list) -> None:
        row_w = QWidget()
        row = QHBoxLayout(row_w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        combo = QComboBox()
        combo.setMinimumWidth(280)
        combo.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};")
        for g in formatos:
            combo.addItem(_etiqueta_formato_catalogo(g), g)
        if not formatos:
            combo.addItem("(Sin placas compatibles en catálogo)", None)
            combo.setEnabled(False)

        unlimited = QCheckBox("Sin límite")
        unlimited.setChecked(True)

        spin = QSpinBox()
        spin.setRange(1, 999)
        spin.setValue(1)
        spin.setEnabled(False)
        spin.setFixedWidth(70)

        def _toggle_lim(_checked=False, u=unlimited, s=spin):
            s.setEnabled(not u.isChecked())

        unlimited.toggled.connect(_toggle_lim)

        row.addWidget(QLabel("Placa:"), 0)
        row.addWidget(combo, 1)
        row.addWidget(unlimited, 0)
        row.addWidget(QLabel("Cant:"), 0)
        row.addWidget(spin, 0)
        host_lay.addWidget(row_w)
        picks.append({"combo": combo, "unlimited": unlimited, "spin": spin})

    for req in requerimientos:
        cal = str(req.get("calibre") or "").strip()
        mat = str(req.get("material") or "").strip()
        formatos = agrupar_formatos_placas_inventario(
            datos_placas, calibre=cal or None, material=mat or None
        )
        # Si el filtro deja vacío (p.ej. material normalizado distinto), mostrar catálogo completo.
        if not formatos and (cal or mat):
            formatos = agrupar_formatos_placas_inventario(datos_placas)

        card = QFrame()
        card.setObjectName("HerinoxCard")
        card.setStyleSheet(
            f"QFrame#HerinoxCard {{ background:#FFFFFF; border:1px solid {COLOR_BORDE}; "
            f"border-radius:8px; }}"
        )
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(12, 10, 12, 10)
        card_lay.setSpacing(8)

        if cal or mat:
            clave = f"{cal}_{mat}" if cal and mat else (cal or mat)
            titulo_req = format_clave_calibre_display(clave) or clave
        else:
            titulo_req = "Catálogo general"
        qty_pz = int(req.get("qty_piezas") or 0)
        n_skus = int(req.get("n_skus") or 0)
        if qty_pz > 0:
            sub = f"{qty_pz} pieza(s)"
            if n_skus > 0:
                sub += f" · {n_skus} SKU(s)"
            hdr_txt = f"{titulo_req}  ·  {sub}"
        else:
            hdr_txt = titulo_req

        hdr = QLabel(hdr_txt)
        hdr.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};font-weight:700;font-size:12px;")
        card_lay.addWidget(hdr)

        picks_host = QVBoxLayout()
        picks_host.setSpacing(6)
        card_lay.addLayout(picks_host)

        picks: list[dict] = []
        _add_plate_pick_row(picks_host, formatos, picks)

        btn_add = QPushButton("+ Otra placa del catálogo")
        apply_push_button(btn_add, COLOR_GRIS_DARK, font_size=10, font_weight=600)
        btn_add.setEnabled(bool(formatos))

        def _on_add(_checked=False, lay_p=picks_host, fmts=formatos, pk=picks):
            _add_plate_pick_row(lay_p, fmts, pk)

        btn_add.clicked.connect(_on_add)
        card_lay.addWidget(btn_add, alignment=Qt.AlignmentFlag.AlignLeft)

        if not formatos:
            warn = QLabel("No hay placas compatibles en inventario para este calibre/material.")
            warn.setStyleSheet("color:#B45309;font-size:11px;")
            warn.setWordWrap(True)
            card_lay.addWidget(warn)

        inner_lay.addWidget(card)
        group_ui.append(
            {
                "calibre": cal,
                "material": mat,
                "picks": picks,
                "formatos": formatos,
            }
        )

    inner_lay.addStretch()
    scroll.setWidget(inner)
    lay.addWidget(scroll, 1)

    btn_row = QHBoxLayout()
    btn_cancel = QPushButton("Cancelar")
    apply_push_button(btn_cancel, "#64748B", font_size=11)
    btn_auto = QPushButton("Selección Auto")
    apply_push_button(btn_auto, COLOR_ACENTO, font_size=11, font_weight=700)
    btn_ok = QPushButton("Usar placas seleccionadas")
    apply_push_button(btn_ok, COLOR_EXITO, font_size=11, font_weight=700)
    btn_row.addWidget(btn_cancel)
    btn_row.addStretch()
    btn_row.addWidget(btn_auto)
    btn_row.addWidget(btn_ok)
    lay.addLayout(btn_row)

    result: dict = {"value": None}

    def _accept_manual():
        items = []
        seen: set[tuple] = set()
        for g in group_ui:
            for pick in g["picks"]:
                data = pick["combo"].currentData()
                if not isinstance(data, dict):
                    continue
                key = str(data.get("key") or "")
                dedupe = (key, g["calibre"], g["material"])
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                qty = None if pick["unlimited"].isChecked() else int(pick["spin"].value())
                items.append(
                    {
                        "key": key,
                        "w_in": float(data["w_in"]),
                        "h_in": float(data["h_in"]),
                        "qty": qty,
                        "calibre": g["calibre"],
                        "material": g["material"],
                    }
                )
        if not items:
            QMessageBox.warning(
                dlg,
                "Selección vacía",
                "Elige al menos una placa del catálogo por calibre, o usa Selección Auto.",
            )
            return
        result["value"] = {"mode": "manual", "items": items}
        dlg.accept()

    def _accept_auto():
        warn = QMessageBox.warning(
            dlg,
            "Selección Auto — tiempo alto",
            "Selección Auto usa el inventario completo y elige placas por "
            "precio + acomodo (modo por defecto).\n\n"
            "Esto puede tardar MUCHO más tiempo a costa de la selección "
            "definitiva de placas.\n\n"
            "¿Continuar con Selección Auto?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if warn != QMessageBox.StandardButton.Yes:
            return
        result["value"] = {"mode": "auto"}
        dlg.accept()

    btn_ok.clicked.connect(_accept_manual)
    btn_auto.clicked.connect(_accept_auto)
    btn_cancel.clicked.connect(dlg.reject)
    _centrar_dialogo(dlg, parent)

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return result["value"]

