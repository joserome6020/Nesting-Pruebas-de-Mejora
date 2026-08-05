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
from postgres_connector import (
    guardar_nesting_en_postgresql,
    obtener_wos_sin_lista_largos,
    reiniciar_avisos_lista_largos,
)
from reporte_pdf_nesting import exportar_pdf_nesting
from interface.export_checkpoint_service import (
    checkpoint_export_ok,
    guardar_checkpoint_export,
    nuevo_export_run_id,
)
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


class ExportStageError(RuntimeError):
    """Fallo recuperable con una etapa explícita para el operador."""

    def __init__(self, stage: str, detail: str):
        self.stage = str(stage or "DESCONOCIDA").upper()
        super().__init__(f"[{self.stage}] {detail}")


def _es_job_swo(nombre: str) -> bool:
    """Detecta SWO-001 / S.W.O 01 X1 / similares."""
    n = str(nombre or "").strip().upper()
    if not n:
        return False
    return n.startswith("SWO") or n.startswith("S.W.O") or "S.W.O" in n


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

        FreeCAD = generador_verde (producción clásica).
        Arga Nesting Suite / OCCT = motor embebido CAD (OCCT), sin FreeCAD.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Motor 3D (STEP)")
        box.setText("¿Con qué motor generar los archivos STEP?")
        box.setInformativeText(
            "FreeCAD: producción clásica (generador_verde).\n\n"
            "Arga Nesting Suite (OCCT): motor embebido — no usa FreeCAD.\n\n"
            "El visor 3D OCCT funciona con STEPs de cualquiera de los dos."
        )
        btn_fc = box.addButton("FreeCAD", QMessageBox.ButtonRole.YesRole)
        btn_occt = box.addButton("OCCT (Arga)", QMessageBox.ButtonRole.ActionRole)
        btn_cancel = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        for btn, min_w in ((btn_fc, 120), (btn_occt, 140), (btn_cancel, 104)):
            btn.setMinimumWidth(min_w)
            btn.setMinimumHeight(34)
        box.setDefaultButton(btn_occt)
        box.exec()
        clicked = box.clickedButton()
        if clicked is None or clicked == btn_cancel:
            return None
        if clicked == btn_occt:
            return "occt"
        return "freecad"

    def _validar_lote_exportado(
        self,
        exportados: list[str] | tuple[str, ...] | None,
        mini_resultados: dict,
        n_wo: str,
    ) -> int:
        """
        Verifica el contrato entre el exportador CAD y la persistencia PQART.

        Ningún lote puede seguir a PostgreSQL/VSM si el exportador no entregó
        DXF físicos y sus metadatos de producción por hoja.
        """
        rutas = [os.path.normpath(str(path)) for path in (exportados or []) if str(path).strip()]
        if not rutas:
            raise RuntimeError(f"{n_wo}: el exportador no produjo DXF de nesting.")
        rutas_exportadas = {os.path.normcase(path) for path in rutas}
        if len(rutas_exportadas) != len(rutas):
            raise RuntimeError(f"{n_wo}: el exportador devolvió rutas DXF duplicadas.")

        faltantes = [path for path in rutas if not os.path.isfile(path)]
        if faltantes:
            raise RuntimeError(
                f"{n_wo}: faltan {len(faltantes)} DXF después de exportar. "
                f"Primero: {faltantes[0]}"
            )

        from modules.nesting_engine.resultados_grupos import iter_grupos_material

        pqart = []
        for _material, data in iter_grupos_material(mini_resultados):
            for hoja in data.get("hojas", []) or []:
                if isinstance(hoja, dict):
                    pqart.extend(hoja.get("pqart_exports", []) or [])

        if not pqart:
            raise RuntimeError(
                f"{n_wo}: no se generó metadata PQART para los DXF exportados."
            )

        rutas_pqart = [
            os.path.normpath(str(item.get("ruta") or ""))
            for item in pqart
            if isinstance(item, dict) and str(item.get("ruta") or "").strip()
        ]
        if not rutas_pqart:
            raise RuntimeError(
                f"{n_wo}: la metadata PQART no contiene rutas DXF válidas."
            )
        rutas_pqart_norm = {os.path.normcase(path) for path in rutas_pqart}
        if len(rutas_pqart_norm) != len(rutas_pqart):
            raise RuntimeError(f"{n_wo}: PQART contiene rutas DXF duplicadas.")
        sin_pqart = rutas_exportadas - rutas_pqart_norm
        if sin_pqart:
            raise RuntimeError(
                f"{n_wo}: hay DXF exportados sin registro PQART. "
                f"Primero: {sorted(sin_pqart)[0]}"
            )
        faltantes_pqart = [path for path in rutas_pqart if not os.path.isfile(path)]
        if faltantes_pqart:
            raise RuntimeError(
                f"{n_wo}: PQART apunta a DXF inexistente. Primero: {faltantes_pqart[0]}"
            )
        return len(rutas_pqart)

    def _consolidar_snapshot_swo(self) -> dict:
        """Une grupos de todos los lotes para publicar un solo reporte SWO."""
        from modules.nesting_engine.resultados_grupos import iter_grupos_material

        consolidado: dict = {}
        for orden in getattr(self.app, "resultados_multilote", []) or []:
            data_lote = (orden or {}).get("data") or {}
            for clave, grupo in iter_grupos_material(data_lote):
                if clave not in consolidado:
                    base = copy.deepcopy(grupo)
                    base["hojas"] = []
                    consolidado[clave] = base
                consolidado[clave]["hojas"].extend(
                    copy.deepcopy(grupo.get("hojas", []) or [])
                )
        if not consolidado:
            raise RuntimeError("No hay grupos de material para publicar el reporte SWO.")
        return consolidado

    def _revertir_wos_persistidas(
        self,
        db_conf: dict,
        wos_persistidas: list,
        job_activo: str,
        es_swo: bool,
    ) -> list:
        """
        Deshace las WO que ya se escribieron en PostgreSQL cuando la corrida
        aborta antes de centralizar.

        Solo se invoca antes de VSM/ContPAQ: a partir de ahí la exportación es
        válida y únicamente falta reanudar la sincronización. Sin esto, cada
        intento fallido dejaba una orden a medias que además consumía el
        consecutivo del siguiente intento.
        """
        import psycopg2

        orden = str(job_activo or "").strip()
        etiquetas = [str(w).strip() for w in (wos_persistidas or []) if str(w).strip()]
        if not etiquetas:
            return []

        revertidas = []
        try:
            with psycopg2.connect(**db_conf) as conexion:
                with conexion.cursor() as cursor:
                    if es_swo:
                        # La SWO comparte un solo layout maestro: se borra una vez.
                        cursor.execute(
                            """
                            DELETE FROM reporte_cortes
                            WHERE BTRIM(super_work_order) = %s
                              AND estatus ILIKE 'Pendiente%%'
                            """,
                            (orden,),
                        )
                        revertidas.append(f"{orden} ({cursor.rowcount} pieza(s))")
                        cursor.execute(
                            "DELETE FROM public.pqart_swo WHERE BTRIM(nombre_swo) = %s",
                            (orden,),
                        )
                        cursor.execute(
                            "DELETE FROM material_requerido_ldg WHERE BTRIM(orden_id) = %s",
                            (orden,),
                        )
                    else:
                        for wo in etiquetas:
                            cursor.execute(
                                """
                                DELETE FROM reporte_cortes
                                WHERE BTRIM(work_order) = %s
                                  AND BTRIM(job) = %s
                                  AND estatus ILIKE 'Pendiente%%'
                                """,
                                (wo, orden),
                            )
                            revertidas.append(f"{wo} ({cursor.rowcount} pieza(s))")
                            cursor.execute(
                                "DELETE FROM public.pqart_wo WHERE BTRIM(nombre_wo) = %s",
                                (wo,),
                            )
                            cursor.execute(
                                "DELETE FROM material_requerido_ldg WHERE BTRIM(orden_id) = %s",
                                (wo,),
                            )

                    for wo in etiquetas:
                        cursor.execute(
                            """
                            DELETE FROM costos_prorrateo
                            WHERE BTRIM(work_order) = %s AND BTRIM(job) = %s
                            """,
                            (wo, orden),
                        )
                        cursor.execute(
                            "DELETE FROM public.export_stage_checkpoints WHERE scope_id = %s",
                            (wo,),
                        )
        except Exception as exc:
            print(f"[EXPORT][ROLLBACK][ERROR] No se pudo revertir la corrida: {exc}")
            return []

        for detalle in revertidas:
            print(f"[EXPORT][ROLLBACK] {detalle} revertida(s).")
        return revertidas

    def _centralizar_exportacion_confirmada(
        self,
        *,
        db_conf: dict,
        job_activo: str,
        es_swo: bool,
        reporte_swo: dict | None = None,
        run_id: str | None = None,
    ) -> None:
        """
        Sincroniza VSM y ContPAQ únicamente después de persistir todos los
        lotes, sus DXF y sus pedidos de material. Cada subetapa se registra
        para permitir reanudarla sin reexportar CAD ni duplicar ContPAQ.
        """
        import psycopg2

        from modules.nesting_engine.api_client import (
            avanzar_job_centralizado,
            avanzar_swo_centralizado,
            enviar_reporte_a_api,
            trigger_po_contpaq,
            validar_po_contpaq,
        )

        scope_id = str(job_activo or "").strip()
        scope_type = "SWO" if es_swo else "JOB"
        run_id = str(run_id or getattr(self.app, "export_run_id", "") or "").strip() or None

        def _checkpoint(stage: str, *, status: str, detail: str = "", resultado=None):
            metadata = {
                "job_activo": scope_id,
                "es_swo": bool(es_swo),
                "stage": str(stage).upper(),
            }
            if resultado is not None:
                metadata["api_target"] = str(getattr(resultado, "target", "") or "")
                metadata["api_operation"] = str(getattr(resultado, "operation", "") or "")
                metadata["api_response"] = getattr(resultado, "response", None) or {}
            try:
                guardar_checkpoint_export(
                    scope_id,
                    scope_type,
                    stage,
                    status=status,
                    run_id=run_id,
                    detail=detail,
                    http_status=getattr(resultado, "http_status", None),
                    metadata=metadata,
                    db_config=db_conf,
                )
            except Exception as exc:
                # El log no debe convertir un éxito externo real en falso error.
                print(f"[EXPORT][CHECKPOINT][WARN] {stage}: {exc}")

        def _pendiente(stage: str) -> bool:
            try:
                return not checkpoint_export_ok(
                    scope_id,
                    scope_type,
                    stage,
                    db_config=db_conf,
                )
            except Exception as exc:
                print(f"[EXPORT][CHECKPOINT][WARN] consulta {stage}: {exc}")
                return True

        jobs_involved: set[str] = set()
        try:
            with psycopg2.connect(**db_conf) as conn:
                with conn.cursor() as cur:
                    if es_swo:
                        # Solo jobs del nest exportado. Nunca diccionario_swo:
                        # puede arrastrar WO/jobs viejos que no vienen en esta SWO.
                        cur.execute(
                            """
                            SELECT DISTINCT TRIM(job)
                            FROM reporte_cortes
                            WHERE TRIM(super_work_order) = %s
                              AND job IS NOT NULL
                              AND BTRIM(job) <> ''
                            """,
                            (job_activo,),
                        )
                        jobs_involved.update(
                            str(row[0]).strip() for row in cur.fetchall() if row[0]
                        )
                    else:
                        jobs_involved.add(job_activo)
        except Exception as exc:
            raise RuntimeError(
                "No se pudo confirmar en PostgreSQL la relación Job/SWO antes "
                f"de centralizar: {exc}"
            ) from exc

        jobs_involved = {
            str(j).strip()
            for j in jobs_involved
            if str(j or "").strip() and not _es_job_swo(str(j).strip())
        }
        if not jobs_involved:
            raise RuntimeError(
                "No hay Jobs VSM trazables para centralizar esta exportación. "
                "Revisa reporte_cortes / diccionario_swo antes de ContPAQ."
            )

        # No avance VSM si la OC SWO ni siquiera puede validarse. Esto evita
        # una tarjeta EXPORTADO sin PO por equivalencias/catálogo pendientes.
        if es_swo:
            stage = "CONTPAQ_PREFLIGHT"
            if _pendiente(stage):
                resultado_preflight = validar_po_contpaq(job_activo)
                if not resultado_preflight:
                    detalle = resultado_preflight.summary()
                    _checkpoint(
                        stage,
                        status="FAILED",
                        detail=detalle,
                        resultado=resultado_preflight,
                    )
                    raise ExportStageError(
                        stage,
                        "DXF y PostgreSQL ya están confirmados, pero la validación "
                        f"de códigos/materiales para ContPAQ falló: {detalle}. "
                        "No se avanzó VSM ni se creó una OC; corrija las "
                        "equivalencias y use ‘Reanudar sync’.",
                    )
                _checkpoint(
                    stage,
                    status="OK",
                    detail=resultado_preflight.summary(),
                    resultado=resultado_preflight,
                )
            else:
                print("[CENTRALIZED] CONTPAQ_PREFLIGHT ya confirmado; se omite reintento.")

        print(f"[CENTRALIZED] Jobs a sincronizar: {sorted(jobs_involved)}")
        for job in sorted(jobs_involved):
            stage = f"VSM_JOB:{job}"
            if not _pendiente(stage):
                print(f"[CENTRALIZED] {stage} ya confirmado; se omite reintento.")
                continue
            resultado = avanzar_job_centralizado(job)
            if not resultado:
                detalle = resultado.summary()
                _checkpoint(stage, status="FAILED", detail=detalle, resultado=resultado)
                raise ExportStageError(
                    stage,
                    "DXF, PostgreSQL y MRL ya están confirmados; "
                    f"VSM no confirmó esta etapa: {detalle}. "
                    "Use ‘Reanudar sync’ cuando VSM esté disponible.",
                )
            _checkpoint(stage, status="OK", detail=resultado.summary(), resultado=resultado)

        if es_swo:
            stage = "VSM_SWO"
            if _pendiente(stage):
                resultado = avanzar_swo_centralizado(job_activo)
                if not resultado:
                    detalle = resultado.summary()
                    _checkpoint(stage, status="FAILED", detail=detalle, resultado=resultado)
                    raise ExportStageError(
                        stage,
                        "DXF, PostgreSQL y MRL ya están confirmados; "
                        f"VSM no confirmó la SWO: {detalle}. "
                        "Use ‘Reanudar sync’ cuando VSM esté disponible.",
                    )
                _checkpoint(stage, status="OK", detail=resultado.summary(), resultado=resultado)
            else:
                print("[CENTRALIZED] VSM_SWO ya confirmado; se omite reintento.")

        stage = "CONTPAQ"
        if not es_swo:
            _checkpoint(
                stage,
                status="OK",
                detail=(
                    "PO ContPAQ omitida intencionalmente: las WO normales no "
                    "generan pedido; la compra se consolida únicamente en la SWO."
                ),
            )
            print("[CENTRALIZED] CONTPAQ omitido para WO normal; solo las SWO generan PO.")
        elif _pendiente(stage):
            resultado_po = trigger_po_contpaq(job_activo)
            if not resultado_po:
                detalle = resultado_po.summary()
                _checkpoint(stage, status="FAILED", detail=detalle, resultado=resultado_po)
                raise ExportStageError(
                    stage,
                    "DXF, PostgreSQL, MRL y VSM ya están confirmados; "
                    f"ContPAQ no confirmó el pedido: {detalle}. "
                    "Use ‘Reanudar sync’; no reexporte CAD.",
                )
            _checkpoint(stage, status="OK", detail=resultado_po.summary(), resultado=resultado_po)
        else:
            print("[CENTRALIZED] CONTPAQ ya confirmado; se omite reintento.")

        if es_swo:
            if not reporte_swo:
                _checkpoint(
                    "REPORTE_SWO",
                    status="WARNING",
                    detail="Falta snapshot en memoria; reanude desde el workspace.",
                )
                return
            if _pendiente("REPORTE_SWO"):
                resultado_reporte = enviar_reporte_a_api(job_activo, reporte_swo)
                if not resultado_reporte:
                    # El reporte web solo alimenta el dashboard: la SWO ya quedó
                    # exportada en CAD, PostgreSQL, MRL, VSM y ContPAQ. Se avisa
                    # en consola y queda pendiente para ‘Reanudar sync’, pero no
                    # se convierte en un error de exportación para el usuario.
                    detalle = resultado_reporte.summary()
                    _checkpoint(
                        "REPORTE_SWO",
                        status="WARNING",
                        detail=detalle,
                        resultado=resultado_reporte,
                    )
                    print(
                        f"[CENTRALIZED][WARN] REPORTE_SWO {job_activo}: el reporte "
                        f"web no confirmó ({detalle}). La exportación sigue válida; "
                        "use ‘Reanudar sync’ para reintentarlo."
                    )
                    return
                _checkpoint(
                    "REPORTE_SWO",
                    status="OK",
                    detail=resultado_reporte.summary(),
                    resultado=resultado_reporte,
                )

    def reanudar_centralizacion_pendiente(self):
        """
        Reintenta únicamente VSM/ContPAQ/reporte para una exportación ya
        persistida. Nunca ejecuta DXF, STEP, PostgreSQL ni MRL.
        """
        job_activo = str(getattr(self.app, "job_activo", "") or "").strip().upper()
        if not job_activo or job_activo in {"JOB", "NESTING"}:
            return QMessageBox.warning(
                self,
                "Reanudar sync",
                "Abra el workspace de la WO/SWO exportada antes de reanudar.",
            )
        es_swo = _es_job_swo(job_activo)
        confirmar = QMessageBox.question(
            self,
            "Reanudar centralización",
            "Solo se reintentarán VSM, ContPAQ y el reporte web pendiente.\n\n"
            "No se generarán DXF/STEP, no se creará otra WO y no se reconstruirá MRL.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if confirmar != QMessageBox.StandardButton.Yes:
            return
        db_conf = {
            "host": getattr(config, "NESTING_DB_HOST", "192.168.2.80"),
            "database": getattr(config, "NESTING_DB_NAME", "nestingpro_db"),
            "user": getattr(config, "NESTING_DB_USER", "postgres"),
            "password": getattr(config, "NESTING_DB_PASSWORD", "nesting123"),
            "port": getattr(config, "NESTING_DB_PORT", "5433"),
        }

        def _worker_reanudar():
            try:
                import psycopg2

                with psycopg2.connect(**db_conf) as conexion:
                    with conexion.cursor() as cursor:
                        if es_swo:
                            cursor.execute(
                                """
                                SELECT EXISTS(
                                    SELECT 1 FROM reporte_cortes
                                    WHERE BTRIM(super_work_order) = %s
                                )
                                """,
                                (job_activo,),
                            )
                        else:
                            cursor.execute(
                                """
                                SELECT EXISTS(
                                    SELECT 1 FROM reporte_cortes
                                    WHERE BTRIM(job) = %s
                                )
                                """,
                                (job_activo,),
                            )
                        exportacion_persistida = bool((cursor.fetchone() or [False])[0])
                if not exportacion_persistida:
                    raise ExportStageError(
                        "PRECONDICION",
                        "No se encontró una exportación PostgreSQL para esta orden; "
                        "no se ejecutó ninguna llamada externa.",
                    )
                self._centralizar_exportacion_confirmada(
                    db_conf=db_conf,
                    job_activo=job_activo,
                    es_swo=es_swo,
                    reporte_swo=self._consolidar_snapshot_swo()
                    if es_swo and getattr(self.app, "resultados_multilote", None)
                    else None,
                    run_id=str(getattr(self.app, "export_run_id", "") or "") or None,
                )
                self.app.after(
                    0,
                    lambda: QMessageBox.information(
                        self,
                        "Reanudar sync",
                        "Centralización confirmada. No se repitió la exportación CAD.",
                    ),
                )
            except Exception as exc:
                self.app.after(
                    0,
                    lambda e=str(exc): QMessageBox.warning(
                        self,
                        "Centralización pendiente",
                        e,
                    ),
                )

        threading.Thread(target=_worker_reanudar, daemon=True).start()

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
            # ANS C++: preferir OCCT por defecto (opt-out ARGA_EXPORT_3D_MOTOR=freecad)
            prefer = str(os.environ.get("ARGA_EXPORT_3D_MOTOR", "occt")).strip().lower()
            if prefer in ("occt", "arga", "nans") and str(
                os.environ.get("ARGA_EXPORT_3D_ASK", "1")
            ).strip().lower() in ("0", "false", "no", "off"):
                motor_3d = "occt"
                print("[ANS-CPP] Motor 3D forzado OCCT (ARGA_EXPORT_3D_ASK=0)", flush=True)
            else:
                elegido = self._preguntar_motor_3d_export()
                if elegido is None:
                    return
                motor_3d = elegido
                # Si el usuario elige FreeCAD pero el core/OCCT está listo, dejar traza
                if motor_3d == "freecad" and prefer in ("occt", "arga", "nans"):
                    print(
                        "[ANS-CPP] Hint: default recomendado es OCCT "
                        "(ARGA_EXPORT_3D_MOTOR=occt)",
                        flush=True,
                    )
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
            # WO ya escritas en PostgreSQL en esta corrida. Si el export aborta
            # antes de centralizar, se revierten todas.
            wos_persistidas_run = []
            centralizacion_iniciada = False
            db_conf = {}
            job_activo = ""
            es_swo_flag = False
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
                    "host": getattr(config, "NESTING_DB_HOST", "192.168.2.80"),
                    "database": getattr(config, "NESTING_DB_NAME", "nestingpro_db"),
                    "user": getattr(config, "NESTING_DB_USER", "postgres"),
                    "password": getattr(config, "NESTING_DB_PASSWORD", "nesting123"),
                    "port": getattr(config, "NESTING_DB_PORT", "5433"),
                }

                r_base = resolver_ruta_base_exportacion(self.app, modo_servidor=modo_servidor)
                print(f"[EXPORT] r_base = {r_base}")
                os.makedirs(r_base, exist_ok=True)

                rutas_generadas = []
                reiniciar_avisos_lista_largos()
                job_activo = getattr(self.app, 'job_activo', 'JOB').strip().upper()
                es_swo_flag = _es_job_swo(job_activo)
                export_run_id = nuevo_export_run_id()
                self.app.export_run_id = export_run_id
                print(
                    f"[EXPORT][RUN] id={export_run_id} job={job_activo} "
                    f"tipo={'SWO' if es_swo_flag else 'WO'}"
                )

                def _registrar_checkpoint(
                    scope_id: str,
                    scope_type: str,
                    stage: str,
                    *,
                    status: str = "OK",
                    detail: str = "",
                    metadata: dict | None = None,
                ):
                    if not modo_servidor:
                        return
                    try:
                        guardar_checkpoint_export(
                            scope_id,
                            scope_type,
                            stage,
                            status=status,
                            run_id=export_run_id,
                            detail=detail,
                            metadata=metadata or {},
                            db_config=db_conf,
                        )
                    except Exception as exc:
                        print(f"[EXPORT][CHECKPOINT][WARN] {stage}: {exc}")

                if not hasattr(self.app, "wo_reales_por_lote") or self.app.wo_reales_por_lote is None:
                    self.app.wo_reales_por_lote = {}

                if modo_servidor:
                    try:
                        consecutivo_base = obtener_siguiente_consecutivo(db_conf)
                    except Exception as exc:
                        raise RuntimeError(
                            "No hay conexión al servidor para asignar una WO oficial. "
                            "La exportación a servidor se canceló para evitar "
                            f"desalinear archivos y PostgreSQL: {exc}"
                        ) from exc
                else:
                    os.makedirs(desktop_nesteos_locales(), exist_ok=True)
                    consecutivo_base = obtener_consecutivo_wo_local()

                if modo_servidor:
                    error_largos = str(getattr(self.app, "plan_largos_error", "") or "").strip()
                    if error_largos:
                        print(
                            "[LARGOS_NESTING][WARN] El precálculo reportó: "
                            f"{error_largos}. Se verificará el plan persistido "
                            "después de registrar la WO."
                        )

                    from modules.nesting_engine.api_client import (
                        preflight_servicios_centralizados,
                    )

                    preflight = preflight_servicios_centralizados(es_swo=es_swo_flag)
                    if not preflight:
                        raise RuntimeError(
                            "No se inició la exportación a servidor porque "
                            f"VSM/ContPAQ no respondieron: {preflight.summary()}"
                        )
                    print(f"[CENTRALIZED] Preflight OK: {preflight.summary()}")

                instancias_swo_por_lote: dict[str, int] = {}

                # 2. EL BUCLE DE PRODUCCIÓN SECUENCIAL
                for i, orden_obj in enumerate(self.app.resultados_multilote):
                    k_val = orden_obj["lote_k"]
                    mini_resultados = orden_obj["data"]

                    if es_swo_flag:
                        try:
                            consecutivo_actual = int(job_activo.replace("SWO-", ""))
                        except Exception:
                            consecutivo_actual = consecutivo_base + i
                    else:
                        consecutivo_actual = consecutivo_base + i

                    qty_str = str(k_val) if k_val != "N/A" else "1"
                    instancia_lote = 1
                    if es_swo_flag:
                        instancia_lote = instancias_swo_por_lote.get(qty_str, 0) + 1
                        instancias_swo_por_lote[qty_str] = instancia_lote

                    n_wo, ruta_absoluta_wo = crear_estructura_carpetas(
                        r_base,
                        consecutivo_actual,
                        qty_str,
                        es_swo=es_swo_flag,
                        modo_local=not modo_servidor,
                        instancia_lote=instancia_lote,
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

                    exportados_lote = self.app.motor_nesting.exportar_resultados_a_dxf(
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
                    self._validar_lote_exportado(
                        exportados_lote,
                        mini_resultados,
                        n_wo,
                    )
                    dxf_done_global[0] += int(lote_dxf_max[0])
                    step_done_global[0] += int(lote_step_max[0])
                    _on_export_progress(
                        mensaje=f"Lote {i + 1} exportado",
                        dxf_done=0,
                        step_done=0 if respuesta_3d else None,
                    )

                    ruta_pdf_auto = self._exportar_pdf_nesting_en_carpeta_export(
                        mini_resultados,
                        ruta_export,
                        n_wo,
                        job_activo=job_activo,
                    )
                    if not os.path.isfile(ruta_pdf_auto):
                        raise RuntimeError(f"{n_wo}: no se creó el reporte PDF oficial.")
                    print(f"[PDF][EXPORT] Reporte automático: {ruta_pdf_auto}")

                    ruta_arganest_auto = self._exportar_arganest_en_carpeta_export(
                        mini_resultados,
                        ruta_export,
                        n_wo,
                        lote_idx=i,
                        k_val=k_val,
                        job_activo=job_activo,
                    )
                    if not os.path.isfile(ruta_arganest_auto):
                        raise RuntimeError(f"{n_wo}: no se creó el workspace .arganest.")
                    print(f"[ARGANEST][EXPORT] Workspace automático: {ruta_arganest_auto}")
                    tipo_checkpoint_lote = "SWO_LOTE" if es_swo_flag else "WO"
                    _registrar_checkpoint(
                        n_wo,
                        tipo_checkpoint_lote,
                        "CAD_ASSETS",
                        detail=f"DXF/STEP/PDF/ARGANEST validados para lote {i + 1}.",
                        metadata={"lote_idx": i, "ruta_export": ruta_export},
                    )

                    # El costeo queda después de validar CAD y artefactos:
                    # evita asientos financieros sin DXF/PDF/.arganest reales.
                    estado_costeo = generar_csv_compras(
                        r_base,
                        n_wo,
                        mini_resultados,
                        ruta_destino=ruta_absoluta_wo,
                        datos_piezas=self.app.datos_partes_actuales,
                        es_swo=es_swo_flag,
                        db_config=db_conf if modo_servidor else None,
                    )
                    if modo_servidor and not bool((estado_costeo or {}).get("ok")):
                        _registrar_checkpoint(
                            n_wo,
                            tipo_checkpoint_lote,
                            "COSTEO_ERP",
                            status="FAILED",
                            detail=str((estado_costeo or {}).get("mensaje") or "Estado desconocido"),
                        )
                        raise ExportStageError(
                            "COSTEO_ERP",
                            f"{n_wo}: no se persistió el costeo/ERP: "
                            f"{(estado_costeo or {}).get('mensaje', 'estado desconocido')}",
                        )
                    _registrar_checkpoint(
                        n_wo,
                        tipo_checkpoint_lote,
                        "COSTEO_ERP",
                        detail=f"Costeo y ERP confirmados para lote {i + 1}.",
                    )

                    if modo_servidor:
                        ok_bd, resultado_bd = guardar_nesting_en_postgresql(
                            job_activo,
                            n_wo,
                            mini_resultados,
                            db_conf,
                            "COMPLETADO" if respuesta_3d else "PENDIENTE",
                            ruta_export,
                            limpiar_previos=(i == 0),
                        )
                        if not ok_bd:
                            _registrar_checkpoint(
                                n_wo,
                                tipo_checkpoint_lote,
                                "POSTGRESQL",
                                status="FAILED",
                                detail=str(resultado_bd),
                            )
                            raise RuntimeError(
                                f"{n_wo}: PostgreSQL no confirmó el nesting/PQART: {resultado_bd}"
                            )
                        _registrar_checkpoint(
                            n_wo,
                            tipo_checkpoint_lote,
                            "POSTGRESQL",
                            detail=f"PQART y reporte de corte confirmados ({resultado_bd} pieza(s)).",
                        )
                        wos_persistidas_run.append(str(n_wo))

                    # La WO queda visible solo cuando los DXF y PostgreSQL ya
                    # coinciden; evita que el UI anuncie una orden inexistente.
                    self.app.wo_reales_por_lote[i] = str(n_wo)

                    if modo_servidor and not es_swo_flag:
                        from interface.largos_nesting_service import aplicar_pedido_largos_tras_export

                        ok_ldg, msg_ldg = aplicar_pedido_largos_tras_export(
                            self.app,
                            i,
                            str(n_wo),
                            "WO",
                        )
                        if not ok_ldg:
                            _registrar_checkpoint(
                                n_wo,
                                tipo_checkpoint_lote,
                                "MRL",
                                status="FAILED",
                                detail=msg_ldg,
                            )
                            raise RuntimeError(
                                f"{n_wo}: no se confirmó el pedido de largos: {msg_ldg}"
                            )
                        print(f"[LARGOS_NESTING][EXPORT] WO {n_wo} lote={i}: {msg_ldg}")
                        _registrar_checkpoint(
                            n_wo,
                            tipo_checkpoint_lote,
                            "MRL",
                            detail=msg_ldg,
                        )

                    rutas_generadas.append(ruta_absoluta_wo)

                # --- FIN DEL BUCLE MULTI-LOTE ---

                if modo_servidor:
                    if es_swo_flag:
                        from interface.largos_nesting_service import (
                            aplicar_pedido_largos_swo_acumulado_tras_export,
                            validar_mrl_swo_canonica_tras_export,
                        )

                        ok_ldg, msg_ldg = aplicar_pedido_largos_swo_acumulado_tras_export(
                            self.app,
                            job_activo,
                            list(range(len(self.app.resultados_multilote))),
                        )
                        if not ok_ldg:
                            _registrar_checkpoint(
                                job_activo,
                                "SWO",
                                "MRL",
                                status="FAILED",
                                detail=msg_ldg,
                            )
                            raise RuntimeError(
                                f"SWO {job_activo}: no se confirmó el pedido acumulado de largos: "
                                f"{msg_ldg}"
                            )
                        ok_mrl, msg_mrl = validar_mrl_swo_canonica_tras_export(job_activo)
                        if not ok_mrl:
                            _registrar_checkpoint(
                                job_activo,
                                "SWO",
                                "MRL",
                                status="FAILED",
                                detail=msg_mrl,
                            )
                            raise ExportStageError("MRL", msg_mrl)
                        print(f"[LARGOS_NESTING][EXPORT] SWO {job_activo}: {msg_ldg}")
                        _registrar_checkpoint(
                            job_activo,
                            "SWO",
                            "MRL",
                            detail=f"{msg_ldg} {msg_mrl}",
                        )

                    centralizacion_iniciada = True
                    self._centralizar_exportacion_confirmada(
                        db_conf=db_conf,
                        job_activo=job_activo,
                        es_swo=es_swo_flag,
                        reporte_swo=self._consolidar_snapshot_swo() if es_swo_flag else None,
                        run_id=export_run_id,
                    )

                total_carpetas = len(rutas_generadas)

                if not modo_servidor and total_carpetas > 0:
                    guardar_consecutivo_wo_local(consecutivo_base + total_carpetas - 1)

                if modo_servidor:
                    mensaje_final = f"Se exportaron {total_carpetas} Órdenes de Trabajo separadas."
                else:
                    mensaje_final = (
                        f"Se exportaron {total_carpetas} lotes en modo local.\n"
                        f"Carpeta base: {desktop_nesteos_locales()}\n\n"
                        "No se envió información a PostgreSQL ni al servidor centralizado."
                    )

                wos_sin_largos = obtener_wos_sin_lista_largos()
                if wos_sin_largos:
                    mensaje_final += (
                        "\n\nSin lista de largos (no se generó pedido MRL):\n"
                        + "\n".join(f"  • {w}" for w in wos_sin_largos)
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
                if not centralizacion_iniciada and wos_persistidas_run:
                    revertidas = self._revertir_wos_persistidas(
                        db_conf,
                        wos_persistidas_run,
                        job_activo,
                        es_swo_flag,
                    )
                    if revertidas:
                        self.app.wo_reales_por_lote = {}
                        mensaje_error += (
                            "\n\nNo quedó ninguna orden a medias: se revirtieron "
                            + ", ".join(revertidas)
                            + "."
                        )
                    else:
                        mensaje_error += (
                            "\n\nATENCIÓN: no se pudieron revertir las órdenes ya "
                            "escritas en PostgreSQL ("
                            + ", ".join(str(w) for w in wos_persistidas_run)
                            + "). Revísalas antes de reexportar."
                        )
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
