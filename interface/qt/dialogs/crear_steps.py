"""Crear STEPs desde DXF ya exportados (recuperación post-export).

Dos modos:
  - Desde nesteo: Local/Remoto → cliente → job → W.O. (o S.W.O.)
  - Desde ruta: selector de carpeta NESTING (igual que despachador_nocturno)

Motor: OCCT (join LINE/ARC en memoria → STEP). No reescribe los DXF de nest
(exactitud 1:1). Misma tubería que el despachador nocturno / export 3D ANS.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from interface.qt.theme import (
    COLOR_BORDE,
    COLOR_GRIS_DARK,
    COLOR_TARJETA,
    COLOR_TEXTO_SECUNDARIO,
    COLOR_TEXTO_TITULO,
    apply_push_button,
    surface_dialog_stylesheet,
)
from interface.qt.thread_bridge import call_on_main

_REPO = Path(__file__).resolve().parents[3]
_CAD_OCCT = _REPO / "CAD (OCCT)"
if str(_CAD_OCCT) not in sys.path:
    sys.path.insert(0, str(_CAD_OCCT))


def _limpiar_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w:
            w.deleteLater()
        sub = item.layout()
        if sub:
            _limpiar_layout(sub)


def _preguntar_modo(parent) -> str | None:
    """'nesteo' | 'ruta' | None."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Crear STEPs")
    box.setText("¿Cómo quieres generar los archivos STEP?")
    box.setInformativeText(
        "Desde nesteo: elige cliente → job → W.O. (o S.W.O.) que ya tenga DXF.\n\n"
        "Desde ruta: apunta a la carpeta NESTING de una W.O. "
        "(igual que el despachador nocturno)."
    )
    btn_nest = box.addButton("Desde nesteo", QMessageBox.ButtonRole.AcceptRole)
    btn_ruta = box.addButton("Desde ruta", QMessageBox.ButtonRole.ActionRole)
    btn_cancel = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
    for btn, w in ((btn_nest, 140), (btn_ruta, 130), (btn_cancel, 100)):
        btn.setMinimumWidth(w)
        btn.setMinimumHeight(34)
    box.setDefaultButton(btn_nest)
    box.exec()
    clicked = box.clickedButton()
    if clicked is btn_nest:
        return "nesteo"
    if clicked is btn_ruta:
        return "ruta"
    return None


