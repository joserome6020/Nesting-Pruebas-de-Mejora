from __future__ import annotations
"""Métodos de exportación DXF/STEP/PDF/arganest para TabNesting."""


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
DEFAULT_KERF_IN = 0.3
DEFAULT_MARGIN_IN = 0.15



class ExportMixin:
    """Métodos de exportación DXF/STEP/PDF/arganest para TabNesting."""

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
        # Marca material para Icon Handler de Explorer (misma extensión .arganest).
        try:
            payload["workspace_material_kind"] = clasificar_material_workspace(payload)
        except Exception:
            pass
        guardar_workspace_payload(payload, ruta_arganest)
        return ruta_arganest

    def exportar_reporte_pdf_nesting(self):
        if not hasattr(self.app, 'resultados_nesting') or not self.app.resultados_nesting:
            return QMessageBox.warning(self, "Atención", "No hay datos de nesting para exportar.")

        try:
            from modules.nesting_engine.nest_poka_yoke import (
                allow_incomplete_nest,
                listar_fallas_resultados_nest,
            )

            fallas_pdf = listar_fallas_resultados_nest(self.app.resultados_nesting)
            if fallas_pdf and not allow_incomplete_nest():
                texto = "\n\n".join(fallas_pdf[:6])
                if len(fallas_pdf) > 6:
                    texto += f"\n\n(+{len(fallas_pdf) - 6} más)"
                return QMessageBox.critical(
                    self,
                    "PDF bloqueado (poka-yoke)",
                    "No se genera reporte oficial de un nest incompleto/solapado.\n\n"
                    f"{texto}",
                )
        except Exception as exc:
            return QMessageBox.critical(
                self,
                "PDF bloqueado (poka-yoke)",
                f"No se pudo validar integridad antes del PDF:\n{exc}",
            )

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

    def _preguntar_motor_3d_export(self):
        """
        Motor STEP tras 'SI, generar 3D'.
        Returns: 'freecad' | 'occt' | None (cancelar toda la exportación).

        FreeCAD / generador_verde = producción.
        Arga Nesting Suite = mismo FreeCAD si está instalado; OCCT solo si no hay FreeCAD.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Motor 3D (STEP)")
        box.setText("¿Con qué motor generar los archivos STEP?")
        box.setInformativeText(
            "FreeCAD: producción (generador_verde).\n\n"
            "Arga Nesting Suite: reutiliza FreeCAD si está instalado "
            "(mismo resultado); solo usa OCCT embebido si FreeCAD no está disponible.\n\n"
            "El visor 3D OCCT no se desconecta en ningún caso."
        )
        btn_fc = box.addButton("FreeCAD", QMessageBox.ButtonRole.YesRole)
        btn_occt = box.addButton("Arga Nesting Suite", QMessageBox.ButtonRole.ActionRole)
        btn_cancel = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        for btn, min_w in ((btn_fc, 120), (btn_occt, 180), (btn_cancel, 104)):
            btn.setMinimumWidth(min_w)
            btn.setMinimumHeight(34)
        box.setDefaultButton(btn_fc)
        box.exec()
        clicked = box.clickedButton()
        if clicked is None or clicked == btn_cancel:
            return None
        if clicked == btn_occt:
            return "occt"
        return "freecad"

    def exportar_resultados_dxf(self):
        if not hasattr(self.app, 'resultados_multilote') or not self.app.resultados_multilote:
            return QMessageBox.warning(self, "Atención", "No hay datos para exportar.")

        # Poka-yoke: no exportar nests incompletos / solapados / con error de grupo.
        try:
            from modules.nesting_engine.nest_poka_yoke import (
                allow_incomplete_nest,
                listar_fallas_resultados_nest,
            )

            fallas_export: list[str] = []
            for lote in self.app.resultados_multilote or []:
                data = (lote or {}).get("data")
                fallas_export.extend(listar_fallas_resultados_nest(data))
            if fallas_export and not allow_incomplete_nest():
                texto = "\n\n".join(fallas_export[:6])
                if len(fallas_export) > 6:
                    texto += f"\n\n(+{len(fallas_export) - 6} más)"
                return QMessageBox.critical(
                    self,
                    "Exportación bloqueada (poka-yoke)",
                    "Hay grupos con error, inventario incompleto o solapes.\n"
                    "Corrija el nesteo (o renestee) antes de exportar a corte.\n\n"
                    f"{texto}\n\n"
                    "Escape shop: ARGA_ALLOW_INCOMPLETE_NEST=1",
                )
        except Exception as exc:
            return QMessageBox.critical(
                self,
                "Exportación bloqueada (poka-yoke)",
                f"No se pudo validar integridad antes de exportar:\n{exc}",
            )

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

        motor_3d = "freecad"
        if respuesta_3d:
            elegido = self._preguntar_motor_3d_export()
            if elegido is None:
                return
            motor_3d = elegido
        print(f"[DEBUG] Motor 3D: {motor_3d}")

        # Totales estimados (todos los lotes) para la pantalla dual
        try:
            from modules.nesting_engine.exporter import estimar_conteos_export

            n_dxf_tot = 0
            n_step_tot = 0
            for orden_obj in (self.app.resultados_multilote or []):
                mini = orden_obj.get("data") or {}
                d, s = estimar_conteos_export(mini, generar_step=bool(respuesta_3d))
                n_dxf_tot += int(d)
                n_step_tot += int(s)
        except Exception as exc_est:
            print(f"[EXPORT][WARN] No se pudo estimar conteos: {exc_est}")
            n_dxf_tot, n_step_tot = 0, 0

        titulo_export = (
            "Exportando DXF / STEP — Arga Nesting Suite"
            if motor_3d == "occt" and respuesta_3d
            else (
                "Exportando DXF / STEP — FreeCAD"
                if respuesta_3d
                else "Exportando DXF…"
            )
        )
        if hasattr(self.app, "abrir_ventana_carga_export"):
            self.app.abrir_ventana_carga_export(
                titulo_export,
                n_dxf=n_dxf_tot,
                n_step=n_step_tot if respuesta_3d else 0,
            )
        elif hasattr(self.app, "abrir_ventana_carga"):
            self.app.abrir_ventana_carga(titulo_export)

        dxf_done_global = [0]
        step_done_global = [0]

        def _on_export_progress(**kwargs):
            msg = str(kwargs.get("mensaje") or "")
            dd = kwargs.get("dxf_done")
            sd = kwargs.get("step_done")
            # Acumular por lote: el exporter reporta contadores locales del lote actual.
            # Usamos deltas vía máximos locales trackeados por lote en el worker.
            if hasattr(self.app, "actualizar_progreso_export"):
                self.app.actualizar_progreso_export(
                    mensaje=msg,
                    dxf_done=dxf_done_global[0] + int(dd or 0) if dd is not None else None,
                    step_done=step_done_global[0] + int(sd or 0) if sd is not None else None,
                    dxf_total=n_dxf_tot,
                    step_total=n_step_tot if respuesta_3d else 0,
                )
            elif hasattr(self.app, "actualizar_progreso"):
                pct = 0.0
                if n_dxf_tot > 0 and dd is not None:
                    pct = 0.55 * float(dxf_done_global[0] + int(dd)) / float(n_dxf_tot)
                if respuesta_3d and n_step_tot > 0 and sd is not None:
                    pct = 0.55 + 0.45 * float(step_done_global[0] + int(sd)) / float(n_step_tot)
                self.app.actualizar_progreso(msg or "Exportando…", pct)

        def worker():
            try:
                _on_export_progress(
                    mensaje="Preparando geometría / carpetas…",
                    dxf_done=0,
                    step_done=0 if respuesta_3d else None,
                )
                self._bloquear_hasta_geom_prep()
                _on_export_progress(
                    mensaje="Creando estructura de W.O.…",
                    dxf_done=0,
                    step_done=0 if respuesta_3d else None,
                )
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

                    from modules.nesting_engine.resultados_grupos import iter_grupos_material

                    for _mat, info in iter_grupos_material(mini_resultados):
                        hojas_test = info.get("hojas") or []
                        if not hojas_test:
                            continue
                        hoja_test = hojas_test[0]
                        if not isinstance(hoja_test, dict):
                            continue
                        print("Límites de placa extraídos:", hoja_test.get("limites_placa", "No existe llave 'limites_placa'"))
                        piezas_test = hoja_test.get("piezas") or []
                        if piezas_test and isinstance(piezas_test[0], dict):
                            pieza_test = piezas_test[0]
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
                    try:
                        self.app.ultima_ruta_export_cad = ruta_export
                    except Exception:
                        pass

                    # 1) Primero exportamos DXF/STEP para que mini_resultados
                    #    quede enriquecido con pqart_exports por hoja.
                    lote_dxf_max = [0]
                    lote_step_max = [0]

                    def _progress_lote(**kwargs):
                        dd = kwargs.get("dxf_done")
                        sd = kwargs.get("step_done")
                        if dd is not None:
                            lote_dxf_max[0] = max(lote_dxf_max[0], int(dd))
                        if sd is not None:
                            lote_step_max[0] = max(lote_step_max[0], int(sd))
                        _on_export_progress(
                            mensaje=kwargs.get("mensaje", ""),
                            dxf_done=lote_dxf_max[0],
                            step_done=lote_step_max[0] if respuesta_3d else None,
                        )

                    self.app.motor_nesting.exportar_resultados_a_dxf(
                        mini_resultados,
                        ruta_export,
                        "NESTING",
                        respuesta_3d,
                        wo_label=n_wo,
                        es_swo=es_swo_flag,
                        swo_id=job_activo if es_swo_flag else None,
                        datos_partes=getattr(self.app, "datos_partes_actuales", None),
                        motor_3d=motor_3d if respuesta_3d else "freecad",
                        progress_cb=_progress_lote,
                    )
                    dxf_done_global[0] += int(lote_dxf_max[0])
                    step_done_global[0] += int(lote_step_max[0])
                    _on_export_progress(
                        mensaje=f"Lote {i + 1} exportado",
                        dxf_done=0,
                        step_done=0 if respuesta_3d else None,
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
                            if n_dxf > 0:
                                detalle = f"{n_step} STEP" if n_step else "0 STEP"
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
