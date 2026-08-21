from __future__ import annotations
"""Métodos de cálculo, ejecución y re-nesteo para TabNesting."""


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
from modules.nesting_engine.cut_gaps_table import PLATE_TO_PIECE_DEFAULT_IN
COLOR_TARJETA = "#FFFFFF"
COLOR_BORDE = "#CBD5E1"
COLOR_GRIS_DARK = "#1E293B"
COLOR_GRIS_MED = "#475569"
COLOR_TEXTO_TITULO = "#0F172A"
COLOR_TEXTO_SECUNDARIO = "#64748B"
# Fallbacks UI; el nest real por grupo siempre sobrescribe con gaps_for_calibre.
DEFAULT_KERF_IN = 0.15
DEFAULT_MARGIN_IN = PLATE_TO_PIECE_DEFAULT_IN



class NestingCalcMixin:
    """Métodos de cálculo, ejecución y re-nesteo para TabNesting."""

    def _gaps_tabla_para_renest(self, clave, hoja=None) -> tuple[float, float]:
        """Kerf/margen oficiales para renestear (nunca UI 0.15 si la tabla pide más).

        Acero: ``gaps_for_calibre`` / ``gaps_efectivos_para_hoja``.
        Cobre: (0, 0).
        """
        from modules.nesting_engine.cut_gaps_table import (
            PLATE_TO_PIECE_DEFAULT_IN,
            gaps_efectivos_para_hoja,
            gaps_for_calibre,
        )

        if self._es_grupo_cobre(clave) or (
            isinstance(hoja, dict) and hoja.get("modo_largos_cu")
        ):
            return 0.0, 0.0
        cal = ""
        clv = str(clave or "").strip()
        if "_" in clv:
            cal = clv.split("_", 1)[0].strip()
        if cal:
            try:
                return gaps_for_calibre(cal)[:2]
            except Exception:
                pass
        try:
            return gaps_efectivos_para_hoja(
                hoja if isinstance(hoja, dict) else None,
                clave=clv,
                kerf_fallback=getattr(self, "global_kerf_val", DEFAULT_KERF_IN),
                margin_fallback=getattr(
                    self, "global_margin_val", PLATE_TO_PIECE_DEFAULT_IN
                ),
            )
        except Exception:
            pass
        try:
            k = self._kerf_efectivo()
        except Exception:
            k = DEFAULT_KERF_IN
        try:
            m = self._margin_efectivo()
        except Exception:
            m = PLATE_TO_PIECE_DEFAULT_IN
        return float(k), float(m)

    def _bloquear_si_dxf_no_apto(self, *, titulo: str = "DXF no aptos (poka-yoke)") -> bool:
        """
        True = hay que abortar (DXF malo / auditoría pendiente).
        Obliga a reparar o cambiar el DXF en PARTS antes de nest/renest.
        """
        try:
            from modules.nesting_engine.nest_poka_yoke import (
                validar_auditoria_dxf_antes_nest,
            )

            ok_dxf, msg_dxf = validar_auditoria_dxf_antes_nest(
                getattr(self.app, "dxf_nesting_audit", None),
                pending=bool(getattr(self.app, "dxf_audit_pending", False)),
            )
            if not ok_dxf:
                QMessageBox.critical(self, titulo, msg_dxf)
                return True
        except Exception as exc:
            QMessageBox.critical(
                self,
                titulo,
                f"No se pudo validar la auditoría de DXF:\n{exc}\n\n"
                "Vuelva a PARTS, revise VER OMITIDOS y repare/cambie el DXF.",
            )
            return True
        return False

    def _poka_yoke_stock_grupos_ok(
        self,
        lista_partes,
        datos_placas=None,
        *,
        solo_claves=None,
        titulo: str = "Stock por calibre (poka-yoke)",
    ) -> bool:
        """True si se puede continuar; False si ya mostró el diálogo de bloqueo."""
        try:
            from modules.nesting_engine.nest_poka_yoke import (
                validar_stock_por_grupos_antes_nest,
            )

            placas = datos_placas
            if placas is None:
                placas = self.app.plates_manager.obtener_datos_placas()
            ok, msg = validar_stock_por_grupos_antes_nest(
                lista_partes,
                placas,
                coinciden=self.app.motor_nesting._coinciden,
                solo_claves=set(solo_claves) if solo_claves else None,
            )
            if not ok:
                QMessageBox.critical(self, titulo, msg)
                return False
            return True
        except Exception as exc:
            QMessageBox.critical(
                self,
                titulo,
                f"No se pudo validar stock por calibre antes de nestear:\n{exc}",
            )
            return False

    def _finalizar_renesteo_lote(self, nuevo_resultado, *, backup_resultados=None):
        if hasattr(self.app, 'cerrar_ventana_carga'):
            self.app.cerrar_ventana_carga()

        if not isinstance(nuevo_resultado, dict) or not nuevo_resultado:
            return QMessageBox.critical(self, "Error", "El renesteo no devolvió un resultado válido.")

        if not hasattr(self.app, "resultados_multilote") or not self.app.resultados_multilote:
            return QMessageBox.critical(self, "Error", "No existe un lote activo para sustituir.")

        # Poka-yoke: rechazar grupos con error / inventario incompleto / solapes.
        errores = []
        for clave, info in nuevo_resultado.items():
            if not isinstance(info, dict):
                continue
            if info.get("error"):
                errores.append(f"{clave}: {info.get('error')}")
            elif info.get("inventario_incompleto"):
                errores.append(f"{clave}: {info.get('advertencia') or 'inventario incompleto'}")
        if errores:
            texto = "\n\n".join(errores[:6])
            if len(errores) > 6:
                texto += f"\n\n(+{len(errores) - 6} más)"
            return QMessageBox.critical(
                self,
                "Renesteo rechazado (poka-yoke)",
                "No se aplicó el renesteo del lote: hay grupos incompletos o con error.\n\n"
                f"{texto}",
            )

        try:
            from modules.nesting_engine.nest_poka_yoke import validar_solapes_hojas_fail_closed

            for clave, info in nuevo_resultado.items():
                if not isinstance(info, dict) or info.get("error"):
                    continue
                ok_s, msg_s = validar_solapes_hojas_fail_closed(info.get("hojas") or [])
                if not ok_s:
                    return QMessageBox.critical(
                        self,
                        "Renesteo rechazado (poka-yoke)",
                        f"Solapes / validación metal en {clave}:\n{msg_s}",
                    )
        except Exception as exc:
            return QMessageBox.critical(
                self,
                "Renesteo rechazado (poka-yoke)",
                f"No se pudo validar solapes (fail-closed):\n{exc}",
            )

        self.app.resultados_multilote[self.lote_actual_idx]["data"] = nuevo_resultado
        self.app.resultados_nesting = nuevo_resultado
        self.app.lote_editado_dirty = False
        # No reactivar sync a gemelas: cada WO se renestea sola.
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

        if self._bloquear_si_dxf_no_apto(titulo="DXF no aptos — no se puede renestear"):
            return

        km = self._leer_kerf_margin_ui()
        if km is None:
            return
        kerf_ui, margin_ui = km

        if not self._poka_yoke_stock_grupos_ok(
            lote_inputs,
            titulo="Renesteo lote — stock (poka-yoke)",
        ):
            return

        backup_resultados = copy.deepcopy(getattr(self.app, "resultados_nesting", None))

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
                if not datos_placas:
                    raise RuntimeError(
                        "Sin placas DISPONIBLE en inventario (poka-yoke). "
                        "Revise Herinox / stock antes de renestear."
                    )
                wo_act = str(getattr(self.app, 'job_activo', 'PENDIENTE')).strip().upper() or "PENDIENTE"

                nuevo_resultado = self.app.motor_nesting.ejecutar_nesting_visual(
                    lote_inputs,
                    datos_placas,
                    progress_callback=receptor_en_vivo,
                    config_kerf=kerf_ui,
                    config_margin=margin_ui,
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

                self.app.after(
                    0,
                    lambda r=nuevo_resultado, b=backup_resultados: self._finalizar_renesteo_lote(
                        r, backup_resultados=b
                    ),
                )

            except Exception as e:
                msg = str(e)

                def throw_err():
                    if hasattr(self.app, 'cerrar_ventana_carga'):
                        self.app.cerrar_ventana_carga()
                    if backup_resultados is not None:
                        self.app.resultados_nesting = backup_resultados
                    QMessageBox.critical(self, "Error", f"No se pudo renestear el lote activo:\n{msg}")

                self.app.after(0, throw_err)

        threading.Thread(target=worker, daemon=True).start()

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
        especiales = getattr(self.app, "cu_especial_por_ruta", None) or {}
        self.app.motor_nesting.cu_especial_por_ruta = {
            str(k): bool(v) for k, v in dict(especiales).items() if v
        }
        plasma = getattr(self.app, "plasma_compensada_por_ruta", None) or {}
        self.app.motor_nesting.plasma_compensada_por_ruta = {
            str(k): bool(v) for k, v in dict(plasma).items() if v
        }
        plasma_dxf = getattr(self.app, "plasma_dxf_por_ruta", None) or {}
        self.app.motor_nesting.plasma_dxf_por_ruta = {
            str(k): str(v) for k, v in dict(plasma_dxf).items() if v
        }
        orient_corte = getattr(self.app, "orientacion_corte_por_ruta", None) or {}
        self.app.motor_nesting.orientacion_corte_por_ruta = {
            str(k): int(v) % 360 for k, v in dict(orient_corte).items()
        }
        bloqueo = getattr(self.app, "orientacion_corte_bloqueada_por_ruta", None) or {}
        self.app.motor_nesting.orientacion_corte_bloqueada_por_ruta = {
            str(k): bool(v) for k, v in dict(bloqueo).items() if v
        }

    def ejecutar_nesting(self):
        if not self.app.datos_partes_actuales:
            return QMessageBox.warning(self, "Atención", "No hay piezas importadas.")

        # Poka-yoke INPUT: no Run si DXF omitidos / auditoría pendiente.
        if self._bloquear_si_dxf_no_apto(titulo="DXF no aptos — no se puede nestear"):
            return

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

        # Kerf/margin ANTES de abrir ventana de carga (no gastar UI en config inválida).
        km = self._leer_kerf_margin_ui()
        if km is None:
            return
        kerf_ui, margin_ui = km
        self.global_margin_val = margin_ui

        # Poka-yoke stock: sin placas = bloqueo; cache vieja = confirmación.
        try:
            from modules.nesting_engine.nest_poka_yoke import validar_stock_antes_nest

            datos_chk = self.app.plates_manager.obtener_datos_placas()
            ok_st, msg_st, nivel_st = validar_stock_antes_nest(datos_chk)
            if not ok_st:
                return QMessageBox.critical(
                    self, "Stock insuficiente (poka-yoke)", msg_st
                )
            if nivel_st == "warn" and msg_st:
                resp_st = QMessageBox.question(
                    self,
                    "Stock posiblemente desactualizado",
                    f"{msg_st}\n\n¿Continuar de todos modos?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if resp_st != QMessageBox.StandardButton.Yes:
                    return
        except Exception as exc:
            return QMessageBox.critical(
                self,
                "Stock (poka-yoke)",
                f"No se pudo validar inventario de placas antes de nestear:\n{exc}",
            )

        # Poka-yoke por calibre/material (stock usable) ANTES de gastar el nest.
        if not self._poka_yoke_stock_grupos_ok(
            self._datos_partes_activos_para_nesting(),
            getattr(self.app.plates_manager, "obtener_datos_placas", lambda: [])(),
            titulo="Stock por calibre (poka-yoke)",
        ):
            return

        plate_selection = None
        if not self._wo_solo_cobre():
            try:
                from modules.nesting_engine.nest_engine_context import ENGINE_SVGNEST_ULTRA

                steel_eid = self._steel_engine_id_para_nesteo()
                if str(steel_eid) == ENGINE_SVGNEST_ULTRA:
                    from interface.qt.dialogs.nesting_modals import (
                        preguntar_seleccion_placas_nesting,
                    )

                    datos_ui = self.app.plates_manager.obtener_datos_placas()
                    plate_selection = preguntar_seleccion_placas_nesting(
                        self,
                        datos_ui,
                        engine_label="SVGNest Ultra",
                        datos_partes=getattr(self.app, "datos_partes_actuales", None),
                    )
                    if plate_selection is None:
                        return
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Selección de placas",
                    f"No se pudo abrir el selector de placas:\n{exc}",
                )
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

        opt_ui = self.cmb_opt.currentText() if hasattr(self, "cmb_opt") else "OPTIMIZAR LARGO Y ANCHO"

        threading.Thread(
            target=self.thread_worker,
            args=(
                T,
                margin_ui,
                self.global_corner_val,
                kerf_ui,
                opt_ui,
                plate_selection,
            ),
            daemon=True
        ).start()

    def thread_worker(self, T, margin_val, corner_val, kerf_val, opt_val, plate_selection=None):
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
            if plate_selection and plate_selection.get("mode") == "manual":
                from interface.qt.dialogs.nesting_modals import (
                    filtrar_datos_placas_nest_selection,
                )

                datos_placas = filtrar_datos_placas_nest_selection(
                    datos_placas, plate_selection
                )

            steel_engine_id = None
            if not self._wo_solo_cobre():
                steel_engine_id = self._steel_engine_id_para_nesteo()
                try:
                    from modules.nesting_engine.engine_registry import get_engine_meta

                    meta = get_engine_meta(steel_engine_id)
                    receptor_en_vivo(f"Motor: {meta.display_name}", 0.01)
                except Exception:
                    pass

            # Cobre 100%: barras largas deterministas -> nesteo directo, sin
            # análisis de lotes MES (los escenarios optimizan costo de placa,
            # que no aplica al cobre). La cantidad se coloca tal cual.
            if T < 4 or self._wo_solo_cobre():
                datos_base = self._clonar_datos_partes_edicion(
                    getattr(self.app, "datos_partes_actuales", [])
                )

                self._inputs_precalculados_por_k[int(T)] = self._clonar_datos_partes_edicion(datos_base)

                nest_kwargs = dict(
                    progress_callback=receptor_en_vivo,
                    config_kerf=kerf_val,
                    config_margin=margin_val,
                    config_corner=corner_val,
                    config_opt=opt_val,
                    wo_name=wo_act,
                )
                if steel_engine_id:
                    nest_kwargs["engine_id"] = steel_engine_id
                if plate_selection is not None:
                    nest_kwargs["plate_selection"] = plate_selection

                res = self.app.motor_nesting.ejecutar_nesting_visual(
                    datos_base,
                    datos_placas,
                    **nest_kwargs,
                )

                if _abortar_si_cancelado():
                    return
                if isinstance(res, dict) and res.get("error") == "Operación cancelada por el usuario.":
                    self.app.after(0, self.restaurar_controles_tras_cancelacion)
                    return

                self._propagar_auditoria_dxf_a_parts(res)

                self.app.tiempo_calculo = time.time() - tiempo_inicio
                lista_unica = [{"lote_k": T, "data": res}]
                # Poka-yoke: si el nest abortó (p. ej. DXF), no gastar en largos/display.
                if isinstance(res, dict) and res.get("error"):
                    self.app.after(0, lambda rl=lista_unica: self.finalizar(rl))
                    return
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

                nest_kwargs_lote = dict(
                    progress_callback=cb_wrapper,
                    config_kerf=kerf_val,
                    config_margin=margin_val,
                    config_corner=corner_val,
                    config_opt=opt_val,
                    wo_name=wo_act,
                )
                if steel_engine_id:
                    nest_kwargs_lote["engine_id"] = steel_engine_id
                if plate_selection is not None:
                    nest_kwargs_lote["plate_selection"] = plate_selection
                nestings_precalculados[k] = self.app.motor_nesting.ejecutar_nesting_visual(
                    datos_k,
                    datos_placas,
                    **nest_kwargs_lote,
                )
                self._propagar_auditoria_dxf_a_parts(nestings_precalculados[k])
                if _abortar_si_cancelado():
                    return
                # Poka-yoke: un k con error de geometría/DXF/integridad no debe seguir anidando.
                res_k = nestings_precalculados[k]
                if isinstance(res_k, dict) and res_k.get("error"):
                    self.app.tiempo_calculo = time.time() - tiempo_inicio
                    lista_fail = [{"lote_k": k, "data": res_k}]
                    self.app.after(0, lambda rl=lista_fail: self.finalizar(rl))
                    return
                try:
                    from modules.nesting_engine.nest_poka_yoke import (
                        listar_fallas_resultados_nest,
                    )

                    fallas_k = listar_fallas_resultados_nest(res_k)
                except Exception as exc:
                    fallas_k = [f"No se pudo auditar integridad del lote k={k}: {exc}"]
                if fallas_k:
                    self.app.tiempo_calculo = time.time() - tiempo_inicio
                    lista_fail = [{"lote_k": k, "data": res_k}]
                    self.app.after(0, lambda rl=lista_fail: self.finalizar(rl))
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

        try:
            item0 = (resultados_list or [{}])[0] or {}
            engine_id = item0.get("nest_engine_id")
            if engine_id:
                from modules.nesting_engine.engine_registry import get_engine_meta

                meta = get_engine_meta(engine_id)
                mensaje = (
                    f"Motor seleccionado: {meta.display_name}\n\n" + mensaje
                )
        except Exception:
            pass

        avisos = []
        bloqueantes = []
        for item in resultados_list or []:
            data = (item or {}).get("data")
            if isinstance(data, dict) and data.get("error"):
                bloqueantes.append(str(data.get("error")))
                continue
            if not isinstance(data, dict):
                continue
            for clave, info in data.items():
                if not isinstance(info, dict):
                    continue
                if info.get("error") or info.get("inventario_incompleto"):
                    bloqueantes.append(
                        f"{clave}: {info.get('error') or info.get('advertencia')}"
                    )
                elif info.get("advertencia"):
                    avisos.append(f"{clave}: {info.get('advertencia')}")

        if bloqueantes:
            texto = "\n\n".join(bloqueantes[:5])
            if len(bloqueantes) > 5:
                texto += f"\n\n(+{len(bloqueantes) - 5} más)"
            QMessageBox.critical(
                self,
                "Nest incompleto (poka-yoke)",
                f"{mensaje}\n\n"
                "Hay grupos rechazados o incompletos. La exportación a corte "
                "quedará bloqueada hasta corregirlos.\n\n"
                f"{texto}",
            )
        elif avisos:
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

    def _steel_engine_id_para_nesteo(self) -> str:
        """Motor elegido en FILES; se aplica al inicio de cada corrida de acero."""
        from modules.nesting_engine.nest_engine_config import apply_saved_steel_engine

        motor = getattr(self.app, "motor_nesting", None)
        return apply_saved_steel_engine(motor=motor)

    def _es_engine_svgnest_ultra(self, engine_id=None) -> bool:
        from modules.nesting_engine.nest_engine_context import (
            ENGINE_SVGNEST_ULTRA,
            get_active_engine_id,
            normalize_engine_id,
        )

        eid = engine_id if engine_id is not None else get_active_engine_id()
        return normalize_engine_id(eid) == ENGINE_SVGNEST_ULTRA

    def _abrir_carga_renest(self, titulo: str, *, engine_id=None) -> bool:
        """Abre popup de carga con tiempo; si Ultra, habilita «Aceptar mejor actual»."""
        ultra = self._es_engine_svgnest_ultra(engine_id)
        if hasattr(self.app, "abrir_ventana_carga"):
            try:
                self.app.abrir_ventana_carga(titulo, ultra_accept=ultra)
            except TypeError:
                self.app.abrir_ventana_carga(titulo)
        return ultra

    def _ctx_ultra_renest_enter(self, ultra: bool):
        """Activa accept-mode Ultra + callback UI. Retorna tokens a limpiar."""
        if not ultra:
            return None, None
        from interface.qt.thread_bridge import call_on_main
        from modules.nesting_engine.nest_engine_context import (
            set_ultra_best_callback,
            set_ultra_renest_accept_mode,
        )

        tok_mode = set_ultra_renest_accept_mode(True)

        def _on_best(resumen: str = ""):
            if hasattr(self.app, "notificar_mejor_nest_listo"):
                call_on_main(self.app.notificar_mejor_nest_listo, resumen)

        tok_cb = set_ultra_best_callback(_on_best)
        return tok_mode, tok_cb

    def _ctx_ultra_renest_exit(self, tok_mode, tok_cb):
        from modules.nesting_engine.nest_engine_context import (
            reset_ultra_best_callback,
            reset_ultra_renest_accept_mode,
        )

        if tok_cb is not None:
            reset_ultra_best_callback(tok_cb)
        if tok_mode is not None:
            reset_ultra_renest_accept_mode(tok_mode)

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
            menu.addAction(
                "RENESTEAR POR BARRA",
                self._safe_ctx(
                    "Renestear por barra cobre",
                    lambda c=clave: self._renestear_por_barra_cobre_ui(c),
                ),
            )
            menu.exec(widget.mapToGlobal(pos))

        for w in (header, lbl_header):
            w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            w.customContextMenuRequested.connect(lambda pos, ww=w: show_menu(pos, ww))

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

    def _bind_menu_renestear_calibre_acero(self, header, lbl_header, clave):
        def show_menu(pos, widget):
            if not self._ctx_tiene_resultados(clave):
                return
            menu = QMenu(self)
            sub_renest = QMenu("Renestear calibre completo", menu)
            sub_renest.aboutToShow.connect(
                lambda sm=sub_renest, c=clave: self._rellenar_submenu_renest_calibre(sm, c)
            )
            menu.addMenu(sub_renest)
            menu.exec(widget.mapToGlobal(pos))

        for w in (header, lbl_header):
            w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            w.customContextMenuRequested.connect(lambda pos, ww=w: show_menu(pos, ww))

    def _bind_menu_compensar_calibre(self, header, lbl_header, clave):
        """Compat: ya no ofrece compensación (solo PARTS). Renesteo de calibre."""
        self._bind_menu_renestear_calibre_acero(header, lbl_header, clave)

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
            # RTZCU virtual duplica piezas ya presentes en la madre.
            if isinstance(hoja, dict) and hoja.get("cu_rtz_virtual"):
                continue
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
        # Exacto primero, sinónimo (_coinciden) solo si no hay fila exacta del nombre.
        exactas = {}
        sinonimos = {}
        mat_clave = str(material_hoja or "").strip().upper()
        for p_nom, mat, qty, cal, st, ruta in self._datos_partes_activos_para_nesting():
            nom = self._nombre_canonico_pieza(p_nom)
            if not nom:
                continue
            if not self.app.motor_nesting._coinciden(calibre_hoja, cal):
                continue
            mat_u = str(mat or "").strip().upper()
            if mat_u == mat_clave:
                bucket = exactas
            elif self.app.motor_nesting._coinciden(material_hoja, mat):
                bucket = sinonimos
            else:
                continue
            try:
                q = max(0, int(qty or 0))
            except Exception:
                q = 0
            if q <= 0:
                continue
            bucket[nom] = bucket.get(nom, 0) + q
        for nom, q in exactas.items():
            conteo[nom] = q
        for nom, q in sinonimos.items():
            if nom not in conteo:
                conteo[nom] = q
        return conteo

    def _grupo_inventario_incompleto(self, clave) -> bool:
        grupo = (getattr(self.app, "resultados_nesting", None) or {}).get(clave) or {}
        if not isinstance(grupo, dict):
            return False
        if grupo.get("inventario_ok") is False:
            return True
        msg = str(grupo.get("advertencia") or "").lower()
        return (
            "inventario incompleto" in msg
            or "faltan" in msg
            or "incompleta" in msg
        )

    def _conteo_para_renest_calibre(self, clave):
        """Demanda para renesteo de calibre.

        Por defecto conserva cantidades DEL NEST (evita inflar).
        Si el grupo ya está incompleto vs PARTS (edición manual / fusión rota),
        restaura con max(job, nest) para recuperar piezas perdidas.
        """
        conteo_job = self._conteo_piezas_job_grupo(clave)
        conteo_nido = self._contar_piezas_reales_grupo(clave)
        if self._grupo_inventario_incompleto(clave) and conteo_job:
            nombres = set(conteo_job) | set(conteo_nido)
            out = {}
            for nom in nombres:
                tot = max(int(conteo_job.get(nom, 0) or 0), int(conteo_nido.get(nom, 0) or 0))
                if tot > 0:
                    out[nom] = tot
            return out
        return conteo_nido if conteo_nido else conteo_job

    def _poly_desrotar_a_ejes(self, poly, marks=None):
        """
        Quita rotación de pose del nest (p. ej. 45° horneada) para que FORCE
        trabaje con orientación nativa; solo aplica 0/90/180/270 después.
        """
        import math

        from shapely import affinity

        if poly is None or getattr(poly, "is_empty", True):
            return poly, marks
        try:
            mrr = poly.minimum_rotated_rectangle
            coords = list(mrr.exterior.coords)
            if len(coords) < 3:
                return poly, marks
            dx = float(coords[1][0] - coords[0][0])
            dy = float(coords[1][1] - coords[0][1])
            ang = math.degrees(math.atan2(dy, dx))
            # Snap al múltiplo de 90° más cercano → deshacer solo el sesgo (45°, etc.).
            snap = round(ang / 90.0) * 90.0
            delta = ang - snap
            if abs(delta) < 1.0:
                return poly, marks
            origin = "centroid"
            poly_u = affinity.rotate(poly, -delta, origin=origin, use_radians=False)
            marks_u = marks
            if marks is not None and not getattr(marks, "is_empty", True):
                marks_u = affinity.rotate(marks, -delta, origin=origin, use_radians=False)
            return poly_u, marks_u
        except Exception:
            return poly, marks

    def _construir_fuente_geometria_por_nombre(
        self, clave, nombres_requeridos=None, *, prefer_dxf=False
    ):
        """
        Mapa nombre->geometría base para renest/compensar.

        Orden por defecto (rápido + orientación correcta):
        1) Nest en memoria (sin red) + desrotar pose (quita 45° horneado).
        2) DXF solo de nombres que aún falten.

        Con prefer_dxf=True (reparación tras reprocesar AutoDXF):
        1) DXF actual de PARTS.
        2) Nest en memoria solo si falta el DXF.
        """
        material_hoja = clave.split("_")[1] if "_" in clave else clave
        calibre_hoja = clave.split("_")[0] if "_" in clave else ""
        fuente = {}
        req = None
        if nombres_requeridos:
            req = {self._nombre_canonico_pieza(n) for n in nombres_requeridos}
            req.discard("")

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

        def _agregar_fuente(nom, poly, marks, cal, mat, ruta, *, desrotar=False):
            canon = self._nombre_canonico_pieza(nom)
            if not canon or canon in fuente or poly is None or getattr(poly, "is_empty", True):
                return
            if req is not None and canon not in req:
                return
            from shapely import affinity
            from interface.utils_nesting import clave_orientacion_cobre_ruta, es_material_cobre

            poly_use = poly
            marks_use = marks
            if desrotar:
                poly_use, marks_use = self._poly_desrotar_a_ejes(poly_use, marks_use)
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

        def _cargar_desde_nest():
            grp = (self.app.resultados_nesting or {}).get(clave) or {}
            for hoja in (grp.get("hojas") or []):
                for p in (hoja.get("piezas") or []):
                    nom = str(p.get("nombre", "")).strip()
                    canon = self._nombre_canonico_pieza(nom)
                    if not canon or self._es_pieza_virtual(nom) or canon in fuente:
                        continue
                    if req is not None and canon not in req:
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
                            desrotar=True,
                        )
                    except Exception:
                        continue

        def _cargar_desde_dxf(*, solo_faltantes=True):
            faltan = None
            if solo_faltantes:
                if req is not None:
                    faltan = {n for n in req if n not in fuente}
                    if not faltan:
                        return
                # sin req: solo nombres que aún no están en fuente
            mat_clave = str(material_hoja or "").strip().upper()
            filas = list(self._datos_partes_activos_para_nesting())

            def _cargar_pass(*, exacto: bool):
                nonlocal faltan
                for p_nom, mat, qty, cal, st, ruta in filas:
                    nom = str(p_nom or "").strip()
                    canon = self._nombre_canonico_pieza(nom)
                    if not canon or canon in fuente:
                        continue
                    if faltan is not None and canon not in faltan:
                        continue
                    if req is not None and canon not in req:
                        continue
                    if not self.app.motor_nesting._coinciden(calibre_hoja, cal):
                        continue
                    mat_u = str(mat or "").strip().upper()
                    if exacto:
                        if mat_u != mat_clave:
                            continue
                    else:
                        if mat_u == mat_clave:
                            continue
                        if not self.app.motor_nesting._coinciden(material_hoja, mat):
                            continue
                    poly, marks = self.app.motor_nesting.recuperar_geometria_robusta(ruta)
                    if not poly:
                        continue
                    _agregar_fuente(nom, poly, marks, cal, mat, ruta, desrotar=False)
                    if faltan is not None:
                        faltan.discard(canon)
                        if not faltan:
                            return

            _cargar_pass(exacto=True)
            _cargar_pass(exacto=False)

        if prefer_dxf:
            _cargar_desde_dxf(solo_faltantes=False)
            _cargar_desde_nest()
        else:
            _cargar_desde_nest()
            _cargar_desde_dxf(solo_faltantes=True)

        print(
            f"[GEOM-FUENTE] clave={clave} | nest+dxf={len(fuente)}"
            + (f" | req={len(req)}" if req is not None else "")
            + (f" | prefer_dxf={prefer_dxf}" if prefer_dxf else ""),
            flush=True,
        )
        return fuente

    def _pieza_pack_desde_fuente(
        self,
        src,
        *,
        forzar_compensacion_plasma: bool = False,
        offset_mm_forzado=None,
    ):
        """Reconstruye una pieza y conserva su compensación seleccionada en PARTS.

        El estado de PARTS es la fuente de verdad. Así cualquier operación que
        reconstruya geometría (renest, cambio de placa o calibre) vuelve a usar
        el DXF compensado en lugar de degradar silenciosamente al DXF base.
        """
        from interface.utils_nesting import clave_orientacion_cobre_ruta, es_material_cobre
        from modules.plasma_compensator import (
            asegurar_dxf_plasma_compensado,
            compute_plasma_offset_mm,
        )

        ruta = str(src.get("ruta") or "").strip()
        material = src.get("material", "")
        calibre = src.get("calibre", "")
        clave_ruta = clave_orientacion_cobre_ruta(ruta) if ruta else ""
        plasma_parts = bool(
            ruta
            and not es_material_cobre(material)
            and (getattr(self.app, "plasma_compensada_por_ruta", None) or {}).get(
                clave_ruta, False
            )
        )
        compensar = plasma_parts or bool(forzar_compensacion_plasma)
        poly = copy.deepcopy(src["poly_base"])
        marks = copy.deepcopy(src["marks_base"])
        offset_mm = 0.0
        ruta_plasma = ""

        if plasma_parts:
            thk = self.app.motor_nesting._parse_thickness_value(calibre)
            if thk is None:
                thk = float(self.app.motor_nesting._extraer_numero(calibre) or 0.0)
            offset_mm = float(compute_plasma_offset_mm(float(thk or 0.0)))
            if offset_mm <= 0:
                raise RuntimeError(
                    f"No se pudo calcular la compensación plasma de {src.get('nombre')!r}."
                )
            if not hasattr(self.app, "plasma_dxf_por_ruta") or self.app.plasma_dxf_por_ruta is None:
                self.app.plasma_dxf_por_ruta = {}
            ruta_plasma, error = asegurar_dxf_plasma_compensado(ruta, offset_mm)
            if not ruta_plasma:
                cached = str(
                    (getattr(self.app, "plasma_dxf_por_ruta", None) or {}).get(
                        clave_ruta, ""
                    )
                    or ""
                )
                if cached and os.path.isfile(cached):
                    # Workspace/renest: el origen puede no estar; reusar el DXF
                    # compensado ya mapeado en PARTS.
                    ruta_plasma, error = cached, ""
            if not ruta_plasma:
                raise RuntimeError(
                    f"No se pudo restaurar compensación plasma de "
                    f"{src.get('nombre')!r}: {error or 'DXF compensado no disponible.'}"
                )
            self.app.plasma_dxf_por_ruta[clave_ruta] = ruta_plasma
            poly_p, marks_p = self.app.motor_nesting.recuperar_geometria_robusta(ruta_plasma)
            if poly_p is None or poly_p.is_empty:
                raise RuntimeError(
                    f"El DXF compensado de {src.get('nombre')!r} no contiene geometría usable."
                )
            poly = poly_p
            if marks_p is not None:
                marks = marks_p
        elif compensar:
            offset_mm = float(
                offset_mm_forzado
                if offset_mm_forzado is not None
                else (self._offset_compensacion_mm_desde_clave(calibre) or 0.0)
            )
            comp = self._aplicar_compensacion_poligono(poly, offset_mm)
            if comp is None or comp.is_empty:
                raise RuntimeError(
                    f"No se pudo aplicar compensación plasma a {src.get('nombre')!r}."
                )
            from shapely import affinity

            mx, my, _, _ = comp.bounds
            poly = affinity.translate(comp, -mx, -my)

        # BLOQUEAR ORIEN (PARTS): hornear rotación y fijar grain_locked.
        # Sin esto, renest calibre/placa reconstruía el DXF en 0° y el motor
        # podía girar la pieza otra vez (solo el nest completo respetaba el candado).
        rot_lock_deg = 0
        orient_bloqueada = False
        if clave_ruta and not es_material_cobre(material):
            orient_bloqueada = bool(
                (getattr(self.app, "orientacion_corte_bloqueada_por_ruta", None) or {}).get(
                    clave_ruta, False
                )
            )
            if orient_bloqueada:
                rot_lock_deg = int(
                    (getattr(self.app, "orientacion_corte_por_ruta", None) or {}).get(
                        clave_ruta, 0
                    )
                    or 0
                ) % 360
                if rot_lock_deg:
                    from shapely import affinity as _aff

                    cx, cy = poly.centroid.x, poly.centroid.y
                    poly = _aff.rotate(poly, rot_lock_deg, origin=(cx, cy), use_radians=False)
                    if marks is not None and not getattr(marks, "is_empty", True):
                        marks = _aff.rotate(
                            marks, rot_lock_deg, origin=(cx, cy), use_radians=False
                        )
                    minx, miny, _, _ = poly.bounds
                    poly = _aff.translate(poly, -minx, -miny)
                    if marks is not None and not getattr(marks, "is_empty", True):
                        marks = _aff.translate(marks, -minx, -miny)

        item = {
            "nombre": src["nombre"],
            "poly": copy.deepcopy(poly),
            "poly_exact": copy.deepcopy(poly),
            "marks": copy.deepcopy(marks),
            "area": float(getattr(poly, "area", 0) or src.get("area_base", 0)),
            "calibre": calibre,
            "material": material,
            "ruta": ruta,
        }
        if orient_bloqueada:
            item["grain_locked"] = True
            item["allowed_rotations"] = [0]
            item["orientacion_corte_bloqueada"] = True
            item["orientacion_corte_deg"] = int(rot_lock_deg) % 360
        if compensar:
            item["plasma_compensada_manual"] = True
            item["plasma_offset_mm_manual"] = float(offset_mm)
            item["plasma_fuente_ya_compensada"] = bool(plasma_parts)
            if ruta_plasma:
                item["ruta_plasma"] = ruta_plasma
        return item

    def _build_piezas_para_renest_compensado(self, clave, cupos_compensar_por_nombre, offset_mm):
        """
        Construye lista completa de piezas del calibre con compensación parcial/global.
        cupos_compensar_por_nombre: dict nombre->cantidad de instancias a compensar.
        """
        conteo_total = self._contar_piezas_reales_grupo(clave)
        if not conteo_total:
            return [], {}
        # PARTS conserva la ruta original necesaria para recuperar el DXF plasma;
        # el nest previo puede venir de una transferencia antigua sin esa ruta.
        fuente = self._construir_fuente_geometria_por_nombre(clave, prefer_dxf=True)
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

                item = {
                    "nombre": src["nombre"],
                    "poly": copy.deepcopy(poly_use),
                    "marks": copy.deepcopy(src["marks_base"]),
                    "area": area_use,
                    "calibre": src["calibre"],
                    "material": src["material"],
                    "ruta": src["ruta"],
                }
                if aplicar_comp:
                    # Este metadata es contrato de seguridad: el polygon que
                    # entra al packer YA es el contorno final de corte. Si se
                    # pierde, al reabrir un .arganest sólo queda una heurística
                    # de bbox que confundía una rotación de 90° con un offset.
                    item["plasma_compensada_manual"] = True
                    item["plasma_offset_mm_manual"] = float(offset_mm)
                    item["plasma_fuente_ya_compensada"] = True
                piezas_out.append(item)
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
            # Un placement puede girar 90°; comparar ancho contra ancho sin
            # normalizar hizo que OP-1010-211 base se marcara "compensada"
            # sólo por tener W/H intercambiados. El desfase agranda ambas
            # dimensiones, no las intercambia.
            w_fin, h_fin = sorted((w_fin, h_fin))
            w_base, h_base = sorted((w_base, h_base))
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
                if self._es_pieza_virtual(nom):
                    continue
                # El packer preserva los campos explícitos desde
                # `_build_piezas_para_renest_compensado`. La heurística queda
                # sólo para resultados legacy; no debe pisar evidencia cierta.
                compensada = bool(p.get("plasma_compensada_manual"))
                if not compensada:
                    compensada = self._pieza_parece_compensada_plasma(
                        p, base_meta, offset_mm
                    )
                if compensada:
                    p["plasma_compensada_manual"] = True
                    p["plasma_offset_mm_manual"] = float(offset_mm or 0.0)
                    p["plasma_fuente_ya_compensada"] = True
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
            k, m = self._gaps_tabla_para_renest(clave)
        except Exception:
            return QMessageBox.critical(self, "Error", "Kerf inválido.")
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

                inv_esperado = self._inventario_piezas_canonico(piezas_pack)
                ok_inv, msg_inv = self._validar_renest_conserva_inventario(
                    inv_esperado, resultado
                )
                if not ok_inv:
                    raise RuntimeError(msg_inv)

                from modules.nesting_engine.nest_poka_yoke import (
                    validar_solapes_hojas_fail_closed,
                )

                ok_s, msg_s = validar_solapes_hojas_fail_closed(
                    resultado.get("hojas") or []
                )
                if not ok_s:
                    raise RuntimeError(
                        "Compensación rechazada (poka-yoke solapes fail-closed):\n"
                        f"{msg_s}"
                    )

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
        return QMessageBox.information(
            self,
            "Plasma",
            "La compensación plasma ya no se hace por placa.\n\n"
            "Márcala en PARTS → columna ESP. (piezas de acero). "
            "Al nestear irán compensadas en placas solo-plasma.",
        )

    def _build_piezas_para_renest_calibre(self, clave):
        # Conservar ambos conteos para informar el origen del renesteo. La
        # demanda efectiva puede restaurar piezas faltantes con max(job,nido).
        conteo_job = self._conteo_piezas_job_grupo(clave)
        conteo_nido = self._contar_piezas_reales_grupo(clave)
        conteo_total = self._conteo_para_renest_calibre(clave)
        if not conteo_total:
            self._renest_calibre_build_info = {}
            return []
        fuente = self._construir_fuente_geometria_por_nombre(clave, prefer_dxf=True)
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
                try:
                    piezas_out.append(self._pieza_pack_desde_fuente(src))
                except RuntimeError:
                    faltantes_geom.append((nom, 1))
        self._renest_calibre_build_info = {
            "conteo_job": conteo_job,
            "conteo_nido": conteo_nido,
            "faltantes_geom": faltantes_geom,
            "total_esperado": sum(int(v) for v in conteo_total.values()),
            "total_generado": len(piezas_out),
            "fuente_conteo": (
                "max(job,nido)"
                if conteo_total not in (conteo_job, conteo_nido)
                else ("nido" if conteo_nido else "job")
            ),
        }
        return piezas_out

    def renestear_calibre_completo_ui(self, clave, *, candidata_placa=None, engine_id=None):
        if not getattr(self.app, "resultados_nesting", None):
            return QMessageBox.warning(self, "Atención", "No hay resultados de nesting.")
        if clave not in self.app.resultados_nesting:
            return QMessageBox.warning(self, "Atención", "No se encontró ese calibre/material.")

        if self._bloquear_si_dxf_no_apto(titulo="DXF no aptos — no se puede renestear"):
            return

        piezas_pack = self._build_piezas_para_renest_calibre(clave)
        build_info = getattr(self, "_renest_calibre_build_info", {}) or {}
        faltantes_geom = build_info.get("faltantes_geom") or []
        if faltantes_geom:
            lineas = "\n".join(f"  · {nom}: {cnt}" for nom, cnt in faltantes_geom[:12])
            extra = f"\n… y {len(faltantes_geom) - 12} más." if len(faltantes_geom) > 12 else ""
            return QMessageBox.warning(
                self,
                "Atención",
                "No se encontró geometría DXF para todas las piezas a renestear:\n"
                f"{lineas}{extra}",
            )
        if not piezas_pack:
            return QMessageBox.warning(self, 
                "Atención",
                "No se pudieron reconstruir las piezas de este calibre para renestear.",
            )
        total_esperado = int(build_info.get("total_esperado") or len(piezas_pack))
        if len(piezas_pack) != total_esperado:
            return QMessageBox.warning(
                self,
                "Atención",
                f"No se pudieron reconstruir todas las piezas del nest.\n"
                f"Esperadas: {total_esperado} · Generadas: {len(piezas_pack)}.\n"
                "Revise PARTS y rutas DXF antes de renestear.",
            )

        # PARTS → motor: bloqueo de orientación debe vivir también en renest parcial.
        self._sync_orientacion_cobre_al_motor()

        # Stock del calibre ANTES de confirmar / gastar renesteo.
        partes_grupo = []
        for p_nom, mat, qty, cal, st, ruta in self._datos_partes_activos_para_nesting():
            if self.app.motor_nesting._coinciden(
                str(clave).split("_", 1)[0], cal
            ) and self.app.motor_nesting._coinciden(
                (str(clave).split("_", 1) + [""])[1], mat
            ):
                partes_grupo.append((p_nom, mat, qty, cal, st, ruta))
        if not partes_grupo:
            # Fallback: sintetizar desde piezas_pack
            from collections import Counter

            cnt = Counter(p.get("nombre") for p in piezas_pack)
            for nom, q in cnt.items():
                src = next((p for p in piezas_pack if p.get("nombre") == nom), {})
                partes_grupo.append(
                    (
                        nom,
                        src.get("material") or "",
                        q,
                        src.get("calibre") or str(clave).split("_", 1)[0],
                        "OK",
                        src.get("ruta") or "",
                    )
                )
        if not self._poka_yoke_stock_grupos_ok(
            partes_grupo,
            solo_claves={str(clave)},
            titulo="Renesteo calibre — stock (poka-yoke)",
        ):
            return

        total_nido = sum(int(v) for v in (build_info.get("conteo_nido") or {}).values())
        total_job_cnt = sum(int(v) for v in (build_info.get("conteo_job") or {}).values())
        aviso_cantidad = ""
        if total_job_cnt and total_nido and total_job_cnt != total_nido:
            aviso_cantidad = (
                f"\n\nSe renesteará con las {total_nido} piezas del nest actual "
                f"(el job pide {total_job_cnt}; no se inventan piezas extra)."
            )
        es_cobre = self._es_grupo_cobre(clave)
        cu_separacion_in = None
        cu_largo_sin_separacion_in = None
        if es_cobre:
            opts_cu = self._preguntar_opts_renest_cobre(clave)
            if opts_cu is None:
                return
            cu_separacion_in, cu_largo_sin_separacion_in = opts_cu
            detalle = (
                f"Se volverá a optimizar el calibre {clave} en barras largo CU "
                f"(gap {cu_separacion_in:g}\"; Z/especial sin gap)."
                f"{aviso_cantidad}\n\n¿Continuar?"
            )
            titulo = "Renestear cobre en largos"
        else:
            motor_txt = ""
            if engine_id:
                try:
                    from modules.nesting_engine.engine_registry import get_engine_meta

                    motor_txt = f"\n\nMotor: {get_engine_meta(engine_id).display_name}"
                except Exception:
                    motor_txt = f"\n\nMotor: {engine_id}"
            placa_txt = ""
            if candidata_placa:
                placa_txt = (
                    f"\n\nPlaca seleccionada:\n"
                    f"  {self._etiqueta_placa_inventario(candidata_placa)}"
                )
            detalle = (
                f"Se volverá a optimizar todo el calibre {clave} desde cero."
                f"{motor_txt}{placa_txt}{aviso_cantidad}\n\n¿Continuar?"
            )
            titulo = "Renestear calibre completo"
        if QMessageBox.question(self, titulo, detalle) != QMessageBox.StandardButton.Yes:
            return

        # Cobre: parámetros fijos del módulo LARGOS CU (no kerf/margin/opt de motores acero).
        if es_cobre:
            k, m = 0.0, 0.0
            opt, corner = "LARGOS CU", "INFERIOR IZQUIERDA"
        else:
            try:
                k, m = self._gaps_tabla_para_renest(clave)
            except Exception:
                return QMessageBox.critical(self, "Error", "Kerf inválido.")
            opt = (
                self.cmb_opt.currentText()
                if hasattr(self, "cmb_opt")
                else "OPTIMIZAR LARGO Y ANCHO"
            )
            corner = self.global_corner_val

        engine_renest = None if es_cobre else engine_id
        ultra_renest = (not es_cobre) and self._es_engine_svgnest_ultra(engine_renest)
        self._abrir_carga_renest("Renesteando calibre completo...", engine_id=engine_renest)

        sep_cu = cu_separacion_in
        largo_sin_cu = cu_largo_sin_separacion_in

        def worker():
            backup_grp = copy.deepcopy((self.app.resultados_nesting or {}).get(clave))
            # Validar contra las piezas que se mandaron a renestear (nest), no el job.
            inv_esperado = self._inventario_piezas_canonico(
                {n: int(c) for n, c in (build_info.get("conteo_nido") or {}).items()}
            )
            if not inv_esperado:
                from collections import Counter

                inv_esperado = self._inventario_piezas_canonico(
                    dict(Counter(str(p.get("nombre") or "") for p in piezas_pack))
                )
            engine_token = None
            tok_mode = tok_cb = None
            try:
                if engine_renest and not es_cobre:
                    from modules.nesting_engine.nest_engine_context import set_active_engine_id

                    engine_token = set_active_engine_id(engine_renest)
                    if getattr(self.app, "motor_nesting", None) is not None:
                        self.app.motor_nesting.active_engine_id = engine_renest
                tok_mode, tok_cb = self._ctx_ultra_renest_enter(ultra_renest)
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
                if getattr(self.app, "tarea_abortada", lambda: False)():
                    raise RuntimeError("Cancelado por el usuario.")
                resultado = raw[1] if isinstance(raw, tuple) and len(raw) == 2 else raw
                if not isinstance(resultado, dict) or resultado.get("error"):
                    raise RuntimeError(str((resultado or {}).get("error", "Sin resultado válido.")))
                if not (resultado.get("hojas") or []):
                    raise RuntimeError("El renesteo no generó hojas válidas.")

                ok_inv, msg_inv = self._validar_renest_conserva_inventario(inv_esperado, resultado)
                if not ok_inv:
                    raise RuntimeError(msg_inv)

                from modules.nesting_engine.nest_poka_yoke import (
                    validar_solapes_hojas_fail_closed,
                )

                ok_s, msg_s = validar_solapes_hojas_fail_closed(
                    resultado.get("hojas") or []
                )
                if not ok_s:
                    raise RuntimeError(
                        "Renesteo rechazado (poka-yoke solapes fail-closed):\n"
                        f"{msg_s}"
                    )

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
            finally:
                self._ctx_ultra_renest_exit(tok_mode, tok_cb)
                if engine_token is not None:
                    from modules.nesting_engine.nest_engine_context import reset_active_engine_id

                    reset_active_engine_id(engine_token)

        threading.Thread(target=worker, daemon=True).start()

    def compensar_calibre_completo(self, clave):
        return QMessageBox.information(
            self,
            "Plasma",
            "La compensación plasma ya no se hace por calibre.\n\n"
            "Selecciona las piezas en PARTS → ESP. y vuelve a nestear.",
        )

    def _opciones_motores_renest(self):
        """Lista (engine_id, etiqueta) de motores listos para renesteo de acero."""
        from modules.nesting_engine.engine_registry import list_engine_metas, is_engine_ready

        out = []
        for meta in list_engine_metas():
            if not is_engine_ready(meta.engine_id):
                continue
            out.append((meta.engine_id, meta.display_name))
        if not out:
            out.append(("arga_force", "ARGA FORCE"))
        return out

    def _engine_renest_fijo_para_clave(self, clave) -> str | None:
        """Si el calibre tiene motor oculto, no se pregunta. None = diálogo."""
        from modules.nesting_engine.giga_cal11_galv import engine_id_for_renest

        return engine_id_for_renest(clave)

    def _mostrar_dialogo_motor_renest(self, titulo, texto):
        """Diálogo para elegir motor de renesteo. Retorna engine_id o None si cancela."""
        from interface.qt.theme import apply_push_button

        opciones = self._opciones_motores_renest()
        dlg = QDialog(self)
        dlg.setWindowTitle(titulo)
        dlg.setModal(True)
        dlg.resize(480, 360)
        dlg.setStyleSheet("background:#F8FAFC;")
        lay = QVBoxLayout(dlg)

        lbl = QLabel(texto)
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

        seleccion = {"eid": None}

        def _pick(eid):
            seleccion["eid"] = eid
            dlg.accept()

        for eid, label in opciones:
            b = QPushButton(label)
            apply_push_button(b, COLOR_GRIS_DARK, font_size=10, padding="8px 10px")
            b.clicked.connect(lambda _checked=False, e=eid: _pick(e))
            inner_lay.addWidget(b)
        inner_lay.addStretch()

        btn_cerrar = QPushButton("CANCELAR")
        apply_push_button(btn_cerrar, "#FFFFFF", font_size=11)
        btn_cerrar.clicked.connect(dlg.reject)
        lay.addWidget(btn_cerrar)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return seleccion["eid"]

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
        # El margen de la tabla es la distancia final placa→pieza, no un
        # clearance al que se sume el kerf. El motor valida el kerf entre
        # piezas durante el empaque exacto.
        edge_margin_mm = max(0.0, float(m or 0.0)) * 25.4
        for ww, hh in ((w_mm, h_mm), (h_mm, w_mm)):
            usable_w = ww - (2.0 * edge_margin_mm)
            usable_h = hh - (2.0 * edge_margin_mm)
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
                fits = (pw <= usable_w and ph <= usable_h) or (
                    ph <= usable_w and pw <= usable_h
                )
                if not fits:
                    ok = False
                    break
                total_area += float(poly.area)
            if ok and total_area <= usable_w * usable_h * 0.98:
                return True
        return False

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
            try:
                piezas = self._piezas_pack_madre_para_empaque(clave, hoja)
            except RuntimeError as exc:
                return QMessageBox.warning(
                    self,
                    "Cambiar de placa",
                    f"No se pudo conservar la compensación de PARTS.\n\n{exc}",
                )
        if not piezas:
            return QMessageBox.warning(
                self,
                "Cambiar de placa",
                "No se pudieron reconstruir las piezas de esta placa para renestear.",
            )

        try:
            k, m = self._gaps_tabla_para_renest(clave, hoja)
        except Exception:
            k, m = DEFAULT_KERF_IN, DEFAULT_MARGIN_IN

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

        ultra_renest = self._es_engine_svgnest_ultra(None)
        self._abrir_carga_renest("Renesteando en placa seleccionada...")

        opt = self.cmb_opt.currentText() if hasattr(self, "cmb_opt") else "OPTIMIZAR LARGO Y ANCHO"
        corner = self.global_corner_val
        hoja_ref = hoja
        w_mm = float(candidata["w_mm"])
        h_mm = float(candidata["h_mm"])
        piezas_worker = copy.deepcopy(piezas)
        self._cambio_placa_ultimo_pack = copy.deepcopy(piezas)

        def worker():
            tok_mode = tok_cb = None
            try:
                tok_mode, tok_cb = self._ctx_ultra_renest_enter(ultra_renest)
                nh, sobras = self.app.motor_nesting.empaquetar_una_hoja_mc(
                    piezas_worker,
                    w_mm,
                    h_mm,
                    k,
                    m,
                    opt,
                    corner,
                )
                if getattr(self.app, "tarea_abortada", lambda: False)():
                    raise RuntimeError("Cancelado por el usuario.")
            except Exception as exc:
                self._ctx_ultra_renest_exit(tok_mode, tok_cb)
                call_on_main(
                    self._on_error_cambio_placa,
                    clave,
                    ses_gen,
                    str(exc),
                )
                return

            self._ctx_ultra_renest_exit(tok_mode, tok_cb)
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

        # El motor entrega posiciones; reinyectamos metadatos de la fuente para
        # que la marca plasma sobreviva también al cambio de placa.
        from modules.nesting_engine.manager import enriquecer_piezas_hoja_con_fuentes

        enriquecer_piezas_hoja_con_fuentes(
            nh,
            getattr(self, "_cambio_placa_ultimo_pack", []) or [],
        )
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
                pz["plasma_fuente_ya_compensada"] = True
        return nh

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
        prefer_dxf=False,
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
            prefer_dxf=prefer_dxf,
        )
        nueva = None
        hojas_extra = []
        renovada_en_pose = False
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
                    debug_tag=f"clave={clave} | recalc_absorber_rtz",
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
                        debug_tag=f"clave={clave} | recalc_contexto_rtz",
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
                    from modules.nesting_engine.nest_engine_context import (
                        get_active_engine_id,
                        is_ultra_renest_accept_mode,
                    )

                    # Renest normal: SIEMPRE empaquetar con el motor elegido (misma placa).
                    # La renovación DXF-en-pose solo es fallback tras REPROCESAR AUTODXF
                    # si el pack no logra colocar todas (p. ej. espejo) — nunca abre hoja extra.
                    n_esperado = len(piezas_a_reprocesar)
                    renovada_en_pose = False
                    n_intentos = 24 if prefer_dxf else 12
                    try:
                        from modules.nesting_engine.giga_cal11_galv import (
                            should_force_giga_engine,
                        )

                        if should_force_giga_engine(clave):
                            n_intentos = 1
                    except Exception:
                        pass
                    print(
                        f"[RENEST-PACK] piezas={n_esperado} | misma_placa | "
                        f"intentos={n_intentos} | engine={get_active_engine_id()} | "
                        f"ultra_accept={is_ultra_renest_accept_mode()} | "
                        f"prefer_dxf={bool(prefer_dxf)}",
                        flush=True,
                    )
                    nh = self.app.motor_nesting.empaquetar_con_reintentos(
                        piezas_a_reprocesar,
                        hoja["placa_w"],
                        hoja["placa_h"],
                        k,
                        m,
                        opt,
                        corner,
                        intentos=n_intentos,
                        debug_tag=f"clave={clave} | recalc_contexto",
                    )
                    colocadas = self._conteo_piezas_reales_en_nest(nh) if nh else 0
                    if nh and colocadas >= n_esperado:
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
                        print(
                            f"[RENEST-PACK] pack incompleto {colocadas}/{n_esperado}",
                            flush=True,
                        )
                        # NUNCA restaurar el nest viejo: eso deja el acomodo igual.
                        if nueva is None:
                            print(
                                "[RENEST-PACK] no caben todas en la misma placa — sin hoja extra",
                                flush=True,
                            )
                            return None, idx_retazos_asociados, []
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
                        pz["plasma_fuente_ya_compensada"] = True
        fuente_pack = piezas_a_reprocesar
        if fuente_pack and not renovada_en_pose:
            from modules.nesting_engine.manager import enriquecer_piezas_hoja_con_fuentes

            for hoja_out in [hoja for hoja in [nueva, *hojas_extra] if hoja]:
                enriquecer_piezas_hoja_con_fuentes(hoja_out, fuente_pack)

        # Kerf de tabla en la hoja. No densify / hole-fill / Venom: eso
        # reacomoda un nest ya hecho (diagonales, huecos, “amontonado”).
        if nueva and isinstance(nueva, dict) and nueva.get("piezas"):
            nueva["kerf_usado"] = float(k)
            nueva["margin_usado"] = float(m)
            if clave and not nueva.get("clave"):
                nueva["clave"] = clave
            for hx in hojas_extra or []:
                if isinstance(hx, dict) and (hx.get("piezas") or []):
                    hx["kerf_usado"] = float(k)
                    hx["margin_usado"] = float(m)

        # Venom mueve piezas: rot/shift previos quedan stale y el display 1:1
        # puede dibujar la geometría DXF desplazada (parece solape / UI rota).
        if not renovada_en_pose:
            try:
                from modules.nesting_engine.display_geometry import invalidar_sellos_geom_hoja
                from modules.nesting_engine.manager import enriquecer_piezas_hoja_con_fuentes

                for hoja_out in [h for h in [nueva, *(hojas_extra or [])] if h]:
                    invalidar_sellos_geom_hoja(hoja_out)
                    if fuente_pack:
                        enriquecer_piezas_hoja_con_fuentes(hoja_out, fuente_pack)
            except Exception:
                pass

        return nueva, idx_retazos_asociados, hojas_extra

    def _preguntar_opts_renest_cobre(self, clave, hoja=None):
        """Diálogo gap + umbral sin-gap (calibre o barra sola). Retorna (sep, largo) o None."""
        from interface.qt.dialogs.nesting_modals import preguntar_separacion_cobre_renest
        from modules.nesting_engine.cu_largos_nesting import (
            DEFAULT_SEPARACION_CU_IN,
            LARGO_SIN_SEPARACION_CU_IN,
        )

        grp_act = (self.app.resultados_nesting or {}).get(clave) or {}
        src = hoja if isinstance(hoja, dict) else {}
        valor_sep = float(
            src.get(
                "separacion_cu_in",
                grp_act.get("separacion_cu_in", DEFAULT_SEPARACION_CU_IN),
            )
        )
        valor_largo = float(
            src.get(
                "largo_sin_separacion_cu_in",
                grp_act.get("largo_sin_separacion_cu_in", LARGO_SIN_SEPARACION_CU_IN),
            )
        )
        return preguntar_separacion_cobre_renest(self, valor_sep, valor_largo)

    def _listar_barras_madre_cobre(self, clave):
        """Barras madre CU del calibre (excluye retazos / RTZCU virtual)."""
        grp = (self.app.resultados_nesting or {}).get(clave) or {}
        hojas = grp.get("hojas") or []
        out = []
        for i, h in enumerate(hojas):
            if not isinstance(h, dict):
                continue
            if h.get("es_retazo") or h.get("cu_rtz_virtual"):
                continue
            if not (h.get("modo_largos_cu") or self._es_grupo_cobre(clave, grp)):
                continue
            pid = str(h.get("placa_id") or h.get("sheet_code") or f"P#{i + 1}")
            try:
                n_pzas = int(contar_piezas_hoja(h) or 0)
            except Exception:
                n_pzas = sum(
                    1
                    for p in (h.get("piezas") or [])
                    if isinstance(p, dict)
                    and not self._es_pieza_virtual(str(p.get("nombre") or ""))
                )
            modo = str(h.get("cu_modo_separacion_barra") or "").strip().lower() or "-"
            out.append(
                {
                    "idx": i,
                    "hoja": h,
                    "label": f"{pid}  ·  {n_pzas} pzas  ·  {modo}",
                }
            )
        return out

    def _renestear_por_barra_cobre_ui(self, clave):
        """Menú de calibre CU: elegir una o varias barras y renestearlas con el diálogo de gap."""
        if not getattr(self.app, "resultados_nesting", None):
            return QMessageBox.warning(self, "Atención", "No hay resultados de nesting.")
        if clave not in (self.app.resultados_nesting or {}):
            return QMessageBox.warning(
                self, "Atención", "Material no encontrado en el resultado actual."
            )
        if not self._es_grupo_cobre(clave):
            return QMessageBox.information(
                self,
                "Renestear por barra",
                "Esta opción solo aplica a grupos de cobre (largos CU).",
            )

        from interface.qt.dialogs.nesting_modals import preguntar_barras_cobre_renest

        candidatas = self._listar_barras_madre_cobre(clave)
        if not candidatas:
            return QMessageBox.warning(
                self, "Atención", "No hay barras madre de cobre para renestear."
            )

        idxs = preguntar_barras_cobre_renest(self, candidatas)
        if not idxs:
            return

        by_idx = {int(b["idx"]): b["hoja"] for b in candidatas}
        # Snapshots: al renestar secuencial el índice de hojas puede cambiar.
        hojas_sel = [copy.deepcopy(by_idx[i]) for i in idxs if i in by_idx]
        if not hojas_sel:
            return

        opts_cu = self._preguntar_opts_renest_cobre(clave, hojas_sel[0])
        if opts_cu is None:
            return
        cu_separacion_in, cu_largo_sin_separacion_in = opts_cu

        labels = [
            str(h.get("placa_id") or h.get("sheet_code") or "?") for h in hojas_sel
        ]
        lista_txt = ", ".join(labels[:8]) + ("…" if len(labels) > 8 else "")
        if (
            QMessageBox.question(
                self,
                "Renestear por barra",
                f"Se reacomodarán {len(hojas_sel)} barra(s): {lista_txt}\n"
                f"(gap {cu_separacion_in:g}\"; Z/especial sin gap).\n\n"
                "Misma lógica que el renesteo de calibre, aplicada solo a las barras elegidas.\n\n"
                "¿Continuar?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        self._cu_renest_barra_queue = [
            {
                "clave": clave,
                "hoja": h,
                "opts_cu": (cu_separacion_in, cu_largo_sin_separacion_in),
            }
            for h in hojas_sel
        ]
        self._kick_cu_renest_barra_queue()

    def _kick_cu_renest_barra_queue(self):
        """Procesa la siguiente barra de la cola de renesteo CU (una a la vez)."""
        cola = getattr(self, "_cu_renest_barra_queue", None) or []
        if not cola:
            return
        item = cola.pop(0)
        self._cu_renest_barra_queue = cola
        clave = item.get("clave")
        hoja_snap = item.get("hoja")
        opts = item.get("opts_cu")
        # Resolver hoja actual por sheet_uid / placa_id (el grupo pudo cambiar).
        hoja_act = None
        grp = (self.app.resultados_nesting or {}).get(clave) or {}
        uid = str((hoja_snap or {}).get("sheet_uid") or "")
        pid = str((hoja_snap or {}).get("placa_id") or "")
        for h in grp.get("hojas") or []:
            if not isinstance(h, dict) or h.get("es_retazo") or h.get("cu_rtz_virtual"):
                continue
            if uid and str(h.get("sheet_uid") or "") == uid:
                hoja_act = h
                break
            if pid and str(h.get("placa_id") or "") == pid:
                hoja_act = h
                break
        if hoja_act is None:
            hoja_act = hoja_snap
        self._renestear_solo_barra_cobre(
            clave,
            hoja_act,
            opts_cu=opts,
            skip_confirm=True,
            on_done=self._kick_cu_renest_barra_queue,
        )

    def _renestear_solo_barra_cobre(
        self, clave, hoja, *, opts_cu=None, skip_confirm=False, on_done=None
    ):
        """Renestea una barra CU con la misma lógica LARGOS (gap / umbral ajustables)."""
        if not getattr(self.app, "resultados_nesting", None):
            return QMessageBox.warning(self, "Atención", "No hay resultados de nesting.")
        if clave not in self.app.resultados_nesting:
            return QMessageBox.warning(
                self, "Atención", "Material no encontrado en el resultado actual."
            )
        if not hoja or hoja.get("es_retazo") or hoja.get("cu_rtz_virtual"):
            return QMessageBox.information(
                self,
                "Renestear barra cobre",
                "Solo se pueden renestear barras madre de cobre (largos CU).",
            )

        if opts_cu is None:
            opts_cu = self._preguntar_opts_renest_cobre(clave, hoja)
            if opts_cu is None:
                return
        cu_separacion_in, cu_largo_sin_separacion_in = opts_cu

        pid = str(hoja.get("placa_id") or hoja.get("sheet_code") or "?")
        if not skip_confirm and (
            QMessageBox.question(
                self,
                "Renestear barra cobre",
                f"Se reacomodarán solo las piezas de la barra {pid} "
                f"(gap {cu_separacion_in:g}\"; Z/especial sin gap).\n\n"
                "Si con el nuevo gap no caben en una sola barra, se abrirán barras adicionales "
                "conservando el inventario del calibre.\n\n¿Continuar?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        bloque_previo = self._desglosar_bloque_placa_mini(clave, hoja)
        # Madre ya incluye piezas de zona RTZ; absorber_rtz duplicaría las del virtual.
        resumen_esperado = self._inventario_piezas_canonico(
            self._resumen_bloque_placa_y_rtz(bloque_previo, absorber_rtz=False)
        )
        if not resumen_esperado:
            return QMessageBox.warning(
                self, "Atención", "La barra no tiene piezas reales para renestear."
            )

        piezas_pack = self._piezas_pack_para_resumen_compensado(
            clave, resumen_esperado, compensar_plasma=False
        )
        n_esp = sum(int(v) for v in resumen_esperado.values())
        if len(piezas_pack) < n_esp:
            return QMessageBox.critical(
                self,
                "Error",
                f"No se pudieron reconstruir todas las piezas de la barra.\n"
                f"Esperadas: {n_esp} · Generadas: {len(piezas_pack)}.",
            )

        # Módulo LARGOS CU: sin kerf/margin/opt/corner de motores de acero.
        k, m = 0.0, 0.0
        opt, corner = "LARGOS CU", "INFERIOR IZQUIERDA"

        idx_objetivo = bloque_previo.get("idx_base", -1)
        idx_rtz_asoc = list(bloque_previo.get("idx_retazos") or [])
        hoja_ref = hoja
        backup_grupo = self._snapshot_grupo_nesting(clave)
        inventario_antes = self._inventario_piezas_grupo(clave)
        sep_cu = float(cu_separacion_in)
        largo_sin_cu = float(cu_largo_sin_separacion_in)

        self._abrir_carga_renest("Renesteando barra cobre...", engine_id=None)

        def worker():
            from interface.qt.thread_bridge import call_on_main

            try:
                if hasattr(self.app, "actualizar_progreso"):
                    call_on_main(
                        self.app.actualizar_progreso,
                        "Reacomodando largos CU...",
                        0.4,
                    )
                datos_placas = self.app.plates_manager.obtener_datos_placas()
                raw = self.app.motor_nesting._procesar_grupo_parallel(
                    clave,
                    piezas_pack,
                    datos_placas,
                    k,
                    m,
                    opt,
                    corner,
                    self._work_order_label_lote_activo(),
                    cu_routing_override="largos",
                    cu_separacion_in=sep_cu,
                    cu_largo_sin_separacion_in=largo_sin_cu,
                )
                if getattr(self.app, "tarea_abortada", lambda: False)():
                    raise RuntimeError("Cancelado por el usuario.")
                resultado = raw[1] if isinstance(raw, tuple) and len(raw) == 2 else raw
                if not isinstance(resultado, dict) or resultado.get("error"):
                    raise RuntimeError(
                        str((resultado or {}).get("error", "Sin resultado válido."))
                    )
                hojas_nuevas = [
                    h
                    for h in (resultado.get("hojas") or [])
                    if isinstance(h, dict)
                    and not h.get("es_retazo")
                    and not h.get("cu_rtz_virtual")
                ]
                if not hojas_nuevas:
                    raise RuntimeError("El renesteo no generó barras válidas.")

                ok_inv, msg_inv = self._validar_renest_conserva_inventario(
                    resumen_esperado, {"hojas": hojas_nuevas}
                )
                if not ok_inv:
                    raise RuntimeError(msg_inv)

                nueva = hojas_nuevas[0]
                hojas_extra = hojas_nuevas[1:]

                def on_ok(
                    _nueva=nueva,
                    _extra=list(hojas_extra),
                    _sep=sep_cu,
                    _largo=largo_sin_cu,
                    _idx_rtz=list(idx_rtz_asoc),
                ):
                    aplicado = self.finalizar_recalc(
                        _nueva,
                        clave_renest=clave,
                        post_fill=False,
                        hoja_original=copy.deepcopy(hoja),
                        idx_objetivo=idx_objetivo,
                        hoja_ref=hoja_ref,
                        backup_grupo=backup_grupo,
                        inventario_antes=inventario_antes,
                        resumen_esperado=resumen_esperado,
                        hojas_adicionales=_extra or None,
                        eliminar_rtz_asociados=bool(_idx_rtz),
                        idx_retazos_asociados=_idx_rtz or None,
                    )
                    if not aplicado:
                        if callable(on_done):
                            on_done()
                        return
                    grp = (self.app.resultados_nesting or {}).get(clave)
                    if isinstance(grp, dict) and grp.get("modo_largos_cu"):
                        grp["separacion_cu_in"] = _sep
                        grp["largo_sin_separacion_cu_in"] = _largo
                        try:
                            asignar_rtz_cu_sin_gap_ids(self.app.resultados_nesting)
                        except Exception:
                            pass
                        self.procesar_lista_hojas(self.app.resultados_nesting)
                        hojas_grp = grp.get("hojas") or []
                        if 0 <= int(idx_objetivo) < len(hojas_grp):
                            self.dibujar_hoja_full(hojas_grp[int(idx_objetivo)], clave)
                    if callable(on_done):
                        on_done()

                self.app.after(0, on_ok)
            except Exception as e:
                def on_err(msg=str(e)):
                    if hasattr(self.app, "cerrar_ventana_carga"):
                        self.app.cerrar_ventana_carga()
                    self._cu_renest_barra_queue = []
                    self._abortar_y_restaurar_nesting(
                        clave,
                        backup_grupo,
                        f"No se pudo renestear la barra de cobre.\n\nDetalle:\n{msg}",
                        hoja_original=hoja,
                        idx_objetivo=idx_objetivo,
                    )

                self.app.after(0, on_err)

        threading.Thread(target=worker, daemon=True).start()

    def _filtrar_placas_ancho_barra_cu(self, datos_placas, ancho_barra_mm: float):
        """Deja solo tiras CU del mismo ancho que la barra (evita bajar a tira más angosta)."""
        if not datos_placas or ancho_barra_mm <= 0.5:
            return list(datos_placas or [])
        ancho_in = float(ancho_barra_mm) / 25.4
        filtradas = []
        extraer = getattr(self.app.motor_nesting, "_extraer_numero", None)
        for placa in datos_placas:
            try:
                d1 = float(extraer(placa[3])) if callable(extraer) else float(placa[3])
                d2 = float(extraer(placa[4])) if callable(extraer) else float(placa[4])
            except Exception:
                continue
            # En largos CU una cota es ~144" (largo) y la otra el ancho de tira.
            if abs(d1 - ancho_in) <= 0.06 or abs(d2 - ancho_in) <= 0.06:
                filtradas.append(placa)
        return filtradas if filtradas else list(datos_placas or [])

    def renestear_solo_placa(
        self,
        clave,
        hoja,
        post_fill=False,
        compensar_plasma=False,
        offset_mm_forzado=None,
        absorber_rtz=False,
        engine_id=None,
        prefer_dxf=True,
    ):
        if not getattr(self.app, "resultados_nesting", None):
            return QMessageBox.warning(self, "Atención", "No hay resultados de nesting.")
        if clave not in self.app.resultados_nesting:
            return QMessageBox.warning(self, "Atención", "Material no encontrado en el resultado actual.")
        if not hoja:
            return

        if self._bloquear_si_dxf_no_apto(titulo="DXF no aptos — no se puede renestear"):
            return

        if (
            not compensar_plasma
            and (
                bool(hoja.get("modo_largos_cu"))
                or self._es_grupo_cobre(clave)
            )
        ):
            return self._renestear_solo_barra_cobre(clave, hoja)
        if hoja.get("es_retazo", False):
            return QMessageBox.information(self, 
                "Renestear placa",
                "Las placas reutilizadas (RTZ) o mini-nest no se pueden renestear desde el menú contextual.",
            )
        try:
            k, m = self._gaps_tabla_para_renest(clave, hoja)
        except Exception:
            return QMessageBox.critical(self, "Error", "Valores no válidos.")

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
        ultra_renest = (not compensar_plasma) and self._es_engine_svgnest_ultra(engine_id)
        # PARTS → motor: mismo candado de orientación que nest completo / calibre.
        self._sync_orientacion_cobre_al_motor()
        self._abrir_carga_renest(titulo_carga, engine_id=engine_id)

        bloque_objetivo = bloque_previo
        idx_objetivo = bloque_objetivo.get("idx_base", -1)
        hoja_ref = hoja
        backup_grupo = self._snapshot_grupo_nesting(clave)
        inventario_antes = self._inventario_piezas_grupo(clave)
        engine_renest = engine_id
        # Renest de placa siempre lee geometría actual de PARTS/DXF (fallback nest).
        forzar_dxf = True if prefer_dxf is None else bool(prefer_dxf)

        def worker():
            import time as _time
            from concurrent.futures import ThreadPoolExecutor

            from interface.qt.thread_bridge import call_on_main
            from modules.nesting_engine.manager import (
                _bind_pack_cancel_checker,
                _unbind_pack_cancel_checker,
            )

            engine_token = None
            tok_mode = tok_cb = None
            cc_bound = False
            prev_cc = None
            try:
                if engine_renest:
                    from modules.nesting_engine.nest_engine_context import set_active_engine_id

                    engine_token = set_active_engine_id(engine_renest)
                    if getattr(self.app, "motor_nesting", None) is not None:
                        self.app.motor_nesting.active_engine_id = engine_renest
                tok_mode, tok_cb = self._ctx_ultra_renest_enter(ultra_renest)
                if getattr(self.app, "tarea_cancelada", None):
                    prev_cc = _bind_pack_cancel_checker(self.app.tarea_cancelada)
                    cc_bound = True

                opt = self.cmb_opt.currentText()
                corner = self.global_corner_val
                if hasattr(self.app, "actualizar_progreso"):
                    call_on_main(
                        self.app.actualizar_progreso,
                        (
                            "Ultra buscando primer acomodo nuevo..."
                            if ultra_renest
                            else "Ejecutando motor de renesteo..."
                        ),
                        0.35,
                    )

                idx_retazos_asociados = bloque_previo.get("idx_retazos") or []
                hojas_extra = []
                nueva = None

                def _do_recalc():
                    return self._recalcular_hoja_con_contexto(
                        clave,
                        hoja,
                        k,
                        m,
                        opt,
                        corner,
                        compensar_plasma=compensar_plasma,
                        offset_mm_forzado=offset_mm_forzado,
                        absorber_rtz=absorber_rtz,
                        prefer_dxf=forzar_dxf,
                    )

                if ultra_renest:
                    # Recalc en hilo: al Aceptar (solo si ya hay acomodo nuevo) no bloquea.
                    # Importante: copiar contextvars (ultra_accept / engine) al pool worker.
                    import contextvars

                    pool = ThreadPoolExecutor(max_workers=1)
                    _ctx = contextvars.copy_context()
                    fut = pool.submit(_ctx.run, _do_recalc)
                    try:
                        while not fut.done():
                            if getattr(self.app, "tarea_acepto_mejor", lambda: False)():
                                break
                            if getattr(self.app, "tarea_abortada", lambda: False)():
                                break
                            _time.sleep(0.15)
                        acepto = bool(getattr(self.app, "tarea_acepto_mejor", lambda: False)())
                        aborto = bool(getattr(self.app, "tarea_abortada", lambda: False)())
                        if fut.done():
                            nueva, idx_retazos_asociados, hojas_extra = fut.result()
                        elif acepto:
                            # Esperar el pack en vuelo (puede ser minuto+); no cortar a vacío.
                            for _ in range(2400):  # hasta ~6 min
                                if fut.done():
                                    break
                                _time.sleep(0.15)
                            if fut.done():
                                nueva, idx_retazos_asociados, hojas_extra = fut.result()
                            else:
                                raise RuntimeError(
                                    "El motor aún no terminó el acomodo al aceptar. "
                                    "Espere a que termine o cancele e intente de nuevo."
                                )
                        elif aborto:
                            raise RuntimeError("Cancelado por el usuario.")
                        else:
                            nueva, idx_retazos_asociados, hojas_extra = fut.result()
                    finally:
                        pool.shutdown(wait=False, cancel_futures=False)
                else:
                    nueva, idx_retazos_asociados, hojas_extra = _do_recalc()

                acepto = bool(getattr(self.app, "tarea_acepto_mejor", lambda: False)())
                if getattr(self.app, "tarea_abortada", lambda: False)() and not acepto:
                    raise RuntimeError("Cancelado por el usuario.")

                if nueva is None or not (nueva.get("piezas") or []):
                    raise RuntimeError(
                        "No se pudo renovar/acomodar todas las piezas en la misma placa.\n"
                        "Un espejo no debería requerir hoja extra; revise el DXF regenerado "
                        "o pruebe otro motor."
                    )

                if hasattr(self.app, "actualizar_progreso"):
                    call_on_main(self.app.actualizar_progreso, "Actualizando vista...", 0.9)

                conservar_rtz = bool(idx_retazos_asociados) and not absorber_rtz

                def on_ok(
                    _nueva=nueva,
                    _idx_rtz=idx_retazos_asociados,
                    _hojas_extra=list(hojas_extra or []),
                    _conservar=conservar_rtz,
                    _from_dxf=forzar_dxf,
                ):
                    ok = self.finalizar_recalc(
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
                    if ok and _from_dxf:
                        try:
                            self.app.autodxf_reprocesado_pendiente = False
                        except Exception:
                            pass

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
            finally:
                if cc_bound:
                    try:
                        _unbind_pack_cancel_checker(prev_cc)
                    except Exception:
                        pass
                self._ctx_ultra_renest_exit(tok_mode, tok_cb)
                if engine_token is not None:
                    from modules.nesting_engine.nest_engine_context import reset_active_engine_id

                    reset_active_engine_id(engine_token)

        threading.Thread(target=worker, daemon=True).start()

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
            return False

        grp = self.app.resultados_nesting.get(clv)
        if not grp or "hojas" not in grp:
            QMessageBox.warning(self, "Atención", "No se encontró el grupo de material en el resultado.")
            return False

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
                return False

        # Anti-solape fail-closed: no aplicar nest con metal overlapping ni si no se pudo validar.
        from modules.nesting_engine.nest_poka_yoke import validar_solapes_hojas_fail_closed

        ok_s, msg_s = validar_solapes_hojas_fail_closed(
            [h for h in [nueva, *(hojas_adicionales or [])] if h]
        )
        if not ok_s:
            self._abortar_y_restaurar_nesting(
                clv,
                snapshot,
                "Renesteo de bloque rechazado (poka-yoke solapes fail-closed).\n"
                f"{msg_s}",
                hoja_original=hoja_original,
                idx_objetivo=idx_objetivo,
            )
            return False

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
            return False

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
            return False

        self._recalcular_costos_grupo(clv)
        self.sincronizar_overlays_clave(clv)
        self._replicar_lote_activo_a_gemelos()
        # Regenerar display 1:1 limpio (evita poligonos/transform stale → solape visual).
        try:
            from modules.nesting_engine.display_geometry import (
                invalidar_sellos_geom_hoja,
                refrescar_poligonos_display_hoja,
            )

            for hfix in [hoja_actualizada, *(hojas_adicionales or [])]:
                if not hfix:
                    continue
                invalidar_sellos_geom_hoja(hfix)
                refrescar_poligonos_display_hoja(hfix)
        except Exception:
            pass
        self.dibujar_hoja_full(hoja_actualizada, clv)
        self.procesar_lista_hojas(self.app.resultados_nesting)
        return True
