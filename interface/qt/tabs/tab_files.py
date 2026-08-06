"""Pestaña FILES — PySide6 nativo (paridad con interface/tab_files.py oficial)."""
from __future__ import annotations

import csv
import glob
import os
import re
import shutil
import threading
import concurrent.futures

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import config
from interface.autodxf_metadata import combinar_metadata_dxf, normalizar_material_autodxf
from modules.processed_layers import ProcesadorDXF
from modules.scanner import EscanerServidor
from interface.qt.layout_helpers import make_card
from interface.qt.thread_bridge import call_on_main
from interface.qt.theme import (
    COLOR_BORDE,
    COLOR_FONDO_APP,
    COLOR_GRIS_DARK,
    COLOR_GRIS_MED,
    COLOR_TARJETA,
    COLOR_TEXTO_SECUNDARIO,
    COLOR_TEXTO_SUBTITULO,
    COLOR_TEXTO_TITULO,
    COLOR_TEXTO_MUTED,
    apply_push_button,
    surface_dialog_stylesheet,
)


class TabFiles(QWidget):
    def __init__(self, parent, app_principal):
        super().__init__(parent)
        self.app = app_principal
        self.escaner = EscanerServidor()
        self.procesador = ProcesadorDXF()
        if not hasattr(self.app, "meta_pdf_por_ruta"):
            self.app.meta_pdf_por_ruta = {}
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = make_card()
        card.setMinimumSize(720, 480)
        card.setMaximumWidth(980)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(48, 44, 48, 44)
        lay.setSpacing(18)

        title = QLabel("CONEXIÓN CON EL SERVIDOR")
        title.setStyleSheet(f"font-size:22px;font-weight:700;color:{COLOR_TEXTO_SUBTITULO};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        self.btn_nest_scan = QPushButton("IMPORTAR JOB INDIVIDUAL\n(INGENIERÍA)")
        self.btn_nest_scan.setFixedSize(450, 80)
        apply_push_button(self.btn_nest_scan, COLOR_GRIS_DARK, font_size=16, padding="12px 20px")
        self.btn_nest_scan.clicked.connect(self.ejecutar_escaneo_servidor)
        lay.addWidget(self.btn_nest_scan, alignment=Qt.AlignmentFlag.AlignCenter)

        self.btn_swo_web = QPushButton("IMPORTAR S.W.O.\n(FUSIÓN DESDE TABLERO WEB)")
        self.btn_swo_web.setFixedSize(450, 80)
        apply_push_button(self.btn_swo_web, "#455E75", hover="#334659", font_size=16, padding="12px 20px")
        self.btn_swo_web.clicked.connect(self.buscar_swos_pendientes)
        lay.addWidget(self.btn_swo_web, alignment=Qt.AlignmentFlag.AlignCenter)

        self.btn_historial = QPushButton("GESTIONAR HISTORIAL\n(JOBS YA IMPORTADOS)")
        self.btn_historial.setFixedSize(450, 56)
        apply_push_button(self.btn_historial, "#FFFFFF", font_size=12, padding="8px 16px")
        self.btn_historial.clicked.connect(self.mostrar_gestion_historial)
        lay.addWidget(self.btn_historial, alignment=Qt.AlignmentFlag.AlignCenter)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{COLOR_BORDE};max-height:1px;")
        lay.addWidget(sep)

        engine_title = QLabel("MOTOR DE NESTEO")
        engine_title.setStyleSheet(
            f"font-size:13px;font-weight:700;color:{COLOR_TEXTO_SECUNDARIO};letter-spacing:0.5px;"
        )
        engine_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(engine_title)

        from modules.nesting_engine.engine_registry import list_engine_metas
        from modules.nesting_engine.nest_engine_config import load_default_steel_engine_id

        self._engine_combo = QComboBox()
        self._engine_combo.setObjectName("HerinoxCombo")
        self._engine_combo.setFixedSize(450, 40)
        self._engine_metas = list_engine_metas()
        current_eid = load_default_steel_engine_id()
        current_idx = 0
        for i, meta in enumerate(self._engine_metas):
            label = meta.display_name
            if meta.status != "ready":
                label = f"{label}  (pendiente)"
            self._engine_combo.addItem(label, meta.engine_id)
            idx = self._engine_combo.count() - 1
            self._engine_combo.setItemData(
                idx,
                meta.description or meta.display_name,
                Qt.ItemDataRole.ToolTipRole,
            )
            if meta.status != "ready":
                model = self._engine_combo.model()
                item = model.item(idx)
                if item is not None:
                    item.setEnabled(False)
            if meta.engine_id == current_eid:
                current_idx = i
        self._engine_combo.blockSignals(True)
        self._engine_combo.setCurrentIndex(current_idx)
        self._engine_combo.blockSignals(False)
        self._engine_combo.currentIndexChanged.connect(self._on_engine_combo_changed)
        lay.addWidget(self._engine_combo, alignment=Qt.AlignmentFlag.AlignCenter)

        self.lbl_engine_status = QLabel("")
        self.lbl_engine_status.setStyleSheet(f"color:{COLOR_TEXTO_MUTED};font-size:11px;")
        self.lbl_engine_status.setWordWrap(True)
        self.lbl_engine_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._refresh_engine_status_label(current_eid)
        lay.addWidget(self.lbl_engine_status)

        outer.addStretch()
        outer.addWidget(card, alignment=Qt.AlignmentFlag.AlignCenter)
        outer.addStretch()

    def _refresh_engine_status_label(self, engine_id: str) -> None:
        from modules.nesting_engine.engine_registry import get_engine_meta
        from modules.nesting_engine.nest_engine_context import (
            ENGINE_ARGA_LITE,
            ENGINE_SVGNEST_ULTRA,
        )

        try:
            meta = get_engine_meta(engine_id)
            desc = (meta.description or "").strip()
            if str(engine_id) == ENGINE_SVGNEST_ULTRA:
                tip = (
                    "Activo: SVGNest Ultra — optimiza como NestFab; "
                    "deja correr y pulsa Cancelar para aceptar el mejor resultado."
                )
                self.lbl_engine_status.setText(tip)
            elif str(engine_id) == ENGINE_ARGA_LITE:
                self.lbl_engine_status.setText(
                    "Activo: ARGA LITE — MC 3 pases (refine desde el mejor). "
                    "Rápido y decente: respaldo urgente sin sacrificar tanto la densidad."
                )
            elif desc:
                self.lbl_engine_status.setText(f"Activo: {meta.display_name} — {desc}")
            else:
                self.lbl_engine_status.setText(f"Activo: {meta.display_name}")
        except Exception:
            self.lbl_engine_status.setText(f"Activo: {engine_id}")

    def _on_engine_combo_changed(self, index: int) -> None:
        from modules.nesting_engine.nest_engine_config import apply_steel_engine

        if index < 0:
            return
        eid = str(self._engine_combo.itemData(index) or "").strip()
        if not eid:
            return
        motor = getattr(self.app, "motor_nesting", None)
        apply_steel_engine(eid, motor=motor)
        self._refresh_engine_status_label(eid)

    def _ui(self, fn, *args):
        call_on_main(fn, *args)

    # --- lógica copiada 1:1 del original (solo cambia capa UI) ---
    def _normalizar_ruta(self, ruta):
        try:
            return os.path.normcase(os.path.normpath(str(ruta)))
        except Exception:
            return str(ruta)

    def _normalizar_material(self, texto_material):
        return normalizar_material_autodxf(texto_material, default="CARBONO")

    def _parsear_nombre_dxf(self, nombre_archivo, ruta_origen=None):
        pieza, mat, qty_str, cal, _extras = combinar_metadata_dxf(
            ruta_origen or nombre_archivo,
            nombre_archivo=nombre_archivo,
        )
        return pieza, mat, qty_str, cal

    def _listar_dxfs_recursivo(self, carpeta_base):
        out = []
        base = str(carpeta_base or "").strip()
        if not base or not os.path.isdir(base):
            return out
        excluidas = {"processed files", "procesados", "nesting", "__pycache__"}
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d.strip().lower() not in excluidas]
            for f in files:
                if str(f).lower().endswith(".dxf"):
                    out.append(os.path.join(root, f))
        return out

    def _nombre_destino_unico(self, nombre_original, usados):
        base, ext = os.path.splitext(str(nombre_original))
        candidato = f"{base}{ext}"
        i = 2
        while candidato.lower() in usados:
            candidato = f"{base}__{i}{ext}"
            i += 1
        usados.add(candidato.lower())
        return candidato

    def _infer_job_desde_autodxf(self, carpeta_autodxf: str) -> str:
        try:
            actual = os.path.normpath(str(carpeta_autodxf))
            while actual and actual not in (actual[:1], os.path.dirname(actual)):
                if os.path.basename(actual).strip().lower() == "model core files":
                    return os.path.basename(os.path.dirname(actual))
                actual = os.path.dirname(actual)
        except Exception:
            pass
        return str(getattr(self.app, "job_activo", "") or "").strip()

    def _leer_multiplicador_desde_job_data(self, carpeta_autodxf: str, job_name: str) -> int:
        mult = max(1, int(getattr(self.app, "multiplicador_tanques", 1) or 1))
        try:
            autodxf = os.path.normpath(str(carpeta_autodxf))
            ruta_root = os.path.dirname(os.path.dirname(autodxf))
            ruta_csv = os.path.join(ruta_root, f"job_data_{job_name}.csv")
            if os.path.exists(ruta_csv):
                with open(ruta_csv, newline="", encoding="utf-8", errors="ignore") as f:
                    reader = list(csv.reader(f))
                    if len(reader) > 1 and len(reader[1]) > 3 and str(reader[1][3]).strip().isdigit():
                        mult = max(1, int(str(reader[1][3]).strip()))
        except Exception:
            pass
        return mult

    def _resolver_autodxf_desde_datos_actuales(self) -> str | None:
        rutas = [
            str(r[5])
            for r in (getattr(self.app, "datos_partes_actuales", []) or [])
            if len(r) > 5 and r[5]
        ]
        for ruta in rutas:
            try:
                actual = os.path.normpath(str(ruta))
            except Exception:
                continue
            while actual and actual not in (actual[:1], os.path.dirname(actual)):
                base = os.path.basename(actual).strip().lower()
                if base == "autodxf":
                    return actual
                if base == "processed files" and os.path.basename(os.path.dirname(actual)).strip().lower() == "autodxf":
                    return os.path.dirname(actual)
                actual = os.path.dirname(actual)

        job = str(getattr(self.app, "job_activo", "") or "").strip()
        if job:
            ruta_job = self.obtener_ruta_real_job(config.RUTA_SERVIDOR_RAIZ, job)
            if ruta_job:
                autodxf = os.path.join(ruta_job, "MODEL CORE FILES", "AutoDXF")
                if os.path.isdir(autodxf):
                    return autodxf
        return None

    def _procesar_autodxf_a_items(
        self,
        carpeta_autodxf: str,
        job_name: str,
        multiplicador: int,
        *,
        progress_cb=None,
    ):
        rutas_dxf = sorted(set(self._listar_dxfs_recursivo(carpeta_autodxf)), key=self._normalizar_ruta)
        carpeta_procesados = os.path.join(carpeta_autodxf, "Processed Files")
        os.makedirs(carpeta_procesados, exist_ok=True)
        items_procesados, nombres_usados = [], set()
        meta_pdf = {}
        total = len(rutas_dxf)
        for idx, ruta_in in enumerate(rutas_dxf, start=1):
            if callable(progress_cb):
                progress_cb(f"Validando DXF {idx}/{total}…", idx / max(1, total))
            arch = os.path.basename(ruta_in)
            ruta_out_real = os.path.join(carpeta_procesados, self._nombre_destino_unico(arch, nombres_usados))
            try:
                ok_proc = self.procesador.limpiar_archivo(ruta_in, ruta_out_real)
                if (not ok_proc) or (not os.path.exists(ruta_out_real)):
                    shutil.copy2(ruta_in, ruta_out_real)
                pieza, mat, qty_str, cal = self._parsear_nombre_dxf(arch, ruta_origen=ruta_in)
                try:
                    qty_final = str(int(qty_str) * multiplicador)
                except Exception:
                    qty_final = qty_str
                ruta_norm = self._normalizar_ruta(ruta_out_real)
                meta_pdf[ruta_norm] = {"job": job_name, "item": pieza}
                items_procesados.append((pieza, mat, qty_final, cal, "LISTO", ruta_out_real))
            except Exception:
                try:
                    if not os.path.exists(ruta_out_real):
                        shutil.copy2(ruta_in, ruta_out_real)
                except Exception:
                    pass
                ruta_norm = self._normalizar_ruta(ruta_out_real)
                meta_pdf[ruta_norm] = {"job": job_name, "item": os.path.splitext(arch)[0]}
                items_procesados.append((arch, "?", str(multiplicador), "?", "LISTO", ruta_out_real))
        return items_procesados, meta_pdf

    def escanear_partes_desde_ruta(self, *, progress_cb=None) -> dict | None:
        """Solo disco/parseo — seguro en hilo de fondo (sin tocar Qt)."""
        autodxf = self._resolver_autodxf_desde_datos_actuales()
        if not autodxf:
            return None
        job_name = self._infer_job_desde_autodxf(autodxf) or str(getattr(self.app, "job_activo", "") or "").strip()
        multiplicador = self._leer_multiplicador_desde_job_data(autodxf, job_name)
        items, meta_pdf = self._procesar_autodxf_a_items(
            autodxf,
            job_name,
            multiplicador,
            progress_cb=progress_cb,
        )
        return {
            "items": items,
            "meta_pdf": meta_pdf,
            "job_name": job_name,
            "multiplicador": multiplicador,
        }

    def aplicar_partes_resincronizadas(self, payload: dict, *, thumbnails_async: bool = False) -> int:
        """Aplica el resultado del escaneo en el hilo principal de Qt."""
        items = list(payload.get("items") or [])
        meta_pdf = dict(payload.get("meta_pdf") or {})
        job_name = str(payload.get("job_name") or "").strip()
        multiplicador = max(1, int(payload.get("multiplicador") or 1))
        if job_name:
            self.app.job_activo = job_name
        self.app.multiplicador_tanques = multiplicador
        self.app.meta_pdf_por_ruta = meta_pdf
        self.app.cargar_datos_parts(items, thumbnails_async=thumbnails_async)
        if hasattr(self.app, "editable_inputs_actuales"):
            self.app.editable_inputs_actuales = [list(x) for x in items]
        by_lote = getattr(self.app, "editable_inputs_by_lote", None)
        idx_lote = int(getattr(getattr(self.app, "vista_nesting", None), "lote_actual_idx", 0) or 0)
        if isinstance(by_lote, list) and 0 <= idx_lote < len(by_lote):
            by_lote[idx_lote] = [list(x) for x in items]
        return len(items)

    def resincronizar_partes_desde_ruta(self, *, thumbnails_async: bool = False, progress_cb=None) -> tuple[bool, int]:
        """Re-escanea AutoDXF y reconcilia PARTS con el disco (altas/bajas)."""
        payload = self.escanear_partes_desde_ruta(progress_cb=progress_cb)
        if not payload:
            return False, 0
        total = self.aplicar_partes_resincronizadas(payload, thumbnails_async=thumbnails_async)
        return True, total

    def _buscar_dxf_item_en_autodxf(self, ruta_autodxf, item):
        item_limpio = str(item or "").strip().lower()
        if not item_limpio:
            return ""
        candidatos = []
        ruta_proc = os.path.join(ruta_autodxf, "Processed Files")
        if os.path.isdir(ruta_proc):
            candidatos.extend(self._listar_dxfs_recursivo(ruta_proc))
        if os.path.isdir(ruta_autodxf):
            candidatos.extend(self._listar_dxfs_recursivo(ruta_autodxf))
        vistos, ordenados = set(), []
        for p in candidatos:
            k = self._normalizar_ruta(p)
            if k not in vistos:
                vistos.add(k)
                ordenados.append(p)
        for ruta in ordenados:
            f_lower = os.path.basename(ruta).lower()
            if (
                f_lower == f"{item_limpio}.dxf"
                or f_lower.startswith(f"{item_limpio},")
                or f_lower.startswith(f"{item_limpio} ")
                or f_lower.startswith(f"{item_limpio}_")  # ej. 62135-1251-P03_TAB, A 36...
            ):
                return ruta
        return ""

    def ejecutar_escaneo_servidor(self):
        self.btn_nest_scan.setEnabled(False)
        self.btn_nest_scan.setText("ESCANEANDO...")
        apply_push_button(self.btn_nest_scan, "#E2E8F0", font_size=16, padding="12px 20px")
        threading.Thread(target=self.thread_escaneo, daemon=True).start()

    def thread_escaneo(self):
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        jobs, err = [], None
        try:
            if hasattr(self.app, "recargar_historial_jobs"):
                self.app.recargar_historial_jobs()
            fut = pool.submit(self.escaner.buscar_nuevos_jobs, self.app.jobs_procesados)
            try:
                jobs, err = fut.result(timeout=120)
            except concurrent.futures.TimeoutError:
                jobs, err = [], (
                    "El escaneo del servidor tardó demasiado.\n"
                    "Verifique VPN/conexión LAN o use nesteos locales con el switch desactivado."
                )
        except Exception as e:
            jobs, err = [], str(e)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        self._ui(self.after_escaneo, jobs, err)

    def after_escaneo(self, jobs, err=None):
        self.btn_nest_scan.setEnabled(True)
        self.btn_nest_scan.setText("IMPORTAR JOB INDIVIDUAL\n(INGENIERÍA)")
        apply_push_button(self.btn_nest_scan, COLOR_GRIS_DARK, font_size=16, padding="12px 20px")
        try:
            if err:
                QMessageBox.critical(self, "Error", str(err))
                return
            if not jobs:
                QMessageBox.information(self, "Estatus", "No hay nuevos Jobs.")
                return
            self.mostrar_selector_jobs(jobs)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo mostrar la lista de jobs:\n{e}")

    def _dialogo_lista(self, titulo, ancho=800, alto=600):
        dlg = QDialog(self)
        dlg.setWindowTitle(titulo)
        dlg.resize(ancho, alto)
        dlg.setStyleSheet(surface_dialog_stylesheet())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("AppScroll")
        inner = QWidget()
        scroll.setWidget(inner)
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(8)
        inner_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        inner_lay.addStretch(1)
        root = QVBoxLayout(dlg)
        root.setContentsMargins(16, 16, 16, 16)
        root.addWidget(scroll)
        return dlg, inner_lay

    @staticmethod
    def _limpiar_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            sub = item.layout()
            if sub:
                TabFiles._limpiar_layout(sub)

    def _agrupar_jobs_por_cliente(self, jobs):
        por_cliente: dict[str, list] = {}
        for job in jobs:
            cliente = str(job.get("cliente") or "Sin cliente").strip() or "Sin cliente"
            por_cliente.setdefault(cliente, []).append(job)
        for lista in por_cliente.values():
            lista.sort(key=lambda j: str(j.get("job_name", "")).upper())
        return dict(sorted(por_cliente.items(), key=lambda kv: kv[0].upper()))

    def mostrar_selector_jobs(self, jobs):
        dlg = QDialog(self)
        dlg.setWindowTitle("IMPORTAR TRABAJOS")
        dlg.resize(820, 620)
        dlg.setStyleSheet(surface_dialog_stylesheet())
        dlg.setModal(True)

        por_cliente = self._agrupar_jobs_por_cliente(jobs)
        estado = {"cliente": None}

        root = QVBoxLayout(dlg)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        hdr = QLabel("Seleccione el cliente y el job a procesar")
        hdr.setStyleSheet(f"font-size:18px;font-weight:700;color:{COLOR_TEXTO_TITULO};")
        root.addWidget(hdr)

        search_wrap = QFrame()
        search_wrap.setStyleSheet(
            f"QFrame{{background:{COLOR_TARJETA};border:1px solid {COLOR_BORDE};border-radius:10px;}}"
        )
        search_lay = QHBoxLayout(search_wrap)
        search_lay.setContentsMargins(12, 4, 10, 4)
        search_lay.setSpacing(8)
        ent_buscar = QLineEdit()
        ent_buscar.setObjectName("HerinoxSearch")
        ent_buscar.setPlaceholderText("Buscar por cliente, job o producto…")
        ent_buscar.setStyleSheet(
            f"QLineEdit{{background:transparent;border:none;color:{COLOR_TEXTO_TITULO};font-size:13px;}}"
            f"QLineEdit:focus{{border:none;}}"
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

        btn_volver = QPushButton("← Volver a clientes")
        apply_push_button(btn_volver, "#FFFFFF", font_size=11, padding="6px 12px")
        btn_volver.hide()
        root.addWidget(btn_volver)

        def _card_job(job_info):
            row = QFrame()
            row.setObjectName("HerinoxCard")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(14, 12, 14, 12)
            info = QVBoxLayout()
            lbl_job = QLabel(str(job_info.get("job_name", "")))
            lbl_job.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};font-weight:700;font-size:13px;")
            info.addWidget(lbl_job)
            sub = QLabel(
                f"{job_info.get('producto', '—')}  ·  {job_info.get('cliente', '—')}"
            )
            sub.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-size:11px;")
            info.addWidget(sub)
            rl.addLayout(info, 1)
            btn = QPushButton("IMPORTAR")
            apply_push_button(btn, COLOR_GRIS_DARK, font_size=11, padding="6px 14px")
            btn.clicked.connect(lambda _c=False, j=job_info: (dlg.accept(), self.procesar_seleccion(j)))
            rl.addWidget(btn)
            return row

        def _card_cliente(nombre_cliente, lista_jobs):
            row = QFrame()
            row.setObjectName("HerinoxCard")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(14, 12, 14, 12)
            info = QVBoxLayout()
            lbl_c = QLabel(nombre_cliente)
            lbl_c.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};font-weight:700;font-size:14px;")
            info.addWidget(lbl_c)
            productos = sorted({str(j.get("producto", "")).strip() for j in lista_jobs if j.get("producto")})
            det = QLabel(f"{len(lista_jobs)} job(s)" + (f"  ·  {', '.join(productos[:3])}" if productos else ""))
            det.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-size:11px;")
            info.addWidget(det)
            rl.addLayout(info, 1)
            btn = QPushButton("VER JOBS")
            apply_push_button(btn, "#FFFFFF", font_size=11, padding="6px 14px")

            def abrir_cliente(_c=False, c=nombre_cliente):
                estado["cliente"] = c
                ent_buscar.clear()
                refrescar()

            btn.clicked.connect(abrir_cliente)
            rl.addWidget(btn)
            return row

        def refrescar():
            self._limpiar_layout(lista)
            filtro = ent_buscar.text().strip().lower()
            cliente_activo = estado.get("cliente")

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
                        str(job.get(k, ""))
                        for k in ("job_name", "cliente", "producto")
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
                        lista.addWidget(_card_job(job))
                lista.addStretch()
                return

            if cliente_activo:
                btn_volver.show()
                lbl_ruta.setText(f"Clientes  ›  {cliente_activo}")
                jobs_cliente = por_cliente.get(cliente_activo, [])
                vistos = set()
                for job in jobs_cliente:
                    clave = job.get("job_name")
                    if clave in vistos:
                        continue
                    vistos.add(clave)
                    lista.addWidget(_card_job(job))
                if not jobs_cliente:
                    vacio = QLabel("Este cliente no tiene jobs pendientes.")
                    vacio.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};padding:12px;")
                    lista.addWidget(vacio)
            else:
                btn_volver.hide()
                lbl_ruta.setText("Clientes")
                if not por_cliente:
                    vacio = QLabel("No hay jobs disponibles.")
                    vacio.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};padding:12px;")
                    lista.addWidget(vacio)
                else:
                    for nombre, lista_jobs in por_cliente.items():
                        lista.addWidget(_card_cliente(nombre, lista_jobs))
            lista.addStretch()

        def volver_clientes():
            estado["cliente"] = None
            ent_buscar.clear()
            refrescar()

        btn_volver.clicked.connect(volver_clientes)
        ent_buscar.textChanged.connect(lambda _t: refrescar())
        refrescar()
        dlg.exec()

    def mostrar_gestion_historial(self):
        from interface.qt.dialogs.password_prompt import solicitar_contrasena
        from modules.historial_auth import verificar_clave_historial

        clave = solicitar_contrasena(
            self,
            titulo="Historial de jobs",
            mensaje=(
                "Acceso restringido.\n\n"
                "Ingrese la contraseña para ver o quitar jobs del historial de importación."
            ),
        )
        if clave is None:
            return
        if not verificar_clave_historial(clave):
            QMessageBox.warning(self, "Acceso denegado", "Contraseña incorrecta.")
            return

        if hasattr(self.app, "recargar_historial_jobs"):
            self.app.recargar_historial_jobs()

        dlg, lay = self._dialogo_lista("HISTORIAL DE JOBS IMPORTADOS", 720, 520)
        hdr = QLabel("Jobs ocultos del listado IMPORTAR (ya nesteados o importados)")
        hdr.setStyleSheet(f"font-size:15px;font-weight:700;color:{COLOR_TEXTO_TITULO};")
        hdr.setWordWrap(True)
        lay.addWidget(hdr)
        hint = QLabel(
            "Quitar un job aquí lo vuelve a mostrar en IMPORTAR JOB INDIVIDUAL. "
            f"Archivo: {config.DB_HISTORIAL}"
        )
        hint.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};font-size:11px;")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        lista_wrap = QVBoxLayout()

        def refrescar_historial_ui():
            TabFiles._limpiar_layout(lista_wrap)
            items = list(getattr(self.app, "jobs_procesados", []) or [])
            if not items:
                vacio = QLabel("El historial está vacío — todos los jobs aparecen al importar.")
                vacio.setStyleSheet(f"color:{COLOR_TEXTO_SECUNDARIO};padding:12px;")
                lista_wrap.addWidget(vacio)
                return
            for nombre in items:
                row = QFrame()
                row.setObjectName("HerinoxCard")
                rl = QHBoxLayout(row)
                lbl = QLabel(str(nombre))
                lbl.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};font-weight:600;font-size:13px;")
                rl.addWidget(lbl, 1)
                btn = QPushButton("QUITAR")
                apply_push_button(btn, COLOR_GRIS_MED, font_size=11, padding="6px 12px")

                def quitar(_c=False, job=nombre):
                    if hasattr(self.app, "eliminar_jobs_del_historial"):
                        self.app.eliminar_jobs_del_historial([job])
                    else:
                        from modules.historial_jobs import eliminar_jobs_del_historial

                        self.app.jobs_procesados = eliminar_jobs_del_historial([job])
                    refrescar_historial_ui()

                btn.clicked.connect(quitar)
                rl.addWidget(btn)
                lista_wrap.addWidget(row)

        container = QWidget()
        container.setLayout(lista_wrap)
        lay.addWidget(container)
        refrescar_historial_ui()
        dlg.exec()

    def procesar_seleccion(self, job_info):
        job_name = str(job_info.get("job_name") or "job")
        if hasattr(self.app, "abrir_ventana_carga"):
            self.app.abrir_ventana_carga(f"Importando {job_name}…")
        threading.Thread(
            target=self._thread_importar_job,
            args=(job_info,),
            daemon=True,
        ).start()

    def _thread_importar_job(self, job_info):
        err = None
        payload = None
        try:
            payload = self._preparar_import_job(job_info)
        except Exception as e:
            err = str(e)
        self._ui(self._finalizar_import_job, payload, err)

    def _preparar_import_job(self, job_info):
        carpeta_origen = job_info["ruta_full"]
        job_name = job_info["job_name"]
        ruta_root = os.path.dirname(os.path.dirname(carpeta_origen))
        multiplicador = self._leer_multiplicador_desde_job_data(carpeta_origen, job_name)
        rutas_dxf = sorted(set(self._listar_dxfs_recursivo(carpeta_origen)), key=self._normalizar_ruta)
        if not rutas_dxf:
            raise RuntimeError("No se encontraron DXF en AutoDXF (ni en subcarpetas).")

        def _progress(msg, pct):
            if hasattr(self.app, "actualizar_progreso"):
                self.app.actualizar_progreso(msg, pct)

        items_procesados, meta_pdf = self._procesar_autodxf_a_items(
            carpeta_origen,
            job_name,
            multiplicador,
            progress_cb=_progress,
        )
        return {
            "job_info": job_info,
            "job_name": job_name,
            "items": items_procesados,
            "meta_pdf": meta_pdf,
            "multiplicador": multiplicador,
            "ruta_root": job_info.get("ruta_job_root", ruta_root),
        }

    def _finalizar_import_job(self, payload, err=None):
        if hasattr(self.app, "cerrar_ventana_carga"):
            self.app.cerrar_ventana_carga()
        if err:
            QMessageBox.critical(self, "Error", f"Error al importar:\n{err}")
            return
        if not payload:
            QMessageBox.critical(self, "Error", "No se pudo completar la importación.")
            return
        self.app.job_activo = payload["job_name"]
        self.app.multiplicador_tanques = payload["multiplicador"]
        self.app.meta_pdf_por_ruta = payload["meta_pdf"]
        self.app.cargar_datos_parts(payload["items"])
        self.app.ir_a_tab("PARTS")

    def buscar_swos_pendientes(self):
        self.btn_swo_web.setEnabled(False)
        self.btn_swo_web.setText("BUSCANDO S.W.O...")
        threading.Thread(target=self.thread_swos, daemon=True).start()

    def thread_swos(self):
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            cred = {"host": "192.168.2.80", "database": "nestingpro_db", "user": "postgres", "password": "nesting123", "port": "5433"}
            con = psycopg2.connect(**cred)
            cur = con.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT DISTINCT super_work_order FROM reporte_cortes WHERE estatus = 'Pendiente SWO' AND super_work_order IS NOT NULL;")
            lista = [s["super_work_order"] for s in cur.fetchall()]
            cur.close()
            con.close()
            self._ui(self.mostrar_selector_swo, lista)
        except Exception as e:
            self._ui(self.restaurar_boton_swo, str(e))

    def restaurar_boton_swo(self, err=None):
        self.btn_swo_web.setEnabled(True)
        self.btn_swo_web.setText("IMPORTAR S.W.O.\n(FUSIÓN DESDE TABLERO WEB)")
        if err:
            QMessageBox.critical(self, "Error BD", f"No se pudo conectar a PostgreSQL:\n{err}")

    def mostrar_selector_swo(self, swos):
        self.restaurar_boton_swo()
        if not swos:
            QMessageBox.information(self, "Bandeja Vacía", "No hay Súper Work Orders pendientes por descargar.")
            return
        dlg, lay = self._dialogo_lista("IMPORTAR SÚPER WORK ORDER", 600, 450)
        hdr_swo = QLabel("Seleccione la SWO a Descargar")
        hdr_swo.setStyleSheet(f"font-size:16px;font-weight:700;color:{COLOR_TEXTO_TITULO};")
        lay.addWidget(hdr_swo)
        for swo in swos:
            row = QFrame()
            row.setObjectName("HerinoxCard")
            row.setStyleSheet(
                f"QFrame#HerinoxCard{{background:#ECFDF5;border:1px solid #10B981;border-radius:10px;}}"
            )
            rl = QHBoxLayout(row)
            lbl_swo = QLabel(str(swo))
            lbl_swo.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};font-weight:600;")
            rl.addWidget(lbl_swo)
            btn = QPushButton("DESCARGAR")
            apply_push_button(btn, "#10B981", font_size=11, padding="6px 14px")
            btn.clicked.connect(lambda _c=False, s=swo: (dlg.accept(), self.procesar_descarga_swo(s)))
            rl.addWidget(btn)
            lay.addWidget(row)
        dlg.exec()

    def obtener_ruta_real_job(self, ruta_raiz, nombre_job):
        """Resuelve carpeta de job bajo producto/cliente.

        El VSM a veces nombra el job sin espacios (GIGABOARD5) y en red queda
        un duplicado con espacios (GIGA BOARD 5). Prefiere la carpeta que tenga
        MODEL CORE FILES/AutoDXF para no tumbar la descarga SWO.
        """
        if not os.path.exists(ruta_raiz):
            return None
        job_pedido = str(nombre_job or "").strip()
        if not job_pedido:
            return None

        def _compact(s: str) -> str:
            return re.sub(r"[\s_\-]+", "", str(s or "").strip().upper())

        job_key = _compact(job_pedido)
        exactas, equivalentes = [], []
        try:
            for producto in os.listdir(ruta_raiz):
                ruta_prod = os.path.join(ruta_raiz, producto)
                if not os.path.isdir(ruta_prod):
                    continue
                for cliente in os.listdir(ruta_prod):
                    ruta_cli = os.path.join(ruta_prod, cliente)
                    if not os.path.isdir(ruta_cli):
                        continue
                    # Match exacto histórico.
                    ruta_exacta = os.path.join(ruta_cli, job_pedido)
                    if os.path.isdir(ruta_exacta):
                        exactas.append(ruta_exacta)
                    try:
                        for nombre in os.listdir(ruta_cli):
                            ruta_job = os.path.join(ruta_cli, nombre)
                            if not os.path.isdir(ruta_job):
                                continue
                            if _compact(nombre) == job_key:
                                equivalentes.append(ruta_job)
                    except Exception:
                        continue
        except Exception:
            return None

        def _tiene_autodxf(ruta_job: str) -> bool:
            return os.path.isdir(os.path.join(ruta_job, "MODEL CORE FILES", "AutoDXF"))

        # Orden: equivalentes/exactas con AutoDXF primero (carpeta VSM real).
        vistos, orden = set(), []
        for grupo in (exactas, equivalentes):
            for r in grupo:
                k = os.path.normcase(os.path.normpath(r))
                if k not in vistos:
                    vistos.add(k)
                    orden.append(r)
        con_ad = [r for r in orden if _tiene_autodxf(r)]
        if con_ad:
            return con_ad[0]
        return orden[0] if orden else None

    def procesar_descarga_swo(self, swo_id):
        self.app.abrir_ventana_carga(f"Descargando {swo_id}...")
        threading.Thread(target=self.thread_descarga_swo, args=(swo_id,), daemon=True).start()

    def thread_descarga_swo(self, swo_id):
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            from postgres_connector import registrar_diccionario_swo
            cred = {"host": "192.168.2.80", "database": "nestingpro_db", "user": "postgres", "password": "nesting123", "port": "5433"}
            con = psycopg2.connect(**cred)
            cur = con.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                "SELECT job, work_order, calibre, item, COUNT(*) as qty FROM reporte_cortes "
                "WHERE super_work_order = %s AND estatus = 'Pendiente SWO' GROUP BY job, work_order, calibre, item",
                (swo_id,),
            )
            items_db = cur.fetchall()
            cur.close()
            con.close()
            items_procesados, errores, prefijos = [], 0, set()
            self.app.meta_pdf_por_ruta = {}
            for row in items_db:
                job, work_order, item = row["job"], row["work_order"], row["item"]
                prefijo_adn = work_order.strip().upper()
                if prefijo_adn not in prefijos:
                    ruta_base_job = self.obtener_ruta_real_job(config.RUTA_SERVIDOR_RAIZ, job)
                    c_cli = c_job_com = c_prod = "N/A"
                    if ruta_base_job:
                        archivos_csv = glob.glob(os.path.join(ruta_base_job, f"job_data_{job}.csv"))
                        if archivos_csv:
                            try:
                                with open(archivos_csv[0], encoding="utf-8-sig") as f:
                                    reader = csv.reader(f)
                                    enc = [str(e).strip().upper() for e in next(reader, [])]
                                    datos = next(reader, [])
                                    if "CLIENTE" in enc:
                                        c_cli = datos[enc.index("CLIENTE")].strip()
                                    if "PRODUCTO" in enc:
                                        c_prod = datos[enc.index("PRODUCTO")].strip()
                                    if "JOB NUMBER" in enc:
                                        c_job_com = datos[enc.index("JOB NUMBER")].strip()
                                    elif "JOB" in enc:
                                        c_job_com = datos[enc.index("JOB")].strip()
                            except Exception:
                                pass
                    registrar_diccionario_swo(swo_id, prefijo_adn, c_cli, c_job_com, c_prod, cred)
                    prefijos.add(prefijo_adn)
                partes_cal = row["calibre"].split("_")
                cal_num = partes_cal[0]
                mat = partes_cal[1] if len(partes_cal) > 1 else "CARBONO"
                ruta_base_job = self.obtener_ruta_real_job(config.RUTA_SERVIDOR_RAIZ, job)
                ruta_dxf = ""
                if ruta_base_job:
                    ruta_dxf = self._buscar_dxf_item_en_autodxf(os.path.join(ruta_base_job, "MODEL CORE FILES", "AutoDXF"), item)
                if ruta_dxf:
                    item_pref = f"{prefijo_adn}__{item}"
                    ruta_norm = self._normalizar_ruta(ruta_dxf)
                    self.app.meta_pdf_por_ruta[ruta_norm] = {"job": job, "item": item, "work_order": prefijo_adn}
                    items_procesados.append((item_pref, mat, str(row["qty"]), cal_num, "LISTO", ruta_dxf))
                else:
                    errores += 1
            self._ui(self.finalizar_descarga_swo, swo_id, items_procesados, errores)
        except Exception as e:
            self._ui(self.error_descarga_swo, str(e))

    def finalizar_descarga_swo(self, swo_id, items, errores):
        self.app.cerrar_ventana_carga()
        if not items:
            QMessageBox.critical(self, "Fallo Crítico", "No se encontró archivos .dxf para esta SWO.")
            return
        if errores > 0:
            QMessageBox.warning(self, "Advertencia", f"Faltaron {errores} archivos en la red.")
        self.app.job_activo = swo_id
        self.app.multiplicador_tanques = 1
        self.app.cargar_datos_parts(items)
        self.app.ir_a_tab("PARTS")
        QMessageBox.information(self, "SWO Descargada", f"¡{swo_id} inyectada con éxito!")

    def error_descarga_swo(self, err):
        self.app.cerrar_ventana_carga()
        QMessageBox.critical(self, "Error en Descarga", f"Ocurrió un problema:\n{err}")
