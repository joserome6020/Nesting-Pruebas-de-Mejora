from __future__ import annotations
"""Métodos de edición de lotes y piezas para TabNesting."""


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



class LoteEditMixin:
    """Métodos de edición de lotes y piezas para TabNesting."""

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

            # Solo inyecta stick si el DXF aún no trae marcaje del script.
            try:
                from modules.dxf_mark.inject import tiene_marcaje_stick
                from modules.dxf_mark.pipeline import aplicar_marcaje_nesting

                if not tiene_marcaje_stick(ruta_origen):
                    aplicar_marcaje_nesting(ruta_processed_destino)
            except Exception:
                pass

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
