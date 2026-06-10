import os
from tkinter import ttk, filedialog, messagebox
import customtkinter as ctk

# Misma paleta que la barra de la pestaña NESTING (tonos Arga).
ARGB_BTN_1 = "#202A36"
ARGB_BTN_2 = "#334659"
ARGB_BTN_3 = "#455E75"
ARGB_BTN_4 = "#708DA9"
ARGB_BTN_5 = "#8AABC2"


class EditorLoteWindow(ctk.CTkToplevel):
    def __init__(self, tab_nesting):
        super().__init__(tab_nesting)

        self.tab = tab_nesting
        self.title("Editar lote activo")
        self.geometry("1220x620")
        self.resizable(True, True)
        self.transient(tab_nesting.winfo_toplevel())
        self.grab_set()
        self._after_ids = []
        self.protocol("WM_DELETE_WINDOW", self._cerrar_seguro)

        try:
            self._programar_after(50, self._focus_editor_seguro)
        except Exception:
            pass

        self._setup_ui()
        self._refrescar_tabla()
    
    def _focus_editor_seguro(self):
        if not self.winfo_exists():
            return
        try:
            self.focus_force()
        except Exception:
            pass

    def _destruir_diferido(self):
        if not self.winfo_exists():
            return
        try:
            self.destroy()
        except Exception:
            pass

    def _setup_ui(self):
        self.configure(fg_color="#F1F5F9")

        root = ctk.CTkFrame(self, fg_color="#FFFFFF", corner_radius=12, border_width=1, border_color="#CBD5E1")
        root.pack(fill="both", expand=True, padx=12, pady=12)

        header = ctk.CTkFrame(root, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 6))

        self.lbl_titulo = ctk.CTkLabel(
            header,
            text="EDITOR DE LOTE ACTIVO",
            font=("Inter", 20, "bold"),
            text_color="#0F172A",
        )
        self.lbl_titulo.pack(side="left")

        self.lbl_info = ctk.CTkLabel(
            header,
            text="",
            font=("Inter", 12, "bold"),
            text_color="#475569",
        )
        self.lbl_info.pack(side="right")

        self.lbl_ayuda = ctk.CTkLabel(
            root,
            text=(
                "Agregar/Reemplazar DXF: intenta leer nombre/material/calibre/QTY desde la nomenclatura del archivo. "
                "Si falta algún dato, usa el renglón seleccionado como respaldo."
            ),
            font=("Inter", 11),
            text_color="#64748B",
            anchor="w",
            justify="left",
        )
        self.lbl_ayuda.pack(fill="x", padx=14, pady=(0, 10))

        tabla_wrap = ctk.CTkFrame(root, fg_color="transparent")
        tabla_wrap.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        cols = ("idx", "nombre", "material", "qty", "calibre", "estado", "ruta")
        self.tree = ttk.Treeview(tabla_wrap, columns=cols, show="headings", selectmode="extended", height=18)

        self.tree.heading("idx", text="#")
        self.tree.heading("nombre", text="PIEZA / REF")
        self.tree.heading("material", text="MATERIAL")
        self.tree.heading("qty", text="QTY")
        self.tree.heading("calibre", text="CALIBRE")
        self.tree.heading("estado", text="ESTADO")
        self.tree.heading("ruta", text="RUTA DXF")

        self.tree.column("idx", width=45, anchor="center")
        self.tree.column("nombre", width=240, anchor="w")
        self.tree.column("material", width=120, anchor="center")
        self.tree.column("qty", width=70, anchor="center")
        self.tree.column("calibre", width=90, anchor="center")
        self.tree.column("estado", width=90, anchor="center")
        self.tree.column("ruta", width=480, anchor="w")

        yscroll = ttk.Scrollbar(tabla_wrap, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(tabla_wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        tabla_wrap.grid_rowconfigure(0, weight=1)
        tabla_wrap.grid_columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(root, fg_color="transparent")
        footer.pack(fill="x", padx=12, pady=(0, 12))

        self.btn_agregar = ctk.CTkButton(
            footer,
            text="➕ AGREGAR DXF",
            width=150,
            fg_color=ARGB_BTN_2,
            hover_color=ARGB_BTN_3,
            text_color="white",
            command=self._agregar_dxfs
        )
        self.btn_agregar.pack(side="left", padx=(0, 8))

        self.btn_eliminar = ctk.CTkButton(
            footer,
            text="🗑 ELIMINAR",
            width=130,
            fg_color=ARGB_BTN_1,
            hover_color=ARGB_BTN_2,
            text_color="white",
            command=self._eliminar_seleccionados
        )
        self.btn_eliminar.pack(side="left", padx=8)

        self.btn_reemplazar = ctk.CTkButton(
            footer,
            text="♻ REEMPLAZAR DXF",
            width=160,
            fg_color=ARGB_BTN_3,
            hover_color=ARGB_BTN_4,
            text_color="white",
            command=self._reemplazar_dxf
        )
        self.btn_reemplazar.pack(side="left", padx=8)

        self.btn_renestear = ctk.CTkButton(
            footer,
            text="🚀 RENESTEAR LOTE",
            width=180,
            fg_color="#1E293B",
            hover_color="#475569",
            text_color="white",
            command=self._renestear_lote
        )
        self.btn_renestear.pack(side="right", padx=(8, 0))

        self.btn_cerrar = ctk.CTkButton(
            footer,
            text="CERRAR",
            width=110,
            fg_color=ARGB_BTN_4,
            hover_color=ARGB_BTN_5,
            text_color="#0F172A",
            command=self._cerrar_seguro
        )
        self.btn_cerrar.pack(side="right", padx=(8, 0))

    def _datos_actuales(self):
        return list(getattr(self.tab.app, "editable_inputs_actuales", []) or [])

    def _refresh_info(self):
        datos = self._datos_actuales()
        idx = int(getattr(self.tab, "lote_actual_idx", 0) or 0) + 1
        self.lbl_info.configure(
            text=f"Work Order seleccionado: {idx} | Piezas editables: {len(datos)}"
        )

    def _refrescar_tabla(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        datos = self._datos_actuales()
        for i, fila in enumerate(datos, start=1):
            nombre, material, qty, calibre, estado, ruta = fila
            self.tree.insert(
                "",
                "end",
                iid=str(i - 1),
                values=(i, nombre, material, qty, calibre, estado, ruta)
            )

        self._refresh_info()

    def _indices_seleccionados(self):
        seleccion = list(self.tree.selection())
        try:
            return sorted([int(x) for x in seleccion])
        except Exception:
            return []

    def _fila_base_seleccionada(self):
        indices = self._indices_seleccionados()
        datos = self._datos_actuales()
        if indices:
            idx = indices[0]
            if 0 <= idx < len(datos):
                return datos[idx]
        if datos:
            return datos[0]
        return None

    def _agregar_dxfs(self):
        rutas = filedialog.askopenfilenames(
            title="Seleccionar DXF para agregar al lote activo",
            filetypes=[("Archivos DXF", "*.dxf"), ("Todos los archivos", "*.*")]
        )
        if not rutas:
            return

        try:
            self.tab.agregar_dxfs_a_lote(list(rutas), fila_base=self._fila_base_seleccionada())
            self._refrescar_tabla()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudieron agregar los DXF:\n{e}")

    def _eliminar_seleccionados(self):
        indices = self._indices_seleccionados()
        if not indices:
            return messagebox.showwarning("Atención", "Selecciona una o más piezas para eliminar.")

        if not messagebox.askyesno(
            "Confirmar eliminación",
            f"¿Eliminar {len(indices)} pieza(s) del lote activo?"
        ):
            return

        try:
            self.tab.eliminar_piezas_de_lote(indices)
            self._refrescar_tabla()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudieron eliminar las piezas:\n{e}")

    def _reemplazar_dxf(self):
        indices = self._indices_seleccionados()
        if len(indices) != 1:
            return messagebox.showwarning("Atención", "Selecciona exactamente una pieza para reemplazar su DXF.")

        nueva_ruta = filedialog.askopenfilename(
            title="Seleccionar nuevo DXF para reemplazo",
            filetypes=[("Archivos DXF", "*.dxf"), ("Todos los archivos", "*.*")]
        )
        if not nueva_ruta:
            return

        try:
            self.tab.reemplazar_dxf_de_lote(indices[0], nueva_ruta)
            self._refrescar_tabla()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo reemplazar el DXF:\n{e}")

    def _renestear_lote(self):
        datos = self._datos_actuales()
        if not datos:
            return messagebox.showwarning("Atención", "El lote activo no tiene piezas para renestear.")

        if not messagebox.askyesno(
            "Confirmar renesteo",
            "Se renesteará únicamente el lote activo.\n\n¿Deseas continuar?"
        ):
            return

        try:
            self.tab.renestear_lote_actual()
            self._cerrar_seguro()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo iniciar el renesteo:\n{e}")

    def _programar_after(self, delay_ms, callback):
        after_id = self.after(delay_ms, callback)
        self._after_ids.append(after_id)
        return after_id

    def _cancelar_afters(self):
        for after_id in getattr(self, "_after_ids", []):
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
        self._after_ids = []

    def _cerrar_seguro(self):
        self._cancelar_afters()

        if not self.winfo_exists():
            return

        try:
            # Quitar foco del tree/editor antes de destruir
            parent = self.tab.winfo_toplevel() if hasattr(self, "tab") else None
            if parent and parent.winfo_exists():
                try:
                    parent.focus_force()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self.grab_release()
        except Exception:
            pass

        try:
            # Ocultar primero para evitar más interacción con widgets destruyéndose
            self.withdraw()
        except Exception:
            pass

        # Destruir diferido, no inmediato
        try:
            self._programar_after(20, self._destruir_diferido)
        except Exception:
            try:
                self.destroy()
            except Exception:
                pass


def abrir_editor_lote(tab_nesting):
    return EditorLoteWindow(tab_nesting)