"""Pestaña FILES — PySide6 nativo (paridad con interface/tab_files.py oficial)."""
from __future__ import annotations


import csv
import glob
import os
import re
import shutil
import threading
import concurrent.futures
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
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
from interface.autodxf_metadata import (
    combinar_metadata_dxf,
    dxf_corresponde_a_item,
    item_sin_prefijo_wo,
    normalizar_material_autodxf,
    parsear_nombre_archivo_dxf,
)
from modules.processed_layers import ProcesadorDXF
from modules.scanner import EscanerServidor
from interface.qt.layout_helpers import make_card, make_scroll
from interface.qt.thread_bridge import call_on_main
from interface.qt.ui_scale import fit_window, s, sp, ui_factor
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

        scroll = make_scroll(self)
        host = QWidget()
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(s(8), s(8), s(8), s(8))

        card = make_card()
        card.setMinimumSize(s(720, min_px=480), s(480, min_px=360))
        card.setMaximumWidth(s(980, min_px=560))
        m = s(48, min_px=16)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(m, s(44, min_px=16), m, s(44, min_px=16))
        lay.setSpacing(s(18, min_px=10))

        title = QLabel("CONEXIÓN CON EL SERVIDOR")
        title.setStyleSheet(
            f"font-size:{s(22, min_px=15)}px;font-weight:700;color:{COLOR_TEXTO_SUBTITULO};"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        bw, bh = s(450, min_px=280), s(80, min_px=52)
        fs_main = s(16, min_px=12)
        pad_main = sp("12px 20px")

        self.btn_nest_scan = QPushButton("IMPORTAR JOB INDIVIDUAL\n(INGENIERÍA)")
        self.btn_nest_scan.setFixedSize(bw, bh)
        apply_push_button(
            self.btn_nest_scan, COLOR_GRIS_DARK, font_size=fs_main, padding=pad_main
        )
        self.btn_nest_scan.clicked.connect(self.ejecutar_escaneo_servidor)
        lay.addWidget(self.btn_nest_scan, alignment=Qt.AlignmentFlag.AlignCenter)

        self.btn_swo_web = QPushButton("IMPORTAR S.W.O.\n(FUSIÓN DESDE TABLERO WEB)")
        self.btn_swo_web.setFixedSize(bw, bh)
        apply_push_button(
            self.btn_swo_web, "#455E75", hover="#334659", font_size=fs_main, padding=pad_main
        )
        self.btn_swo_web.clicked.connect(self.buscar_swos_pendientes)
        lay.addWidget(self.btn_swo_web, alignment=Qt.AlignmentFlag.AlignCenter)

        self.btn_step_feedstock = QPushButton("PROCESAR STEP DEL JOB\n(COMPLEMENTO LOCAL)")
        self.btn_step_feedstock.setFixedSize(bw, s(72, min_px=48))
        apply_push_button(
            self.btn_step_feedstock,
            "#0F766E",
            hover="#0D9488",
            font_size=s(14, min_px=11),
            padding=sp("10px 18px"),
        )
        self.btn_step_feedstock.setToolTip(
            "Busca .stp/.step dentro de AutoDXF del job activo, genera DXF en "
            "AutoDXF/FROM_STEP/ (placas planas MVP) y los carga a PARTS. "
            "No reemplaza Inventor/AutoDXF."
        )
        self.btn_step_feedstock.clicked.connect(self.ejecutar_step_feedstock)
        lay.addWidget(self.btn_step_feedstock, alignment=Qt.AlignmentFlag.AlignCenter)

        self.btn_historial = QPushButton("GESTIONAR HISTORIAL\n(JOBS YA IMPORTADOS)")
        self.btn_historial.setFixedSize(bw, s(56, min_px=40))
        apply_push_button(
            self.btn_historial,
            "#FFFFFF",
            font_size=s(12, min_px=10),
            padding=sp("8px 16px"),
        )
        self.btn_historial.clicked.connect(self.mostrar_gestion_historial)
        lay.addWidget(self.btn_historial, alignment=Qt.AlignmentFlag.AlignCenter)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{COLOR_BORDE};max-height:1px;")
        lay.addWidget(sep)

        engine_title = QLabel("MOTOR DE NESTEO")
        engine_title.setStyleSheet(
            f"font-size:{s(13, min_px=10)}px;font-weight:700;color:{COLOR_TEXTO_SECUNDARIO};letter-spacing:0.5px;"
        )
        engine_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(engine_title)

        from modules.nesting_engine.engine_registry import list_engine_metas
        from modules.nesting_engine.nest_engine_config import load_default_steel_engine_id

        self._engine_combo = QComboBox()
        self._engine_combo.setObjectName("HerinoxCombo")
        self._engine_combo.setFixedSize(bw, s(40, min_px=28))
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
        self.lbl_engine_status.setStyleSheet(
            f"color:{COLOR_TEXTO_MUTED};font-size:{s(11, min_px=9)}px;"
        )
        self.lbl_engine_status.setWordWrap(True)
        self.lbl_engine_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._refresh_engine_status_label(current_eid)
        lay.addWidget(self.lbl_engine_status)

        host_lay.addStretch()
        host_lay.addWidget(card, alignment=Qt.AlignmentFlag.AlignCenter)
        host_lay.addStretch()
        scroll.setWidget(host)
        outer.addWidget(scroll)
        self.refrescar_step_feedstock_ui()
        try:
            print(f"[UI-SCALE][FILES] factor={ui_factor():.3f} btn={bw}x{bh}", flush=True)
        except Exception:
            pass

    def refrescar_step_feedstock_ui(self) -> None:
        """Muestra/oculta el 3er botón según Configuración Global."""
        try:
            from modules.nesting_engine.nest_runtime_prefs import is_step_feedstock_enabled

            on = bool(is_step_feedstock_enabled())
        except Exception:
            on = False
        btn = getattr(self, "btn_step_feedstock", None)
        if btn is not None:
            btn.setVisible(on)

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
        # from_step / step: salida o materia del complemento STEP (no mezclar con Inventor).
        excluidas = {
            "processed files",
            "procesados",
            "nesting",
            "__pycache__",
            "from_step",
            "step",
        }
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
            pieza, mat, qty_str, cal = self._parsear_nombre_dxf(arch, ruta_origen=ruta_in)
            from modules.nesting_engine.nest_runtime_prefs import should_omit_copper_marks

            omit_marcaje = should_omit_copper_marks(mat)
            ruta_out_real = os.path.join(carpeta_procesados, self._nombre_destino_unico(arch, nombres_usados))
            try:
                ok_proc = self.procesador.limpiar_archivo(
                    ruta_in, ruta_out_real, omit_marcaje=omit_marcaje
                )
                if (not ok_proc) or (not os.path.exists(ruta_out_real)):
                    shutil.copy2(ruta_in, ruta_out_real)
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

    def _contexto_swo_activo(self) -> bool:
        job = str(getattr(self.app, "job_activo", "") or "").strip().upper()
        if re.match(r"^S\.?W\.?O", job):
            return True
        prev = getattr(self.app, "datos_partes_actuales", None) or []
        for row in prev:
            if not row:
                continue
            nom = str(row[0] or "")
            if "__" in nom and item_sin_prefijo_wo(nom) != nom:
                return True
        meta = getattr(self.app, "meta_pdf_por_ruta", None) or {}
        return any(
            str((info or {}).get("work_order") or "").strip()
            for info in meta.values()
            if isinstance(info, dict)
        )

    def _fusionar_reproceso_swo(self, prev_items, scanned_items, scanned_meta: dict):
        """Reprocesar AutoDXF en SWO: actualiza rutas DXF sin quitar prefijo WO ni qty BD."""
        by_item: dict[str, tuple] = {}
        for row in scanned_items or []:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            pieza = str(row[0] or "").strip()
            key = pieza.lower()
            if key and key not in by_item:
                by_item[key] = tuple(row)

        prev_meta = dict(getattr(self.app, "meta_pdf_por_ruta", None) or {})
        out = []
        new_meta: dict = {}
        for row in prev_items or []:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            nombre = str(row[0] or "").strip()
            item_naked = item_sin_prefijo_wo(nombre)
            scanned = by_item.get(item_naked.lower())
            ruta_prev = str(row[5] or "")
            ruta_prev_norm = self._normalizar_ruta(ruta_prev) if ruta_prev else ""
            meta_prev = dict(prev_meta.get(ruta_prev_norm) or {})
            if not meta_prev.get("work_order") and "__" in nombre:
                pref = nombre.split("__", 1)[0].strip()
                if pref:
                    meta_prev["work_order"] = pref
            if not meta_prev.get("item"):
                meta_prev["item"] = item_naked

            if scanned:
                ruta_nueva = str(scanned[5] or ruta_prev)
                out.append(
                    (
                        nombre,
                        row[1],
                        row[2],
                        row[3],
                        "LISTO",
                        ruta_nueva,
                    )
                )
                ruta_norm = self._normalizar_ruta(ruta_nueva)
                meta_scan = dict((scanned_meta or {}).get(ruta_norm) or {})
                new_meta[ruta_norm] = {
                    "job": meta_prev.get("job") or meta_scan.get("job") or "",
                    "item": item_naked,
                    "work_order": meta_prev.get("work_order") or "",
                }
            else:
                out.append(tuple(row))
                if ruta_prev_norm:
                    new_meta[ruta_prev_norm] = meta_prev
        return out, new_meta

    def aplicar_partes_resincronizadas(self, payload: dict, *, thumbnails_async: bool = False) -> int:
        """Aplica el resultado del escaneo en el hilo principal de Qt."""
        items = list(payload.get("items") or [])
        meta_pdf = dict(payload.get("meta_pdf") or {})
        job_name = str(payload.get("job_name") or "").strip()
        multiplicador = max(1, int(payload.get("multiplicador") or 1))
        prev_items = list(getattr(self.app, "datos_partes_actuales", []) or [])
        job_prev = str(getattr(self.app, "job_activo", "") or "").strip()
        # SWO: no reemplazar PARTS con nombres crudos del DXF (pierden W.O.__ y qty BD).
        if self._contexto_swo_activo() and prev_items:
            items, meta_pdf = self._fusionar_reproceso_swo(prev_items, items, meta_pdf)
            if job_prev:
                self.app.job_activo = job_prev
            # Mantener multiplicador de la sesión SWO (qty ya vienen de reporte_cortes).
        else:
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
        """Resuelve el DXF de un item SWO/BD con match exacto de pieza (no prefijo corto)."""
        item_limpio = str(item or "").strip()
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
        # 1) Exacto (pieza parseada == item). 2) Legacy `_TAB` solo si no hay exacto.
        alias_underscore = ""
        for ruta in ordenados:
            base = os.path.basename(ruta)
            if not dxf_corresponde_a_item(base, item_limpio):
                continue
            pieza = str(parsear_nombre_archivo_dxf(base).get("pieza") or "").strip()
            if pieza.lower() == item_limpio.lower():
                return ruta
            if not alias_underscore:
                alias_underscore = ruta
        return alias_underscore

    def ejecutar_escaneo_servidor(self):
        self.btn_nest_scan.setEnabled(False)
        self.btn_nest_scan.setText("ESCANEANDO...")
        apply_push_button(self.btn_nest_scan, "#E2E8F0", font_size=16, padding="12px 20px")
        threading.Thread(target=self.thread_escaneo, daemon=True).start()

    def ejecutar_step_feedstock(self):
        """Complemento: STEP dentro de AutoDXF → FROM_STEP DXF → PARTS."""
        try:
            from modules.nesting_engine.nest_runtime_prefs import is_step_feedstock_enabled

            if not is_step_feedstock_enabled():
                QMessageBox.information(
                    self,
                    "Feedstock STEP",
                    "Activa el switch en Configuración Global para usar este complemento.",
                )
                self.refrescar_step_feedstock_ui()
                return
        except Exception:
            pass

        autodxf = self._resolver_autodxf_desde_datos_actuales()
        step_override = None
        if not autodxf:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Seleccionar STEP dentro de AutoDXF (o carpeta del job)",
                "",
                "STEP (*.stp *.step);;Todos (*.*)",
            )
            if not path:
                QMessageBox.information(
                    self,
                    "Feedstock STEP",
                    "Cancelado. También puedes importar el job primero y dejar el "
                    ".stp en AutoDXF/ o AutoDXF/STEP/.",
                )
                return
            step_override = path
            autodxf = self._inferir_autodxf_desde_step(path)
            if not autodxf:
                QMessageBox.warning(
                    self,
                    "Feedstock STEP",
                    "El STEP debe estar dentro de una carpeta AutoDXF "
                    "(…/MODEL CORE FILES/AutoDXF/…).",
                )
                return

        self.btn_step_feedstock.setEnabled(False)
        self.btn_step_feedstock.setText("PROCESANDO STEP…")
        apply_push_button(
            self.btn_step_feedstock, "#E2E8F0", font_size=14, padding="10px 18px"
        )
        if hasattr(self.app, "abrir_ventana_carga"):
            self.app.abrir_ventana_carga("Procesando STEP → DXF…")
        threading.Thread(
            target=self._thread_step_feedstock,
            args=(autodxf, step_override),
            daemon=True,
        ).start()

    def _inferir_autodxf_desde_step(self, step_path: str) -> str | None:
        try:
            actual = os.path.normpath(str(step_path))
            if os.path.isfile(actual):
                actual = os.path.dirname(actual)
            while actual and actual not in (actual[:1], os.path.dirname(actual)):
                if os.path.basename(actual).strip().lower() == "autodxf":
                    return actual
                actual = os.path.dirname(actual)
        except Exception:
            pass
        return None

    def _thread_step_feedstock(self, carpeta_autodxf: str, step_override=None):
        err = None
        payload = None
        try:
            payload = self._preparar_step_feedstock(carpeta_autodxf, step_override)
        except Exception as e:
            err = str(e)
        self._ui(self._finalizar_step_feedstock, payload, err)

    def _preparar_step_feedstock(self, carpeta_autodxf: str, step_override=None) -> dict:
        from modules.tank_step_feedstock import (
            FROM_STEP_DIRNAME,
            process_autodxf_step_feedstock,
        )

        def _progress(msg, pct):
            if hasattr(self.app, "actualizar_progreso"):
                self.app.actualizar_progreso(msg, pct)

        result = process_autodxf_step_feedstock(
            carpeta_autodxf,
            step_path=step_override,
            progress_cb=_progress,
        )
        if not result.ok:
            raise RuntimeError(result.message or "No se pudo procesar el STEP.")

        from_step = os.path.join(carpeta_autodxf, FROM_STEP_DIRNAME)
        job_name = self._infer_job_desde_autodxf(carpeta_autodxf) or str(
            getattr(self.app, "job_activo", "") or ""
        ).strip()
        multiplicador = self._leer_multiplicador_desde_job_data(carpeta_autodxf, job_name)

        # Procesar solo FROM_STEP (no mezclar con DXF Inventor).
        rutas_dxf = sorted(
            set(self._listar_dxfs_recursivo_incluir_from_step(from_step)),
            key=self._normalizar_ruta,
        )
        if not rutas_dxf:
            raise RuntimeError(
                "El STEP se procesó pero no hay DXF en FROM_STEP.\n" + result.message
            )

        carpeta_procesados = os.path.join(from_step, "Processed Files")
        os.makedirs(carpeta_procesados, exist_ok=True)
        items_procesados, nombres_usados = [], set()
        meta_pdf = {}
        total = len(rutas_dxf)
        for idx, ruta_in in enumerate(rutas_dxf, start=1):
            _progress(f"Validando DXF STEP {idx}/{total}…", idx / max(1, total))
            arch = os.path.basename(ruta_in)
            pieza, mat, qty_str, cal = self._parsear_nombre_dxf(arch, ruta_origen=ruta_in)
            from modules.nesting_engine.nest_runtime_prefs import should_omit_copper_marks

            omit_marcaje = should_omit_copper_marks(mat)
            ruta_out_real = os.path.join(
                carpeta_procesados, self._nombre_destino_unico(arch, nombres_usados)
            )
            try:
                ok_proc = self.procesador.limpiar_archivo(
                    ruta_in, ruta_out_real, omit_marcaje=omit_marcaje
                )
                if (not ok_proc) or (not os.path.exists(ruta_out_real)):
                    shutil.copy2(ruta_in, ruta_out_real)
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
                items_procesados.append(
                    (arch, "?", str(multiplicador), "?", "LISTO", ruta_out_real)
                )

        return {
            "items": items_procesados,
            "meta_pdf": meta_pdf,
            "job_name": job_name,
            "multiplicador": multiplicador,
            "summary": result.message,
            "step_name": result.step_path.name if result.step_path else "",
            "n_exports": len(result.exports),
            "n_skipped": len(result.report.skipped) if result.report else 0,
        }

    def _listar_dxfs_recursivo_incluir_from_step(self, carpeta_base):
        """Lista DXF bajo FROM_STEP (excluye solo Processed Files internos)."""
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

    def _finalizar_step_feedstock(self, payload, err=None):
        if hasattr(self.app, "cerrar_ventana_carga"):
            self.app.cerrar_ventana_carga()
        self.btn_step_feedstock.setEnabled(True)
        self.btn_step_feedstock.setText("PROCESAR STEP DEL JOB\n(COMPLEMENTO LOCAL)")
        apply_push_button(
            self.btn_step_feedstock, "#0F766E", hover="#0D9488", font_size=14, padding="10px 18px"
        )
        self.refrescar_step_feedstock_ui()
        if err:
            QMessageBox.critical(self, "Feedstock STEP", f"Error al procesar STEP:\n{err}")
            return
        if not payload:
            QMessageBox.critical(self, "Feedstock STEP", "No se pudo completar el proceso.")
            return
        self.app.job_activo = payload["job_name"]
        self.app.multiplicador_tanques = payload["multiplicador"]
        self.app.meta_pdf_por_ruta = payload["meta_pdf"]
        self.app.cargar_datos_parts(payload["items"])
        self.app.ir_a_tab("PARTS")
        extra = ""
        if payload.get("n_skipped"):
            extra = (
                f"\n\nOmitidos (no placa plana / con doblez): {payload['n_skipped']}. "
                "El MVP solo aplana sólidos de espesor constante."
            )
        QMessageBox.information(
            self,
            "Feedstock STEP",
            f"{payload.get('summary') or 'Listo.'}\n"
            f"Piezas cargadas a PARTS: {len(payload.get('items') or [])}."
            f"{extra}",
        )

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
        # Sin stretch al inicio: si no, las filas quedan abajo y el diálogo
        # parece vacío (SWO-058: solo se veía la tarjeta al fondo).
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
        fit_window(dlg, 820, 620)
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
        lay.addStretch(1)
        dlg.exec()

    def obtener_rutas_reales_job(self, ruta_raiz, nombre_job) -> list[str]:
        """Todas las carpetas de job bajo producto/cliente (mismo número, varios productos).

        Caso SWO-058 / job 25432: existe en ATC_COMPARTMENT y en TANKS; ambas
        tienen AutoDXF. Devolver todas (AutoDXF primero) para no quedarnos con
        la primera alfabética vacía de piezas.
        """
        if not os.path.exists(ruta_raiz):
            return []
        job_pedido = str(nombre_job or "").strip()
        if not job_pedido:
            return []

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
            return []

        def _tiene_autodxf(ruta_job: str) -> bool:
            return os.path.isdir(os.path.join(ruta_job, "MODEL CORE FILES", "AutoDXF"))

        vistos, orden = set(), []
        for grupo in (exactas, equivalentes):
            for r in grupo:
                k = os.path.normcase(os.path.normpath(r))
                if k not in vistos:
                    vistos.add(k)
                    orden.append(r)
        con_ad = [r for r in orden if _tiene_autodxf(r)]
        sin_ad = [r for r in orden if not _tiene_autodxf(r)]
        return con_ad + sin_ad

    def obtener_ruta_real_job(
        self, ruta_raiz, nombre_job, items_hint=None, prefer_ruta=None, product_hint=None
    ):
        """Resuelve carpeta de job bajo producto/cliente.

        El VSM a veces nombra el job sin espacios (GIGABOARD5) y en red queda
        un duplicado con espacios (GIGA BOARD 5). Prefiere la carpeta que tenga
        MODEL CORE FILES/AutoDXF.

        Desempate (SWO-058 / job en ATC y TANKS):
        1) ``prefer_ruta`` (job root derivado de ``ruta_exportacion`` en BD)
        2) ``product_hint`` (p. ej. TANKS del VSM / job_data)
        3) ``items_hint`` → carpeta que resuelve más DXF de la SWO
        """
        orden = self.obtener_rutas_reales_job(ruta_raiz, nombre_job)
        if not orden:
            return None

        prefer = self._normalizar_ruta(prefer_ruta) if prefer_ruta else ""
        if prefer:
            for r in orden:
                if self._normalizar_ruta(r) == prefer:
                    return r

        prod = str(product_hint or "").strip().upper()
        if prod:
            prod_key = re.sub(r"[^A-Z0-9]+", "", prod)
            for r in orden:
                segs = [re.sub(r"[^A-Z0-9]+", "", s.upper()) for s in Path(r).parts]
                if prod_key and prod_key in segs:
                    return r
                # ATC/COMPARTMENT ↔ ATC_COMPARTMENT
                if "ATC" in prod_key and any(s.startswith("ATC") for s in segs):
                    return r

        hints = [str(x or "").strip() for x in (items_hint or []) if str(x or "").strip()]
        if len(orden) > 1 and hints:
            mejor, mejor_n = orden[0], -1
            for ruta_job in orden:
                autodxf = os.path.join(ruta_job, "MODEL CORE FILES", "AutoDXF")
                if not os.path.isdir(autodxf):
                    continue
                n = sum(
                    1
                    for it in hints
                    if self._buscar_dxf_item_en_autodxf(autodxf, it)
                )
                if n > mejor_n:
                    mejor, mejor_n = ruta_job, n
            if mejor_n > 0:
                return mejor
        return orden[0]

    @staticmethod
    def _job_root_desde_ruta_exportacion(ruta_exportacion: str | None) -> str:
        """Sube desde ``…/JOB/MODEL CORE FILES/W.O.…`` hasta la carpeta del job."""
        ruta = str(ruta_exportacion or "").strip()
        if not ruta:
            return ""
        actual = os.path.normpath(ruta)
        for _ in range(12):
            base = os.path.basename(actual).strip().lower()
            padre = os.path.dirname(actual)
            if base == "model core files" and padre:
                return padre
            if not padre or padre == actual:
                break
            actual = padre
        return ""

    @staticmethod
    def _producto_cliente_desde_job_root(ruta_job: str) -> tuple[str, str]:
        """De ``…/PRODUCTO/CLIENTE/JOB`` → (producto, cliente)."""
        try:
            p = Path(os.path.normpath(str(ruta_job or "")))
            parts = list(p.parts)
            if len(parts) < 3:
                return "", ""
            return str(parts[-3]), str(parts[-2])
        except Exception:
            return "", ""

    def _ordenar_rutas_job_preferidas(
        self, rutas_job: list[str], prefer_ruta: str | None = None, product_hint: str | None = None
    ) -> list[str]:
        if not rutas_job:
            return []
        prefer = self._normalizar_ruta(prefer_ruta) if prefer_ruta else ""
        prod = str(product_hint or "").strip().upper()
        prod_key = re.sub(r"[^A-Z0-9]+", "", prod) if prod else ""

        def _score(ruta: str) -> tuple[int, str]:
            rn = self._normalizar_ruta(ruta)
            score = 0
            if prefer and rn == prefer:
                score += 100
            if prod_key:
                segs = [re.sub(r"[^A-Z0-9]+", "", s.upper()) for s in Path(ruta).parts]
                if prod_key in segs:
                    score += 50
                elif "ATC" in prod_key and any(s.startswith("ATC") for s in segs):
                    score += 50
            return (-score, rn)

        return sorted(rutas_job, key=_score)

    def _buscar_dxf_item_en_jobs(
        self, rutas_job, item, prefer_ruta=None, product_hint=None
    ) -> str:
        """Busca el DXF del item; prioriza carpeta BD (ruta_exportacion / producto)."""
        ordenadas = self._ordenar_rutas_job_preferidas(
            list(rutas_job or []), prefer_ruta=prefer_ruta, product_hint=product_hint
        )
        for ruta_job in ordenadas:
            autodxf = os.path.join(ruta_job, "MODEL CORE FILES", "AutoDXF")
            hit = self._buscar_dxf_item_en_autodxf(autodxf, item)
            if hit:
                return hit
        return ""

    def _validar_origen_swo(
        self,
        *,
        prefer_ruta: str,
        product_hint: str,
        items: list,
    ) -> dict:
        """Resume qué se pidió y desde qué producto/carpeta se resolvieron los DXF."""
        prod_esperado, cli_esperado = self._producto_cliente_desde_job_root(prefer_ruta)
        if product_hint and not prod_esperado:
            prod_esperado = product_hint
        origenes: dict[str, int] = {}
        mismatch = 0
        for tup in items or []:
            ruta_dxf = tup[5] if len(tup) > 5 else ""
            root = ""
            actual = os.path.normpath(str(ruta_dxf or ""))
            for _ in range(16):
                if os.path.basename(actual).strip().lower() == "model core files":
                    root = os.path.dirname(actual)
                    break
                padre = os.path.dirname(actual)
                if not padre or padre == actual:
                    break
                actual = padre
            prod, cli = self._producto_cliente_desde_job_root(root)
            clave = f"{prod}/{cli}" if prod else (root or "?")
            origenes[clave] = origenes.get(clave, 0) + 1
            if prefer_ruta and root and self._normalizar_ruta(root) != self._normalizar_ruta(
                prefer_ruta
            ):
                mismatch += 1
            elif prod_esperado and prod and prod.upper() != prod_esperado.upper():
                # ATC/COMPARTMENT vs ATC_COMPARTMENT
                a = re.sub(r"[^A-Z0-9]+", "", prod.upper())
                b = re.sub(r"[^A-Z0-9]+", "", prod_esperado.upper())
                if a != b and not (a.startswith("ATC") and b.startswith("ATC")):
                    mismatch += 1
        return {
            "producto": prod_esperado or product_hint or "",
            "cliente": cli_esperado or "",
            "carpeta": prefer_ruta or "",
            "origenes": origenes,
            "mismatch": mismatch,
        }

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
                "SELECT job, work_order, calibre, item, COUNT(*) as qty, "
                "MAX(NULLIF(TRIM(ruta_exportacion), '')) AS ruta_exportacion "
                "FROM reporte_cortes "
                "WHERE super_work_order = %s AND estatus = 'Pendiente SWO' "
                "GROUP BY job, work_order, calibre, item",
                (swo_id,),
            )
            items_db = cur.fetchall()
            cur.close()
            con.close()
            items_procesados, errores, prefijos = [], 0, set()
            self.app.meta_pdf_por_ruta = {}
            items_hint = [str(r.get("item") or "").strip() for r in items_db]
            # Fuente de verdad VSM/BD: ruta_exportacion (SWO-058 → TANKS\VANTRAN\25432)
            prefer_por_job: dict[str, str] = {}
            product_por_job: dict[str, str] = {}
            for row in items_db:
                job = str(row.get("job") or "").strip()
                if not job or job in prefer_por_job:
                    continue
                root = self._job_root_desde_ruta_exportacion(row.get("ruta_exportacion"))
                if root:
                    prefer_por_job[job] = root
                    prod, _cli = self._producto_cliente_desde_job_root(root)
                    if prod:
                        product_por_job[job] = prod
            rutas_por_job: dict[str, list[str]] = {}
            for row in items_db:
                job, work_order, item = row["job"], row["work_order"], row["item"]
                prefijo_adn = work_order.strip().upper()
                if job not in rutas_por_job:
                    rutas_por_job[job] = self.obtener_rutas_reales_job(
                        config.RUTA_SERVIDOR_RAIZ, job
                    )
                rutas_job = rutas_por_job[job]
                prefer_ruta = prefer_por_job.get(job, "")
                product_hint = product_por_job.get(job, "")
                if prefijo_adn not in prefijos:
                    ruta_base_job = self.obtener_ruta_real_job(
                        config.RUTA_SERVIDOR_RAIZ,
                        job,
                        items_hint=items_hint,
                        prefer_ruta=prefer_ruta or None,
                        product_hint=product_hint or None,
                    )
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
                    if product_hint and (not c_prod or c_prod == "N/A"):
                        c_prod = product_hint
                    registrar_diccionario_swo(swo_id, prefijo_adn, c_cli, c_job_com, c_prod, cred)
                    prefijos.add(prefijo_adn)
                    if ruta_base_job and job not in prefer_por_job:
                        prefer_por_job[job] = ruta_base_job
                        product_por_job[job] = c_prod if c_prod != "N/A" else product_hint
                partes_cal = row["calibre"].split("_")
                cal_num = partes_cal[0]
                mat = partes_cal[1] if len(partes_cal) > 1 else "CARBONO"
                ruta_dxf = self._buscar_dxf_item_en_jobs(
                    rutas_job,
                    item,
                    prefer_ruta=prefer_ruta or None,
                    product_hint=product_hint or None,
                )
                if ruta_dxf:
                    item_pref = f"{prefijo_adn}__{item}"
                    ruta_norm = self._normalizar_ruta(ruta_dxf)
                    self.app.meta_pdf_por_ruta[ruta_norm] = {
                        "job": job,
                        "item": item,
                        "work_order": prefijo_adn,
                        "producto": product_hint,
                        "carpeta_job": prefer_ruta,
                    }
                    items_procesados.append((item_pref, mat, str(row["qty"]), cal_num, "LISTO", ruta_dxf))
                else:
                    errores += 1
            prefer_txt = next(iter(prefer_por_job.values()), "")
            product_txt = next(iter(product_por_job.values()), "")
            validacion = self._validar_origen_swo(
                prefer_ruta=prefer_txt,
                product_hint=product_txt,
                items=items_procesados,
            )
            self._ui(
                self.finalizar_descarga_swo,
                swo_id,
                items_procesados,
                errores,
                validacion,
            )
        except Exception as e:
            self._ui(self.error_descarga_swo, str(e))

    def finalizar_descarga_swo(self, swo_id, items, errores, validacion=None):
        self.app.cerrar_ventana_carga()
        if not items:
            QMessageBox.critical(
                self,
                "Fallo Crítico",
                "No se encontró archivos .dxf para esta SWO.\n"
                "Revisa que el job exista bajo el producto correcto "
                "(p. ej. TANKS vs ATC_COMPARTMENT) y que AutoDXF tenga las piezas.",
            )
            return
        if errores > 0:
            QMessageBox.warning(self, "Advertencia", f"Faltaron {errores} archivos en la red.")
        self.app.job_activo = swo_id
        self.app.multiplicador_tanques = 1
        self.app.cargar_datos_parts(items)
        self.app.ir_a_tab("PARTS")
        val = validacion or {}
        prod = val.get("producto") or "?"
        cli = val.get("cliente") or "?"
        carpeta = val.get("carpeta") or "?"
        origenes = val.get("origenes") or {}
        origen_txt = ", ".join(f"{k}×{n}" for k, n in origenes.items()) or "—"
        mismatch = int(val.get("mismatch") or 0)
        detalle = (
            f"¡{swo_id} inyectada con éxito!\n\n"
            f"Pedido / origen BD: {prod} · {cli}\n"
            f"Carpeta job: {carpeta}\n"
            f"Piezas resueltas: {len(items)}\n"
            f"DXF desde: {origen_txt}"
        )
        if mismatch:
            QMessageBox.warning(
                self,
                "SWO con origen mixto",
                detalle
                + f"\n\n⚠ {mismatch} DXF no coinciden con la carpeta BD "
                f"(posible cruce ATC/TANKS). Revisa PARTS antes de nestear.",
            )
        else:
            QMessageBox.information(self, "SWO Descargada", detalle)

    def error_descarga_swo(self, err):
        self.app.cerrar_ventana_carga()
        QMessageBox.critical(self, "Error en Descarga", f"Ocurrió un problema:\n{err}")
