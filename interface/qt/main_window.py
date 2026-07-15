"""Ventana principal nativa PySide6 — paridad con interface/main_window.py oficial."""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMainWindow, QTabWidget, QVBoxLayout, QWidget

import config
from modules.nesting_engine import MotorNesting
from modules.sheets_manager import PlatesManager
from interface.qt.progress_dialog import ProgressDialog
from interface.qt.tabs.tab_files import TabFiles
from interface.qt.tabs.tab_nesting import TabNesting
from interface.qt.tabs.tab_parts import TabParts
from interface.qt.tabs.tab_sheets import TabSheets
from interface.qt.theme import COLOR_FONDO_APP, COLOR_TEXTO_SECUNDARIO
from interface.qt.thread_bridge import call_on_main, call_on_main_later, init_thread_bridge
from interface.qt.widgets.herinox_switch import HerinoxSwitch


def asegurar_instancia_unica():
    puerto_secreto = 65432

    def escuchar_kill():
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.bind(("localhost", puerto_secreto))
            server.listen(1)
            while True:
                conn, _ = server.accept()
                if conn.recv(1024).decode() == "CERRAR":
                    os._exit(0)
        except Exception:
            pass

    try:
        cliente = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cliente.connect(("localhost", puerto_secreto))
        cliente.sendall(b"CERRAR")
        cliente.close()
        time.sleep(0.5)
    except ConnectionRefusedError:
        pass
    threading.Thread(target=escuchar_kill, daemon=True).start()


import multiprocessing

if multiprocessing.current_process().name == "MainProcess":
    asegurar_instancia_unica()