def _preguntar_origen(parent, titulo: str) -> bool | None:
    """True=remoto, False=local, None=cancelado."""
    box = QMessageBox(parent)
    box.setWindowTitle(titulo)
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(
        "¿Dónde está la exportación de DXF?\n\n"
        "• Local → Nesteos Locales (escritorio / OneDrive)\n"
        "• Remoto → servidor (ARGA METALS CORPORATE SYSTEM)"
    )
    btn_local = box.addButton("Local", QMessageBox.ButtonRole.AcceptRole)
    btn_remoto = box.addButton("Remoto", QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    clicked = box.clickedButton()
    if clicked is btn_local:
        return False
    if clicked is btn_remoto:
        return True
    return None


def _preguntar_tipo_orden(parent) -> str | None:
    """'wo' | 'swo' | None."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Crear STEPs — tipo de orden")
    box.setText("¿W.O. de un job o S.W.O. (Máxima Optimización)?")
    btn_wo = box.addButton("W.O. (job)", QMessageBox.ButtonRole.AcceptRole)
    btn_swo = box.addButton("S.W.O.", QMessageBox.ButtonRole.ActionRole)
    btn_cancel = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(btn_wo)
    box.exec()
    clicked = box.clickedButton()
    if clicked is btn_wo:
        return "wo"
    if clicked is btn_swo:
        return "swo"
    return None


def _card_row(titulo: str, detalle: str, btn_texto: str, on_click) -> QFrame:
    row = QFrame()
    row.setObjectName("HerinoxCard")
    rl = QHBoxLayout(row)
    rl.setContentsMargins(14, 12, 14, 12)
    info = QVBoxLayout()
    lbl = QLabel(titulo)
    lbl.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};font-weight:700;font-size:13px;")
    info.addWidget(lbl)
    sub = QLabel(detalle)
    sub.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-size:11px;")
    sub.setWordWrap(True)
    info.addWidget(sub)
    rl.addLayout(info, 1)
    btn = QPushButton(btn_texto)
    apply_push_button(btn, COLOR_GRIS_DARK, font_size=11, padding="6px 14px")
    btn.clicked.connect(on_click)
    rl.addWidget(btn)
    return row


def _seleccionar_job_y_wo(parent, *, modo_servidor: bool) -> Path | None:
    """UI cliente → job → W.O. Devuelve ruta NESTING o None."""
    from engine.step_paths import escanear_jobs_con_model_core, listar_wo_con_dxf

    origen = "Remoto" if modo_servidor else "Local"
    app = getattr(parent, "app", None)
    if app is not None and hasattr(app, "abrir_ventana_carga"):
        app.abrir_ventana_carga(f"Escaneando jobs ({origen})…")
        try:
            from PySide6.QtWidgets import QApplication

            QApplication.processEvents()
        except Exception:
            pass

    jobs: list[dict] = []
    err: str | None = None
    try:
        jobs = escanear_jobs_con_model_core(modo_servidor=modo_servidor)
    except Exception as e:
        err = str(e)
    finally:
        if app is not None and hasattr(app, "cerrar_ventana_carga"):
            app.cerrar_ventana_carga()

    if err:
        QMessageBox.critical(parent, "Crear STEPs", f"No se pudo escanear:\n{err}")
        return None
    if not jobs:
        QMessageBox.information(
            parent,
            "Crear STEPs",
            f"No hay jobs con MODEL CORE FILES en {origen}.",
        )
        return None

    por_cliente: dict[str, list] = {}
    for job in jobs:
        cli = str(job.get("cliente") or "Sin cliente").strip() or "Sin cliente"
        por_cliente.setdefault(cli, []).append(job)
    for lista in por_cliente.values():
        lista.sort(key=lambda j: str(j.get("job_name", "")).upper())
    por_cliente = dict(sorted(por_cliente.items(), key=lambda kv: kv[0].upper()))

    dlg = QDialog(parent)
    dlg.setWindowTitle(f"CREAR STEPs — {origen}")
    dlg.resize(820, 620)
    dlg.setStyleSheet(surface_dialog_stylesheet())
    dlg.setModal(True)
    elegido: dict = {"nesting": None}
    estado: dict = {"cliente": None, "job": None}

    root = QVBoxLayout(dlg)
    root.setContentsMargins(20, 18, 20, 18)
    root.setSpacing(12)

    hdr = QLabel("Seleccione cliente, job y W.O. con DXF")
    hdr.setStyleSheet(f"font-size:18px;font-weight:700;color:{COLOR_TEXTO_TITULO};")
    root.addWidget(hdr)

    search_wrap = QFrame()
    search_wrap.setStyleSheet(
        f"QFrame{{background:{COLOR_TARJETA};border:1px solid {COLOR_BORDE};border-radius:10px;}}"
    )
    search_lay = QHBoxLayout(search_wrap)
    search_lay.setContentsMargins(12, 4, 10, 4)
    ent_buscar = QLineEdit()
    ent_buscar.setPlaceholderText("Buscar por cliente, job o producto…")
    ent_buscar.setStyleSheet(
        f"QLineEdit{{background:transparent;border:none;color:{COLOR_TEXTO_TITULO};font-size:13px;}}"
    )
    search_lay.addWidget(ent_buscar, 1)
    root.addWidget(search_wrap)

    lbl_ruta = QLabel("Clientes")
    lbl_ruta.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-weight:700;font-size:11px;")
    root.addWidget(lbl_ruta)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setObjectName("AppScroll")
    inner = QWidget()
    lista = QVBoxLayout(inner)
    lista.setSpacing(8)
    lista.setAlignment(Qt.AlignmentFlag.AlignTop)
    scroll.setWidget(inner)
    root.addWidget(scroll, 1)

    btn_volver = QPushButton("← Volver")
    apply_push_button(btn_volver, "#FFFFFF", font_size=11, padding="6px 12px")
    btn_volver.hide()
    root.addWidget(btn_volver)

    def refrescar() -> None:
        _limpiar_layout(lista)
        filtro = ent_buscar.text().strip().lower()
        cliente_activo = estado.get("cliente")
        job_activo = estado.get("job")

        if job_activo is not None:
            btn_volver.show()
            lbl_ruta.setText(
                f"Clientes  ›  {job_activo.get('cliente')}  ›  {job_activo.get('job_name')}  ›  W.O."
            )
            wos = listar_wo_con_dxf(job_activo["ruta_mcf"])
            if not wos:
                vacio = QLabel(
                    "Este job no tiene W.O. con DXF de nesting convertibles "
                    "(ROBOT LASER / PLASMA / NESTEOS DE COBRE)."
                )
                vacio.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};padding:12px;")
                vacio.setWordWrap(True)
                lista.addWidget(vacio)
            else:
                for wo in wos:
                    det = f"{wo['n_dxf']} DXF"
                    if wo["n_steps"]:
                        det += f"  ·  {wo['n_steps']} STEP ya existentes"
                    else:
                        det += "  ·  sin STEP"

                    def elegir(_c=False, w=wo):
                        elegido["nesting"] = Path(w["nesting"])
                        dlg.accept()

                    lista.addWidget(
                        _card_row(wo["nombre"], det, "CREAR STEPs", elegir)
                    )
            lista.addStretch()
            return

        if filtro:
            btn_volver.hide()
            lbl_ruta.setText("Resultados de búsqueda")
            vistos = set()
            coincidencias = []
            for job in jobs:
                clave = (job.get("ruta_job_root"), job.get("job_name"))
                if clave in vistos:
                    continue
                vistos.add(clave)
                texto = " ".join(
                    str(job.get(k, "")) for k in ("job_name", "cliente", "producto")
                ).lower()
                if filtro in texto:
                    coincidencias.append(job)
            coincidencias.sort(key=lambda j: str(j.get("job_name", "")).upper())
            if not coincidencias:
                vacio = QLabel("No se encontraron jobs con ese criterio.")
                vacio.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};padding:12px;")
                lista.addWidget(vacio)
            else:
                for job in coincidencias:

                    def abrir_job(_c=False, j=job):
                        estado["job"] = j
                        estado["cliente"] = j.get("cliente")
                        ent_buscar.clear()
                        refrescar()

                    lista.addWidget(
                        _card_row(
                            str(job.get("job_name", "")),
                            f"{job.get('producto', '—')}  ·  {job.get('cliente', '—')}",
                            "VER W.O.",
                            abrir_job,
                        )
                    )
            lista.addStretch()
            return

        if cliente_activo:
            btn_volver.show()
            lbl_ruta.setText(f"Clientes  ›  {cliente_activo}")
            jobs_cli = por_cliente.get(cliente_activo, [])
            vistos = set()
            for job in jobs_cli:
                clave = job.get("job_name")
                if clave in vistos:
                    continue
                vistos.add(clave)

                def abrir_job(_c=False, j=job):
                    estado["job"] = j
                    ent_buscar.clear()
                    refrescar()

                lista.addWidget(
                    _card_row(
                        str(job.get("job_name", "")),
                        f"{job.get('producto', '—')}  ·  {job.get('cliente', '—')}",
                        "VER W.O.",
                        abrir_job,
                    )
                )
            lista.addStretch()
            return

        btn_volver.hide()
        lbl_ruta.setText("Clientes")
        for nombre, lista_jobs in por_cliente.items():
            productos = sorted(
                {
                    str(j.get("producto", "")).strip()
                    for j in lista_jobs
                    if j.get("producto")
                }
            )
            det = f"{len(lista_jobs)} job(s)"
            if productos:
                det += f"  ·  {', '.join(productos[:3])}"

            def abrir_cliente(_c=False, c=nombre):
                estado["cliente"] = c
                estado["job"] = None
                ent_buscar.clear()
                refrescar()

            lista.addWidget(_card_row(nombre, det, "VER JOBS", abrir_cliente))
        lista.addStretch()

    def volver() -> None:
        if estado.get("job") is not None:
            estado["job"] = None
        elif estado.get("cliente") is not None:
            estado["cliente"] = None
        ent_buscar.clear()
        refrescar()

    btn_volver.clicked.connect(volver)
    ent_buscar.textChanged.connect(lambda _t: refrescar())
    refrescar()
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return elegido.get("nesting")


def _seleccionar_swo(parent, *, modo_servidor: bool) -> Path | None:
    from engine.step_paths import listar_swo_con_dxf

    origen = "Remoto" if modo_servidor else "Local"
    swos = listar_swo_con_dxf(modo_servidor=modo_servidor)
    if not swos:
        QMessageBox.information(
            parent,
            "Crear STEPs",
            f"No hay S.W.O. con DXF convertibles en Máxima Optimización ({origen}).",
        )
        return None

    dlg = QDialog(parent)
    dlg.setWindowTitle(f"CREAR STEPs — S.W.O. ({origen})")
    dlg.resize(720, 520)
    dlg.setStyleSheet(surface_dialog_stylesheet())
    dlg.setModal(True)
    elegido: dict = {"nesting": None}

    root = QVBoxLayout(dlg)
    root.setContentsMargins(20, 18, 20, 18)
    root.setSpacing(12)
    hdr = QLabel("Seleccione la S.W.O. para generar STEPs")
    hdr.setStyleSheet(f"font-size:18px;font-weight:700;color:{COLOR_TEXTO_TITULO};")
    root.addWidget(hdr)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setObjectName("AppScroll")
    inner = QWidget()
    lista = QVBoxLayout(inner)
    lista.setSpacing(8)
    lista.setAlignment(Qt.AlignmentFlag.AlignTop)
    scroll.setWidget(inner)
    root.addWidget(scroll, 1)

    for swo in swos:
        det = f"{swo['n_dxf']} DXF"
        if swo["n_steps"]:
            det += f"  ·  {swo['n_steps']} STEP ya existentes"
        else:
            det += "  ·  sin STEP"

        def elegir(_c=False, s=swo):
            elegido["nesting"] = Path(s["nesting"])
            dlg.accept()

        lista.addWidget(_card_row(swo["nombre"], det, "CREAR STEPs", elegir))
    lista.addStretch()

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return elegido.get("nesting")


def _pedir_ruta_nesting(parent) -> Path | None:
    ruta = QFileDialog.getExistingDirectory(
        parent,
        "Selecciona la carpeta NESTING",
        "",
        QFileDialog.Option.ShowDirsOnly,
    )
    if not ruta:
        return None
    p = Path(os.path.normpath(ruta))
    if p.name.upper() != "NESTING":
        # Permitir apuntar a ARGA MODEL CORE o a la WO: buscar NESTING hijo.
        candidato = p / "NESTING"
        if candidato.is_dir():
            p = candidato
        elif (p / "ARGA MODEL CORE" / "NESTING").is_dir():
            p = p / "ARGA MODEL CORE" / "NESTING"
        else:
            QMessageBox.warning(
                parent,
                "Crear STEPs",
                "La carpeta debe ser NESTING (o contenerla).\n\n"
                f"Seleccionada:\n{ruta}",
            )
            return None
    if not p.is_dir():
        return None
    return p


def _ejecutar_conversion(parent, ruta_nesting: Path) -> None:
    """Lanza procesar_ruta_nesting en hilo (OCCT por defecto)."""
    ruta = os.path.normpath(str(ruta_nesting))
    app = getattr(parent, "app", None)
    if app is not None and hasattr(app, "abrir_ventana_carga"):
        app.abrir_ventana_carga("Generando STEPs (OCCT)…")

    def _worker():
        err = None
        resumen = None
        try:
            from despachador_nocturno import procesar_ruta_nesting

            resumen = procesar_ruta_nesting(ruta, actualizar_bd=False)
        except Exception as e:
            err = str(e)

        def _done():
            if app is not None and hasattr(app, "cerrar_ventana_carga"):
                app.cerrar_ventana_carga()
            if err:
                QMessageBox.critical(
                    parent,
                    "Crear STEPs",
                    f"Error al generar STEPs:\n{err}\n\nCarpeta:\n{ruta}",
                )
                return
            ok = bool(resumen and resumen.get("ok"))
            n_step = int((resumen or {}).get("total_step_final") or 0)
            n_fam = int((resumen or {}).get("familias_detectadas") or 0)
            n_dxf_fam = int((resumen or {}).get("familias_con_dxf") or 0)
            if ok:
                QMessageBox.information(
                    parent,
                    "Crear STEPs",
                    f"STEPs generados correctamente.\n\n"
                    f"Familias: {n_fam}  ·  con DXF: {n_dxf_fam}\n"
                    f"STEP totales: {n_step}\n\n"
                    f"{ruta}",
                )
            else:
                QMessageBox.warning(
                    parent,
                    "Crear STEPs",
                    f"No se completó la conversión 3D.\n\n"
                    f"Familias: {n_fam}  ·  con DXF: {n_dxf_fam}\n"
                    f"STEP detectados: {n_step}\n\n"
                    f"Revisa el log del despachador / OCCT.\n{ruta}",
                )

        call_on_main(_done)

    threading.Thread(target=_worker, daemon=True).start()


def abrir_crear_steps(tab) -> None:
    """Entrada desde la cinta NESTING (bajo Ver STEP)."""
    modo = _preguntar_modo(tab)
    if modo is None:
        return

    ruta_nesting: Path | None = None

    if modo == "ruta":
        ruta_nesting = _pedir_ruta_nesting(tab)
    else:
        origen = _preguntar_origen(tab, "Crear STEPs — origen")
        if origen is None:
            return
        tipo = _preguntar_tipo_orden(tab)
        if tipo is None:
            return
        if tipo == "swo":
            ruta_nesting = _seleccionar_swo(tab, modo_servidor=origen)
        else:
            ruta_nesting = _seleccionar_job_y_wo(tab, modo_servidor=origen)

    if ruta_nesting is None:
        return

    conf = QMessageBox.question(
        tab,
        "Crear STEPs",
        "Se generarán STEP de todos los DXF convertibles en:\n\n"
        f"{ruta_nesting}\n\n"
        "Modo actual: 1 STEP por DXF (carpeta STEP plana), coords 1:1 "
        "sin Cama A/B ni offsets — igual que Exportar 3D del ANS.\n"
        "Familias: CAMA LASER, ROBOT LASER/PLASMA, NESTEOS DE COBRE.\n"
        "Motor: OCCT (une LINE/ARC en memoria; no altera los DXF).\n\n"
        "¿Continuar?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    if conf != QMessageBox.StandardButton.Yes:
        return

    _ejecutar_conversion(tab, ruta_nesting)
