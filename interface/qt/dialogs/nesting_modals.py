# nesting_modals.py — diálogos Qt (paridad con interface/nesting_modals.py)
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
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

from interface.qt.theme import (
    COLOR_BORDE,
    COLOR_GRIS_DARK,
    COLOR_TEXTO_SECUNDARIO,
    COLOR_TEXTO_TITULO,
    apply_push_button,
    surface_dialog_stylesheet,
)


def _centrar_dialogo(dlg: QDialog, parent: QWidget) -> None:
    if parent is None:
        return
    geo = parent.frameGeometry()
    center = geo.center()
    fg = dlg.frameGeometry()
    fg.moveCenter(center)
    dlg.move(fg.topLeft())


def abrir_modal_configuracion(parent):
    dlg = QDialog(parent)
    dlg.setWindowTitle("Configuración Global")
    dlg.setModal(True)
    dlg.setFixedSize(390, 270)
    dlg.setStyleSheet(surface_dialog_stylesheet())

    lay = QVBoxLayout(dlg)
    tit = QLabel("⚙️ CONFIGURACIÓN GLOBAL", alignment=Qt.AlignmentFlag.AlignCenter)
    tit.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
    lay.addWidget(tit)

    kerf_actual = ""
    try:
        kerf_actual = str(float(parent.ent_kerf.text()))
    except Exception:
        kerf_actual = str(getattr(parent, "global_kerf_val", 0.3))

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
            parent.global_margin_val = margin_val
            parent.global_kerf_val = kerf_val
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


def abrir_modal_costos(parent):
    dlg = QDialog(parent)
    dlg.setWindowTitle("Reporte Económico del Proyecto")
    dlg.setModal(True)
    dlg.resize(480, 550)
    dlg.setStyleSheet(surface_dialog_stylesheet())

    lay = QVBoxLayout(dlg)
    tit = QLabel("💲 RESUMEN DE INVERSIÓN", alignment=Qt.AlignmentFlag.AlignCenter)
    tit.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
    lay.addWidget(tit)

    tc = float(getattr(parent, "tipo_cambio_usdmxn", 18.50) or 18.50)
    fuente_tc = str(getattr(parent, "tipo_cambio_fuente", "FALLBACK"))
    ts_tc = str(getattr(parent, "tipo_cambio_actualizado", ""))

    lay.addWidget(QLabel(f"MXN: ${parent.costo_mxn_val:,.2f}", alignment=Qt.AlignmentFlag.AlignCenter))
    lay.addWidget(QLabel(f"USD: ${parent.costo_usd_val:,.2f}", alignment=Qt.AlignmentFlag.AlignCenter))
    lay.addWidget(QLabel(f"TC DOF usado: {tc:,.4f} MXN/USD ({fuente_tc})", alignment=Qt.AlignmentFlag.AlignCenter))
    if ts_tc:
        lay.addWidget(QLabel(f"Actualizado: {ts_tc}", alignment=Qt.AlignmentFlag.AlignCenter))

    total_mxn_empresa = 0.0
    total_mxn_proveedor = 0.0
    desglose_UI = []

    if hasattr(parent.app, "resultados_nesting") and parent.app.resultados_nesting:
        for clave, info in parent.app.resultados_nesting.items():
            costo_mat_emp = 0.0
            costo_mat_prov = 0.0
            for hoja in info.get("hojas", []):
                if hoja.get("es_retazo", False) or hoja.get("ignorar_deduccion"):
                    continue
                precio = hoja.get("precio_placa", 0.0)
                origen = hoja.get("origen_placa", "EMPRESA")
                if origen == "PROVEEDOR":
                    costo_mat_prov += precio
                else:
                    costo_mat_emp += precio
            total_mxn_empresa += costo_mat_emp
            total_mxn_proveedor += costo_mat_prov
            costo_total_mat = costo_mat_emp + costo_mat_prov
            if costo_mat_emp > 0 and costo_mat_prov == 0:
                etiqueta = "🏢 [EMP]"
            elif costo_mat_prov > 0 and costo_mat_emp == 0:
                etiqueta = "🚚 [PROV]"
            elif costo_mat_prov > 0 and costo_mat_emp > 0:
                etiqueta = "🏢/🚚 [MIX]"
            else:
                etiqueta = "📦 [RET]"
            desglose_UI.append({"clave": clave, "etiqueta": etiqueta, "total": costo_total_mat})

    lay.addWidget(QLabel(f"🏢 Stock Interno: ${total_mxn_empresa:,.2f} MXN", alignment=Qt.AlignmentFlag.AlignCenter))
    lay.addWidget(QLabel(f"🚚 Gasto Proveedor: ${total_mxn_proveedor:,.2f} MXN", alignment=Qt.AlignmentFlag.AlignCenter))
    lay.addWidget(QLabel("📦 DESGLOSE POR MATERIAL"))

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    inner = QWidget()
    inner_lay = QVBoxLayout(inner)
    if desglose_UI:
        for item in desglose_UI:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{item['etiqueta']} {item['clave']}"))
            total_mxn = float(item["total"] or 0.0)
            total_usd = (total_mxn / tc) if tc > 0 else 0.0
            row.addWidget(
                QLabel(f"${total_mxn:,.2f} MXN  |  ${total_usd:,.2f} USD"),
                alignment=Qt.AlignmentFlag.AlignRight,
            )
            inner_lay.addLayout(row)
    else:
        inner_lay.addWidget(QLabel("No hay datos de nesting calculados."))
    scroll.setWidget(inner)
    lay.addWidget(scroll, 1)

    btn_cerrar = QPushButton("CERRAR REPORTE")
    apply_push_button(btn_cerrar, "#FFFFFF", font_size=11)
    btn_cerrar.clicked.connect(dlg.accept)
    lay.addWidget(btn_cerrar)

    _centrar_dialogo(dlg, parent)
    dlg.exec()


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
    tit = QLabel("⚡ ANÁLISIS DE RENDIMIENTO - WORK ORDERS", alignment=Qt.AlignmentFlag.AlignCenter)
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
        title_prefix = "🏆 RECOMENDADO: " if idx == 0 else f"Opción {idx+1}: "
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


