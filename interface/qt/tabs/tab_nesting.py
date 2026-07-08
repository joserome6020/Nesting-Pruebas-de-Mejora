"""Tab NESTING — PySide6 nativo (lógica 1:1 con oficial)."""
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

COLOR_TARJETA = "#FFFFFF"
COLOR_BORDE = "#CBD5E1"
COLOR_GRIS_DARK = "#1E293B"
COLOR_GRIS_MED = "#475569"
COLOR_TEXTO_TITULO = "#0F172A"
COLOR_TEXTO_SECUNDARIO = "#64748B"
DEFAULT_KERF_IN = 0.3
DEFAULT_MARGIN_IN = 0.15

class TabNesting(QWidget, TimerHost):
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
        """Kerf global de nesting (placa láser). Nunca 0: las placas CU guardan kerf_usado=0 aparte."""
        try:
            k = float(self.global_kerf_val)
        except Exception:
            k = DEFAULT_KERF_IN
        if k <= 0:
            k = DEFAULT_KERF_IN
        return k

    def _sync_kerf_widget(self) -> None:
        k = self._kerf_efectivo()
        self.global_kerf_val = k
        if hasattr(self, "ent_kerf"):
            self.ent_kerf.setText(str(k))

    def _ruta_carpeta_reporte_pdf_nesting(self, ruta_export_base: str) -> str:
        from modules.nesting_engine.exporter import REPORTE_PDF_NESTING

        return os.path.join(ruta_export_base, "NESTING", REPORTE_PDF_NESTING)

    def _ruta_carpeta_arganest_nesting(self, ruta_export_base: str) -> str:
        from modules.nesting_engine.exporter import ARCHIVO_ARGANEST_NESTING

        return os.path.join(ruta_export_base, "NESTING", ARCHIVO_ARGANEST_NESTING)

    def _nombre_archivo_export_lote(self, job_activo: str, n_wo: str, *, extension: str) -> str:
        orden_archivo = str(job_activo).replace("/", "-").replace("\\", "-")
        wo_archivo = str(n_wo).replace("/", "-").replace("\\", "-")
        ext = str(extension or "").strip().lower()
        if ext and not ext.startswith("."):
            ext = f".{ext}"
        return f"Nesting_{orden_archivo}-{wo_archivo}{ext}"

    def _exportar_pdf_nesting_en_carpeta_export(
        self,
        mini_resultados,
        ruta_export_base: str,
        n_wo: str,
        *,
        job_activo: str | None = None,
    ) -> str:
        job = str(job_activo or getattr(self.app, "job_activo", "NESTING")).strip() or "NESTING"
        carpeta_pdf = self._ruta_carpeta_reporte_pdf_nesting(ruta_export_base)
        os.makedirs(carpeta_pdf, exist_ok=True)

        nombre_pdf = self._nombre_archivo_export_lote(job, n_wo, extension=".pdf").replace(
            "Nesting_", "Nesting_Reporte_", 1
        )
        ruta_pdf = os.path.join(carpeta_pdf, nombre_pdf)

        exportar_pdf_nesting(
            resultados_nesting=mini_resultados,
            ruta_pdf=ruta_pdf,
            nombre_orden=job,
            meta_por_ruta=getattr(self.app, "meta_pdf_por_ruta", {}),
            job_fallback=job,
            work_order_label=str(n_wo),
        )
        return ruta_pdf

    def _exportar_arganest_en_carpeta_export(
        self,
        mini_resultados,
        ruta_export_base: str,
        n_wo: str,
        *,
        lote_idx: int,
        k_val,
        job_activo: str | None = None,
    ) -> str:
        job = str(job_activo or getattr(self.app, "job_activo", "NESTING")).strip() or "NESTING"
        carpeta_arganest = self._ruta_carpeta_arganest_nesting(ruta_export_base)
        os.makedirs(carpeta_arganest, exist_ok=True)

        nombre_arganest = self._nombre_archivo_export_lote(job, n_wo, extension=".arganest")
        ruta_arganest = os.path.join(carpeta_arganest, nombre_arganest)

        payload = construir_payload_workspace_lote_export(
            self,
            lote_idx=int(lote_idx),
            mini_resultados=mini_resultados,
            n_wo=str(n_wo),
            k_val=k_val,
        )
        guardar_workspace_payload(payload, ruta_arganest)
        return ruta_arganest

    def exportar_reporte_pdf_nesting(self):
        if not hasattr(self.app, 'resultados_nesting') or not self.app.resultados_nesting:
            return QMessageBox.warning(self, "Atención", "No hay datos de nesting para exportar.")
        wo_real = self._obtener_wo_real_lote_actual()
        if not wo_real:
            return QMessageBox.warning(self, 
                "Atención",
                "Primero debes exportar DXF/STEP para asignar la WO oficial del lote actual."
            )

        orden_actual = str(getattr(self.app, "job_activo", "NESTING")).strip() or "NESTING"
        wo_real_str = str(wo_real).strip()

        orden_archivo = orden_actual.replace("/", "-").replace("\\", "-")
        wo_archivo = wo_real_str.replace("/", "-").replace("\\", "-")

        nombre_sugerido = f"Nesting_Reporte_{orden_archivo}-{wo_archivo}.pdf"

        ruta_pdf = self._ask_save_file(
            title="Guardar reporte PDF de nesting",
            defaultextension=".pdf",
            initialfile=nombre_sugerido,
            filetypes=[("PDF", "*.pdf")]
        )
        if not ruta_pdf:
            return

        if hasattr(self.app, 'abrir_ventana_carga'):
            self.app.abrir_ventana_carga("Generando PDF de nesting...")

        def worker():
            try:
                exportar_pdf_nesting(
                    resultados_nesting=self.app.resultados_nesting,
                    ruta_pdf=ruta_pdf,
                    nombre_orden=orden_actual,
                    meta_por_ruta=getattr(self.app, "meta_pdf_por_ruta", {}),
                    job_fallback=orden_actual,
                    work_order_label=str(wo_real),
                )
                self.app.after(0, lambda: self.finalizar_exportacion_pdf(True, ruta_pdf))
            except Exception as e:
                msg = str(e)
                self.app.after(0, lambda m=msg: self.finalizar_exportacion_pdf(False, m))

        threading.Thread(target=worker, daemon=True).start()

    def finalizar_exportacion_pdf(self, exito, payload):
        if hasattr(self.app, 'cerrar_ventana_carga'):
            self.app.cerrar_ventana_carga()

        if exito:
            QMessageBox.information(self, "PDF generado", f"Reporte creado correctamente:\n{payload}")
            try:
                os.startfile(payload)
            except Exception:
                pass
        else:
            QMessageBox.critical(self, "Error al generar PDF", payload)

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
            filetypes=[
                ("Arga Nest Workspace", "*.arganest"),
                ("Nava Nest Workspace", "*.navanest"),
                ("JSON", "*.json"),
                ("Todos los archivos", "*.*"),
            ]
        )
        if not ruta_archivo:
            return

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
            filetypes=[
                ("Arga Nest Workspace", "*.arganest"),
                ("Nava Nest Workspace", "*.navanest"),
                ("JSON", "*.json"),
                ("Todos los archivos", "*.*"),
            ]
        )
        if not ruta_archivo:
            return
        self.cargar_workspace_async(ruta_archivo)

    def abrir_nesting_largos(self):
        from interface.qt.dialogs.largos_nesting_modal import abrir_nesting_largos

        abrir_nesting_largos(self)

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
                btn.setToolTip("")

    def _bloquear_hasta_geom_prep(self):
        """En hilo de export: espera a que termine el refinado post-.arganest."""
        th = getattr(self, "_geom_refine_thread", None)
        if th is None:
            return
        try:
            if th.is_alive():
                log_arganest_load(
                    "Exportación en espera: transform export 1:1 en curso…",
                    phase="EXPORT",
                )
                th.join()
        except Exception:
            pass

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

    def _normalizar_fila_pieza_edicion(self, fila):
        """
        Estructura estándar:
        (nombre, material, qty, calibre, estado, ruta_dxf)
        """
        if isinstance(fila, dict):
            nombre = fila.get("nombre") or fila.get("pieza") or fila.get("ref") or ""
            material = fila.get("material") or ""
            qty = fila.get("qty") or fila.get("cantidad") or "1"
            calibre = fila.get("calibre") or ""
            estado = fila.get("estado") or "LISTO"
            ruta = fila.get("ruta") or fila.get("path") or fila.get("dxf") or ""
            return (
                str(nombre),
                str(material),
                str(qty),
                str(calibre),
                str(estado),
                str(ruta),
            )

        if isinstance(fila, (list, tuple)):
            vals = list(fila[:6])
            while len(vals) < 6:
                vals.append("")
            return (
                str(vals[0]),
                str(vals[1]),
                str(vals[2]),
                str(vals[3]),
                str(vals[4]),
                str(vals[5]),
            )

        return None

    def _clonar_datos_partes_edicion(self, datos):
        salida = []
        for fila in (datos or []):
            fila_ok = self._normalizar_fila_pieza_edicion(fila)
            if fila_ok:
                salida.append(fila_ok)
        return salida

    def _extraer_rutas_dxf_desde_partes(self, datos):
        rutas = []
        for fila in (datos or []):
            fila_ok = self._normalizar_fila_pieza_edicion(fila)
            if fila_ok and fila_ok[5]:
                rutas.append(str(fila_ok[5]))
        return rutas

    def _reconstruir_editables_por_resultado(self, resultados_list):
        """
        A partir de resultados_multilote, arma editable_inputs_by_lote.
        Usa self._inputs_precalculados_por_k como fuente principal.
        """
        fuentes_por_k = getattr(self, "_inputs_precalculados_por_k", {}) or {}
        base_general = self._clonar_datos_partes_edicion(
            getattr(self.app, "datos_partes_actuales", [])
        )

        editable_inputs_by_lote = []

        for orden in (resultados_list or []):
            try:
                lote_k = int(orden.get("lote_k", 0) or 0)
            except Exception:
                lote_k = 0

            fuente = fuentes_por_k.get(lote_k)
            if not fuente:
                fuente = base_general

            editable_inputs_by_lote.append(
                self._clonar_datos_partes_edicion(fuente)
            )

        self.app.editable_inputs_by_lote = editable_inputs_by_lote

        if editable_inputs_by_lote:
            idx = min(max(getattr(self, "lote_actual_idx", 0), 0), len(editable_inputs_by_lote) - 1)
            self.app.editable_inputs_actuales = self._clonar_datos_partes_edicion(
                editable_inputs_by_lote[idx]
            )
        else:
            self.app.editable_inputs_actuales = []

        self.app.source_dxf_paths_by_lote = [
            self._extraer_rutas_dxf_desde_partes(lote)
            for lote in editable_inputs_by_lote
        ]

        self.app.source_dxf_paths_workspace = self._extraer_rutas_dxf_desde_partes(
            self.app.editable_inputs_actuales
        )

        self.app.lote_editado_dirty = False

    def _sincronizar_parts_con_lote_activo(self):
        """
        Hace que PARTS refleje el lote actualmente seleccionado.
        """
        lote_inputs = []

        if (
            hasattr(self.app, "editable_inputs_by_lote")
            and self.app.editable_inputs_by_lote
            and 0 <= self.lote_actual_idx < len(self.app.editable_inputs_by_lote)
        ):
            lote_inputs = self._clonar_datos_partes_edicion(
                self.app.editable_inputs_by_lote[self.lote_actual_idx]
            )
        elif hasattr(self.app, "editable_inputs_actuales") and self.app.editable_inputs_actuales:
            lote_inputs = self._clonar_datos_partes_edicion(self.app.editable_inputs_actuales)

        if not lote_inputs:
            lote_inputs = self._clonar_datos_partes_edicion(
                getattr(self.app, "datos_partes_actuales", [])
            )

        self.app.editable_inputs_actuales = self._clonar_datos_partes_edicion(lote_inputs)
        self.app.source_dxf_paths_workspace = self._extraer_rutas_dxf_desde_partes(lote_inputs)

        if hasattr(self.app, "source_dxf_paths_by_lote"):
            while len(self.app.source_dxf_paths_by_lote) <= self.lote_actual_idx:
                self.app.source_dxf_paths_by_lote.append([])
            self.app.source_dxf_paths_by_lote[self.lote_actual_idx] = list(self.app.source_dxf_paths_workspace)

        if hasattr(self.app, "cargar_datos_parts"):
            self.app.cargar_datos_parts(lote_inputs)
        else:
            self.app.datos_partes_actuales = lote_inputs

    def _defaults_lote_activo(self):
        """
        Obtiene defaults razonables para nuevas piezas del lote activo.
        Prioridad:
        1) primera pieza editable del lote
        2) clave actual del nesting
        3) valores genéricos
        """
        datos = self._clonar_datos_partes_edicion(
            getattr(self.app, "editable_inputs_actuales", [])
        )

        if datos:
            nombre, material, qty, calibre, estado, ruta = datos[0]
            return {
                "material": str(material or ""),
                "calibre": str(calibre or ""),
                "estado": str(estado or "LISTO"),
            }

        material = ""
        calibre = ""
        if getattr(self, "clave_actual", "") and "_" in self.clave_actual:
            try:
                calibre, material = self.clave_actual.split("_", 1)
            except Exception:
                pass

        return {
            "material": str(material or ""),
            "calibre": str(calibre or ""),
            "estado": "LISTO",
        }
    
    def _normalizar_ruta_meta_lote(self, ruta):
        try:
            return os.path.normcase(os.path.normpath(str(ruta)))
        except Exception:
            return str(ruta)
        
    def _detectar_tipo_dxf(self, ruta_dxf):
        """
        Detecta si el DXF seleccionado ya viene procesado
        (CUT_OUTER / CUT_INNER / MARK) o si es un DXF crudo de Inventor
        (IV_OUTER_PROFILE / IV_INTERIOR_PROFILES / IV_MARK_*).
        """
        try:
            doc = ezdxf.readfile(ruta_dxf)
            layers = {str(e.dxf.layer).strip().upper() for e in doc.modelspace() if hasattr(e.dxf, "layer")}
        except Exception:
            return "unknown"

        has_processed = any(
            lyr in {"CUT_OUTER", "CUT_INNER", "MARK"} for lyr in layers
        )
        has_raw = any(
            lyr in {
                "IV_OUTER_PROFILE",
                "IV_INTERIOR_PROFILES",
                "IV_MARK_SURFACE",
                "IV_MARK_TROUGHT",
                "IV_FEATURE_PROFILES",
                "IV_FEATURE_PROFILES_DOWN",
            }
            for lyr in layers
        )

        if has_processed:
            return "processed"
        if has_raw:
            return "raw"
        return "unknown"

    def _resolver_rutas_proyecto_lote(self, ruta_referencia=None):
        """
        Devuelve ambas rutas oficiales del proyecto para el lote activo:
        - carpeta_raw: ...\\AutoDXF
        - carpeta_processed: ...\\AutoDXF\\Processed Files

        La referencia actual del lote normalmente apunta a Processed Files.
        """
        ruta_base = None

        if ruta_referencia:
            ruta_base = str(ruta_referencia)
        else:
            for fila in getattr(self.app, "editable_inputs_actuales", []) or []:
                fila_ok = self._normalizar_fila_pieza_edicion(fila)
                if fila_ok and fila_ok[5]:
                    ruta_base = str(fila_ok[5])
                    break

        if not ruta_base:
            raise ValueError("No se pudo determinar la ruta base del proyecto para el lote activo.")

        carpeta_actual = os.path.dirname(os.path.abspath(ruta_base))
        nombre_carpeta = os.path.basename(carpeta_actual).strip().lower()

        if nombre_carpeta in ("processed files", "procesados"):
            carpeta_processed = carpeta_actual
            carpeta_raw = os.path.dirname(carpeta_actual)
        else:
            carpeta_raw = carpeta_actual
            carpeta_processed = os.path.join(carpeta_raw, "Processed Files")

        os.makedirs(carpeta_raw, exist_ok=True)
        os.makedirs(carpeta_processed, exist_ok=True)

        return {
            "carpeta_raw": carpeta_raw,
            "carpeta_processed": carpeta_processed,
        }

    def _copiar_o_procesar_hacia_proyecto(self, ruta_origen, ruta_raw_destino, ruta_processed_destino):
        """
        Mantiene el mismo flujo del proyecto:
        1) deja un archivo base en AutoDXF
        2) deja un archivo procesado en AutoDXF\\Processed Files
        3) devuelve la ruta PROCESADA final, que es la que usa el lote
        """
        if not os.path.exists(ruta_origen):
            raise FileNotFoundError(f"No existe el DXF seleccionado:\n{ruta_origen}")

        os.makedirs(os.path.dirname(ruta_raw_destino), exist_ok=True)
        os.makedirs(os.path.dirname(ruta_processed_destino), exist_ok=True)

        tipo = self._detectar_tipo_dxf(ruta_origen)

        # Caso 1: el usuario seleccionó un DXF crudo tipo Inventor
        if tipo == "raw":
            shutil.copy2(ruta_origen, ruta_raw_destino)

            exito = self.procesador_lote.limpiar_archivo(ruta_raw_destino, ruta_processed_destino)
            if not exito or not os.path.exists(ruta_processed_destino):
                raise ValueError(
                    f"No se pudo procesar el DXF para el lote activo:\n{ruta_origen}"
                )

            return ruta_processed_destino

        # Caso 2: el usuario seleccionó un DXF ya procesado
        if tipo == "processed":
            # Guardamos copia directa como procesado final
            shutil.copy2(ruta_origen, ruta_processed_destino)

            # También dejamos copia base en AutoDXF para mantener trazabilidad física
            shutil.copy2(ruta_origen, ruta_raw_destino)

            return ruta_processed_destino

        # Caso 3: tipo desconocido -> fallback conservador
        # Copiamos a raw y tratamos de procesar desde ahí.
        shutil.copy2(ruta_origen, ruta_raw_destino)

        exito = self.procesador_lote.limpiar_archivo(ruta_raw_destino, ruta_processed_destino)
        if exito and os.path.exists(ruta_processed_destino):
            return ruta_processed_destino

        raise ValueError(
            f"No se pudo procesar el DXF para el lote activo:\n{ruta_origen}"
        )
    
    def _work_order_label_lote_activo(self):
        return f"W.O. {int(getattr(self, 'lote_actual_idx', 0) or 0) + 1}"

    def _order_label_para_rtz(self) -> str:
        job = str(getattr(self.app, "job_activo", "") or "").strip()
        if job.upper().startswith("SWO"):
            return job
        return self._work_order_label_lote_activo()

    def _sincronizar_sobrante_rtz_placa(self, clave, hoja, ignorar: bool) -> None:
        if not isinstance(hoja, dict) or hoja.get("es_retazo"):
            return
        calibre = str(clave).split("_", 1)[0].strip() or "NA"
        contador = inicializar_contador_rtz_sobrante(self.app.resultados_nesting or {})
        contador_rtzc = inicializar_contador_rtzc_sobrante(self.app.resultados_nesting or {})
        sincronizar_hoja_sobrante_rtz(
            hoja,
            ignorar=bool(ignorar),
            contador_rtz=contador,
            contador_rtzc=contador_rtzc,
            calibre=calibre,
            wo_name=self._order_label_para_rtz(),
        )

    def _multiplicador_lote_activo(self):
        """
        Regresa el multiplicador real del lote actualmente seleccionado.
        Ejemplos:
        - lote X5 -> 5
        - lote X2 -> 2
        - si no se puede determinar -> 1
        """
        try:
            resultados = getattr(self.app, "resultados_multilote", []) or []
            idx = int(getattr(self, "lote_actual_idx", 0) or 0)

            if 0 <= idx < len(resultados):
                lote_k = resultados[idx].get("lote_k", 1)
                return max(1, int(lote_k))
        except Exception:
            pass

        try:
            return max(1, int(getattr(self.app, "multiplicador_tanques", 1) or 1))
        except Exception:
            return 1

    def _procesar_dxf_para_lote(self, ruta_original, nombre_destino_forzado=None):
        """
        Lleva el DXF seleccionado a las rutas oficiales del proyecto:
        - AutoDXF (raw)
        - AutoDXF\\Processed Files (processed)

        Devuelve SIEMPRE la ruta final PROCESADA.
        """
        if not ruta_original:
            raise ValueError("Ruta DXF vacía.")

        ruta_original = os.path.abspath(str(ruta_original))
        rutas = self._resolver_rutas_proyecto_lote()

        nombre_archivo = (
            str(nombre_destino_forzado)
            if nombre_destino_forzado
            else os.path.basename(ruta_original)
        )

        ruta_raw_destino = os.path.join(rutas["carpeta_raw"], nombre_archivo)
        ruta_processed_destino = os.path.join(rutas["carpeta_processed"], nombre_archivo)

        return self._copiar_o_procesar_hacia_proyecto(
            ruta_original,
            ruta_raw_destino,
            ruta_processed_destino
        )

    def _registrar_meta_pdf_lote(self, ruta_dxf_final, item_nombre, *, ruta_origen=None):
        """
        Mantiene alineado el nuevo DXF con el mismo sistema de metadata por ruta
        que usa el PDF y otros flujos.
        """
        if not hasattr(self.app, "meta_pdf_por_ruta") or self.app.meta_pdf_por_ruta is None:
            self.app.meta_pdf_por_ruta = {}

        ruta_norm = self._normalizar_ruta_meta_lote(ruta_dxf_final)
        job_actual = str(getattr(self.app, "job_activo", "NESTING")).strip() or "NESTING"
        work_order = self._work_order_label_lote_activo()

        meta_prev = (getattr(self.app, "meta_pdf_por_ruta", None) or {}).get(ruta_norm) or {}
        if ruta_origen:
            meta_prev = meta_prev or (
                (getattr(self.app, "meta_pdf_por_ruta", None) or {}).get(
                    self._normalizar_ruta_meta_lote(ruta_origen)
                )
                or {}
            )
        job_meta = str(meta_prev.get("job") or "").strip()
        if job_actual.upper().startswith("SWO") and job_meta:
            job_display = job_meta
        else:
            job_display = job_actual

        self.app.meta_pdf_por_ruta[ruta_norm] = {
            "job": job_display,
            "item": str(item_nombre).strip() or os.path.splitext(os.path.basename(str(ruta_dxf_final)))[0],
            "work_order": work_order,
        }

    def _nombre_logico_desde_dxf(self, ruta_o_nombre):
        """
        Devuelve el nombre lógico visible de la pieza a partir del archivo seleccionado,
        sin extensión.
        """
        texto = os.path.splitext(os.path.basename(str(ruta_o_nombre or "")))[0].strip()
        return texto

    def _extraer_qty_desde_nombre_dxf(self, ruta_o_nombre):
        """
        Busca cantidades embebidas en nombres tipo:
        - QTY 3
        - QTY:3
        - QTY_3
        - CANT 3
        - CANTIDAD 3

        Si no encuentra nada, regresa 1.
        """
        texto = self._nombre_logico_desde_dxf(ruta_o_nombre)
        if not texto:
            return 1

        patrones = [
            r"(?i)\bQTY\s*[:=_-]?\s*(\d+)\b",
            r"(?i)\bCANT(?:IDAD)?\s*[:=_-]?\s*(\d+)\b",
        ]

        for patron in patrones:
            m = re.search(patron, texto)
            if m:
                try:
                    return max(1, int(m.group(1)))
                except Exception:
                    pass

        return 1

    def _extraer_calibre_desde_nombre_dxf(self, ruta_o_nombre):
        """
        Busca calibre embebido en nombres tipo:
        - Cal 0.75
        - CAL:0.75
        - Cal_0.75
        """
        texto = self._nombre_logico_desde_dxf(ruta_o_nombre)
        if not texto:
            return ""

        patrones = [
            r"(?i)\bCAL\s*[:=_-]?\s*([0-9]+(?:\.[0-9]+)?)\b",
            r"(?i)\bCALIBRE\s*[:=_-]?\s*([0-9]+(?:\.[0-9]+)?)\b",
        ]

        for patron in patrones:
            m = re.search(patron, texto)
            if m:
                return str(m.group(1)).strip()

        return ""

    def _normalizar_material_desde_texto(self, texto_material):
        return normalizar_material_autodxf(texto_material, default="")

    def _extraer_material_desde_nombre_dxf(self, ruta_o_nombre):
        """
        Intenta leer el material desde la nomenclatura separada por comas.
        Ejemplo:
        LIFTINGLUG, Steel, Carbon, QTY 4, Cal 0.75
        -> material = CARBONO
        """
        texto = self._nombre_logico_desde_dxf(ruta_o_nombre)
        if not texto:
            return ""

        partes = [p.strip() for p in texto.split(",") if str(p).strip()]
        if len(partes) < 2:
            return ""

        # Tomamos solo segmentos que no sean QTY ni CAL
        candidatos = []
        for p in partes[1:]:
            up = p.upper()
            if "QTY" in up or "CANT" in up or "CAL" in up:
                continue
            candidatos.append(p)

        material_crudo = " ".join(candidatos).strip()
        return self._normalizar_material_desde_texto(material_crudo)

    def _parsear_metadata_desde_nombre_dxf(self, ruta_o_nombre, fila_base=None):
        """
        Parsea metadata desde la nomenclatura del DXF.
        Si algo no viene en el nombre, usa fallback del renglón base/defaults.
        """
        fila_norm = self._normalizar_fila_pieza_edicion(fila_base) if fila_base else None
        defaults = self._defaults_lote_activo()

        nombre_logico = self._nombre_logico_desde_dxf(ruta_o_nombre)
        meta_carpeta = extraer_metadata_carpetas_autodxf(str(ruta_o_nombre or ""))
        material_detectado = (
            self._extraer_material_desde_nombre_dxf(ruta_o_nombre)
            or meta_carpeta.get("material")
        )
        calibre_detectado = (
            self._extraer_calibre_desde_nombre_dxf(ruta_o_nombre)
            or meta_carpeta.get("calibre")
        )
        qty_base = self._extraer_qty_desde_nombre_dxf(ruta_o_nombre)

        material_final = (
            material_detectado
            or (fila_norm[1] if fila_norm else "")
            or defaults["material"]
        )

        calibre_final = (
            calibre_detectado
            or (fila_norm[3] if fila_norm else "")
            or defaults["calibre"]
        )

        estado_final = (
            (fila_norm[4] if fila_norm else "")
            or defaults["estado"]
            or "LISTO"
        )

        return {
            "nombre": str(nombre_logico),
            "material": str(material_final),
            "calibre": str(calibre_final),
            "estado": str(estado_final),
            "qty_base": max(1, int(qty_base or 1)),
        }

    def _crear_fila_editable_desde_dxf(self, ruta_dxf, fila_base=None):
        if not ruta_dxf:
            raise ValueError("Ruta DXF vacía.")

        metadata = self._parsear_metadata_desde_nombre_dxf(ruta_dxf, fila_base=fila_base)

        nombre_archivo_destino = os.path.basename(str(ruta_dxf))

        ruta_final_proyecto = self._procesar_dxf_para_lote(
            ruta_dxf,
            nombre_destino_forzado=nombre_archivo_destino
        )

        multiplicador_lote = self._multiplicador_lote_activo()
        qty_final = metadata["qty_base"] * multiplicador_lote

        self._registrar_meta_pdf_lote(ruta_final_proyecto, metadata["nombre"], ruta_origen=ruta_dxf)

        return (
            str(metadata["nombre"]),
            str(metadata["material"]),
            str(qty_final),
            str(metadata["calibre"]),
            str(metadata["estado"]),
            str(ruta_final_proyecto),
        )

    def _actualizar_lote_editable_en_memoria(self, nuevas_filas):
        nuevas_filas = self._clonar_datos_partes_edicion(nuevas_filas)

        self.app.editable_inputs_actuales = nuevas_filas

        if not hasattr(self.app, "editable_inputs_by_lote") or self.app.editable_inputs_by_lote is None:
            self.app.editable_inputs_by_lote = []

        while len(self.app.editable_inputs_by_lote) <= self.lote_actual_idx:
            self.app.editable_inputs_by_lote.append([])

        self.app.editable_inputs_by_lote[self.lote_actual_idx] = self._clonar_datos_partes_edicion(nuevas_filas)

        self.app.source_dxf_paths_workspace = self._extraer_rutas_dxf_desde_partes(nuevas_filas)

        if not hasattr(self.app, "source_dxf_paths_by_lote") or self.app.source_dxf_paths_by_lote is None:
            self.app.source_dxf_paths_by_lote = []

        while len(self.app.source_dxf_paths_by_lote) <= self.lote_actual_idx:
            self.app.source_dxf_paths_by_lote.append([])

        self.app.source_dxf_paths_by_lote[self.lote_actual_idx] = list(self.app.source_dxf_paths_workspace)

        self.app.lote_editado_dirty = True

        # Esto mantiene PARTS en sincronía con el lote activo
        self._sincronizar_parts_con_lote_activo()

    def agregar_dxfs_a_lote(self, rutas_dxfs, fila_base=None):
        actuales = self._clonar_datos_partes_edicion(
            getattr(self.app, "editable_inputs_actuales", [])
        )

        agregadas = 0
        for ruta in (rutas_dxfs or []):
            if not ruta:
                continue
            fila = self._crear_fila_editable_desde_dxf(ruta, fila_base=fila_base)
            actuales.append(fila)
            agregadas += 1

        if agregadas <= 0:
            return

        self._actualizar_lote_editable_en_memoria(actuales)

    def eliminar_piezas_de_lote(self, indices):
        actuales = self._clonar_datos_partes_edicion(
            getattr(self.app, "editable_inputs_actuales", [])
        )
        if not actuales:
            return

        indices_validos = sorted(
            {i for i in (indices or []) if isinstance(i, int) and 0 <= i < len(actuales)},
            reverse=True
        )

        for idx in indices_validos:
            actuales.pop(idx)

        self._actualizar_lote_editable_en_memoria(actuales)

    def reemplazar_dxf_de_lote(self, indice, nueva_ruta):
        actuales = self._clonar_datos_partes_edicion(
            getattr(self.app, "editable_inputs_actuales", [])
        )

        if not (0 <= int(indice) < len(actuales)):
            raise ValueError("Índice fuera de rango para reemplazo.")

        nombre_actual, material_actual, qty_actual, calibre_actual, estado_actual, ruta_vieja = actuales[indice]

        fila_base = actuales[indice]

        # Conservamos el mismo nombre físico dentro del proyecto
        # para no romper la trazabilidad interna del archivo
        nombre_destino = os.path.basename(str(ruta_vieja))

        ruta_final_proyecto = self._procesar_dxf_para_lote(
            nueva_ruta,
            nombre_destino_forzado=nombre_destino
        )

        metadata = self._parsear_metadata_desde_nombre_dxf(nueva_ruta, fila_base=fila_base)
        multiplicador_lote = self._multiplicador_lote_activo()
        qty_final = metadata["qty_base"] * multiplicador_lote

        actuales[indice] = (
            str(metadata["nombre"]),
            str(metadata["material"]),
            str(qty_final),
            str(metadata["calibre"]),
            str(metadata["estado"]),
            str(ruta_final_proyecto),
        )

        self._registrar_meta_pdf_lote(ruta_final_proyecto, metadata["nombre"], ruta_origen=ruta_dxf)
        self._actualizar_lote_editable_en_memoria(actuales)
    def _primer_hoja_disponible(self, resultados):
        if not isinstance(resultados, dict):
            return None, None

        for clave, info in resultados.items():
            hojas = info.get("hojas", [])
            if hojas:
                return hojas[0], clave

        return None, None

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

    def _preguntar_generacion_3d_export(self):
        """True=con 3D, False=solo DXF, None=cancelar exportación."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Exportación")
        box.setText("¿Generar la exportación del nesteo?")
        box.setInformativeText("(La generación de archivos 3D tardará un poco más)")
        btn_si = box.addButton("SI, generar 3D", QMessageBox.ButtonRole.YesRole)
        btn_no = box.addButton("No, solo DXF", QMessageBox.ButtonRole.NoRole)
        btn_cancel = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        for btn, min_w in ((btn_si, 158), (btn_no, 138), (btn_cancel, 104)):
            btn.setMinimumWidth(min_w)
            btn.setMinimumHeight(34)
        box.setDefaultButton(btn_no)
        box.exec()
        clicked = box.clickedButton()
        if clicked is None or clicked == btn_cancel:
            return None
        return clicked == btn_si

    def _finalizar_renesteo_lote(self, nuevo_resultado):
        if hasattr(self.app, 'cerrar_ventana_carga'):
            self.app.cerrar_ventana_carga()

        if not isinstance(nuevo_resultado, dict) or not nuevo_resultado:
            return QMessageBox.critical(self, "Error", "El renesteo no devolvió un resultado válido.")

        if not hasattr(self.app, "resultados_multilote") or not self.app.resultados_multilote:
            return QMessageBox.critical(self, "Error", "No existe un lote activo para sustituir.")

        self.app.resultados_multilote[self.lote_actual_idx]["data"] = nuevo_resultado
        self.app.resultados_nesting = nuevo_resultado
        self.app.lote_editado_dirty = False
        self.app.resultados_multilote[self.lote_actual_idx].pop("gemelo_desync", None)
        self._replicar_lote_activo_a_gemelos()

        try:
            from modules.nesting_engine.display_geometry import refrescar_poligonos_display_resultados

            refrescar_poligonos_display_resultados(nuevo_resultado)
        except Exception:
            pass

        # Mantener PARTS del lote activo
        self._sincronizar_parts_con_lote_activo()

        self.procesar_lista_hojas(self.app.resultados_nesting)

        hoja, clave = self._primer_hoja_disponible(self.app.resultados_nesting)
        if hoja is not None and clave is not None:
            self.dibujar_hoja_full(hoja, clave)

        QMessageBox.information(self, 
            "Renesteo completado",
            "El lote activo fue renesteado correctamente sin recalcular los demás lotes."
        )

    def renestear_lote_actual(self):
        lote_inputs = self._clonar_datos_partes_edicion(
            getattr(self.app, "editable_inputs_actuales", [])
        )

        if not lote_inputs:
            return QMessageBox.warning(self, "Atención", "El lote activo no tiene piezas para renestear.")

        if hasattr(self.app, 'abrir_ventana_carga'):
            self.app.abrir_ventana_carga("Renesteando lote activo...")

        def receptor_en_vivo(msg, pct):
            if hasattr(self.app, 'actualizar_progreso'):
                self.app.after(0, lambda: self.app.actualizar_progreso(msg, pct))
                self.app.after(0, self.app.update_idletasks)

        def worker():
            try:
                self._sync_orientacion_cobre_al_motor()
                datos_placas = self.app.plates_manager.obtener_datos_placas()
                wo_act = str(getattr(self.app, 'job_activo', 'PENDIENTE')).strip().upper() or "PENDIENTE"

                try:
                    kerf_ui = self._kerf_efectivo()
                except Exception:
                    kerf_ui = DEFAULT_KERF_IN
                nuevo_resultado = self.app.motor_nesting.ejecutar_nesting_visual(
                    lote_inputs,
                    datos_placas,
                    progress_callback=receptor_en_vivo,
                    config_kerf=kerf_ui,
                    config_margin=self.global_margin_val,
                    config_corner=self.global_corner_val,
                    config_opt=self.cmb_opt.currentText() if hasattr(self, "cmb_opt") else "OPTIMIZAR LARGO Y ANCHO",
                    wo_name=wo_act,
                )

                if getattr(self.app, "tarea_cancelada", lambda: False)():
                    self.app.after(0, self.restaurar_controles_tras_cancelacion)
                    return
                if isinstance(nuevo_resultado, dict) and nuevo_resultado.get("error") == "Operación cancelada por el usuario.":
                    self.app.after(0, self.restaurar_controles_tras_cancelacion)
                    return

                self.app.after(0, lambda r=nuevo_resultado: self._finalizar_renesteo_lote(r))

            except Exception as e:
                msg = str(e)

                def throw_err():
                    if hasattr(self.app, 'cerrar_ventana_carga'):
                        self.app.cerrar_ventana_carga()
                    QMessageBox.critical(self, "Error", f"No se pudo renestear el lote activo:\n{msg}")

                self.app.after(0, throw_err)

        threading.Thread(target=worker, daemon=True).start()

    def editar_lote_activo(self):
        if not hasattr(self.app, "resultados_multilote") or not self.app.resultados_multilote:
            return QMessageBox.warning(self, 
                "Atención",
                "Primero debes generar o abrir un nesting."
            )

        if not hasattr(self.app, "editable_inputs_by_lote") or not self.app.editable_inputs_by_lote:
            return QMessageBox.warning(self, 
                "Atención",
                "Aún no hay datos editables del lote activo."
            )

        # Aseguramos que el lote activo y PARTS estén sincronizados antes de abrir el editor
        self._sincronizar_parts_con_lote_activo()

        abrir_editor_lote(self)

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
            self.btn_transferir.setText(f"MUDAR {n} PIEZAS")
        else:
            self.btn_transferir.setText("MUDAR A OTRA PLACA")
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

    def _sufijo_placa_en_grupo(self, clave, hoja) -> str:
        if not isinstance(hoja, dict) or hoja.get("es_retazo"):
            return ""
        hojas = (self.app.resultados_nesting or {}).get(clave, {}).get("hojas") or []
        nombre = str(hoja.get("placa_id", "") or "")
        iguales = [
            j for j, h in enumerate(hojas)
            if str(h.get("placa_id", "") or "") == nombre and not h.get("es_retazo")
        ]
        if len(iguales) <= 1:
            return ""
        try:
            idx = hojas.index(hoja)
        except ValueError:
            return ""
        if idx not in iguales:
            return ""
        return f" · P{iguales.index(idx) + 1}"

    def _actualizar_panel_placa(self, hoja=None, clave=None):
        if not hasattr(self, "lbl_placa_resumen"):
            return
        hoja = hoja if hoja is not None else self.hoja_actual_data
        clave = clave if clave is not None else self.clave_actual
        if not isinstance(hoja, dict) or not clave:
            self.lbl_placa_resumen.setText("SIN PLACA ACTIVA")
            self.lbl_placa_stats.setText("-")
            self.lbl_placa_dims.setText("-")
            return

        placa_id = str(hoja.get("placa_id", "-") or "-")
        sufijo = self._sufijo_placa_en_grupo(clave, hoja)
        es_retazo = bool(hoja.get("es_retazo", False))
        titulo = f"{placa_id}{sufijo}"
        if es_retazo:
            titulo += " (ACCESORIOS)"

        n_pzas = contar_piezas_hoja(hoja)
        d = float(
            (hoja or {}).get("eficiencia_directa", (hoja or {}).get("eficiencia", 0.0)) or 0.0
        )
        r = float((hoja or {}).get("eficiencia_real", d) or 0.0)
        efi_txt = f"DIR {d:.1f}% | REAL {r:.1f}%"
        origen = "PROVEEDOR" if str(hoja.get("origen_placa", "")).upper() == "PROVEEDOR" else "EMPRESA"
        try:
            w_in = float(hoja.get("placa_w", 0) or 0) / 25.4
            h_in = float(hoja.get("placa_h", 0) or 0) / 25.4
            dims = f"{w_in:.1f} × {h_in:.1f} in"
        except Exception:
            dims = "-"

        self.lbl_placa_resumen.setText(f"{titulo} · {n_pzas} PZAS")
        self.lbl_placa_stats.setText(efi_txt)
        self.lbl_placa_dims.setText(
            f"{format_clave_calibre_display(clave)} · {origen} · {dims}"
        )

        acciones_ok = not es_retazo
        self.btn_panel_renest_placa.setEnabled(acciones_ok)
        self.btn_panel_cambiar_placa.setEnabled(acciones_ok)
        self.btn_panel_renest_calibre.setEnabled(bool(clave))

    def _actualizar_seccion_pieza_seleccionada(self, piezas=None):
        if not hasattr(self, "lbl_pieza_sel"):
            return
        piezas = piezas if piezas is not None else self.visor.piezas_seleccionadas
        if not piezas:
            self.lbl_pieza_sel.setText("SIN SELECCIÓN — CLIC EN EL CANVAS")
            return
        if len(piezas) == 1:
            p = piezas[0]
            nom = str(p.get("nombre", "Pieza") or "Pieza")
            if len(nom) > 42:
                nom = nom[:39] + "…"
            self.lbl_pieza_sel.setText(nom)
            return
        self.lbl_pieza_sel.setText(f"{len(piezas)} PIEZAS SELECCIONADAS")

    def panel_limpiar_seleccion(self):
        self.visor.limpiar_seleccion_piezas()
        self.on_piece_selected()

    def panel_ajustar_vista(self):
        if self.hoja_actual_data and self.clave_actual:
            self.visor.dibujar_hoja_full(
                self.hoja_actual_data,
                self.clave_actual,
                preserve_view=False,
            )

    def panel_renestear_placa(self):
        if not self._ctx_tiene_resultados(self.clave_actual):
            return
        if not self._ctx_hoja_valida(self.hoja_actual_data, "Renestear placa"):
            return
        self.renestear_solo_placa(self.clave_actual, self.hoja_actual_data)

    def panel_renestear_calibre(self):
        if not self._ctx_tiene_resultados(self.clave_actual):
            return
        self._iniciar_renest_calibre_con_seleccion_placa(self.clave_actual)

    def _iniciar_renest_calibre_con_seleccion_placa(self, clave, *, candidata_placa=None):
        if candidata_placa is not None or self._es_grupo_cobre(clave):
            self.renestear_calibre_completo_ui(clave, candidata_placa=candidata_placa)
            return
        candidata = self._mostrar_dialogo_placa_renest_calibre(clave)
        if candidata is False:
            return
        self.renestear_calibre_completo_ui(clave, candidata_placa=candidata)

    def panel_mudar_todas_piezas(self):
        if not self._ctx_tiene_resultados(self.clave_actual):
            return
        if not self._ctx_hoja_valida(self.hoja_actual_data, "Mudar piezas"):
            return
        abrir_modal_transferencia_masiva(self, self.clave_actual, self.hoja_actual_data)

    def panel_cambiar_placa_madre(self):
        clave = self.clave_actual
        hoja = self.hoja_actual_data
        if not self._ctx_tiene_resultados(clave):
            return
        if not self._ctx_hoja_valida(hoja, "Cambiar placa"):
            return
        candidata = self._mostrar_dialogo_placa_inventario(
            clave,
            hoja,
            titulo="CAMBIAR PLACA MADRE",
            mensaje=(
                f"Placa actual: {hoja.get('placa_id', '-')}\n"
                "Seleccione una placa del inventario Herinox (mismo calibre/material)."
            ),
        )
        if candidata:
            self.cambiar_placa_y_renestear(clave, hoja, candidata)

    def _reposicionar_panel_ajuste(self):
        from interface.qt.tabs.tab_nesting_ui import PANEL_TOOLS_MIN_WIDTH

        host = getattr(self, "visor_host", None)
        frame = getattr(self, "frame_ajuste_container", None)
        if not host or not frame or not frame.isVisible():
            return
        marg = 10
        host_h = max(1, host.height())
        host_w = max(1, host.width())
        max_frame_h = max(340, host_h - marg * 2)
        panel_w = min(max(PANEL_TOOLS_MIN_WIDTH, int(host_w * 0.21)), host_w - marg * 2)

        frame.setFixedWidth(panel_w)
        self.panel_ajuste_contenido.setFixedWidth(panel_w)
        self.btn_toggle_ajuste.setFixedWidth(panel_w)

        toggle_h = self.btn_toggle_ajuste.sizeHint().height() + 12
        footer_h = self.btn_ajustar_vista.sizeHint().height() + 18 if hasattr(self, "btn_ajustar_vista") else 0
        content_cap = max(300, max_frame_h - toggle_h)
        if self.ajuste_desplegado:
            self.panel_ajuste_contenido.setMinimumHeight(content_cap)
            self.panel_ajuste_contenido.setMaximumHeight(content_cap)
            scroll = getattr(self, "_panel_tools_scroll", None)
            if scroll is not None:
                scroll.setMinimumHeight(max(200, content_cap - footer_h))
        else:
            self.panel_ajuste_contenido.setMinimumHeight(0)
            self.panel_ajuste_contenido.setMaximumHeight(content_cap)

        frame.setMaximumHeight(max_frame_h)
        fh = min(frame.sizeHint().height(), max_frame_h)
        x = max(0, host_w - panel_w - marg)
        y = max(0, host_h - fh - marg)
        frame.setGeometry(x, y, panel_w, fh)
        frame.raise_()

    def toggle_ajuste_placa(self):
        if self.ajuste_desplegado:
            self.panel_ajuste_contenido.hide()
            self.btn_toggle_ajuste.setText("HERRAMIENTAS DE PLACA")
            self.ajuste_desplegado = False
        else:
            self.panel_ajuste_contenido.show()
            self.btn_toggle_ajuste.setText("OCULTAR PANEL")
            self.ajuste_desplegado = True
        self._reposicionar_panel_ajuste()

    def dibujar_hoja_full(self, hoja, clave):
        try:
            from modules.nesting_engine.display_geometry import (
                pieza_necesita_geom_dxf,
                refrescar_poligonos_display_hoja,
                refrescar_poligonos_display_pieza,
            )

            if not self._geom_prep_en_curso():
                refrescar_poligonos_display_hoja(hoja)
            else:
                for pz in hoja.get("piezas") or []:
                    if (
                        isinstance(pz, dict)
                        and pz.get("_transform_export_ok")
                        and pieza_necesita_geom_dxf(pz)
                    ):
                        refrescar_poligonos_display_pieza(pz)
        except Exception:
            pass
        if hoja and clave and not hoja.get("es_retazo"):
            self.sincronizar_overlays_clave(clave)
        if hoja is not self.hoja_actual_data:
            self._desactivar_edicion_libre_si_cambia_contexto()
            self.visor.limpiar_seleccion_piezas()
            self.on_piece_selected()
        try:
            self.visor.dibujar_hoja_full(hoja, clave)
        except Exception as exc:
            print(f"[NESTING][VISOR][WARN] dibujar_hoja_full: {exc}")
            raise
        self.frame_ajuste_container.show()
        if not self.ajuste_desplegado:
            self.panel_ajuste_contenido.hide()
            self.btn_toggle_ajuste.setText("HERRAMIENTAS DE PLACA")
        self._reposicionar_panel_ajuste()
        self._sync_kerf_widget()
        self._actualizar_panel_placa(hoja, clave)
        self._actualizar_seccion_pieza_seleccionada()
        self._actualizar_seleccion_lista_hojas()

    # =========================================================
    # LÓGICA DEL MENÚ DESPLEGABLE (EL MES EN ACCIÓN)
    # =========================================================
    def actualizar_dropdown_lotes(self, *, activar_primero: bool = True):
        if not hasattr(self.app, 'resultados_multilote') or not self.app.resultados_multilote:
            self.cmb_lotes.blockSignals(True)
            self.cmb_lotes.clear()
            self.cmb_lotes.addItem("SIN ÓRDENES")
            self.cmb_lotes.setEnabled(False)
            self.cmb_lotes.blockSignals(False)
            return

        opciones = [
            f"Work Order {i+1} [ Lote X{orden['lote_k']} ]"
            for i, orden in enumerate(self.app.resultados_multilote)
        ]
        from interface.qt.theme import apply_herinox_combo

        self.cmb_lotes.blockSignals(True)
        self.cmb_lotes.clear()
        self.cmb_lotes.addItems(opciones)
        self.cmb_lotes.setEnabled(True)
        apply_herinox_combo(self.cmb_lotes)
        idx_sel = min(max(getattr(self, "lote_actual_idx", 0), 0), len(opciones) - 1)
        self.cmb_lotes.setCurrentIndex(idx_sel)
        self.cmb_lotes.blockSignals(False)
        if activar_primero:
            self.on_lote_selected(opciones[idx_sel])

    def _persistir_lote_saliente(self, old_idx, *, nuevo_idx=None):
        """Guarda resultados_nesting en el slot multilote al cambiar de WO (no al cargar)."""
        if nuevo_idx is not None and int(old_idx) == int(nuevo_idx):
            return
        ml = getattr(self.app, "resultados_multilote", None) or []
        li = int(old_idx)
        if not ml or li < 0 or li >= len(ml):
            return
        rn = getattr(self.app, "resultados_nesting", None)
        if not isinstance(rn, dict) or not rn:
            return
        slot = ml[li].get("data")
        if slot is rn:
            return
        if ml[li].get("gemelo_desync"):
            ml[li]["data"] = copy.deepcopy(rn)
        else:
            ml[li]["data"] = rn

    def _cargar_resultados_lote_idx(self, idx):
        ml = getattr(self.app, "resultados_multilote", None) or []
        li = int(idx)
        if not ml or li < 0 or li >= len(ml):
            self.app.resultados_nesting = {}
            return
        slot = ml[li].get("data")
        if ml[li].get("gemelo_desync") and isinstance(slot, dict):
            self.app.resultados_nesting = copy.deepcopy(slot)
            ml[li]["data"] = self.app.resultados_nesting
        else:
            self.app.resultados_nesting = slot or {}

    def on_lote_selected(self, val, *, sync_parts: bool = True):
        try:
            idx = [self.cmb_lotes.itemText(i) for i in range(self.cmb_lotes.count())].index(val)
            old_idx = int(getattr(self, "lote_actual_idx", 0) or 0)
            self._persistir_lote_saliente(old_idx, nuevo_idx=idx)

            self.lote_actual_idx = idx
            self._cargar_resultados_lote_idx(idx)

            # NUEVO: mover también el set editable del lote activo
            if (
                hasattr(self.app, "editable_inputs_by_lote")
                and self.app.editable_inputs_by_lote
                and idx < len(self.app.editable_inputs_by_lote)
            ):
                self.app.editable_inputs_actuales = self._clonar_datos_partes_edicion(
                    self.app.editable_inputs_by_lote[idx]
                )
            else:
                self.app.editable_inputs_actuales = self._clonar_datos_partes_edicion(
                    getattr(self.app, "datos_partes_actuales", [])
                )

            # NUEVO: refrescar PARTS con el lote activo
            if sync_parts:
                self._sincronizar_parts_con_lote_activo()

            self.procesar_lista_hojas(self.app.resultados_nesting)
            self._mostrar_primera_hoja_si_hay(self.app.resultados_nesting)

        except ValueError:
            pass

    def reabrir_modal_escenarios(self):
        if hasattr(self.app, 'ultimos_escenarios') and self.app.ultimos_escenarios:
            mostrar_modal_escenarios(self, self.app.ultimos_escenarios)
        else:
            QMessageBox.information(self, "Work Orders", "No hay estrategias de Work Orders generadas en este momento.")

    def restaurar_controles_tras_cancelacion(self):
        try:
            self.btn_run_nest.setEnabled(True)
        except Exception:
            pass
        try:
            self.btn_ver_lotes.setEnabled(True)
        except Exception:
            pass

    def _sync_orientacion_cobre_al_motor(self):
        orientaciones = getattr(self.app, "orientacion_cobre_por_ruta", None) or {}
        self.app.motor_nesting.orientacion_cobre_por_ruta = dict(orientaciones)

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

    def ejecutar_nesting(self):
        if not self.app.datos_partes_actuales:
            return QMessageBox.warning(self, "Atención", "No hay piezas importadas.")

        if self._tiene_nesting_activo():
            resp = QMessageBox.question(
                self,
                "Nesting activo",
                "Ya hay un nesteo activo.\n\n"
                "¿Seguro que desea renestear o nestear un nuevo trabajo?\n"
                "Se perderá el acomodo actual en pantalla.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                return

        self._sync_orientacion_cobre_al_motor()
        self.btn_run_nest.setEnabled(False)
        self.btn_ver_lotes.setEnabled(False)

        T = getattr(self.app, "multiplicador_tanques", 1)
        self.cantidad_tanques = str(T)
        self.lbl_cantidad.setText(f"CANTIDAD: {self.cantidad_tanques}")

        # Cobre 100%: se nestea aparte (sin análisis de lotes), aunque T>=4.
        analiza_lotes = T >= 4 and not self._wo_solo_cobre()
        self.app.abrir_ventana_carga(
            "Optimizando Lotes..." if analiza_lotes else "Ejecutando Nesting"
        )

        try:
            kerf_ui = self._kerf_efectivo()
        except Exception:
            kerf_ui = DEFAULT_KERF_IN
        if kerf_ui < DEFAULT_KERF_IN:
            kerf_ui = DEFAULT_KERF_IN
        opt_ui = self.cmb_opt.currentText() if hasattr(self, "cmb_opt") else "OPTIMIZAR LARGO Y ANCHO"

        threading.Thread(
            target=self.thread_worker,
            args=(T, self.global_margin_val, self.global_corner_val, kerf_ui, opt_ui),
            daemon=True
        ).start()

    def thread_worker(self, T, margin_val, corner_val, kerf_val, opt_val):
        tiempo_inicio = time.time()
        wo_act = getattr(self.app, 'job_activo', 'PENDIENTE').strip().upper()

        # NUEVO: reset de cache editable por corrida
        self._inputs_precalculados_por_k = {}

        def _abortar_si_cancelado():
            if getattr(self.app, "tarea_cancelada", lambda: False)():
                self.app.after(0, self.restaurar_controles_tras_cancelacion)
                return True
            return False

        try:
            if _abortar_si_cancelado():
                return
            self._sync_orientacion_cobre_al_motor()
            # =========================================================
            # RECEPTOR DE TELEMETRÍA ASÍNCRONO
            # =========================================================
            def receptor_en_vivo(msg, pct):
                if hasattr(self.app, 'actualizar_progreso'):
                    self.app.after(0, lambda: self.app.actualizar_progreso(msg, pct))
                    self.app.after(0, self.app.update_idletasks)
            # =========================================================

            datos_placas = self.app.plates_manager.obtener_datos_placas()

            # Cobre 100%: barras largas deterministas -> nesteo directo, sin
            # análisis de lotes MES (los escenarios optimizan costo de placa,
            # que no aplica al cobre). La cantidad se coloca tal cual.
            if T < 4 or self._wo_solo_cobre():
                datos_base = self._clonar_datos_partes_edicion(
                    getattr(self.app, "datos_partes_actuales", [])
                )

                self._inputs_precalculados_por_k[int(T)] = self._clonar_datos_partes_edicion(datos_base)

                res = self.app.motor_nesting.ejecutar_nesting_visual(
                    datos_base,
                    datos_placas,
                    progress_callback=receptor_en_vivo,
                    config_kerf=kerf_val,
                    config_margin=margin_val,
                    config_corner=corner_val,
                    config_opt=opt_val,
                    wo_name=wo_act,
                )

                if _abortar_si_cancelado():
                    return
                if isinstance(res, dict) and res.get("error") == "Operación cancelada por el usuario.":
                    self.app.after(0, self.restaurar_controles_tras_cancelacion)
                    return

                self._propagar_auditoria_dxf_a_parts(res)

                self.app.tiempo_calculo = time.time() - tiempo_inicio
                lista_unica = [{"lote_k": T, "data": res}]
                self._preparar_resultados_nesting_pesado(
                    lista_unica, progress_callback=receptor_en_vivo
                )
                self.app.after(0, lambda rl=lista_unica: self.finalizar(rl))
                return

            escenarios = generar_combinaciones_lotes(T)
            ks_necesarios = list(set([k for esc in escenarios for k, mult in esc]))
            nestings_precalculados = {}

            for idx, k in enumerate(ks_necesarios):
                if _abortar_si_cancelado():
                    return
                def cb_wrapper(msg, pct, k_val=k, current_idx=idx, t_ks=len(ks_necesarios)):
                    progreso_real = (current_idx / t_ks) + (pct / t_ks)
                    self.app.after(
                        0,
                        lambda m=msg, p=progreso_real: self.app.actualizar_progreso(f"[Lote {k_val}X] {m}", p)
                    )
                    self.app.after(0, self.app.update_idletasks)

                datos_k = escalar_piezas(getattr(self.app, "datos_partes_actuales", []), T, k)
                datos_k = self._clonar_datos_partes_edicion(datos_k)

                self._inputs_precalculados_por_k[int(k)] = self._clonar_datos_partes_edicion(datos_k)

                nestings_precalculados[k] = self.app.motor_nesting.ejecutar_nesting_visual(
                    datos_k,
                    datos_placas,
                    progress_callback=cb_wrapper,
                    config_kerf=kerf_val,
                    config_margin=margin_val,
                    config_corner=corner_val,
                    config_opt=opt_val,
                    wo_name=wo_act,
                )
                self._propagar_auditoria_dxf_a_parts(nestings_precalculados[k])
                if _abortar_si_cancelado():
                    return

            escenarios_resultados = []
            for esc in escenarios:
                res_esc_list, costo_esc, efi_esc = ensamblar_escenario(esc, nestings_precalculados)
                escenarios_resultados.append({
                    "config": esc,
                    "resultados": res_esc_list,
                    "costo": costo_esc,
                    "efi": efi_esc
                })

            escenarios_resultados.sort(key=lambda x: x["costo"])

            self.app.ultimos_escenarios = escenarios_resultados
            self.app.tiempo_calculo = time.time() - tiempo_inicio

            self.app.after(0, lambda: self.btn_ver_lotes.setEnabled(True))
            self.app.after(0, lambda: mostrar_modal_escenarios(self, escenarios_resultados))

        except Exception as e:
            import traceback

            err_detail = traceback.format_exc()
            print(f"[NESTING][WORKER][ERROR]\n{err_detail}")

            def throw_err(err=str(e)):
                if hasattr(self.app, 'cerrar_ventana_carga'):
                    self.app.cerrar_ventana_carga()
                self.btn_run_nest.setEnabled(True)
                self.btn_ver_lotes.setEnabled(True)
                QMessageBox.critical(
                    self,
                    "Error Interno",
                    f"{err}\n\nRevise la terminal o C:\\NEST_EXPORTS\\nesting_debug_geometry.txt",
                )

            self.app.after(0, throw_err)

    def aplicar_escenario_seleccionado(self, resultados_list, top_window):
        if hasattr(top_window, "accept"):
            top_window.accept()
        elif hasattr(top_window, "close"):
            top_window.close()

        def _bg_prep_y_finalizar():
            self._preparar_resultados_nesting_pesado(resultados_list)
            self.app.after(0, lambda: self.finalizar(resultados_list))

        threading.Thread(target=_bg_prep_y_finalizar, daemon=True).start()

    def _preparar_resultados_nesting_pesado(self, resultados_list, progress_callback=None):
        """
        Trabajo CPU/IO pesado fuera del hilo UI: plan de largos (BD) y refinado DXF display.
        Debe ejecutarse en el hilo worker de nesting, no en finalizar().
        """
        def _notify(msg, pct=0.99):
            if progress_callback:
                try:
                    progress_callback(msg, pct)
                except Exception:
                    pass

        _notify("Calculando plan de largos…", 0.99)
        try:
            from interface.largos_nesting_service import calcular_planes_largos_nesting

            calcular_planes_largos_nesting(self.app, resultados_list)
        except Exception as e_largos:
            print(f"[LARGOS_NESTING][WARN] No se pudo calcular plan de largos: {e_largos}")

        _notify("Refinando geometría de display…", 0.995)
        try:
            from modules.nesting_engine.display_geometry import preparar_geom_multilote_paralelo

            def _log_display(msg, phase="DISPLAY"):
                _notify(str(msg), 0.995)

            stats = preparar_geom_multilote_paralelo(resultados_list, log=_log_display)
            geom_ok = int((stats or {}).get("geom_ok", 0) or 0)
            if geom_ok:
                print(f"[NESTING][DISPLAY] geom_ok={geom_ok}")
        except Exception as exc_display:
            print(f"[NESTING][DISPLAY][WARN] {exc_display}")
            try:
                from modules.nesting_engine.display_geometry import refrescar_poligonos_display_multilote

                refrescar_poligonos_display_multilote(resultados_list)
            except Exception:
                pass

    def finalizar(self, resultados_list):
        # Guardamos la lista completa de Work Orders
        self.app.resultados_multilote = resultados_list

        if hasattr(self.app, "guardar_historial"):
            self.app.guardar_historial()

        # NUEVO: construir inputs editables por lote
        self._reconstruir_editables_por_resultado(resultados_list)

        if hasattr(self.app, 'cerrar_ventana_carga'):
            self.app.cerrar_ventana_carga()

        self.btn_run_nest.setEnabled(True)

        # =========================================================
        # FORMATO DE TIEMPO PARA EL POP-UP
        # =========================================================
        t_total = getattr(self.app, 'tiempo_calculo', 0)
        t_int = max(0, int(round(float(t_total))))
        horas = t_int // 3600
        mins = (t_int % 3600) // 60
        segs = t_int % 60
        if horas > 0:
            tiempo_str = f"{horas} h {mins} min {segs} seg"
        elif mins > 0:
            tiempo_str = f"{mins} min {segs} seg"
        else:
            tiempo_str = f"{segs} seg"

        mensaje = (
            f"Tiempo de procesamiento: {tiempo_str}\n\n"
            "Acomodo listo en BORRADOR. Modifica si es necesario y exporta cuando termines.\n\n"
            "El nesteo de largos también quedó calculado: revísalo en NESTEO DE LARGOS "
            "y elige qué barras van al pedido."
        )

        avisos = []
        for item in resultados_list or []:
            data = (item or {}).get("data")
            if isinstance(data, dict) and data.get("error"):
                avisos.append(str(data.get("error")))
                continue
            if not isinstance(data, dict):
                continue
            for clave, info in data.items():
                if not isinstance(info, dict):
                    continue
                if info.get("error"):
                    avisos.append(f"{clave}: {info.get('error')}")
                elif info.get("advertencia"):
                    avisos.append(f"{clave}: {info.get('advertencia')}")

        if avisos:
            texto_aviso = "\n\n".join(avisos[:5])
            if len(avisos) > 5:
                texto_aviso += f"\n\n(+{len(avisos) - 5} avisos más)"
            QMessageBox.warning(
                self,
                "Cálculo con avisos",
                f"{mensaje}\n\n⚠ Algunos grupos no quedaron completos:\n{texto_aviso}",
            )
        else:
            QMessageBox.information(self, "Cálculo Terminado", mensaje)

        # Dibujar la primera hoja después del diálogo (jobs grandes pueden tumbar Qt aquí).
        QTimer.singleShot(0, self._poblar_ui_tras_nesting_completado)

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

    def _actualizar_piezas_totales_label(self, resultados=None):
        if not hasattr(self, "lbl_piezas_totales"):
            return
        res = resultados if resultados is not None else getattr(self.app, "resultados_nesting", None)
        if not isinstance(res, dict) or not res:
            self.lbl_piezas_totales.setText("PIEZAS TOTALES: -")
            return
        total = 0
        for info in res.values():
            if isinstance(info, dict) and "error" not in info:
                total += contar_piezas_grupo(info)
        self.lbl_piezas_totales.setText(f"PIEZAS TOTALES: {total}")

    def sincronizar_overlays_clave(self, clave):
        """Actualiza REF/guillotina/tatuajes del retazo en la placa madre."""
        if not clave:
            return
        grp = (getattr(self.app, "resultados_nesting", None) or {}).get(clave)
        if not isinstance(grp, dict):
            return
        if grp.get("modo_largos_cu"):
            return
        hojas = grp.get("hojas")
        if not isinstance(hojas, list):
            return
        sincronizar_overlays_grupo(hojas)
        actualizar_eficiencias_resultados({clave: grp})
        self._recalcular_costos_grupo(clave)

    def _madre_de_hoja_rtz(self, clave, rtz_hoja):
        if not clave or not isinstance(rtz_hoja, dict) or not rtz_hoja.get("es_retazo"):
            return None
        hojas = (getattr(self.app, "resultados_nesting", None) or {}).get(clave, {}).get("hojas") or []
        idx = -1
        rtz_id = str(rtz_hoja.get("placa_id", "") or "")
        for i, h in enumerate(hojas):
            if h is rtz_hoja:
                idx = i
                break
        if idx < 0 and rtz_id:
            for i, h in enumerate(hojas):
                if h.get("es_retazo") and str(h.get("placa_id", "") or "") == rtz_id:
                    idx = i
                    break
        if idx <= 0:
            return None
        for i in range(idx - 1, -1, -1):
            if not (hojas[i] or {}).get("es_retazo"):
                return hojas[i]
        return None

    def sincronizar_overlays_rtz_en_vivo(self, clave, hoja_contexto=None):
        """Sincroniza overlays madre↔RTZ y redibuja la madre si está visible."""
        hoja = hoja_contexto
        if hoja is None:
            hoja = getattr(self.visor, "hoja_actual_data", None)
        if not clave:
            return None
        self.sincronizar_overlays_clave(clave)
        madre = None
        if isinstance(hoja, dict) and hoja.get("es_retazo"):
            madre = self._madre_de_hoja_rtz(clave, hoja)
        elif isinstance(hoja, dict) and not hoja.get("es_retazo"):
            madre = hoja
        visor = getattr(self, "visor", None)
        if madre and visor and getattr(visor, "hoja_actual_data", None) is madre:
            visor.dibujar_hoja_full(
                madre,
                clave,
                selected_indices=getattr(visor, "piezas_seleccionadas_indices", None),
                preserve_view=True,
            )
        return madre

    def _etiqueta_hoja_lista(self, hoja) -> str:
        try:
            seq = hoja.get("sheet_seq")
            if seq is not None:
                return f"H{int(seq)}"
        except (TypeError, ValueError):
            pass
        return ""

    def _hoja_es_la_seleccionada(self, hoja, clave) -> bool:
        if not hoja or clave != self.clave_actual:
            return False
        actual = self.hoja_actual_data
        if hoja is actual:
            return True
        if isinstance(hoja, dict) and isinstance(actual, dict):
            uid = str(hoja.get("sheet_uid") or "").strip()
            uid_actual = str(actual.get("sheet_uid") or "").strip()
            if uid and uid_actual and uid == uid_actual:
                return True
        return False

    def _ss_btn_placa_nesting(
        self,
        bg: str,
        fg: str,
        *,
        hover_bg: str | None = None,
        padding: str = "8px 10px",
        radius: int = 8,
        font_size: int = 12,
        align: str = "",
    ) -> str:
        from interface.qt.theme import COLOR_ACENTO, _shade

        border = "#CBD5E1" if fg == COLOR_TEXTO_TITULO else _shade(bg, -0.2)
        hover = hover_bg or _shade(bg, 0.12 if fg == "#FFFFFF" else -0.06)
        hover_border = "#94A3B8" if fg == COLOR_TEXTO_TITULO else _shade(bg, 0.05)
        pressed = _shade(bg, -0.1)
        base = (
            f"background-color:{bg};color:{fg};border:1px solid {border};"
            f"border-radius:{radius}px;padding:{padding};font-weight:600;"
            f"font-size:{font_size}px;{align}"
        )
        hover_rule = (
            f"background-color:{hover};color:{fg};border:1px solid {hover_border};"
            f"border-radius:{radius}px;"
        )
        return (
            f"QPushButton{{{base}}}"
            f"QPushButton:hover{{{hover_rule}}}"
            f"QPushButton:pressed{{background-color:{pressed};color:{fg};"
            f"border:1px solid {hover_border};border-radius:{radius}px;}}"
            f"QPushButton:focus{{color:{fg};border-color:{COLOR_ACENTO};"
            f"border-radius:{radius}px;outline:none;}}"
        )

    def _aplicar_estilo_btn_placa(self, btn, *, seleccionada: bool = False, **estilo) -> None:
        padding = "8px 10px"
        radius = 8
        font_size = 12
        es_retazo = bool(estilo.get("es_retazo"))
        align = "text-align:left;" if es_retazo else ""

        if seleccionada:
            bg, fg, hover = "#FFFFFF", COLOR_TEXTO_TITULO, "#F1F5F9"
        else:
            btn.setObjectName("")
            es_rtzc = bool(estilo.get("es_rtzc"))
            es_sobrante_rtz = bool(estilo.get("es_sobrante_rtz"))
            ignorada = bool(estilo.get("ignorada"))
            origen_str = str(estilo.get("origen_str") or "")

            if es_rtzc:
                bg, fg, hover = "#1C1917", "#FB923C", "#292524"
            elif es_sobrante_rtz or es_retazo:
                bg, fg, hover = "#0F172A", "#38BDF8", "#1E293B"
            elif ignorada:
                bg, fg, hover = "#1F2937", "#94A3B8", "#374151"
            elif origen_str:
                bg, fg, hover = "#323741", "#FCA5A5", "#3F4854"
            else:
                bg, fg, hover = "#323741", "#FFFFFF", "#3F4854"

        btn.setStyleSheet(
            self._ss_btn_placa_nesting(
                bg,
                fg,
                hover_bg=hover,
                padding=padding,
                radius=radius,
                font_size=font_size,
                align=align,
            )
        )
        pal = btn.palette()
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(fg))
        btn.setPalette(pal)
        btn.setAutoFillBackground(False)

    def _actualizar_seleccion_lista_hojas(self):
        layout = getattr(self, "_lista_hojas_layout", None)
        if layout is None:
            return
        for i in range(layout.count()):
            item = layout.itemAt(i)
            contenedor = item.widget() if item else None
            if contenedor is None:
                continue
            for btn in contenedor.findChildren(QPushButton):
                meta = getattr(btn, "_nest_meta", None)
                if not isinstance(meta, dict):
                    continue
                seleccionada = self._hoja_es_la_seleccionada(meta.get("hoja"), meta.get("clave"))
                self._aplicar_estilo_btn_placa(
                    btn,
                    seleccionada=seleccionada,
                    **meta.get("estilo", {}),
                )
        
    def procesar_lista_hojas(self, resultados):
        deduplicar_resultados_nesting(resultados, kerf_global=self._kerf_efectivo())
        sincronizar_overlays_resultados(resultados)
        actualizar_eficiencias_resultados(resultados)
        sincronizar_sobrantes_rtz_en_resultados(
            resultados,
            wo_name=self._order_label_para_rtz(),
        )
        for clave in (resultados or {}):
            if isinstance((resultados or {}).get(clave), dict):
                self._recalcular_costos_grupo(clave)
        wo_label = self._order_label_para_rtz()
        if not numeracion_hojas_es_consistente(resultados, wo_label):
            asignar_numeracion_global_hojas(resultados, wo_label, sobrescribir=True)
        scroll_clear(self.lista_hojas)

        from interface.nesting_costos import calcular_reporte_costos, aplicar_totales_a_tab

        reporte_costos = calcular_reporte_costos(
            resultados,
            app=self.app,
            lote_idx=int(getattr(self, "lote_actual_idx", 0) or 0),
            tab=self,
        )
        costo_proyecto = float(reporte_costos.get("total_mxn") or 0.0)
        self.total_usd_empresa = float(reporte_costos.get("total_empresa_mxn") or 0.0)
        self.total_usd_proveedor = 0.0

        claves_ordenadas = sorted(
            (k for k in resultados.keys() if isinstance(resultados.get(k), dict)),
            key=lambda k: grupo_nesting_sort_key(k, resultados.get(k)),
        )
        for clave in claves_ordenadas:
            info = resultados[clave]
            es_grupo_cu = self._es_grupo_cobre(clave, info)
            hojas_del_material = info.get("hojas", [])
            header = QWidget()
            hdr_lay = QHBoxLayout(header)
            hdr_lay.setContentsMargins(0, 10, 0, 0)
            efi_tanque = formatear_eficiencias_tanque(info)
            clave_txt = format_clave_calibre_display(clave)
            lbl_header = QLabel(clave_txt + (f" | {efi_tanque}" if efi_tanque else ""))
            lbl_header.setStyleSheet(f"font-weight:700;color:{COLOR_TEXTO_TITULO};")
            hdr_lay.addWidget(lbl_header)
            if es_grupo_cu:
                self._bind_menu_renestear_calibre_cobre(header, lbl_header, clave)
            else:
                self._bind_menu_compensar_calibre(header, lbl_header, clave)
            scroll_add_widget(self.lista_hojas, header)

            if es_grupo_cu and hojas_del_material:
                info.setdefault("ignorar_deduccion_cu", True)
                ign_cu = bool(info.get("ignorar_deduccion_cu", True))
                for h_cu in hojas_del_material:
                    if not h_cu.get("es_retazo"):
                        h_cu["ignorar_deduccion"] = ign_cu
                self._crear_switch_ignorar_cu_grupo(self.lista_hojas, clave, info)

            if len(hojas_del_material) == 0:
                aviso_txt, aviso_cross = self._texto_aviso_material_grupo(info)
                if not aviso_txt:
                    aviso_txt = "NO HAY EN INVENTARIO"
                err_lbl = QLabel(f"AVISO: {aviso_txt}")
                color = "#38BDF8" if aviso_cross else "#EF4444"
                err_lbl.setStyleSheet(f"font-weight:700;color:{color};")
                err_lbl.setWordWrap(True)
                scroll_add_widget(self.lista_hojas, err_lbl)
            else:
                for i, hoja in enumerate(hojas_del_material):
                    es_retazo = hoja.get('es_retazo', False)
                    nombre_placa = hoja.get('placa_id', f"P#{i+1}")
                    origen_str = " (PROVEEDOR)" if hoja.get('origen_placa') == "PROVEEDOR" else ""
                    efi_txt = formatear_eficiencias_placa(hoja)
                    if not es_retazo:
                        iguales = [
                            j for j, h in enumerate(hojas_del_material)
                            if str(h.get("placa_id", "") or "") == str(nombre_placa)
                            and not h.get("es_retazo")
                        ]
                        sufijo = f" · P{iguales.index(i) + 1}" if len(iguales) > 1 else ""
                    else:
                        sufijo = ""
                    ignorada = bool(hoja.get("ignorar_deduccion", False))
                    es_rtzc = es_placa_madre_rtzc(hoja)
                    es_sobrante_rtz = es_placa_madre_sobrante_rtz(hoja)
                    prefijo_ign = (
                        "[IGN] "
                        if ignorada
                        and not es_sobrante_rtz
                        and not es_rtzc
                        and not hoja.get("modo_largos_cu")
                        else ""
                    )
                    texto_btn = (
                        f"   {nombre_placa} (ACCESORIOS) | {efi_txt}"
                        if es_retazo else
                        f"{prefijo_ign}{nombre_placa}{sufijo}{origen_str} | {efi_txt}"
                    )
                    hoja_tag = self._etiqueta_hoja_lista(hoja)
                    if hoja_tag:
                        texto_btn = f"{texto_btn}  {hoja_tag}"
                    estilo_params = dict(
                        es_retazo=es_retazo,
                        es_rtzc=es_rtzc,
                        es_sobrante_rtz=es_sobrante_rtz,
                        ignorada=ignorada,
                        origen_str=origen_str,
                    )
                    seleccionada = self._hoja_es_la_seleccionada(hoja, clave)

                    fila_placa = QWidget()
                    fila_lay = QVBoxLayout(fila_placa)
                    fila_lay.setContentsMargins(20 if es_retazo else 0, 1, 0, 1)

                    btn = QPushButton(texto_btn)
                    btn._nest_meta = {"hoja": hoja, "clave": clave, "estilo": estilo_params}
                    self._aplicar_estilo_btn_placa(
                        btn,
                        seleccionada=seleccionada,
                        **estilo_params,
                    )
                    btn.clicked.connect(lambda checked=False, h=hoja, c=clave: self.dibujar_hoja_full(h, c))
                    fila_lay.addWidget(btn)
                    if not es_grupo_cu:
                        self._bind_menu_renestear_placa(btn, clave, hoja)

                    if (
                        not es_retazo
                        and not es_grupo_cu
                        and placa_debe_mostrar_opcion_ignorar(hoja, hojas_del_material)
                    ):
                        self._crear_switch_ignorar_placa(fila_placa, clave, hoja, hojas_del_material)

                    scroll_add_widget(self.lista_hojas, fila_placa)

        self._actualizar_tipo_cambio()
        self.costo_mxn_val = float(costo_proyecto)
        tc = float(self.tipo_cambio_usdmxn or 18.50)
        self.costo_usd_val = (self.costo_mxn_val / tc) if tc > 0 else 0.0
        self._actualizar_piezas_totales_label(resultados)
        if self.hoja_actual_data and self.clave_actual:
            self._actualizar_panel_placa(self.hoja_actual_data, self.clave_actual)
        self._actualizar_seleccion_lista_hojas()

    def _ctx_tiene_resultados(self, clave=None) -> bool:
        res = getattr(self.app, "resultados_nesting", None) or {}
        if not res:
            QMessageBox.warning(self, "Atención", "No hay resultados de nesting para esta acción.")
            return False
        if clave is not None and clave not in res:
            QMessageBox.warning(self, "Atención", "No se encontró ese calibre/material en el resultado.")
            return False
        return True

    def _ctx_hoja_valida(self, hoja, accion: str = "esta operación") -> bool:
        if not isinstance(hoja, dict):
            QMessageBox.warning(self, "Atención", f"No hay datos de placa válidos para {accion}.")
            return False
        if hoja.get("es_retazo", False):
            QMessageBox.information(
                self,
                accion,
                "Esta acción no aplica a placas RTZ/retazo o mini-nest.",
            )
            return False
        return True

    def _safe_ctx(self, titulo: str, fn):
        def run(*_args, **_kwargs):
            try:
                return fn()
            except Exception as e:
                QMessageBox.critical(self, titulo, f"Error inesperado:\n{e}")
        return run

    def _es_grupo_cobre(self, clave, info=None) -> bool:
        if isinstance(info, dict) and info.get("modo_largos_cu"):
            return True
        s = str(clave or "").strip().upper()
        if s.endswith("_CU"):
            return True
        if "_" in s:
            mat = s.split("_", 1)[1].strip().upper()
            if mat in ("CU", "COBRE", "COPPER") or "COBRE" in mat or "COPPER" in mat:
                return True
        return False

    def _wo_solo_cobre(self) -> bool:
        """True si TODAS las piezas importadas son cobre (work order 100% CU).

        El cobre se nestea aparte (barras largas deterministas), así que no debe
        entrar al análisis de lotes MES (escenarios de placa) aunque T>=4: se
        nestea directo con la cantidad tal cual.
        """
        datos = getattr(self.app, "datos_partes_actuales", []) or []
        if not datos:
            return False
        for item in datos:
            try:
                mat = str(item[1] or "").strip().upper()
            except (IndexError, TypeError):
                return False
            es_cu = (
                mat in ("CU", "COBRE", "COPPER")
                or "COBRE" in mat
                or "COPPER" in mat
            )
            if not es_cu:
                return False
        return True

    def _inventario_desde_resultado(self, resultado) -> dict:
        inventario = {}
        for hoja in (resultado or {}).get("hojas") or []:
            for nom, cnt in self._resumen_piezas_reales_hoja(hoja).items():
                inventario[nom] = inventario.get(nom, 0) + int(cnt)
        return inventario

    def _validar_renest_conserva_inventario(self, inv_antes: dict, resultado) -> tuple[bool, str]:
        inv_antes = self._inventario_piezas_canonico(inv_antes)
        inv_despues = self._inventario_piezas_canonico(self._inventario_desde_resultado(resultado))
        if self._inventarios_equivalentes(inv_antes, inv_despues):
            return True, ""
        total_antes = sum(int(v) for v in (inv_antes or {}).values())
        total_despues = sum(int(v) for v in (inv_despues or {}).values())
        diff = self._texto_diff_inventario(inv_antes, inv_despues)
        pend = (resultado or {}).get("piezas_pendientes") or []
        extra = ""
        if pend:
            extra = "\nPendientes: " + ", ".join(str(x) for x in pend[:12])
        return (
            False,
            f"El renesteo no conservó todas las piezas ({total_despues}/{total_antes}).\n"
            f"{diff}{extra}".strip(),
        )

    def _aplicar_flags_cobre_resultado(self, clave, resultado, backup_grp=None):
        if not isinstance(resultado, dict):
            return
        if not self._es_grupo_cobre(clave, resultado):
            resultado.pop("ignorar_deduccion_cu", None)
            for hoja in resultado.get("hojas") or []:
                if isinstance(hoja, dict) and not hoja.get("es_retazo"):
                    hoja["ignorar_deduccion"] = False
            return
        ign = True
        if isinstance(backup_grp, dict):
            ign = bool(backup_grp.get("ignorar_deduccion_cu", True))
        resultado["ignorar_deduccion_cu"] = ign
        for hoja in resultado.get("hojas") or []:
            if isinstance(hoja, dict) and not hoja.get("es_retazo"):
                hoja["ignorar_deduccion"] = ign

    def _bind_menu_renestear_calibre_cobre(self, header, lbl_header, clave):
        def show_menu(pos, widget):
            if not self._ctx_tiene_resultados(clave):
                return
            menu = QMenu(self)
            menu.addAction(
                "RENESTEAR CALIBRE COMPLETO",
                self._safe_ctx(
                    "Renestear calibre cobre",
                    lambda c=clave: self.renestear_calibre_completo_ui(c),
                ),
            )
            menu.exec(widget.mapToGlobal(pos))

        for w in (header, lbl_header):
            w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            w.customContextMenuRequested.connect(lambda pos, ww=w: show_menu(pos, ww))

    def _bind_menu_renestear_placa(self, btn, clave, hoja):
        if hoja.get("es_retazo", False):
            return

        def show_menu(pos):
            if not self._ctx_tiene_resultados(clave):
                return
            if not self._ctx_hoja_valida(hoja, "Menú de placa"):
                return
            menu = QMenu(self)
            bloque = self._desglosar_bloque_placa_mini(clave, hoja)
            tiene_rtz = bool(bloque.get("idx_retazos"))
            es_cu_largos = bool(hoja.get("modo_largos_cu"))

            sub_renest = QMenu("RENESTEAR", menu)
            if tiene_rtz and not es_cu_largos:
                sub_renest.addAction(
                    "CON RTZ (conservar retazo)",
                    self._safe_ctx(
                        "Renestear con RTZ",
                        lambda c=clave, h=hoja: self.renestear_solo_placa(
                            c, h, absorber_rtz=False
                        ),
                    ),
                )
                sub_renest.addAction(
                    "SIN RTZ (piezas a placa madre)",
                    self._safe_ctx(
                        "Renestear sin RTZ",
                        lambda c=clave, h=hoja: self.renestear_solo_placa(
                            c, h, absorber_rtz=True
                        ),
                    ),
                )
                menu.addMenu(sub_renest)
            else:
                menu.addAction(
                    "RENESTEAR",
                    self._safe_ctx(
                        "Renestear placa",
                        lambda c=clave, h=hoja: self.renestear_solo_placa(c, h),
                    ),
                )
            menu.addAction(
                "CAMBIAR PIEZAS A OTRA PLACA",
                self._safe_ctx(
                    "Transferencia",
                    lambda c=clave, h=hoja: abrir_modal_transferencia_masiva(self, c, h),
                ),
            )
            if not es_cu_largos and not self._es_grupo_cobre(clave):
                menu.addAction(
                    "COMPENSAR PLASMA",
                    self._safe_ctx(
                        "Compensación",
                        lambda c=clave, h=hoja: self.compensar_solo_placa(c, h),
                    ),
                )
            info_grupo = (self.app.resultados_nesting or {}).get(clave)
            puede_sobrante_forzado = (
                not hoja_excluida_de_rtz_sobrante(hoja)
                and not self._es_grupo_cobre(clave, info_grupo)
            )
            if puede_sobrante_forzado:
                menu.addSeparator()
                if hoja_es_sobrante_sin_compra(hoja):
                    menu.addAction(
                        "QUITAR SOBRANTE (forzado)",
                        self._safe_ctx(
                            "Quitar sobrante",
                            lambda c=clave, h=hoja: self._toggle_ignorar_deduccion_placa(c, h, False),
                        ),
                    )
                else:
                    menu.addAction(
                        "MARCAR COMO SOBRANTE (forzado)",
                        self._safe_ctx(
                            "Sobrante forzado",
                            lambda c=clave, h=hoja: self._toggle_ignorar_deduccion_placa(c, h, True),
                        ),
                    )
            sub_cambiar = QMenu("CAMBIAR DE PLACA", menu)
            sub_cambiar.aboutToShow.connect(
                lambda sm=sub_cambiar, c=clave, h=hoja: self._rellenar_submenu_cambiar_placa(sm, c, h)
            )
            menu.addMenu(sub_cambiar)
            menu.exec(btn.mapToGlobal(pos))

        btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        btn.customContextMenuRequested.connect(show_menu)

    def _offset_compensacion_mm_desde_clave(self, clave):
        calibre_txt = clave.split("_", 1)[0] if "_" in str(clave) else str(clave)
        try:
            parse_thk = getattr(self.app.motor_nesting, "_parse_thickness_value", None)
            thk_in = parse_thk(calibre_txt) if callable(parse_thk) else None
            if thk_in is None:
                thk_in = float(self.app.motor_nesting._extraer_numero(calibre_txt))
        except Exception:
            thk_in = 0.0
        if thk_in <= 0:
            return None
        return float(compute_plasma_offset_mm(thk_in))

    def _bind_menu_compensar_calibre(self, header, lbl_header, clave):
        def show_menu(pos, widget):
            if not self._ctx_tiene_resultados(clave):
                return
            menu = QMenu(self)
            sub_renest = QMenu("Renestear calibre completo", menu)
            sub_renest.aboutToShow.connect(
                lambda sm=sub_renest, c=clave: self._rellenar_submenu_renest_calibre(sm, c)
            )
            menu.addMenu(sub_renest)
            menu.addAction(
                "Compensar calibre completo (Plasma)",
                self._safe_ctx(
                    "Compensación",
                    lambda c=clave: self.compensar_calibre_completo(c),
                ),
            )
            menu.exec(widget.mapToGlobal(pos))

        for w in (header, lbl_header):
            w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            w.customContextMenuRequested.connect(lambda pos, ww=w: show_menu(pos, ww))

    def _aplicar_compensacion_poligono(self, poly, offset_mm):
        try:
            if poly is None or poly.is_empty:
                return None
            p = poly.buffer(0)
            if p.is_empty:
                return None
            c = p.buffer(float(offset_mm), join_style=1, quad_segs=24)
            if c.is_empty:
                return None
            return c.buffer(0)
        except Exception:
            return None

    def _compensar_poligonos_en_hoja(self, hoja, offset_mm):
        """Aplica buffer plasma a poligonos de piezas reales (fallback recalcular_hoja_full)."""
        from modules.nesting_engine.geometry_parser import reconstruir_poly_seguro

        hoja_out = copy.deepcopy(hoja)
        off = float(offset_mm or 0.0)
        if off <= 0:
            return hoja_out

        for pz in hoja_out.get("piezas") or []:
            nom_pz = str(pz.get("nombre", "")).strip()
            if self._es_pieza_virtual(nom_pz):
                continue
            pols = pz.get("poligonos") or []
            if not pols:
                continue
            poly = reconstruir_poly_seguro(pols)
            if poly is None or poly.is_empty:
                continue
            comp = self._aplicar_compensacion_poligono(poly, off)
            if comp is None or comp.is_empty:
                continue
            try:
                from shapely.geometry import GeometryCollection, MultiPolygon

                if isinstance(comp, MultiPolygon):
                    comp = max(comp.geoms, key=lambda g: float(g.area))
                elif isinstance(comp, GeometryCollection):
                    polys = [
                        g for g in comp.geoms
                        if getattr(g, "geom_type", "") == "Polygon" and not g.is_empty
                    ]
                    if polys:
                        comp = max(polys, key=lambda g: float(g.area))
            except Exception:
                pass
            try:
                outer = list(comp.exterior.coords)
            except Exception:
                continue
            if outer and outer[0] == outer[-1]:
                outer = outer[:-1]
            holes = []
            for interior in getattr(comp, "interiors", []):
                ring = list(interior.coords)
                if ring and ring[0] == ring[-1]:
                    ring = ring[:-1]
                if len(ring) >= 3:
                    holes.append(ring)
            if len(outer) >= 3:
                pz["poligonos"] = [outer] + holes
        return hoja_out

    def _nombre_canonico_pieza(self, nom):
        s = str(nom or "").strip()
        if not s:
            return ""
        if "," in s:
            return s.split(",", 1)[0].strip()
        return s

    def _inventario_piezas_canonico(self, inv):
        out = {}
        for nom, cnt in (inv or {}).items():
            c = self._nombre_canonico_pieza(nom)
            if not c:
                continue
            out[c] = out.get(c, 0) + int(cnt or 0)
        return out

    def _datos_partes_activos_para_nesting(self):
        """Misma fuente que ejecutar_nesting: lote editable activo o PARTS global."""
        if getattr(self.app, "editable_inputs_actuales", None):
            datos = self._clonar_datos_partes_edicion(self.app.editable_inputs_actuales)
            if datos:
                return datos
        return self._clonar_datos_partes_edicion(
            getattr(self.app, "datos_partes_actuales", []) or []
        )

    def _contar_piezas_reales_grupo(self, clave):
        grp = (self.app.resultados_nesting or {}).get(clave) or {}
        hojas = grp.get("hojas") or []
        conteo = {}
        for hoja in hojas:
            for p in (hoja.get("piezas") or []):
                nom = self._nombre_canonico_pieza(p.get("nombre", ""))
                if not nom or self._es_pieza_virtual(nom):
                    continue
                conteo[nom] = conteo.get(nom, 0) + 1
        return conteo

    def _conteo_piezas_job_grupo(self, clave):
        """Cantidades del lote/job activo para calibre+material (fuente de verdad del renesteo)."""
        try:
            calibre_hoja, material_hoja = (str(clave).split("_", 1) + [""])[:2]
        except Exception:
            calibre_hoja, material_hoja = str(clave), ""
        conteo = {}
        for p_nom, mat, qty, cal, st, ruta in self._datos_partes_activos_para_nesting():
            nom = self._nombre_canonico_pieza(p_nom)
            if not nom:
                continue
            if not self.app.motor_nesting._coinciden(calibre_hoja, cal):
                continue
            if not self.app.motor_nesting._coinciden(material_hoja, mat):
                continue
            try:
                q = max(0, int(qty or 0))
            except Exception:
                q = 0
            if q <= 0:
                continue
            conteo[nom] = conteo.get(nom, 0) + q
        return conteo

    def _construir_fuente_geometria_por_nombre(self, clave):
        material_hoja = clave.split("_")[1] if "_" in clave else clave
        calibre_hoja = clave.split("_")[0] if "_" in clave else ""
        fuente = {}

        def _marks_from_raw(raw_marks):
            try:
                from shapely.geometry import LineString, MultiLineString
            except Exception:
                return None
            segs = []
            for seg in (raw_marks or []):
                pts = []
                for pt in (seg or []):
                    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                        try:
                            pts.append((float(pt[0]), float(pt[1])))
                        except Exception:
                            continue
                if len(pts) >= 2:
                    segs.append(pts)
            if not segs:
                return LineString()
            if len(segs) == 1:
                return LineString(segs[0])
            return MultiLineString(segs)

        def _agregar_fuente(nom, poly, marks, cal, mat, ruta):
            canon = self._nombre_canonico_pieza(nom)
            if not canon or canon in fuente or poly is None or getattr(poly, "is_empty", True):
                return
            from shapely import affinity
            from interface.utils_nesting import clave_orientacion_cobre_ruta, es_material_cobre

            poly_use = poly
            marks_use = marks
            if es_material_cobre(mat):
                rot_deg = int(
                    (getattr(self.app, "orientacion_cobre_por_ruta", {}) or {}).get(
                        clave_orientacion_cobre_ruta(ruta), 0
                    )
                ) % 360
                if rot_deg:
                    try:
                        cx, cy = poly_use.centroid.x, poly_use.centroid.y
                        poly_use = affinity.rotate(
                            poly_use, rot_deg, origin=(cx, cy), use_radians=False
                        )
                        if marks_use is not None and not marks_use.is_empty:
                            marks_use = affinity.rotate(
                                marks_use, rot_deg, origin=(cx, cy), use_radians=False
                            )
                    except Exception:
                        pass

            mx, my, _, _ = poly_use.bounds
            marks_ok = marks_use
            try:
                if marks_ok is None:
                    from shapely.geometry import LineString
                    marks_ok = LineString()
            except Exception:
                marks_ok = marks_use
            fuente[canon] = {
                "nombre": canon,
                "poly_base": affinity.translate(poly_use, -mx, -my),
                "marks_base": affinity.translate(marks_ok, -mx, -my) if hasattr(marks_ok, "is_empty") and not marks_ok.is_empty else marks_ok,
                "area_base": float(poly_use.area),
                "calibre": cal,
                "material": mat,
                "ruta": ruta,
            }

        # 1) Fuente primaria: geometría fresca desde rutas DXF.
        for p_nom, mat, qty, cal, st, ruta in self._datos_partes_activos_para_nesting():
            nom = str(p_nom or "").strip()
            if not nom:
                continue
            if not self.app.motor_nesting._coinciden(calibre_hoja, cal):
                continue
            if not self.app.motor_nesting._coinciden(material_hoja, mat):
                continue
            poly, marks = self.app.motor_nesting.recuperar_geometria_robusta(ruta)
            if not poly:
                continue
            _agregar_fuente(nom, poly, marks, cal, mat, ruta)

        # 2) Fallback robusto: reconstruir desde piezas ya anidadas en memoria (cuando la ruta DXF falla).
        grp = (self.app.resultados_nesting or {}).get(clave) or {}
        for hoja in (grp.get("hojas") or []):
            for p in (hoja.get("piezas") or []):
                nom = str(p.get("nombre", "")).strip()
                canon = self._nombre_canonico_pieza(nom)
                if not canon or self._es_pieza_virtual(nom) or canon in fuente:
                    continue
                pols = p.get("poligonos") or []
                if not pols or not pols[0]:
                    continue
                try:
                    from shapely.geometry import Polygon
                    outer = []
                    for pt in (pols[0] or []):
                        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                            try:
                                outer.append((float(pt[0]), float(pt[1])))
                            except Exception:
                                continue
                    holes = []
                    for h in pols[1:]:
                        hh = []
                        for pt in (h or []):
                            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                                try:
                                    hh.append((float(pt[0]), float(pt[1])))
                                except Exception:
                                    continue
                        if len(hh) >= 3:
                            holes.append(hh)
                    if len(outer) < 3:
                        continue
                    poly = Polygon(outer, holes)
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    if poly is None or poly.is_empty:
                        continue
                    marks = _marks_from_raw(p.get("marcas"))
                    _agregar_fuente(
                        nom,
                        poly,
                        marks,
                        p.get("calibre", calibre_hoja),
                        p.get("material", material_hoja),
                        p.get("ruta", ""),
                    )
                except Exception:
                    continue
        return fuente

    def _build_piezas_para_renest_compensado(self, clave, cupos_compensar_por_nombre, offset_mm):
        """
        Construye lista completa de piezas del calibre con compensación parcial/global.
        cupos_compensar_por_nombre: dict nombre->cantidad de instancias a compensar.
        """
        conteo_total = self._contar_piezas_reales_grupo(clave)
        if not conteo_total:
            return [], {}
        fuente = self._construir_fuente_geometria_por_nombre(clave)
        if not fuente:
            return [], {}

        cupos = {str(k): int(v) for k, v in (cupos_compensar_por_nombre or {}).items() if int(v or 0) > 0}
        compensados_reales = {}
        piezas_out = []
        for nom, total in conteo_total.items():
            src = fuente.get(nom)
            if not src:
                continue
            cupo_nom = int(cupos.get(nom, 0))
            for i in range(int(total)):
                aplicar_comp = i < cupo_nom and float(offset_mm or 0.0) > 0
                poly_use = src["poly_base"]
                area_use = src["area_base"]
                if aplicar_comp:
                    comp = self._aplicar_compensacion_poligono(src["poly_base"], float(offset_mm))
                    if comp is None or comp.is_empty:
                        # Si no se puede compensar esta pieza, mantenemos base para no romper el lote.
                        aplicar_comp = False
                    else:
                        poly_use = comp
                        area_use = float(comp.area)
                        compensados_reales[nom] = compensados_reales.get(nom, 0) + 1

                piezas_out.append(
                    {
                        "nombre": src["nombre"],
                        "poly": copy.deepcopy(poly_use),
                        "marks": copy.deepcopy(src["marks_base"]),
                        "area": area_use,
                        "calibre": src["calibre"],
                        "material": src["material"],
                        "ruta": src["ruta"],
                    }
                )
        return piezas_out, compensados_reales

    def _meta_geometria_base_por_nombre(self, clave):
        meta = {}
        for nom, src in (self._construir_fuente_geometria_por_nombre(clave) or {}).items():
            try:
                poly = src["poly_base"]
                minx, miny, maxx, maxy = poly.bounds
                meta[str(nom)] = {
                    "w": float(maxx - minx),
                    "h": float(maxy - miny),
                    "area": float(poly.area),
                }
            except Exception:
                continue
        return meta

    def _pieza_parece_compensada_plasma(self, p, base_meta, offset_mm) -> bool:
        nom = str(p.get("nombre", "")).strip()
        base = base_meta.get(nom)
        if not base:
            return False
        pols = p.get("poligonos") or []
        if not pols:
            return False
        try:
            from modules.nesting_engine.geometry_parser import reconstruir_poly_seguro

            poly = reconstruir_poly_seguro(pols)
            if poly is None or poly.is_empty:
                return False
            area_fin = float(poly.area)
            area_base = float(base.get("area", 0.0) or 0.0)
            if area_base <= 0.0:
                return False
            umbral = max(2.0, float(offset_mm or 0.0) * 1.5)
            if (area_fin - area_base) > umbral:
                return True
            w_fin, h_fin = float(poly.bounds[2] - poly.bounds[0]), float(poly.bounds[3] - poly.bounds[1])
            w_base = float(base.get("w", 0.0) or 0.0)
            h_base = float(base.get("h", 0.0) or 0.0)
            tol = max(0.35, float(offset_mm or 0.0) * 0.35)
            return (w_fin - w_base) > tol or (h_fin - h_base) > tol
        except Exception:
            return False

    def _marcar_hojas_compensadas_plasma(self, clave, resultado_grupo, compensados_por_nombre, offset_mm):
        if not isinstance(resultado_grupo, dict):
            return
        hojas = resultado_grupo.get("hojas") or []
        if not hojas:
            return

        base_meta = self._meta_geometria_base_por_nombre(clave)
        if not base_meta:
            return

        for h in hojas:
            h.pop("plasma_compensado_manual", None)
            h.pop("plasma_offset_mm_manual", None)
            h.pop("plasma_piezas_compensadas", None)
            if h.get("es_retazo", False):
                continue
            piezas_comp_hoja = 0
            for p in (h.get("piezas") or []):
                nom = str(p.get("nombre", "")).strip()
                p.pop("plasma_compensada_manual", None)
                if self._es_pieza_virtual(nom):
                    continue
                if self._pieza_parece_compensada_plasma(p, base_meta, offset_mm):
                    p["plasma_compensada_manual"] = True
                    piezas_comp_hoja += 1

            if piezas_comp_hoja > 0:
                h["plasma_compensado_manual"] = True
                h["plasma_offset_mm_manual"] = float(offset_mm or 0.0)
                h["plasma_piezas_compensadas"] = int(piezas_comp_hoja)

    def _renestear_clave_con_compensacion(
        self,
        clave,
        cupos_compensar_por_nombre,
        offset_mm,
        titulo,
        post_fill=False,
        placa_id_objetivo=None,
    ):
        if not getattr(self.app, "resultados_nesting", None):
            return QMessageBox.warning(self, "Atención", "No hay resultados de nesting.")
        if clave not in self.app.resultados_nesting:
            return QMessageBox.warning(self, "Atención", "No se encontró el calibre/material en el resultado.")

        try:
            k = self._kerf_efectivo()
        except Exception:
            return QMessageBox.critical(self, "Error", "Kerf inválido.")
        m = self.global_margin_val
        opt = self.cmb_opt.currentText() if hasattr(self, "cmb_opt") else "OPTIMIZAR LARGO Y ANCHO"
        corner = self.global_corner_val

        if hasattr(self.app, "abrir_ventana_carga"):
            self.app.abrir_ventana_carga(titulo)

        def worker():
            backup_grp = copy.deepcopy((self.app.resultados_nesting or {}).get(clave))
            try:
                if hasattr(self.app, "actualizar_progreso"):
                    self.app.actualizar_progreso("Preparando geometrías del calibre...", 0.20)

                piezas_pack, compensados = self._build_piezas_para_renest_compensado(
                    clave, cupos_compensar_por_nombre, offset_mm
                )
                if not piezas_pack:
                    raise RuntimeError("No se pudieron reconstruir piezas para renesteo.")
                if not compensados:
                    raise RuntimeError("No se logró aplicar compensación en las piezas objetivo.")

                if hasattr(self.app, "actualizar_progreso"):
                    self.app.actualizar_progreso("Buscando placas candidatas...", 0.45)
                datos_placas = self.app.plates_manager.obtener_datos_placas()

                if hasattr(self.app, "actualizar_progreso"):
                    self.app.actualizar_progreso("Renesteando calibre completo...", 0.75)
                raw = self.app.motor_nesting._procesar_grupo_parallel(
                    clave,
                    piezas_pack,
                    datos_placas,
                    k,
                    m,
                    opt,
                    corner,
                    self._work_order_label_lote_activo(),
                    sin_rtz=True,
                )
                resultado = raw[1] if isinstance(raw, tuple) and len(raw) == 2 else raw
                if not isinstance(resultado, dict) or resultado.get("error"):
                    raise RuntimeError(str((resultado or {}).get("error", "Sin resultado válido del motor.")))
                if not (resultado.get("hojas") or []):
                    raise RuntimeError("El renesteo no generó hojas válidas.")

                self._marcar_hojas_compensadas_plasma(clave, resultado, compensados, offset_mm)
                self.app.resultados_nesting[clave] = resultado
                self._recalcular_costos_grupo(clave)
                self._replicar_lote_activo_a_gemelos()

                def on_ok():
                    if hasattr(self.app, "cerrar_ventana_carga"):
                        self.app.cerrar_ventana_carga()
                    hojas = (self.app.resultados_nesting.get(clave) or {}).get("hojas") or []
                    if hojas:
                        hojas_orden = list(hojas)
                        if placa_id_objetivo:
                            pref = [
                                hh
                                for hh in hojas_orden
                                if str(hh.get("placa_id", "")).strip() == str(placa_id_objetivo).strip()
                            ]
                            if pref:
                                hojas_orden = pref + [hh for hh in hojas_orden if hh not in pref]
                        hoja_mostrar = max(
                            hojas_orden,
                            key=lambda hh: int(hh.get("plasma_piezas_compensadas", 0) or 0),
                        )
                        if int(hoja_mostrar.get("plasma_piezas_compensadas", 0) or 0) <= 0:
                            hoja_mostrar = hojas[0]
                        if post_fill:
                            self._llenar_placa_desde_otras_hojas(clave, hoja_mostrar)
                        self.dibujar_hoja_full(hoja_mostrar, clave)
                    self.procesar_lista_hojas(self.app.resultados_nesting)
                    total_comp = sum(int(v) for v in compensados.values())
                    QMessageBox.information(self, 
                        "Compensación",
                        f"Renesteo completado.\nPiezas compensadas: {total_comp}\nHojas resultantes: {len(hojas)}",
                    )

                self.app.after(0, on_ok)
            except Exception as e:
                # Rollback seguro para no dejar estado inconsistente.
                if backup_grp is not None:
                    self.app.resultados_nesting[clave] = backup_grp

                def on_err(msg=str(e)):
                    if hasattr(self.app, "cerrar_ventana_carga"):
                        self.app.cerrar_ventana_carga()
                    QMessageBox.critical(self, 
                        "Compensación",
                        f"No se pudo completar el renesteo compensado.\n\nDetalle:\n{msg}",
                    )

                self.app.after(0, on_err)

        threading.Thread(target=worker, daemon=True).start()

    def compensar_solo_placa(self, clave, hoja):
        offset_mm = self._offset_compensacion_mm_desde_clave(clave)
        if offset_mm is None:
            return QMessageBox.warning(self, 
                "Compensación",
                "No se pudo leer el calibre para calcular compensación plasma.",
            )
        bloque = self._desglosar_bloque_placa_mini(clave, hoja)
        tiene_rtz = bool(bloque.get("idx_retazos"))
        resumen_placa = self._resumen_bloque_placa_y_rtz(
            bloque, absorber_rtz=tiene_rtz
        )
        if not resumen_placa:
            return QMessageBox.warning(self, 
                "Compensación",
                "No se detectaron piezas reales en la placa seleccionada.",
            )
        if tiene_rtz:
            if QMessageBox.question(
                self,
                "Compensar plasma",
                "Se compensarán todas las piezas del bloque (placa madre + RTZ), "
                "se renestearán juntas en la menor cantidad de placas posible "
                "y los RTZ se eliminarán del resultado.\n\n¿Continuar?",
            ) != QMessageBox.StandardButton.Yes:
                return
        self.renestear_solo_placa(
            clave,
            hoja,
            compensar_plasma=True,
            offset_mm_forzado=offset_mm,
            absorber_rtz=tiene_rtz,
        )

    def _build_piezas_para_renest_calibre(self, clave):
        conteo_job = self._conteo_piezas_job_grupo(clave)
        conteo_nido = self._contar_piezas_reales_grupo(clave)
        # Renesteo de calibre completo debe usar el job/lote, no solo lo ya colocado.
        conteo_total = conteo_job if conteo_job else conteo_nido
        if not conteo_total:
            self._renest_calibre_build_info = {}
            return []
        fuente = self._construir_fuente_geometria_por_nombre(clave)
        if not fuente:
            self._renest_calibre_build_info = {"error": "sin_fuente"}
            return []
        piezas_out = []
        faltantes_geom = []
        for nom, total in conteo_total.items():
            src = fuente.get(nom)
            if not src:
                faltantes_geom.append((nom, int(total)))
                continue
            for _ in range(int(total)):
                piezas_out.append(
                    {
                        "nombre": src["nombre"],
                        "poly": copy.deepcopy(src["poly_base"]),
                        "marks": copy.deepcopy(src["marks_base"]),
                        "area": src["area_base"],
                        "calibre": src["calibre"],
                        "material": src["material"],
                        "ruta": src["ruta"],
                    }
                )
        self._renest_calibre_build_info = {
            "conteo_job": conteo_job,
            "conteo_nido": conteo_nido,
            "faltantes_geom": faltantes_geom,
            "total_esperado": sum(int(v) for v in conteo_total.values()),
            "total_generado": len(piezas_out),
        }
        return piezas_out

    def renestear_calibre_completo_ui(self, clave, *, candidata_placa=None):
        if not getattr(self.app, "resultados_nesting", None):
            return QMessageBox.warning(self, "Atención", "No hay resultados de nesting.")
        if clave not in self.app.resultados_nesting:
            return QMessageBox.warning(self, "Atención", "No se encontró ese calibre/material.")
        piezas_pack = self._build_piezas_para_renest_calibre(clave)
        build_info = getattr(self, "_renest_calibre_build_info", {}) or {}
        faltantes_geom = build_info.get("faltantes_geom") or []
        if faltantes_geom:
            lineas = "\n".join(f"  · {nom}: {cnt}" for nom, cnt in faltantes_geom[:12])
            extra = f"\n… y {len(faltantes_geom) - 12} más." if len(faltantes_geom) > 12 else ""
            return QMessageBox.warning(
                self,
                "Atención",
                "No se encontró geometría DXF para todas las piezas del job:\n"
                f"{lineas}{extra}",
            )
        if not piezas_pack:
            return QMessageBox.warning(self, 
                "Atención",
                "No se pudieron reconstruir las piezas de este calibre para renestear.",
            )
        total_job = int(build_info.get("total_esperado") or len(piezas_pack))
        if len(piezas_pack) != total_job:
            return QMessageBox.warning(
                self,
                "Atención",
                f"No se pudieron reconstruir todas las piezas del job.\n"
                f"Esperadas: {total_job} · Generadas: {len(piezas_pack)}.\n"
                "Revise PARTS y rutas DXF antes de renestear.",
            )
        total_nido = sum(int(v) for v in (build_info.get("conteo_nido") or {}).values())
        aviso_cantidad = ""
        if total_nido and total_job != total_nido:
            aviso_cantidad = (
                f"\n\nPiezas en el job: {total_job}\n"
                f"Piezas en el nesteo actual: {total_nido}\n"
                "Se renesteará con la cantidad del job/lote activo."
            )
        es_cobre = self._es_grupo_cobre(clave)
        cu_separacion_in = None
        cu_largo_sin_separacion_in = None
        if es_cobre:
            from interface.qt.dialogs.nesting_modals import preguntar_separacion_cobre_renest
            from modules.nesting_engine.cu_largos_nesting import (
                DEFAULT_SEPARACION_CU_IN,
                LARGO_SIN_SEPARACION_CU_IN,
            )

            grp_act = (self.app.resultados_nesting or {}).get(clave) or {}
            valor_sep = float(grp_act.get("separacion_cu_in", DEFAULT_SEPARACION_CU_IN))
            valor_largo = float(
                grp_act.get("largo_sin_separacion_cu_in", LARGO_SIN_SEPARACION_CU_IN)
            )
            opts_cu = preguntar_separacion_cobre_renest(self, valor_sep, valor_largo)
            if opts_cu is None:
                return
            cu_separacion_in, cu_largo_sin_separacion_in = opts_cu
            detalle = (
                f"Se volverá a optimizar el calibre {clave} en barras largo CU "
                f"(gap {cu_separacion_in:g}\", piezas ≤{cu_largo_sin_separacion_in:g}\" sin gap)."
                f"{aviso_cantidad}\n\n¿Continuar?"
            )
            titulo = "Renestear cobre en largos"
        else:
            placa_txt = ""
            if candidata_placa:
                placa_txt = (
                    f"\n\nPlaca seleccionada:\n"
                    f"  {self._etiqueta_placa_inventario(candidata_placa)}"
                )
            detalle = (
                f"Se volverá a optimizar todo el calibre {clave} desde cero."
                f"{placa_txt}{aviso_cantidad}\n\n¿Continuar?"
            )
            titulo = "Renestear calibre completo"
        if QMessageBox.question(self, titulo, detalle) != QMessageBox.StandardButton.Yes:
            return

        try:
            k = self._kerf_efectivo()
        except Exception:
            return QMessageBox.critical(self, "Error", "Kerf inválido.")

        m = self.global_margin_val
        opt = self.cmb_opt.currentText() if hasattr(self, "cmb_opt") else "OPTIMIZAR LARGO Y ANCHO"
        corner = self.global_corner_val

        if hasattr(self.app, "abrir_ventana_carga"):
            self.app.abrir_ventana_carga("Renesteando calibre completo...")

        sep_cu = cu_separacion_in
        largo_sin_cu = cu_largo_sin_separacion_in

        def worker():
            backup_grp = copy.deepcopy((self.app.resultados_nesting or {}).get(clave))
            inv_esperado = self._conteo_piezas_job_grupo(clave) or self._inventario_piezas_grupo(clave)
            try:
                datos_placas = self.app.plates_manager.obtener_datos_placas()
                if candidata_placa:
                    datos_placas = self._filtrar_datos_placas_para_candidata(
                        datos_placas, candidata_placa
                    )
                    if not datos_placas:
                        raise RuntimeError(
                            "La placa seleccionada ya no está disponible en inventario."
                        )
                raw = self.app.motor_nesting._procesar_grupo_parallel(
                    clave,
                    piezas_pack,
                    datos_placas,
                    k,
                    m,
                    opt,
                    corner,
                    self._work_order_label_lote_activo(),
                    cu_routing_override="largos" if es_cobre else None,
                    cu_separacion_in=sep_cu if es_cobre else None,
                    cu_largo_sin_separacion_in=largo_sin_cu if es_cobre else None,
                )
                resultado = raw[1] if isinstance(raw, tuple) and len(raw) == 2 else raw
                if not isinstance(resultado, dict) or resultado.get("error"):
                    raise RuntimeError(str((resultado or {}).get("error", "Sin resultado válido.")))
                if not (resultado.get("hojas") or []):
                    raise RuntimeError("El renesteo no generó hojas válidas.")

                ok_inv, msg_inv = self._validar_renest_conserva_inventario(inv_esperado, resultado)
                if not ok_inv:
                    raise RuntimeError(msg_inv)

                self._aplicar_flags_cobre_resultado(clave, resultado, backup_grp)
                self.app.resultados_nesting[clave] = resultado
                self._recalcular_costos_grupo(clave)
                self._replicar_lote_activo_a_gemelos()

                def on_ok():
                    if hasattr(self.app, "cerrar_ventana_carga"):
                        self.app.cerrar_ventana_carga()
                    hojas = (self.app.resultados_nesting.get(clave) or {}).get("hojas") or []
                    if hojas:
                        self.dibujar_hoja_full(hojas[0], clave)
                    self.procesar_lista_hojas(self.app.resultados_nesting)
                    QMessageBox.information(self, 
                        "Renesteo",
                        f"Calibre {clave} renesteado.\nHojas resultantes: {len(hojas)}",
                    )

                self.app.after(0, on_ok)
            except Exception as e:
                if backup_grp is not None:
                    self.app.resultados_nesting[clave] = backup_grp

                def on_err(msg=str(e)):
                    if hasattr(self.app, "cerrar_ventana_carga"):
                        self.app.cerrar_ventana_carga()
                    self.procesar_lista_hojas(self.app.resultados_nesting)
                    QMessageBox.critical(self, 
                        "Renesteo",
                        f"No se pudo renestear el calibre completo.\n\nDetalle:\n{msg}",
                    )

                self.app.after(0, on_err)

        threading.Thread(target=worker, daemon=True).start()

    def compensar_calibre_completo(self, clave):
        if not getattr(self.app, "resultados_nesting", None):
            return QMessageBox.warning(self, "Atención", "No hay resultados de nesting.")
        grp = self.app.resultados_nesting.get(clave)
        if not grp or "hojas" not in grp:
            return QMessageBox.warning(self, "Atención", "No se encontró ese calibre/material en el resultado.")
        offset_mm = self._offset_compensacion_mm_desde_clave(clave)
        if offset_mm is None:
            return QMessageBox.warning(self, 
                "Compensación",
                "No se pudo leer el calibre para calcular compensación plasma.",
            )
        conteo_total = self._contar_piezas_reales_grupo(clave)
        if not conteo_total:
            return QMessageBox.warning(self, 
                "Compensación",
                "No se detectaron piezas reales para ese calibre/material.",
            )
        self._renestear_clave_con_compensacion(
            clave,
            cupos_compensar_por_nombre=conteo_total,
            offset_mm=offset_mm,
            titulo="Compensando calibre completo y renesteando...",
            post_fill=False,
        )

    def _obtener_candidatas_placa(self, clave, hoja):
        try:
            cal_req, mat_req = (clave.split("_", 1) + [""])[:2]
        except Exception:
            cal_req, mat_req = "", clave

        candidatos = []
        vistos = set()
        datos_placas = self.app.plates_manager.obtener_datos_placas()
        for placa in (datos_placas or []):
            try:
                p_cal = placa[0]
                p_mat = placa[1]
                if not self.app.motor_nesting._coinciden(cal_req, p_cal):
                    continue
                if not self.app.motor_nesting._coinciden(mat_req, p_mat):
                    continue

                pid = str(placa[2])
                w_in = self.app.motor_nesting._extraer_numero(placa[3])
                h_in = self.app.motor_nesting._extraer_numero(placa[4])
                if w_in <= 0 or h_in <= 0:
                    continue

                key = (pid, round(w_in, 4), round(h_in, 4))
                if key in vistos:
                    continue
                vistos.add(key)

                precio_mxn = self.app.motor_nesting._extraer_numero(placa[6]) if len(placa) > 6 else 0.0
                lb = self.app.motor_nesting._extraer_numero(placa[5]) if len(placa) > 5 else 0.0
                usd_lb = self.app.motor_nesting._extraer_numero(placa[10]) if len(placa) > 10 else (
                    self.app.motor_nesting._extraer_numero(placa[7]) if len(placa) > 7 else 0.0
                )
                precio = precio_mxn if precio_mxn > 0 else (lb * usd_lb)
                candidatos.append(
                    {
                        "id": pid,
                        "w_mm": w_in * 25.4,
                        "h_mm": h_in * 25.4,
                        "w_in": w_in,
                        "h_in": h_in,
                        "origen": str(placa[9]).upper() if len(placa) > 9 else "EMPRESA",
                        "precio": float(precio or 0.0),
                    }
                )
            except Exception:
                continue

        candidatos.sort(key=lambda x: (x["w_mm"] * x["h_mm"], x["precio"]))
        return candidatos

    def _etiqueta_placa_inventario(self, cand) -> str:
        return (
            f"{cand['id']} | {cand['w_in']:.1f}\"×{cand['h_in']:.1f}\""
            f" | ${cand.get('precio', 0.0):,.2f} MXN"
        )

    def _obtener_candidatas_placa_por_calibre(self, clave, hoja, *, preferir_id=None):
        """Placas Herinox del mismo calibre/material (sin simular empaque)."""
        if hoja and hoja.get("es_retazo", False):
            return []
        raw = self._obtener_candidatas_placa(clave, hoja)
        cur_id = str(hoja.get("placa_id", "") or "") if hoja else ""
        cur_w = float(hoja.get("placa_w", 0) or 0) if hoja else 0.0
        cur_h = float(hoja.get("placa_h", 0) or 0) if hoja else 0.0
        out = []
        for cand in raw:
            if (
                cur_id
                and str(cand.get("id", "")) == cur_id
                and abs(float(cand["w_mm"]) - cur_w) < 0.5
                and abs(float(cand["h_mm"]) - cur_h) < 0.5
            ):
                continue
            out.append(cand)
        if preferir_id:
            pid = str(preferir_id).strip()
            out.sort(
                key=lambda c: (
                    0 if str(c.get("id", "")).strip() == pid else 1,
                    c["w_mm"] * c["h_mm"],
                    c.get("precio", 0.0),
                )
            )
        return out

    def _obtener_candidatas_placa_renest_calibre(self, clave):
        """Placas Herinox compatibles con el calibre/material (renesteo completo)."""
        return self._obtener_candidatas_placa(clave, None)

    def _filtrar_datos_placas_para_candidata(self, datos_placas, candidata):
        if not candidata or not datos_placas:
            return list(datos_placas or [])
        pid = str(candidata.get("id", "") or "").strip()
        w_in = float(candidata.get("w_in", 0) or 0)
        h_in = float(candidata.get("h_in", 0) or 0)
        if not pid or w_in <= 0 or h_in <= 0:
            return list(datos_placas or [])
        filtradas = []
        for placa in datos_placas:
            try:
                if str(placa[2]).strip() != pid:
                    continue
                pw = self.app.motor_nesting._extraer_numero(placa[3])
                ph = self.app.motor_nesting._extraer_numero(placa[4])
                if abs(pw - w_in) > 0.05 or abs(ph - h_in) > 0.05:
                    continue
                filtradas.append(placa)
            except Exception:
                continue
        return filtradas

    def _rellenar_submenu_renest_calibre(self, sub_menu, clave):
        sub_menu.clear()
        if self._es_grupo_cobre(clave):
            sub_menu.addAction(
                "COBRE (automático)",
                self._safe_ctx(
                    "Renestear calibre cobre",
                    lambda c=clave: self.renestear_calibre_completo_ui(c),
                ),
            )
            return

        candidatas = self._obtener_candidatas_placa_renest_calibre(clave)
        if not candidatas:
            na = sub_menu.addAction("Sin placas compatibles en Herinox")
            na.setEnabled(False)
            return

        sub_menu.addAction(
            "AUTOMÁTICO (mejor placa)",
            self._safe_ctx(
                "Renestear calibre",
                lambda c=clave: self.renestear_calibre_completo_ui(c),
            ),
        )
        sub_menu.addSeparator()
        for cand in candidatas[:30]:
            sub_menu.addAction(
                self._etiqueta_placa_inventario(cand),
                self._safe_ctx(
                    "Renestear calibre",
                    lambda c=clave, p=cand: self.renestear_calibre_completo_ui(
                        c, candidata_placa=p
                    ),
                ),
            )

    def _mostrar_dialogo_placa_renest_calibre(self, clave):
        """
        Diálogo para elegir placa al renestear calibre completo.
        Retorna False si cancela, None para automático, o dict candidata.
        """
        candidatas = self._obtener_candidatas_placa_renest_calibre(clave)
        if not candidatas:
            QMessageBox.information(
                self,
                "Renestear calibre completo",
                "No hay placas de este calibre/material en el inventario Herinox (stock DISPONIBLE).",
            )
            return False

        dlg = QDialog(self)
        dlg.setWindowTitle("Renestear calibre completo")
        dlg.setModal(True)
        dlg.resize(560, 500)
        dlg.setStyleSheet("background:#F8FAFC;")
        lay = QVBoxLayout(dlg)

        lbl = QLabel(
            f"Seleccione con qué placa desea renestear el calibre {clave}.\n"
            "AUTOMÁTICO deja que el motor elija la mejor opción."
        )
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#475569;font-size:11px;")
        lay.addWidget(lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(6)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        seleccion = {"cand": None}
        from interface.qt.theme import apply_push_button

        def _pick(cand_item):
            seleccion["cand"] = cand_item
            dlg.accept()

        btn_auto = QPushButton("AUTOMÁTICO (mejor placa)")
        apply_push_button(btn_auto, COLOR_GRIS_DARK, font_size=10, padding="8px 10px")
        btn_auto.clicked.connect(lambda: _pick(None))
        inner_lay.addWidget(btn_auto)

        for cand in candidatas[:40]:
            b = QPushButton(self._etiqueta_placa_inventario(cand))
            apply_push_button(b, COLOR_GRIS_DARK, font_size=10, padding="8px 10px")
            b.clicked.connect(lambda _checked=False, cand_item=cand: _pick(cand_item))
            inner_lay.addWidget(b)
        inner_lay.addStretch()

        btn_cerrar = QPushButton("CANCELAR")
        apply_push_button(btn_cerrar, "#FFFFFF", font_size=11)
        btn_cerrar.clicked.connect(dlg.reject)
        lay.addWidget(btn_cerrar)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        return seleccion["cand"]

    def _mostrar_dialogo_placa_inventario(
        self,
        clave,
        hoja,
        *,
        titulo="CAMBIAR DE PLACA",
        mensaje="",
        preferir_id=None,
        excluir_candidata=None,
    ):
        candidatas = self._obtener_candidatas_placa_por_calibre(
            clave, hoja, preferir_id=preferir_id
        )
        if excluir_candidata and isinstance(excluir_candidata, dict):
            ex_id = str(excluir_candidata.get("id", "") or "")
            ex_w = float(excluir_candidata.get("w_mm", 0) or 0)
            ex_h = float(excluir_candidata.get("h_mm", 0) or 0)
            candidatas = [
                c
                for c in candidatas
                if not (
                    str(c.get("id", "")) == ex_id
                    and abs(float(c["w_mm"]) - ex_w) < 0.5
                    and abs(float(c["h_mm"]) - ex_h) < 0.5
                )
            ]
        if not candidatas:
            QMessageBox.information(
                self,
                titulo,
                "No hay placas de este calibre/material en el inventario Herinox (stock DISPONIBLE).",
            )
            return None

        dlg = QDialog(self)
        dlg.setWindowTitle(titulo)
        dlg.setModal(True)
        dlg.resize(520, 460)
        dlg.setStyleSheet("background:#F8FAFC;")
        lay = QVBoxLayout(dlg)
        if mensaje:
            lbl = QLabel(mensaje)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color:#475569;font-size:11px;")
            lay.addWidget(lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(6)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        seleccion = {"cand": None}
        from interface.qt.theme import apply_push_button

        for cand in candidatas[:40]:
            b = QPushButton(self._etiqueta_placa_inventario(cand))
            apply_push_button(b, COLOR_GRIS_DARK, font_size=10, padding="8px 10px")

            def _pick(*_args, cand_item=cand, **_kwargs):
                seleccion["cand"] = cand_item
                dlg.accept()

            b.clicked.connect(_pick)
            inner_lay.addWidget(b)
        inner_lay.addStretch()

        btn_cerrar = QPushButton("CANCELAR")
        apply_push_button(btn_cerrar, "#FFFFFF", font_size=11)
        btn_cerrar.clicked.connect(dlg.reject)
        lay.addWidget(btn_cerrar)
        dlg.exec()
        return seleccion["cand"]

    def _piezas_pack_madre_para_empaque(self, clave, hoja):
        """Solo piezas de la placa madre (no mini-nests), listas para el motor de empaque."""
        if not hoja or hoja.get("es_retazo", False):
            return []

        bloque = self._desglosar_bloque_placa_mini(clave, hoja)
        resumen = bloque.get("resumen_base") or {}
        if not resumen:
            return []

        material_hoja = clave.split("_")[1] if "_" in clave else clave
        calibre_hoja = clave.split("_")[0] if "_" in clave else ""

        piezas_fuente = {}
        for p_nom, mat, qty, cal, st, ruta in getattr(self.app, "datos_partes_actuales", []) or []:
            if not self.app.motor_nesting._coinciden(calibre_hoja, cal):
                continue
            if not self.app.motor_nesting._coinciden(material_hoja, mat):
                continue
            if p_nom in piezas_fuente:
                continue
            poly, marks = self.app.motor_nesting.recuperar_geometria_robusta(ruta)
            if not poly:
                continue
            from shapely import affinity

            mx, my, _, _ = poly.bounds
            piezas_fuente[p_nom] = {
                "nombre": p_nom,
                "poly": affinity.translate(poly, -mx, -my),
                "marks": affinity.translate(marks, -mx, -my) if not marks.is_empty else marks,
                "area": poly.area,
                "calibre": cal,
                "material": mat,
                "ruta": ruta,
            }

        out = []
        for nom, cnt in resumen.items():
            src = piezas_fuente.get(nom)
            if not src:
                continue
            for _ in range(int(cnt)):
                out.append(copy.deepcopy(src))
        return out

    def _piezas_sin_espacio_en_placa(self, piezas, w_mm, h_mm, k, m) -> list[dict]:
        """Piezas cuyo bbox no cabe en la placa (ninguna orientación), con kerf/margen."""
        clearance = float(k or DEFAULT_KERF_IN) * 25.4 + float(m or 0.0)
        margin = clearance * 2.0
        rechazadas: list[dict] = []
        for p in piezas or []:
            poly = p.get("poly")
            nom = str(p.get("nombre", "?") or "?")
            if poly is None or getattr(poly, "is_empty", True):
                rechazadas.append({"nombre": nom, "motivo": "sin geometría DXF"})
                continue
            b = poly.bounds
            pw, ph = float(b[2] - b[0]), float(b[3] - b[1])
            cabe = False
            for ww, hh in ((w_mm, h_mm), (h_mm, w_mm)):
                usable_w = float(ww) - margin
                usable_h = float(hh) - margin
                if usable_w <= 0 or usable_h <= 0:
                    continue
                if (pw + clearance <= usable_w and ph + clearance <= usable_h) or (
                    ph + clearance <= usable_w and pw + clearance <= usable_h
                ):
                    cabe = True
                    break
            if not cabe:
                rechazadas.append(
                    {
                        "nombre": nom,
                        "w_in": pw / 25.4,
                        "h_in": ph / 25.4,
                    }
                )
        return rechazadas

    def _texto_piezas_sin_espacio_placa(self, candidata, rechazadas, *, pendientes=False) -> str:
        placa_txt = (
            f"{candidata.get('id', '?')} "
            f"({float(candidata.get('w_in', 0) or 0):.1f}\"×"
            f"{float(candidata.get('h_in', 0) or 0):.1f}\")"
        )
        ctx = "pendientes" if pendientes else "de esta placa"
        lineas = []
        for r in (rechazadas or [])[:10]:
            if r.get("w_in") is not None:
                lineas.append(
                    f"  · {r['nombre']}: {float(r['w_in']):.1f}\"×{float(r['h_in']):.1f}\""
                )
            else:
                lineas.append(f"  · {r['nombre']}: {r.get('motivo', 'sin datos')}")
        extra = ""
        if len(rechazadas or []) > 10:
            extra = f"\n… y {len(rechazadas) - 10} más."
        return (
            f"La placa {placa_txt} no puede usarse: {len(rechazadas or [])} pieza(s) {ctx} "
            f"exceden sus dimensiones con el kerf/margen actual.\n\n"
            f"Piezas que no caben por tamaño:\n"
            + ("\n".join(lineas) if lineas else "  · (sin detalle)")
            + extra
            + "\n\nSeleccione otra placa del inventario o pulse CANCELAR para abortar."
        )

    def _cancelar_sesion_cambio_placa_ui(self, clave, mensaje: str) -> None:
        self._restaurar_sesion_cambio_placa(clave)
        QMessageBox.information(self, "Cambiar de placa", mensaje)
        self.procesar_lista_hojas(self.app.resultados_nesting)

    def _resolver_candidata_dimensiones_cambio_placa(
        self,
        clave,
        hoja,
        candidata,
        piezas,
        *,
        nueva_hoja: bool,
        k,
        m,
    ):
        """
        Valida que al menos una pieza quepa por dimensiones en la placa.
        Si ninguna cabe, avisa y pide otra placa o cancelar (sin restaurar hasta cancelar).
        """
        actual = candidata
        while actual:
            rechazadas = self._piezas_sin_espacio_en_placa(
                piezas, float(actual["w_mm"]), float(actual["h_mm"]), k, m
            )
            if len(rechazadas) < len(piezas or []):
                return actual

            msg = self._texto_piezas_sin_espacio_placa(
                actual, rechazadas, pendientes=nueva_hoja
            )
            QMessageBox.warning(self, "Placa no compatible por dimensiones", msg)
            otra = self._mostrar_dialogo_placa_inventario(
                clave,
                hoja,
                titulo="SELECCIONE OTRA PLACA",
                mensaje=msg,
                excluir_candidata=actual,
            )
            if not otra:
                self._cancelar_sesion_cambio_placa_ui(
                    clave,
                    "Operación cancelada. Se restauró el nesteo anterior.",
                )
                return None
            actual = otra
        return None

    def _candidata_cabe_piezas_empaque(self, piezas, w_mm, h_mm, k, m, opt, corner):
        """True si todas las piezas caben en la placa (prueba orientación normal y rotada 90°)."""
        if not piezas:
            return True
        pool = copy.deepcopy(piezas)
        orientaciones = [(w_mm, h_mm)]
        if abs(w_mm - h_mm) > 0.5:
            orientaciones.append((h_mm, w_mm))
        for ww, hh in orientaciones:
            nh, sobras = self.app.motor_nesting.empaquetar_una_hoja_mc(
                copy.deepcopy(pool), ww, hh, k, m, opt, corner
            )
            if not sobras:
                return True
        return False

    def _candidata_cabe_piezas_rapido(self, piezas, w_mm, h_mm, k, m):
        """
        Filtro instantáneo para el submenú: bbox + área total.
        La validación exacta (empaque MC) se reserva para cambiar_placa_y_renestear.
        """
        if not piezas:
            return True
        clearance = float(k or 0.2) * 25.4 + float(m or 0.0)
        margin = clearance * 2.0
        for ww, hh in ((w_mm, h_mm), (h_mm, w_mm)):
            usable_w = ww - margin
            usable_h = hh - margin
            if usable_w <= 0 or usable_h <= 0:
                continue
            total_area = 0.0
            ok = True
            for p in piezas:
                poly = p.get("poly")
                if poly is None or getattr(poly, "is_empty", True):
                    ok = False
                    break
                b = poly.bounds
                pw, ph = b[2] - b[0], b[3] - b[1]
                fits = (
                    pw + clearance <= usable_w and ph + clearance <= usable_h
                ) or (
                    ph + clearance <= usable_w and pw + clearance <= usable_h
                )
                if not fits:
                    ok = False
                    break
                total_area += float(poly.area)
            if ok and total_area <= usable_w * usable_h * 0.98:
                return True
        return False

    def _obtener_candidatas_placa_validas(self, clave, hoja, *, rapido=False):
        """
        Candidatas del inventario donde caben todas las piezas de la placa madre actual
        (mismo kerf/margen/opt/corner que la UI).
        """
        if hoja.get("es_retazo", False):
            return []

        raw = self._obtener_candidatas_placa(clave, hoja)
        piezas = self._piezas_pack_madre_para_empaque(clave, hoja)

        # Nota: estos valores son inputs de UI; mantener la función preparada
        # para que su cálculo pesado pueda moverse a thread sin tocar widgets.
        k = getattr(self, "_cached_k_for_submenu", None)
        m = getattr(self, "_cached_m_for_submenu", None)
        opt = getattr(self, "_cached_opt_for_submenu", None)
        corner = getattr(self, "_cached_corner_for_submenu", None)
        if k is None:
            try:
                k = self._kerf_efectivo()
            except Exception:
                k = DEFAULT_KERF_IN
        if m is None:
            m = self.global_margin_val
        if opt is None:
            opt = self.cmb_opt.currentText() if hasattr(self, "cmb_opt") else "OPTIMIZAR LARGO Y ANCHO"
        if corner is None:
            corner = self.global_corner_val

        cur_w = float(hoja.get("placa_w", 0) or 0)
        cur_h = float(hoja.get("placa_h", 0) or 0)
        cur_id = str(hoja.get("placa_id", "") or "")

        if not piezas:
            # Sin geometría DXF no podemos validar: no ofrecer cambios que puedan fallar.
            return []

        validas = []
        for cand in raw:
            if str(cand.get("id", "")) == cur_id and abs(cand["w_mm"] - cur_w) < 0.5 and abs(cand["h_mm"] - cur_h) < 0.5:
                continue
            if rapido:
                cabe = self._candidata_cabe_piezas_rapido(
                    piezas, cand["w_mm"], cand["h_mm"], k, m
                )
            else:
                cabe = self._candidata_cabe_piezas_empaque(
                    piezas, cand["w_mm"], cand["h_mm"], k, m, opt, corner
                )
            if cabe:
                validas.append(cand)
        return validas

    def _rellenar_submenu_cambiar_placa(self, sub_menu, clave, hoja):
        """Lista sincrónica de placas Herinox del mismo calibre (sin hilo en background)."""
        sub_menu.clear()
        if hoja.get("es_retazo", False):
            na = sub_menu.addAction("No aplica a RTZ / retazo")
            na.setEnabled(False)
            return

        candidatas = self._obtener_candidatas_placa_por_calibre(clave, hoja)
        if not candidatas:
            na = sub_menu.addAction("Sin placas de este calibre en Herinox")
            na.setEnabled(False)
            return

        for cand in candidatas[:30]:
            sub_menu.addAction(
                self._etiqueta_placa_inventario(cand),
                self._safe_ctx(
                    "Cambiar de placa",
                    lambda c=clave, h=hoja, p=cand: self.cambiar_placa_y_renestear(c, h, p),
                ),
            )

    def _replicar_lote_activo_a_gemelos(self):
        resultados_ml = getattr(self.app, "resultados_multilote", None)
        if not isinstance(resultados_ml, list) or not resultados_ml:
            return

        idx = int(getattr(self, "lote_actual_idx", 0) or 0)
        if idx < 0 or idx >= len(resultados_ml):
            return

        if resultados_ml[idx].get("gemelo_desync"):
            return
        if self._data_tiene_transferencias_cross_wo(self.app.resultados_nesting):
            return

        lote_k_ref = resultados_ml[idx].get("lote_k")
        data_ref = copy.deepcopy(self.app.resultados_nesting)
        for j, orden in enumerate(resultados_ml):
            if j == idx:
                continue
            if orden.get("lote_k") != lote_k_ref:
                continue
            if orden.get("gemelo_desync"):
                continue
            if self._data_tiene_transferencias_cross_wo(orden.get("data")):
                continue
            self.app.resultados_multilote[j]["data"] = copy.deepcopy(data_ref)
            if (
                hasattr(self.app, "editable_inputs_by_lote")
                and isinstance(self.app.editable_inputs_by_lote, list)
                and idx < len(self.app.editable_inputs_by_lote)
            ):
                while len(self.app.editable_inputs_by_lote) <= j:
                    self.app.editable_inputs_by_lote.append([])
                self.app.editable_inputs_by_lote[j] = self._clonar_datos_partes_edicion(
                    self.app.editable_inputs_by_lote[idx]
                )

    def _toggle_ignorar_deduccion_cu_grupo(self, clave, info, ignorar: bool):
        if not isinstance(info, dict):
            return
        info["ignorar_deduccion_cu"] = bool(ignorar)
        for hoja in info.get("hojas", []) or []:
            if not hoja.get("es_retazo"):
                hoja["ignorar_deduccion"] = bool(ignorar)
                self._sincronizar_sobrante_rtz_placa(clave, hoja, ignorar)
        self._recalcular_costos_grupo(clave)
        self._replicar_lote_activo_a_gemelos()
        QTimer.singleShot(
            240,
            lambda: self.procesar_lista_hojas(self.app.resultados_nesting),
        )

    def _crear_switch_ignorar_cu_grupo(self, parent, clave, info):
        from interface.qt.widgets.herinox_switch import HerinoxSwitch

        ignorada = bool(info.get("ignorar_deduccion_cu", True))

        fila_ign = QFrame(parent)
        fila_ign.setStyleSheet("background:#111827;border:1px solid #374151;border-radius:8px;")
        ign_lay = QHBoxLayout(fila_ign)
        ign_lay.setContentsMargins(10, 6, 10, 6)

        lbl = QLabel("COBRE — IGNORAR DEDUCCIÓN INVENTARIO")
        lbl.setStyleSheet("color:#9CA3AF;font-size:12px;font-weight:700;")
        ign_lay.addWidget(lbl)
        ign_lay.addStretch()

        sw = HerinoxSwitch(
            label_on="ON · Sobrante",
            label_off="OFF · Comprar",
            checked=ignorada,
        )
        sw.toggled.connect(
            lambda checked, c=clave, g=info: self._toggle_ignorar_deduccion_cu_grupo(c, g, checked)
        )
        ign_lay.addWidget(sw)
        scroll_add_widget(parent, fila_ign)

    def _toggle_ignorar_deduccion_placa(self, clave, hoja, ignorar: bool):
        if not isinstance(hoja, dict) or hoja.get("es_retazo"):
            return
        hoja["ignorar_deduccion"] = bool(ignorar)
        self._sincronizar_sobrante_rtz_placa(clave, hoja, ignorar)
        self._recalcular_costos_grupo(clave)
        self._replicar_lote_activo_a_gemelos()
        # Esperar a que termine la animación del switch antes de reconstruir la lista.
        QTimer.singleShot(
            240,
            lambda: self.procesar_lista_hojas(self.app.resultados_nesting),
        )

    def _crear_switch_ignorar_placa(self, parent, clave, hoja, hojas_grupo):
        from interface.qt.widgets.herinox_switch import HerinoxSwitch

        ignorada = bool(hoja.get("ignorar_deduccion", False))
        efi_real = eficiencia_para_umbral_ignorar(hoja, hojas_grupo)

        fila_ign = QFrame(parent)
        fila_ign.setStyleSheet("background:#F8FAFC;border:none;border-radius:8px;")
        ign_lay = QHBoxLayout(fila_ign)
        ign_lay.setContentsMargins(10, 6, 10, 6)

        lbl = QLabel(f"IGNORAR DEDUCCIÓN  |  REAL {efi_real:.1f}%")
        lbl.setStyleSheet("color:#64748B;font-size:12px;")
        ign_lay.addWidget(lbl)
        ign_lay.addStretch()

        sw = HerinoxSwitch(
            label_on="ON · Sobrante",
            label_off="OFF · Comprar",
            checked=ignorada,
        )
        sw.toggled.connect(lambda checked, c=clave, h=hoja: self._toggle_ignorar_deduccion_placa(c, h, checked))
        ign_lay.addWidget(sw)

        parent.layout().addWidget(fila_ign)

    def _recalcular_costos_grupo(self, clave):
        from interface.nesting_costos import recalcular_costos_grupo

        grp = self.app.resultados_nesting.get(clave)
        recalcular_costos_grupo(grp)

    def _llenar_placa_desde_otras_hojas(self, clave, hoja_objetivo):
        grupo = self.app.resultados_nesting.get(clave, {})
        hojas = grupo.get("hojas", [])
        if not hojas:
            return

        cambios = True
        while cambios:
            cambios = False
            for hoja_origen in list(hojas):
                if hoja_origen is hoja_objetivo:
                    continue
                resultado = self.app.motor_nesting.transferir_piezas_a_placa(
                    self.app.resultados_nesting,
                    hoja_origen,
                    hoja_objetivo,
                )
                if int((resultado or {}).get("movidas", 0) or 0) > 0:
                    cambios = True
                    break

    def _es_pieza_virtual(self, nombre):
        n = str(nombre or "")
        return (
            n.startswith("REMANENTE__")
            or n.startswith("REF__")
            or n.startswith("TATUAJE__")
            or n.startswith("RETAZO_GUILLOTINA__")
            or n.startswith("CU_CORTE__")
            or n.startswith("RETAZO_")
        )

    def _placa_equivalente_en_esencia(self, hoja_a, hoja_b, tol_mm=1.5):
        """
        Define si dos placas son esencialmente iguales:
        mismas piezas reales (conteo por nombre) y mismo marco ocupado aprox.
        """
        def _firma(h):
            piezas = [p for p in (h.get("piezas") or []) if not self._es_pieza_virtual(p.get("nombre", ""))]
            conteo = {}
            minx = miny = float("inf")
            maxx = maxy = float("-inf")
            area_sum = 0.0
            for p in piezas:
                nom = str(p.get("nombre", ""))
                conteo[nom] = conteo.get(nom, 0) + 1
                pols = p.get("poligonos") or []
                if not pols:
                    continue
                pts = pols[0]
                if not pts:
                    continue
                xs = [pt[0] for pt in pts]
                ys = [pt[1] for pt in pts]
                minx = min(minx, min(xs))
                miny = min(miny, min(ys))
                maxx = max(maxx, max(xs))
                maxy = max(maxy, max(ys))
                try:
                    area_sum += float(p.get("area", 0.0) or 0.0)
                except Exception:
                    pass
            if minx == float("inf"):
                minx = miny = maxx = maxy = 0.0
            return conteo, (minx, miny, maxx, maxy), area_sum

        try:
            c1, b1, a1 = _firma(hoja_a or {})
            c2, b2, a2 = _firma(hoja_b or {})
            if c1 != c2:
                return False
            for v1, v2 in zip(b1, b2):
                if abs(float(v1) - float(v2)) > float(tol_mm):
                    return False
            if abs(float(a1) - float(a2)) > 1.0:
                return False
            return True
        except Exception:
            return False

    def _desglosar_bloque_placa_mini(self, clave, hoja):
        """
        Devuelve metadata del bloque:
        - idx_base
        - idx_retazos asociados consecutivos
        - resumen_base (solo placa principal)
        - resumen_retazos: lista de dicts {idx, hoja, resumen}
        """
        grupo = self.app.resultados_nesting.get(clave, {})
        hojas = grupo.get("hojas", []) if isinstance(grupo, dict) else []
        if not hojas:
            return {"idx_base": -1, "idx_retazos": [], "resumen_base": {}, "resumen_retazos": []}

        idx_base = -1
        # 1) Coincidencia por identidad del objeto (preferente para "por placa").
        for i, h in enumerate(hojas):
            if h is hoja:
                idx_base = i
                break
        # 2) Fallback por placa_id + es_retazo + dimensiones (solo si es único).
        if idx_base < 0:
            candidatos = []
            pid_ref = str(hoja.get("placa_id", "") or "")
            es_ref = bool(hoja.get("es_retazo", False))
            w_ref = float(hoja.get("placa_w", 0) or 0)
            h_ref = float(hoja.get("placa_h", 0) or 0)
            for i, h in enumerate(hojas):
                if str(h.get("placa_id", "") or "") != pid_ref:
                    continue
                if bool(h.get("es_retazo", False)) != es_ref:
                    continue
                if abs(float(h.get("placa_w", 0) or 0) - w_ref) > 0.5:
                    continue
                if abs(float(h.get("placa_h", 0) or 0) - h_ref) > 0.5:
                    continue
                candidatos.append(i)
            if len(candidatos) == 1:
                idx_base = candidatos[0]
        if idx_base < 0:
            return {"idx_base": -1, "idx_retazos": [], "resumen_base": {}, "resumen_retazos": []}

        idx_retazos = []
        resumen_base = {}
        resumen_retazos = []
        hoja_base = hojas[idx_base]

        for p in (hoja_base.get("piezas") or []):
            nom = str(p.get("nombre", ""))
            if self._es_pieza_virtual(nom):
                continue
            resumen_base[nom] = resumen_base.get(nom, 0) + 1

        if not hoja_base.get("es_retazo", False):
            j = idx_base + 1
            while j < len(hojas) and hojas[j].get("es_retazo", False):
                idx_retazos.append(j)
                resumen_r = {}
                for p in (hojas[j].get("piezas") or []):
                    nom = str(p.get("nombre", ""))
                    if self._es_pieza_virtual(nom):
                        continue
                    resumen_r[nom] = resumen_r.get(nom, 0) + 1
                resumen_retazos.append({"idx": j, "hoja": hojas[j], "resumen": resumen_r})
                j += 1

        return {
            "idx_base": idx_base,
            "idx_retazos": idx_retazos,
            "resumen_base": resumen_base,
            "resumen_retazos": resumen_retazos,
        }

    def _resumen_bloque_placa_y_rtz(self, bloque, absorber_rtz: bool = False) -> dict:
        resumen = dict(bloque.get("resumen_base") or {})
        if absorber_rtz:
            for item in bloque.get("resumen_retazos") or []:
                for nom, cnt in (item.get("resumen") or {}).items():
                    resumen[nom] = resumen.get(nom, 0) + int(cnt)
        return resumen

    def _resumen_piezas_reales_hoja(self, hoja):
        resumen = {}
        for p in (hoja.get("piezas") or []):
            nom = self._nombre_canonico_pieza(p.get("nombre", ""))
            if self._es_pieza_virtual(nom):
                continue
            resumen[nom] = resumen.get(nom, 0) + 1
        return resumen

    def _firma_resumen_piezas_hoja(self, hoja):
        return tuple(sorted((self._resumen_piezas_reales_hoja(hoja) or {}).items()))

    def _inventario_piezas_grupo(self, clave, resultados=None):
        datos = resultados if resultados is not None else getattr(self.app, "resultados_nesting", {})
        grp = (datos or {}).get(clave, {})
        inventario = {}
        for hoja in (grp.get("hojas") or []):
            for nom, cnt in self._resumen_piezas_reales_hoja(hoja).items():
                inventario[nom] = inventario.get(nom, 0) + int(cnt)
        return inventario

    def _inventarios_equivalentes(self, inv_a, inv_b):
        norm = lambda d: {str(k): int(v) for k, v in (d or {}).items()}
        return norm(inv_a) == norm(inv_b)

    def _texto_diff_inventario(self, inv_antes, inv_despues):
        antes = {str(k): int(v) for k, v in (inv_antes or {}).items()}
        despues = {str(k): int(v) for k, v in (inv_despues or {}).items()}
        nombres = sorted(set(antes) | set(despues))
        lineas = []
        for nom in nombres:
            a = int(antes.get(nom, 0))
            d = int(despues.get(nom, 0))
            if a != d:
                lineas.append(f"  · {nom}: {a} → {d}")
        if not lineas:
            return ""
        return "Cambios detectados:\n" + "\n".join(lineas[:12])

    def _hoja_cumple_resumen_esperado(self, hoja, resumen_esperado):
        if not resumen_esperado:
            return True
        actual = self._resumen_piezas_reales_hoja(hoja)
        esperado = {str(k): int(v) for k, v in resumen_esperado.items()}
        return actual == esperado

    def _snapshot_grupo_nesting(self, clave):
        grp = (getattr(self.app, "resultados_nesting", None) or {}).get(clave)
        if not isinstance(grp, dict):
            return None
        return copy.deepcopy(grp)

    def _restaurar_grupo_nesting(self, clave, snapshot):
        if snapshot is None:
            return False
        if not isinstance(getattr(self.app, "resultados_nesting", None), dict):
            self.app.resultados_nesting = {}
        self.app.resultados_nesting[clave] = copy.deepcopy(snapshot)
        return True

    def _etiqueta_work_order(self, lote_idx):
        ml = getattr(self.app, "resultados_multilote", None) or []
        if len(ml) <= 1:
            return ""
        lk = ml[lote_idx].get("lote_k", "") if 0 <= int(lote_idx) < len(ml) else ""
        base = f"Work Order {int(lote_idx) + 1}"
        if lk:
            return f"{base} [ Lote X{lk} ]"
        return base

    def _data_tiene_transferencias_cross_wo(self, data):
        if not isinstance(data, dict):
            return False
        for grp in data.values():
            if not isinstance(grp, dict):
                continue
            if grp.get("transferencias_cross_wo_salida") or grp.get("transferencias_cross_wo_entrada"):
                return True
        return False

    def _desacoplar_ordenes_multilote(self, *lote_indices):
        """Clona el data completo de cada WO para romper gemelas compartidas en memoria."""
        ml = getattr(self.app, "resultados_multilote", None) or []
        vistos = set()
        for raw_idx in lote_indices:
            li = int(raw_idx)
            if li in vistos or li < 0 or li >= len(ml):
                continue
            vistos.add(li)
            data = ml[li].get("data")
            if isinstance(data, dict):
                ml[li]["data"] = copy.deepcopy(data)

    def _marcar_gemelas_desync(self, *lote_indices):
        ml = getattr(self.app, "resultados_multilote", None) or []
        for raw_idx in lote_indices:
            li = int(raw_idx)
            if 0 <= li < len(ml):
                ml[li]["gemelo_desync"] = True

    def _hoja_en_orden_multilote(self, lote_idx, clave, hoja_idx=None, hoja_ref=None):
        ml = getattr(self.app, "resultados_multilote", None) or []
        li = int(lote_idx)
        if li < 0 or li >= len(ml):
            return None
        hojas = ((ml[li].get("data") or {}).get(clave) or {}).get("hojas") or []
        if hoja_idx is not None and 0 <= int(hoja_idx) < len(hojas):
            return hojas[int(hoja_idx)]
        if isinstance(hoja_ref, dict):
            uid = str(hoja_ref.get("sheet_uid") or "").strip()
            pid = str(hoja_ref.get("placa_id", "") or "")
            for h in hojas:
                if h is hoja_ref:
                    return h
                if uid and str(h.get("sheet_uid") or "").strip() == uid:
                    return h
                if pid and str(h.get("placa_id") or "") == pid and not h.get("es_retazo"):
                    return h
        return None

    def _desacoplar_multilote_grupo(self, clave):
        """
        WO gemelas pueden compartir el mismo dict de grupo (legacy).
        Clona el grupo completo por WO antes de transferir cross-WO.
        """
        ml = getattr(self.app, "resultados_multilote", None) or []
        if len(ml) < 2:
            return
        refs_grupo: dict[int, list[int]] = {}
        for li, orden in enumerate(ml):
            grp = (orden.get("data") or {}).get(clave)
            if isinstance(grp, dict):
                refs_grupo.setdefault(id(grp), []).append(li)
        for locs in refs_grupo.values():
            if len(locs) <= 1:
                continue
            canonical = copy.deepcopy(ml[locs[0]]["data"][clave])
            for li in locs:
                ml[li]["data"][clave] = copy.deepcopy(canonical)

    def _preparar_transferencia_cross_wo(
        self, clave, lote_origen_idx, lote_dest_idx, hoja_origen, entry_destino
    ):
        self._desacoplar_ordenes_multilote(lote_origen_idx, lote_dest_idx)
        self._desacoplar_multilote_grupo(clave)
        ml = getattr(self.app, "resultados_multilote", None) or []
        li_o = int(lote_origen_idx)
        li_d = int(lote_dest_idx)
        if 0 <= li_o < len(ml):
            self.app.resultados_nesting = ml[li_o].get("data") or {}
        resultados_dest = ml[li_d].get("data") if 0 <= li_d < len(ml) else {}
        hoja_o = self._hoja_en_orden_multilote(
            li_o, clave, hoja_idx=entry_destino.get("_hoja_origen_idx"), hoja_ref=hoja_origen
        ) or hoja_origen
        hoja_d = self._hoja_en_orden_multilote(
            li_d, clave, hoja_idx=entry_destino.get("hoja_idx"), hoja_ref=entry_destino.get("hoja")
        ) or entry_destino.get("hoja")
        return hoja_o, hoja_d, resultados_dest

    def _registrar_meta_transferencia_cross_wo(
        self,
        clave,
        lote_origen_idx,
        lote_dest_idx,
        placa_origen,
        placa_destino,
        movidas,
    ):
        ml = getattr(self.app, "resultados_multilote", None) or []
        if not ml:
            return
        registro = {
            "cantidad": int(movidas or 0),
            "placa_origen": str(placa_origen or "Placa"),
            "placa_destino": str(placa_destino or "Placa"),
            "wo_origen": self._etiqueta_work_order(lote_origen_idx),
            "wo_destino": self._etiqueta_work_order(lote_dest_idx),
        }
        li_o = int(lote_origen_idx)
        li_d = int(lote_dest_idx)
        if 0 <= li_o < len(ml):
            grp_o = (ml[li_o].get("data") or {}).setdefault(clave, {})
            if isinstance(grp_o, dict):
                grp_o.setdefault("transferencias_cross_wo_salida", []).append(
                    {
                        "cantidad": registro["cantidad"],
                        "wo": registro["wo_destino"],
                        "placa": registro["placa_destino"],
                    }
                )
        if 0 <= li_d < len(ml):
            grp_d = (ml[li_d].get("data") or {}).setdefault(clave, {})
            if isinstance(grp_d, dict):
                grp_d.setdefault("transferencias_cross_wo_entrada", []).append(
                    {
                        "cantidad": registro["cantidad"],
                        "wo": registro["wo_origen"],
                        "placa": registro["placa_origen"],
                    }
                )

    def _texto_aviso_material_grupo(self, info):
        if not isinstance(info, dict):
            return None, False
        salidas = info.get("transferencias_cross_wo_salida") or []
        if salidas:
            partes = [
                f"{t.get('cantidad', '?')} pieza(s) → {t.get('wo', 'otra WO')} · {t.get('placa', 'placa')}"
                for t in salidas
            ]
            return (
                "Piezas movidas a otra Work Order: " + "; ".join(partes),
                True,
            )
        entradas = info.get("transferencias_cross_wo_entrada") or []
        adv = str(info.get("advertencia") or "").strip()
        if entradas and adv.startswith("Inventario incompleto"):
            partes = [
                f"{t.get('cantidad', '?')} pieza(s) desde {t.get('wo', 'otra WO')} · {t.get('placa', 'placa')}"
                for t in entradas
            ]
            return (
                "Incluye piezas recibidas de otra Work Order: " + "; ".join(partes),
                True,
            )
        if adv:
            return adv, False
        if info.get("error"):
            return str(info.get("error")), False
        return None, False

    def _texto_resumen_transferencia(
        self,
        *,
        movidas,
        restantes=0,
        placa_origen,
        placa_destino,
        lote_origen_idx,
        lote_dest_idx,
        parcial=False,
    ):
        wo_orig = self._etiqueta_work_order(lote_origen_idx)
        wo_dest = self._etiqueta_work_order(lote_dest_idx)
        cross_wo = int(lote_origen_idx) != int(lote_dest_idx)
        p_orig = str(placa_origen or "Placa")
        p_dest = str(placa_destino or "Placa")

        if cross_wo:
            lineas = [
                f"Se movieron {movidas} pieza(s).",
                f"Origen:  {wo_orig} · {p_orig}",
                f"Destino: {wo_dest} · {p_dest}",
            ]
            if restantes > 0:
                lineas.append(
                    f"Quedan {restantes} pieza(s) en {wo_orig} · {p_orig}."
                )
            elif parcial:
                lineas.append("No cupieron más piezas con la configuración actual.")
            else:
                lineas.append(
                    f"Las piezas ya están anidadas en {wo_dest} · {p_dest}."
                )
            lineas.append(
                "\nNota: las Work Orders gemelas ya no se sincronizan automáticamente "
                "tras un movimiento entre órdenes."
            )
            return "\n".join(lineas)

        if restantes > 0 or parcial:
            return (
                f"Se movieron {movidas} pieza(s) de {p_orig} a {p_dest}.\n"
                f"Quedan {restantes} pieza(s) en {p_orig}."
            )
        return (
            f"Se movieron {movidas} pieza(s) de {p_orig} a {p_dest}.\n"
            f"Destino: {p_dest}"
        )

    def _destinos_transferencia_placa(self, clave, hoja_origen, lote_origen_idx=None):
        ml = getattr(self.app, "resultados_multilote", None) or []
        if lote_origen_idx is None:
            lote_origen_idx = int(getattr(self, "lote_actual_idx", 0) or 0)

        lotes = [(lote_origen_idx, self.app.resultados_nesting)]
        if len(ml) > 1:
            for i, orden in enumerate(ml):
                if i == lote_origen_idx:
                    continue
                data = orden.get("data") or {}
                if clave in data:
                    lotes.append((i, data))

        entries = []
        for lote_idx, resultados in lotes:
            hojas = (resultados.get(clave) or {}).get("hojas") or []
            wo = self._etiqueta_work_order(lote_idx)
            for h_idx, h in enumerate(hojas):
                if h.get("es_retazo"):
                    continue
                if lote_idx == lote_origen_idx and h is hoja_origen:
                    continue
                entries.append(
                    {
                        "lote_idx": lote_idx,
                        "hoja": h,
                        "hoja_idx": h_idx,
                        "hojas_ctx": hojas,
                        "resultados": resultados,
                        "wo_label": wo,
                    }
                )
        return entries

    def _inventario_piezas_multilote_grupo(self, clave):
        ml = getattr(self.app, "resultados_multilote", None) or []
        if len(ml) <= 1:
            return self._inventario_piezas_grupo(clave)
        inventario = {}
        for orden in ml:
            data = orden.get("data") or {}
            if clave not in data:
                continue
            for nom, cnt in self._inventario_piezas_grupo(clave, data).items():
                inventario[nom] = inventario.get(nom, 0) + int(cnt)
        return inventario

    def _snapshot_multilote_grupos(self, clave):
        ml = getattr(self.app, "resultados_multilote", None) or []
        if len(ml) <= 1:
            snap = self._snapshot_grupo_nesting(clave)
            return {int(getattr(self, "lote_actual_idx", 0) or 0): snap} if snap else {}
        snaps = {}
        for i, orden in enumerate(ml):
            grp = (orden.get("data") or {}).get(clave)
            if isinstance(grp, dict):
                snaps[i] = copy.deepcopy(grp)
        return snaps

    def _restaurar_multilote_grupos(self, clave, snaps):
        if not snaps:
            return False
        ml = getattr(self.app, "resultados_multilote", None) or []
        if not ml:
            solo = next(iter(snaps.values()), None)
            return self._restaurar_grupo_nesting(clave, solo)
        for i, snap in snaps.items():
            idx = int(i)
            if 0 <= idx < len(ml):
                ml[idx].setdefault("data", {})[clave] = copy.deepcopy(snap)
        idx_act = int(getattr(self, "lote_actual_idx", 0) or 0)
        if 0 <= idx_act < len(ml):
            self.app.resultados_nesting = ml[idx_act].get("data") or {}
        return True

    def _activar_lote_idx(self, lote_idx):
        ml = getattr(self.app, "resultados_multilote", None) or []
        idx = int(lote_idx)
        if not ml or idx < 0 or idx >= len(ml):
            return
        old_idx = int(getattr(self, "lote_actual_idx", 0) or 0)
        self._persistir_lote_saliente(old_idx, nuevo_idx=idx)
        if self.cmb_lotes.count() > idx:
            self.cmb_lotes.setCurrentIndex(idx)
            return
        self.lote_actual_idx = idx
        self._cargar_resultados_lote_idx(idx)

    def _post_transferencia_exito(
        self,
        clave,
        lote_origen_idx,
        lote_dest_idx,
        hoja_destino,
        *,
        limpiar_seleccion=True,
        placa_origen=None,
        placa_destino=None,
        movidas=0,
    ):
        cross_wo = int(lote_origen_idx) != int(lote_dest_idx)
        if cross_wo and int(movidas or 0) > 0:
            self._registrar_meta_transferencia_cross_wo(
                clave,
                lote_origen_idx,
                lote_dest_idx,
                placa_origen or "?",
                placa_destino or (hoja_destino or {}).get("placa_id", "Placa"),
                movidas,
            )
            self._marcar_gemelas_desync(lote_origen_idx, lote_dest_idx)
        ml = getattr(self.app, "resultados_multilote", None) or []
        afectados = {int(lote_origen_idx)}
        if cross_wo:
            afectados.add(int(lote_dest_idx))

        prev_rn = self.app.resultados_nesting
        for idx in sorted(afectados):
            if ml and 0 <= idx < len(ml):
                self.app.resultados_nesting = ml[idx].get("data") or {}
            self.sincronizar_overlays_clave(clave)
            self._recalcular_costos_grupo(clave)
        self.app.resultados_nesting = prev_rn

        if not cross_wo:
            self._replicar_lote_activo_a_gemelos()

        if limpiar_seleccion:
            self.visor.limpiar_seleccion_piezas()
            self.on_piece_selected()
            self.btn_transferir.setEnabled(False)
            self.btn_rot_90.setEnabled(False)
            self.btn_rot_m1.setEnabled(False)
            self.btn_rot_p1.setEnabled(False)

        if cross_wo:
            self._activar_lote_idx(int(lote_dest_idx))

        self.procesar_lista_hojas(self.app.resultados_nesting)
        if hoja_destino is not None:
            _, h_viva = self._asegurar_indice_hoja_objetivo(clave, hoja_destino)
            if h_viva is not None:
                self.dibujar_hoja_full(h_viva, clave)
            else:
                self.dibujar_hoja_full(hoja_destino, clave)

    def _buscar_hoja_restaurada(self, clave, hoja_original=None, idx_objetivo=None):
        grp = (getattr(self.app, "resultados_nesting", None) or {}).get(clave, {})
        hojas = grp.get("hojas") or []
        if isinstance(hoja_original, dict):
            idx_vivo, h_viva = self._asegurar_indice_hoja_objetivo(clave, hoja_original)
            if idx_vivo >= 0 and h_viva is not None:
                return h_viva
        if idx_objetivo is not None and 0 <= int(idx_objetivo) < len(hojas):
            return hojas[int(idx_objetivo)]
        return hojas[0] if hojas else None

    def _abortar_y_restaurar_nesting(
        self,
        clave,
        snapshot,
        mensaje,
        hoja_original=None,
        idx_objetivo=None,
        multilote_snaps=None,
    ):
        if multilote_snaps:
            self._restaurar_multilote_grupos(clave, multilote_snaps)
            if len(multilote_snaps) <= 1:
                self._replicar_lote_activo_a_gemelos()
        elif snapshot is not None:
            self._restaurar_grupo_nesting(clave, snapshot)
            self._replicar_lote_activo_a_gemelos()
        self.procesar_lista_hojas(self.app.resultados_nesting)
        hoja_vista = self._buscar_hoja_restaurada(
            clave,
            hoja_original=hoja_original,
            idx_objetivo=idx_objetivo,
        )
        if hoja_vista is not None:
            self.dibujar_hoja_full(hoja_vista, clave)
        QMessageBox.critical(
            self,
            "Operación cancelada",
            f"{mensaje}\n\nSe restauró el nesteo anterior. Ninguna pieza fue eliminada.",
        )

    def _asegurar_indice_hoja_objetivo(self, clave, hoja):
        """Ubica de forma única la hoja dentro del grupo."""
        grp = self.app.resultados_nesting.get(clave, {})
        hojas = grp.get("hojas") or []
        if not hojas or not isinstance(hoja, dict):
            return -1, None
        for i, h in enumerate(hojas):
            if h is hoja:
                return i, h
        uid = str(hoja.get("sheet_uid") or "").strip()
        if uid:
            matches = [i for i, h in enumerate(hojas) if str(h.get("sheet_uid") or "").strip() == uid]
            if len(matches) == 1:
                return matches[0], hojas[matches[0]]
        pid_ref = str(hoja.get("placa_id", "") or "")
        es_ref = bool(hoja.get("es_retazo", False))
        w_ref = float(hoja.get("placa_w", 0) or 0)
        h_ref_dim = float(hoja.get("placa_h", 0) or 0)
        nest_idx = hoja.get("_nest_list_idx")
        if nest_idx is not None:
            ni = int(nest_idx)
            if 0 <= ni < len(hojas):
                h_cand = hojas[ni]
                if (
                    str(h_cand.get("placa_id", "") or "") == pid_ref
                    and bool(h_cand.get("es_retazo", False)) == es_ref
                    and abs(float(h_cand.get("placa_w", 0) or 0) - w_ref) <= 0.5
                    and abs(float(h_cand.get("placa_h", 0) or 0) - h_ref_dim) <= 0.5
                    and self._firma_resumen_piezas_hoja(h_cand) == self._firma_resumen_piezas_hoja(hoja)
                ):
                    return ni, h_cand
        candidatos = []
        for i, h in enumerate(hojas):
            if str(h.get("placa_id", "") or "") != pid_ref:
                continue
            if bool(h.get("es_retazo", False)) != es_ref:
                continue
            if abs(float(h.get("placa_w", 0) or 0) - w_ref) > 0.5:
                continue
            if abs(float(h.get("placa_h", 0) or 0) - h_ref_dim) > 0.5:
                continue
            candidatos.append(i)
        if len(candidatos) == 1:
            return candidatos[0], hojas[candidatos[0]]
        if candidatos:
            firma = self._firma_resumen_piezas_hoja(hoja)
            por_firma = [i for i in candidatos if self._firma_resumen_piezas_hoja(hojas[i]) == firma]
            if len(por_firma) == 1:
                return por_firma[0], hojas[por_firma[0]]
        return -1, None

    def _contar_piezas_pack(self, piezas) -> int:
        return len(piezas or [])

    def _resumen_desde_pack(self, piezas) -> dict:
        resumen = {}
        for p in piezas or []:
            nom = self._nombre_canonico_pieza(p.get("nombre", ""))
            if not nom or self._es_pieza_virtual(nom):
                continue
            resumen[nom] = resumen.get(nom, 0) + 1
        return resumen

    def _restaurar_sesion_cambio_placa(self, clave):
        ses = getattr(self, "_cambio_placa_sesion", None) or {}
        backup = ses.get("backup")
        if backup is not None and clave in (self.app.resultados_nesting or {}):
            self.app.resultados_nesting[clave] = copy.deepcopy(backup)
        self._cambio_placa_sesion = None

    def _iniciar_sesion_cambio_placa(self, clave, hoja):
        bloque = self._desglosar_bloque_placa_mini(clave, hoja)
        idx_ancla = int(bloque.get("idx_base", -1))
        resumen_objetivo = copy.deepcopy(bloque.get("resumen_base") or {})
        self._cambio_placa_gen = int(getattr(self, "_cambio_placa_gen", 0) or 0) + 1
        self._cambio_placa_sesion = {
            "clave": clave,
            "gen": self._cambio_placa_gen,
            "backup": self._snapshot_grupo_nesting(clave),
            "resumen_objetivo": resumen_objetivo,
            "piezas_objetivo": sum(int(v) for v in resumen_objetivo.values()),
            "idx_ancla": idx_ancla,
            "insert_pos": idx_ancla + 1 if idx_ancla >= 0 else -1,
            "indices_sesion": [idx_ancla] if idx_ancla >= 0 else [],
        }

    def _hidratar_hoja_desde_candidata(self, nh, candidata, hoja_ref, k, m, opt, corner):
        nh = actualizar_eficiencias_hoja(nh)
        nh.update(
            {
                "placa_id": candidata["id"],
                "placa_w": candidata["w_mm"],
                "placa_h": candidata["h_mm"],
                "precio_placa": candidata.get("precio", 0.0),
                "origen_placa": candidata.get("origen", hoja_ref.get("origen_placa", "EMPRESA")),
                "kerf_usado": k,
                "margin_usado": m,
                "opt_usado": opt,
                "corner_usado": corner,
            }
        )
        for mk in (
            "lote_desc",
            "lote_mult",
            "ignorar_deduccion",
            "modo_largos_cu",
        ):
            if mk in hoja_ref:
                nh[mk] = hoja_ref[mk]
        nh.pop("es_retazo", None)
        return nh

    def _aplicar_hoja_en_sesion_cambio_placa(self, clave, nh, *, nueva_hoja=False):
        ses = getattr(self, "_cambio_placa_sesion", None) or {}
        grp = self.app.resultados_nesting.get(clave)
        if not grp or "hojas" not in grp:
            return None
        hojas = grp["hojas"]

        if not nueva_hoja:
            idx = int(ses.get("idx_ancla", -1))
            if idx < 0 or idx >= len(hojas):
                return None
            anterior = hojas[idx]
            for mk in (
                "sheet_uid",
                "sheet_code",
                "sheet_seq",
                "sheet_display_name",
                "plate_group_key",
                "_nest_list_idx",
            ):
                if anterior.get(mk) is not None:
                    nh[mk] = anterior[mk]
            # No eliminar RTZ asociados: solo se renestea la placa madre.
            if 0 <= idx < len(hojas):
                hojas[idx] = nh
                ses["insert_pos"] = idx + 1
                ses["indices_sesion"] = [idx]
                return nh
            return None

        pos = int(ses.get("insert_pos", -1))
        if pos < 0:
            pos = len(hojas)
        hojas.insert(pos, nh)
        ses["insert_pos"] = pos + 1
        indices = list(ses.get("indices_sesion") or [])
        if pos not in indices:
            indices.append(pos)
        ses["indices_sesion"] = sorted(indices)
        return nh

    def _resumen_piezas_sesion_cambio_placa(self, clave) -> dict:
        ses = getattr(self, "_cambio_placa_sesion", None) or {}
        grp = (self.app.resultados_nesting or {}).get(clave) or {}
        hojas = grp.get("hojas") or []
        resumen = {}
        for idx in ses.get("indices_sesion") or []:
            if not (0 <= int(idx) < len(hojas)):
                continue
            for nom, cnt in self._resumen_piezas_reales_hoja(hojas[int(idx)]).items():
                resumen[nom] = resumen.get(nom, 0) + int(cnt)
        return resumen

    def _sesion_cambio_placa_completa(self, clave) -> bool:
        ses = getattr(self, "_cambio_placa_sesion", None) or {}
        objetivo = {str(k): int(v) for k, v in (ses.get("resumen_objetivo") or {}).items()}
        if not objetivo:
            return True
        colocado = self._resumen_piezas_sesion_cambio_placa(clave)
        return colocado == objetivo

    def _finalizar_sesion_cambio_placa(self, clave, hoja_mostrar):
        if not self._sesion_cambio_placa_completa(clave):
            ses = getattr(self, "_cambio_placa_sesion", None) or {}
            objetivo = ses.get("resumen_objetivo") or {}
            colocado = self._resumen_piezas_sesion_cambio_placa(clave)
            diff = self._texto_diff_inventario(objetivo, colocado)
            self._restaurar_sesion_cambio_placa(clave)
            QMessageBox.warning(
                self,
                "Cambiar de placa",
                "No se colocaron todas las piezas de la placa objetivo.\n"
                f"{diff}\n\nSe restauró el nesteo anterior.",
            )
            self.procesar_lista_hojas(self.app.resultados_nesting)
            return False

        self._cambio_placa_sesion = None
        self._recalcular_costos_grupo(clave)
        self.sincronizar_overlays_clave(clave)
        self._replicar_lote_activo_a_gemelos()
        if hoja_mostrar:
            self.dibujar_hoja_full(hoja_mostrar, clave)
        self.procesar_lista_hojas(self.app.resultados_nesting)
        return True

    def cambiar_placa_y_renestear(
        self,
        clave,
        hoja,
        candidata,
        *,
        piezas_pendientes=None,
        nueva_hoja=False,
    ):
        if not self._ctx_tiene_resultados(clave):
            return
        if not nueva_hoja and not self._ctx_hoja_valida(hoja, "Cambiar de placa"):
            return
        if not candidata or not isinstance(candidata, dict):
            return QMessageBox.warning(self, "Cambiar de placa", "La placa seleccionada no es válida.")
        if not nueva_hoja and hoja.get("es_retazo", False):
            return QMessageBox.information(
                self,
                "Cambiar de placa",
                "Las piezas en retazo/RTZ o mini-nest no se pueden reasignar a otra placa del inventario.",
            )

        piezas = piezas_pendientes
        if piezas is None:
            piezas = self._piezas_pack_madre_para_empaque(clave, hoja)
        if not piezas:
            return QMessageBox.warning(
                self,
                "Cambiar de placa",
                "No se pudieron reconstruir las piezas de esta placa para renestear.",
            )

        try:
            k = self._kerf_efectivo()
        except Exception:
            k = DEFAULT_KERF_IN
        m = self.global_margin_val

        candidata = self._resolver_candidata_dimensiones_cambio_placa(
            clave,
            hoja,
            candidata,
            piezas,
            nueva_hoja=nueva_hoja,
            k=k,
            m=m,
        )
        if not candidata:
            return

        if not nueva_hoja:
            self._iniciar_sesion_cambio_placa(clave, hoja)
        ses_gen = int((getattr(self, "_cambio_placa_sesion", None) or {}).get("gen", 0) or 0)

        if hasattr(self.app, "abrir_ventana_carga"):
            self.app.abrir_ventana_carga("Renesteando en placa seleccionada...")

        opt = self.cmb_opt.currentText() if hasattr(self, "cmb_opt") else "OPTIMIZAR LARGO Y ANCHO"
        corner = self.global_corner_val
        hoja_ref = hoja
        w_mm = float(candidata["w_mm"])
        h_mm = float(candidata["h_mm"])
        piezas_worker = copy.deepcopy(piezas)
        self._cambio_placa_ultimo_pack = copy.deepcopy(piezas)

        def worker():
            try:
                nh, sobras = self.app.motor_nesting.empaquetar_una_hoja_mc(
                    piezas_worker,
                    w_mm,
                    h_mm,
                    k,
                    m,
                    opt,
                    corner,
                )
            except Exception as exc:
                call_on_main(
                    self._on_error_cambio_placa,
                    clave,
                    ses_gen,
                    str(exc),
                )
                return

            call_on_main(
                self._on_resultado_cambio_placa,
                clave,
                hoja_ref,
                candidata,
                nh,
                copy.deepcopy(sobras or []),
                k,
                m,
                opt,
                corner,
                nueva_hoja,
                ses_gen,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _on_error_cambio_placa(self, clave, ses_gen, mensaje):
        if ses_gen != int((getattr(self, "_cambio_placa_sesion", None) or {}).get("gen", -1)):
            return
        if hasattr(self.app, "cerrar_ventana_carga"):
            self.app.cerrar_ventana_carga()
        self._restaurar_sesion_cambio_placa(clave)
        QMessageBox.critical(
            self,
            "Cambiar de placa",
            f"No se pudo renestear en la placa seleccionada.\n\n{mensaje}",
        )

    def _on_resultado_cambio_placa(
        self,
        clave,
        hoja_ref,
        candidata,
        nh,
        sobras,
        k,
        m,
        opt,
        corner,
        nueva_hoja,
        ses_gen,
    ):
        if ses_gen != int((getattr(self, "_cambio_placa_sesion", None) or {}).get("gen", -1)):
            if hasattr(self.app, "cerrar_ventana_carga"):
                self.app.cerrar_ventana_carga()
            return

        if hasattr(self.app, "cerrar_ventana_carga"):
            self.app.cerrar_ventana_carga()

        colocadas_resumen = self._resumen_piezas_reales_hoja(nh or {})
        n_colocadas = sum(int(v) for v in colocadas_resumen.values())
        if not nh or n_colocadas <= 0:
            piezas_intento = list(getattr(self, "_cambio_placa_ultimo_pack", []) or [])
            rechazadas = self._piezas_sin_espacio_en_placa(
                piezas_intento,
                float(candidata["w_mm"]),
                float(candidata["h_mm"]),
                k,
                m,
            )
            if len(rechazadas) >= len(piezas_intento or []) and piezas_intento:
                msg = self._texto_piezas_sin_espacio_placa(
                    candidata, rechazadas, pendientes=nueva_hoja
                )
                QMessageBox.warning(self, "Placa no compatible por dimensiones", msg)
                otra = self._mostrar_dialogo_placa_inventario(
                    clave,
                    hoja_ref,
                    titulo="SELECCIONE OTRA PLACA",
                    mensaje=msg,
                    excluir_candidata=candidata,
                )
                if otra:
                    self.cambiar_placa_y_renestear(
                        clave,
                        hoja_ref,
                        otra,
                        piezas_pendientes=piezas_intento,
                        nueva_hoja=nueva_hoja,
                    )
                    return
                self._cancelar_sesion_cambio_placa_ui(
                    clave,
                    "Operación cancelada. Se restauró el nesteo anterior.",
                )
                return

            QMessageBox.warning(
                self,
                "Cambiar de placa",
                f"Ninguna pieza cupo en {candidata.get('id', 'la placa seleccionada')} "
                f"({candidata.get('w_in', 0):.1f}\"×{candidata.get('h_in', 0):.1f}\") "
                "con la configuración actual (kerf/margen/optimización).\n\n"
                "Seleccione otra placa o cancele la operación.",
            )
            otra = self._mostrar_dialogo_placa_inventario(
                clave,
                hoja_ref,
                titulo="SELECCIONE OTRA PLACA",
                mensaje=(
                    "El motor no pudo colocar piezas en la placa elegida.\n"
                    "Pruebe una placa de mayor área u otra configuración de kerf/margen."
                ),
                excluir_candidata=candidata,
            )
            if otra:
                self.cambiar_placa_y_renestear(
                    clave,
                    hoja_ref,
                    otra,
                    piezas_pendientes=piezas_intento or None,
                    nueva_hoja=nueva_hoja,
                )
                return
            self._cancelar_sesion_cambio_placa_ui(
                clave,
                "Operación cancelada. Se restauró el nesteo anterior.",
            )
            return

        nh = self._hidratar_hoja_desde_candidata(nh, candidata, hoja_ref, k, m, opt, corner)
        aplicada = self._aplicar_hoja_en_sesion_cambio_placa(clave, nh, nueva_hoja=nueva_hoja)
        if aplicada is None:
            self._restaurar_sesion_cambio_placa(clave)
            QMessageBox.warning(self, "Cambiar de placa", "No se pudo actualizar la placa en el resultado.")
            self.procesar_lista_hojas(self.app.resultados_nesting)
            return

        if sobras:
            n_sobran = self._contar_piezas_pack(sobras)
            siguiente = self._mostrar_dialogo_placa_inventario(
                clave,
                hoja_ref,
                titulo="PIEZAS PENDIENTES",
                mensaje=(
                    f"En {candidata['id']} ({candidata['w_in']:.1f}\"×{candidata['h_in']:.1f}\") "
                    f"se colocaron {n_colocadas} pieza(s).\n"
                    f"Quedan {n_sobran} pieza(s) sin acomodar.\n\n"
                    "Seleccione otra placa del mismo calibre para terminar "
                    "(puede ser la misma medida u otra del inventario)."
                ),
                preferir_id=candidata.get("id"),
            )
            if siguiente:
                self.cambiar_placa_y_renestear(
                    clave,
                    hoja_ref,
                    siguiente,
                    piezas_pendientes=sobras,
                    nueva_hoja=True,
                )
                return

            self._cancelar_sesion_cambio_placa_ui(
                clave,
                "No se seleccionó placa para las piezas pendientes.\n"
                "Se restauró el nesteo anterior.",
            )
            return

        if self._finalizar_sesion_cambio_placa(clave, aplicada):
            QMessageBox.information(
                self,
                "Cambiar de placa",
                f"Placa actualizada: {candidata['id']} ({candidata['w_in']:.1f}\"×{candidata['h_in']:.1f}\").",
            )

    def _hidratar_hoja_repack(
        self,
        nh,
        hoja,
        k,
        m,
        opt,
        corner,
        *,
        clave=None,
        compensar_plasma=False,
        offset_mm_forzado=None,
    ):
        from modules.nesting_engine.efficiency_metrics import actualizar_eficiencias_hoja

        nh = actualizar_eficiencias_hoja(nh)
        nh.update(
            {
                "placa_id": hoja["placa_id"],
                "placa_w": hoja["placa_w"],
                "placa_h": hoja["placa_h"],
                "precio_placa": hoja.get("precio_placa", 0),
                "kerf_usado": k,
                "margin_usado": m,
                "opt_usado": opt,
                "corner_usado": corner,
            }
        )
        for mk in (
            "origen_placa",
            "es_retazo",
            "id_remanente_usado",
            "lote_desc",
            "lote_mult",
            "ignorar_deduccion",
            "modo_largos_cu",
        ):
            if mk in hoja:
                nh[mk] = hoja[mk]
        nh.pop("es_retazo", None)
        if compensar_plasma:
            off = float(
                offset_mm_forzado
                if offset_mm_forzado is not None
                else (self._offset_compensacion_mm_desde_clave(clave or "") or 0.0)
            )
            nh["plasma_compensado_manual"] = True
            nh["plasma_offset_mm_manual"] = off
            for pz in nh.get("piezas") or []:
                nom_pz = str(pz.get("nombre", "")).strip()
                if self._es_pieza_virtual(nom_pz):
                    continue
                pz["plasma_compensada_manual"] = True
        return nh

    def _resumen_piezas_en_hojas(self, hojas):
        resumen = {}
        for hoja in hojas or []:
            for nom, cnt in self._resumen_piezas_reales_hoja(hoja).items():
                resumen[nom] = resumen.get(nom, 0) + int(cnt)
        return resumen

    def _piezas_pack_para_resumen_compensado(
        self,
        clave,
        resumen,
        *,
        compensar_plasma=False,
        offset_mm_forzado=None,
    ):
        """Reconstruye piezas para renest/compensar usando fuente robusta (DXF + fallback nest)."""
        resumen_canon = self._inventario_piezas_canonico(resumen or {})
        if not resumen_canon:
            return []

        fuente = self._construir_fuente_geometria_por_nombre(clave)
        if not fuente:
            return []

        off = 0.0
        if compensar_plasma:
            off = float(
                offset_mm_forzado
                if offset_mm_forzado is not None
                else (self._offset_compensacion_mm_desde_clave(clave) or 0.0)
            )

        out = []
        for nom, cnt in resumen_canon.items():
            src = fuente.get(nom)
            if not src:
                continue
            for _ in range(int(cnt)):
                poly = copy.deepcopy(src["poly_base"])
                marks = copy.deepcopy(src["marks_base"])
                if compensar_plasma and off > 0:
                    from shapely import affinity

                    comp = self._aplicar_compensacion_poligono(poly, off)
                    if comp is not None and not comp.is_empty:
                        mx, my, _, _ = comp.bounds
                        poly = affinity.translate(comp, -mx, -my)
                out.append(
                    {
                        "nombre": src["nombre"],
                        "poly": poly,
                        "poly_exact": copy.deepcopy(poly),
                        "marks": copy.deepcopy(marks),
                        "area": float(getattr(poly, "area", 0) or src.get("area_base", 0)),
                        "calibre": src.get("calibre", ""),
                        "material": src.get("material", ""),
                        "ruta": src.get("ruta", ""),
                    }
                )
        return out

    def _conteo_piezas_reales_en_nest(self, hoja_nest) -> int:
        return sum(
            1
            for pz in (hoja_nest or {}).get("piezas") or []
            if not self._es_pieza_virtual(str(pz.get("nombre", "")))
        )

    def _empaquetar_en_placas_minimas(
        self,
        piezas_pack,
        hoja,
        k,
        m,
        opt,
        corner,
        *,
        intentos_por_placa: int = 24,
        debug_tag: str = "absorber_rtz",
    ):
        """Empaqueta todas las piezas en la menor cantidad de placas del mismo tamaño."""
        import random

        from modules.nesting_engine.manager import _safe_empaquetar_una_hoja_mc
        from modules.nesting_engine.nest_optimization import get_nest_profile

        if not piezas_pack:
            return []

        w = float(hoja.get("placa_w", 0) or 0)
        h_pl = float(hoja.get("placa_h", 0) or 0)
        if w <= 0 or h_pl <= 0:
            return []

        n_esperado = len(piezas_pack)
        nh_single = self.app.motor_nesting.empaquetar_con_reintentos(
            piezas_pack,
            w,
            h_pl,
            k,
            m,
            opt,
            corner,
            intentos=intentos_por_placa,
            debug_tag=f"{debug_tag}|single",
        )
        if nh_single and self._conteo_piezas_reales_en_nest(nh_single) >= n_esperado:
            return [nh_single]

        mc_iters = int(get_nest_profile().get("mc_iterations", 15))
        pendientes = list(piezas_pack)
        hojas_out = []
        n_intentos = max(1, int(intentos_por_placa or 1))

        while pendientes:
            base = sorted(
                pendientes,
                key=lambda x: float(x.get("area", 0) or 0),
                reverse=True,
            )
            mejor_nh = None
            mejor_sobras = pendientes
            mejor_colocadas = -1

            for intento in range(n_intentos):
                if intento == 0:
                    batch = base
                else:
                    batch = base.copy()
                    random.shuffle(batch)
                    batch.sort(key=lambda x: float(x.get("area", 0) or 0), reverse=True)

                nh, sobras = _safe_empaquetar_una_hoja_mc(
                    batch,
                    w,
                    h_pl,
                    k,
                    m,
                    opt,
                    corner,
                    debug_tag=f"{debug_tag}|placa={len(hojas_out)+1}|try={intento+1}",
                    mc_iterations=mc_iters,
                )
                sobras = list(sobras or [])
                colocadas = len(pendientes) - len(sobras)
                if not sobras and nh:
                    mejor_nh = nh
                    mejor_sobras = []
                    break
                if colocadas > mejor_colocadas and nh:
                    mejor_colocadas = colocadas
                    mejor_nh = nh
                    mejor_sobras = sobras

            if mejor_colocadas <= 0 or mejor_nh is None:
                return []
            hojas_out.append(mejor_nh)
            pendientes = mejor_sobras

        return hojas_out

    def _recalcular_hoja_con_contexto(
        self,
        clave,
        hoja,
        k,
        m,
        opt,
        corner,
        compensar_plasma=False,
        offset_mm_forzado=None,
        absorber_rtz=False,
    ):
        bloque = self._desglosar_bloque_placa_mini(clave, hoja)
        resumen_hoja = self._inventario_piezas_canonico(
            self._resumen_bloque_placa_y_rtz(bloque, absorber_rtz=absorber_rtz)
        )
        idx_retazos_asociados = bloque["idx_retazos"]
        if compensar_plasma and idx_retazos_asociados:
            absorber_rtz = True
            resumen_hoja = self._inventario_piezas_canonico(
                self._resumen_bloque_placa_y_rtz(bloque, absorber_rtz=True)
            )

        piezas_a_reprocesar = self._piezas_pack_para_resumen_compensado(
            clave,
            resumen_hoja,
            compensar_plasma=compensar_plasma,
            offset_mm_forzado=offset_mm_forzado,
        )
        nueva = None
        hojas_extra = []
        if piezas_a_reprocesar:
            n_esperado = sum(int(v) for v in (resumen_hoja or {}).values())
            if len(piezas_a_reprocesar) < n_esperado:
                return None, idx_retazos_asociados, []
            if absorber_rtz:
                hojas_raw = self._empaquetar_en_placas_minimas(
                    piezas_a_reprocesar,
                    hoja,
                    k,
                    m,
                    opt,
                    corner,
                    intentos_por_placa=24,
                    debug_tag="recalc_absorber_rtz",
                )
                if not hojas_raw:
                    return None, idx_retazos_asociados, []
                hojas_pack = [
                    self._hidratar_hoja_repack(
                        nh_raw,
                        hoja,
                        k,
                        m,
                        opt,
                        corner,
                        clave=clave,
                        compensar_plasma=compensar_plasma,
                        offset_mm_forzado=offset_mm_forzado,
                    )
                    for nh_raw in hojas_raw
                ]
                nueva = hojas_pack[0]
                hojas_extra = hojas_pack[1:]
            else:
                grp = (getattr(self.app, "resultados_nesting", None) or {}).get(clave) or {}
                hojas_grupo = grp.get("hojas") or []
                if idx_retazos_asociados:
                    nh, sobras = self.app.motor_nesting._empaquetar_respetando_rtz_madre(
                        piezas_a_reprocesar,
                        hoja,
                        hojas_grupo,
                        debug_tag="recalc_contexto_rtz",
                        intentos=24,
                    )
                    if nh and not sobras:
                        nueva = self._hidratar_hoja_repack(
                            nh,
                            hoja,
                            k,
                            m,
                            opt,
                            corner,
                            clave=clave,
                            compensar_plasma=compensar_plasma,
                            offset_mm_forzado=offset_mm_forzado,
                        )
                else:
                    nh = self.app.motor_nesting.empaquetar_con_reintentos(
                        piezas_a_reprocesar,
                        hoja["placa_w"],
                        hoja["placa_h"],
                        k,
                        m,
                        opt,
                        corner,
                        intentos=8,
                        debug_tag="recalc_contexto",
                    )
                    if nh:
                        nueva = self._hidratar_hoja_repack(
                            nh,
                            hoja,
                            k,
                            m,
                            opt,
                            corner,
                            clave=clave,
                            compensar_plasma=compensar_plasma,
                            offset_mm_forzado=offset_mm_forzado,
                        )
        else:
            hoja_recalc = hoja
            if compensar_plasma:
                off = float(
                    offset_mm_forzado
                    if offset_mm_forzado is not None
                    else (self._offset_compensacion_mm_desde_clave(clave) or 0.0)
                )
                hoja_recalc = self._compensar_poligonos_en_hoja(hoja, off)
            nueva = self.app.motor_nesting.recalcular_hoja_full(
                hoja_recalc, k, m, opt, corner
            )
            if nueva:
                for mk in (
                    "origen_placa",
                    "es_retazo",
                    "id_remanente_usado",
                    "lote_desc",
                    "lote_mult",
                    "ignorar_deduccion",
                    "modo_largos_cu",
                ):
                    if mk in hoja:
                        nueva[mk] = hoja[mk]
                if compensar_plasma:
                    off = float(
                        offset_mm_forzado
                        if offset_mm_forzado is not None
                        else (self._offset_compensacion_mm_desde_clave(clave) or 0.0)
                    )
                    nueva["plasma_compensado_manual"] = True
                    nueva["plasma_offset_mm_manual"] = off
                    for pz in nueva.get("piezas") or []:
                        nom_pz = str(pz.get("nombre", "")).strip()
                        if self._es_pieza_virtual(nom_pz):
                            continue
                        pz["plasma_compensada_manual"] = True
        fuente_pack = piezas_a_reprocesar
        if fuente_pack:
            from modules.nesting_engine.manager import enriquecer_piezas_hoja_con_fuentes

            for hoja_out in [hoja for hoja in [nueva, *hojas_extra] if hoja]:
                enriquecer_piezas_hoja_con_fuentes(hoja_out, fuente_pack)
        return nueva, idx_retazos_asociados, hojas_extra

    def renestear_solo_placa(
        self,
        clave,
        hoja,
        post_fill=False,
        compensar_plasma=False,
        offset_mm_forzado=None,
        absorber_rtz=False,
    ):
        if not getattr(self.app, "resultados_nesting", None):
            return QMessageBox.warning(self, "Atención", "No hay resultados de nesting.")
        if clave not in self.app.resultados_nesting:
            return QMessageBox.warning(self, "Atención", "Material no encontrado en el resultado actual.")
        if not hoja:
            return
        if hoja.get("es_retazo", False):
            return QMessageBox.information(self, 
                "Renestear placa",
                "Las placas reutilizadas (RTZ) o mini-nest no se pueden renestear desde el menú contextual.",
            )
        try:
            k = self._kerf_efectivo()
        except Exception:
            return QMessageBox.critical(self, "Error", "Valores no válidos.")
        m = self.global_margin_val

        bloque_previo = self._desglosar_bloque_placa_mini(clave, hoja)
        if compensar_plasma and bloque_previo.get("idx_retazos"):
            absorber_rtz = True
        if absorber_rtz and not bloque_previo.get("idx_retazos"):
            absorber_rtz = False
        resumen_esperado = self._inventario_piezas_canonico(
            self._resumen_bloque_placa_y_rtz(bloque_previo, absorber_rtz=absorber_rtz)
        )

        if absorber_rtz and not compensar_plasma:
            if QMessageBox.question(
                self,
                "Renestear sin RTZ",
                "Las piezas de los retazos (RTZ) asociados se moverán a la placa madre "
                "y los RTZ se eliminarán del resultado.\n\n¿Continuar?",
            ) != QMessageBox.StandardButton.Yes:
                return

        if hasattr(self.app, "abrir_ventana_carga"):
            if compensar_plasma:
                titulo_carga = (
                    "Compensando placa madre + RTZ..."
                    if absorber_rtz
                    else "Compensando placa..."
                )
            elif absorber_rtz:
                titulo_carga = "Renesteando placa (absorbiendo RTZ)..."
            else:
                titulo_carga = "Renesteando placa..."
            self.app.abrir_ventana_carga(titulo_carga)

        bloque_objetivo = bloque_previo
        idx_objetivo = bloque_objetivo.get("idx_base", -1)
        hoja_ref = hoja
        backup_grupo = self._snapshot_grupo_nesting(clave)
        inventario_antes = self._inventario_piezas_grupo(clave)

        def worker():
            try:
                if hasattr(self.app, "actualizar_progreso"):
                    self.app.actualizar_progreso("Preparando geometrías...", 0.1)
                if hasattr(self.app, "actualizar_progreso"):
                    self.app.actualizar_progreso("Extrayendo datos de piezas...", 0.3)
                opt = self.cmb_opt.currentText()
                corner = self.global_corner_val
                if hasattr(self.app, "actualizar_progreso"):
                    self.app.actualizar_progreso("Ejecutando motor...", 0.6)
                nueva, idx_retazos_asociados, hojas_extra = self._recalcular_hoja_con_contexto(
                    clave,
                    hoja,
                    k,
                    m,
                    opt,
                    corner,
                    compensar_plasma=compensar_plasma,
                    offset_mm_forzado=offset_mm_forzado,
                    absorber_rtz=absorber_rtz,
                )

                if hasattr(self.app, "actualizar_progreso"):
                    self.app.actualizar_progreso("Actualizando vista...", 0.9)

                conservar_rtz = bool(idx_retazos_asociados) and not absorber_rtz

                def on_ok(
                    _nueva=nueva,
                    _idx_rtz=idx_retazos_asociados,
                    _hojas_extra=list(hojas_extra or []),
                    _conservar=conservar_rtz,
                ):
                    self.finalizar_recalc(
                        _nueva,
                        clave_renest=clave,
                        post_fill=post_fill and not absorber_rtz,
                        idx_retazos_asociados=_idx_rtz if absorber_rtz else None,
                        nuevas_retazos=None,
                        hoja_original=copy.deepcopy(hoja),
                        tiene_minis=_conservar,
                        idx_objetivo=idx_objetivo,
                        hoja_ref=hoja_ref,
                        backup_grupo=backup_grupo,
                        inventario_antes=inventario_antes,
                        resumen_esperado=resumen_esperado if absorber_rtz else None,
                        eliminar_rtz_asociados=absorber_rtz,
                        hojas_adicionales=_hojas_extra,
                    )

                self.app.after(0, on_ok)
            except Exception as e:
                def on_err(msg=str(e)):
                    if hasattr(self.app, "cerrar_ventana_carga"):
                        self.app.cerrar_ventana_carga()
                    self._abortar_y_restaurar_nesting(
                        clave,
                        backup_grupo,
                        f"No se pudo completar la operación.\n\nDetalle:\n{msg}",
                        hoja_original=hoja,
                        idx_objetivo=idx_objetivo,
                    )

                self.app.after(0, on_err)

        threading.Thread(target=worker, daemon=True).start()

    def aplicar_cambios_locales(self):
        if not self.hoja_actual_data:
            return
        try:
            k, m = self._kerf_efectivo(), self.global_margin_val
        except Exception:
            return QMessageBox.critical(self, "Error", "Valores no válidos.")

        if hasattr(self.app, 'abrir_ventana_carga'):
            self.app.abrir_ventana_carga("Recalculando Placa...")

        backup_grupo = self._snapshot_grupo_nesting(self.clave_actual)
        inventario_antes = self._inventario_piezas_grupo(self.clave_actual)

        def worker():
            if hasattr(self.app, 'actualizar_progreso'):
                self.app.actualizar_progreso("Preparando geometrías...", 0.1)

            piezas_a_reprocesar = []
            material_hoja = self.clave_actual.split('_')[1] if '_' in self.clave_actual else self.clave_actual
            calibre_hoja = self.clave_actual.split('_')[0] if '_' in self.clave_actual else ""

            bloque_local = self._desglosar_bloque_placa_mini(self.clave_actual, self.hoja_actual_data)
            resumen_hoja = bloque_local["resumen_base"]
            idx_objetivo = bloque_local.get("idx_base", -1)
            hoja_ref = self.hoja_actual_data

            if hasattr(self.app, 'actualizar_progreso'):
                self.app.actualizar_progreso("Extrayendo datos de piezas...", 0.3)

            for p_nom, mat, qty, cal, st, ruta in getattr(self.app, 'datos_partes_actuales', []):
                if p_nom in resumen_hoja and str(cal).strip().upper() == calibre_hoja and str(mat).strip().upper() == material_hoja:
                    poly, marks = self.app.motor_nesting.recuperar_geometria_robusta(ruta)
                    if poly:
                        from shapely import affinity
                        mx, my, _, _ = poly.bounds
                        for _ in range(resumen_hoja[p_nom]):
                            piezas_a_reprocesar.append({
                                "nombre": p_nom,
                                "poly": affinity.translate(poly, -mx, -my),
                                "marks": affinity.translate(marks, -mx, -my) if not marks.is_empty else marks,
                                "area": poly.area,
                                "calibre": cal,
                                "material": mat,
                                "ruta": ruta
                            })

            nueva = None
            if hasattr(self.app, 'actualizar_progreso'):
                self.app.actualizar_progreso("Ejecutando Motor de Nesting...", 0.6)

            if piezas_a_reprocesar:
                nh = self.app.motor_nesting.empaquetar_con_reintentos(
                    piezas_a_reprocesar,
                    self.hoja_actual_data['placa_w'],
                    self.hoja_actual_data['placa_h'],
                    k,
                    m,
                    self.cmb_opt.currentText(),
                    self.global_corner_val,
                    intentos=8,
                    debug_tag="recalc_local",
                    solo_completo=True,
                )
                if nh and not self._hoja_cumple_resumen_esperado(nh, resumen_hoja):
                    nh = None
                if nh:
                    nh.update({
                        'placa_id': self.hoja_actual_data['placa_id'],
                        'placa_w': self.hoja_actual_data['placa_w'],
                        'placa_h': self.hoja_actual_data['placa_h'],
                        'precio_placa': self.hoja_actual_data.get('precio_placa', 0),
                        'kerf_usado': k,
                        'margin_usado': m,
                        'opt_usado': self.cmb_opt.currentText(),
                        'corner_usado': self.global_corner_val
                    })
                    nueva = nh
            else:
                nueva = self.app.motor_nesting.recalcular_hoja_full(
                    self.hoja_actual_data,
                    k,
                    m,
                    self.cmb_opt.currentText(),
                    self.global_corner_val
                )

            if hasattr(self.app, 'actualizar_progreso'):
                self.app.actualizar_progreso("Generando nueva visualización...", 0.9)
            self.app.after(
                0,
                lambda: self.finalizar_recalc(
                    nueva,
                    clave_renest=self.clave_actual,
                    post_fill=False,
                    idx_objetivo=idx_objetivo,
                    hoja_ref=hoja_ref,
                    hoja_original=copy.deepcopy(self.hoja_actual_data),
                    backup_grupo=backup_grupo,
                    inventario_antes=inventario_antes,
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _resolver_indice_hoja_objetivo(self, grp, nueva, idx_objetivo=None, hoja_ref=None, hoja_original=None):
        hojas = grp.get("hojas") or []
        if idx_objetivo is not None and 0 <= int(idx_objetivo) < len(hojas):
            return int(idx_objetivo)
        if hoja_ref is not None:
            for i, h in enumerate(hojas):
                if h is hoja_ref:
                    return i
        if hoja_original is not None:
            uid_ref = str(hoja_original.get("sheet_uid") or "").strip()
            if uid_ref:
                matches = [
                    i for i, h in enumerate(hojas)
                    if str(h.get("sheet_uid") or "").strip() == uid_ref
                ]
                if len(matches) == 1:
                    return matches[0]
            nest_idx = hoja_original.get("_nest_list_idx")
            if nest_idx is not None:
                ni = int(nest_idx)
                if 0 <= ni < len(hojas):
                    return ni
            pid_ref = str(hoja_original.get("placa_id", "") or "")
            es_ref = bool(hoja_original.get("es_retazo", False))
            w_ref = float(hoja_original.get("placa_w", 0) or 0)
            h_ref = float(hoja_original.get("placa_h", 0) or 0)
            candidatos = []
            for i, h in enumerate(hojas):
                if str(h.get("placa_id", "") or "") != pid_ref:
                    continue
                if bool(h.get("es_retazo", False)) != es_ref:
                    continue
                if abs(float(h.get("placa_w", 0) or 0) - w_ref) > 0.5:
                    continue
                if abs(float(h.get("placa_h", 0) or 0) - h_ref) > 0.5:
                    continue
                candidatos.append(i)
            if len(candidatos) == 1:
                return candidatos[0]
            if candidatos:
                firma = self._firma_resumen_piezas_hoja(hoja_original)
                por_firma = [
                    i for i in candidatos
                    if self._firma_resumen_piezas_hoja(hojas[i]) == firma
                ]
                if len(por_firma) == 1:
                    return por_firma[0]
        pid_n = str((nueva or {}).get("placa_id", "") or "")
        for i, h in enumerate(hojas):
            if str(h.get("placa_id", "") or "") == pid_n:
                return i
        return -1

    def finalizar_recalc(
        self,
        nueva,
        clave_renest=None,
        post_fill=False,
        idx_retazos_asociados=None,
        nuevas_retazos=None,
        hoja_original=None,
        tiene_minis=False,
        idx_objetivo=None,
        hoja_ref=None,
        backup_grupo=None,
        inventario_antes=None,
        resumen_esperado=None,
        eliminar_rtz_asociados=False,
        hojas_adicionales=None,
    ):
        if hasattr(self.app, 'cerrar_ventana_carga'):
            self.app.cerrar_ventana_carga()

        clv = clave_renest if clave_renest is not None else self.clave_actual
        snapshot = (
            backup_grupo
            if backup_grupo is not None
            else self._snapshot_grupo_nesting(clv)
        )
        inv_antes = (
            inventario_antes
            if inventario_antes is not None
            else self._inventario_piezas_grupo(clv)
        )

        if not nueva:
            QMessageBox.warning(
                self,
                "Atención",
                "No se logró un acomodo válido para esa placa (sobran piezas o Kerf/márgenes incompatibles).\n\n"
                "El nesteo no fue modificado.",
            )
            return

        grp = self.app.resultados_nesting.get(clv)
        if not grp or "hojas" not in grp:
            QMessageBox.warning(self, "Atención", "No se encontró el grupo de material en el resultado.")
            return

        if hoja_original or resumen_esperado:
            if resumen_esperado:
                resumen_req = {str(k): int(v) for k, v in resumen_esperado.items()}
            else:
                resumen_req = self._resumen_piezas_reales_hoja(hoja_original)
            hojas_validar = [h for h in [nueva, *(hojas_adicionales or [])] if h]
            resumen_colocado = self._resumen_piezas_en_hojas(hojas_validar)
            if resumen_colocado != resumen_req:
                self._abortar_y_restaurar_nesting(
                    clv,
                    snapshot,
                    "El motor no colocó todas las piezas del bloque objetivo.",
                    hoja_original=hoja_original,
                    idx_objetivo=idx_objetivo,
                )
                return

        if tiene_minis and hoja_original and self._placa_equivalente_en_esencia(hoja_original, nueva):
            nueva = copy.deepcopy(hoja_original)
            idx_retazos_asociados = None
            nuevas_retazos = None

        if idx_retazos_asociados and nuevas_retazos is not None:
            for ridx in sorted(set(idx_retazos_asociados), reverse=True):
                if 0 <= ridx < len(grp["hojas"]) and grp["hojas"][ridx].get("es_retazo", False):
                    grp["hojas"].pop(ridx)

        idx_match = self._resolver_indice_hoja_objetivo(
            grp,
            nueva,
            idx_objetivo=idx_objetivo,
            hoja_ref=hoja_ref,
            hoja_original=hoja_original,
        )
        if idx_match < 0:
            self._abortar_y_restaurar_nesting(
                clv,
                snapshot,
                "No se pudo ubicar la placa correcta en el listado.",
                hoja_original=hoja_original,
                idx_objetivo=idx_objetivo,
            )
            return

        anterior = grp["hojas"][idx_match]
        for mk in (
            "sheet_uid",
            "sheet_code",
            "sheet_seq",
            "sheet_display_name",
            "plate_group_key",
            "_nest_list_idx",
        ):
            if anterior.get(mk) is not None:
                nueva[mk] = anterior[mk]

        self.app.resultados_nesting[clv]["hojas"][idx_match] = nueva
        hoja_ref = self.app.resultados_nesting[clv]["hojas"][idx_match]

        if eliminar_rtz_asociados and idx_retazos_asociados:
            grp_rtz = self.app.resultados_nesting.get(clv) or {}
            hojas_rtz = grp_rtz.get("hojas") or []
            for ridx in sorted(set(idx_retazos_asociados), reverse=True):
                if 0 <= ridx < len(hojas_rtz) and hojas_rtz[ridx].get("es_retazo"):
                    hojas_rtz.pop(ridx)
            grp_rtz["hojas"] = hojas_rtz
            idx_match = self._resolver_indice_hoja_objetivo(
                grp_rtz,
                hoja_ref,
                idx_objetivo=idx_objetivo,
                hoja_ref=hoja_ref,
                hoja_original=hoja_original,
            )
            if idx_match >= 0:
                hoja_ref = grp_rtz["hojas"][idx_match]

        if hojas_adicionales:
            pos = idx_match + 1
            for h_extra in hojas_adicionales:
                self.app.resultados_nesting[clv]["hojas"].insert(pos, h_extra)
                pos += 1

        if post_fill:
            self._llenar_placa_desde_otras_hojas(clv, hoja_ref)
        hoja_actualizada = self.app.resultados_nesting[clv]["hojas"][idx_match]
        if nuevas_retazos:
            pos = idx_match + 1
            for hret in nuevas_retazos:
                self.app.resultados_nesting[clv]["hojas"].insert(pos, hret)
                pos += 1

        inv_despues = self._inventario_piezas_grupo(clv)
        if not self._inventarios_equivalentes(inv_antes, inv_despues):
            diff = self._texto_diff_inventario(inv_antes, inv_despues)
            self._abortar_y_restaurar_nesting(
                clv,
                snapshot,
                "La operación alteró el inventario total de piezas del calibre.\n"
                f"{diff}".strip(),
                hoja_original=hoja_original,
                idx_objetivo=idx_objetivo,
            )
            return

        self._recalcular_costos_grupo(clv)
        self.sincronizar_overlays_clave(clv)
        self._replicar_lote_activo_a_gemelos()
        self.dibujar_hoja_full(hoja_actualizada, clv)
        self.procesar_lista_hojas(self.app.resultados_nesting)

    def cargar_workspace_swo(self):
        ruta_archivo = self._ask_open_file(
            title="Seleccionar Workspace",
            filetypes=[("Archivos JSON", "*.json")]
        )
        if not ruta_archivo:
            return

        try:
            with open(ruta_archivo, 'r', encoding='utf-8') as f:
                res = json.load(f)

            # Compatibilidad si el SWO es formato viejo (un solo dict) o nuevo (lista multilote)
            if isinstance(res, list):
                self.app.resultados_multilote = res
            else:
                self.app.resultados_multilote = [{"lote_k": "SWO", "data": res}]

            self.actualizar_dropdown_lotes()
            generar_csv_compras(
                os.path.dirname(ruta_archivo),
                os.path.basename(ruta_archivo).replace(".json", ""),
                self.app.resultados_nesting
            )
            QMessageBox.information(self, "Éxito", "Workspace cargado y O.C. actualizada.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Fallo al cargar: {e}")

    def ejecutar_transferencia_masiva(self, entry_idx, entries, hoja_origen, clave, ventana):
        if entry_idx < 0 or entry_idx >= len(entries):
            return QMessageBox.warning(self, "Atención", "Debes seleccionar una placa destino.")
        entry = entries[entry_idx]
        hoja_destino = entry["hoja"]
        resultados_dest = entry["resultados"]
        lote_dest_idx = int(entry["lote_idx"])
        lote_origen_idx = int(getattr(self, "lote_actual_idx", 0) or 0)
        cross_wo = lote_dest_idx != lote_origen_idx
        if hasattr(ventana, "accept"):
            ventana.accept()
        else:
            ventana.close()

        if hasattr(self.app, "abrir_ventana_carga"):
            msg = "Moviendo piezas a otra Work Order..." if cross_wo else "Moviendo piezas a otra placa..."
            self.app.abrir_ventana_carga(msg)

        if cross_wo:
            hoja_origen, hoja_destino, resultados_dest = self._preparar_transferencia_cross_wo(
                clave,
                lote_origen_idx,
                lote_dest_idx,
                hoja_origen,
                entry,
            )
        else:
            resultados_dest = self.app.resultados_nesting

        backup_multilote = self._snapshot_multilote_grupos(clave)
        inventario_antes = self._inventario_piezas_multilote_grupo(clave)
        placa_origen_id = str(hoja_origen.get("placa_id", "Placa") or "Placa")
        placa_destino_id = str(hoja_destino.get("placa_id", "Placa") or "Placa")

        def worker():
            kwargs = {}
            if cross_wo:
                kwargs["resultados_destino"] = resultados_dest
            resultado = self.app.motor_nesting.transferir_piezas_a_placa(
                self.app.resultados_nesting,
                hoja_origen,
                hoja_destino,
                **kwargs,
            )
            self.app.after(
                0,
                lambda r=resultado: self.finalizar_transferencia_masiva(
                    r,
                    hoja_destino,
                    clave,
                    lote_origen_idx=lote_origen_idx,
                    lote_dest_idx=lote_dest_idx,
                    backup_multilote=backup_multilote,
                    inventario_antes=inventario_antes,
                    placa_origen=placa_origen_id,
                    placa_destino=placa_destino_id,
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def finalizar_transferencia_masiva(
        self,
        resultado,
        hoja_destino,
        clave,
        lote_origen_idx=0,
        lote_dest_idx=0,
        backup_multilote=None,
        backup_grupo=None,
        inventario_antes=None,
        placa_origen=None,
        placa_destino=None,
    ):
        if hasattr(self.app, "cerrar_ventana_carga"):
            self.app.cerrar_ventana_carga()

        inv_antes = (
            inventario_antes
            if inventario_antes is not None
            else self._inventario_piezas_multilote_grupo(clave)
        )
        inv_despues = self._inventario_piezas_multilote_grupo(clave)
        if not self._inventarios_equivalentes(inv_antes, inv_despues):
            diff = self._texto_diff_inventario(inv_antes, inv_despues)
            self._abortar_y_restaurar_nesting(
                clave,
                backup_grupo,
                "La transferencia alteró el inventario total de piezas.\n"
                f"{diff}".strip(),
                hoja_original=hoja_destino,
                multilote_snaps=backup_multilote,
            )
            return

        movidas = int((resultado or {}).get("movidas", 0) or 0)
        restantes = int((resultado or {}).get("restantes", 0) or 0)

        if movidas > 0:
            p_orig = placa_origen or str(
                (resultado or {}).get("placa_origen", "") or "Placa"
            )
            p_dest = placa_destino or str(
                (resultado or {}).get("placa_destino", "")
                or hoja_destino.get("placa_id", "Placa")
            )
            self._post_transferencia_exito(
                clave,
                lote_origen_idx,
                lote_dest_idx,
                hoja_destino,
                placa_origen=p_orig,
                placa_destino=p_dest,
                movidas=movidas,
            )
            parcial = restantes > 0
            msg = self._texto_resumen_transferencia(
                movidas=movidas,
                restantes=restantes,
                placa_origen=p_orig,
                placa_destino=p_dest,
                lote_origen_idx=lote_origen_idx,
                lote_dest_idx=lote_dest_idx,
                parcial=parcial,
            )
            if parcial:
                QMessageBox.warning(self, "Transferencia masiva", msg)
            else:
                QMessageBox.information(self, "Transferencia masiva", msg)
        else:
            motivo = str((resultado or {}).get("motivo", "") or "").strip()
            detalle = (
                f"\n\nDetalle técnico: {motivo}"
                if motivo and motivo not in ("", "sin_espacio")
                else ""
            )
            QMessageBox.warning(
                self,
                "Transferencia masiva",
                "No se pudo mover ninguna pieza a la placa destino con la configuración actual."
                + detalle,
            )

    def ejecutar_transferencia(self, entry_idx, entries, ventana):
        if entry_idx < 0 or entry_idx >= len(entries):
            return QMessageBox.warning(self, "Atención", "Debes seleccionar una placa.")
        entry = entries[entry_idx]
        hoja_destino = entry["hoja"]
        resultados_dest = entry["resultados"]
        lote_dest_idx = int(entry["lote_idx"])
        lote_origen_idx = int(getattr(self, "lote_actual_idx", 0) or 0)
        cross_wo = lote_dest_idx != lote_origen_idx
        piezas_sel = list(self.visor.piezas_seleccionadas)
        indices_sel = sorted(self.visor.piezas_seleccionadas_indices)
        if not indices_sel and self.visor.idx_pieza_seleccionada >= 0:
            indices_sel = [self.visor.idx_pieza_seleccionada]
        if not piezas_sel:
            return QMessageBox.warning(self, "Atención", "Debes seleccionar al menos una pieza.")
        if not self.hoja_actual_data:
            return QMessageBox.warning(self, "Atención", "No hay placa activa en el visor.")
        candidatos_prev = self.app.motor_nesting._resolver_candidatos_transferencia(
            self.hoja_actual_data,
            piezas_sel,
            indices=indices_sel,
        )
        if not candidatos_prev:
            placa_id = self.hoja_actual_data.get("placa_id", "?")
            return QMessageBox.warning(
                self,
                "Atención",
                "La pieza seleccionada no coincide con la placa activa del visor.\n\n"
                f"Placa en visor: {placa_id}\n\n"
                "Haz clic en la placa correcta en la lista izquierda, "
                "selecciona la pieza de nuevo y repite la transferencia.",
            )
        if hasattr(ventana, "accept"):
            ventana.accept()
        else:
            ventana.close()

        if hasattr(self.app, 'abrir_ventana_carga'):
            msg_carga = (
                f"Transfiriendo {len(piezas_sel)} piezas..."
                if len(piezas_sel) > 1
                else "Transfiriendo y reoptimizando..."
            )
            self.app.abrir_ventana_carga(msg_carga)

        if cross_wo:
            hoja_origen_snap, hoja_destino, resultados_dest = self._preparar_transferencia_cross_wo(
                self.clave_actual,
                lote_origen_idx,
                lote_dest_idx,
                self.hoja_actual_data,
                entry,
            )
        else:
            hoja_origen_snap = self.hoja_actual_data
            resultados_dest = self.app.resultados_nesting

        backup_multilote = self._snapshot_multilote_grupos(self.clave_actual)
        inventario_antes = self._inventario_piezas_multilote_grupo(self.clave_actual)
        placa_origen_id = str(hoja_origen_snap.get("placa_id", "Placa") or "Placa")
        placa_destino_id = str(hoja_destino.get("placa_id", "Placa") or "Placa")

        def worker():
            kwargs = {
                "piezas_especificas": piezas_sel,
                "piezas_indices": indices_sel,
            }
            if cross_wo:
                kwargs["resultados_destino"] = resultados_dest
            resultado = self.app.motor_nesting.transferir_piezas_a_placa(
                self.app.resultados_nesting,
                hoja_origen_snap,
                hoja_destino,
                **kwargs,
            )
            self.app.after(
                0,
                lambda r=resultado: self.finalizar_transferencia(
                    r,
                    hoja_destino,
                    lote_origen_idx=lote_origen_idx,
                    lote_dest_idx=lote_dest_idx,
                    backup_multilote=backup_multilote,
                    inventario_antes=inventario_antes,
                    clave=self.clave_actual,
                    placa_origen=placa_origen_id,
                    placa_destino=placa_destino_id,
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def finalizar_transferencia(
        self,
        exito,
        hoja_destino=None,
        lote_origen_idx=0,
        lote_dest_idx=0,
        backup_multilote=None,
        backup_grupo=None,
        inventario_antes=None,
        clave=None,
        placa_origen=None,
        placa_destino=None,
    ):
        if hasattr(self.app, 'cerrar_ventana_carga'):
            self.app.cerrar_ventana_carga()

        clv = clave if clave is not None else self.clave_actual
        inv_antes = (
            inventario_antes
            if inventario_antes is not None
            else self._inventario_piezas_multilote_grupo(clv)
        )
        inv_despues = self._inventario_piezas_multilote_grupo(clv)
        if not self._inventarios_equivalentes(inv_antes, inv_despues):
            diff = self._texto_diff_inventario(inv_antes, inv_despues)
            self._abortar_y_restaurar_nesting(
                clv,
                backup_grupo,
                "La transferencia alteró el inventario total de piezas.\n"
                f"{diff}".strip(),
                hoja_original=hoja_destino,
                multilote_snaps=backup_multilote,
            )
            return

        resultado = None
        if isinstance(exito, dict):
            resultado = exito
            exito = bool(resultado.get("ok"))

        if exito:
            movidas = int((resultado or {}).get("movidas", 0) or 0) if resultado else 0
            p_orig = placa_origen or "Placa"
            p_dest = placa_destino or str(
                (hoja_destino or {}).get("placa_id", "Placa")
            )
            self._post_transferencia_exito(
                clv,
                lote_origen_idx,
                lote_dest_idx,
                hoja_destino,
                limpiar_seleccion=True,
                placa_origen=p_orig,
                placa_destino=p_dest,
                movidas=movidas,
            )

            if resultado:
                restantes = int(resultado.get("restantes", 0) or 0)
                parcial = restantes > 0
                msg = self._texto_resumen_transferencia(
                    movidas=movidas,
                    restantes=restantes,
                    placa_origen=p_orig,
                    placa_destino=p_dest,
                    lote_origen_idx=lote_origen_idx,
                    lote_dest_idx=lote_dest_idx,
                    parcial=parcial,
                )
                titulo = "Transferencia parcial" if parcial else "Transferencia exitosa"
                if parcial:
                    QMessageBox.warning(self, titulo, msg)
                else:
                    QMessageBox.information(self, titulo, msg)
            else:
                QMessageBox.information(self, "Éxito", "Transferencia exitosa.")
        else:
            motivo = str((resultado or {}).get("motivo", "") or "")
            if motivo == "pieza_no_encontrada":
                QMessageBox.critical(
                    self,
                    "Falló",
                    "No se pudo identificar la pieza en la placa actual.\n"
                    "Vuelve a seleccionarla en el visor e intenta de nuevo.",
                )
            elif resultado and int(resultado.get("solicitadas", 0) or 0) > 1:
                QMessageBox.critical(
                    self,
                    "Falló",
                    "No hay espacio suficiente en destino para las piezas seleccionadas.",
                )
            else:
                QMessageBox.critical(
                    self,
                    "Falló",
                    "No hay espacio suficiente en la placa destino.\n\n"
                    "Si regresas una pieza a una RTZ, elige la hoja marcada como (RTZ).\n"
                    "En placas madre, el área reservada a RTZ no admite piezas nuevas.",
                )

    # =========================================================
    # EL EXPORTADOR HÍBRIDO (El más rápido y automático)
    # =========================================================
    def exportar_resultados_dxf(self):
        if not hasattr(self.app, 'resultados_multilote') or not self.app.resultados_multilote:
            return QMessageBox.warning(self, "Atención", "No hay datos para exportar.")

        if self._geom_prep_en_curso():
            return QMessageBox.information(
                self,
                "Validación geométrica en curso",
                "El nest se abrió en vista rápida, pero aún se calculan las "
                "transformaciones DXF 1:1 en segundo plano.\n\n"
                "Espera a que termine (el botón EXPORTAR se habilitará solo) para evitar "
                "DXF con piezas mal rotadas o fuera de placa.\n\n"
                "El visor puede verse poligonal hasta abrir cada placa; el export usa "
                "el DXF fuente con la rotación correcta.",
            )

        # =====================================================
        # === RADAR DE RUTAS DE IMPORTACIÓN (DIAGNÓSTICO) ===
        # =====================================================
        print("\n" + "=" * 50)
        print("VERIFICACIÓN DE ORIGEN DE DXFs (S.W.O.)")
        print("=" * 50)

        rutas_vistas = set()
        for pieza in self.app.datos_partes_actuales:
            nombre_pieza = pieza[0]
            ruta_original = pieza[5]

            if ruta_original not in rutas_vistas:
                print(f"Pieza: {nombre_pieza}")
                print(f"Viene de: {ruta_original}\n")
                rutas_vistas.add(ruta_original)

        print("=" * 50 + "\n")
        # =====================================================

        respuesta_3d = self._preguntar_generacion_3d_export()
        if respuesta_3d is None:
            return
        print(f"[DEBUG] Respuesta 3D: {respuesta_3d} (True=Yes, False=No, None=Cancelado)")

        if hasattr(self.app, 'abrir_ventana_carga'):
            self.app.abrir_ventana_carga("Procesando Exportación...")

        def worker():
            try:
                self._bloquear_hasta_geom_prep()
                modo_servidor = bool(getattr(self.app, "exportar_a_servidor", True))
                print(
                    f"[EXPORT] modo={'SERVIDOR+BD' if modo_servidor else 'LOCAL (Nesteos Locales)'}"
                )
                db_conf = {
                    "host": "192.168.2.80",
                    "database": "nestingpro_db",
                    "user": "postgres",
                    "password": "nesting123",
                    "port": "5433"
                }

                r_base = resolver_ruta_base_exportacion(self.app, modo_servidor=modo_servidor)
                print(f"[EXPORT] r_base = {r_base}")
                os.makedirs(r_base, exist_ok=True)

                rutas_generadas = []
                job_activo = getattr(self.app, 'job_activo', 'JOB').strip().upper()

                if not hasattr(self.app, "wo_reales_por_lote") or self.app.wo_reales_por_lote is None:
                    self.app.wo_reales_por_lote = {}

                usando_offline = False
                ruta_txt = os.path.join(r_base, "contador_emergencia.txt")

                if modo_servidor:
                    try:
                        consecutivo_base = obtener_siguiente_consecutivo(db_conf)
                    except Exception:
                        usando_offline = True
                        if os.path.exists(ruta_txt):
                            with open(ruta_txt, "r") as f:
                                consecutivo_base = int(f.read().strip())
                        else:
                            consecutivo_base = 1
                else:
                    os.makedirs(desktop_nesteos_locales(), exist_ok=True)
                    consecutivo_base = obtener_consecutivo_wo_local()

                # 2. EL BUCLE DE PRODUCCIÓN SECUENCIAL
                for i, orden_obj in enumerate(self.app.resultados_multilote):
                    k_val = orden_obj["lote_k"]
                    mini_resultados = orden_obj["data"]

                    es_swo_flag = job_activo.startswith("SWO")

                    if es_swo_flag:
                        try:
                            consecutivo_actual = int(job_activo.replace("SWO-", ""))
                        except Exception:
                            consecutivo_actual = consecutivo_base + i
                    else:
                        consecutivo_actual = consecutivo_base + i

                    qty_str = str(k_val) if k_val != "N/A" else "1"

                    n_wo, ruta_absoluta_wo = crear_estructura_carpetas(
                        r_base,
                        consecutivo_actual,
                        qty_str,
                        es_swo=es_swo_flag,
                        modo_local=not modo_servidor,
                    )
                    if not modo_servidor:
                        asegurar_exportacion_local(r_base, etiqueta="r_base")
                        asegurar_exportacion_local(ruta_absoluta_wo, etiqueta="carpeta WO")
                    print(f"[EXPORT] WO={n_wo} -> {ruta_absoluta_wo}")

                    # === INICIO DE RADIOGRAFÍA DE DATOS ===
                    print("\n" + "=" * 40)
                    print("--- TEST DE RUTAS Y GEOMETRÍA ---")
                    print(f"Ruta Base buscando CSV: {r_base}")
                    if self.app.datos_partes_actuales:
                        print(f"Ruta de la primera pieza importada: {self.app.datos_partes_actuales[0][5]}")

                    for mat, info in mini_resultados.items():
                        if "hojas" in info and len(info["hojas"]) > 0:
                            hoja_test = info["hojas"][0]
                            print("Límites de placa extraídos:", hoja_test.get("limites_placa", "No existe llave 'limites_placa'"))
                            if "piezas" in hoja_test and len(hoja_test["piezas"]) > 0:
                                pieza_test = hoja_test["piezas"][0]
                                print("Nombre de la pieza 1:", pieza_test.get("item", pieza_test.get("nombre", "Desconocido")))
                                print("Llaves dentro de la pieza:", list(pieza_test.keys()))
                                print("¿Tiene llave 'geometria'?:", "geometria" in pieza_test)
                                if "geometria" in pieza_test:
                                    print("Llaves dentro de geometría:", list(pieza_test["geometria"].keys()))
                            break
                    print("=" * 40 + "\n")
                    # === FIN DE RADIOGRAFÍA DE DATOS ===

                    generar_csv_compras(
                        r_base,
                        n_wo,
                        mini_resultados,
                        ruta_destino=ruta_absoluta_wo,
                        datos_piezas=self.app.datos_partes_actuales,
                        es_swo=es_swo_flag,
                        db_config=db_conf if modo_servidor else None,
                    )

                    ruta_export = os.path.join(ruta_absoluta_wo, "ARGA MODEL CORE")
                    os.makedirs(ruta_export, exist_ok=True)

                    # 1) Primero exportamos DXF/STEP para que mini_resultados
                    #    quede enriquecido con pqart_exports por hoja.
                    self.app.motor_nesting.exportar_resultados_a_dxf(
                        mini_resultados,
                        ruta_export,
                        "NESTING",
                        respuesta_3d,
                        wo_label=n_wo,
                        es_swo=es_swo_flag,
                        swo_id=job_activo if es_swo_flag else None,
                        datos_partes=getattr(self.app, "datos_partes_actuales", None),
                    )

                    try:
                        ruta_pdf_auto = self._exportar_pdf_nesting_en_carpeta_export(
                            mini_resultados,
                            ruta_export,
                            n_wo,
                            job_activo=job_activo,
                        )
                        print(f"[PDF][EXPORT] Reporte automático: {ruta_pdf_auto}")
                    except Exception as e_pdf:
                        print(f"[PDF][EXPORT][WARN] Lote {i + 1}: no se pudo generar el PDF: {e_pdf}")

                    try:
                        ruta_arganest_auto = self._exportar_arganest_en_carpeta_export(
                            mini_resultados,
                            ruta_export,
                            n_wo,
                            lote_idx=i,
                            k_val=k_val,
                            job_activo=job_activo,
                        )
                        print(f"[ARGANEST][EXPORT] Workspace automático: {ruta_arganest_auto}")
                    except Exception as e_arganest:
                        print(
                            f"[ARGANEST][EXPORT][WARN] Lote {i + 1}: "
                            f"no se pudo generar el .arganest: {e_arganest}"
                        )

                    if modo_servidor:
                        try:
                            guardar_nesting_en_postgresql(
                                job_activo,
                                n_wo,
                                mini_resultados,
                                db_conf,
                                "COMPLETADO" if respuesta_3d else "PENDIENTE",
                                ruta_export,
                                limpiar_previos=(i == 0),
                            )
                        except Exception as e:
                            print(f"[PQART][ERROR] No se pudo guardar en PostgreSQL después de exportar DXF: {e}")

                    # NUEVO: guardar la WO oficial del lote exportado
                    self.app.wo_reales_por_lote[i] = str(n_wo)

                    if modo_servidor:
                        try:
                            from interface.largos_nesting_service import aplicar_pedido_largos_tras_export

                            orden_largos = job_activo if es_swo_flag else str(n_wo)
                            tipo_largos = "SWO" if es_swo_flag else "WO"
                            ok_ldg, msg_ldg = aplicar_pedido_largos_tras_export(
                                self.app, i, orden_largos, tipo_largos
                            )
                            if ok_ldg:
                                print(
                                    f"[LARGOS_NESTING][EXPORT] {tipo_largos} {orden_largos} lote={i}: {msg_ldg}"
                                )
                            else:
                                print(
                                    f"[LARGOS_NESTING][EXPORT][WARN] {tipo_largos} {orden_largos} "
                                    f"lote={i}: {msg_ldg}"
                                )
                        except Exception as e_ldg:
                            print(f"[LARGOS_NESTING][EXPORT][ERROR] lote={i}: {e_ldg}")

                    rutas_generadas.append(ruta_absoluta_wo)

                # --- FIN DEL BUCLE MULTI-LOTE ---

                if modo_servidor:
                    try:
                        from modules.nesting_engine.api_client import avanzar_job_centralizado, avanzar_swo_centralizado

                        jobs_involved = set()
                        try:
                            import psycopg2
                            conn = psycopg2.connect(**db_conf)
                            with conn.cursor() as cur:
                                if es_swo_flag:
                                    cur.execute(
                                        "SELECT DISTINCT job FROM reporte_cortes WHERE super_work_order = %s AND job IS NOT NULL",
                                        (job_activo,),
                                    )
                                    for r in cur.fetchall():
                                        if r[0]:
                                            jobs_involved.add(r[0].strip())
                                    cur.execute(
                                        "SELECT DISTINCT job_numero FROM diccionario_swo WHERE swo_id = %s",
                                        (job_activo,),
                                    )
                                    for r in cur.fetchall():
                                        if r[0]:
                                            jobs_involved.add(r[0].strip())
                                else:
                                    jobs_involved.add(job_activo)
                            conn.close()
                        except Exception as db_err:
                            print(f"[CENTRALIZED][WARN] No se pudo consultar DB para jobs_involved: {db_err}")

                        if not jobs_involved:
                            jobs_involved.add(job_activo)

                        print(f"[CENTRALIZED] Procesando avance para: {jobs_involved}")
                        for j_val in jobs_involved:
                            avanzar_job_centralizado(str(j_val).strip())

                        if es_swo_flag:
                            avanzar_swo_centralizado(job_activo)

                        from modules.nesting_engine.api_client import trigger_po_contpaq, trigger_pedido_po
                        if es_swo_flag:
                            trigger_po_contpaq(job_activo)
                        else:
                            trigger_pedido_po(job_activo)

                    except Exception as api_err:
                        print(f"[API_TRIGGER][ERROR] Error crítico en el avance: {api_err}")

                total_carpetas = len(rutas_generadas)

                if modo_servidor and usando_offline:
                    with open(ruta_txt, "w") as f:
                        f.write(str(consecutivo_base + total_carpetas))
                elif not modo_servidor and total_carpetas > 0:
                    guardar_consecutivo_wo_local(consecutivo_base + total_carpetas - 1)

                if modo_servidor:
                    mensaje_final = f"Se exportaron {total_carpetas} Órdenes de Trabajo separadas."
                    if usando_offline:
                        mensaje_final += "\n\n(AVISO: Se usó el contador Offline porque el Servidor está desconectado)."
                else:
                    mensaje_final = (
                        f"Se exportaron {total_carpetas} lotes en modo local.\n"
                        f"Carpeta base: {desktop_nesteos_locales()}\n\n"
                        "No se envió información a PostgreSQL ni al servidor centralizado."
                    )

                if respuesta_3d:
                    try:
                        from modules.nesting_engine.exporter import obtener_resumen_step_ultimo_export

                        step_resumen = obtener_resumen_step_ultimo_export()
                        lineas_step = []
                        for fam, counts in sorted(step_resumen.items()):
                            n_dxf = int(counts.get("dxf") or 0)
                            n_step = int(counts.get("step") or 0)
                            n_iges = int(counts.get("iges") or 0)
                            if n_dxf > 0:
                                partes = []
                                if n_step:
                                    partes.append(f"{n_step} STEP")
                                if n_iges:
                                    partes.append(f"{n_iges} IGES")
                                detalle = ", ".join(partes) if partes else "0 3D"
                                lineas_step.append(f"  • {fam}: {detalle} / {n_dxf} DXF")
                        if lineas_step:
                            mensaje_final += "\n\nArchivos 3D:\n" + "\n".join(lineas_step)
                    except Exception:
                        pass

                ruta_mostrar = rutas_generadas[0] if rutas_generadas else r_base
                self.app.after(0, lambda: self.finalizar_exportacion(True, mensaje_final, ruta_mostrar))

            except Exception as e:
                mensaje_error = str(e)
                self.app.after(0, lambda msg=mensaje_error: self.finalizar_exportacion(False, msg, ""))

        threading.Thread(target=worker, daemon=True).start()

    def finalizar_exportacion(self, exito, mensaje, ruta):
        if hasattr(self.app, 'cerrar_ventana_carga'):
            self.app.cerrar_ventana_carga()
        if exito:
            QMessageBox.information(self, "Éxito", f"{mensaje}\n{ruta}")
            try:
                os.startfile(ruta)
            except Exception:
                pass
        else:
            QMessageBox.critical(self, "Error", mensaje)