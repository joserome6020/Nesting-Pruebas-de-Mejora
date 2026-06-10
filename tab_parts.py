import os
import csv
import json
import re
from pathlib import Path

import customtkinter as ctk
from tkinter import ttk, messagebox
from modules.visualizer import VisorDXF, generar_thumbnail
from responsive_layout import configurar_contenedor_expandible

COLOR_TARJETA = "#FFFFFF"
COLOR_BORDE = "#CBD5E1"
COLOR_GRIS_DARK = "#1E293B"
COLOR_GRIS_MED = "#475569"
COLOR_TEXTO_TITULO = "#0F172A"
COLOR_HOVER = "#E2E8F0"

# Tonos alineados con la barra de herramientas de la pestaña NESTING.
ARGB_BTN_1 = "#202A36"
ARGB_BTN_2 = "#334659"
ARGB_BTN_3 = "#455E75"
ARGB_BTN_4 = "#708DA9"


class TabParts(ctk.CTkFrame):
    def __init__(self, master, app_principal):
        super().__init__(master, fg_color="transparent")
        self.app = app_principal

        self.local_col_config = [
            {"weight": 3, "min": 160},
            {"weight": 2, "min": 100},
            {"weight": 1, "min": 50},
            {"weight": 1, "min": 80},
            {"weight": 1, "min": 70},
            {"weight": 1, "min": 90},
            {"weight": 1, "min": 70},
        ]

        # Estado para lista de largos
        self.btn_lista_largos = None
        self.ventana_lista_largos = None

        self.rutas_dxf_actuales = []

        self.setup_ui()

    def setup_ui(self):
        configurar_contenedor_expandible(self, filas=1, columnas=2)
        # Mín. ~680 px = suma de columnas (620) + scroll/padding; crece con la ventana (sin sash).
        self.columnconfigure(0, weight=2, minsize=680)
        self.columnconfigure(1, weight=3, minsize=320)

        # --- PANEL IZQUIERDO (ancho fijo; sin divisor arrastrable) ---
        frame_tabla = ctk.CTkFrame(
            self,
            fg_color=COLOR_TARJETA,
            border_width=1,
            border_color=COLOR_BORDE,
            corner_radius=15,
        )
        frame_tabla.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        frame_header = ctk.CTkFrame(frame_tabla, fg_color="transparent")
        frame_header.pack(fill="x", padx=25, pady=(15, 0))

        self.lbl_tanques = ctk.CTkLabel(
            frame_header,
            text="⚙️ TANQUES DEL PROYECTO:",
            font=("Inter", 15, "bold"),
            text_color="#3B82F6"
        )
        self.lbl_tanques.pack(side="left", anchor="w")

        self.ent_tanques = ctk.CTkEntry(
            frame_header,
            width=70,
            height=30,
            font=("Inter", 14, "bold"),
            justify="center",
        )
        self.ent_tanques.pack(side="left", padx=(8, 4))
        self.ent_tanques.insert(0, "X1")

        self.btn_aplicar_tanques = ctk.CTkButton(
            frame_header,
            text="Aplicar",
            width=72,
            height=30,
            fg_color=ARGB_BTN_2,
            hover_color=ARGB_BTN_3,
            text_color="white",
            font=("Inter", 11, "bold"),
            command=self.aplicar_cantidad_tanques,
        )
        self.btn_aplicar_tanques.pack(side="left", padx=(2, 0))
        self.ent_tanques.bind("<Return>", lambda e: self.aplicar_cantidad_tanques())

        frame_botones = ctk.CTkFrame(frame_header, fg_color="transparent")
        frame_botones.pack(side="right", anchor="e")

        self.btn_lista_largos = ctk.CTkButton(
            frame_botones,
            text="Lista de largos",
            width=140,
            height=32,
            fg_color=ARGB_BTN_3,
            hover_color=ARGB_BTN_4,
            text_color="white",
            font=("Inter", 12, "bold"),
            state="normal",
            command=self.abrir_ventana_lista_largos
        )
        self.btn_lista_largos.pack(side="right", padx=(0, 8))

        head = ctk.CTkFrame(frame_tabla, height=45, fg_color=COLOR_GRIS_MED, corner_radius=0)
        head.pack(fill="x", padx=(5, 21), pady=(10, 0))
        head.grid_propagate(False)

        titulos = ["PIEZA / REF", "MATERIAL", "QTY", "TOTAL QTY", "CALIBRE", "ESTADO", "VISTA"]
        for i, txt in enumerate(titulos):
            head.columnconfigure(
                i,
                weight=self.local_col_config[i]["weight"],
                minsize=self.local_col_config[i]["min"],
                uniform="parts"
            )
            ctk.CTkLabel(
                head,
                text=txt,
                font=("Inter", 11, "bold"),
                text_color="white"
            ).grid(row=0, column=i, sticky="nsew")

        self.lista_scroll = ctk.CTkScrollableFrame(frame_tabla, fg_color="transparent", corner_radius=0)
        self.lista_scroll.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        # --- PANEL DERECHO ---
        self.frame_derecho = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_derecho.grid(row=0, column=1, sticky="nsew")

        self.frame_derecho.rowconfigure(0, weight=100)
        self.frame_derecho.rowconfigure(1, weight=0)
        self.frame_derecho.columnconfigure(0, weight=1)

        # 1. Visor
        frame_visor_bg = ctk.CTkFrame(
            self.frame_derecho,
            fg_color=COLOR_TARJETA,
            border_width=1,
            border_color=COLOR_BORDE,
            corner_radius=15
        )
        frame_visor_bg.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        ctk.CTkLabel(
            frame_visor_bg,
            text="DETALLE DE PIEZA",
            font=("Inter", 13, "bold"),
            text_color=COLOR_TEXTO_TITULO
        ).pack(pady=8)

        self.frame_black_visor = ctk.CTkFrame(frame_visor_bg, fg_color="#0F172A", corner_radius=10)
        self.frame_black_visor.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.visor = VisorDXF(self.frame_black_visor)

        # Información técnica ahora vive en la tabla azul integrada del visor (sin panel duplicado).

    def refrescar_tabla(self, datos):
        multiplicador = getattr(self.app, "multiplicador_tanques", 1)
        self.lbl_tanques.configure(text="⚙️ TANQUES DEL PROYECTO:")
        try:
            self.ent_tanques.delete(0, "end")
            self.ent_tanques.insert(0, f"X{int(multiplicador)}")
        except Exception:
            pass

        self.rutas_dxf_actuales = []

        for w in self.lista_scroll.winfo_children():
            w.destroy()

        for idx, item in enumerate(datos):
            pieza, mat, qty_total, cal, st, ruta = item

            if ruta:
                self.rutas_dxf_actuales.append(str(ruta))

            try:
                tot_val = int(qty_total)
                qty_unidad = max(1, tot_val // multiplicador)
            except Exception:
                tot_val, qty_unidad = qty_total, qty_total

            color_fondo = "#FFFFFF" if idx % 2 == 0 else "#F8FAFC"
            row = ctk.CTkFrame(self.lista_scroll, height=48, fg_color=color_fondo, corner_radius=0)
            row.pack(fill="x")
            row.grid_propagate(False)
            row.orig_color = color_fondo

            valores = [pieza, mat, str(qty_unidad), str(tot_val), cal, st]
            for i, conf in enumerate(self.local_col_config):
                row.columnconfigure(i, weight=conf["weight"], minsize=conf["min"], uniform="parts")
                if i < 6:
                    lbl = ctk.CTkLabel(
                        row,
                        text=valores[i],
                        text_color=COLOR_TEXTO_TITULO,
                        font=("Inter", 11),
                        anchor="w" if i == 0 else "center"
                    )
                    lbl.grid(row=0, column=i, sticky="nsew", padx=15 if i == 0 else 0)
                    lbl.bind("<Button-1>", lambda e, r=ruta, f=row, p=pieza: self.seleccionar_fila(r, f, p))
                else:
                    try:
                        thumb = generar_thumbnail(ruta, size=(32, 32))
                        if thumb:
                            l_t = ctk.CTkLabel(row, text="", image=thumb)
                            l_t.grid(row=0, column=i)
                            l_t.bind("<Button-1>", lambda e, r=ruta, f=row, p=pieza: self.seleccionar_fila(r, f, p))
                    except Exception:
                        pass

            row.bind("<Button-1>", lambda e, r=ruta, f=row, p=pieza: self.seleccionar_fila(r, f, p))
            ctk.CTkFrame(self.lista_scroll, height=1, fg_color=COLOR_BORDE, corner_radius=0).pack(fill="x")

    def _resolver_job_data_csv_actual(self):
        rutas = [str(r[5]) for r in (getattr(self.app, "datos_partes_actuales", []) or []) if len(r) > 5 and r[5]]
        if not rutas:
            return None

        job = str(getattr(self.app, "job_activo", "") or "").strip()
        for ruta in rutas:
            p = Path(ruta)
            candidatos = []
            for actual in [p.parent, *p.parents]:
                if job:
                    candidatos.append(actual / f"job_data_{job}.csv")
                candidatos.extend(sorted(actual.glob("job_data_*.csv")))
            for c in candidatos:
                if c.exists() and c.is_file():
                    return c
        return None

    def _persistir_multiplicador_en_job_data(self, nuevo_mult: int):
        ruta_csv = self._resolver_job_data_csv_actual()
        actualizo_algo = False
        detalle = []

        if ruta_csv is not None:
            try:
                with open(ruta_csv, newline="", encoding="utf-8", errors="ignore") as f:
                    rows = list(csv.reader(f))
                if rows:
                    while len(rows) < 2:
                        rows.append([])
                    while len(rows[1]) <= 3:
                        rows[1].append("")
                    rows[1][3] = str(int(nuevo_mult))
                    with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
                        writer = csv.writer(f)
                        writer.writerows(rows)
                    actualizo_algo = True
                    detalle.append(ruta_csv.name)
            except Exception:
                pass

        # Compatibilidad con archivo legacy: job_data_job / .txt / .json
        rutas = [str(r[5]) for r in (getattr(self.app, "datos_partes_actuales", []) or []) if len(r) > 5 and r[5]]
        for ruta in rutas:
            p = Path(ruta)
            for actual in [p.parent, *p.parents]:
                for nombre in ("job_data_job.json", "job_data_job.txt", "job_data_job"):
                    legacy = actual / nombre
                    if not legacy.exists() or not legacy.is_file():
                        continue
                    try:
                        txt = legacy.read_text(encoding="utf-8", errors="ignore")
                        if nombre.endswith(".json") or txt.strip().startswith("{"):
                            data = json.loads(txt) if txt.strip() else {}
                            if not isinstance(data, dict):
                                data = {}
                            data["cantidad_tanques"] = int(nuevo_mult)
                            data["multiplicador_tanques"] = int(nuevo_mult)
                            legacy.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                        else:
                            nuevo = re.sub(
                                r"(?im)^(\s*(?:cantidad_tanques|multiplicador_tanques)\s*[:=]\s*)\d+\s*$",
                                rf"\g<1>{int(nuevo_mult)}",
                                txt,
                            )
                            if nuevo == txt:
                                nuevo = txt.rstrip() + f"\nmultiplicador_tanques={int(nuevo_mult)}\n"
                            legacy.write_text(nuevo, encoding="utf-8")
                        actualizo_algo = True
                        detalle.append(legacy.name)
                    except Exception:
                        continue
                if actualizo_algo:
                    break
            if actualizo_algo:
                break

        if actualizo_algo:
            return True, ", ".join(sorted(set(detalle)))
        return False, "No se encontró job_data_*.csv ni job_data_job del proyecto actual."

    def aplicar_cantidad_tanques(self):
        valor = str(self.ent_tanques.get() or "").strip().upper()
        if valor.startswith("X"):
            valor = valor[1:].strip()
        if not valor.isdigit() or int(valor) <= 0:
            messagebox.showerror("Valor inválido", "Ingresa una cantidad válida, por ejemplo: X10")
            return

        nuevo_mult = int(valor)
        mult_actual = max(1, int(getattr(self.app, "multiplicador_tanques", 1) or 1))

        ok, msg = self._persistir_multiplicador_en_job_data(nuevo_mult)
        if not ok:
            return messagebox.showerror("No se pudo actualizar", msg)

        nuevos_datos = []
        for fila in getattr(self.app, "datos_partes_actuales", []) or []:
            try:
                pieza, mat, qty_total, cal, st, ruta = fila
                qty_total_int = int(str(qty_total).strip())
                qty_base = max(1, qty_total_int // mult_actual)
                nuevos_total = qty_base * nuevo_mult
                nuevos_datos.append((pieza, mat, str(nuevos_total), cal, st, ruta))
            except Exception:
                nuevos_datos.append(fila)

        self.app.multiplicador_tanques = nuevo_mult
        self.app.cargar_datos_parts(nuevos_datos)
        messagebox.showinfo("Actualizado", f"Cantidad de tanques actualizada a X{nuevo_mult}.")

    def seleccionar_fila(self, ruta_dxf, frame_fila, nombre_pieza):
        for child in self.lista_scroll.winfo_children():
            if hasattr(child, "orig_color"):
                child.configure(fg_color=child.orig_color)
        frame_fila.configure(fg_color="#DBEAFE")

        if os.path.exists(ruta_dxf):
            self.visor.renderizar_dxf(ruta_dxf)
            # Mantener una sola fuente de verdad para medidas: el propio render del visor (con detección de unidades).
            self.visor.actualizar_info_extra(referencia=nombre_pieza)

    # =========================================================
    # HELPERS GENERALES AUTODXF
    # =========================================================
    def _resolver_autodxf_desde_ruta(self, ruta_archivo: str):
        try:
            p = Path(str(ruta_archivo))
        except Exception:
            return None

        candidatos = [p]
        candidatos.extend(p.parents)

        for actual in candidatos:
            nombre = actual.name.strip().lower()

            if nombre == "autodxf":
                return actual

            if nombre == "processed files":
                padre = actual.parent
                if padre.name.strip().lower() == "autodxf":
                    return padre

        return None

    def _resolver_job_desde_autodxf(self, ruta_autodxf: Path) -> str:
        """
        Intenta sacar el nombre del job desde la ruta:
        .../<JOB>/MODEL CORE FILES/AutoDXF
        """
        try:
            actual = ruta_autodxf
            while actual.parent != actual:
                if actual.name.strip().lower() == "model core files":
                    return actual.parent.name
                actual = actual.parent
        except Exception:
            pass

        try:
            return ruta_autodxf.parent.name
        except Exception:
            return "JOB_DESCONOCIDO"

    def _normalizar_key_csv(self, value: str) -> str:
        text = str(value or "").strip().lower().lstrip("\ufeff")
        text = (
            text.replace("á", "a")
            .replace("é", "e")
            .replace("í", "i")
            .replace("ó", "o")
            .replace("ú", "u")
        )
        return text

    def _safe_int(self, value, default=0):
        try:
            if value is None or value == "":
                return default
            return int(float(value))
        except Exception:
            return default

    def _normalizar_nombre_dxf(self, value: str) -> str:
        txt = str(value or "").replace("\\", "/").strip().lower()
        txt = os.path.basename(txt)
        txt = " ".join(txt.split())
        return txt

    # =========================================================
    # LISTA DE LARGOS DESDE CSV EN AUTODXF
    # =========================================================
    def _resolver_csv_lista_largos(self, ruta_autodxf: Path):
        candidatos_exactos = [
            "Lista_Perfiles_Clasificados.csv",
            "materiales_input.csv",
            "Lista_Largos.csv",
        ]

        for nombre in candidatos_exactos:
            ruta = ruta_autodxf / nombre
            if ruta.exists() and ruta.is_file():
                return ruta

        try:
            for archivo in sorted(ruta_autodxf.glob("*.csv")):
                nombre = archivo.name.lower()
                if "lista" in nombre and ("perfil" in nombre or "larg" in nombre):
                    return archivo
        except Exception:
            pass

        return None

    def _mapear_columnas_lista_largos(self, fieldnames):
        mapa = {self._normalizar_key_csv(c): c for c in (fieldnames or [])}
        return {
            "nombre": mapa.get("nombre"),
            "clasificacion": mapa.get("clasificacion") or mapa.get("clasificación"),
            "largo_in": mapa.get("largo (in)") or mapa.get("largo"),
            "cantidad": mapa.get("cantidad") or mapa.get("qty"),
        }

    def _leer_csv_lista_largos(self, csv_path: Path):
        encodings = ("utf-8-sig", "cp1252", "latin-1")
        ultimo_error = None

        for enc in encodings:
            try:
                with csv_path.open("r", encoding=enc, newline="") as f:
                    reader = csv.DictReader(f)
                    columnas = self._mapear_columnas_lista_largos(reader.fieldnames or [])

                    if not columnas["nombre"] or not columnas["cantidad"]:
                        raise ValueError(
                            f"CSV sin columnas mínimas esperadas. Detectadas: {reader.fieldnames}"
                        )

                    rows = []
                    for raw in reader:
                        nombre = str(raw.get(columnas["nombre"], "")).strip()
                        clasificacion = str(raw.get(columnas["clasificacion"], "")).strip() if columnas["clasificacion"] else ""
                        largo_txt = str(raw.get(columnas["largo_in"], "0")).strip() if columnas["largo_in"] else "0"
                        cantidad_txt = str(raw.get(columnas["cantidad"], "0")).strip()

                        if not nombre:
                            continue

                        try:
                            largo_in = round(float(largo_txt or 0), 3)
                        except Exception:
                            largo_in = 0.0

                        try:
                            cantidad = int(float(cantidad_txt or 0))
                        except Exception:
                            cantidad = 0

                        rows.append({
                            "nombre": nombre,
                            "clasificacion": clasificacion,
                            "largo_in": largo_in,
                            "cantidad": cantidad,
                        })

                    return rows

            except Exception as e:
                ultimo_error = e

        raise RuntimeError(f"No se pudo leer el CSV '{csv_path}'. Error: {ultimo_error}")

    def _cargar_listas_largos_desde_rutas(self):
        """
        Regresa un grupo por cada AutoDXF detectado en el contexto.
        Si el job no tiene CSV, también se agrega para poder mostrarlo explícitamente.
        """
        if not self.rutas_dxf_actuales:
            return []

        grupos = {}
        vistos_autodxf = set()

        for ruta in self.rutas_dxf_actuales:
            ruta_autodxf = self._resolver_autodxf_desde_ruta(ruta)
            if not ruta_autodxf:
                continue

            clave_autodxf = str(ruta_autodxf).lower()
            if clave_autodxf in vistos_autodxf:
                continue
            vistos_autodxf.add(clave_autodxf)

            job = self._resolver_job_desde_autodxf(ruta_autodxf)
            csv_path = self._resolver_csv_lista_largos(ruta_autodxf)

            grupo = {
                "job": job,
                "ruta_autodxf": str(ruta_autodxf),
                "csv_path": str(csv_path) if csv_path else "",
                "rows": [],
                "status": "sin_csv",
                "mensaje": "No se encontró lista de largos para este job.",
            }

            if csv_path:
                try:
                    rows = self._leer_csv_lista_largos(csv_path)
                    grupo["rows"] = rows
                    grupo["status"] = "ok"
                    grupo["mensaje"] = f"CSV encontrado: {csv_path.name}"
                except Exception as e:
                    grupo["status"] = "error_csv"
                    grupo["mensaje"] = f"No se pudo leer el CSV: {e}"
                    print(f"[TAB_PARTS][LISTA_LARGOS][WARN] No se pudo leer '{csv_path}': {e}")

            grupos[clave_autodxf] = grupo

        return sorted(list(grupos.values()), key=lambda g: str(g.get("job", "")).lower())

    def _crear_bloque_job(self, contenedor, grupo, columnas, encabezados, anchos):
        status = grupo.get("status", "sin_csv")

        if status == "ok":
            color_titulo = "#2563EB"
            texto_status = "CON LISTA DE LARGOS"
            color_status = "#16A34A"
            color_fondo = "#F8FAFC"
        elif status == "sin_csv":
            color_titulo = "#DC2626"
            texto_status = "SIN LISTA DE LARGOS"
            color_status = "#DC2626"
            color_fondo = "#FEF2F2"
        else:
            color_titulo = "#D97706"
            texto_status = "ERROR AL LEER CSV"
            color_status = "#D97706"
            color_fondo = "#FFFBEB"

        frame_job = ctk.CTkFrame(
            contenedor,
            fg_color=color_fondo,
            border_width=1,
            border_color=COLOR_BORDE,
            corner_radius=10
        )
        frame_job.pack(fill="x", expand=True, padx=6, pady=6)

        header_job = ctk.CTkFrame(frame_job, fg_color="transparent")
        header_job.pack(fill="x", padx=12, pady=(10, 2))

        ctk.CTkLabel(
            header_job,
            text=f"JOB: {grupo['job']}",
            font=("Inter", 14, "bold"),
            text_color=color_titulo
        ).pack(side="left", anchor="w")

        ctk.CTkLabel(
            header_job,
            text=texto_status,
            font=("Inter", 11, "bold"),
            text_color=color_status
        ).pack(side="right", anchor="e")

        if status != "ok":
            ctk.CTkLabel(
                frame_job,
                text=grupo.get("mensaje", ""),
                font=("Inter", 10),
                text_color=color_status
            ).pack(anchor="w", padx=12, pady=(0, 8))
        else:
            ctk.CTkFrame(frame_job, fg_color="transparent", height=6).pack(fill="x")

        if status != "ok":
            return

        tree_frame = ctk.CTkFrame(frame_job, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        tree = ttk.Treeview(
            tree_frame,
            columns=columnas,
            show="headings",
            height=min(max(len(grupo["rows"]), 3), 10)
        )
        tree.pack(side="left", fill="both", expand=True)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        vsb.pack(side="right", fill="y")
        tree.configure(yscrollcommand=vsb.set)

        for col in columnas:
            tree.heading(col, text=encabezados[col])
            tree.column(col, width=anchos[col], anchor="center")

        for row in grupo["rows"]:
            tree.insert(
                "",
                "end",
                values=(
                    row.get("nombre", ""),
                    row.get("clasificacion", ""),
                    f"{float(row.get('largo_in', 0) or 0):.3f}",
                    row.get("cantidad", 0),
                )
            )

    def abrir_ventana_lista_largos(self):
        grupos = self._cargar_listas_largos_desde_rutas()

        if not grupos:
            messagebox.showinfo(
                "Lista de largos",
                "No se encontraron rutas AutoDXF válidas en el contexto actual."
            )
            return

        if self.ventana_lista_largos and self.ventana_lista_largos.winfo_exists():
            self.ventana_lista_largos.destroy()

        self.ventana_lista_largos = ctk.CTkToplevel(self)
        self.ventana_lista_largos.title("Lista de largos")
        self.ventana_lista_largos.geometry("1260x680")
        self.ventana_lista_largos.transient(self.winfo_toplevel())
        self.ventana_lista_largos.grab_set()

        frame_main = ctk.CTkFrame(
            self.ventana_lista_largos,
            fg_color=COLOR_TARJETA,
            border_width=1,
            border_color=COLOR_BORDE,
            corner_radius=12
        )
        frame_main.pack(fill="both", expand=True, padx=12, pady=12)

        total_grupos = len(grupos)
        total_ok = sum(1 for g in grupos if g.get("status") == "ok")
        total_sin_csv = sum(1 for g in grupos if g.get("status") == "sin_csv")
        total_error = sum(1 for g in grupos if g.get("status") == "error_csv")
        total_rows = sum(len(g["rows"]) for g in grupos if g.get("status") == "ok")

        ctk.CTkLabel(
            frame_main,
            text="LISTA DE LARGOS",
            font=("Inter", 16, "bold"),
            text_color=COLOR_TEXTO_TITULO
        ).pack(anchor="w", padx=15, pady=(12, 4))

        ctk.CTkLabel(
            frame_main,
            text=(
                f"Jobs detectados: {total_grupos}   |   "
                f"Con lista: {total_ok}   |   "
                f"Sin lista: {total_sin_csv}   |   "
                f"Error lectura: {total_error}   |   "
                f"Registros totales: {total_rows}"
            ),
            font=("Inter", 11),
            text_color=COLOR_GRIS_MED
        ).pack(anchor="w", padx=15, pady=(0, 10))

        contenedor = ctk.CTkScrollableFrame(frame_main, fg_color="transparent")
        contenedor.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        columnas = ("nombre", "clasificacion", "largo_in", "cantidad")
        encabezados = {
            "nombre": "NOMBRE",
            "clasificacion": "CLASIFICACIÓN",
            "largo_in": "LARGO (in)",
            "cantidad": "CANTIDAD",
        }
        anchos = {
            "nombre": 360,
            "clasificacion": 180,
            "largo_in": 120,
            "cantidad": 120,
        }

        for grupo in grupos:
            self._crear_bloque_job(contenedor, grupo, columnas, encabezados, anchos)