def _build_transfer_dialog(parent, piezas_sel, hojas_disp, titulo, on_confirm):
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

    idx_actual = -1
    for i, h in enumerate(hojas_disp):
        if h is parent.hoja_actual_data:
            idx_actual = i

    for i, hoja in enumerate(hojas_disp):
        if i == idx_actual:
            continue
        efi_dir = float(hoja.get("eficiencia_directa", hoja.get("eficiencia", 0)) or 0)
        efi_real = float(hoja.get("eficiencia_real", efi_dir) or 0)
        nombre_placa = hoja.get("placa_id", f"Placa #{i+1}")
        if hoja.get("es_retazo", False):
            nombre_placa = f"{nombre_placa} (RTZ)"
        w_in = float(hoja.get("placa_w", 0) or 0) / 25.4
        h_in = float(hoja.get("placa_h", 0) or 0) / 25.4
        sufijo_dup = _sufijo_placa_duplicada(hojas_disp, hoja, i)
        texto_principal = f"◼ {nombre_placa}{sufijo_dup}  ({w_in:.0f}\" x {h_in:.0f}\")"
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

    scroll.setWidget(inner)
    lay.addWidget(scroll, 1)

    btn_conf = QPushButton("✅ CONFIRMAR TRANSFERENCIA")
    apply_push_button(btn_conf, COLOR_GRIS_DARK, font_size=11)
    btn_conf.clicked.connect(lambda: on_confirm(var_destino["idx"], hojas_disp, dlg))
    lay.addWidget(btn_conf)

    _centrar_dialogo(dlg, parent)
    dlg.exec()


