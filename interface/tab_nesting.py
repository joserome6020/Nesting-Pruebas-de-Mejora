import threading
import shutil
import ezdxf
import time
import os
import json
import copy
import re
import urllib.request
import urllib.parse
from datetime import datetime
from tkinter import filedialog, messagebox, Menu
import customtkinter as ctk
from reporte_pdf_nesting import exportar_pdf_nesting
from nesting_workspace import (
    guardar_workspace,
    cargar_workspace_desde_archivo,
    aplicar_workspace,
)
from nesting_lote_editor import abrir_editor_lote
from postgres_connector import guardar_nesting_en_postgresql
from nesting_canvas import VisorNesting
from utils_nesting import (
    obtener_siguiente_consecutivo, crear_estructura_carpetas,
    generar_combinaciones_lotes, escalar_piezas, ensamblar_escenario, generar_csv_compras
)
from nesting_modals import (
    abrir_modal_configuracion, abrir_modal_costos,
    mostrar_modal_escenarios, abrir_modal_transferencia,
    abrir_modal_transferencia_masiva,
)
import config
from modules.processed_layers import ProcesadorDXF
from modules.plasma_compensator import compute_plasma_offset_mm
from modules.nesting_engine.efficiency_metrics import (
    actualizar_eficiencias_resultados,
    eficiencia_para_umbral_ignorar,
    es_placa_madre_sobrante_rtz,
    es_placa_madre_rtzc,
    formatear_eficiencias_placa,
    formatear_eficiencias_tanque,
    hoja_cuenta_para_deduccion,
    inicializar_contador_rtz_sobrante,
    inicializar_contador_rtzc_sobrante,
    placa_debe_mostrar_opcion_ignorar,
    sincronizar_hoja_sobrante_rtz,
    sincronizar_sobrantes_rtz_en_resultados,
)
from modules.nesting_engine.rtz_overlays import sincronizar_overlays_resultados
from responsive_layout import configurar_contenedor_expandible

COLOR_TARJETA = "#FFFFFF"
COLOR_BORDE = "#CBD5E1"
COLOR_GRIS_DARK = "#1E293B"
COLOR_GRIS_MED = "#475569"
COLOR_TEXTO_TITULO = "#0F172A"
COLOR_TEXTO_SECUNDARIO = "#64748B"
DEFAULT_KERF_IN = 0.3
DEFAULT_MARGIN_IN = 0.15