class SistemaNestingPro(QMainWindow):
    def __init__(self):
        super().__init__()
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GrupoArga.NestingSuite.V4")
        except Exception:
            pass

        self.setWindowTitle("ARGA NESTING SUITE")
        self.setMinimumSize(1000, 650)
        self._cancelar_tarea_flag = threading.Event()
        self._accept_best_flag = threading.Event()
        self._abort_task_flag = threading.Event()
        self._nesting_executor = None
        self._ventana_carga_abierta = False
        self._progress_dialog: ProgressDialog | None = None

        init_thread_bridge()
        self._init_estado()
        self._build_ui()
        QTimer.singleShot(200, self._mostrar_maximizado)
        QTimer.singleShot(300, self._intentar_sync_placas_react_herinox_async)
        QTimer.singleShot(3500, self._intentar_auto_update_async)

    def _init_estado(self):
        self.jobs_procesados = self.cargar_historial()
        self.plates_manager = PlatesManager()
        self.ultimo_resultado_sync_herinox = None
        self.herinox_tc_dof = 18.50
        self.herinox_tc_fuente = "FALLBACK"
        self.herinox_nominal_by_code = {}
        self.motor_nesting = MotorNesting()
        from modules.nesting_engine.nest_engine_config import apply_saved_steel_engine

        apply_saved_steel_engine(self.motor_nesting)
        self.datos_placas_empresa, self.datos_placas_proveedor = self.plates_manager.obtener_datos_placas_divididos()
        self.datos_partes_actuales = []
        self.dxf_nesting_audit = {"total": 0, "ok": 0, "omitidos": []}
        self.orientacion_cobre_por_ruta = {}
        self._parts_ui_pendiente = None
        self.resultados_nesting = {}
        self.resultados_multilote = []
        self.meta_pdf_por_ruta = {}
        self.job_activo = "NESTING"
        self.ultimos_escenarios = []
        self.wo_reales_por_lote = {}
        self.plan_largos_por_lote = {}
        self.exclusiones_largos_pedido_por_lote = {}
        self.exclusiones_mrl_unidades_por_lote = {}
        self.plan_largos_job = ""
        self.editable_inputs_by_lote = []
        self.editable_inputs_actuales = []
        self.lote_editado_dirty = False
        self.source_dxf_paths_workspace = []
        self.source_dxf_paths_by_lote = []
        self.exportar_a_servidor = True
        self.COL_CONFIG = [
            {"min": 250, "weight": 4}, {"min": 150, "weight": 2}, {"min": 80, "weight": 1},
            {"min": 120, "weight": 1}, {"min": 100, "weight": 1}, {"min": 100, "weight": 1},
        ]
        self.COL_CONFIG_SHEETS = [
            {"min": 100, "weight": 1}, {"min": 150, "weight": 2}, {"min": 90, "weight": 1},
            {"min": 80, "weight": 1}, {"min": 80, "weight": 1}, {"min": 80, "weight": 1},
            {"min": 80, "weight": 1}, {"min": 130, "weight": 2}, {"min": 60, "weight": 1},
        ]

    def _build_ui(self):
        central = QWidget()
        central.setStyleSheet(f"background:{COLOR_FONDO_APP};")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(28, 12, 28, 22)
        root.setSpacing(10)

        from interface.qt.layout_helpers import _soft_shadow

        navbar = QWidget()
        navbar.setObjectName("NavBar")
        navbar.setFixedHeight(108)
        _soft_shadow(navbar, blur=16, y_offset=1, alpha=24)
        nav_lay = QHBoxLayout(navbar)
        nav_lay.setContentsMargins(28, 14, 28, 14)
        logo_lbl = QLabel()
        logo_lbl.setStyleSheet("background:transparent;border:none;padding:0;margin:0;")
        try:
            logo_path = config.ruta_recurso(os.path.join("assets", "branding", "logo_main.png"))
            if os.path.exists(logo_path):
                pix = QPixmap(logo_path).scaled(
                    220,
                    56,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                logo_lbl.setPixmap(pix)
                nav_lay.addWidget(logo_lbl)
            else:
                nav_lay.addWidget(QLabel("GRUPO ARGA"))
        except Exception:
            nav_lay.addWidget(QLabel("GRUPO ARGA"))
        title_lbl = QLabel("|  ARGA NESTING SUITE")
        title_lbl.setStyleSheet(
            f"color:{COLOR_TEXTO_SECUNDARIO};font-weight:700;font-size:20px;letter-spacing:0.5px;"
        )
        nav_lay.addWidget(title_lbl)
        nav_lay.addStretch()
        root.addWidget(navbar)
        root.addSpacing(4)

        self.tabview = QTabWidget()
        self.tabview.setObjectName("mainTabs")
        self.tabview.setDocumentMode(True)
        self.tabview.tabBar().setDrawBase(False)
        self.tabview.setStyleSheet(
            "QTabWidget#mainTabs::pane{border:none;background:transparent;margin:0;padding:0;}"
            "QTabWidget#mainTabs QTabBar::base{border:none;background:transparent;height:0;}"
        )
        self.vista_files = TabFiles(self.tabview, self)
        self.vista_parts = TabParts(self.tabview, self)
        self.vista_sheets = TabSheets(self.tabview, self)
        self.vista_nesting = TabNesting(self.tabview, self)
        for name, widget in [
            ("FILES", self.vista_files),
            ("PARTS", self.vista_parts),
            ("SHEETS", self.vista_sheets),
            ("NESTING", self.vista_nesting),
        ]:
            self.tabview.addTab(widget, name)
        root.addWidget(self.tabview, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(4, 0, 4, 0)
        footer.addStretch()
        self.switch_exportar_servidor = HerinoxSwitch(
            label_on="EXPORTAR A SERVIDOR Y BD",
            label_off="SOLO NESTEOS LOCALES",
            checked=True,
        )
        self.switch_exportar_servidor.toggled.connect(self._on_toggle_exportar_servidor)
        footer.addWidget(self.switch_exportar_servidor)
        root.addLayout(footer)

        try:
            icon_path = config.ruta_recurso(os.path.join("assets", "branding", "logo_icon1.png"))
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except Exception:
            pass

    def _mostrar_maximizado(self):
        self.showMaximized()
        self.raise_()
        self.activateWindow()

    def _on_toggle_exportar_servidor(self, activo: bool):
        self.exportar_a_servidor = bool(activo)

    def ir_a_tab(self, nombre: str):
        tabs = {"FILES": 0, "PARTS": 1, "SHEETS": 2, "NESTING": 3}
        idx = tabs.get(str(nombre).upper())
        if idx is not None:
            self.tabview.setCurrentIndex(idx)
        if str(nombre).upper() == "PARTS":
            self._refrescar_parts_ui_pendiente(thumbnails_async=True)

    def after(self, ms: int, callback):
        call_on_main_later(ms, callback)

    def update_idletasks(self):
        QApplication.processEvents()

    def cargar_historial(self):
        from modules.historial_jobs import cargar_nombres_jobs

        return cargar_nombres_jobs()

    def guardar_historial(self, job_ref=None):
        from modules.historial_jobs import registrar_job_nesteado

        ref = job_ref if job_ref is not None else getattr(self, "job_activo", None)
        if not ref:
            return
        self.jobs_procesados = registrar_job_nesteado(self.jobs_procesados, ref)

    def recargar_historial_jobs(self):
        self.jobs_procesados = self.cargar_historial()
        return list(self.jobs_procesados)

    def eliminar_jobs_del_historial(self, jobs):
        from modules.historial_jobs import eliminar_jobs_del_historial

        self.jobs_procesados = eliminar_jobs_del_historial(jobs)
        return list(self.jobs_procesados)

    def _intentar_sync_placas_react_herinox_async(self):
        threading.Thread(target=self._worker_sync_herinox, daemon=True).start()

    def _worker_sync_herinox(self):
        resultado = None
        try:
            from modules.consulta_herinox_bridge import refresh_herinox_bridge_json
            bridge = refresh_herinox_bridge_json(config.HERINOX_SYNC_SETTINGS_FILE)
            print(
                f"[HERINOX BRIDGE] OK | largos={bridge.get('largos_count', 0)} | "
                f"materiales={bridge.get('raw_materials_count', 0)}"
            )
        except Exception as e:
            print(f"[HERINOX BRIDGE] WARN: {e}")
        try:
            from catalogo_largos import _cargar_placas_largos_desde_herinox

            _cargar_placas_largos_desde_herinox(solo_disponibles=False)
        except Exception as e:
            print(f"[HERINOX LARGOS] WARN prefetch/respaldo: {e}")
        try:
            resultado = self.plates_manager.sincronizar_desde_react_herinox()
        except Exception as e:
            print(f"[HERINOX SYNC] ERROR inesperado: {e}")
        call_on_main(self._aplicar_sync_herinox, resultado)

    def _aplicar_sync_herinox(self, resultado):
        if resultado is None:
            return
        self.ultimo_resultado_sync_herinox = resultado
        self.herinox_tc_dof = float(getattr(resultado, "dof_rate", 18.50) or 18.50)
        self.herinox_tc_fuente = str(getattr(resultado, "dof_source", "FALLBACK") or "FALLBACK")
        self.herinox_nominal_by_code = dict(getattr(resultado, "nominal_by_code", {}) or {})
        if resultado.ok:
            print(
                f"[HERINOX SYNC] OK({resultado.source}) | hojas={resultado.sheet_count} | "
                f"coincidencias={resultado.matched_codes} | actualizadas={resultado.updated_rows} | "
                f"TC={resultado.dof_rate:.4f} ({resultado.dof_source})"
            )
            try:
                self.datos_placas_empresa, self.datos_placas_proveedor = (
                    self.plates_manager.obtener_datos_placas_divididos()
                )
                if hasattr(self.vista_sheets, "actualizar_inventario"):
                    self.vista_sheets.actualizar_inventario()
            except Exception as e:
                print(f"[HERINOX SYNC] No se pudo refrescar SHEETS: {e}")
        else:
            print(f"[HERINOX SYNC] OMITIDA | {resultado.message}")

    def cargar_datos_parts(self, datos, *, thumbnails_async: bool = False):
        from interface.utils_nesting import ordenar_filas_partes

        datos = ordenar_filas_partes(datos)
        self.datos_partes_actuales = datos
        if hasattr(self.vista_parts, "refrescar_tabla"):
            self.vista_parts.refrescar_tabla(datos, thumbnails_async=thumbnails_async)

    def _refrescar_parts_ui_pendiente(self, *, thumbnails_async: bool = True):
        datos = getattr(self, "_parts_ui_pendiente", None)
        if datos is None:
            return
        self._parts_ui_pendiente = None
        self.cargar_datos_parts(datos, thumbnails_async=thumbnails_async)

    def abrir_workspace_arganest_en_arranque(self, ruta_workspace: str):
        self.ir_a_tab("NESTING")
        if hasattr(self.vista_nesting, "cargar_workspace_async"):
            self.vista_nesting.cargar_workspace_async(ruta_workspace, mostrar_exito=False)
            return
        try:
            from nesting_workspace import cargar_workspace_desde_archivo, aplicar_workspace
            payload = cargar_workspace_desde_archivo(ruta_workspace)
            aplicar_workspace(self.vista_nesting, payload)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "No se pudo abrir workspace", f"Archivo: {ruta_workspace}\n\n{e}")

    def reiniciar_cancelacion_tarea(self):
        self._cancelar_tarea_flag.clear()
        self._accept_best_flag.clear()
        self._abort_task_flag.clear()

    def tarea_cancelada(self):
        return self._cancelar_tarea_flag.is_set()

    def tarea_acepto_mejor(self) -> bool:
        return self._accept_best_flag.is_set()

    def tarea_abortada(self) -> bool:
        """True si el usuario canceló con X (no «Aceptar mejor actual»)."""
        return self._abort_task_flag.is_set() and not self._accept_best_flag.is_set()

    def aceptar_mejor_actual(self):
        """Ultra renesteo: detiene el GA y conserva el mejor acomodo hallado."""
        self._accept_best_flag.set()
        self._abort_task_flag.clear()
        self._cancelar_tarea_flag.set()
        dlg = self._progress_dialog
        if dlg and hasattr(dlg, "marcar_aceptando"):
            try:
                dlg.marcar_aceptando()
            except Exception:
                pass

    def notificar_mejor_nest_listo(self, resumen: str = ""):
        dlg = self._progress_dialog
        if not dlg or not self._ventana_carga_abierta:
            return

        def _apply(d=dlg, r=resumen):
            if self._progress_dialog is not d or not self._ventana_carga_abierta:
                return
            try:
                d.habilitar_aceptar_mejor(r)
            except Exception:
                pass

        QTimer.singleShot(0, _apply)

    def cancelar_tarea_actual(self, desde_popup=False):
        if not self._accept_best_flag.is_set():
            self._abort_task_flag.set()
        self._cancelar_tarea_flag.set()
        if self._nesting_executor is not None:
            try:
                self._nesting_executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            self._nesting_executor = None
        if desde_popup:
            self.cerrar_ventana_carga(solicitud_usuario=True)

    def registrar_nesting_executor(self, executor):
        self._nesting_executor = executor

    def abrir_ventana_carga(self, titulo="Ejecutando Nesting", *, ultra_accept: bool = False):
        # Si había otro renest/nest en curso, abortarlo (evita botón Aceptar de otra placa).
        if self._ventana_carga_abierta:
            try:
                self._abort_task_flag.set()
                self._cancelar_tarea_flag.set()
            except Exception:
                pass
            self.cerrar_ventana_carga(solicitud_usuario=False)
        self.reiniciar_cancelacion_tarea()
        if hasattr(self.motor_nesting, "set_cancel_checker"):
            self.motor_nesting.set_cancel_checker(self.tarea_cancelada)
        self._progress_dialog = ProgressDialog(self, titulo, ultra_accept=bool(ultra_accept))
        self._ventana_carga_abierta = True
        self._progress_dialog.show()

    def actualizar_progreso(self, mensaje, porcentaje):
        dlg = self._progress_dialog
        if not dlg or not self._ventana_carga_abierta:
            return

        def _apply(d=dlg, m=mensaje, p=porcentaje):
            if self._progress_dialog is not d or not self._ventana_carga_abierta:
                return
            try:
                d.actualizar(m, p)
            except Exception:
                pass

        QTimer.singleShot(0, _apply)

    def cerrar_ventana_carga(self, solicitud_usuario=False):
        if self._progress_dialog:
            if hasattr(self._progress_dialog, "force_close"):
                self._progress_dialog.force_close()
            else:
                self._progress_dialog.close()
            self._progress_dialog = None
        self._ventana_carga_abierta = False
        if hasattr(self.motor_nesting, "set_cancel_checker"):
            self.motor_nesting.set_cancel_checker(None)
        if solicitud_usuario and hasattr(self.vista_nesting, "restaurar_controles_tras_cancelacion"):
            try:
                self.vista_nesting.restaurar_controles_tras_cancelacion()
            except Exception:
                pass

    def closeEvent(self, event):
        self.cancelar_tarea_actual()
        event.accept()

    def _intentar_auto_update_async(self):
        threading.Thread(target=self._worker_buscar_actualizacion, daemon=True).start()

    def _worker_buscar_actualizacion(self):
        try:
            from modules.app_auto_update import check_for_updates, entry_mode

            mode = entry_mode()
            print(f"[AUTO-UPDATE] Modo arranque: {mode}")
            info = check_for_updates()
        except Exception as exc:
            print(f"[AUTO-UPDATE] WARN: {exc}")
            return
        if not info.has_update:
            if info.reason_blocked and "Sin acceso" not in info.reason_blocked:
                print(f"[AUTO-UPDATE] {info.reason_blocked}")
            return
        call_on_main(self._mostrar_dialogo_actualizacion, info)

    def _preguntar_si_no(self, titulo: str, texto: str, *, default_si: bool = True) -> bool:
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(titulo)
        box.setText(texto)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        btn_si = box.button(QMessageBox.StandardButton.Yes)
        btn_no = box.button(QMessageBox.StandardButton.No)
        if btn_si is not None:
            btn_si.setText("Sí")
        if btn_no is not None:
            btn_no.setText("No")
        box.setDefaultButton(
            QMessageBox.StandardButton.Yes
            if default_si
            else QMessageBox.StandardButton.No
        )
        return box.exec() == QMessageBox.StandardButton.Yes

    def _mostrar_dialogo_actualizacion(self, info):
        from PySide6.QtWidgets import QMessageBox
        from modules.app_auto_update import dismiss_available_update, entry_mode

        resumen = (info.remote_summary or "Mejoras y correcciones en el nesting.").strip()

        if not info.can_apply:
            detalle = f"\n\n{info.reason_blocked}" if info.reason_blocked else ""
            QMessageBox.information(
                self,
                "Actualización disponible",
                f"Se encontró una actualización nueva.\n\n{resumen}{detalle}",
            )
            return

        extra = ""
        if info.needs_bootstrap:
            extra = (
                "\n\n(Esta PC aún no tiene el proyecto descargado; "
                "se instalará en la primera actualización.)"
            )

        if entry_mode() == "python":
            pregunta = "¿Actualizar el proyecto ahora?"
        else:
            pregunta = "¿Actualizar, compilar el .exe e instalar ahora?"

        if not self._preguntar_si_no(
            "Actualización disponible",
            f"Se encontró una actualización nueva.\n\n{resumen}\n\n{pregunta}{extra}",
            default_si=True,
        ):
            dismiss_available_update(info)
            return

        self.abrir_ventana_carga("Actualizando ARGA Nesting Suite…")
        threading.Thread(
            target=self._worker_aplicar_actualizacion,
            args=(info,),
            daemon=True,
        ).start()

    def _worker_aplicar_actualizacion(self, info):
        from modules.app_auto_update import apply_update

        def _prog(msg, pct):
            self.actualizar_progreso(str(msg), float(pct))

        result = apply_update(info, progress=_prog, parent_pid=os.getpid())
        call_on_main(self._finalizar_actualizacion, result)

    def _finalizar_actualizacion(self, result):
        from PySide6.QtWidgets import QMessageBox

        self.cerrar_ventana_carga()
        if not result.ok:
            QMessageBox.critical(self, "Actualización fallida", result.message)
            return

        if result.quit_app:
            QMessageBox.information(
                self,
                "Actualización en curso",
                result.message,
            )
            QApplication.quit()
            return

        QMessageBox.information(self, "Actualización correcta", result.message)
        if result.needs_restart:
            if self._preguntar_si_no(
                "Reiniciar",
                "¿Reiniciar la aplicación ahora para usar la versión nueva?",
                default_si=True,
            ):
                from modules.app_auto_update import launch_restart

                launch_restart(result)
                QApplication.quit()

    def _extractor_numerico(self, valor):
        try:
            nums = re.findall(r"[-+]?\d*\.\d+|\d+", str(valor).replace(",", ""))
            return float(nums[0]) if nums else 0.0
        except Exception:
            return 0.0