def abrir_modal_transferencia(parent):
    piezas_sel = getattr(parent, "piezas_seleccionadas", None) or []
    if not piezas_sel and not parent.info_pieza_seleccionada:
        return
    if not piezas_sel and parent.info_pieza_seleccionada:
        piezas_sel = [parent.info_pieza_seleccionada]

    hojas_disp = parent.app.resultados_nesting.get(parent.clave_actual, {}).get("hojas", [])
    if len(hojas_disp) <= 1:
        QMessageBox.information(parent, "Aviso", "No hay otras placas de este mismo material para realizar la transferencia.")
        return

    multi = len(piezas_sel) > 1
    titulo = (
        f"🔄 MUDAR {len(piezas_sel)} PIEZAS A OTRA PLACA"
        if multi
        else "🔄 MUDAR PIEZA A OTRA PLACA"
    )
    _build_transfer_dialog(parent, piezas_sel, hojas_disp, titulo, parent.ejecutar_transferencia)


def abrir_modal_transferencia_masiva(parent, clave, hoja_origen):
    if not hoja_origen or hoja_origen.get("es_retazo", False):
        QMessageBox.information(
            parent,
            "Aviso",
            "Esta acción solo aplica a placas madre (no RTZ / mini-nest).",
        )
        return

    hojas_disp = parent.app.resultados_nesting.get(clave, {}).get("hojas", [])
    if len(hojas_disp) <= 1:
        QMessageBox.information(parent, "Aviso", "No hay otras placas de este mismo material para recibir piezas.")
        return

    bloque = parent._desglosar_bloque_placa_mini(clave, hoja_origen)
    resumen = bloque.get("resumen_base") or {}
    total_piezas = sum(int(v) for v in resumen.values())
    if total_piezas <= 0:
        QMessageBox.warning(parent, "Atención", "La placa seleccionada no tiene piezas reales para mover.")
        return

    placa_origen = str(hoja_origen.get("placa_id", "Placa") or "Placa")
    titulo = f"📦 CAMBIAR PIEZAS A OTRA PLACA\nOrigen: {placa_origen}  |  Piezas: {total_piezas}"

    dlg = QDialog(parent)
    dlg.setWindowTitle("Cambiar piezas a otra placa")
    dlg.setModal(True)
    dlg.resize(520, 580)
    dlg.setStyleSheet(surface_dialog_stylesheet())

    lay = QVBoxLayout(dlg)
    lbl_tit = QLabel(titulo, alignment=Qt.AlignmentFlag.AlignCenter)
    lbl_tit.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
    lay.addWidget(lbl_tit)
    lay.addWidget(QLabel("Se moverán todas las piezas que quepan en la placa destino.", alignment=Qt.AlignmentFlag.AlignCenter))

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    inner = QWidget()
    inner_lay = QVBoxLayout(inner)
    group = QButtonGroup(dlg)
    var_destino = {"idx": -1}

    idx_actual = -1
    for i, h in enumerate(hojas_disp):
        if h is hoja_origen:
            idx_actual = i

    destinos = 0
    for i, hoja in enumerate(hojas_disp):
        if i == idx_actual:
            continue
        if hoja.get("es_retazo", False):
            continue
        destinos += 1
        efi_dir = float(hoja.get("eficiencia_directa", hoja.get("eficiencia", 0)) or 0)
        efi_real = float(hoja.get("eficiencia_real", efi_dir) or 0)
        nombre_placa = hoja.get("placa_id", f"Placa #{i+1}")
        w_in = float(hoja.get("placa_w", 0) or 0) / 25.4
        h_in = float(hoja.get("placa_h", 0) or 0) / 25.4
        sufijo_dup = _sufijo_placa_duplicada(hojas_disp, hoja, i)
        texto_principal = f"◼ {nombre_placa}{sufijo_dup}  ({w_in:.0f}\" x {h_in:.0f}\")"
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

    if destinos <= 0:
        QMessageBox.information(
            parent,
            "Aviso",
            "No hay otras placas madre de este material para recibir piezas.",
        )
        return

    scroll.setWidget(inner)
    lay.addWidget(scroll, 1)

    btn_conf = QPushButton("✅ MOVER PIEZAS POSIBLES")
    apply_push_button(btn_conf, COLOR_GRIS_DARK, font_size=11)
    btn_conf.clicked.connect(
        lambda: parent.ejecutar_transferencia_masiva(var_destino["idx"], hojas_disp, hoja_origen, clave, dlg)
    )
    lay.addWidget(btn_conf)

    _centrar_dialogo(dlg, parent)
    dlg.exec()
