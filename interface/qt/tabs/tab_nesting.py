from __future__ import annotations

import copy
import json
import os
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

import ezdxf
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QDialog,
    QMenu,
)

import config
from interface.autodxf_metadata import (
    extraer_metadata_carpetas_autodxf,
    normalizar_material_autodxf,
)
from interface.qt.nesting_canvas import VisorNesting
from interface.qt.ui_mixins import TimerHost, q_configure, scroll_clear, scroll_add_widget
from interface.qt.thread_bridge import call_on_main
from interface.qt.dialogs.nesting_modals import (
    abrir_modal_configuracion,
    abrir_modal_costos,
    mostrar_modal_escenarios,
    abrir_modal_transferencia,
    abrir_modal_transferencia_masiva,
)
from interface.qt.dialogs.lote_editor import abrir_editor_lote
from nesting_workspace import (
    guardar_workspace,
    guardar_workspace_payload,
    construir_payload_workspace_lote_export,
    cargar_workspace_desde_archivo,
    aplicar_workspace,
    reset_arganest_load_log,
    log_arganest_load,
    enlazar_rutas_en_payload,
    preparar_dxf_en_app,
    preparar_dxf_cache_en_payload,
    clasificar_material_workspace,
    filetypes_workspace_guardar,
    filetypes_workspace_abrir,
)
from postgres_connector import guardar_nesting_en_postgresql
from reporte_pdf_nesting import exportar_pdf_nesting
from interface.qt.export_paths import (
    asegurar_exportacion_local,
    desktop_nesteos_locales,
    guardar_consecutivo_wo_local,
    obtener_consecutivo_wo_local,
    resolver_ruta_base_exportacion,
)
from utils_nesting import (
    obtener_siguiente_consecutivo,
    crear_estructura_carpetas,
    generar_combinaciones_lotes,
    escalar_piezas,
    ensamblar_escenario,
    generar_csv_compras,
    clave_nesting_sort_key,
    grupo_nesting_sort_key,
    format_clave_calibre_display,
)
from modules.processed_layers import ProcesadorDXF
from modules.plasma_compensator import compute_plasma_offset_mm
from modules.nesting_engine.efficiency_metrics import (
    actualizar_eficiencias_hoja,
    actualizar_eficiencias_resultados,
    contar_piezas_grupo,
    contar_piezas_hoja,
    eficiencia_para_umbral_ignorar,
    es_placa_madre_sobrante_rtz,
    es_placa_madre_rtzc,
    formatear_eficiencias_placa,
    formatear_eficiencias_tanque,
    hoja_cuenta_para_deduccion,
    hoja_es_sobrante_sin_compra,
    hoja_excluida_de_rtz_sobrante,
    inicializar_contador_rtz_sobrante,
    inicializar_contador_rtzc_sobrante,
    placa_debe_mostrar_opcion_ignorar,
    sincronizar_hoja_sobrante_rtz,
    sincronizar_sobrantes_rtz_en_resultados,
)
from modules.nesting_engine.rtz_overlays import (
    sincronizar_overlays_grupo,
    sincronizar_overlays_resultados,
)
from modules.nesting_engine.sheet_integrity import deduplicar_resultados_nesting
from modules.nesting_engine.sheet_numbering import (
    asignar_numeracion_global_hojas,
    numeracion_hojas_es_consistente,
)
from modules.nesting_engine.cu_rtz_sin_gap import asignar_rtz_cu_sin_gap_ids
COLOR_TARJETA = "#FFFFFF"
COLOR_BORDE = "#CBD5E1"
COLOR_GRIS_DARK = "#1E293B"
COLOR_GRIS_MED = "#475569"
COLOR_TEXTO_TITULO = "#0F172A"
COLOR_TEXTO_SECUNDARIO = "#64748B"
DEFAULT_KERF_IN = 0.15
DEFAULT_MARGIN_IN = 0.15


from interface.qt.tabs._mixin_export import ExportMixin
from interface.qt.tabs._mixin_nesting_calc import NestingCalcMixin
from interface.qt.tabs._mixin_plate_mgmt import PlateManagementMixin
from interface.qt.tabs._mixin_transfer import TransferMixin
from interface.qt.tabs._mixin_lote_edit import LoteEditMixin


