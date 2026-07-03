# nesting_modals.py — diálogos Qt (paridad con interface/nesting_modals.py)
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
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
from utils_nesting import format_clave_calibre_display


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
    valor_largo_sin: float = 15.0,
) -> tuple[float, float] | None:
    """
    Opciones de separación CU antes de renestear calibre completo.
    Retorna (separacion_in, largo_sin_separacion_in) o None si cancela.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle("Separación entre piezas de cobre")
    dlg.setModal(True)
    dlg.setFixedSize(360, 175)
    dlg.setStyleSheet(surface_dialog_stylesheet())

    lay = QVBoxLayout(dlg)
    tit = QLabel("SEPARACIÓN ENTRE PIEZAS CU", alignment=Qt.AlignmentFlag.AlignCenter)
    tit.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
    lay.addWidget(tit)

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

    row_lim = QHBoxLayout()
    row_lim.addWidget(QLabel("Piezas ≤ este largo (in) sin gap:"))
    spin_lim = QDoubleSpinBox()
    spin_lim.setRange(0.0, 144.0)
    spin_lim.setDecimals(2)
    spin_lim.setSingleStep(0.5)
    spin_lim.setValue(max(0.0, float(valor_largo_sin or 15.0)))
    spin_lim.setFixedWidth(100)
    row_lim.addWidget(spin_lim)
    row_lim.addStretch(1)
    lay.addLayout(row_lim)

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
    return float(spin_sep.value()), float(spin_lim.value())


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

    kerf_actual = str(getattr(parent, "_kerf_efectivo", lambda: getattr(parent, "global_kerf_val", 0.3))())
    try:
        kf = float(kerf_actual)
        if kf <= 0:
            kerf_actual = "0.3"
    except Exception:
        kerf_actual = str(getattr(parent, "global_kerf_val", 0.3) or 0.3)

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
            kerf_val = float(ent_kerf.text())
            margin_val = float(ent_margin.text())
            if kerf_val <= 0:
                kerf_val = 0.3
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
