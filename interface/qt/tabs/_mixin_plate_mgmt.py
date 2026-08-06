from __future__ import annotations
"""Métodos de gestión de placas, inventario y display para TabNesting."""


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



class PlateManagementMixin:
    """Métodos de gestión de placas, inventario y display para TabNesting."""

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
            for name in (
                "btn_panel_renest_placa",
                "btn_panel_cambiar_placa",
                "btn_panel_renest_calibre",
            ):
                btn = getattr(self, name, None)
                if btn is not None:
                    btn.setEnabled(False)
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
        es_cu = self._es_grupo_cobre(clave) or bool((hoja or {}).get("modo_largos_cu"))
        self.btn_panel_renest_placa.setEnabled(acciones_ok)
        # Cobre: solo renesteo (gap/largo). Sin cambiar placa / transferencias de acero.
        self.btn_panel_cambiar_placa.setEnabled(acciones_ok and not es_cu)
        self.btn_panel_renest_calibre.setEnabled(bool(clave))
        if hasattr(self, "btn_panel_renest_placa"):
            self.btn_panel_renest_placa.setText("Renest barra" if es_cu else "Renestear")
            self.btn_panel_renest_placa.setToolTip(
                "Renestear barra cobre (gap/largo)"
                if es_cu
                else "Renestear la placa activa"
            )
        if hasattr(self, "btn_panel_renest_calibre"):
            self.btn_panel_renest_calibre.setText("Calibre")
            self.btn_panel_renest_calibre.setToolTip("Renestear calibre completo")

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
        if self._es_grupo_cobre(self.clave_actual) or bool(
            (self.hoja_actual_data or {}).get("modo_largos_cu")
        ):
            self._renestear_solo_barra_cobre(self.clave_actual, self.hoja_actual_data)
            return
        engine_id = self._mostrar_dialogo_motor_renest(
            "Renestear placa",
            f"Elija el motor para renestear la placa {self.hoja_actual_data.get('placa_id', '')}.",
        )
        if not engine_id:
            return
        self.renestear_solo_placa(
            self.clave_actual, self.hoja_actual_data, engine_id=engine_id
        )

    def panel_renestear_calibre(self):
        if not self._ctx_tiene_resultados(self.clave_actual):
            return
        self._iniciar_renest_calibre_con_seleccion_motor(self.clave_actual)

    def _iniciar_renest_calibre_con_seleccion_motor(self, clave, *, engine_id=None):
        if self._es_grupo_cobre(clave):
            self.renestear_calibre_completo_ui(clave)
            return
        if engine_id is None:
            engine_id = self._mostrar_dialogo_motor_renest(
                "Renestear calibre completo",
                f"Elija el motor para renestear el calibre {clave}.",
            )
            if not engine_id:
                return
        self.renestear_calibre_completo_ui(clave, engine_id=engine_id)

    def _iniciar_renest_calibre_con_seleccion_placa(self, clave, *, candidata_placa=None):
        self._iniciar_renest_calibre_con_seleccion_motor(clave)

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
        if self._es_grupo_cobre(clave) or bool((hoja or {}).get("modo_largos_cu")):
            return QMessageBox.information(
                self,
                "Cobre",
                "En cobre solo se puede renestear la barra (gap / umbral de largo).\n"
                "Cambiar placa madre aplica únicamente a placas de acero.",
            )
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
        # Panel flotante retirado: acciones en cinta «Placa».
        frame = getattr(self, "frame_ajuste_container", None)
        if frame is not None:
            frame.hide()

    def toggle_ajuste_placa(self):
        # Compat no-op (botón eliminado; acciones en cinta).
        self.ajuste_desplegado = False
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
        self.frame_ajuste_container.hide()
        self.ajuste_desplegado = False
        self._reposicionar_panel_ajuste()
        self._sync_kerf_widget()
        self._actualizar_panel_placa(hoja, clave)
        self._actualizar_seccion_pieza_seleccionada()
        self._actualizar_seleccion_lista_hojas()

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
        asignar_rtz_cu_sin_gap_ids(resultados)
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
                from modules.nesting_engine.cu_largos_nesting import (
                    ordenar_hojas_largos_cu_por_ancho,
                )

                hojas_del_material = ordenar_hojas_largos_cu_por_ancho(
                    list(hojas_del_material)
                )
                info["hojas"] = hojas_del_material
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
                    origen_up = str(hoja.get("origen_placa") or "").upper()
                    es_rem_ui = (
                        "REMANENTE" in origen_up
                        or str(nombre_placa).upper().startswith("REM-")
                        or str(nombre_placa).upper().startswith("PL-")
                    )
                    if es_rem_ui and not str(nombre_placa).upper().startswith("REM-"):
                        # Legacy PL-…: no mostrar como SKU de catálogo
                        try:
                            w_in = float(hoja.get("placa_w") or 0) / 25.4
                            h_in = float(hoja.get("placa_h") or 0) / 25.4
                            cal = str(hoja.get("placa_cal") or "").strip() or "?"
                            nombre_placa = f"REM-{cal}-{w_in:g}x{h_in:g}"
                        except Exception:
                            nombre_placa = f"REM-{nombre_placa}"
                    origen_str = (
                        " (REMANENTE)"
                        if es_rem_ui
                        else (" (PROVEEDOR)" if hoja.get('origen_placa') == "PROVEEDOR" else "")
                    )
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
                    if hoja_tag and not hoja.get("cu_rtz_virtual"):
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
                    # Cobre y acero: clic derecho en la barra/placa para renestear.
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

    def _bind_menu_renestear_placa(self, btn, clave, hoja):
        if hoja.get("es_retazo", False):
            return

        def show_menu(pos):
            if not self._ctx_tiene_resultados(clave):
                return
            if not self._ctx_hoja_valida(hoja, "Menú de placa"):
                return
            menu = QMenu(self)
            es_cu_largos = bool(hoja.get("modo_largos_cu")) or self._es_grupo_cobre(clave)

            # Cobre: SOLO renesteo con gap/umbral. Sin transferencias, motores ni
            # "cambiar de placa" (lógicas exclusivas del módulo de acero).
            if es_cu_largos:
                menu.addAction(
                    "RENESTEAR POR BARRA",
                    self._safe_ctx(
                        "Renestear barra cobre",
                        lambda c=clave, h=hoja: self._renestear_solo_barra_cobre(c, h),
                    ),
                )
                menu.exec(btn.mapToGlobal(pos))
                return

            bloque = self._desglosar_bloque_placa_mini(clave, hoja)
            tiene_rtz = bool(bloque.get("idx_retazos"))
            # Motores como submenú (mismo patrón que calibre), no como ítems planos.
            sub_renest = QMenu("Renestear placa", menu)
            if tiene_rtz:
                for eid, label in self._opciones_motores_renest():
                    sub_m = QMenu(label, sub_renest)
                    sub_m.addAction(
                        "CON RTZ (conservar retazo)",
                        self._safe_ctx(
                            "Renestear con RTZ",
                            lambda c=clave, h=hoja, e=eid: self.renestear_solo_placa(
                                c, h, absorber_rtz=False, engine_id=e
                            ),
                        ),
                    )
                    sub_m.addAction(
                        "SIN RTZ (piezas a placa madre)",
                        self._safe_ctx(
                            "Renestear sin RTZ",
                            lambda c=clave, h=hoja, e=eid: self.renestear_solo_placa(
                                c, h, absorber_rtz=True, engine_id=e
                            ),
                        ),
                    )
                    sub_renest.addMenu(sub_m)
            else:
                for eid, label in self._opciones_motores_renest():
                    sub_renest.addAction(
                        label,
                        self._safe_ctx(
                            "Renestear placa",
                            lambda c=clave, h=hoja, e=eid: self.renestear_solo_placa(
                                c, h, engine_id=e
                            ),
                        ),
                    )
            menu.addMenu(sub_renest)
            menu.addAction(
                "CAMBIAR PIEZAS A OTRA PLACA",
                self._safe_ctx(
                    "Transferencia",
                    lambda c=clave, h=hoja: abrir_modal_transferencia_masiva(self, c, h),
                ),
            )
            info_grupo = (self.app.resultados_nesting or {}).get(clave)
            puede_sobrante_forzado = not hoja_excluida_de_rtz_sobrante(hoja)
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

    def _obtener_candidatas_placa(self, clave, hoja):
        try:
            cal_req, mat_req = (clave.split("_", 1) + [""])[:2]
        except Exception:
            cal_req, mat_req = "", clave

        candidatos = []
        vistos = set()
        datos_placas = self.app.plates_manager.obtener_datos_placas()
        try:
            placas_ok, _mode = self.app.motor_nesting._clasificar_placas_por_calibre(
                cal_req, mat_req, datos_placas or []
            )
        except Exception:
            placas_ok = []
            for placa in (datos_placas or []):
                try:
                    p_cal = placa[0]
                    p_mat = placa[1]
                    if not self.app.motor_nesting._coinciden(cal_req, p_cal):
                        continue
                    if not self.app.motor_nesting._coinciden(mat_req, p_mat):
                        continue
                    w_in = self.app.motor_nesting._extraer_numero(placa[3])
                    h_in = self.app.motor_nesting._extraer_numero(placa[4])
                    if w_in <= 0 or h_in <= 0:
                        continue
                    precio_mxn = self.app.motor_nesting._extraer_numero(placa[6]) if len(placa) > 6 else 0.0
                    lb = self.app.motor_nesting._extraer_numero(placa[5]) if len(placa) > 5 else 0.0
                    usd_lb = self.app.motor_nesting._extraer_numero(placa[10]) if len(placa) > 10 else (
                        self.app.motor_nesting._extraer_numero(placa[7]) if len(placa) > 7 else 0.0
                    )
                    precio = precio_mxn if precio_mxn > 0 else (lb * usd_lb)
                    placas_ok.append(
                        {
                            "id": str(placa[2]),
                            "w": w_in * 25.4,
                            "h": h_in * 25.4,
                            "precio": float(precio or 0.0),
                            "origen": str(placa[9]).upper() if len(placa) > 9 else "EMPRESA",
                            "calibre": str(p_cal).strip(),
                        }
                    )
                except Exception:
                    continue

        for placa_dbg in placas_ok:
            try:
                pid = str(placa_dbg.get("id") or "")
                w_mm = float(placa_dbg.get("w") or 0.0)
                h_mm = float(placa_dbg.get("h") or 0.0)
                w_in = w_mm / 25.4
                h_in = h_mm / 25.4
                if w_in <= 0 or h_in <= 0:
                    continue
                key = (pid, round(w_in, 4), round(h_in, 4))
                if key in vistos:
                    continue
                vistos.add(key)
                candidatos.append(
                    {
                        "id": pid,
                        "w_mm": w_mm,
                        "h_mm": h_mm,
                        "w_in": w_in,
                        "h_in": h_in,
                        "origen": str(placa_dbg.get("origen") or "EMPRESA").upper(),
                        "precio": float(placa_dbg.get("precio") or 0.0),
                        "calibre": str(placa_dbg.get("calibre") or ""),
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

        for eid, label in self._opciones_motores_renest():
            sub_menu.addAction(
                label,
                self._safe_ctx(
                    "Renestear calibre",
                    lambda c=clave, e=eid: self.renestear_calibre_completo_ui(
                        c, engine_id=e
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
        mat_clave = str(material_hoja or "").strip().upper()

        piezas_fuente = {}
        filas = list(getattr(self.app, "datos_partes_actuales", []) or [])

        def _cargar_pass(*, exacto: bool):
            for p_nom, mat, qty, cal, st, ruta in filas:
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
                    # Mantener etiqueta del grupo nest para no “pasar” a A 36.
                    "material": material_hoja or mat,
                    "ruta": ruta,
                }

        _cargar_pass(exacto=True)
        _cargar_pass(exacto=False)

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
        fila_ign.setMaximumHeight(42)
        ign_lay = QHBoxLayout(fila_ign)
        ign_lay.setContentsMargins(10, 6, 10, 6)
        ign_lay.setSpacing(10)

        lbl = QLabel("COBRE — IGNORAR DEDUCCIÓN INVENTARIO")
        lbl.setStyleSheet("color:#9CA3AF;font-size:12px;font-weight:700;")
        ign_lay.addWidget(lbl)

        sw = HerinoxSwitch(
            label_on="ON · Sobrante",
            label_off="OFF · Comprar",
            checked=ignorada,
        )
        sw.toggled.connect(
            lambda checked, c=clave, g=info: self._toggle_ignorar_deduccion_cu_grupo(c, g, checked)
        )
        ign_lay.addWidget(sw)
        ign_lay.addStretch(1)
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
        fila_ign.setMaximumHeight(42)
        ign_lay = QHBoxLayout(fila_ign)
        ign_lay.setContentsMargins(10, 6, 10, 6)
        ign_lay.setSpacing(10)

        lbl = QLabel(f"IGNORAR DEDUCCIÓN  |  REAL {efi_real:.1f}%")
        lbl.setStyleSheet("color:#64748B;font-size:12px;")
        ign_lay.addWidget(lbl)

        sw = HerinoxSwitch(
            label_on="ON · Sobrante",
            label_off="OFF · Comprar",
            checked=ignorada,
        )
        sw.toggled.connect(lambda checked, c=clave, h=hoja: self._toggle_ignorar_deduccion_placa(c, h, checked))
        ign_lay.addWidget(sw)
        ign_lay.addStretch(1)

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
            if isinstance(hoja, dict) and hoja.get("cu_rtz_virtual"):
                continue
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

    def _marcar_gemelas_desync(self, *lote_indices):
        ml = getattr(self.app, "resultados_multilote", None) or []
        for raw_idx in lote_indices:
            li = int(raw_idx)
            if 0 <= li < len(ml):
                ml[li]["gemelo_desync"] = True

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
        prefer_dxf=False,
    ):
        """Reconstruye piezas para renest/compensar usando fuente robusta (DXF + fallback nest)."""
        resumen_canon = self._inventario_piezas_canonico(resumen or {})
        if not resumen_canon:
            return []

        fuente = self._construir_fuente_geometria_por_nombre(
            clave,
            nombres_requeridos=set(resumen_canon.keys()),
            prefer_dxf=bool(prefer_dxf),
        )
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
                    intentos=1,
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
                    try:
                        import os
                        from modules.nesting_engine import venom_ai
                        engine_id = os.environ.get("ARGA_MOTOR_NESTING", "svgnest_ultra")
                        venom_ai.apply_smart_polisher(nh, engine_id)
                    except Exception:
                        pass
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