class TabNesting(ctk.CTkFrame):
    def __init__(self, master, app_principal):
        super().__init__(master, fg_color="transparent")
        self.app = app_principal
        self.app.tiempo_calculo = 0
        self.cantidad_tanques = "N/A"
        self.lote_actual_idx = 0  # <--- NUEVO: Puntero de memoria para el Dropdown

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

        # Evita congelamientos si el usuario abre el submenú "Cambiar de placa" repetidas veces.
        self._submenu_cambiar_busy = False

        self.setup_ui()

    def exportar_reporte_pdf_nesting(self):
        if not hasattr(self.app, 'resultados_nesting') or not self.app.resultados_nesting:
            return messagebox.showwarning("Atención", "No hay datos de nesting para exportar.")
        wo_real = self._obtener_wo_real_lote_actual()
        if not wo_real:
            return messagebox.showwarning(
                "Atención",
                "Primero debes exportar DXF/STEP para asignar la WO oficial del lote actual."
            )

        orden_actual = str(getattr(self.app, "job_activo", "NESTING")).strip() or "NESTING"
        wo_real_str = str(wo_real).strip()

        orden_archivo = orden_actual.replace("/", "-").replace("\\", "-")
        wo_archivo = wo_real_str.replace("/", "-").replace("\\", "-")

        nombre_sugerido = f"Nesting_Reporte_{orden_archivo}-{wo_archivo}.pdf"

        ruta_pdf = filedialog.asksaveasfilename(
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
            messagebox.showinfo("PDF generado", f"Reporte creado correctamente:\n{payload}")
            try:
                os.startfile(payload)
            except Exception:
                pass
        else:
            messagebox.showerror("Error al generar PDF", payload)

    def _obtener_wo_real_lote_actual(self):
        wo_map = getattr(self.app, "wo_reales_por_lote", {}) or {}
        idx = int(getattr(self, "lote_actual_idx", 0) or 0)
        return wo_map.get(idx)

    def guardar_workspace_nesting(self):
        tiene_multilote = hasattr(self.app, "resultados_multilote") and bool(self.app.resultados_multilote)
        tiene_simple = hasattr(self.app, "resultados_nesting") and bool(self.app.resultados_nesting)

        if not tiene_multilote and not tiene_simple:
            return messagebox.showwarning("Atención", "No hay datos de nesting para guardar.")

        orden_actual = str(getattr(self.app, "job_activo", "NESTING")).strip() or "NESTING"
        nombre_sugerido = f"{orden_actual.replace('/', '-').replace('\\\\', '-')}.arganest"

        ruta_archivo = filedialog.asksaveasfilename(
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
            messagebox.showinfo(
                "Workspace guardado",
                f"Se guardó correctamente el workspace:\n{ruta_archivo}"
            )
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo guardar el workspace:\n{e}")

    def abrir_workspace_nesting(self):
        ruta_archivo = filedialog.askopenfilename(
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

        try:
            payload = cargar_workspace_desde_archivo(ruta_archivo)
            aplicar_workspace(self, payload)
            messagebox.showinfo(
                "Workspace cargado",
                f"Workspace restaurado correctamente:\n{ruta_archivo}"
            )
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir el workspace:\n{e}")

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

    def _registrar_meta_pdf_lote(self, ruta_dxf_final, item_nombre):
        """
        Mantiene alineado el nuevo DXF con el mismo sistema de metadata por ruta
        que usa el PDF y otros flujos.
        """
        if not hasattr(self.app, "meta_pdf_por_ruta") or self.app.meta_pdf_por_ruta is None:
            self.app.meta_pdf_por_ruta = {}

        ruta_norm = self._normalizar_ruta_meta_lote(ruta_dxf_final)
        job_actual = str(getattr(self.app, "job_activo", "NESTING")).strip() or "NESTING"
        work_order = self._work_order_label_lote_activo()

        self.app.meta_pdf_por_ruta[ruta_norm] = {
            "job": job_actual,
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
        texto = str(texto_material or "").strip().upper()
        if not texto:
            return ""

        # Normalización simple alineada a lo que hoy muestra la tabla
        if "CARBON" in texto:
            return "CARBONO"
        if "INOX" in texto or "STAINLESS" in texto:
            return "INOX"
        if "ALUMIN" in texto:
            return "ALUMINIO"

        return texto

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
        material_detectado = self._extraer_material_desde_nombre_dxf(ruta_o_nombre)
        calibre_detectado = self._extraer_calibre_desde_nombre_dxf(ruta_o_nombre)
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

        self._registrar_meta_pdf_lote(ruta_final_proyecto, metadata["nombre"])

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

        self._registrar_meta_pdf_lote(ruta_final_proyecto, metadata["nombre"])
        self._actualizar_lote_editable_en_memoria(actuales)
    def _primer_hoja_disponible(self, resultados):
        from modules.nesting_engine.resultados_grupos import primer_grupo_con_hojas

        return primer_grupo_con_hojas(resultados)

    def _finalizar_renesteo_lote(self, nuevo_resultado):
        if hasattr(self.app, 'cerrar_ventana_carga'):
            self.app.cerrar_ventana_carga()

        if not isinstance(nuevo_resultado, dict) or not nuevo_resultado:
            return messagebox.showerror("Error", "El renesteo no devolvió un resultado válido.")

        if not hasattr(self.app, "resultados_multilote") or not self.app.resultados_multilote:
            return messagebox.showerror("Error", "No existe un lote activo para sustituir.")

        self.app.resultados_multilote[self.lote_actual_idx]["data"] = nuevo_resultado
        self.app.resultados_nesting = nuevo_resultado
        self.app.lote_editado_dirty = False
        self._replicar_lote_activo_a_gemelos()

        # Mantener PARTS del lote activo
        self._sincronizar_parts_con_lote_activo()

        self.procesar_lista_hojas(self.app.resultados_nesting)

        hoja, clave = self._primer_hoja_disponible(self.app.resultados_nesting)
        if hoja is not None and clave is not None:
            self.dibujar_hoja_full(hoja, clave)

        messagebox.showinfo(
            "Renesteo completado",
            "El lote activo fue renesteado correctamente sin recalcular los demás lotes."
        )

    def renestear_lote_actual(self):
        lote_inputs = self._clonar_datos_partes_edicion(
            getattr(self.app, "editable_inputs_actuales", [])
        )

        if not lote_inputs:
            return messagebox.showwarning("Atención", "El lote activo no tiene piezas para renestear.")

        if hasattr(self.app, 'abrir_ventana_carga'):
            self.app.abrir_ventana_carga("Renesteando lote activo...")

        def receptor_en_vivo(msg, pct):
            if hasattr(self.app, 'actualizar_progreso'):
                self.app.after(0, lambda: self.app.actualizar_progreso(msg, pct))
                self.app.after(0, self.app.update_idletasks)

        def worker():
            try:
                datos_placas = self.app.plates_manager.obtener_datos_placas()
                wo_act = str(getattr(self.app, 'job_activo', 'PENDIENTE')).strip().upper() or "PENDIENTE"

                try:
                    kerf_ui = float(self.ent_kerf.get())
                except Exception:
                    kerf_ui = DEFAULT_KERF_IN
                nuevo_resultado = self.app.motor_nesting.ejecutar_nesting_visual(
                    lote_inputs,
                    datos_placas,
                    progress_callback=receptor_en_vivo,
                    config_kerf=kerf_ui,
                    config_margin=self.global_margin_val,
                    config_corner=self.global_corner_val,
                    config_opt=self.cmb_opt.get() if hasattr(self, "cmb_opt") else "OPTIMIZAR LARGO Y ANCHO",
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
                    messagebox.showerror("Error", f"No se pudo renestear el lote activo:\n{msg}")

                self.app.after(0, throw_err)

        threading.Thread(target=worker, daemon=True).start()

    def editar_lote_activo(self):
        if not hasattr(self.app, "resultados_multilote") or not self.app.resultados_multilote:
            return messagebox.showwarning(
                "Atención",
                "Primero debes generar o abrir un nesting."
            )

        if not hasattr(self.app, "editable_inputs_by_lote") or not self.app.editable_inputs_by_lote:
            return messagebox.showwarning(
                "Atención",
                "Aún no hay datos editables del lote activo."
            )

        # Aseguramos que el lote activo y PARTS estén sincronizados antes de abrir el editor
        self._sincronizar_parts_con_lote_activo()

        abrir_editor_lote(self)

    def setup_ui(self):
        configurar_contenedor_expandible(self, filas=1, columnas=2)
        self.columnconfigure(0, weight=0, minsize=380)
        self.columnconfigure(1, weight=1, minsize=480)

        # === PANEL IZQUIERDO (proporción fija; sin divisor arrastrable) ===
        panel_izq = ctk.CTkFrame(
            self,
            fg_color=COLOR_TARJETA,
            border_width=1,
            border_color=COLOR_BORDE,
            corner_radius=15,
        )
        panel_izq.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        self.frame_header = ctk.CTkFrame(panel_izq, fg_color="transparent")
        self.frame_header.pack(side="top", fill="x", padx=20, pady=(20, 0))

        self.lbl_cantidad = ctk.CTkLabel(
            self.frame_header,
            text="Cantidad: -",
            font=("Inter", 12, "bold"),
            text_color=COLOR_TEXTO_TITULO
        )
        self.lbl_cantidad.pack(side="left")

        # =========================================================
        # NUEVO MENÚ DESPLEGABLE (SELECTOR DE WORK ORDERS / LOTES)
        # =========================================================
        self.cmb_lotes = ctk.CTkComboBox(
            panel_izq,
            font=("Inter", 12, "bold"),
            fg_color="#1E293B",
            text_color="white",
            command=self.on_lote_selected,
            state="disabled"
        )
        self.cmb_lotes.pack(side="top", fill="x", padx=20, pady=(10, 0))
        self.cmb_lotes.set("SIN ÓRDENES")

        self.btn_run_nest = ctk.CTkButton(
            panel_izq,
            text="🚀 EJECUTAR NESTING",
            font=("Inter", 14, "bold"),
            height=55,
            fg_color=COLOR_GRIS_DARK,
            hover_color=COLOR_GRIS_MED,
            command=self.ejecutar_nesting
        )
        self.btn_run_nest.pack(side="top", fill="x", padx=30, pady=(15, 10))

        self.lista_hojas = ctk.CTkScrollableFrame(panel_izq, fg_color="transparent")
        self.lista_hojas.pack(side="top", fill="both", expand=True, padx=20, pady=(10, 20))

        # === PANEL DERECHO ===
        self.contenedor_derecho = ctk.CTkFrame(self, fg_color="transparent")
        self.contenedor_derecho.grid(row=0, column=1, sticky="nsew")

        self.frame_header_der = ctk.CTkFrame(self.contenedor_derecho, fg_color="transparent")
        self.frame_header_der.pack(side="top", fill="x", padx=20, pady=(10, 5))

        self.panel_der = ctk.CTkFrame(self.contenedor_derecho, fg_color="#1E293B", corner_radius=15)
        self.panel_der.pack(side="top", fill="both", expand=True)

        self.btn_exportar = ctk.CTkButton(
            self.frame_header_der,
            text="💾 EXPORTAR DXF/STEP",
            font=("Inter", 11, "bold"),
            width=150,
            height=28,
            fg_color="#202A36",
            hover_color="#151C24",
            command=self.exportar_resultados_dxf
        )
        self.btn_exportar.pack(side="left", padx=(0, 5))

        self.btn_ver_lotes = ctk.CTkButton(
            self.frame_header_der,
            text="📄 HISTORIAL DE W.O.",
            font=("Inter", 11, "bold"),
            width=190,
            height=28,
            fg_color="#455E75",
            hover_color="#334659",
            command=self.reabrir_modal_escenarios
        )
        self.btn_ver_lotes.pack(side="left", padx=5)

        self.btn_costos = ctk.CTkButton(
            self.frame_header_der,
            text="💲 COSTOS DE ORDEN",
            font=("Inter", 11, "bold"),
            width=150,
            height=28,
            fg_color="#708DA9",
            hover_color="#597A96",
            command=lambda: abrir_modal_costos(self)
        )
        self.btn_costos.pack(side="left", padx=5)

        self.btn_config = ctk.CTkButton(
            self.frame_header_der,
            text="⚙️ CONFIGURACIÓN",
            font=("Inter", 11, "bold"),
            width=130,
            height=28,
            fg_color="#8AABC2",
            hover_color="#708DA9",
            text_color="#1E293B",
            command=lambda: abrir_modal_configuracion(self)
        )
        self.btn_config.pack(side="left", padx=5)

        self.btn_pdf_nesting = ctk.CTkButton(
            self.frame_header_der,
            text="🧾 PDF NESTING",
            font=("Inter", 11, "bold"),
            width=125,
            height=28,
            fg_color="#BFD3E6",
            hover_color="#9FBBD5",
            text_color="#1E293B",
            command=self.exportar_reporte_pdf_nesting
        )
        self.btn_pdf_nesting.pack(side="left", padx=5)

        self.btn_editar_lote = ctk.CTkButton(
            self.frame_header_der,
            text="✏️ EDITAR LOTE",
            font=("Inter", 11, "bold"),
            width=125,
            height=28,
            fg_color="#DCEBFA",
            hover_color="#BFD3E6",
            text_color="#1E293B",
            command=self.editar_lote_activo
        )
        self.btn_editar_lote.pack(side="left", padx=5)

        self.btn_guardar_nest = ctk.CTkButton(
            self.frame_header_der,
            text="💾 GUARDAR NEST",
            font=("Inter", 11, "bold"),
            width=135,
            height=28,
            fg_color="#DCEBFA",
            hover_color="#BFD3E6",
            text_color="#1E293B",
            command=self.guardar_workspace_nesting
        )
        self.btn_guardar_nest.pack(side="left", padx=5)

        self.btn_abrir_nest = ctk.CTkButton(
            self.frame_header_der,
            text="📂 ABRIR NEST",
            font=("Inter", 11, "bold"),
            width=125,
            height=28,
            fg_color="#E8EEF5",
            hover_color="#D7E3EE",
            text_color="#1E293B",
            command=self.abrir_workspace_nesting
        )
        self.btn_abrir_nest.pack(side="left", padx=5)

        self.visor = VisorNesting(self.panel_der, self.app, self.on_piece_selected)
        self.visor.pack(fill="both", expand=True)

        self.frame_ajuste_container = ctk.CTkFrame(self.panel_der, fg_color="transparent")
        self.ajuste_desplegado = False

        self.panel_ajuste_contenido = ctk.CTkFrame(
            self.frame_ajuste_container,
            fg_color="#0F172A",
            corner_radius=8,
            border_width=1,
            border_color="#475569"
        )
        self.lbl_id_hud = ctk.CTkLabel(
            self.panel_ajuste_contenido,
            text="ID: -",
            font=("Inter", 10, "bold"),
            text_color="#94A3B8"
        )
        self.lbl_id_hud.pack(padx=10, pady=(6, 2))

        f_kerf = ctk.CTkFrame(self.panel_ajuste_contenido, fg_color="transparent")
        f_kerf.pack(padx=10, pady=2, fill="x")
        ctk.CTkLabel(f_kerf, text="Kerf:", text_color="white", font=("Inter", 11)).pack(side="left")
        self.ent_kerf = ctk.CTkEntry(f_kerf, width=60, height=24)
        self.ent_kerf.pack(side="right")
        self.ent_kerf.insert(0, str(DEFAULT_KERF_IN))

        f_opt = ctk.CTkFrame(self.panel_ajuste_contenido, fg_color="transparent")
        f_opt.pack(padx=10, pady=2, fill="x")
        ctk.CTkLabel(f_opt, text="Opt:", text_color="white", font=("Inter", 11)).pack(side="left")
        self.cmb_opt = ctk.CTkComboBox(
            f_opt,
            values=["OPTIMIZAR LARGO Y ANCHO", "OPTIMIZAR LARGO", "OPTIMIZAR ANCHO"],
            width=110,
            height=24,
            font=("Inter", 10)
        )
        self.cmb_opt.pack(side="right")
        self.cmb_opt.set("OPTIMIZAR LARGO Y ANCHO")

        self.btn_recalc = ctk.CTkButton(
            self.panel_ajuste_contenido,
            text="RECALCULAR PLACA",
            height=24,
            fg_color="#323741",
            hover_color=COLOR_GRIS_MED,
            font=("Inter", 10, "bold"),
            command=self.aplicar_cambios_locales
        )
        self.btn_recalc.pack(padx=10, pady=(6, 4), fill="x")

        self.btn_transferir = ctk.CTkButton(
            self.panel_ajuste_contenido,
            text="MUDAR PIEZA",
            height=24,
            fg_color="#3B82F6",
            hover_color="#2563EB",
            font=("Inter", 10, "bold"),
            state="disabled",
            command=lambda: abrir_modal_transferencia(self)
        )
        self.btn_transferir.pack(padx=10, pady=(0, 6), fill="x")

        frame_rot_box = ctk.CTkFrame(self.panel_ajuste_contenido, fg_color="transparent")
        frame_rot_box.pack(padx=10, pady=(0, 8), fill="x")

        self.btn_rot_90 = ctk.CTkButton(
            frame_rot_box,
            text="⟳ 90°",
            width=60,
            height=24,
            fg_color="#334155",
            hover_color="#475569",
            font=("Inter", 10, "bold"),
            state="disabled",
            command=lambda: self.visor.rotar_pieza_seleccionada(90)
        )
        self.btn_rot_90.pack(side="left", padx=(0, 2), expand=True, fill="x")

        self.btn_rot_m1 = ctk.CTkButton(
            frame_rot_box,
            text="- 1°",
            width=35,
            height=24,
            fg_color="#1E293B",
            border_width=1,
            border_color="#475569",
            font=("Inter", 10, "bold"),
            state="disabled",
            command=lambda: self.visor.rotar_pieza_seleccionada(-1)
        )
        self.btn_rot_m1.pack(side="left", padx=2)

        self.btn_rot_p1 = ctk.CTkButton(
            frame_rot_box,
            text="+ 1°",
            width=35,
            height=24,
            fg_color="#1E293B",
            border_width=1,
            border_color="#475569",
            font=("Inter", 10, "bold"),
            state="disabled",
            command=lambda: self.visor.rotar_pieza_seleccionada(1)
        )
        self.btn_rot_p1.pack(side="left", padx=(2, 0))

        self.btn_toggle_ajuste = ctk.CTkButton(
            self.frame_ajuste_container,
            text="⚙️ AJUSTE DE PLACA 🔼",
            font=("Inter", 11, "bold"),
            fg_color="#1E293B",
            hover_color="#323741",
            corner_radius=8,
            border_width=1,
            border_color="#475569",
            height=32,
            command=self.toggle_ajuste_placa
        )
        self.btn_toggle_ajuste.pack(side="bottom", fill="x", pady=(5, 0))

    def on_piece_selected(self, info_pieza=None):
        piezas = self.visor.piezas_seleccionadas
        n = len(piezas)
        estado_transfer = "normal" if n >= 1 else "disabled"
        estado_rot = "normal" if n == 1 else "disabled"
        self.btn_transferir.configure(state=estado_transfer)
        self.btn_rot_90.configure(state=estado_rot)
        self.btn_rot_m1.configure(state=estado_rot)
        self.btn_rot_p1.configure(state=estado_rot)
        if n > 1:
            self.btn_transferir.configure(text=f"MUDAR PIEZAS ({n})")
        else:
            self.btn_transferir.configure(text="MUDAR PIEZA")

    def toggle_ajuste_placa(self):
        if self.ajuste_desplegado:
            self.panel_ajuste_contenido.pack_forget()
            self.btn_toggle_ajuste.configure(text="⚙️ AJUSTE DE PLACA 🔼")
            self.ajuste_desplegado = False
        else:
            self.panel_ajuste_contenido.pack(side="top", fill="both", expand=True)
            self.btn_toggle_ajuste.configure(text="⚙️ AJUSTE DE PLACA 🔽")
            self.ajuste_desplegado = True

    def dibujar_hoja_full(self, hoja, clave):
        if hoja is not self.hoja_actual_data:
            self.visor.limpiar_seleccion_piezas()
            self.on_piece_selected()
        self.visor.dibujar_hoja_full(hoja, clave)
        self.frame_ajuste_container.place(relx=0.98, rely=0.98, anchor="se")
        self.lbl_id_hud.configure(text=f"[{clave}]\nID: {hoja.get('placa_id')}")
        self.ent_kerf.delete(0, 'end')
        self.ent_kerf.insert(0, str(hoja.get('kerf_usado', DEFAULT_KERF_IN)))

    # =========================================================
    # LÓGICA DEL MENÚ DESPLEGABLE (EL MES EN ACCIÓN)
    # =========================================================
    def actualizar_dropdown_lotes(self):
        if not hasattr(self.app, 'resultados_multilote') or not self.app.resultados_multilote:
            self.cmb_lotes.configure(values=["SIN ÓRDENES"], state="disabled")
            self.cmb_lotes.set("SIN ÓRDENES")
            return

        opciones = []
        for i, orden in enumerate(self.app.resultados_multilote):
            opciones.append(f"Work Order {i+1} [ Lote X{orden['lote_k']} ]")

        self.cmb_lotes.configure(values=opciones, state="normal")
        self.cmb_lotes.set(opciones[0])
        self.on_lote_selected(opciones[0])

    def on_lote_selected(self, val):
        try:
            idx = self.cmb_lotes.cget("values").index(val)
            self.lote_actual_idx = idx

            # Resultado visual del lote seleccionado
            self.app.resultados_nesting = self.app.resultados_multilote[idx]["data"]

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
            self._sincronizar_parts_con_lote_activo()

            self.procesar_lista_hojas(self.app.resultados_nesting)

            # Limpiar visor si cambiamos de orden
            if hasattr(self, 'visor'):
                self.visor.hoja_actual_data = None
                if hasattr(self.visor, 'canvas'):
                    for w in self.visor.canvas.winfo_children():
                        w.destroy()
                self.frame_ajuste_container.place_forget()

        except ValueError:
            pass

    def reabrir_modal_escenarios(self):
        if hasattr(self.app, 'ultimos_escenarios') and self.app.ultimos_escenarios:
            mostrar_modal_escenarios(self, self.app.ultimos_escenarios)
        else:
            messagebox.showinfo("Work Orders", "No hay estrategias de Work Orders generadas en este momento.")

    def restaurar_controles_tras_cancelacion(self):
        try:
            self.btn_run_nest.configure(state="normal")
        except Exception:
            pass
        try:
            self.btn_ver_lotes.configure(state="normal")
        except Exception:
            pass

    def ejecutar_nesting(self):
        if not self.app.datos_partes_actuales:
            return messagebox.showwarning("Atención", "No hay piezas importadas.")

        self.btn_run_nest.configure(state="disabled")
        self.btn_ver_lotes.configure(state="disabled")

        T = getattr(self.app, "multiplicador_tanques", 1)
        self.cantidad_tanques = str(T)
        self.lbl_cantidad.configure(text=f"Cantidad: {self.cantidad_tanques}")

        self.app.abrir_ventana_carga(
            "Optimizando Lotes..." if T >= 4 else "Ejecutando Nesting"
        )

        try:
            kerf_ui = float(self.ent_kerf.get())
        except Exception:
            kerf_ui = DEFAULT_KERF_IN
        opt_ui = self.cmb_opt.get() if hasattr(self, "cmb_opt") else "OPTIMIZAR LARGO Y ANCHO"

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
            # =========================================================
            # 📡 RECEPTOR DE TELEMETRÍA ASÍNCRONO
            # =========================================================
            def receptor_en_vivo(msg, pct):
                if hasattr(self.app, 'actualizar_progreso'):
                    self.app.after(0, lambda: self.app.actualizar_progreso(msg, pct))
                    self.app.after(0, self.app.update_idletasks)
            # =========================================================

            datos_placas = self.app.plates_manager.obtener_datos_placas()

            if T < 4:
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

                self.app.tiempo_calculo = time.time() - tiempo_inicio
                lista_unica = [{"lote_k": T, "data": res}]
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

            self.app.after(0, lambda: self.btn_ver_lotes.configure(state="normal"))
            self.app.after(0, lambda: mostrar_modal_escenarios(self, escenarios_resultados))

        except Exception as e:
            def throw_err(err=str(e)):
                if hasattr(self.app, 'cerrar_ventana_carga'):
                    self.app.cerrar_ventana_carga()
                self.btn_run_nest.configure(state="normal")
                self.btn_ver_lotes.configure(state="normal")
                messagebox.showerror("Error Interno", err)

            self.app.after(0, throw_err)

    def aplicar_escenario_seleccionado(self, resultados_list, top_window):
        top_window.destroy()
        self.finalizar(resultados_list)

    def finalizar(self, resultados_list):
        # Guardamos la lista completa de Work Orders
        self.app.resultados_multilote = resultados_list

        if hasattr(self.app, "guardar_historial"):
            self.app.guardar_historial()

        # NUEVO: construir inputs editables por lote
        self._reconstruir_editables_por_resultado(resultados_list)

        if hasattr(self.app, 'cerrar_ventana_carga'):
            self.app.cerrar_ventana_carga()

        self.btn_run_nest.configure(state="normal")

        # Desplegamos el menú de lotes
        self.actualizar_dropdown_lotes()

        # =========================================================
        # ⏱️ FORMATO DE TIEMPO PARA EL POP-UP
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
            f"⏱️ Tiempo de procesamiento: {tiempo_str}\n\n"
            "Acomodo listo en BORRADOR. Modifica si es necesario y exporta cuando termines."
        )

        messagebox.showinfo("Cálculo Terminado", mensaje)

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
        
    def procesar_lista_hojas(self, resultados):
        sincronizar_overlays_resultados(resultados)
        actualizar_eficiencias_resultados(resultados)
        sincronizar_sobrantes_rtz_en_resultados(
            resultados,
            wo_name=self._order_label_para_rtz(),
        )
        for clave in (resultados or {}):
            if isinstance((resultados or {}).get(clave), dict):
                self._recalcular_costos_grupo(clave)
        for w in self.lista_hojas.winfo_children():
            w.destroy()

        costo_proyecto = 0
        self.total_usd_empresa = 0.0
        self.total_usd_proveedor = 0.0

        for clave, info in resultados.items():
            header = ctk.CTkFrame(self.lista_hojas, fg_color="transparent")
            header.pack(fill="x", pady=(10, 0))
            efi_tanque = formatear_eficiencias_tanque(info)
            lbl_header = ctk.CTkLabel(
                header,
                text=f"📋 {clave}" + (f" | {efi_tanque}" if efi_tanque else ""),
                font=("Inter", 11, "bold"),
                text_color=COLOR_TEXTO_TITULO,
            )
            lbl_header.pack(side="left")
            self._bind_menu_compensar_calibre(header, lbl_header, clave)

            if "costo_total" in info:
                costo_proyecto += info["costo_total"]
            if "costo_empresa" in info:
                self.total_usd_empresa += info["costo_empresa"]
            if "costo_proveedor" in info:
                self.total_usd_proveedor += info["costo_proveedor"]

            hojas_del_material = info.get("hojas", [])

            if len(hojas_del_material) == 0:
                ctk.CTkLabel(
                    self.lista_hojas,
                    text=f"⚠️ {info.get('error', 'NO HAY EN INVENTARIO')}",
                    font=("Inter", 11, "bold"),
                    text_color="#EF4444",
                    wraplength=280
                ).pack(pady=10)
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
                        sufijo = (
                            f" · P{iguales.index(i) + 1}"
                            if len(iguales) > 1
                            else ""
                        )
                    else:
                        sufijo = ""
                    ignorada = bool(hoja.get("ignorar_deduccion", False))
                    es_rtzc = es_placa_madre_rtzc(hoja)
                    es_sobrante_rtz = es_placa_madre_sobrante_rtz(hoja)
                    prefijo_ign = (
                        "⊘ "
                        if ignorada
                        and not es_sobrante_rtz
                        and not es_rtzc
                        and not hoja.get("modo_largos_cu")
                        else ""
                    )
                    texto_btn = (
                        f"   ↳ {nombre_placa} (Accesorios) | {efi_txt}"
                        if es_retazo else
                        f"{prefijo_ign}◼ {nombre_placa}{sufijo}{origen_str} | {efi_txt}"
                    )
                    if es_rtzc:
                        color_fondo = "#1C1917"
                        color_texto = "#FB923C"
                    elif es_sobrante_rtz or es_retazo:
                        color_fondo = "#0F172A"
                        color_texto = "#38BDF8"
                    else:
                        color_fondo = "#1F2937" if ignorada else "#323741"
                        color_texto = "#94A3B8" if ignorada else ("#FCA5A5" if origen_str else "white")

                    fila_placa = ctk.CTkFrame(self.lista_hojas, fg_color="transparent")
                    fila_placa.pack(fill="x", pady=1, padx=(20, 0) if es_retazo else 0)

                    btn = ctk.CTkButton(
                        fila_placa,
                        text=texto_btn,
                        fg_color=color_fondo,
                        hover_color=COLOR_GRIS_MED,
                        text_color=color_texto,
                        anchor="w" if es_retazo else "center",
                        command=lambda h=hoja, c=clave: self.dibujar_hoja_full(h, c)
                    )
                    btn.pack(fill="x")
                    self._bind_menu_renestear_placa(btn, clave, hoja)

                    if not es_retazo and placa_debe_mostrar_opcion_ignorar(hoja, hojas_del_material):
                        self._crear_switch_ignorar_placa(fila_placa, clave, hoja, hojas_del_material)

        # Costo base del sistema: MXN. USD se calcula con tipo de cambio actual.
        self._actualizar_tipo_cambio()
        self.costo_mxn_val = float(costo_proyecto)
        tc = float(self.tipo_cambio_usdmxn or 18.50)
        self.costo_usd_val = (self.costo_mxn_val / tc) if tc > 0 else 0.0

    def _bind_menu_renestear_placa(self, btn, clave, hoja):
        # RTZ / mini-nest: sin menú contextual (no renestear ni cambiar placa).
        if hoja.get("es_retazo", False):
            return

        def on_rclick(event):
            top = self.winfo_toplevel()
            m = Menu(top, tearoff=0)
            m.add_command(
                label="Renestear esta placa",
                command=lambda c=clave, h=hoja: self.renestear_solo_placa(c, h),
            )
            m.add_command(
                label="Cambiar piezas a otra placa",
                command=lambda c=clave, h=hoja: abrir_modal_transferencia_masiva(self, c, h),
            )
            m.add_command(
                label="Renestear calibre completo",
                command=lambda c=clave: self.renestear_calibre_completo_ui(c),
            )
            m.add_command(
                label="Compensar esta placa (Plasma)",
                command=lambda c=clave, h=hoja: self.compensar_solo_placa(c, h),
            )
            sub_cambiar = Menu(m, tearoff=0)
            # Los empaques válidos son costosos: se calculan solo al abrir este submenú (postcommand),
            # no al mostrar el menú principal, para que "Renestear esta placa" responda al instante.
            sub_cambiar.configure(
                postcommand=lambda sm=sub_cambiar, c=clave, h=hoja: self._rellenar_submenu_cambiar_placa(
                    sm, c, h
                )
            )
            m.add_cascade(label="Cambiar de placa", menu=sub_cambiar)
            try:
                m.tk_popup(event.x_root, event.y_root)
            finally:
                m.grab_release()

        def bind_if_widget(w):
            if w is None:
                return
            try:
                w.bind("<Button-3>", on_rclick)
            except Exception:
                pass

        bind_if_widget(btn)
        bind_if_widget(getattr(btn, "_canvas", None))

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
        def on_rclick(event):
            top = self.winfo_toplevel()
            m = Menu(top, tearoff=0)
            m.add_command(
                label="Compensar calibre completo (Plasma)",
                command=lambda c=clave: self.compensar_calibre_completo(c),
            )
            try:
                m.tk_popup(event.x_root, event.y_root)
            finally:
                m.grab_release()

        for w in (header, lbl_header, getattr(lbl_header, "_canvas", None)):
            if w is None:
                continue
            try:
                w.bind("<Button-3>", on_rclick)
            except Exception:
                pass

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

    def _nombre_canonico_pieza(self, nom):
        s = str(nom or "").strip()
        if not s:
            return ""
        if "," in s:
            return s.split(",", 1)[0].strip()
        return s

    def _datos_partes_activos_para_nesting(self):
        datos = getattr(self.app, "datos_partes_actuales", []) or []
        return list(datos)

    def _contar_piezas_reales_grupo(self, clave):
        grp = (self.app.resultados_nesting or {}).get(clave) or {}
        hojas = grp.get("hojas") or []
        conteo = {}
        for hoja in hojas:
            if isinstance(hoja, dict) and hoja.get("cu_rtz_virtual"):
                continue
            for p in (hoja.get("piezas") or []):
                nom = self._nombre_canonico_pieza(p.get("nombre", ""))
                if not nom or self._es_pieza_virtual(nom):
                    continue
                conteo[nom] = conteo.get(nom, 0) + 1
        return conteo

    def _conteo_piezas_job_grupo(self, clave):
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
            if not nom or nom in fuente or poly is None or getattr(poly, "is_empty", True):
                return
            from shapely import affinity
            mx, my, _, _ = poly.bounds
            marks_ok = marks
            try:
                if marks_ok is None:
                    from shapely.geometry import LineString
                    marks_ok = LineString()
            except Exception:
                marks_ok = marks
            fuente[nom] = {
                "nombre": nom,
                "poly_base": affinity.translate(poly, -mx, -my),
                "marks_base": affinity.translate(marks_ok, -mx, -my) if hasattr(marks_ok, "is_empty") and not marks_ok.is_empty else marks_ok,
                "area_base": float(poly.area),
                "calibre": cal,
                "material": mat,
                "ruta": ruta,
            }

        # 1) Fuente primaria: geometría fresca desde rutas DXF.
        for p_nom, mat, qty, cal, st, ruta in getattr(self.app, "datos_partes_actuales", []) or []:
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
                if not nom or self._es_pieza_virtual(nom) or nom in fuente:
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

    def _marcar_hojas_compensadas_plasma(self, clave, resultado_grupo, compensados_por_nombre, offset_mm):
        if not isinstance(resultado_grupo, dict):
            return
        hojas = resultado_grupo.get("hojas") or []
        if not hojas:
            return

        # Fuente base para detección geométrica real (evita falsos negativos por mapeo de nombres).
        base_por_nombre = {}
        for nom, src in (self._construir_fuente_geometria_por_nombre(clave) or {}).items():
            try:
                minx, miny, maxx, maxy = src["poly_base"].bounds
                base_por_nombre[str(nom)] = (float(maxx - minx), float(maxy - miny))
            except Exception:
                continue

        cupos = {str(n): int(c or 0) for n, c in (compensados_por_nombre or {}).items() if int(c or 0) > 0}
        if not cupos:
            return

        tol_mm = 0.20
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

                marcado = False
                # 1) Detección primaria: comparar tamaño real de geometría final vs base.
                base_wh = base_por_nombre.get(nom)
                pols = p.get("poligonos") or []
                if base_wh and pols and pols[0]:
                    try:
                        xs = [pt[0] for pt in pols[0]]
                        ys = [pt[1] for pt in pols[0]]
                        w_fin = float(max(xs) - min(xs))
                        h_fin = float(max(ys) - min(ys))
                        w_base, h_base = base_wh
                        if (w_fin - w_base) > tol_mm or (h_fin - h_base) > tol_mm:
                            marcado = True
                    except Exception:
                        marcado = False

                # 2) Fallback por cupos (si no se pudo medir o no hay fuente base).
                if not marcado:
                    restante = int(cupos.get(nom, 0))
                    if restante > 0:
                        cupos[nom] = restante - 1
                        marcado = True

                if marcado:
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
            return messagebox.showwarning("Atención", "No hay resultados de nesting.")
        if clave not in self.app.resultados_nesting:
            return messagebox.showwarning("Atención", "No se encontró el calibre/material en el resultado.")

        try:
            k = float(self.ent_kerf.get())
        except Exception:
            return messagebox.showerror("Error", "Kerf inválido.")
        m = self.global_margin_val
        opt = self.cmb_opt.get() if hasattr(self, "cmb_opt") else "OPTIMIZAR LARGO Y ANCHO"
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
                    messagebox.showinfo(
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
                    messagebox.showerror(
                        "Compensación",
                        f"No se pudo completar el renesteo compensado.\n\nDetalle:\n{msg}",
                    )

                self.app.after(0, on_err)

        threading.Thread(target=worker, daemon=True).start()

    def compensar_solo_placa(self, clave, hoja):
        offset_mm = self._offset_compensacion_mm_desde_clave(clave)
        if offset_mm is None:
            return messagebox.showwarning(
                "Compensación",
                "No se pudo leer el calibre para calcular compensación plasma.",
            )
        bloque = self._desglosar_bloque_placa_mini(clave, hoja)
        resumen_placa = bloque.get("resumen_base") or {}
        if not resumen_placa:
            return messagebox.showwarning(
                "Compensación",
                "No se detectaron piezas reales en la placa seleccionada.",
            )
        self._renestear_clave_con_compensacion(
            clave,
            cupos_compensar_por_nombre=resumen_placa,
            offset_mm=offset_mm,
            titulo="Compensando placa y renesteando calibre...",
            # En compensación no aplicamos "llenado" adicional porque puede
            # reoptimizar con piezas no compensadas y diluir el resultado.
            post_fill=False,
            placa_id_objetivo=str(hoja.get("placa_id", "") or ""),
        )

    def _build_piezas_para_renest_calibre(self, clave):
        conteo_job = self._conteo_piezas_job_grupo(clave)
        conteo_nido = self._contar_piezas_reales_grupo(clave)
        conteo_total = conteo_job if conteo_job else conteo_nido
        if not conteo_total:
            return []
        fuente = self._construir_fuente_geometria_por_nombre(clave)
        if not fuente:
            return []
        piezas_out = []
        for nom, total in conteo_total.items():
            src = fuente.get(nom) or fuente.get(self._nombre_canonico_pieza(nom))
            if not src:
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
        return piezas_out

    def renestear_calibre_completo_ui(self, clave):
        if not getattr(self.app, "resultados_nesting", None):
            return messagebox.showwarning("Atención", "No hay resultados de nesting.")
        if clave not in self.app.resultados_nesting:
            return messagebox.showwarning("Atención", "No se encontró ese calibre/material.")
        piezas_pack = self._build_piezas_para_renest_calibre(clave)
        if not piezas_pack:
            return messagebox.showwarning(
                "Atención",
                "No se pudieron reconstruir las piezas de este calibre para renestear.",
            )
        if not messagebox.askyesno(
            "Renestear calibre completo",
            f"Se volverá a optimizar todo el calibre {clave} desde cero.\n\n¿Continuar?",
        ):
            return

        try:
            k = float(self.ent_kerf.get())
        except Exception:
            return messagebox.showerror("Error", "Kerf inválido.")

        m = self.global_margin_val
        opt = self.cmb_opt.get() if hasattr(self, "cmb_opt") else "OPTIMIZAR LARGO Y ANCHO"
        corner = self.global_corner_val

        if hasattr(self.app, "abrir_ventana_carga"):
            self.app.abrir_ventana_carga("Renesteando calibre completo...")

        def worker():
            backup_grp = copy.deepcopy((self.app.resultados_nesting or {}).get(clave))
            try:
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
                )
                resultado = raw[1] if isinstance(raw, tuple) and len(raw) == 2 else raw
                if not isinstance(resultado, dict) or resultado.get("error"):
                    raise RuntimeError(str((resultado or {}).get("error", "Sin resultado válido.")))
                if not (resultado.get("hojas") or []):
                    raise RuntimeError("El renesteo no generó hojas válidas.")

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
                    messagebox.showinfo(
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
                    messagebox.showerror(
                        "Renesteo",
                        f"No se pudo renestear el calibre completo.\n\nDetalle:\n{msg}",
                    )

                self.app.after(0, on_err)

        threading.Thread(target=worker, daemon=True).start()

    def compensar_calibre_completo(self, clave):
        if not getattr(self.app, "resultados_nesting", None):
            return messagebox.showwarning("Atención", "No hay resultados de nesting.")
        grp = self.app.resultados_nesting.get(clave)
        if not grp or "hojas" not in grp:
            return messagebox.showwarning("Atención", "No se encontró ese calibre/material en el resultado.")
        offset_mm = self._offset_compensacion_mm_desde_clave(clave)
        if offset_mm is None:
            return messagebox.showwarning(
                "Compensación",
                "No se pudo leer el calibre para calcular compensación plasma.",
            )
        conteo_total = self._contar_piezas_reales_grupo(clave)
        if not conteo_total:
            return messagebox.showwarning(
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

    def _obtener_candidatas_placa_validas(self, clave, hoja):
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
                k = float(self.ent_kerf.get())
            except Exception:
                k = DEFAULT_KERF_IN
        if m is None:
            m = self.global_margin_val
        if opt is None:
            opt = self.cmb_opt.get() if hasattr(self, "cmb_opt") else "OPTIMIZAR LARGO Y ANCHO"
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
            if self._candidata_cabe_piezas_empaque(
                piezas, cand["w_mm"], cand["h_mm"], k, m, opt, corner
            ):
                validas.append(cand)
        return validas

    def _rellenar_submenu_cambiar_placa(self, sub_menu, clave, hoja):
        """Llena el submenú al mostrarlo (postcommand); evita trabajo pesado al abrir el menú principal."""
        try:
            sub_menu.delete(0, "end")
        except Exception:
            return
        sub_menu.add_command(label="Calculando placas (esto puede tardar)...", state="disabled")

        if getattr(self, "_submenu_cambiar_busy", False):
            return
        self._submenu_cambiar_busy = True

        # Cacheamos valores de UI en el hilo principal para que el thread no toque widgets.
        try:
            self._cached_k_for_submenu = float(self.ent_kerf.get())
        except Exception:
            self._cached_k_for_submenu = DEFAULT_KERF_IN
        self._cached_m_for_submenu = self.global_margin_val
        try:
            self._cached_opt_for_submenu = self.cmb_opt.get()
        except Exception:
            self._cached_opt_for_submenu = "OPTIMIZAR LARGO Y ANCHO"
        self._cached_corner_for_submenu = self.global_corner_val

        def worker():
            try:
                candidatas = self._obtener_candidatas_placa_validas(clave, hoja)
            except Exception:
                candidatas = []

            def _apply():
                # Siempre liberamos el lock aunque el menú ya no exista.
                try:
                    # Si el menú ya no existe, no pasa nada.
                    try:
                        sub_menu.delete(0, "end")
                    except Exception:
                        return

                    if candidatas:
                        for cand in candidatas[:20]:
                            txt = (
                                f"{cand['id']} | {cand['w_in']:.1f}\"x{cand['h_in']:.1f}\""
                                f" | ${cand.get('precio', 0.0):,.2f} MXN"
                            )
                            sub_menu.add_command(
                                label=txt,
                                command=lambda c=clave, h=hoja, p=cand: self.cambiar_placa_y_renestear(c, h, p),
                            )
                    else:
                        sub_menu.add_command(
                            label="Ninguna placa del inventario cabe estas piezas",
                            state="disabled",
                        )
                finally:
                    self._submenu_cambiar_busy = False

            self.after(0, _apply)

        threading.Thread(target=worker, daemon=True).start()

    def _replicar_lote_activo_a_gemelos(self):
        resultados_ml = getattr(self.app, "resultados_multilote", None)
        if not isinstance(resultados_ml, list) or not resultados_ml:
            return

        idx = int(getattr(self, "lote_actual_idx", 0) or 0)
        if idx < 0 or idx >= len(resultados_ml):
            return

        lote_k_ref = resultados_ml[idx].get("lote_k")
        data_ref = copy.deepcopy(self.app.resultados_nesting)
        for j, orden in enumerate(resultados_ml):
            if j == idx:
                continue
            if orden.get("lote_k") == lote_k_ref:
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

    def _toggle_ignorar_deduccion_placa(self, clave, hoja, ignorar: bool):
        if not isinstance(hoja, dict) or hoja.get("es_retazo"):
            return
        hoja["ignorar_deduccion"] = bool(ignorar)
        self._sincronizar_sobrante_rtz_placa(clave, hoja, ignorar)
        self._recalcular_costos_grupo(clave)
        self._replicar_lote_activo_a_gemelos()
        self.procesar_lista_hojas(self.app.resultados_nesting)

    def _crear_switch_ignorar_placa(self, parent, clave, hoja, hojas_grupo):
        ignorada = bool(hoja.get("ignorar_deduccion", False))
        efi_real = eficiencia_para_umbral_ignorar(hoja, hojas_grupo)

        fila_ign = ctk.CTkFrame(parent, fg_color="#111827", corner_radius=8, border_width=1, border_color="#374151")
        fila_ign.pack(fill="x", pady=(4, 0), padx=2)

        texto_estado = "ON · Sobrante" if ignorada else "OFF · Comprar"
        ctk.CTkLabel(
            fila_ign,
            text=f"Ignorar deducción  |  Real {efi_real:.1f}%",
            font=("Inter", 10),
            text_color="#9CA3AF",
            anchor="w",
        ).pack(side="left", padx=(10, 6), pady=6)

        var_ign = ctk.BooleanVar(value=ignorada)

        def _on_switch():
            self._toggle_ignorar_deduccion_placa(clave, hoja, bool(var_ign.get()))

        switch = ctk.CTkSwitch(
            fila_ign,
            text=texto_estado,
            variable=var_ign,
            command=_on_switch,
            width=118,
            switch_width=46,
            switch_height=22,
            font=("Inter", 10, "bold"),
            text_color="#F9FAFB" if ignorada else "#FECACA",
            progress_color="#16A34A",
            fg_color="#DC2626",
            button_color="#1F2937",
            button_hover_color="#374151",
        )
        switch.pack(side="right", padx=(4, 10), pady=5)

    def _recalcular_costos_grupo(self, clave):
        grp = self.app.resultados_nesting.get(clave)
        if not isinstance(grp, dict):
            return
        hojas = grp.get("hojas", []) or []
        costo_total = sum(
            float(h.get("precio_placa", 0.0) or 0.0)
            for h in hojas
            if hoja_cuenta_para_deduccion(h)
        )
        costo_empresa = sum(
            float(h.get("precio_placa", 0.0) or 0.0)
            for h in hojas
            if hoja_cuenta_para_deduccion(h)
            and str(h.get("origen_placa", "EMPRESA")).upper() == "EMPRESA"
        )
        costo_proveedor = sum(
            float(h.get("precio_placa", 0.0) or 0.0)
            for h in hojas
            if hoja_cuenta_para_deduccion(h)
            and str(h.get("origen_placa", "EMPRESA")).upper() != "EMPRESA"
        )
        grp["costo_total"] = costo_total
        grp["costo_empresa"] = costo_empresa
        grp["costo_proveedor"] = costo_proveedor

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

    def cambiar_placa_y_renestear(self, clave, hoja, candidata):
        if not hoja or not candidata:
            return
        if hoja.get("es_retazo", False):
            return messagebox.showinfo(
                "Cambiar de placa",
                "Las piezas en retazo/RTZ o mini-nest no se pueden reasignar a otra placa del inventario.",
            )
        piezas = self._piezas_pack_madre_para_empaque(clave, hoja)
        if piezas:
            try:
                k = float(self.ent_kerf.get())
            except Exception:
                k = DEFAULT_KERF_IN
            m = self.global_margin_val
            opt = self.cmb_opt.get() if hasattr(self, "cmb_opt") else "OPTIMIZAR LARGO Y ANCHO"
            corner = self.global_corner_val
            if not self._candidata_cabe_piezas_empaque(
                piezas,
                float(candidata["w_mm"]),
                float(candidata["h_mm"]),
                k,
                m,
                opt,
                corner,
            ):
                return messagebox.showwarning(
                    "Cambiar de placa",
                    "Las piezas de esta placa no caben en la placa seleccionada con la configuración actual (kerf/margen).",
                )
        hoja_tmp = copy.deepcopy(hoja)
        hoja_tmp["placa_id"] = candidata["id"]
        hoja_tmp["placa_w"] = candidata["w_mm"]
        hoja_tmp["placa_h"] = candidata["h_mm"]
        hoja_tmp["precio_placa"] = candidata.get("precio", 0.0)
        hoja_tmp["origen_placa"] = candidata.get("origen", hoja.get("origen_placa", "EMPRESA"))
        self.renestear_solo_placa(clave, hoja_tmp, post_fill=True)

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
    ):
        material_hoja = clave.split("_")[1] if "_" in clave else clave
        calibre_hoja = clave.split("_")[0] if "_" in clave else ""

        bloque = self._desglosar_bloque_placa_mini(clave, hoja)
        resumen_hoja = bloque["resumen_base"]
        idx_retazos_asociados = bloque["idx_retazos"]

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
            if compensar_plasma:
                off = (
                    float(offset_mm_forzado)
                    if offset_mm_forzado is not None
                    else (self._offset_compensacion_mm_desde_clave(clave) or 0.0)
                )
                comp = self._aplicar_compensacion_poligono(poly, off)
                if comp is None or comp.is_empty:
                    continue
                poly = comp
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

        def _build_pack_list(resumen):
            out = []
            for nom, cnt in (resumen or {}).items():
                src = piezas_fuente.get(nom)
                if not src:
                    continue
                for _ in range(int(cnt)):
                    out.append(copy.deepcopy(src))
            return out

        piezas_a_reprocesar = _build_pack_list(resumen_hoja)
        nueva = None
        if piezas_a_reprocesar:
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
                ):
                    if mk in hoja:
                        nh[mk] = hoja[mk]
                if compensar_plasma:
                    nh["plasma_compensado_manual"] = True
                    nh["plasma_offset_mm_manual"] = float(
                        offset_mm_forzado
                        if offset_mm_forzado is not None
                        else (self._offset_compensacion_mm_desde_clave(clave) or 0.0)
                    )
                nueva = nh
        else:
            nueva = self.app.motor_nesting.recalcular_hoja_full(
                hoja, k, m, opt, corner
            )
            if nueva:
                for mk in (
                    "origen_placa",
                    "es_retazo",
                    "id_remanente_usado",
                    "lote_desc",
                    "lote_mult",
                ):
                    if mk in hoja:
                        nueva[mk] = hoja[mk]
                if compensar_plasma:
                    nueva["plasma_compensado_manual"] = True
                    nueva["plasma_offset_mm_manual"] = float(
                        offset_mm_forzado
                        if offset_mm_forzado is not None
                        else (self._offset_compensacion_mm_desde_clave(clave) or 0.0)
                    )
        return nueva, idx_retazos_asociados

    def renestear_solo_placa(
        self,
        clave,
        hoja,
        post_fill=False,
        compensar_plasma=False,
        offset_mm_forzado=None,
    ):
        if not getattr(self.app, "resultados_nesting", None):
            return messagebox.showwarning("Atención", "No hay resultados de nesting.")
        if clave not in self.app.resultados_nesting:
            return messagebox.showwarning("Atención", "Material no encontrado en el resultado actual.")
        if not hoja:
            return
        if hoja.get("es_retazo", False):
            return messagebox.showinfo(
                "Renestear placa",
                "Las placas reutilizadas (RTZ) o mini-nest no se pueden renestear desde el menú contextual.",
            )
        try:
            k = float(self.ent_kerf.get())
        except Exception:
            return messagebox.showerror("Error", "Valores no válidos.")
        m = self.global_margin_val

        if hasattr(self.app, "abrir_ventana_carga"):
            self.app.abrir_ventana_carga("Renesteando placa...")

        bloque_objetivo = self._desglosar_bloque_placa_mini(clave, hoja)
        idx_objetivo = bloque_objetivo.get("idx_base", -1)
        hoja_ref = hoja

        def worker():
            if hasattr(self.app, "actualizar_progreso"):
                self.app.actualizar_progreso("Preparando geometrías...", 0.1)
            if hasattr(self.app, "actualizar_progreso"):
                self.app.actualizar_progreso("Extrayendo datos de piezas...", 0.3)
            opt = self.cmb_opt.get()
            corner = self.global_corner_val
            if hasattr(self.app, "actualizar_progreso"):
                self.app.actualizar_progreso("Ejecutando motor...", 0.6)
            nueva, idx_retazos_asociados = self._recalcular_hoja_con_contexto(
                clave,
                hoja,
                k,
                m,
                opt,
                corner,
                compensar_plasma=compensar_plasma,
                offset_mm_forzado=offset_mm_forzado,
            )

            if hasattr(self.app, "actualizar_progreso"):
                self.app.actualizar_progreso("Actualizando vista...", 0.9)
            self.app.after(
                0, lambda: self.finalizar_recalc(
                    nueva,
                    clave_renest=clave,
                    post_fill=post_fill,
                    idx_retazos_asociados=idx_retazos_asociados,
                    nuevas_retazos=None,
                    hoja_original=copy.deepcopy(hoja),
                    tiene_minis=bool(idx_retazos_asociados),
                    idx_objetivo=idx_objetivo,
                    hoja_ref=hoja_ref,
                )
            )

        threading.Thread(target=worker, daemon=True).start()

    def aplicar_cambios_locales(self):
        if not self.hoja_actual_data:
            return
        try:
            k, m = float(self.ent_kerf.get()), self.global_margin_val
        except Exception:
            return messagebox.showerror("Error", "Valores no válidos.")

        if hasattr(self.app, 'abrir_ventana_carga'):
            self.app.abrir_ventana_carga("Recalculando Placa...")

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
                    self.cmb_opt.get(),
                    self.global_corner_val,
                    intentos=8,
                    debug_tag="recalc_local",
                )
                if nh:
                    nh.update({
                        'placa_id': self.hoja_actual_data['placa_id'],
                        'placa_w': self.hoja_actual_data['placa_w'],
                        'placa_h': self.hoja_actual_data['placa_h'],
                        'precio_placa': self.hoja_actual_data.get('precio_placa', 0),
                        'kerf_usado': k,
                        'margin_usado': m,
                        'opt_usado': self.cmb_opt.get(),
                        'corner_usado': self.global_corner_val
                    })
                    nueva = nh
            else:
                nueva = self.app.motor_nesting.recalcular_hoja_full(
                    self.hoja_actual_data,
                    k,
                    m,
                    self.cmb_opt.get(),
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
    ):
        if hasattr(self.app, 'cerrar_ventana_carga'):
            self.app.cerrar_ventana_carga()

        clv = clave_renest if clave_renest is not None else self.clave_actual

        if nueva:
            grp = self.app.resultados_nesting.get(clv)
            if not grp or "hojas" not in grp:
                messagebox.showwarning("Atención", "No se encontró el grupo de material en el resultado.")
                return

            # Regla de seguridad: si es placa madre con mini-nests y el acomodo
            # no cambió en esencia, conservar exactamente la placa original.
            if tiene_minis and hoja_original and self._placa_equivalente_en_esencia(hoja_original, nueva):
                nueva = copy.deepcopy(hoja_original)
                idx_retazos_asociados = None
                nuevas_retazos = None

            if idx_retazos_asociados and nuevas_retazos is not None:
                for ridx in sorted(set(idx_retazos_asociados), reverse=True):
                    if 0 <= ridx < len(grp["hojas"]) and grp["hojas"][ridx].get("es_retazo", False):
                        grp["hojas"].pop(ridx)
            hoja_actualizada = nueva
            idx_match = self._resolver_indice_hoja_objetivo(
                grp,
                nueva,
                idx_objetivo=idx_objetivo,
                hoja_ref=hoja_ref,
                hoja_original=hoja_original,
            )
            if idx_match >= 0:
                self.app.resultados_nesting[clv]["hojas"][idx_match] = nueva
                hoja_ref = self.app.resultados_nesting[clv]["hojas"][idx_match]
                if post_fill:
                    self._llenar_placa_desde_otras_hojas(clv, hoja_ref)
                hoja_actualizada = self.app.resultados_nesting[clv]["hojas"][idx_match]
                if nuevas_retazos:
                    pos = idx_match + 1
                    for hret in nuevas_retazos:
                        self.app.resultados_nesting[clv]["hojas"].insert(pos, hret)
                        pos += 1
            self._recalcular_costos_grupo(clv)
            self._replicar_lote_activo_a_gemelos()
            if idx_match == -1 and grp["hojas"]:
                hoja_actualizada = grp["hojas"][0]
            self.dibujar_hoja_full(hoja_actualizada, clv)
            self.procesar_lista_hojas(self.app.resultados_nesting)
        else:
            messagebox.showwarning(
                "Atención",
                "No se logró un acomodo válido para esa placa (sobran piezas o Kerf/márgenes incompatibles).",
            )

    def cargar_workspace_swo(self):
        ruta_archivo = filedialog.askopenfilename(
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
            messagebox.showinfo("Éxito", "Workspace cargado y O.C. actualizada.")
        except Exception as e:
            messagebox.showerror("Error", f"Fallo al cargar: {e}")

    def ejecutar_transferencia_masiva(self, idx_destino, hojas_disp, hoja_origen, clave, ventana):
        if idx_destino == -1:
            return messagebox.showwarning("Atención", "Debes seleccionar una placa destino.")
        hoja_destino = hojas_disp[idx_destino]
        ventana.destroy()

        if hasattr(self.app, "abrir_ventana_carga"):
            self.app.abrir_ventana_carga("Moviendo piezas a otra placa...")

        def worker():
            resultado = self.app.motor_nesting.transferir_piezas_a_placa(
                self.app.resultados_nesting,
                hoja_origen,
                hoja_destino,
            )
            self.app.after(
                0,
                lambda r=resultado: self.finalizar_transferencia_masiva(r, hoja_destino, clave),
            )

        threading.Thread(target=worker, daemon=True).start()

    def finalizar_transferencia_masiva(self, resultado, hoja_destino, clave):
        if hasattr(self.app, "cerrar_ventana_carga"):
            self.app.cerrar_ventana_carga()

        movidas = int((resultado or {}).get("movidas", 0) or 0)
        restantes = int((resultado or {}).get("restantes", 0) or 0)

        if movidas > 0:
            self.visor.limpiar_seleccion_piezas()
            self.on_piece_selected()
            self.btn_transferir.configure(state="disabled")
            self.btn_rot_90.configure(state="disabled")
            self.btn_rot_m1.configure(state="disabled")
            self.btn_rot_p1.configure(state="disabled")
            self._recalcular_costos_grupo(clave)
            self._replicar_lote_activo_a_gemelos()
            self.procesar_lista_hojas(self.app.resultados_nesting)
            self.dibujar_hoja_full(hoja_destino, clave)
            messagebox.showinfo(
                "Transferencia masiva",
                f"Piezas movidas: {movidas}\n"
                f"Piezas que permanecen en origen: {restantes}",
            )
        else:
            messagebox.showwarning(
                "Transferencia masiva",
                "No se pudo mover ninguna pieza a la placa destino con la configuración actual.",
            )

    def ejecutar_transferencia(self, idx_destino, hojas_disp, ventana):
        if idx_destino == -1:
            return messagebox.showwarning("Atención", "Debes seleccionar una placa.")
        hoja_destino = hojas_disp[idx_destino]
        piezas_sel = list(self.visor.piezas_seleccionadas)
        if not piezas_sel:
            return messagebox.showwarning("Atención", "Debes seleccionar al menos una pieza.")
        ventana.destroy()

        if hasattr(self.app, 'abrir_ventana_carga'):
            msg_carga = (
                f"Transfiriendo {len(piezas_sel)} piezas..."
                if len(piezas_sel) > 1
                else "Transfiriendo y reoptimizando..."
            )
            self.app.abrir_ventana_carga(msg_carga)

        def worker():
            resultado = self.app.motor_nesting.transferir_piezas_a_placa(
                self.app.resultados_nesting,
                self.hoja_actual_data,
                hoja_destino,
                piezas_especificas=piezas_sel,
            )
            self.app.after(0, lambda r=resultado: self.finalizar_transferencia(r, hoja_destino))

        threading.Thread(target=worker, daemon=True).start()

    def finalizar_transferencia(self, exito, hoja_destino=None):
        if hasattr(self.app, 'cerrar_ventana_carga'):
            self.app.cerrar_ventana_carga()

        resultado = None
        if isinstance(exito, dict):
            resultado = exito
            exito = bool(resultado.get("ok"))

        if exito:
            self.visor.limpiar_seleccion_piezas()
            self.on_piece_selected()

            self.procesar_lista_hojas(self.app.resultados_nesting)
            if hoja_destino is not None:
                self.dibujar_hoja_full(hoja_destino, self.clave_actual)
            else:
                hojas = self.app.resultados_nesting.get(self.clave_actual, {}).get("hojas", [])
                if hojas:
                    self.dibujar_hoja_full(hojas[0], self.clave_actual)

            if resultado and int(resultado.get("solicitadas", 0) or 0) > 1:
                movidas = int(resultado.get("movidas", 0) or 0)
                solicitadas = int(resultado.get("solicitadas", 0) or 0)
                restantes = int(resultado.get("restantes", 0) or 0)
                if restantes > 0:
                    messagebox.showwarning(
                        "Transferencia parcial",
                        f"Se movieron {movidas} de {solicitadas} piezas seleccionadas.\n"
                        f"{restantes} pieza(s) permanecen en la placa origen.",
                    )
                else:
                    messagebox.showinfo(
                        "Éxito",
                        f"Se movieron las {movidas} piezas seleccionadas correctamente.",
                    )
            else:
                messagebox.showinfo("Éxito", "Transferencia exitosa.")
        else:
            if resultado and int(resultado.get("solicitadas", 0) or 0) > 1:
                messagebox.showerror(
                    "Falló",
                    "No hay espacio suficiente en destino para las piezas seleccionadas.",
                )
            else:
                messagebox.showerror("Falló", "No hay espacio suficiente en destino.")

    # =========================================================
    # EL EXPORTADOR HÍBRIDO (El más rápido y automático)
    # =========================================================
    def exportar_resultados_dxf(self):
        if not hasattr(self.app, 'resultados_multilote') or not self.app.resultados_multilote:
            return messagebox.showwarning("Atención", "No hay datos para exportar.")

        # =====================================================
        # === RADAR DE RUTAS DE IMPORTACIÓN (DIAGNÓSTICO) ===
        # =====================================================
        print("\n" + "=" * 50)
        print("🔍 VERIFICACIÓN DE ORIGEN DE DXFs (S.W.O.)")
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

        respuesta_3d = messagebox.askyesno(
            "Generación 3D",
            "¿Generar modelos 3D ahora mismo?\n(SÍ tardará más, NO generará solo DXF)"
        )
        print(f"[DEBUG] Respuesta 3D: {respuesta_3d} (True=Yes, False=No)")

        if hasattr(self.app, 'abrir_ventana_carga'):
            self.app.abrir_ventana_carga("Procesando Exportación...")

        def worker():
            try:
                db_conf = {
                    "host": "192.168.2.80",
                    "database": "nestingpro_db",
                    "user": "postgres",
                    "password": "nesting123",
                    "port": "5433"
                }

                try:
                    r_base = os.path.dirname(os.path.dirname(os.path.dirname(self.app.datos_partes_actuales[0][5])))
                except Exception:
                    r_base = os.path.expanduser("~/Desktop")

                rutas_generadas = []
                job_activo = getattr(self.app, 'job_activo', 'JOB').strip().upper()

                if not hasattr(self.app, "wo_reales_por_lote") or self.app.wo_reales_por_lote is None:
                    self.app.wo_reales_por_lote = {}

                # ======================================================
                # EL BYPASS AUTOMÁTICO (TEXT FILE EN TU DISCO X:)
                # ======================================================
                usando_offline = False
                ruta_txt = os.path.join(r_base, "contador_emergencia.txt")

                try:
                    consecutivo_base = obtener_siguiente_consecutivo(db_conf)
                except Exception:
                    usando_offline = True
                    if os.path.exists(ruta_txt):
                        with open(ruta_txt, "r") as f:
                            consecutivo_base = int(f.read().strip())
                    else:
                        consecutivo_base = 1

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
                        es_swo=es_swo_flag
                    )

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
                        db_config=db_conf
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
                        wo_label=n_wo
                    )

                    # 2) Después guardamos en PostgreSQL
                    try:
                        guardar_nesting_en_postgresql(
                            job_activo,
                            n_wo,
                            mini_resultados,
                            db_conf,
                            "COMPLETADO" if respuesta_3d else "PENDIENTE",
                            ruta_export,
                            limpiar_previos=(i == 0)
                        )
                    except Exception as e:
                        print(f"[PQART][ERROR] No se pudo guardar en PostgreSQL después de exportar DXF: {e}")

                    # NUEVO: guardar la WO oficial del lote exportado
                    self.app.wo_reales_por_lote[i] = str(n_wo)
                    


                    rutas_generadas.append(os.path.join(r_base, n_wo))

                # --- FIN DEL BUCLE MULTI-LOTE ---

                # 3) EJECUTAR AVANCE DE CENTRALIZED SYSTEM FUERA DEL BUCLE
                try:
                    from modules.nesting_engine.api_client import avanzar_job_centralizado, avanzar_swo_centralizado
                    
                    jobs_involved = set()
                    try:
                        import psycopg2
                        conn = psycopg2.connect(**db_conf)
                        with conn.cursor() as cur:
                            if es_swo_flag:
                                cur.execute("SELECT DISTINCT job FROM reporte_cortes WHERE super_work_order = %s AND job IS NOT NULL", (job_activo,))
                                for r in cur.fetchall():
                                    if r[0]: jobs_involved.add(r[0].strip())
                                cur.execute("SELECT DISTINCT job_numero FROM diccionario_swo WHERE swo_id = %s", (job_activo,))
                                for r in cur.fetchall():
                                    if r[0]: jobs_involved.add(r[0].strip())
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
                        
                    # 4) Ejecutar la API de ContPAQ UNA SOLA VEZ por Job/SWO al finalizar
                    from modules.nesting_engine.api_client import trigger_po_contpaq, trigger_pedido_po
                    if es_swo_flag:
                        trigger_po_contpaq(job_activo)
                    else:
                        trigger_pedido_po(job_activo)
                    
                except Exception as api_err:
                    print(f"[API_TRIGGER][ERROR] Error crítico en el avance: {api_err}")

                total_carpetas = len(rutas_generadas)

                if usando_offline:
                    with open(ruta_txt, "w") as f:
                        f.write(str(consecutivo_base + total_carpetas))

                mensaje_final = f"Se exportaron {total_carpetas} Órdenes de Trabajo separadas."
                if usando_offline:
                    mensaje_final += "\n\n⚠️ (AVISO: Se usó el contador Offline porque el Servidor está desconectado)."

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
            messagebox.showinfo("Éxito", f"{mensaje}\n{ruta}")
            try:
                os.startfile(ruta)
            except Exception:
                pass
        else:
            messagebox.showerror("Error", mensaje)