class TabNesting(QWidget, TimerHost, ExportMixin, NestingCalcMixin, PlateManagementMixin, TransferMixin, LoteEditMixin):
    def __init__(self, master, app_principal):
        QWidget.__init__(self, master)
        TimerHost.__init__(self)
        self.app = app_principal
        self.app.tiempo_calculo = 0
        self.cantidad_tanques = "N/A"
        self.lote_actual_idx = 0  # <--- NUEVO: Puntero de memoria para el Dropdown
        self._syncing_edicion_libre_switch = False

        self.global_margin_val = DEFAULT_MARGIN_IN
        self.global_kerf_val = DEFAULT_KERF_IN
        try:
            from modules.nesting_engine.step_export_prefs import load_step_export_prefs

            self.step_export_prefs = load_step_export_prefs()
        except Exception:
            self.step_export_prefs = {}
        self.global_corner_val = "INFERIOR IZQUIERDA"
        self.costo_usd_val = 0.0
        self.costo_mxn_val = 0.0
        self.tipo_cambio_usdmxn = 18.50
        self.tipo_cambio_fuente = "FALLBACK"
        self.tipo_cambio_actualizado = ""

        # NUEVO: procesador DXF para inyecciones/reemplazos de lote
        self.procesador_lote = ProcesadorDXF()

        if not hasattr(self.app, "meta_pdf_por_ruta") or self.app.meta_pdf_por_ruta is None:
            self.app.meta_pdf_por_ruta = {}

        # NUEVO: cache interno de piezas fuente por tamaño de lote (k)
        self._inputs_precalculados_por_k = {}

        self.setup_ui()
        self._sync_kerf_widget()

    def _kerf_efectivo(self) -> float:
        """Kerf global de nesting (placa láser). Poka-yoke: no coerce silencioso."""
        from modules.nesting_engine.nest_poka_yoke import validar_kerf_in

        raw = getattr(self, "global_kerf_val", DEFAULT_KERF_IN)
        # Si el campo de UI existe, manda sobre el valor en memoria (evita 0.3 viejo del .arganest).
        if hasattr(self, "ent_kerf"):
            try:
                txt = str(self.ent_kerf.text() or "").strip()
                if txt:
                    raw = txt
            except Exception:
                pass
        k, err = validar_kerf_in(raw)
        if err:
            raise ValueError(err)
        self.global_kerf_val = k
        return k

    def _margin_efectivo(self) -> float:
        from modules.nesting_engine.nest_poka_yoke import validar_margin_in

        raw = getattr(self, "global_margin_val", 0.15)
        if hasattr(self, "ent_margin"):
            try:
                txt = str(self.ent_margin.text() or "").strip()
                if txt:
                    raw = txt
            except Exception:
                pass
        m, err = validar_margin_in(raw)
        if err:
            raise ValueError(err)
        self.global_margin_val = m
        return m

    def _leer_kerf_margin_ui(self) -> tuple[float, float] | None:
        """Valida kerf/margin con diálogo; None si el usuario debe corregir."""
        try:
            k = self._kerf_efectivo()
            m = self._margin_efectivo()
        except ValueError as exc:
            QMessageBox.critical(self, "Configuración inválida (poka-yoke)", str(exc))
            return None
        self.global_kerf_val = k
        self.global_margin_val = m
        return k, m

    def _sync_kerf_widget(self) -> None:
        try:
            k = self._kerf_efectivo()
        except Exception:
            k = DEFAULT_KERF_IN
        self.global_kerf_val = k
        if hasattr(self, "ent_kerf"):
            self.ent_kerf.setText(str(k))

    def _obtener_wo_real_lote_actual(self):
        wo_map = getattr(self.app, "wo_reales_por_lote", {}) or {}
        idx = int(getattr(self, "lote_actual_idx", 0) or 0)
        return wo_map.get(idx)

    def guardar_workspace_nesting(self):
        tiene_multilote = hasattr(self.app, "resultados_multilote") and bool(self.app.resultados_multilote)
        tiene_simple = hasattr(self.app, "resultados_nesting") and bool(self.app.resultados_nesting)

        if not tiene_multilote and not tiene_simple:
            return QMessageBox.warning(self, "Atención", "No hay datos de nesting para guardar.")

        orden_actual = str(getattr(self.app, "job_activo", "NESTING")).strip() or "NESTING"
        wo_real = self._obtener_wo_real_lote_actual()
        if wo_real:
            nombre_sugerido = self._nombre_archivo_export_lote(orden_actual, wo_real, extension=".arganest")
        else:
            nombre_sugerido = f"{orden_actual.replace('/', '-').replace('\\\\', '-')}.arganest"

        ruta_archivo = self._ask_save_file(
            title="Guardar workspace de nesting",
            defaultextension=".arganest",
            initialfile=nombre_sugerido,
            filetypes=filetypes_workspace_guardar(),
        )
        if not ruta_archivo:
            return

        # Poka-yoke: no persistir nest inválido como workspace "oficial".
        try:
            from modules.nesting_engine.nest_poka_yoke import (
                allow_incomplete_nest,
                listar_fallas_resultados_nest,
            )

            fallas_ws: list[str] = []
            if tiene_multilote:
                for lote in self.app.resultados_multilote or []:
                    fallas_ws.extend(
                        listar_fallas_resultados_nest((lote or {}).get("data"))
                    )
            else:
                fallas_ws.extend(
                    listar_fallas_resultados_nest(self.app.resultados_nesting)
                )
            if fallas_ws and not allow_incomplete_nest():
                texto = "\n\n".join(fallas_ws[:6])
                if len(fallas_ws) > 6:
                    texto += f"\n\n(+{len(fallas_ws) - 6} más)"
                return QMessageBox.critical(
                    self,
                    "Guardado bloqueado (poka-yoke)",
                    "El nest tiene fallas de integridad; no se guarda .arganest.\n\n"
                    f"{texto}",
                )
        except Exception as exc:
            return QMessageBox.critical(
                self,
                "Guardado bloqueado (poka-yoke)",
                f"No se pudo validar integridad antes de guardar:\n{exc}",
            )

        try:
            guardar_workspace(self, ruta_archivo)
            QMessageBox.information(self, 
                "Workspace guardado",
                f"Se guardó correctamente el workspace:\n{ruta_archivo}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo guardar el workspace:\n{e}")

    def abrir_workspace_nesting(self):
        ruta_archivo = self._ask_open_file(
            title="Abrir workspace de nesting",
            filetypes=filetypes_workspace_abrir(),
        )
        if not ruta_archivo:
            return
        self.cargar_workspace_async(ruta_archivo)

    def abrir_nesting_largos(self):
        from interface.qt.dialogs.largos_nesting_modal import abrir_nesting_largos

        abrir_nesting_largos(self)

    def abrir_nest_sim_lab(self):
        from interface.qt.dialogs.nest_sim_lab import abrir_nest_sim_lab

        abrir_nest_sim_lab(self)

    def abrir_visor_step(self):
        """Visor 3D experimental de STEP (OCCT + VTK). No usa FreeCAD."""
        from interface.qt.dialogs.step_viewer import abrir_visor_step

        abrir_visor_step(self)

    def _ui(self, fn, *args):
        call_on_main(fn, *args)

    def cargar_workspace_async(self, ruta_archivo, mostrar_exito=True):
        nombre = os.path.basename(str(ruta_archivo or "workspace"))
        if hasattr(self.app, "abrir_ventana_carga"):
            self.app.abrir_ventana_carga(f"Cargando {nombre}…")
        threading.Thread(
            target=self._thread_cargar_workspace,
            args=(ruta_archivo, mostrar_exito),
            daemon=True,
        ).start()

    def _thread_cargar_workspace(self, ruta_archivo, mostrar_exito):
        err = None
        payload = None
        reset_arganest_load_log(ruta_archivo)
        log_arganest_load("Hilo de carga iniciado (fase rápida)", phase="INICIO")
        try:
            if hasattr(self.app, "actualizar_progreso"):
                self.app.actualizar_progreso("Leyendo archivo…", 0.15)
            payload = cargar_workspace_desde_archivo(ruta_archivo, log=log_arganest_load)
            if hasattr(self.app, "actualizar_progreso"):
                self.app.actualizar_progreso("Enlazando rutas DXF…", 0.45)
            enlazar_rutas_en_payload(payload, log=log_arganest_load)
            preparar_dxf_cache_en_payload(payload, log=log_arganest_load)
            if not payload.get("_geom_prep_done"):
                payload["_rutas_prep_done"] = True
            if hasattr(self.app, "actualizar_progreso"):
                self.app.actualizar_progreso("Abriendo nest en UI…", 0.72)
            log_arganest_load("Fase rápida lista; abriendo UI…", phase="INICIO")
        except Exception as e:
            err = str(e)
            log_arganest_load(f"ERROR: {err}", phase="ERROR")
        self._ui(self._finalizar_carga_workspace, ruta_archivo, payload, err, mostrar_exito)

    def _thread_refinar_geom_workspace(self):
        """Fase B: geometría 1:1 paralela sin bloquear la UI."""
        try:
            preparar_dxf_en_app(self.app, log=log_arganest_load)
        except Exception as exc:
            log_arganest_load(f"Refinado background falló: {exc}", phase="ERROR")
        finally:
            self._ui(self._on_geom_prep_finalizado)

    def _redibujar_hoja_actual_tras_geom(self):
        hoja = getattr(self, "hoja_actual_data", None)
        clave = getattr(self, "clave_actual", None)
        if hoja is not None and clave:
            try:
                log_arganest_load(f"Redibujando placa visible ({clave})…", phase="UI")
                self.dibujar_hoja_full(hoja, clave)
            except Exception:
                pass

    def _iniciar_refinado_geom_background(self):
        if getattr(self, "_geom_refine_thread", None) is not None:
            try:
                if self._geom_refine_thread.is_alive():
                    log_arganest_load("Refinado geom ya en curso", phase="DXF")
                    return
            except Exception:
                pass
        setattr(self.app, "_geom_prep_done", False)
        setattr(self.app, "_transform_export_done", False)
        self._actualizar_botones_geom_prep()
        self._geom_refine_thread = threading.Thread(
            target=self._thread_refinar_geom_workspace,
            daemon=True,
        )
        self._geom_refine_thread.start()
        log_arganest_load("Transform export 1:1 iniciado en background (paralelo)", phase="DXF")

    def _geom_prep_en_curso(self) -> bool:
        if getattr(self.app, "_geom_prep_done", True):
            return False
        th = getattr(self, "_geom_refine_thread", None)
        try:
            return th is not None and th.is_alive()
        except Exception:
            return False

    def _actualizar_botones_geom_prep(self):
        busy = self._geom_prep_en_curso()
        btn = getattr(self, "btn_exportar", None)
        if btn is not None:
            btn.setEnabled(not busy)
            if busy:
                btn.setToolTip(
                    "Esperando transformaciones DXF 1:1 del .arganest "
                    "(evita exportar con rotación incorrecta)."
                )
            else:
                btn.setToolTip("Exportar DXF/STEP")

    def _on_geom_prep_finalizado(self):
        setattr(self.app, "_transform_export_done", True)
        setattr(self.app, "_geom_prep_done", True)
        self._actualizar_botones_geom_prep()
        self._refrescar_display_hoja_actual()

    def _refrescar_display_hoja_actual(self):
        hoja = getattr(self, "hoja_actual_data", None)
        clave = getattr(self, "clave_actual", None)
        if hoja is None or not clave:
            return
        try:
            from modules.nesting_engine.display_geometry import refrescar_poligonos_display_hoja

            n = refrescar_poligonos_display_hoja(hoja)
            if n:
                log_arganest_load(
                    f"Display 1:1 placa visible ({clave}): {n} pieza(s)",
                    phase="DISPLAY",
                )
                self.dibujar_hoja_full(hoja, clave)
        except Exception:
            pass

    def _finalizar_carga_workspace(self, ruta_archivo, payload, err=None, mostrar_exito=True):
        if err:
            if hasattr(self.app, "cerrar_ventana_carga"):
                self.app.cerrar_ventana_carga()
            QMessageBox.critical(self, "Error", f"No se pudo abrir el workspace:\n{err}")
            return
        if not payload:
            if hasattr(self.app, "cerrar_ventana_carga"):
                self.app.cerrar_ventana_carga()
            QMessageBox.critical(self, "Error", "No se pudo completar la carga del workspace.")
            return
        try:
            if hasattr(self.app, "actualizar_progreso"):
                self.app.actualizar_progreso("Restaurando nesting…", 0.78)
            aplicar_workspace(self, payload, carga_rapida=True)
            try:
                self.app.ruta_workspace_actual = str(ruta_archivo or "")
                self.app.ultimo_arganest = str(ruta_archivo or "")
                # Si el .arganest vive bajo NESTING/..., sube a ARGA MODEL CORE.
                cur = os.path.abspath(str(ruta_archivo or ""))
                for _ in range(8):
                    parent = os.path.dirname(cur)
                    if not parent or parent == cur:
                        break
                    nest_dir = os.path.join(parent, "NESTING")
                    base_name = os.path.basename(parent).upper()
                    if os.path.isdir(nest_dir) and base_name != "NESTING":
                        self.app.ultima_ruta_export_cad = parent
                        break
                    if base_name == "ARGA MODEL CORE":
                        self.app.ultima_ruta_export_cad = parent
                        break
                    cur = parent
            except Exception:
                pass
            if hasattr(self.app, "actualizar_progreso"):
                self.app.actualizar_progreso("Listo", 1.0)
            if hasattr(self.app, "cerrar_ventana_carga"):
                self.app.cerrar_ventana_carga()
            QTimer.singleShot(0, lambda: self._completar_vista_workspace_post_carga())
            if (
                payload.get("_rutas_prep_done")
                and not payload.get("_geom_prep_done")
                and not (payload.get("dxf_export_cache") or {}).get("transform_ready")
            ):
                self._iniciar_refinado_geom_background()
            elif payload.get("_geom_prep_done"):
                setattr(self.app, "_transform_export_done", True)
                setattr(self.app, "_geom_prep_done", True)
                self._actualizar_botones_geom_prep()
            if mostrar_exito:
                QTimer.singleShot(
                    50,
                    lambda: QMessageBox.information(
                        self,
                        "Workspace cargado",
                        f"Workspace restaurado correctamente:\n{ruta_archivo}",
                    ),
                )
        except Exception as e:
            if hasattr(self.app, "cerrar_ventana_carga"):
                self.app.cerrar_ventana_carga()
            QMessageBox.critical(self, "Error", f"No se pudo abrir el workspace:\n{e}")

    def _completar_vista_workspace_post_carga(self):
        lote = getattr(self, "_workspace_lote_pendiente", None)
        if lote:
            try:
                self.on_lote_selected(lote, sync_parts=False)
            except Exception:
                pass
            self._workspace_lote_pendiente = None
        QTimer.singleShot(0, self._dibujar_workspace_pendiente)

    def _dibujar_workspace_pendiente(self):
        pend = getattr(self, "_workspace_vista_pendiente", None) or {}
        hoja = pend.get("hoja")
        clave = pend.get("clave")
        if hoja is not None and clave is not None:
            try:
                self.dibujar_hoja_full(hoja, clave)
            except Exception:
                pass
            if pend.get("ajuste_desplegado") and hasattr(self, "ajuste_desplegado") and not self.ajuste_desplegado:
                try:
                    self.toggle_ajuste_placa()
                except Exception:
                    pass
        self._workspace_vista_pendiente = None
        QTimer.singleShot(
            300,
            lambda: getattr(self.app, "_refrescar_parts_ui_pendiente", lambda **_: None)(thumbnails_async=True),
        )

    @property
    def hoja_actual_data(self):
        return self.visor.hoja_actual_data

    @property
    def clave_actual(self):
        return self.visor.clave_actual

    @property
    def info_pieza_seleccionada(self):
        return self.visor.info_pieza_seleccionada

    @property
    def piezas_seleccionadas(self):
        return self.visor.piezas_seleccionadas

    def _primer_hoja_disponible(self, resultados):
        from modules.nesting_engine.resultados_grupos import primer_grupo_con_hojas

        return primer_grupo_con_hojas(resultados)

    def _tiene_nesting_activo(self) -> bool:
        multilote = getattr(self.app, "resultados_multilote", None) or []
        if multilote:
            return True
        resultados = getattr(self.app, "resultados_nesting", None)
        if not isinstance(resultados, dict) or not resultados:
            return False
        for info in resultados.values():
            if isinstance(info, dict) and info.get("hojas"):
                return True
        return False

    def _mostrar_primera_hoja_si_hay(self, resultados=None):
        datos = resultados if resultados is not None else getattr(self.app, "resultados_nesting", None)
        hoja, clave = self._primer_hoja_disponible(datos)
        if hoja is not None and clave is not None:
            self.dibujar_hoja_full(hoja, clave)
            return True
        if hasattr(self, "visor"):
            self.visor.hoja_actual_data = None
            self.visor.ax_nest.clear()
            self.visor.canvas_nest.draw_idle()
            if hasattr(self, "frame_ajuste_container"):
                self.frame_ajuste_container.hide()
        return False

    def _filetypes_to_filter(self, filetypes):
        if not filetypes:
            return "Todos (*.*)"
        parts = [f"{desc} ({pat})" for desc, pat in filetypes]
        parts.append("Todos (*.*)")
        return ";;".join(parts)

    def _ask_save_file(self, title="", defaultextension="", initialfile="", filetypes=None):
        filt = self._filetypes_to_filter(filetypes)
        path, _ = QFileDialog.getSaveFileName(self, title or "Guardar", initialfile or "", filt)
        if path and defaultextension and not os.path.splitext(path)[1]:
            path += defaultextension
        return path

    def _ask_open_file(self, title="", filetypes=None):
        filt = self._filetypes_to_filter(filetypes)
        path, _ = QFileDialog.getOpenFileName(self, title or "Abrir", "", filt)
        return path

    def setup_ui(self):
        from interface.qt.tabs.tab_nesting_ui import build_tab_nesting_ui
        build_tab_nesting_ui(self)
        return

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_nest_sidebar_user_resized", False):
            return
        from interface.qt.tabs.tab_nesting_ui import apply_nest_sidebar_width, schedule_nest_sidebar_sync
        apply_nest_sidebar_width(self)
        schedule_nest_sidebar_sync(self)
        QTimer.singleShot(0, self._restaurar_vista_nesting_si_vacia)

    def _restaurar_vista_nesting_si_vacia(self):
        if not self._tiene_nesting_activo():
            return
        visor = getattr(self, "visor", None)
        if visor is not None and getattr(visor, "hoja_actual_data", None) is not None:
            return
        self._mostrar_primera_hoja_si_hay()

    def on_piece_selected(self, info_pieza=None):
        piezas = self.visor.piezas_seleccionadas
        n = len(piezas)
        libre = bool(getattr(self.visor, "modo_edicion_libre_seleccion", False))
        estado_transfer = n >= 1
        estado_rot = n == 1 or (libre and n >= 1)
        estado_switch = n >= 2 or libre
        self.btn_transferir.setEnabled(estado_transfer)
        self.btn_rot_90.setEnabled(estado_rot)
        self.btn_rot_m1.setEnabled(estado_rot)
        self.btn_rot_p1.setEnabled(estado_rot)
        if hasattr(self, "switch_edicion_libre"):
            self.switch_edicion_libre.setEnabled(estado_switch)
        if n > 1:
            self.btn_transferir.setText(f"Mudar ({n})")
            self.btn_transferir.setToolTip(f"Mudar {n} piezas a otra placa")
        else:
            self.btn_transferir.setText("Mudar")
            self.btn_transferir.setToolTip("Mudar pieza seleccionada a otra placa")
        self._actualizar_seccion_pieza_seleccionada(piezas)

    def _set_switch_edicion_libre(self, activo: bool):
        self._syncing_edicion_libre_switch = True
        try:
            if hasattr(self, "switch_edicion_libre"):
                self.switch_edicion_libre.blockSignals(True)
                self.switch_edicion_libre.setChecked(bool(activo))
                self.switch_edicion_libre.blockSignals(False)
        except Exception:
            pass
        self._syncing_edicion_libre_switch = False

    def _on_toggle_edicion_libre(self, checked: bool):
        if getattr(self, "_syncing_edicion_libre_switch", False):
            return
        if checked:
            self._activar_edicion_libre_ui()
        else:
            self._desactivar_edicion_libre_ui()

    def _activar_edicion_libre_ui(self):
        ok, msg = self.visor.activar_modo_edicion_libre_seleccion()
        if not ok:
            self._set_switch_edicion_libre(False)
            QMessageBox.warning(self, "Edición libre", msg)
            return
        self._set_switch_edicion_libre(True)
        if hasattr(self, "lbl_edicion_libre"):
            self.lbl_edicion_libre.setText(
                f"MODO ACTIVO ({len(self.visor.indices_edicion_libre)} PIEZAS EN MORADO). "
                "SOLO CHOCAN CON PLACA Y PIEZAS NO SELECCIONADAS."
            )
            self.lbl_edicion_libre.setStyleSheet("color:#C084FC;font-size:10px;background:transparent;")
        self.on_piece_selected()

    def _desactivar_edicion_libre_ui(self):
        _, msg = self.visor.desactivar_modo_edicion_libre_seleccion()
        self._set_switch_edicion_libre(False)
        self._replicar_lote_activo_a_gemelos()
        self.procesar_lista_hojas(self.app.resultados_nesting)
        if hasattr(self, "lbl_edicion_libre"):
            self.lbl_edicion_libre.setText(
                "SOLO COLISIONA CON PLACA Y PIEZAS FUERA DEL GRUPO. EN MODO ACTIVO: MORADO."
            )
            self.lbl_edicion_libre.setStyleSheet("color:#94A3B8;font-size:10px;background:transparent;")
        if msg:
            QMessageBox.warning(self, "Edición libre", msg)
        self.on_piece_selected()

    def _desactivar_edicion_libre_si_cambia_contexto(self):
        if getattr(self.visor, "modo_edicion_libre_seleccion", False):
            self.visor.cancelar_modo_edicion_libre_silencioso()
            self._set_switch_edicion_libre(False)
            if hasattr(self, "lbl_edicion_libre"):
                self.lbl_edicion_libre.setText(
                    "SOLO COLISIONA CON PLACA Y PIEZAS FUERA DEL GRUPO. EN MODO ACTIVO: MORADO."
                )
                self.lbl_edicion_libre.setStyleSheet("color:#94A3B8;font-size:10px;background:transparent;")

    def reabrir_modal_escenarios(self):
        if hasattr(self.app, 'ultimos_escenarios') and self.app.ultimos_escenarios:
            mostrar_modal_escenarios(self, self.app.ultimos_escenarios)
        else:
            QMessageBox.information(self, "Work Orders", "No hay estrategias de Work Orders generadas en este momento.")

    def _propagar_auditoria_dxf_a_parts(self, resultado=None):
        audit = None
        if isinstance(resultado, dict):
            audit = resultado.get("dxf_audit")
        if not audit:
            audit = getattr(self.app.motor_nesting, "_ultima_auditoria_dxf", None)
        if not isinstance(audit, dict):
            return
        self.app.dxf_nesting_audit = dict(audit)
        parts = getattr(self.app, "vista_parts", None)
        if parts is not None and hasattr(parts, "actualizar_resumen_dxf"):
            self.app.after(0, lambda a=dict(audit): parts.actualizar_resumen_dxf(a))

    def _poblar_ui_tras_nesting_completado(self):
        try:
            self.actualizar_dropdown_lotes()
        except Exception as exc:
            import traceback

            print(f"[NESTING][FINALIZAR][WARN] No se pudo cargar la vista: {exc}")
            traceback.print_exc()
            try:
                self.btn_ver_lotes.setEnabled(True)
            except Exception:
                pass
            QMessageBox.warning(
                self,
                "Vista parcial",
                "El cálculo terminó correctamente, pero no se pudo dibujar la primera hoja.\n\n"
                f"Detalle: {exc}\n\n"
                "Use el selector de Work Order o elige otra hoja en la lista lateral.",
            )

    def _obtener_tipo_cambio_dof(self):
        """
        Intenta obtener el FIX (MXN por USD) desde portal DOF.
        Retorna (tc, fuente, timestamp_iso).
        """
        hoy = datetime.now().strftime("%d/%m/%Y")
        fecha_q = urllib.parse.quote(hoy, safe="")
        urls = [
            f"https://www.dof.gob.mx/indicadores_detalle.php?cod_tipo_indicador=158&dfecha={fecha_q}",
            "https://www.dof.gob.mx/indicadores_detalle.php?cod_tipo_indicador=158",
        ]
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        }
        patrones = [
            r"Tipo de cambio[^0-9]{0,40}([0-9]{1,2}\.[0-9]{2,6})",
            r"\bFIX\b[^0-9]{0,40}([0-9]{1,2}\.[0-9]{2,6})",
            r"([0-9]{1,2}\.[0-9]{2,6})",
        ]

        for url in urls:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                if not html:
                    continue
                for pat in patrones:
                    m = re.search(pat, html, flags=re.IGNORECASE | re.DOTALL)
                    if not m:
                        continue
                    tc = float(m.group(1))
                    if 10.0 <= tc <= 30.0:
                        return tc, "DOF", datetime.now().isoformat(timespec="seconds")
            except Exception:
                continue

        return None, "DOF_NO_DISPONIBLE", datetime.now().isoformat(timespec="seconds")

    def _actualizar_tipo_cambio(self):
        tc, fuente, ts = self._obtener_tipo_cambio_dof()
        if tc is None:
            if not isinstance(getattr(self, "tipo_cambio_usdmxn", None), (int, float)) or self.tipo_cambio_usdmxn <= 0:
                self.tipo_cambio_usdmxn = 18.50
            self.tipo_cambio_fuente = "FALLBACK"
            self.tipo_cambio_actualizado = ts
            return

        self.tipo_cambio_usdmxn = float(tc)
        self.tipo_cambio_fuente = fuente
        self.tipo_cambio_actualizado = ts

    def _contar_placas_usadas(self, resultados=None) -> int:
        """Placas/barras físicas usadas (excluye RTZCU virtual y retazos ACCESORIOS)."""
        res = resultados if resultados is not None else getattr(self.app, "resultados_nesting", None)
        if not isinstance(res, dict) or not res:
            return 0
        total = 0
        for info in res.values():
            if not isinstance(info, dict) or "error" in info:
                continue
            for hoja in info.get("hojas") or []:
                if not isinstance(hoja, dict):
                    continue
                if hoja.get("cu_rtz_virtual") or hoja.get("es_retazo"):
                    continue
                total += 1
        return total

    def _actualizar_piezas_totales_label(self, resultados=None):
        if not hasattr(self, "lbl_piezas_totales"):
            return
        res = resultados if resultados is not None else getattr(self.app, "resultados_nesting", None)
        if not isinstance(res, dict) or not res:
            self.lbl_piezas_totales.setText("PIEZAS TOTALES: -")
            if hasattr(self, "lbl_placas_totales"):
                self.lbl_placas_totales.setText("PLACAS: -")
            return
        total = 0
        for info in res.values():
            if isinstance(info, dict) and "error" not in info:
                total += contar_piezas_grupo(info)
        self.lbl_piezas_totales.setText(f"PIEZAS TOTALES: {total}")
        if hasattr(self, "lbl_placas_totales"):
            n_placas = self._contar_placas_usadas(res)
            self.lbl_placas_totales.setText(f"PLACAS: {n_placas}")
