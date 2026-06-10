# ==========================================
# nesting_modals.py
# Contiene todas las ventanas emergentes (CTkToplevel)
# ==========================================
import customtkinter as ctk
from tkinter import messagebox
import copy # <--- NUEVO: Librería nativa para clonar memoria

# Colores compartidos
COLOR_BORDE = "#CBD5E1"
COLOR_GRIS_DARK = "#1E293B"
COLOR_GRIS_MED = "#475569"
COLOR_TEXTO_TITULO = "#0F172A"
COLOR_TEXTO_SECUNDARIO = "#64748B"

def centrar_en_monitor_actual(ventana_hija, parent):
    """
    Calcula la posición para que la ventana hija aparezca centrada 
    respecto a la ventana principal (parent), sin importar el monitor.
    """
    ventana_hija.update_idletasks()
    
    ancho_hija = ventana_hija.winfo_width()
    alto_hija = ventana_hija.winfo_height()
    
    ancho_p = parent.winfo_width()
    alto_p = parent.winfo_height()
    x_p = parent.winfo_x()
    y_p = parent.winfo_y()
    
    x_c = x_p + (ancho_p // 2) - (ancho_hija // 2)
    y_c = y_p + (alto_p // 2) - (alto_hija // 2)
    
    ventana_hija.geometry(f"+{x_c}+{y_c}")

def abrir_modal_configuracion(parent):
    ventana = ctk.CTkToplevel(parent)
    ventana.title("Configuración Global")
    ventana.geometry("390x270")
    ventana.configure(fg_color="#F8FAFC")
    ventana.attributes('-topmost', True)
    ventana.grab_set()
    
    centrar_en_monitor_actual(ventana, parent)
    
    ctk.CTkLabel(ventana, text="⚙️ CONFIGURACIÓN GLOBAL", font=("Inter", 14, "bold"), text_color=COLOR_TEXTO_TITULO).pack(pady=(25, 15))
    
    kerf_actual = ""
    try:
        kerf_actual = str(float(parent.ent_kerf.get()))
    except Exception:
        kerf_actual = str(getattr(parent, "global_kerf_val", 0.3))

    frame_kerf = ctk.CTkFrame(ventana, fg_color="transparent")
    frame_kerf.pack(fill="x", padx=30, pady=(8, 6))
    ctk.CTkLabel(frame_kerf, text="Kerf (in):", font=("Inter", 12, "bold"), text_color=COLOR_GRIS_MED).pack(side="left")
    ent_kerf = ctk.CTkEntry(frame_kerf, width=90, height=28)
    ent_kerf.pack(side="right")
    ent_kerf.insert(0, kerf_actual)

    frame_margin = ctk.CTkFrame(ventana, fg_color="transparent")
    frame_margin.pack(fill="x", padx=30, pady=6)
    ctk.CTkLabel(frame_margin, text="Margen de borde (in):", font=("Inter", 12, "bold"), text_color=COLOR_GRIS_MED).pack(side="left")
    ent_margin = ctk.CTkEntry(frame_margin, width=90, height=28)
    ent_margin.pack(side="right")
    ent_margin.insert(0, str(parent.global_margin_val))
    
    def guardar_y_aplicar():
        try:
            kerf_val = float(ent_kerf.get())
            margin_val = float(ent_margin.get())
            parent.global_margin_val = margin_val
            parent.global_kerf_val = kerf_val
            try:
                parent.ent_kerf.delete(0, "end")
                parent.ent_kerf.insert(0, str(kerf_val))
            except Exception:
                pass
            ventana.destroy()
            parent.ejecutar_nesting()
        except Exception:
            messagebox.showerror("Error", "Kerf y Margen deben ser valores numéricos.", parent=ventana)
            
    btn_guardar = ctk.CTkButton(ventana, text="GUARDAR Y APLICAR", font=("Inter", 12, "bold"), fg_color=COLOR_GRIS_DARK, hover_color=COLOR_GRIS_MED, command=guardar_y_aplicar)
    btn_guardar.pack(pady=(20, 0), padx=30, fill="x")


def abrir_modal_costos(parent):
    ventana = ctk.CTkToplevel(parent)
    ventana.title("Reporte Económico del Proyecto")
    ventana.geometry("480x550") 
    ventana.configure(fg_color="#F8FAFC")
    ventana.attributes('-topmost', True)
    ventana.grab_set()
    
    try: centrar_en_monitor_actual(ventana, parent)
    except: pass
    
    ctk.CTkLabel(ventana, text="💲 RESUMEN DE INVERSIÓN", font=("Inter", 16, "bold"), text_color="#0F172A").pack(pady=(25, 10))
    
    tc = float(getattr(parent, "tipo_cambio_usdmxn", 18.50) or 18.50)
    fuente_tc = str(getattr(parent, "tipo_cambio_fuente", "FALLBACK"))
    ts_tc = str(getattr(parent, "tipo_cambio_actualizado", ""))

    ctk.CTkLabel(ventana, text=f"MXN: ${parent.costo_mxn_val:,.2f}", font=("Inter", 24, "bold"), text_color="#10B981").pack()
    ctk.CTkLabel(ventana, text=f"USD: ${parent.costo_usd_val:,.2f}", font=("Inter", 16, "bold"), text_color="#64748B").pack(pady=(0, 4))
    ctk.CTkLabel(
        ventana,
        text=f"TC DOF usado: {tc:,.4f} MXN/USD ({fuente_tc})",
        font=("Inter", 10),
        text_color="#64748B"
    ).pack()
    if ts_tc:
        ctk.CTkLabel(ventana, text=f"Actualizado: {ts_tc}", font=("Inter", 9), text_color="#94A3B8").pack(pady=(0, 10))
    else:
        ctk.CTkLabel(ventana, text="", font=("Inter", 9), text_color="#94A3B8").pack(pady=(0, 10))
    
    total_mxn_empresa = 0.0
    total_mxn_proveedor = 0.0
    desglose_UI = []

    if hasattr(parent.app, 'resultados_nesting') and parent.app.resultados_nesting:
        for clave, info in parent.app.resultados_nesting.items():
            costo_mat_emp = 0.0
            costo_mat_prov = 0.0
            
            for hoja in info.get("hojas", []):
                if hoja.get("es_retazo", False) or hoja.get("ignorar_deduccion"):
                    continue
                precio = hoja.get("precio_placa", 0.0)
                origen = hoja.get("origen_placa", "EMPRESA")

                if origen == "PROVEEDOR":
                    costo_mat_prov += precio
                else:
                    costo_mat_emp += precio
            
            total_mxn_empresa += costo_mat_emp
            total_mxn_proveedor += costo_mat_prov
            
            costo_total_mat = costo_mat_emp + costo_mat_prov
            
            if costo_mat_emp > 0 and costo_mat_prov == 0:
                etiqueta = "🏢 [EMP]"
            elif costo_mat_prov > 0 and costo_mat_emp == 0:
                etiqueta = "🚚 [PROV]"
            elif costo_mat_prov > 0 and costo_mat_emp > 0:
                etiqueta = "🏢/🚚 [MIX]"
            else:
                etiqueta = "📦 [RET]" 
                
            desglose_UI.append({
                "clave": clave,
                "etiqueta": etiqueta,
                "total": costo_total_mat
            })

    frame_origen = ctk.CTkFrame(ventana, fg_color="transparent")
    frame_origen.pack(fill="x", padx=40, pady=(0, 15))
    
    ctk.CTkLabel(frame_origen, text=f"🏢 Stock Interno: ${total_mxn_empresa:,.2f} MXN", font=("Inter", 13, "bold"), text_color="#0EA5E9").pack(side="top", anchor="center")
    ctk.CTkLabel(frame_origen, text=f"🚚 Gasto Proveedor: ${total_mxn_proveedor:,.2f} MXN", font=("Inter", 13, "bold"), text_color="#EF4444").pack(side="top", anchor="center", pady=(2,0))

    ctk.CTkLabel(ventana, text="📦 DESGLOSE POR MATERIAL", font=("Inter", 12, "bold"), text_color="#1E293B").pack(pady=(10, 5), padx=30, anchor="w")
    
    frame_desglose = ctk.CTkScrollableFrame(ventana, fg_color="#FFFFFF", border_width=1, border_color="#CBD5E1", height=150)
    frame_desglose.pack(fill="both", expand=True, padx=30, pady=(0, 20))
    
    if desglose_UI:
        for item in desglose_UI:
            f_item = ctk.CTkFrame(frame_desglose, fg_color="transparent")
            f_item.pack(fill="x", pady=2)
            
            texto_mostrar = f"{item['etiqueta']} {item['clave']}"
            
            ctk.CTkLabel(f_item, text=texto_mostrar, font=("Inter", 11), text_color="#1E293B").pack(side="left")
            total_mxn = float(item["total"] or 0.0)
            total_usd = (total_mxn / tc) if tc > 0 else 0.0
            ctk.CTkLabel(
                f_item,
                text=f"${total_mxn:,.2f} MXN  |  ${total_usd:,.2f} USD",
                font=("Inter", 11, "bold"),
                text_color="#0F172A"
            ).pack(side="right")
    else:
        ctk.CTkLabel(frame_desglose, text="No hay datos de nesting calculados.", font=("Inter", 11, "italic")).pack(pady=20)
    
    btn_cerrar = ctk.CTkButton(ventana, text="CERRAR REPORTE", font=("Inter", 12, "bold"), fg_color="#1E293B", hover_color="#475569", height=40, command=ventana.destroy)
    btn_cerrar.pack(padx=30, pady=(0, 25), fill="x")


def mostrar_modal_escenarios(parent, escenarios_resultados):
    if hasattr(parent.app, 'cerrar_ventana_carga'): 
        parent.app.cerrar_ventana_carga()
    parent.btn_run_nest.configure(state="normal")

    ventana = ctk.CTkToplevel(parent)
    ventana.title("Análisis MES de Lotes")
    ventana.geometry("750x570")
    ventana.configure(fg_color="#F1F5F9")
    ventana.transient(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent)
    ventana.grab_set()
    if hasattr(parent, "app") and hasattr(parent.app, "_configurar_popup_modal"):
        parent.app._configurar_popup_modal(ventana)

    centrar_en_monitor_actual(ventana, parent)

    ctk.CTkLabel(ventana, text="⚡ ANÁLISIS DE RENDIMIENTO - WORK ORDERS", font=("Inter", 18, "bold"), text_color=COLOR_TEXTO_TITULO).pack(pady=(25, 5))
    ctk.CTkLabel(ventana, text="Estrategias de corte optimizadas para minimizar el costo operativo.", font=("Inter", 12), text_color=COLOR_TEXTO_SECUNDARIO).pack(pady=(0, 20))

    scroll = ctk.CTkScrollableFrame(ventana, fg_color="transparent")
    scroll.pack(fill="both", expand=True, padx=25, pady=10)

    for idx, item in enumerate(escenarios_resultados[:5]):
        lotes_str = " + ".join([f"{mult} Lote(s) de {k}X" for k, mult in item["config"]])
        borde_color = "#3B82F6" if idx == 0 else COLOR_BORDE
        card = ctk.CTkFrame(scroll, fg_color="#FFFFFF", corner_radius=12, border_width=(2 if idx == 0 else 1), border_color=borde_color)
        card.pack(fill="x", pady=8, padx=5)

        txt_frame = ctk.CTkFrame(card, fg_color="transparent")
        txt_frame.pack(side="left", padx=25, pady=20)
        title_prefix = "🏆 RECOMENDADO: " if idx == 0 else f"Opción {idx+1}: "
        ctk.CTkLabel(txt_frame, text=f"{title_prefix}{lotes_str}", font=("Inter", 15, "bold"), text_color=("#10B981" if idx == 0 else COLOR_TEXTO_TITULO)).pack(anchor="w")
        ctk.CTkLabel(txt_frame, text=f"Eficiencia: {item['efi']:.1f}%  |  Costo Estimado: ${item['costo']:,.2f}", font=("Inter", 12), text_color=COLOR_TEXTO_SECUNDARIO).pack(anchor="w", pady=(2, 0))

        # =========================================================
        # PARCHE ARQUITECTÓNICO: FÁBRICA DE COMANDOS + CLONACIÓN
        # =========================================================
        def crear_comando(resultado_aislado):
            # Clonamos de forma profunda la memoria. Así garantizamos que 
            # si el motor recicló variables, esta opción quede sellada
            # en el tiempo exacto en que fue iterada.
            resultado_clonado = copy.deepcopy(resultado_aislado)
            return lambda: parent.aplicar_escenario_seleccionado(resultado_clonado, ventana)

        btn_sel = ctk.CTkButton(
            card, text="SELECCIONAR", fg_color=COLOR_GRIS_DARK, 
            hover_color=COLOR_GRIS_MED, font=("Inter", 11, "bold"), 
            width=130, height=35,
            command=crear_comando(item["resultados"]) # <--- Llamada a la fábrica
        )
        btn_sel.pack(side="right", padx=25)


def abrir_modal_transferencia(parent):
    piezas_sel = getattr(parent, "piezas_seleccionadas", None) or []
    if not piezas_sel and not parent.info_pieza_seleccionada:
        return
    if not piezas_sel and parent.info_pieza_seleccionada:
        piezas_sel = [parent.info_pieza_seleccionada]

    hojas_disp = parent.app.resultados_nesting.get(parent.clave_actual, {}).get("hojas", [])
    if len(hojas_disp) <= 1:
        return messagebox.showinfo("Aviso", "No hay otras placas de este mismo material para realizar la transferencia.")

    multi = len(piezas_sel) > 1
    ventana = ctk.CTkToplevel(parent)
    ventana.title("Mudar Piezas" if multi else "Mudar Pieza")
    ventana.geometry("520x580" if multi else "520x550")
    ventana.configure(fg_color="#F8FAFC")
    ventana.transient(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent)
    ventana.grab_set()
    if hasattr(parent, "app") and hasattr(parent.app, "_configurar_popup_modal"):
        parent.app._configurar_popup_modal(ventana)

    centrar_en_monitor_actual(ventana, parent)

    header_frame = ctk.CTkFrame(ventana, fg_color="transparent")
    header_frame.pack(fill="x", padx=25, pady=(25, 10))

    titulo = (
        f"🔄 MUDAR {len(piezas_sel)} PIEZAS A OTRA PLACA"
        if multi
        else "🔄 MUDAR PIEZA A OTRA PLACA"
    )
    ctk.CTkLabel(header_frame, text=titulo, font=("Inter", 16, "bold"), text_color="#0F172A").pack(anchor="center")

    if multi:
        ctk.CTkLabel(
            header_frame,
            text="Piezas seleccionadas (Ctrl + clic):",
            font=("Inter", 12),
            text_color="#64748B",
        ).pack(anchor="center", pady=(5, 0))
        lista_nombres = "\n".join(f"• {p.get('nombre', 'Pieza')}" for p in piezas_sel[:8])
        if len(piezas_sel) > 8:
            lista_nombres += f"\n• ... y {len(piezas_sel) - 8} más"
        ctk.CTkLabel(
            header_frame,
            text=lista_nombres,
            font=("Inter", 12, "bold"),
            text_color="#3B82F6",
            justify="center",
        ).pack(anchor="center", pady=(2, 0))
    else:
        nombre_pieza = piezas_sel[0].get("nombre", "")
        ctk.CTkLabel(header_frame, text="Pieza seleccionada:", font=("Inter", 12), text_color="#64748B").pack(anchor="center", pady=(5, 0))
        ctk.CTkLabel(header_frame, text=nombre_pieza, font=("Inter", 14, "bold"), text_color="#3B82F6").pack(anchor="center")

    scroll = ctk.CTkScrollableFrame(ventana, fg_color="#F1F5F9", border_width=1, border_color="#E2E8F0", corner_radius=10)
    scroll.pack(fill="both", expand=True, padx=25, pady=(10, 20))

    var_destino = ctk.IntVar(value=-1)
    
    idx_actual = -1
    for i, h in enumerate(hojas_disp):
        if h is parent.hoja_actual_data: idx_actual = i

    def _sufijo_placa_duplicada(hoja, idx):
        pid = str(hoja.get("placa_id", "") or "")
        if not pid or hoja.get("es_retazo"):
            return ""
        iguales = [
            j for j, h in enumerate(hojas_disp)
            if str(h.get("placa_id", "") or "") == pid and not h.get("es_retazo")
        ]
        if len(iguales) <= 1:
            return ""
        return f" · P{iguales.index(idx) + 1}"

    for i, hoja in enumerate(hojas_disp):
        if i == idx_actual: continue 
        
        efi_dir = float(hoja.get("eficiencia_directa", hoja.get("eficiencia", 0)) or 0)
        efi_real = float(hoja.get("eficiencia_real", efi_dir) or 0)
        nombre_placa = hoja.get('placa_id', f"Placa #{i+1}")
        es_retazo = hoja.get('es_retazo', False)
        w_in = float(hoja.get("placa_w", 0) or 0) / 25.4
        h_in = float(hoja.get("placa_h", 0) or 0) / 25.4
        sufijo_dup = _sufijo_placa_duplicada(hoja, i)
        
        card = ctk.CTkFrame(scroll, fg_color="#FFFFFF", corner_radius=8, border_width=1, border_color="#CBD5E1")
        card.pack(fill="x", pady=6, padx=5)

        if es_retazo:
            texto_principal = f"↳ {nombre_placa} (Accesorios)"
            color_eficiencia = "#38BDF8" 
        else:
            texto_principal = f"◼ {nombre_placa}{sufijo_dup}  ({w_in:.0f}\" x {h_in:.0f}\")"
            color_eficiencia = "#10B981" if efi_dir > 70 else ("#F59E0B" if efi_dir > 40 else "#EF4444")

        rb = ctk.CTkRadioButton(
            card, 
            text=texto_principal,
            variable=var_destino, 
            value=i,
            font=("Inter", 13, "bold"),
            text_color="#1E293B", 
            fg_color="#3B82F6", 
            hover_color="#2563EB",
            radiobutton_width=20,
            radiobutton_height=20
        )
        rb.pack(side="left", pady=15, padx=(15, 10))

        ctk.CTkLabel(
            card,
            text=f"Dir {efi_dir:.1f}% | Real {efi_real:.1f}%",
            font=("Inter", 12, "bold"),
            text_color=color_eficiencia,
        ).pack(side="right", padx=15)

    btn_conf = ctk.CTkButton(
        ventana, 
        text="✅ CONFIRMAR TRANSFERENCIA", 
        font=("Inter", 13, "bold"),
        height=45, 
        corner_radius=8,
        fg_color="#1E293B", 
        hover_color="#334659",
        command=lambda: parent.ejecutar_transferencia(var_destino.get(), hojas_disp, ventana)
    )
    btn_conf.pack(pady=(0, 25), padx=25, fill="x")


def abrir_modal_transferencia_masiva(parent, clave, hoja_origen):
    if not hoja_origen or hoja_origen.get("es_retazo", False):
        return messagebox.showinfo(
            "Aviso",
            "Esta acción solo aplica a placas madre (no RTZ / mini-nest).",
        )

    hojas_disp = parent.app.resultados_nesting.get(clave, {}).get("hojas", [])
    if len(hojas_disp) <= 1:
        return messagebox.showinfo(
            "Aviso",
            "No hay otras placas de este mismo material para recibir piezas.",
        )

    bloque = parent._desglosar_bloque_placa_mini(clave, hoja_origen)
    resumen = bloque.get("resumen_base") or {}
    total_piezas = sum(int(v) for v in resumen.values())
    if total_piezas <= 0:
        return messagebox.showwarning("Atención", "La placa seleccionada no tiene piezas reales para mover.")

    ventana = ctk.CTkToplevel(parent)
    ventana.title("Cambiar piezas a otra placa")
    ventana.geometry("520x580")
    ventana.configure(fg_color="#F8FAFC")
    ventana.attributes("-topmost", True)
    ventana.grab_set()

    centrar_en_monitor_actual(ventana, parent)

    header_frame = ctk.CTkFrame(ventana, fg_color="transparent")
    header_frame.pack(fill="x", padx=25, pady=(25, 10))

    ctk.CTkLabel(
        header_frame,
        text="📦 CAMBIAR PIEZAS A OTRA PLACA",
        font=("Inter", 16, "bold"),
        text_color="#0F172A",
    ).pack(anchor="center")

    placa_origen = str(hoja_origen.get("placa_id", "Placa") or "Placa")
    ctk.CTkLabel(
        header_frame,
        text=f"Origen: {placa_origen}  |  Piezas: {total_piezas}",
        font=("Inter", 12),
        text_color="#64748B",
    ).pack(anchor="center", pady=(6, 0))
    ctk.CTkLabel(
        header_frame,
        text="Se moverán todas las piezas que quepan en la placa destino.",
        font=("Inter", 11),
        text_color="#94A3B8",
    ).pack(anchor="center", pady=(4, 0))

    scroll = ctk.CTkScrollableFrame(
        ventana, fg_color="#F1F5F9", border_width=1, border_color="#E2E8F0", corner_radius=10
    )
    scroll.pack(fill="both", expand=True, padx=25, pady=(10, 20))

    var_destino = ctk.IntVar(value=-1)

    idx_actual = -1
    for i, h in enumerate(hojas_disp):
        if h is hoja_origen:
            idx_actual = i

    def _sufijo_placa_duplicada_m(hoja, idx):
        pid = str(hoja.get("placa_id", "") or "")
        if not pid or hoja.get("es_retazo"):
            return ""
        iguales = [
            j for j, h in enumerate(hojas_disp)
            if str(h.get("placa_id", "") or "") == pid and not h.get("es_retazo")
        ]
        if len(iguales) <= 1:
            return ""
        return f" · P{iguales.index(idx) + 1}"

    for i, hoja in enumerate(hojas_disp):
        if i == idx_actual:
            continue

        efi_dir = float(hoja.get("eficiencia_directa", hoja.get("eficiencia", 0)) or 0)
        efi_real = float(hoja.get("eficiencia_real", efi_dir) or 0)
        nombre_placa = hoja.get("placa_id", f"Placa #{i+1}")
        es_retazo = hoja.get("es_retazo", False)

        card = ctk.CTkFrame(
            scroll, fg_color="#FFFFFF", corner_radius=8, border_width=1, border_color="#CBD5E1"
        )
        card.pack(fill="x", pady=6, padx=5)

        w_in = float(hoja.get("placa_w", 0) or 0) / 25.4
        h_in = float(hoja.get("placa_h", 0) or 0) / 25.4
        sufijo_dup = _sufijo_placa_duplicada_m(hoja, i)

        if es_retazo:
            texto_principal = f"↳ {nombre_placa} (Accesorios)"
            color_eficiencia = "#38BDF8"
        else:
            texto_principal = f"◼ {nombre_placa}{sufijo_dup}  ({w_in:.0f}\" x {h_in:.0f}\")"
            color_eficiencia = "#10B981" if efi_dir > 70 else ("#F59E0B" if efi_dir > 40 else "#EF4444")

        rb = ctk.CTkRadioButton(
            card,
            text=texto_principal,
            variable=var_destino,
            value=i,
            font=("Inter", 13, "bold"),
            text_color="#1E293B",
            fg_color="#3B82F6",
            hover_color="#2563EB",
            radiobutton_width=20,
            radiobutton_height=20,
        )
        rb.pack(side="left", pady=15, padx=(15, 10))

        ctk.CTkLabel(
            card,
            text=f"Dir {efi_dir:.1f}% | Real {efi_real:.1f}%",
            font=("Inter", 12, "bold"),
            text_color=color_eficiencia,
        ).pack(side="right", padx=15)

    btn_conf = ctk.CTkButton(
        ventana,
        text="✅ MOVER PIEZAS POSIBLES",
        font=("Inter", 13, "bold"),
        height=45,
        corner_radius=8,
        fg_color="#1E293B",
        hover_color="#334659",
        command=lambda: parent.ejecutar_transferencia_masiva(
            var_destino.get(), hojas_disp, hoja_origen, clave, ventana
        ),
    )
    btn_conf.pack(pady=(0, 25), padx=25, fill="x")