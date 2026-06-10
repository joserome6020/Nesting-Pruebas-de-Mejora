import re
import os
import csv
import sys
import json
import time
import tempfile
import subprocess
import shutil
import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import config

# Colores consistentes con tu interfaz (Estilo Arga Suite)
COLOR_TARJETA = "#FFFFFF"
COLOR_BORDE = "#CBD5E1"
COLOR_GRIS_DARK = "#1E293B"
COLOR_GRIS_MED = "#475569"
COLOR_TEXTO_TITULO = "#0F172A"
COLOR_TEXTO_SECUNDARIO = "#64748B"
COLOR_FONDO_APP = "#F1F5F9"
COLOR_AZUL_ACCENTO = "#3B82F6"

class TabSheets(ctk.CTkFrame):
    def __init__(self, master, app_principal):
        super().__init__(master, fg_color="transparent")
        self.app = app_principal
        
        # --- CONFIGURACIÓN LOCAL DE COLUMNAS (9 en total) ---
        self.local_col_config = [
            {"weight": 1, "min": 90},  # Calibre nominal (0)
            {"weight": 1, "min": 60},  # Thickness (1)
            {"weight": 2, "min": 120}, # Material (2)
            {"weight": 2, "min": 100}, # Arga Code (3)
            {"weight": 1, "min": 60},  # Length (4)
            {"weight": 1, "min": 60},  # Width (5)
            {"weight": 1, "min": 60},  # LB (6)
            {"weight": 1, "min": 80},  # $$/LB (7)
            {"weight": 2, "min": 110}, # PRECIO TOTAL PLACA USD (8)
            {"weight": 1, "min": 90},  # Stock (9)
        ]
        self._selector_vars = {}
        self._selector_values = {}
        self._selector_anchors = {}
        self._selector_callbacks = {}
        self._selector_popup = None
        self._selector_popup_key = None
        self._selector_listbox = None
        
        self.setup_ui()
        # Carga automática de placas al abrir la pestaña SHEETS.
        self.after(80, self.actualizar_inventario)
        self._last_qt_viewer_error = ""

    def setup_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        cont = ctk.CTkFrame(self, fg_color=COLOR_TARJETA, border_width=1, border_color=COLOR_BORDE, corner_radius=15)
        cont.pack(fill="both", expand=True)

        # --- PANEL DE FILTROS Y ACCIONES ---
        filtros = ctk.CTkFrame(cont, fg_color="#F8FAFC", height=75, corner_radius=0)
        filtros.pack(fill="x", padx=1, pady=1)
        
        grp_nom = ctk.CTkFrame(filtros, fg_color="transparent")
        grp_nom.pack(side="left", padx=(20, 4), pady=(6, 4))
        ctk.CTkLabel(grp_nom, text="FILTRAR CALIBRE NOMINAL", font=("Inter", 10, "bold"), text_color=COLOR_TEXTO_SECUNDARIO).pack(anchor="w")
        self._build_selector(
            parent=grp_nom,
            key="nominal",
            width=120,
            values=["TODOS"],
            on_change=self.al_cambiar_nominal,
        )

        grp_thk = ctk.CTkFrame(filtros, fg_color="transparent")
        grp_thk.pack(side="left", padx=4, pady=(6, 4))
        ctk.CTkLabel(grp_thk, text="FILTRAR THICKNESS", font=("Inter", 10, "bold"), text_color=COLOR_TEXTO_SECUNDARIO).pack(anchor="w")
        self._build_selector(
            parent=grp_thk,
            key="thickness",
            width=120,
            values=["TODOS"],
            on_change=self.al_cambiar_thickness,
        )
        
        grp_mat = ctk.CTkFrame(filtros, fg_color="transparent")
        grp_mat.pack(side="left", padx=4, pady=(6, 4))
        ctk.CTkLabel(grp_mat, text="FILTRAR MATERIAL", font=("Inter", 10, "bold"), text_color=COLOR_TEXTO_SECUNDARIO).pack(anchor="w")
        self._build_selector(
            parent=grp_mat,
            key="material",
            width=140,
            values=["TODOS"],
            on_change=self.al_cambiar_material,
        )

        grp_code = ctk.CTkFrame(filtros, fg_color="transparent")
        grp_code.pack(side="left", padx=4, pady=(6, 4))
        ctk.CTkLabel(grp_code, text="FILTRAR ARGA CODE", font=("Inter", 10, "bold"), text_color=COLOR_TEXTO_SECUNDARIO).pack(anchor="w")
        self._build_selector(
            parent=grp_code,
            key="arga_code",
            width=120,
            values=["TODOS"],
            on_change=self._on_filter_change,
        )

        grp_stock = ctk.CTkFrame(filtros, fg_color="transparent")
        grp_stock.pack(side="left", padx=4, pady=(6, 4))
        ctk.CTkLabel(grp_stock, text="STOCK HERINOX", font=("Inter", 10, "bold"), text_color=COLOR_TEXTO_SECUNDARIO).pack(anchor="w")
        self._build_selector(
            parent=grp_stock,
            key="stock",
            width=140,
            values=["TODOS", "DISPONIBLE", "NO DISPONIBLE", "NO EXISTENTE"],
            on_change=self._on_filter_change,
        )

        grp_price = ctk.CTkFrame(filtros, fg_color="transparent")
        grp_price.pack(side="left", padx=4, pady=(6, 4))
        ctk.CTkLabel(grp_price, text="FILTRO $$/LB", font=("Inter", 10, "bold"), text_color=COLOR_TEXTO_SECUNDARIO).pack(anchor="w")
        self._build_selector(
            parent=grp_price,
            key="precio",
            width=140,
            values=["TODOS", "MENOR PRECIO", "MAYOR PRECIO"],
            on_change=self.aplicar_filtros,
        )

        self.btn_remanentes = ctk.CTkButton(
            filtros, text="📦 REMANENTES DISPONIBLES", font=("Inter", 10, "bold"), 
            fg_color=COLOR_GRIS_MED, hover_color=COLOR_GRIS_DARK,
            width=180, height=30, corner_radius=8, command=self.abrir_inventario_remanentes
        )
        self.btn_remanentes.pack(side="right", padx=25)

        # --- NUEVO: CONTENEDOR DE PESTAÑAS (TABVIEW) ---
        self.tabs = ctk.CTkTabview(cont, fg_color="transparent", 
                                   segmented_button_selected_color=COLOR_GRIS_MED,
                                   segmented_button_selected_hover_color=COLOR_GRIS_MED,
                                   text_color=COLOR_GRIS_DARK,
                                   command=self.aplicar_filtros) 
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(5, 0))

        # Creación de las dos pestañas
        self.tab_empresa = self.tabs.add("🏢 STOCK EMPRESA")
        self.tab_proveedor = self.tabs.add("🚚 STOCK PROVEEDOR")
        self._refresh_tabs_text_color()

        # Configuración interna de la pestaña EMPRESA
        self.crear_encabezados(self.tab_empresa)
        self.lista_empresa = ctk.CTkScrollableFrame(self.tab_empresa, fg_color="transparent", corner_radius=0)
        self.lista_empresa.pack(fill="both", expand=True)

        # Configuración interna de la pestaña PROVEEDOR
        self.crear_encabezados(self.tab_proveedor)
        self.lista_proveedor = ctk.CTkScrollableFrame(self.tab_proveedor, fg_color="transparent", corner_radius=0)
        self.lista_proveedor.pack(fill="both", expand=True)

        # Acciones inferiores
        acciones = ctk.CTkFrame(cont, fg_color="transparent")
        acciones.pack(side="bottom", pady=(5, 15))

        self.btn_sync_herinox = ctk.CTkButton(
            acciones,
            text="⟳ Sincronizar con Herinox",
            fg_color=COLOR_GRIS_MED,
            hover_color=COLOR_GRIS_DARK,
            text_color="white",
            font=("Inter", 12, "bold"),
            command=self.sincronizar_con_herinox,
        )
        self.btn_sync_herinox.pack(side="left", padx=(0, 8))

        self.btn_sync = ctk.CTkButton(
            acciones,
            text="▣ Ver cambios de sincronizacion",
            fg_color="transparent",
            text_color=COLOR_GRIS_DARK,
            border_width=1,
            border_color=COLOR_GRIS_DARK,
            font=("Inter", 12, "bold"),
            command=self.mostrar_cambios_sincronizacion,
        )
        self.btn_sync.pack(side="left")

    def crear_encabezados(self, parent_frame):
        h_sheet = ctk.CTkFrame(parent_frame, height=35, fg_color=COLOR_GRIS_MED, corner_radius=0)
        h_sheet.pack(fill="x", pady=(0, 5))
        
        titles = ["CALIBRE NOMINAL", "THICKNESS", "MATERIAL", "ARGA CODE", "LENGTH", "WIDTH", "LB", "$$/LB", "PRECIO TOTAL", "STOCK"]
        
        for i, t in enumerate(titles):
            conf = self.local_col_config[i]
            h_sheet.columnconfigure(i, weight=conf["weight"], minsize=conf["min"], uniform="header")
            ctk.CTkLabel(h_sheet, text=t, font=("Inter", 11, "bold"), text_color="white").grid(row=0, column=i, sticky="nsew")

    def _nominal_from_row(self, row):
        code = str(row[2]).strip()
        return str(getattr(self.app, "herinox_nominal_by_code", {}).get(code, "N/A") or "N/A").strip()

    def _normalize_thickness(self, value) -> str:
        txt = str(value or "").strip()
        if not txt or txt.lower() == "nan":
            return ""

        clean = txt.replace(",", ".").replace("-", " ").strip()
        compact = clean.replace(" ", "")

        # Formato mixto válido: 1 1/2
        mixed = re.match(r"^(\d+)\s+(\d+)\/(\d+)$", clean)
        if mixed:
            den = int(mixed.group(3))
            if den == 0:
                return ""
            val = int(mixed.group(1)) + (int(mixed.group(2)) / den)
        # Fracción simple válida: 3/16
        elif "/" in compact:
            m = re.match(r"^(\d+)\/(\d+)$", compact)
            if not m:
                return ""
            den = int(m.group(2))
            if den == 0:
                return ""
            val = int(m.group(1)) / den
        else:
            # Descarta texto no numérico (ej. "cero.25").
            if re.search(r"[A-Za-z]", compact):
                return ""
            try:
                val = float(compact)
            except Exception:
                return ""

        # Evita que calibres nominales (10, 11, 14, 16...) entren al filtro thickness.
        if val >= 6 and "." not in compact and "/" not in compact:
            return ""
        if val <= 0:
            return ""

        return f"{val:.4f}".rstrip("0").rstrip(".")

    def al_cambiar_nominal(self, *args):
        self._on_filter_change()

    def al_cambiar_thickness(self, *args):
        self._on_filter_change()

    def al_cambiar_material(self, *args):
        self._on_filter_change()

    def _on_filter_change(self, *args):
        self._actualizar_opciones_dependientes()
        self.aplicar_filtros()

    def _refresh_tabs_text_color(self):
        # Compatibilidad con versiones viejas de CustomTkinter:
        # coloreamos texto por botón usando la referencia interna.
        try:
            seg = getattr(self.tabs, "_segmented_button", None)
            btns = getattr(seg, "_buttons_dict", {}) or {}
            selected = str(self.tabs.get() or "").strip()
            for name, btn in btns.items():
                if str(name) == selected:
                    btn.configure(text_color="#FFFFFF")
                else:
                    btn.configure(text_color=COLOR_GRIS_DARK)
        except Exception:
            pass

    def _datos_base_activos(self):
        pestaña_actual = self.tabs.get()
        if pestaña_actual == "🏢 STOCK EMPRESA":
            return list(self.app.datos_placas_empresa or [])
        return list(self.app.datos_placas_proveedor or [])

    def _row_value(self, row, key: str) -> str:
        if key == "nominal":
            return self._nominal_from_row(row)
        if key == "thickness":
            return self._normalize_thickness(row[0])
        if key == "material":
            txt = str(row[1]).strip()
            return "" if txt.lower() == "nan" else txt
        if key == "arga_code":
            return str(row[2]).strip()
        if key == "stock":
            return self._stock_estado(str(row[8]))
        return ""

    def _row_matches_filters(self, row, filtros, ignore_key: str = "") -> bool:
        for key in ("nominal", "thickness", "material", "arga_code", "stock"):
            if key == ignore_key:
                continue
            v = str(filtros.get(key, "TODOS")).strip()
            if v != "TODOS" and self._row_value(row, key) != v:
                return False
        return True

    def _build_selector(self, parent, key, width, values, on_change):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(anchor="w")
        var = tk.StringVar(value="TODOS")
        entrada = ctk.CTkEntry(
            row, width=max(90, width - 25), font=("Inter", 12),
            textvariable=var, state="readonly",
            fg_color=COLOR_TARJETA, border_color=COLOR_BORDE,
        )
        entrada.pack(side="left")
        btn = ctk.CTkButton(
            row, text="▾", width=26, height=28,
            fg_color=COLOR_GRIS_MED, hover_color=COLOR_GRIS_DARK,
            command=lambda k=key: self._toggle_selector_dropdown(k),
        )
        btn.pack(side="left", padx=(2, 0))
        self._selector_vars[key] = var
        self._selector_values[key] = list(values or ["TODOS"])
        self._selector_anchors[key] = row
        self._selector_callbacks[key] = on_change

    def _selector_get(self, key: str) -> str:
        var = self._selector_vars.get(key)
        return str(var.get() if var else "TODOS").strip() or "TODOS"

    def _selector_set(self, key: str, value: str, trigger: bool = False):
        vals = self._selector_values.get(key, ["TODOS"])
        clean = str(value or "TODOS").strip() or "TODOS"
        if clean not in vals:
            clean = "TODOS"
        var = self._selector_vars.get(key)
        if var:
            var.set(clean)
        if trigger:
            callback = self._selector_callbacks.get(key)
            if callback:
                callback(clean)

    def _selector_set_values(self, key: str, values):
        vals = [str(v).strip() for v in list(values or []) if str(v).strip()]
        if "TODOS" not in vals:
            vals = ["TODOS"] + vals
        self._selector_values[key] = vals
        if self._selector_get(key) not in vals:
            self._selector_set(key, "TODOS")

    def _toggle_selector_dropdown(self, key: str):
        popup = self._selector_popup
        if (
            popup
            and popup.winfo_exists()
            and str(popup.state()) != "withdrawn"
            and self._selector_popup_key == key
        ):
            self._close_selector_dropdown(key)
            return
        self._open_selector_dropdown(key)

    def _ensure_selector_popup(self):
        if self._selector_popup and self._selector_popup.winfo_exists():
            return

        popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=COLOR_BORDE)
        popup.bind("<FocusOut>", self._on_selector_focus_out)
        popup.bind("<Escape>", lambda _e: self._close_selector_dropdown())

        outer = tk.Frame(popup, bg=COLOR_BORDE, bd=0, highlightthickness=0)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=COLOR_TARJETA, bd=0, highlightthickness=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        list_container = tk.Frame(inner, bg=COLOR_TARJETA, bd=0, highlightthickness=0)
        list_container.pack(fill="both", expand=True, padx=3, pady=3)

        scrollbar = tk.Scrollbar(list_container, orient="vertical")
        listbox = tk.Listbox(
            list_container,
            yscrollcommand=scrollbar.set,
            selectmode=tk.SINGLE,
            exportselection=False,
            activestyle="none",
            font=("Inter", 10),
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            bg=COLOR_TARJETA,
            fg=COLOR_GRIS_DARK,
            selectbackground="#CBD5E1",
            selectforeground=COLOR_GRIS_DARK,
        )
        scrollbar.config(command=listbox.yview)
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        listbox.bind("<ButtonRelease-1>", self._select_active_listbox_item)
        listbox.bind("<Double-Button-1>", self._select_active_listbox_item)
        listbox.bind("<Return>", self._select_active_listbox_item)

        self._selector_popup = popup
        self._selector_listbox = listbox

    def _open_selector_dropdown(self, key: str):
        self._ensure_selector_popup()
        values = self._selector_values.get(key, ["TODOS"])
        if not values:
            values = ["TODOS"]
            self._selector_values[key] = values

        self.update_idletasks()
        anchor = self._selector_anchors[key]
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 2
        w = max(130, anchor.winfo_width() + 8)
        h = 235

        popup = self._selector_popup
        self._selector_popup_key = key
        popup.geometry(f"{w}x{h}+{x}+{y}")
        listbox = self._selector_listbox
        listbox.delete(0, tk.END)

        for item in values:
            listbox.insert(tk.END, item)

        current = self._selector_get(key)
        if current in values:
            idx = values.index(current)
            listbox.selection_set(idx)
            listbox.see(idx)

        popup.deiconify()
        popup.focus_force()

    def _on_selector_focus_out(self, _event=None):
        popup = self._selector_popup
        if not popup or not popup.winfo_exists():
            return
        focused = popup.focus_get()
        if focused is not None and str(focused).startswith(str(popup)):
            return
        popup.after(10, self._close_selector_dropdown)

    def _select_active_listbox_item(self, event=None):
        key = self._selector_popup_key
        listbox = self._selector_listbox
        if not key or listbox is None:
            return
        idx = None
        if event is not None and hasattr(event, "y"):
            try:
                idx = int(listbox.nearest(event.y))
            except Exception:
                idx = None
        if idx is None:
            sel = listbox.curselection()
            if sel:
                idx = int(sel[0])
        if idx is None:
            return
        try:
            value = str(listbox.get(idx)).strip() or "TODOS"
        except Exception:
            return
        self._selector_set(key, value)
        self._close_selector_dropdown(key)
        callback = self._selector_callbacks.get(key)
        if callback:
            callback()

    def _close_selector_dropdown(self, key: str = None):
        if key and self._selector_popup_key != key:
            return
        popup = self._selector_popup
        if popup and popup.winfo_exists():
            popup.withdraw()
        self._selector_popup_key = None

    def _set_arga_code(self, value: str):
        self._selector_set("arga_code", value)

    def _get_arga_code(self) -> str:
        return self._selector_get("arga_code")

    def _set_arga_code_values(self, values):
        self._selector_set_values("arga_code", values)

    def _actualizar_opciones_dependientes(self):
        datos = self._datos_base_activos()
        filtros = {
            "nominal": self._selector_get("nominal"),
            "thickness": self._selector_get("thickness"),
            "material": self._selector_get("material"),
            "arga_code": self._selector_get("arga_code"),
            "stock": self._selector_get("stock"),
        }

        nominales = sorted(
            list(
                {
                    self._row_value(r, "nominal")
                    for r in datos
                    if self._row_value(r, "nominal") and self._row_matches_filters(r, filtros, ignore_key="nominal")
                }
            )
        )
        thicknesses = sorted(
            list(
                {
                    self._row_value(r, "thickness")
                    for r in datos
                    if self._row_value(r, "thickness") and self._row_matches_filters(r, filtros, ignore_key="thickness")
                }
            ),
            key=lambda x: float(x),
        )
        materiales = sorted(
            list(
                {
                    self._row_value(r, "material")
                    for r in datos
                    if self._row_value(r, "material") and self._row_matches_filters(r, filtros, ignore_key="material")
                }
            )
        )
        codigos = sorted(
            list(
                {
                    self._row_value(r, "arga_code")
                    for r in datos
                    if self._row_value(r, "arga_code") and self._row_matches_filters(r, filtros, ignore_key="arga_code")
                }
            )
        )
        stock_orden = ["DISPONIBLE", "NO DISPONIBLE", "NO EXISTENTE"]
        stock_presentes = [
            s for s in stock_orden
            if any(self._row_value(r, "stock") == s and self._row_matches_filters(r, filtros, ignore_key="stock") for r in datos)
        ]

        self._selector_set_values("nominal", nominales)
        self._selector_set_values("thickness", thicknesses)
        self._selector_set_values("material", materiales)
        self._set_arga_code_values(codigos)
        self._selector_set_values("stock", stock_presentes)

    def aplicar_filtros(self, *args):
        self._refresh_tabs_text_color()
        pestaña_actual = self.tabs.get()
        
        if pestaña_actual == "🏢 STOCK EMPRESA":
            datos_base = self.app.datos_placas_empresa
            lista_activa = self.lista_empresa
        else:
            datos_base = self.app.datos_placas_proveedor
            lista_activa = self.lista_proveedor

        for w in lista_activa.winfo_children(): 
            w.destroy()

        filtros = {
            "nominal": self._selector_get("nominal"),
            "thickness": self._selector_get("thickness"),
            "material": self._selector_get("material"),
            "arga_code": self._get_arga_code(),
            "stock": self._selector_get("stock"),
        }
        precio_val = self._selector_get("precio")
        filtrados = list(datos_base) 
        if not filtrados:
            return

        filtrados = [r for r in filtrados if self._row_matches_filters(r, filtros)]

        if precio_val == "MENOR PRECIO":
            filtrados.sort(key=lambda x: self.app._extractor_numerico(x[7]))
        elif precio_val == "MAYOR PRECIO":
            filtrados.sort(key=lambda x: self.app._extractor_numerico(x[7]), reverse=True)
        else:
            filtrados.sort(key=lambda x: self.app._extractor_numerico(x[0]))

        for idx, fila in enumerate(filtrados):
            color = "#FFFFFF" if idx % 2 == 0 else "#F8FAFC"
            row = ctk.CTkFrame(lista_activa, height=55, fg_color=color, corner_radius=0)
            row.pack(fill="x")
            
            try:
                precio_por_libra = self.app._extractor_numerico(fila[7])
                mxn_placa = self.app._extractor_numerico(fila[6])
                lb_placa = self.app._extractor_numerico(fila[5])
                tc_dof = float(getattr(self.app, "herinox_tc_dof", 18.50) or 18.50)
                # Fuente de verdad preferida: USD/LB provisto por Herinox.
                if precio_por_libra > 0 and lb_placa > 0:
                    costo_placa_usd = precio_por_libra * lb_placa
                else:
                    # Fallback histórico para registros incompletos.
                    costo_placa_usd = (mxn_placa / tc_dof) if tc_dof > 0 else 0.0

                str_costo_placa = f"${costo_placa_usd:,.2f}" if costo_placa_usd > 0 else "$0.00"
                str_precio_libra = f"${precio_por_libra:,.2f}" if precio_por_libra > 0 else "-"
            except Exception:
                str_costo_placa = "---"
                str_precio_libra = "---"

            nominal = str(getattr(self.app, "herinox_nominal_by_code", {}).get(str(fila[2]).strip(), "N/A") or "N/A")
            thickness_mostrar = self._normalize_thickness(fila[0]) or str(fila[0] if str(fila[0]) != "nan" else "-")

            valores_mostrar = [
                nominal,
                thickness_mostrar,
                str(fila[1] if str(fila[1]) != "nan" else "-"),
                str(fila[2] if str(fila[2]) != "nan" else "-"),
                str(fila[3] if str(fila[3]) != "nan" else "-"),
                str(fila[4] if str(fila[4]) != "nan" else "-"),
                str(fila[5] if str(fila[5]) != "nan" else "-"),
                str_precio_libra,
                str_costo_placa,
                str(fila[8] if str(fila[8]) != "nan" else "-")
            ]

            for i in range(10):
                conf = self.local_col_config[i]
                row.columnconfigure(i, weight=conf["weight"], minsize=conf["min"], uniform="row")
                
                if i == 8:
                    ctk.CTkLabel(row, text=valores_mostrar[i], font=("Inter", 13, "bold"), text_color="#2563EB").grid(row=0, column=i, sticky="nsew")
                elif i == 9:
                    estado = self._stock_estado(valores_mostrar[i])
                    color_stock = "#16A34A" if estado == "DISPONIBLE" else ("#CA8A04" if estado == "NO DISPONIBLE" else "#DC2626")
                    ctk.CTkLabel(row, text=estado, font=("Inter", 13, "bold"), text_color=color_stock).grid(row=0, column=i, sticky="nsew")
                else:
                    ctk.CTkLabel(row, text=valores_mostrar[i], font=("Inter", 13)).grid(row=0, column=i, sticky="nsew")

    def actualizar_inventario(self):
        self.app.datos_placas_empresa, self.app.datos_placas_proveedor = self.app.plates_manager.obtener_datos_placas_divididos()
        
        if not self.app.datos_placas_empresa and not self.app.datos_placas_proveedor: return
        
        datos_totales = self.app.datos_placas_empresa + self.app.datos_placas_proveedor
        nominales = sorted(list(set(self._nominal_from_row(row) for row in datos_totales if self._nominal_from_row(row))))
        calibres = sorted(
            list(
                set(
                    self._normalize_thickness(row[0])
                    for row in datos_totales
                    if self._normalize_thickness(row[0])
                )
            ),
            key=lambda x: float(x),
        )
        materiales = sorted(list(set(str(row[1]).strip() for row in datos_totales if str(row[1]).strip().lower() != "nan")))
        codigos = sorted(list(set(str(row[2]).strip() for row in datos_totales if str(row[2]).strip())))
        
        self._selector_set_values("nominal", nominales)
        self._selector_set_values("thickness", calibres)
        self._selector_set_values("material", materiales)
        self._set_arga_code_values(codigos)
        self._selector_set_values("stock", ["TODOS", "DISPONIBLE", "NO DISPONIBLE", "NO EXISTENTE"])
        self._selector_set_values("precio", ["TODOS", "MENOR PRECIO", "MAYOR PRECIO"])
        self._actualizar_opciones_dependientes()
        self.aplicar_filtros()

    @staticmethod
    def _stock_estado(valor: str) -> str:
        txt = str(valor or "").strip().upper()
        if txt in {"DISPONIBLE", "NO DISPONIBLE", "NO EXISTENTE"}:
            return txt
        return "NO EXISTENTE"

    def sincronizar_con_herinox(self):
        self.btn_sync_herinox.configure(state="disabled", text="Sincronizando...")
        self.update_idletasks()
        try:
            resultado = self.app.plates_manager.sincronizar_desde_react_herinox()
            self.app.ultimo_resultado_sync_herinox = resultado
            self.app.herinox_tc_dof = float(getattr(resultado, "dof_rate", 18.50) or 18.50)
            self.app.herinox_tc_fuente = str(getattr(resultado, "dof_source", "FALLBACK") or "FALLBACK")
            self.app.herinox_nominal_by_code = dict(getattr(resultado, "nominal_by_code", {}) or {})
            self.actualizar_inventario()

            if resultado.ok:
                messagebox.showinfo(
                    "Sync Herinox completada",
                    (
                        f"Origen: {resultado.source}\n"
                        f"TC DOF: {resultado.dof_rate:,.4f} ({resultado.dof_source})\n"
                        f"Hojas revisadas: {resultado.sheet_count}\n"
                        f"Codigos coincidentes: {resultado.matched_codes}\n"
                        f"Filas actualizadas: {resultado.updated_rows}"
                    ),
                )
            else:
                messagebox.showwarning(
                    "Sync Herinox omitida",
                    (
                        f"{resultado.message}\n\n"
                        f"Config persistente: {config.HERINOX_SYNC_SETTINGS_FILE}"
                    ),
                )
        except Exception as e:
            messagebox.showerror("Error en Sync Herinox", str(e))
        finally:
            self.btn_sync_herinox.configure(state="normal", text="⟳ Sincronizar con Herinox")

    def mostrar_cambios_sincronizacion(self):
        resultado = getattr(self.app, "ultimo_resultado_sync_herinox", None)
        if resultado is None:
            messagebox.showinfo("Sin datos", "Todavia no hay una sincronizacion registrada en esta sesion.")
            return

        # Intentamos primero visor Qt para render más fluido en resize/move/minimize.
        if self._abrir_viewer_qt(resultado):
            return

        if self._last_qt_viewer_error:
            messagebox.showwarning(
                "Visor Qt no disponible",
                f"No se pudo abrir el visor Qt.\n\nDetalle:\n{self._last_qt_viewer_error}\n\nSe abrira visor compatible (Tk).",
            )

        # Fallback seguro a ventana Tkinter.
        self._mostrar_cambios_sincronizacion_tk(resultado)

    def _abrir_viewer_qt(self, resultado) -> bool:
        self._last_qt_viewer_error = ""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            config.ruta_recurso(os.path.join("modules", "herinox_sync_qt_viewer.py")),
            os.path.join(base_dir, "modules", "herinox_sync_qt_viewer.py"),
        ]
        viewer_script = ""
        for p in candidates:
            if p and os.path.exists(p):
                viewer_script = p
                break
        if not viewer_script:
            self._last_qt_viewer_error = "No se encontro modules/herinox_sync_qt_viewer.py"
            return False

        qt_python = self._resolver_python_con_pyside6()
        if not qt_python:
            self._last_qt_viewer_error = (
                "Ningun interprete con PySide6 disponible.\n"
                "Instala PySide6 en el Python que ejecuta Arga Nesting Suite."
            )
            return False

        payload = {
            "ok": bool(getattr(resultado, "ok", False)),
            "updated_rows": int(getattr(resultado, "updated_rows", 0) or 0),
            "matched_codes": int(getattr(resultado, "matched_codes", 0) or 0),
            "sheet_count": int(getattr(resultado, "sheet_count", 0) or 0),
            "source": str(getattr(resultado, "source", "none") or "none"),
            "dof_rate": float(getattr(resultado, "dof_rate", 18.5) or 18.5),
            "dof_source": str(getattr(resultado, "dof_source", "FALLBACK") or "FALLBACK"),
            "message": str(getattr(resultado, "message", "") or ""),
            "updated_items": list(getattr(resultado, "updated_items", []) or []),
        }

        try:
            fd, json_path = tempfile.mkstemp(prefix="herinox_sync_", suffix=".json")
            os.close(fd)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)

            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.Popen(
                [qt_python, viewer_script, json_path],
                cwd=os.path.dirname(viewer_script),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creationflags,
            )

            # Si Qt falla al arrancar (ej. falta dependencia), hacemos fallback inmediato a Tk.
            time.sleep(0.35)
            if proc.poll() is not None:
                out, err = proc.communicate()
                detalle = (err or out or "").strip()
                if len(detalle) > 500:
                    detalle = detalle[:500] + "..."
                self._last_qt_viewer_error = (
                    detalle
                    or f"Proceso Qt finalizo con code {proc.returncode}. Python usado: {qt_python}"
                )
                return False
            return True
        except Exception as e:
            self._last_qt_viewer_error = str(e)
            return False

    def _resolver_python_con_pyside6(self):
        candidatos = []
        if sys.executable:
            candidatos.append(sys.executable)

        base_dir = os.path.dirname(os.path.abspath(__file__))
        venv_python = os.path.join(base_dir, ".venv", "Scripts", "python.exe")
        if os.path.exists(venv_python):
            candidatos.append(venv_python)

        user_profile = os.environ.get("USERPROFILE", "")
        if user_profile:
            alt_python = os.path.join(
                user_profile, "AppData", "Local", "Python", "pythoncore-3.14-64", "python.exe"
            )
            if os.path.exists(alt_python):
                candidatos.append(alt_python)

        path_python = shutil.which("python")
        if path_python:
            candidatos.append(path_python)
        path_py = shutil.which("py")
        if path_py:
            candidatos.append(path_py)

        # Preservar orden y quitar duplicados.
        vistos = set()
        candidatos_unicos = []
        for c in candidatos:
            key = str(c).lower()
            if key in vistos:
                continue
            vistos.add(key)
            candidatos_unicos.append(c)

        for exe in candidatos_unicos:
            try:
                proc = subprocess.run(
                    [exe, "-c", "import PySide6;print('OK')"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                )
                if proc.returncode == 0:
                    return exe
            except Exception:
                continue
        return None

    def _mostrar_cambios_sincronizacion_tk(self, resultado):

        ventana = ctk.CTkToplevel(self)
        ventana.title("Cambios de sincronizacion Herinox")
        ventana.geometry("1120x680")
        ventana.configure(fg_color=COLOR_FONDO_APP)
        ventana.grab_set()

        estado = "OK" if resultado.ok else "OMITIDA"
        resumen = f"Coincidencias: {resultado.matched_codes} | Filas actualizadas: {resultado.updated_rows}"
        ctk.CTkLabel(
            ventana,
            text="📋 ULTIMA SINCRONIZACION DE PLACAS",
            font=("Inter", 18, "bold"),
            text_color=COLOR_TEXTO_TITULO,
        ).pack(anchor="w", padx=20, pady=(18, 6))
        ctk.CTkLabel(
            ventana,
            text=resumen,
            justify="left",
            font=("Inter", 12),
            text_color=COLOR_TEXTO_SECUNDARIO,
        ).pack(anchor="w", padx=20, pady=(0, 10))

        if resultado.message:
            ctk.CTkLabel(
                ventana,
                text=f"Detalle: {resultado.message}",
                justify="left",
                font=("Inter", 11),
                text_color=COLOR_GRIS_MED,
                wraplength=760,
            ).pack(anchor="w", padx=20, pady=(0, 10))

        items = list(getattr(resultado, "updated_items", []) or [])
        if not items:
            ctk.CTkLabel(
                ventana,
                text="No hubo cambios de campos en placas para mostrar.",
                font=("Inter", 12, "italic"),
                text_color=COLOR_TEXTO_SECUNDARIO,
            ).pack(anchor="w", padx=20, pady=10)
            return

        cont = ctk.CTkScrollableFrame(
            ventana,
            fg_color=COLOR_TARJETA,
            corner_radius=10,
            border_width=1,
            border_color=COLOR_BORDE
        )
        cont.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        max_rows = 500
        mostrados = items[:max_rows]
        if len(items) > max_rows:
            ctk.CTkLabel(
                cont,
                text=f"Mostrando {max_rows} de {len(items)} actualizaciones.",
                font=("Inter", 11, "bold"),
                text_color=COLOR_GRIS_MED,
            ).pack(anchor="w", padx=12, pady=(10, 4))

        parametros_tabla = ["Thickness", "Material", "Length", "Width", "LB", "MXN", "$$/LB", "Stock"]

        for row in mostrados:
            codigo = str(row.get("arga_code", "")).strip() or "SIN_CODIGO"
            sheet = str(row.get("sheet", "")).strip()
            fields = row.get("fields") or []
            changes = row.get("changes") or []
            if not changes and fields:
                changes = [{"field": f, "before": "-", "after": "-"} for f in fields]
            cambios_map = {str(c.get("field", "")).strip(): c for c in changes}

            card = ctk.CTkFrame(cont, fg_color="#F8FAFC", corner_radius=8, border_width=1, border_color=COLOR_BORDE)
            card.pack(fill="x", padx=10, pady=6)

            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=10, pady=(8, 4))
            ctk.CTkLabel(
                top,
                text=f"{codigo}  ({sheet})",
                font=("Inter", 13, "bold"),
                text_color=COLOR_TEXTO_TITULO,
            ).pack(side="left")

            ctk.CTkLabel(
                top,
                text=f"Cambios: {len(changes)}",
                font=("Inter", 11, "bold"),
                text_color=COLOR_AZUL_ACCENTO,
            ).pack(side="right")

            tabla = ctk.CTkFrame(card, fg_color="transparent")
            tabla.pack(fill="x", padx=10, pady=(0, 10))
            # Tabla invertida estilo Excel:
            # columnas = parámetros, filas = Antes/Después/Estatus
            total_cols = len(parametros_tabla) + 1  # +1 para columna etiqueta de fila
            for col in range(total_cols):
                if col == 0:
                    tabla.columnconfigure(col, weight=1, minsize=95)
                else:
                    tabla.columnconfigure(col, weight=1, minsize=120)

            # Fila 0: encabezados (parámetros)
            header_cells = ["CAMPO"] + parametros_tabla
            for col, txt in enumerate(header_cells):
                ctk.CTkLabel(
                    tabla,
                    text=txt,
                    font=("Inter", 10, "bold"),
                    text_color="white",
                    fg_color=COLOR_GRIS_MED,
                    corner_radius=4,
                    width=120,
                    height=28,
                ).grid(row=0, column=col, sticky="nsew", padx=1, pady=1)

            # Filas de datos
            filas = [
                ("ANTES", "#DC2626"),
                ("DESPUES", "#16A34A"),
                ("ESTATUS", COLOR_GRIS_DARK),
            ]
            for row_idx, (label, label_color) in enumerate(filas, start=1):
                ctk.CTkLabel(
                    tabla,
                    text=label,
                    font=("Inter", 10, "bold"),
                    text_color="white" if label != "ESTATUS" else COLOR_TEXTO_TITULO,
                    fg_color=label_color if label != "ESTATUS" else "#E2E8F0",
                    corner_radius=4,
                    width=95,
                    height=28,
                ).grid(row=row_idx, column=0, sticky="nsew", padx=1, pady=1)

                for col_idx, parametro in enumerate(parametros_tabla, start=1):
                    item = cambios_map.get(parametro)
                    cambiado = item is not None
                    antes = str(item.get("before", "")).strip() if cambiado else "N/A"
                    despues = str(item.get("after", "")).strip() if cambiado else "N/A"
                    antes = antes or "N/A"
                    despues = despues or "N/A"

                    if label == "ANTES":
                        valor = antes
                        color = "#DC2626" if cambiado else COLOR_TEXTO_SECUNDARIO
                    elif label == "DESPUES":
                        valor = despues
                        color = "#16A34A" if cambiado else COLOR_TEXTO_SECUNDARIO
                    else:
                        valor = "CAMBIO" if cambiado else "N/A"
                        color = "#16A34A" if cambiado else COLOR_TEXTO_SECUNDARIO

                    ctk.CTkLabel(
                        tabla,
                        text=valor,
                        font=("Inter", 10, "bold" if label == "ESTATUS" else "normal"),
                        text_color=color,
                        fg_color="#FFFFFF" if row_idx % 2 == 1 else "#F8FAFC",
                        corner_radius=4,
                        width=120,
                        height=28,
                    ).grid(row=row_idx, column=col_idx, sticky="nsew", padx=1, pady=1)

    # --- VENTANA DE INVENTARIO DE REMANENTES ---
    def abrir_inventario_remanentes(self):
        ventana = ctk.CTkToplevel(self)
        ventana.title("Inventario de Remanentes (> 400 in²)")
        ventana.geometry("750x550")
        ventana.configure(fg_color=COLOR_FONDO_APP) 
        ventana.attributes('-topmost', True)
        ventana.grab_set()

        ctk.CTkLabel(ventana, text="📂 HISTORIAL DE REMANENTES DISPONIBLES", font=("Inter", 18, "bold"), text_color=COLOR_TEXTO_TITULO).pack(pady=25)
        
        container = ctk.CTkScrollableFrame(ventana, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=25, pady=(0, 20))

        ruta_csv = "inventario_remanentes.csv"
        
        if not os.path.exists(ruta_csv):
            ctk.CTkLabel(container, text="No hay remanentes registrados todavía.", text_color=COLOR_TEXTO_SECUNDARIO, font=("Inter", 13, "italic")).pack(pady=60)
        else:
            try:
                with open(ruta_csv, mode="r", encoding='utf-8') as f:
                    reader = csv.reader(f)
                    data = list(reader)
                    if len(data) <= 1:
                        ctk.CTkLabel(container, text="Inventario vacío.", text_color=COLOR_TEXTO_SECUNDARIO).pack(pady=40)
                    else:
                        for fila in reversed(data[1:]):
                            card = ctk.CTkFrame(container, fg_color=COLOR_TARJETA, corner_radius=12, border_width=1, border_color=COLOR_BORDE)
                            card.pack(fill="x", pady=6, padx=5)
                            
                            info_frame = ctk.CTkFrame(card, fg_color="transparent")
                            info_frame.pack(side="left", padx=20, pady=15)

                            txt_id = f"🆔 {fila[1]}"
                            ctk.CTkLabel(info_frame, text=txt_id, font=("Inter", 14, "bold"), text_color=COLOR_AZUL_ACCENTO).pack(anchor="w")

                            txt_detalles = f"📅 {fila[0]}  |  🛠 {fila[2]}  •  📏 CAL: {fila[3]}"
                            ctk.CTkLabel(info_frame, text=txt_detalles, font=("Inter", 11), text_color=COLOR_TEXTO_SECUNDARIO).pack(anchor="w")
                            
                            area_txt = f"✨ {fila[4]} in²"
                            ctk.CTkLabel(card, text=area_txt, font=("Inter", 15, "bold"), text_color=COLOR_TEXTO_TITULO).pack(side="right", padx=25)
            except Exception as e:
                ctk.CTkLabel(container, text=f"Error al leer base de datos: {e}", text_color="#A31A1A").pack(pady=20)