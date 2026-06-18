import sys, os, re, json, threading
import socket, time
import customtkinter as ctk
from tkinter import messagebox
from PIL import Image
from interface.ctk_qt.widgets import CTkImage

# ==========================================
# AUTO-KILLER: Evita ventanas dobles al dar "Run"
# ==========================================
def asegurar_instancia_unica():
    puerto_secreto = 65432 
    def escuchar_kill():
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.bind(('localhost', puerto_secreto))
            server.listen(1)
            while True:
                conn, addr = server.accept()
                mensaje = conn.recv(1024).decode()
                if mensaje == "CERRAR":
                    os._exit(0) 
        except Exception: pass

    try:
        cliente = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cliente.connect(('localhost', puerto_secreto))
        cliente.sendall(b"CERRAR") 
        cliente.close()
        time.sleep(0.5) 
    except ConnectionRefusedError: pass

    threading.Thread(target=escuchar_kill, daemon=True).start()

import multiprocessing
# Solo la ventana visible real puede activar el seguro, los clones invisibles no.
if multiprocessing.current_process().name == 'MainProcess':
    asegurar_instancia_unica()

# ==========================================
# INICIO DE LA APLICACIÓN
# ==========================================
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path: sys.path.append(parent_dir)
if current_dir not in sys.path: sys.path.append(current_dir)

try:
    import config
    from modules.sheets_manager import PlatesManager
    from modules.nesting_engine import MotorNesting
    from tab_files import TabFiles
    from tab_parts import TabParts
    from tab_sheets import TabSheets
    from tab_nesting import TabNesting
except ImportError as e:
    messagebox.showerror("Error Crítico", f"Error al importar módulos:\n{e}"); sys.exit(1)

COLOR_FONDO_APP, COLOR_TARJETA, COLOR_BORDE, COLOR_GRIS_DARK, COLOR_GRIS_MED, COLOR_TEXTO_TITULO, COLOR_TEXTO_SECUNDARIO = "#F1F5F9", "#FFFFFF", "#CBD5E1", "#1E293B", "#475569", "#0F172A", "#64748B"

