from __future__ import annotations
"""Métodos de transferencia cross-WO y gestión multilote para TabNesting."""


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



class TransferMixin:
    """Métodos de transferencia cross-WO y gestión multilote para TabNesting."""

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

        # Poka-yoke solapes fail-closed tras transferencia.
        try:
            from modules.nesting_engine.nest_poka_yoke import validar_solapes_hojas_fail_closed

            grp = (self.app.resultados_nesting or {}).get(clv) or {}
            ok_s, msg_s = validar_solapes_hojas_fail_closed(grp.get("hojas") or [])
            if not ok_s:
                self._abortar_y_restaurar_nesting(
                    clv,
                    backup_grupo,
                    "Transferencia rechazada (poka-yoke solapes fail-closed).\n"
                    f"{msg_s}",
                    hoja_original=hoja_destino,
                    multilote_snaps=backup_multilote,
                )
                return
        except Exception as exc:
            self._abortar_y_restaurar_nesting(
                clv,
                backup_grupo,
                f"Transferencia rechazada: no se pudo validar solapes.\n{exc}",
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
