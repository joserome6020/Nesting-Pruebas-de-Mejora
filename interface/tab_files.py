import os
import re
import threading
import csv
import shutil
import customtkinter as ctk
from tkinter import messagebox

import config
from modules.scanner import EscanerServidor
from modules.processed_layers import ProcesadorDXF

COLOR_TARJETA = "#FFFFFF"
COLOR_BORDE = "#CBD5E1"
COLOR_GRIS_DARK = "#1E293B"
COLOR_GRIS_MED = "#475569"
COLOR_TEXTO_TITULO = "#0F172A"
COLOR_TEXTO_SECUNDARIO = "#64748B"
COLOR_FONDO_APP = "#F1F5F9"


class TabFiles(ctk.CTkFrame):
    def __init__(self, master, app_principal):
        super().__init__(master, fg_color="transparent")
        self.app = app_principal
        self.escaner = EscanerServidor()
        self.procesador = ProcesadorDXF()

        if not hasattr(self.app, "meta_pdf_por_ruta"):
            self.app.meta_pdf_por_ruta = {}

        self.setup_ui()

    def _normalizar_ruta(self, ruta):
        try:
            return os.path.normcase(os.path.normpath(str(ruta)))
        except Exception:
            return str(ruta)

    def _normalizar_material(self, texto_material):
        mat = str(texto_material or "").strip().upper()
        mat = mat.replace("_", " ")
        mat = re.sub(r"\s+", " ", mat)

        # Normalización bilingüe -> valor interno estable
        if ("CARBON" in mat and "STEEL" in mat) or ("STEEL" in mat and "CARBON" in mat):
            return "CARBONO"
        if "ACERO" in mat and "CARBONO" in mat:
            return "CARBONO"

        if "STAINLESS" in mat or "INOX" in mat or "INOXIDABLE" in mat:
            return "INOXIDABLE"

        if "ALUMINUM" in mat or "ALUMINIO" in mat:
            return "ALUMINIO"

        if "GALVANIZED" in mat or "GALVANIZADO" in mat:
            return "GALVANIZADO"

        # Si no cae en un caso conocido, devolver limpio
        return mat if mat else "CARBONO"

    def _parsear_nombre_dxf(self, nombre_archivo):
        nombre_base = os.path.splitext(os.path.basename(str(nombre_archivo)))[0]
        partes = [p.strip() for p in nombre_base.split(",") if p.strip()]

        if not partes:
            return nombre_base, "CARBONO", "1", "0.375"

        pieza = partes[0]
        qty_str = "1"
        cal = "0.375"
        material_tokens = []

        for token in partes[1:]:
            token_limpio = token.strip()
            token_up = token_limpio.upper()

            # CANTIDAD: español + inglés
            m_qty = re.search(
                r"\b(?:QTY|QUANTITY|CANT|CANTIDAD)\b\s*[:=]?\s*(\d+)",
                token_up
            )
            if m_qty:
                qty_str = m_qty.group(1)
                continue

            # CALIBRE / ESPESOR: español + inglés
            m_cal = re.search(
                r"\b(?:CAL|CALIBRE|GA|GAUGE|THK|THICK|THICKNESS|ESP|ESPESOR)\b\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)",
                token_up
            )
            if m_cal:
                cal = m_cal.group(1)
                continue

            # Si viene solo el número, también tomarlo como calibre
            if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", token_up):
                cal = token_up
                continue

            # Todo lo demás se considera parte del material
            material_tokens.append(token_limpio)

        material_crudo = ", ".join(material_tokens)
        material_final = self._normalizar_material(material_crudo)

        return pieza, material_final, qty_str, cal

    def _listar_dxfs_recursivo(self, carpeta_base):
        """Lista DXF dentro de AutoDXF aceptando raíz y subcarpetas."""
        out = []
        base = str(carpeta_base or "").strip()
        if not base or not os.path.isdir(base):
            return out

        excluidas = {"processed files", "procesados", "nesting", "__pycache__"}
        for root, dirs, files in os.walk(base):
            # Evita re-consumir artefactos ya procesados/exportados.
            dirs[:] = [d for d in dirs if d.strip().lower() not in excluidas]
            for f in files:
                if str(f).lower().endswith(".dxf"):
                    out.append(os.path.join(root, f))
        return out

    def _nombre_destino_unico(self, nombre_original, usados):
        base, ext = os.path.splitext(str(nombre_original))
        candidato = f"{base}{ext}"
        i = 2
        while candidato.lower() in usados:
            candidato = f"{base}__{i}{ext}"
            i += 1
        usados.add(candidato.lower())
        return candidato

    def _buscar_dxf_item_en_autodxf(self, ruta_autodxf, item):
        """
        Busca un item DXF en AutoDXF:
        1) Prioriza Processed Files (si existe),
        2) luego búsqueda recursiva general.
        """
        item_limpio = str(item or "").strip().lower()
        if not item_limpio:
            return ""

        candidatos = []
        ruta_proc = os.path.join(ruta_autodxf, "Processed Files")
        if os.path.isdir(ruta_proc):
            candidatos.extend(self._listar_dxfs_recursivo(ruta_proc))
        if os.path.isdir(ruta_autodxf):
            candidatos.extend(self._listar_dxfs_recursivo(ruta_autodxf))

        # De-duplicar manteniendo orden.
        vistos = set()
        ordenados = []
        for p in candidatos:
            k = self._normalizar_ruta(p)
            if k in vistos:
                continue
            vistos.add(k)
            ordenados.append(p)

        for ruta in ordenados:
            f_lower = os.path.basename(ruta).lower()
            if (
                f_lower == f"{item_limpio}.dxf"
                or f_lower.startswith(f"{item_limpio},")
                or f_lower.startswith(f"{item_limpio} ")
            ):
                return ruta
        return ""

    def setup_ui(self):
        frame_central = ctk.CTkFrame(
            self,
            fg_color=COLOR_TARJETA,
            border_width=1,
            border_color=COLOR_BORDE,
            corner_radius=20,
            width=950,
            height=500
        )
        frame_central.place(relx=0.5, rely=0.45, anchor="center")
        frame_central.pack_propagate(False)

        ctk.CTkLabel(
            frame_central,
            text="CONEXIÓN CON EL SERVIDOR",
            font=("Inter", 22, "bold"),
            text_color=COLOR_TEXTO_TITULO
        ).pack(pady=(40, 20))

        # BOTÓN 1: IMPORTAR JOB NORMAL
        self.btn_nest_scan = ctk.CTkButton(
            frame_central,
            text="☁  IMPORTAR JOB INDIVIDUAL\n(Ingeniería)",
            font=("Inter", 16, "bold"),
            width=450,
            height=80,
            corner_radius=12,
            fg_color=COLOR_GRIS_DARK,
            hover_color=COLOR_GRIS_MED,
            command=self.ejecutar_escaneo_servidor
        )
        self.btn_nest_scan.pack(pady=(20, 15))

        # BOTÓN 2: IMPORTAR SWO DESDE LA WEB
        self.btn_swo_web = ctk.CTkButton(
            frame_central,
            text="📥 IMPORTAR S.W.O.\n(Fusión desde Tablero Web)",
            font=("Inter", 16, "bold"),
            width=450,
            height=80,
            corner_radius=12,
            fg_color="#455E75",
            hover_color="#334659",
            command=self.buscar_swos_pendientes
        )
        self.btn_swo_web.pack(pady=(0, 20))

        self.lbl_status = ctk.CTkLabel(
            frame_central,
            text=f"Ruta Objetivo: {config.RUTA_SERVIDOR_RAIZ}",
            text_color=COLOR_TEXTO_SECUNDARIO,
            font=("Inter", 11),
            wraplength=900
        )
        self.lbl_status.pack(pady=(10, 20))

    # ==========================================
    # LÓGICA DE CENTRADO DINÁMICO
    # ==========================================
    def centrar_popup(self, ventana_hija):
        """Calcula la posición para que el popup aparezca centrado en la App principal"""
        ventana_hija.update_idletasks()
        x_p = self.app.winfo_x()
        y_p = self.app.winfo_y()
        w_p = self.app.winfo_width()
        h_p = self.app.winfo_height()

        w_h = ventana_hija.winfo_width()
        h_h = ventana_hija.winfo_height()

        x_c = x_p + (w_p // 2) - (w_h // 2)
        y_c = y_p + (h_p // 2) - (h_h // 2)

        ventana_hija.geometry(f"+{x_c}+{y_c}")

    # ==========================================
    # LÓGICA DE ESCANEO DE JOBS NORMALES
    # ==========================================
    def ejecutar_escaneo_servidor(self):
        self.btn_nest_scan.configure(state="disabled", text="ESCANEANDO...")
        threading.Thread(target=self.thread_escaneo, daemon=True).start()

    def thread_escaneo(self):
        try:
            jobs, err = self.escaner.buscar_nuevos_jobs(self.app.jobs_procesados)
            self.after(0, lambda: self.after_escaneo(jobs, err))
        except Exception as e:
            self.after(0, lambda: self.after_escaneo([], str(e)))

    def after_escaneo(self, jobs, err=None):
        self.btn_nest_scan.configure(state="normal", text="☁  IMPORTAR JOB INDIVIDUAL\n(Ingeniería)")
        if err:
            messagebox.showerror("Error", err)
            return
        if not jobs or len(jobs) == 0:
            messagebox.showinfo("Estatus", "No hay nuevos Jobs.")
            return
        self.mostrar_selector_jobs(jobs)

    def mostrar_selector_jobs(self, jobs):
        top = ctk.CTkToplevel(self)
        top.title("IMPORTAR TRABAJOS")
        top.geometry("800x600")

        self.centrar_popup(top)
        top.configure(fg_color=COLOR_FONDO_APP)
        top.transient(self.app)
        top.grab_set()

        ctk.CTkLabel(
            top,
            text="Seleccione el Job a Procesar",
            font=("Inter", 18, "bold"),
            text_color=COLOR_TEXTO_TITULO
        ).pack(pady=20)

        scroll = ctk.CTkScrollableFrame(top, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=30, pady=10)

        jobs_unicos = {j['job_name']: j for j in jobs}.values()

        for job in jobs_unicos:
            card = ctk.CTkFrame(
                scroll,
                fg_color=COLOR_TARJETA,
                border_width=1,
                border_color=COLOR_BORDE,
                corner_radius=10
            )
            card.pack(fill="x", pady=6)

            ctk.CTkLabel(
                card,
                text=f"📂 {job['job_name']}",
                font=("Inter", 14, "bold"),
                text_color=COLOR_TEXTO_TITULO
            ).pack(side="left", padx=25, pady=20)

            ctk.CTkButton(
                card,
                text="IMPORTAR",
                fg_color=COLOR_GRIS_DARK,
                hover_color=COLOR_GRIS_MED,
                corner_radius=8,
                width=120,
                height=35,
                font=("Inter", 12, "bold"),
                command=lambda j=job, w=top: self.procesar_seleccion(j, w)
            ).pack(side="right", padx=25)

    def procesar_seleccion(self, job_info, ventana):
        ventana.destroy()
        carpeta_origen = job_info['ruta_full']
        job_name = job_info['job_name']
        self.app.job_activo = job_name

        ruta_root = os.path.dirname(os.path.dirname(carpeta_origen))
        nombre_csv = f"job_data_{job_name}.csv"
        ruta_csv = os.path.join(ruta_root, nombre_csv)

        multiplicador = 1
        if os.path.exists(ruta_csv):
            try:
                with open(ruta_csv, newline='', encoding='utf-8', errors='ignore') as f:
                    reader = list(csv.reader(f))
                    if len(reader) > 1 and len(reader[1]) > 3:
                        valor = str(reader[1][3]).strip()
                        if valor.isdigit():
                            multiplicador = int(valor)
            except Exception:
                pass

        self.app.multiplicador_tanques = multiplicador

        try:
            rutas_dxf = self._listar_dxfs_recursivo(carpeta_origen)
            rutas_dxf = sorted(set(rutas_dxf), key=lambda p: self._normalizar_ruta(p))
        except Exception as e:
            messagebox.showerror("Error", f"Error: {e}")
            return

        if not rutas_dxf:
            messagebox.showwarning("Aviso", "No se encontraron DXF en AutoDXF (ni en subcarpetas).")
            return

        carpeta_procesados = os.path.join(carpeta_origen, "Processed Files")
        os.makedirs(carpeta_procesados, exist_ok=True)

        items_procesados = []
        nombres_usados = set()

        # NUEVO: reiniciamos metadata PDF para esta importación
        self.app.meta_pdf_por_ruta = {}

        for ruta_in in rutas_dxf:
            arch = os.path.basename(ruta_in)
            nombre_out = self._nombre_destino_unico(arch, nombres_usados)
            ruta_out_real = os.path.join(carpeta_procesados, nombre_out)

            try:
                ok_proc = self.procesador.limpiar_archivo(ruta_in, ruta_out_real)
                # Si no pudo limpiar, al menos copiamos el DXF para mantener ruta válida.
                if (not ok_proc) or (not os.path.exists(ruta_out_real)):
                    shutil.copy2(ruta_in, ruta_out_real)

                pieza, mat, qty_str, cal = self._parsear_nombre_dxf(arch)

                try:
                    qty_final = str(int(qty_str) * multiplicador)
                except Exception:
                    qty_final = qty_str

                ruta_norm = self._normalizar_ruta(ruta_out_real)
                self.app.meta_pdf_por_ruta[ruta_norm] = {
                    "job": job_name,
                    "item": pieza
                }

                items_procesados.append((pieza, mat, qty_final, cal, "LISTO", ruta_out_real))

            except Exception:
                nombre_fallback = os.path.splitext(arch)[0].strip() or arch
                # Último recurso: intentar copia directa para no dejar ruta muerta.
                try:
                    if not os.path.exists(ruta_out_real):
                        shutil.copy2(ruta_in, ruta_out_real)
                except Exception:
                    pass
                ruta_norm = self._normalizar_ruta(ruta_out_real)
                self.app.meta_pdf_por_ruta[ruta_norm] = {
                    "job": job_name,
                    "item": nombre_fallback
                }
                items_procesados.append((arch, "?", str(1 * multiplicador), "?", "LISTO", ruta_out_real))

        self.app.cargar_datos_parts(items_procesados)
        self.app.guardar_historial(job_info.get('ruta_job_root', ruta_root))
        self.app.tabview.set("PARTS")

    # ==========================================
    # NUEVA LÓGICA: SÚPER WORK ORDERS (SWO)
    # ==========================================
    def buscar_swos_pendientes(self):
        self.btn_swo_web.configure(state="disabled", text="BUSCANDO S.W.O...")
        threading.Thread(target=self.thread_swos, daemon=True).start()

    def thread_swos(self):
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor

            credenciales_db = {
                "host": "192.168.2.80",
                "database": "nestingpro_db",
                "user": "postgres",
                "password": "nesting123",
                "port": "5433"
            }
            conexion = psycopg2.connect(**credenciales_db)
            cursor = conexion.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                "SELECT DISTINCT super_work_order FROM reporte_cortes "
                "WHERE estatus = 'Pendiente SWO' AND super_work_order IS NOT NULL;"
            )
            swos = cursor.fetchall()
            lista_swos = [s["super_work_order"] for s in swos]
            cursor.close()
            conexion.close()
            self.after(0, lambda: self.mostrar_selector_swo(lista_swos))
        except Exception as e:
            self.after(0, lambda: self.restaurar_boton_swo(str(e)))

    def restaurar_boton_swo(self, err=None):
        self.btn_swo_web.configure(state="normal", text="📥 IMPORTAR S.W.O.\n(Fusión desde Tablero Web)")
        if err:
            messagebox.showerror("Error BD", f"No se pudo conectar a PostgreSQL:\n{err}")

    def mostrar_selector_swo(self, swos):
        self.restaurar_boton_swo()
        if not swos:
            messagebox.showinfo("Bandeja Vacía", "No hay Súper Work Orders pendientes por descargar.")
            return

        top = ctk.CTkToplevel(self)
        top.title("IMPORTAR SÚPER WORK ORDER")
        top.geometry("600x450")

        self.centrar_popup(top)
        top.configure(fg_color=COLOR_FONDO_APP)
        top.transient(self.app)
        top.grab_set()

        ctk.CTkLabel(
            top,
            text="Seleccione la SWO a Descargar",
            font=("Inter", 18, "bold"),
            text_color=COLOR_TEXTO_TITULO
        ).pack(pady=20)

        scroll = ctk.CTkScrollableFrame(top, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=30, pady=10)

        for swo in swos:
            card = ctk.CTkFrame(
                scroll,
                fg_color="#ECFDF5",
                border_width=1,
                border_color="#10B981",
                corner_radius=10
            )
            card.pack(fill="x", pady=6)

            ctk.CTkLabel(
                card,
                text=f"⚡ {swo}",
                font=("Inter", 16, "bold"),
                text_color="#065F46"
            ).pack(side="left", padx=25, pady=20)

            ctk.CTkButton(
                card,
                text="DESCARGAR",
                fg_color="#334659",
                hover_color="#455E75",
                text_color="white",
                corner_radius=8,
                width=120,
                height=35,
                font=("Inter", 12, "bold"),
                command=lambda s=swo, w=top: self.procesar_descarga_swo(s, w)
            ).pack(side="right", padx=25)

    def obtener_ruta_real_job(self, ruta_raiz, nombre_job):
        if not os.path.exists(ruta_raiz):
            return None
        try:
            for producto in os.listdir(ruta_raiz):
                ruta_prod = os.path.join(ruta_raiz, producto)
                if not os.path.isdir(ruta_prod):
                    continue
                for cliente in os.listdir(ruta_prod):
                    ruta_cli = os.path.join(ruta_prod, cliente)
                    if not os.path.isdir(ruta_cli):
                        continue
                    ruta_job = os.path.join(ruta_cli, nombre_job)
                    if os.path.exists(ruta_job) and os.path.isdir(ruta_job):
                        return ruta_job
        except Exception:
            pass
        return None

    def procesar_descarga_swo(self, swo_id, ventana):
        ventana.destroy()
        self.app.abrir_ventana_carga(f"Descargando {swo_id}...")
        threading.Thread(target=self.thread_descarga_swo, args=(swo_id,), daemon=True).start()

    def thread_descarga_swo(self, swo_id):
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            from postgres_connector import registrar_diccionario_swo
            import glob

            credenciales_db = {
                "host": "192.168.2.80",
                "database": "nestingpro_db",
                "user": "postgres",
                "password": "nesting123",
                "port": "5433"
            }
            conexion = psycopg2.connect(**credenciales_db)
            cursor = conexion.cursor(cursor_factory=RealDictCursor)

            # MAGIA 1: Ahora pedimos explícitamente la 'work_order' a la base de datos
            query_items = (
                "SELECT job, work_order, calibre, item, COUNT(*) as qty "
                "FROM reporte_cortes "
                "WHERE super_work_order = %s AND estatus = 'Pendiente SWO' "
                "GROUP BY job, work_order, calibre, item"
            )
            cursor.execute(query_items, (swo_id,))
            items_db = cursor.fetchall()
            cursor.close()
            conexion.close()

            items_procesados = []
            errores_archivos = 0
            prefijos_registrados = set()

            # NUEVO: reiniciamos metadata PDF para esta descarga
            self.app.meta_pdf_por_ruta = {}

            for row in items_db:
                # Extraemos job (para buscar la carpeta física) y work_order (para el ADN)
                job = row['job']
                work_order = row['work_order']
                item = row['item']
                calibre_completo = row['calibre']
                qty = str(row['qty'])

                # EL NUEVO ADN: El prefijo ya no es "JOSE", es "W.O. 1 X2"
                prefijo_adn = work_order.strip().upper()

                # --- 1. REGISTRO EN EL DICCIONARIO CON EXACTITUD MILIMÉTRICA ---
                if prefijo_adn not in prefijos_registrados:
                    ruta_base_job = self.obtener_ruta_real_job(config.RUTA_SERVIDOR_RAIZ, job)
                    c_cli, c_job_com, c_prod = "N/A", "N/A", "N/A"

                    if ruta_base_job:
                        archivos_csv = glob.glob(os.path.join(ruta_base_job, f"job_data_{job}.csv"))
                        if archivos_csv:
                            try:
                                with open(archivos_csv[0], mode='r', encoding='utf-8-sig') as f:
                                    reader = csv.reader(f)
                                    encabezados = [str(e).strip().upper() for e in next(reader, [])]
                                    datos = next(reader, [])
                                    if "CLIENTE" in encabezados:
                                        c_cli = datos[encabezados.index("CLIENTE")].strip()
                                    if "PRODUCTO" in encabezados:
                                        c_prod = datos[encabezados.index("PRODUCTO")].strip()
                                    if "JOB NUMBER" in encabezados:
                                        c_job_com = datos[encabezados.index("JOB NUMBER")].strip()
                                    elif "JOB" in encabezados:
                                        c_job_com = datos[encabezados.index("JOB")].strip()
                            except Exception:
                                pass

                    # Registramos el diccionario usando la W.O. exacta como llave
                    registrar_diccionario_swo(swo_id, prefijo_adn, c_cli, c_job_com, c_prod, credenciales_db)
                    prefijos_registrados.add(prefijo_adn)

                # --- 2. LOCALIZACIÓN FÍSICA DE DXF ---
                partes_cal = calibre_completo.split('_')
                cal_num = partes_cal[0]
                mat = partes_cal[1] if len(partes_cal) > 1 else "CARBONO"

                ruta_base_job = self.obtener_ruta_real_job(config.RUTA_SERVIDOR_RAIZ, job)
                ruta_dxf_final = ""

                if ruta_base_job:
                    ruta_autodxf = os.path.join(ruta_base_job, "MODEL CORE FILES", "AutoDXF")
                    ruta_dxf_final = self._buscar_dxf_item_en_autodxf(ruta_autodxf, item)

                if ruta_dxf_final:
                    # MAGIA 2: Inyectamos el ADN a la pieza (Ej: "W.O. 1 X2__TAPA")
                    item_con_prefijo = f"{prefijo_adn}__{item}"

                    ruta_norm = self._normalizar_ruta(ruta_dxf_final)
                    self.app.meta_pdf_por_ruta[ruta_norm] = {
                        "job": job,
                        "item": item,
                        "work_order": prefijo_adn
                    }

                    items_procesados.append((item_con_prefijo, mat, qty, cal_num, "LISTO", ruta_dxf_final))
                else:
                    errores_archivos += 1

            self.after(0, lambda: self.finalizar_descarga_swo(swo_id, items_procesados, errores_archivos))
        except Exception as e:
            self.after(0, lambda: self.error_descarga_swo(str(e)))

    def finalizar_descarga_swo(self, swo_id, items, errores):
        if hasattr(self.app, 'cerrar_ventana_carga'):
            self.app.cerrar_ventana_carga()

        if not items:
            messagebox.showerror("Fallo Crítico", "No se encontró archivos .dxf para esta SWO.")
            return

        if errores > 0:
            messagebox.showwarning("Advertencia", f"Faltaron {errores} archivos en la red.")

        self.app.job_activo = swo_id
        self.app.multiplicador_tanques = 1
        self.app.cargar_datos_parts(items)
        self.app.tabview.set("PARTS")
        messagebox.showinfo("SWO Descargada", f"¡{swo_id} inyectada con éxito!")

    def error_descarga_swo(self, err):
        if hasattr(self.app, 'cerrar_ventana_carga'):
            self.app.cerrar_ventana_carga()
        messagebox.showerror("Error en Descarga", f"Ocurrió un problema:\n{err}")