class SistemaNestingPro(ctk.CTk):
    def __init__(self):
        super().__init__()
        try:
            # Fuerza identidad de app real en barra de tareas de Windows.
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GrupoArga.NestingSuite.V4")
        except Exception:
            pass
        self.title("ARGA NESTING SUITE")
        ctk.set_appearance_mode("light")
        self._geometry_inicial_aplicada = False
        self.configure(fg_color=COLOR_FONDO_APP)
        try:
            icon_path = config.ruta_recurso(os.path.join("assets", "branding", "logo_icon1.png"))
            if not os.path.exists(icon_path):
                icon_path = config.ruta_recurso("grupo_arga_cover.jpeg")
            icon_img = Image.open(icon_path)
            self._app_icon_photo = CTkImage(light_image=icon_img, size=(32, 32))
            self.iconphoto(True, self._app_icon_photo)
        except Exception:
            pass
        
        self._cancelar_tarea_flag = threading.Event()
        self._nesting_executor = None
        self._ventana_carga_abierta = False

        self.after(200, self.traer_al_frente)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.bind("<Unmap>", self._on_unmap_principal, add="+")
        self.bind("<Map>", self._on_map_principal, add="+")

        # Recursos compartidos
        self.jobs_procesados = self.cargar_historial()
        self.plates_manager = PlatesManager()
        self.ultimo_resultado_sync_herinox = None
        self.herinox_tc_dof = 18.50
        self.herinox_tc_fuente = "FALLBACK"
        self.herinox_nominal_by_code = {}
        self._intentar_sync_placas_react_herinox()
        
        # ¡ESTA ES LA LÍNEA QUE BORRÉ POR ERROR! 👇
        self.motor_nesting = MotorNesting() 
                
        self.datos_placas_empresa, self.datos_placas_proveedor = self.plates_manager.obtener_datos_placas_divididos()

        # Estado compartido principal
        self.datos_partes_actuales = []
        self.resultados_nesting = {}
        self.resultados_multilote = []
        self.meta_pdf_por_ruta = {}
        self.job_activo = "NESTING"
        self.ultimos_escenarios = []
        # NUEVO: WO oficial asignada por lote después de exportar DXF
        self.wo_reales_por_lote = {}
        self.plan_largos_por_lote = {}
        self.exclusiones_largos_pedido_por_lote = {}
        self.exclusiones_mrl_unidades_por_lote = {}
        self.plan_largos_job = ""

        # ===== NUEVO: estado para edición de lotes ya nestados =====
        self.editable_inputs_by_lote = []
        self.editable_inputs_actuales = []
        self.lote_editado_dirty = False
        self.source_dxf_paths_workspace = []
        self.source_dxf_paths_by_lote = []

        # Configuraciones de columnas
        self.resultados_multilote = []
        self.meta_pdf_por_ruta = {}
        self.job_activo = "NESTING"
        self.ultimos_escenarios = []

        # Configuraciones de columnas
        self.COL_CONFIG = [{"min": 250, "weight": 4}, {"min": 150, "weight": 2}, {"min": 80, "weight": 1}, {"min": 120, "weight": 1}, {"min": 100, "weight": 1}, {"min": 100, "weight": 1}]
        self.COL_CONFIG_SHEETS = [{"min": 100, "weight": 1}, {"min": 150, "weight": 2}, {"min": 90, "weight": 1}, {"min": 80, "weight": 1}, {"min": 80, "weight": 1}, {"min": 80, "weight": 1}, {"min": 80, "weight": 1}, {"min": 130, "weight": 2}, {"min": 60, "weight": 1}]
        
        self.setup_ui()

    def traer_al_frente(self):
        self.deiconify()
        self.lift()
        self.focus_force()
        try:
            from responsive_layout import configurar_ventana_principal, maximizar_ventana
            configurar_ventana_principal(self)
            if not self._geometry_inicial_aplicada:
                maximizar_ventana(self)
                self._geometry_inicial_aplicada = True
        except Exception:
            try:
                self.state("zoomed")
                self._geometry_inicial_aplicada = True
            except Exception:
                try:
                    self.geometry("1450x900+50+50")
                    self._geometry_inicial_aplicada = True
                except Exception:
                    pass
        self.after_idle(lambda: self._asegurar_boton_minimizar(self))

    def _asegurar_boton_minimizar(self, ventana):
        """Fuerza el botón (-) en la barra de título en Windows."""
        try:
            import ctypes
            ventana.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(int(ventana.winfo_id()))
            if not hwnd:
                hwnd = int(ventana.winfo_id())
            gwl_style = -16
            ws_minimizebox = 0x00020000
            ws_sysmenu = 0x00080000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, gwl_style)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, gwl_style, style | ws_minimizebox | ws_sysmenu
            )
            ctypes.windll.user32.DrawMenuBar(hwnd)
        except Exception:
            pass

    def _on_unmap_principal(self, event):
        if event.widget is not self:
            return
        try:
            if str(self.state()) == "iconic":
                self.after_idle(self._minimizar_popup_carga_acoplada)
                return
            self.after_idle(self._forzar_minimizar)
        except Exception:
            pass

    def _on_map_principal(self, event):
        if event.widget is not self:
            return
        if not getattr(self, "_ventana_carga_abierta", False):
            return
        ventana = getattr(self, "ventana_carga", None)
        if ventana is None or not ventana.winfo_exists():
            return
        try:
            ventana.deiconify()
            ventana.lift()
        except Exception:
            pass

    def _minimizar_popup_carga_acoplada(self):
        ventana = getattr(self, "ventana_carga", None)
        if ventana is None or not ventana.winfo_exists():
            return
        try:
            if str(ventana.state()) != "iconic":
                ventana.iconify()
        except Exception:
            pass

    def _forzar_minimizar(self):
        try:
            if str(self.state()) in ("zoomed", "normal", "maximized"):
                self.state("normal")
                self.update_idletasks()
                self.iconify()
                self._minimizar_popup_carga_acoplada()
        except Exception:
            pass

    def reiniciar_cancelacion_tarea(self):
        self._cancelar_tarea_flag.clear()

    def tarea_cancelada(self):
        return bool(self._cancelar_tarea_flag.is_set())

    def cancelar_tarea_actual(self, desde_popup=False):
        self._cancelar_tarea_flag.set()
        executor = getattr(self, "_nesting_executor", None)
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            self._nesting_executor = None
        if desde_popup:
            self.cerrar_ventana_carga(solicitud_usuario=True)

    def registrar_nesting_executor(self, executor):
        self._nesting_executor = executor

    def _configurar_popup_modal(self, ventana):
        def _cerrar_modal():
            try:
                ventana.grab_release()
            except Exception:
                pass
            try:
                ventana.destroy()
            except Exception:
                pass

        try:
            ventana.protocol("WM_DELETE_WINDOW", _cerrar_modal)
            ventana.bind(
                "<Unmap>",
                lambda e, v=ventana: self._on_unmap_popup_generico(e, v),
                add="+",
            )
        except Exception:
            pass

    def _on_unmap_popup_generico(self, event, ventana):
        if event.widget is not ventana:
            return
        try:
            if str(ventana.state()) != "iconic":
                self.after_idle(lambda v=ventana: self._minimizar_toplevel(v))
        except Exception:
            pass

    def on_closing(self):
        self.cancelar_tarea_actual()
        try:
            self.quit()
            self.destroy()
        except Exception:
            pass
        finally:
            sys.exit(0)

    def cargar_historial(self):
        if os.path.exists(config.DB_HISTORIAL):
            try:
                with open(config.DB_HISTORIAL, 'r') as f: return json.load(f)
            except: return []
        return []

    def _intentar_sync_placas_react_herinox(self):
        try:
            from modules.consulta_herinox_bridge import refresh_herinox_bridge_json
            bridge = refresh_herinox_bridge_json(config.HERINOX_SYNC_SETTINGS_FILE)
            print(
                f"[HERINOX BRIDGE] OK | largos={bridge.get('largos_count', 0)} | "
                f"materiales={bridge.get('raw_materials_count', 0)}"
            )
        except Exception as e:
            print(f"[HERINOX BRIDGE] WARN: {e}")
        try:
            resultado = self.plates_manager.sincronizar_desde_react_herinox()
            self.ultimo_resultado_sync_herinox = resultado
            self.herinox_tc_dof = float(getattr(resultado, "dof_rate", 18.50) or 18.50)
            self.herinox_tc_fuente = str(getattr(resultado, "dof_source", "FALLBACK") or "FALLBACK")
            self.herinox_nominal_by_code = dict(getattr(resultado, "nominal_by_code", {}) or {})
            if resultado.ok:
                print(
                    f"[HERINOX SYNC] OK({resultado.source}) | hojas={resultado.sheet_count} | "
                    f"coincidencias={resultado.matched_codes} | actualizadas={resultado.updated_rows} | "
                    f"TC={resultado.dof_rate:.4f} ({resultado.dof_source})"
                )
            else:
                print(f"[HERINOX SYNC] OMITIDA | {resultado.message}")
        except Exception as e:
            print(f"[HERINOX SYNC] ERROR inesperado: {e}")

    def guardar_historial(self, job_path):
        if job_path not in self.jobs_procesados:
            self.jobs_procesados.append(job_path)
            try:
                with open(config.DB_HISTORIAL, 'w') as f: json.dump(self.jobs_procesados, f)
            except: pass

    def setup_ui(self):
        # Navbar
        self.navbar = ctk.CTkFrame(self, height=95, fg_color=COLOR_TARJETA, border_color=COLOR_BORDE, border_width=1)
        self.navbar.pack(side="top", fill="x")
        
        try:
            # =========================================================
            # NUEVO: Ruta inteligente para el logo
            # =========================================================
            ruta_logo = config.ruta_recurso("grupo_arga_cover.jpeg")
            img = Image.open(ruta_logo)
            self.logo_img = ctk.CTkImage(light_image=img, size=(240, 55))
            ctk.CTkLabel(self.navbar, image=self.logo_img, text="").pack(side="left", padx=(35, 10), pady=15)
        except Exception as e:
            print(f"No se cargó logo: {e}")
            ctk.CTkLabel(self.navbar, text="GRUPO ARGA", font=("Inter", 22, "bold"), text_color=COLOR_GRIS_DARK).pack(side="left", padx=35)

        ctk.CTkLabel(self.navbar, text="|  ARGA NESTING SUITE", font=("Inter", 14, "bold"), text_color=COLOR_TEXTO_SECUNDARIO).pack(side="left", padx=15)

        # Tabview
        self.tabview = ctk.CTkTabview(self, fg_color="transparent", segmented_button_selected_color=COLOR_GRIS_DARK, corner_radius=12)
        self.tabview.pack(fill="both", expand=True, padx=30, pady=(10, 25))
        
        # Inicialización de Pestañas Modulares
        self.vista_files = TabFiles(self.tabview.add("FILES"), self)
        self.vista_files.pack(fill="both", expand=True)

        self.vista_parts = TabParts(self.tabview.add("PARTS"), self)
        self.vista_parts.pack(fill="both", expand=True)

        self.vista_sheets = TabSheets(self.tabview.add("SHEETS"), self)
        self.vista_sheets.pack(fill="both", expand=True)

        self.vista_nesting = TabNesting(self.tabview.add("NESTING"), self)
        self.vista_nesting.pack(fill="both", expand=True)

    def abrir_workspace_arganest_en_arranque(self, ruta_workspace: str):
        """Carga un .arganest/.navanest al iniciar la app (doble click en archivo asociado)."""
        try:
            from nesting_workspace import cargar_workspace_desde_archivo, aplicar_workspace
            self.tabview.set("NESTING")
            payload = cargar_workspace_desde_archivo(ruta_workspace)
            aplicar_workspace(self.vista_nesting, payload)
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror(
                "No se pudo abrir workspace",
                f"Archivo: {ruta_workspace}\n\nDetalle:\n{e}",
            )

    def cargar_datos_parts(self, datos):
        self.datos_partes_actuales = datos 
        self.vista_parts.refrescar_tabla(datos)

    def abrir_ventana_carga(self, titulo="Ejecutando Nesting"):
        if getattr(self, "_ventana_carga_abierta", False):
            self.cerrar_ventana_carga(solicitud_usuario=False)
        self.reiniciar_cancelacion_tarea()
        if hasattr(self, "motor_nesting") and hasattr(self.motor_nesting, "set_cancel_checker"):
            self.motor_nesting.set_cancel_checker(self.tarea_cancelada)

        self.ventana_carga = ctk.CTkToplevel(self)
        self._ventana_carga_abierta = True
        self.ventana_carga.title(titulo)
        self.ventana_carga.geometry("500x250")
        self._logo_anim_job = None
        self._logo_anim_running = False
        self.ventana_carga.protocol("WM_DELETE_WINDOW", self._cerrar_popup_carga_usuario)
        self.ventana_carga.bind("<Unmap>", self._on_unmap_popup_carga, add="+")
        
        self.ventana_carga.update_idletasks()
        x_principal = self.winfo_x()
        y_principal = self.winfo_y()
        ancho_principal = self.winfo_width()
        alto_principal = self.winfo_height()
        
        x_centro = x_principal + (ancho_principal // 2) - (500 // 2)
        y_centro = y_principal + (alto_principal // 2) - (250 // 2)
        
        self.ventana_carga.geometry(f"+{x_centro}+{y_centro}")
        # Sin grab_set: el nesting corre en segundo plano y se puede minimizar la app.
        self.ventana_carga.attributes("-topmost", True)
        self.after_idle(lambda: self._asegurar_boton_minimizar(self.ventana_carga))

        self.lbl_mensaje_carga = ctk.CTkLabel(self.ventana_carga, text="Procesando motor matemático...", font=("Inter", 12, "bold"), text_color="#1E293B")
        self.lbl_mensaje_carga.pack(pady=(40, 15))

        self.lbl_porcentaje = ctk.CTkLabel(self.ventana_carga, text="0%", font=("Inter", 14, "bold"), text_color="#3B82F6")
        self.lbl_porcentaje.pack(pady=(0, 10))

        self.lbl_tiempo_carga = ctk.CTkLabel(
            self.ventana_carga,
            text="Tiempo: 00:00:00",
            font=("Inter", 11, "bold"),
            text_color="#64748B"
        )
        self.lbl_tiempo_carga.pack(pady=(0, 8))

        self.barra_carga = ctk.CTkProgressBar(self.ventana_carga, width=350, height=10, fg_color="#E2E8F0", progress_color="#1E293B")
        self.barra_carga.pack(pady=10)
        self.barra_carga.set(0)
        self._carga_inicio_ts = time.time()
        self._timer_job = None

        def _tick_timer():
            if not hasattr(self, "ventana_carga") or not self.ventana_carga.winfo_exists():
                self._timer_job = None
                return
            if hasattr(self, "lbl_tiempo_carga") and self.lbl_tiempo_carga.winfo_exists():
                elapsed = max(0, int(time.time() - self._carga_inicio_ts))
                hh = elapsed // 3600
                mm = (elapsed % 3600) // 60
                ss = elapsed % 60
                self.lbl_tiempo_carga.configure(text=f"Tiempo: {hh:02d}:{mm:02d}:{ss:02d}")
            self._timer_job = self.ventana_carga.after(1000, _tick_timer)

        _tick_timer()

        # Para procesos sin porcentaje funcional, usar animación tipo "DVD".
        if self._usar_animacion_logo_en_carga(titulo):
            self.barra_carga.pack_forget()
            self.lbl_porcentaje.pack_forget()
            self.lbl_tiempo_carga.pack_forget()
            self._crear_animacion_logo_arga()

    def _usar_animacion_logo_en_carga(self, titulo: str) -> bool:
        t = str(titulo or "").upper()

        # Procesos con progreso real -> mantener barra porcentual.
        procesos_con_barra_funcional = (
            "EJECUTANDO NESTING",
            "OPTIMIZANDO LOTES",
            "RENESTEANDO LOTE ACTIVO",
            "RECALCULANDO PLACA",
        )
        if any(tag in t for tag in procesos_con_barra_funcional):
            return False

        # Resto de procesos de carga -> animación visual.
        return True

    def _crear_animacion_logo_arga(self):
        self._logo_box = ctk.CTkFrame(
            self.ventana_carga,
            width=360,
            height=95,
            corner_radius=10,
            fg_color="#EEF2F7",
            border_width=1,
            border_color="#CBD5E1",
        )
        self._logo_box.pack(pady=(2, 12))
        self._logo_box.pack_propagate(False)

        logo_path = os.path.join(parent_dir, "assets", "branding", "logo_icon1.png")
        if not os.path.exists(logo_path):
            return

        img = Image.open(logo_path).convert("RGBA")
        img = img.resize((54, 54))
        self._logo_anim_img = ctk.CTkImage(light_image=img, size=(54, 54))
        self._logo_anim_label = ctk.CTkLabel(self._logo_box, image=self._logo_anim_img, text="")
        self._logo_anim_label.place(x=8, y=8)

        self._logo_x = 8.0
        self._logo_y = 8.0
        self._logo_dx = 2.4
        self._logo_dy = 1.8
        self._logo_anim_running = True
        self._animar_logo_arga()

    def _animar_logo_arga(self):
        if not self._logo_anim_running:
            return
        if not hasattr(self, "_logo_anim_label") or not self._logo_anim_label.winfo_exists():
            return

        self._logo_box.update_idletasks()
        bw = max(1, int(self._logo_box.winfo_width()))
        bh = max(1, int(self._logo_box.winfo_height()))
        lw = max(1, int(self._logo_anim_label.winfo_width()))
        lh = max(1, int(self._logo_anim_label.winfo_height()))

        nx = self._logo_x + self._logo_dx
        ny = self._logo_y + self._logo_dy

        if nx <= 0:
            nx = 0
            self._logo_dx = abs(self._logo_dx)
        elif nx + lw >= bw:
            nx = max(0, bw - lw)
            self._logo_dx = -abs(self._logo_dx)

        if ny <= 0:
            ny = 0
            self._logo_dy = abs(self._logo_dy)
        elif ny + lh >= bh:
            ny = max(0, bh - lh)
            self._logo_dy = -abs(self._logo_dy)

        self._logo_x = nx
        self._logo_y = ny
        self._logo_anim_label.place(x=int(nx), y=int(ny))
        self._logo_anim_job = self.ventana_carga.after(16, self._animar_logo_arga)

    def actualizar_progreso(self, mensaje, porcentaje):
        def _actualizar_gui():
            if hasattr(self, 'barra_carga') and self.barra_carga.winfo_exists():
                self.barra_carga.set(porcentaje)
                
            if hasattr(self, 'lbl_porcentaje') and self.lbl_porcentaje.winfo_exists():
                self.lbl_porcentaje.configure(text=f"{int(porcentaje * 100)}%")
                
            if hasattr(self, 'lbl_mensaje_carga') and self.lbl_mensaje_carga.winfo_exists():
                self.lbl_mensaje_carga.configure(text=mensaje)
                
            self.update_idletasks()
            
        self.after(0, _actualizar_gui)

    def _on_unmap_popup_carga(self, event):
        if not getattr(self, "_ventana_carga_abierta", False):
            return
        if event.widget is not getattr(self, "ventana_carga", None):
            return
        try:
            if str(event.widget.state()) != "iconic":
                self.after_idle(lambda w=event.widget: self._minimizar_toplevel(w))
        except Exception:
            pass

    def _minimizar_toplevel(self, ventana):
        try:
            if ventana is None or not ventana.winfo_exists():
                return
            if str(ventana.state()) in ("zoomed", "normal", "maximized"):
                ventana.state("normal")
                ventana.update_idletasks()
                ventana.iconify()
        except Exception:
            pass

    def _cerrar_popup_carga_usuario(self):
        if messagebox.askyesno(
            "Cancelar proceso",
            "¿Desea cancelar el proceso en curso?\n\n"
            "Se detendrá el cálculo y podrá volver a ejecutarlo.",
        ):
            self.cancelar_tarea_actual(desde_popup=True)

    def cerrar_ventana_carga(self, solicitud_usuario=False):
        self._logo_anim_running = False
        if hasattr(self, "_timer_job") and self._timer_job and hasattr(self, "ventana_carga"):
            try:
                self.ventana_carga.after_cancel(self._timer_job)
            except Exception:
                pass
            self._timer_job = None
        if hasattr(self, "_logo_anim_job") and self._logo_anim_job and hasattr(self, "ventana_carga"):
            try:
                self.ventana_carga.after_cancel(self._logo_anim_job)
            except Exception:
                pass
            self._logo_anim_job = None
        if hasattr(self, 'ventana_carga'):
            try:
                self.ventana_carga.destroy()
            except Exception:
                pass
        self._ventana_carga_abierta = False
        if solicitud_usuario and hasattr(self, "vista_nesting"):
            try:
                self.vista_nesting.restaurar_controles_tras_cancelacion()
            except Exception:
                pass

    def mostrar_mensaje_exito(self):
        pass 
        
    def _extractor_numerico(self, valor):
        try:
            nums = re.findall(r"[-+]?\d*\.\d+|\d+", str(valor).replace(',', ''))
            return float(nums[0]) if nums else 0.0
        except: return 0.0

if __name__ == "__main__":
    app = SistemaNestingPro(); app.mainloop()