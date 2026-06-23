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
        self._nesting_executor = None
        self._ventana_carga_abierta = False
        self._progress_dialog: ProgressDialog | None = None

        init_thread_bridge()
        self._init_estado()
        self._build_ui()
        QTimer.singleShot(200, self._mostrar_maximizado)
        QTimer.singleShot(300, self._intentar_sync_placas_react_herinox_async)
        QTimer.singleShot(400, self._precalentar_animacion_logo)

    def _precalentar_animacion_logo(self) -> None:
        try:
            from interface.qt.logo_anim_service import warm_logo_anim_service

            warm_logo_anim_service()
        except Exception:
            pass

    def _init_estado(self):
        self.jobs_procesados = self.cargar_historial()
        self.plates_manager = PlatesManager()
        self.ultimo_resultado_sync_herinox = None
        self.herinox_tc_dof = 18.50
        self.herinox_tc_fuente = "FALLBACK"
        self.herinox_nominal_by_code = {}
        self.motor_nesting = MotorNesting()
        self.datos_placas_empresa, self.datos_placas_proveedor = self.plates_manager.obtener_datos_placas_divididos()
        self.datos_partes_actuales = []
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
        if os.path.exists(config.DB_HISTORIAL):
            try:
                with open(config.DB_HISTORIAL, "r") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def guardar_historial(self, job_path):
        import os

        ruta = os.path.normcase(os.path.normpath(str(job_path or "")))
        historial = {os.path.normcase(os.path.normpath(str(p))) for p in self.jobs_procesados}
        if ruta and ruta not in historial:
            self.jobs_procesados.append(job_path)
            try:
                with open(config.DB_HISTORIAL, "w") as f:
                    json.dump(self.jobs_procesados, f)
            except Exception:
                pass

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

    def tarea_cancelada(self):
        return self._cancelar_tarea_flag.is_set()

    def cancelar_tarea_actual(self, desde_popup=False):
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

    def abrir_ventana_carga(self, titulo="Ejecutando Nesting"):
        if self._ventana_carga_abierta:
            self.cerrar_ventana_carga(solicitud_usuario=False)
        self.reiniciar_cancelacion_tarea()
        if hasattr(self.motor_nesting, "set_cancel_checker"):
            self.motor_nesting.set_cancel_checker(self.tarea_cancelada)
        self._progress_dialog = ProgressDialog(self, titulo)
        self._ventana_carga_abierta = True
        self._progress_dialog.show()

    def actualizar_progreso(self, mensaje, porcentaje):
        dlg = self._progress_dialog
        if not dlg or not self._ventana_carga_abierta:
            return
        if getattr(dlg, "_usar_animacion", False):
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
        try:
            from interface.qt.logo_anim_service import shutdown_logo_anim_service

            shutdown_logo_anim_service()
        except Exception:
            pass
        event.accept()

    def _extractor_numerico(self, valor):
        try:
            nums = re.findall(r"[-+]?\d*\.\d+|\d+", str(valor).replace(",", ""))
            return float(nums[0]) if nums else 0.0
        except Exception:
            return 0.0
