# ==========================================
# nesting_canvas.py
# Motor Gráfico 2D y Control de Eventos (Matplotlib)
# ==========================================
import customtkinter as ctk
import matplotlib
matplotlib.use("TkAgg")
import time
import copy
from matplotlib import patheffects as mpe
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Polygon as MplPolygon, Rectangle

from shapely import wkt as shapely_wkt
from shapely.affinity import translate
from shapely.geometry import Polygon, box, Point, LineString

from responsive_layout import bind_redimension_figura_matplotlib


def _path_effects_numero_pieza(lw=2.8):
    """Número legible sobre huecos oscuros: relleno negro con contorno blanco."""
    return [mpe.Stroke(linewidth=lw, foreground="white"), mpe.Normal()]


class VisorNesting(ctk.CTkFrame):
    def __init__(self, master, app_principal, callback_seleccion):
        super().__init__(master, fg_color="transparent")
        self.app = app_principal
        self.callback_seleccion = callback_seleccion 
        
        self.hoja_actual_data = None
        self.clave_actual = ""
        self.idx_pieza_seleccionada = -1
        self.info_pieza_seleccionada = None
        self.piezas_seleccionadas_indices = set()
        
        self._is_panning = False
        self._pan_start = (0, 0)
        self._lims = None
        self._btn1_down = False
        self._dragging_piece = False
        self._drag_last_data = None
        self._drag_total_dx = 0.0
        self._drag_total_dy = 0.0
        self._drag_marks_base = None
        self._drag_last_render_ts = 0.0
        self._drag_render_interval_s = 1.0 / 30.0  # Limita redraw pesado a ~30 FPS.
        self._drag_dirty_view = False
        self._nav_preview_active = False
        self._nav_last_render_ts = 0.0
        self._nav_render_interval_s = 1.0 / 45.0
        self._nav_restore_after_id = None
        self._nav_restore_delay_ms = 140
        self._hover_idx = -1
        self._cursor_mode = "normal"  # normal | hover | dragging | panning
        self._manual_piece_indices = []
        self._manual_piece_bounds = {}

        self.setup_canvas()

    def setup_canvas(self):
        self.fig_nest = Figure(figsize=(8, 6), dpi=110)
        self.fig_nest.patch.set_facecolor('#1E293B')
        self.ax_nest = self.fig_nest.add_axes([0, 0, 1, 1])
        self.ax_nest.set_facecolor('#0F172A')
        
        self.canvas_nest = FigureCanvasTkAgg(self.fig_nest, master=self)
        self.canvas_widget = self.canvas_nest.get_tk_widget()
        self.canvas_widget.pack(fill="both", expand=True)

        # Esquina superior derecha: no compite con la tabla de piezas (debajo de la placa, en mm).
        self.coord_text = self.fig_nest.text(
            0.98,
            0.96,
            "",
            color="white",
            alpha=0.88,
            fontsize=9,
            family="monospace",
            ha="right",
            va="top",
        )

        self.canvas_nest.mpl_connect('scroll_event', self.on_scroll)
        self.canvas_nest.mpl_connect('button_press_event', self.on_press)
        self.canvas_nest.mpl_connect('button_release_event', self.on_release)
        self.canvas_nest.mpl_connect('motion_notify_event', self.on_motion)
        self.canvas_nest.mpl_connect('axes_leave_event', self.on_leave) 
        self.canvas_nest.mpl_connect('key_press_event', self.manejar_teclado)

        bind_redimension_figura_matplotlib(
            self.canvas_widget,
            self.fig_nest,
            on_resize=self._redibujar_tras_resize_canvas,
        )

    def _redibujar_tras_resize_canvas(self):
        if not self.hoja_actual_data:
            self.canvas_nest.draw_idle()
            return
        self.dibujar_hoja_full(
            self.hoja_actual_data,
            self.clave_actual,
            selected_indices=self.piezas_seleccionadas_indices,
            preserve_view=False,
        )

    def _es_pieza_seleccionable(self, nombre):
        n = str(nombre or "")
        return not (
            n.startswith("REMANENTE__")
            or n.startswith("REF__")
            or n.startswith("RETAZO_")
            or n.startswith("TATUAJE__")
        )

    def _indices_seleccion_activos(self, selected_idx=-1, selected_indices=None):
        if selected_indices is not None:
            return set(selected_indices)
        if self.piezas_seleccionadas_indices:
            return set(self.piezas_seleccionadas_indices)
        if selected_idx >= 0:
            return {selected_idx}
        return set()

    @property
    def piezas_seleccionadas(self):
        if not self.hoja_actual_data:
            return []
        indices = sorted(self.piezas_seleccionadas_indices)
        if not indices and self.idx_pieza_seleccionada >= 0:
            indices = [self.idx_pieza_seleccionada]
        piezas = []
        for idx in indices:
            if 0 <= idx < len(self.hoja_actual_data.get("piezas") or []):
                p = self.hoja_actual_data["piezas"][idx]
                if self._es_pieza_seleccionable(p.get("nombre", "")):
                    piezas.append(p)
        return piezas

    def limpiar_seleccion_piezas(self):
        self.piezas_seleccionadas_indices = set()
        self.idx_pieza_seleccionada = -1
        self.info_pieza_seleccionada = None

    def _ctrl_presionado(self, event):
        key = str(getattr(event, "key", "") or "").lower()
        if "control" in key or "ctrl" in key:
            return True
        gui = getattr(event, "guiEvent", None)
        if gui is not None:
            state = int(getattr(gui, "state", 0) or 0)
            return bool(state & 0x0004)
        return False

    def _indices_para_arrastre(self):
        if self.piezas_seleccionadas_indices:
            indices = []
            for idx in sorted(self.piezas_seleccionadas_indices):
                if idx < 0 or not self.hoja_actual_data:
                    continue
                piezas = self.hoja_actual_data.get("piezas") or []
                if idx >= len(piezas):
                    continue
                if self._es_pieza_seleccionable(piezas[idx].get("nombre", "")):
                    indices.append(idx)
            if indices:
                return indices
        if self.idx_pieza_seleccionada >= 0:
            return [self.idx_pieza_seleccionada]
        return []

    def _notificar_seleccion(self):
        piezas = self.piezas_seleccionadas
        self.info_pieza_seleccionada = piezas[-1] if piezas else None
        if piezas:
            self.idx_pieza_seleccionada = max(self.piezas_seleccionadas_indices or {0})
        else:
            self.idx_pieza_seleccionada = -1
        try:
            self.callback_seleccion(self.info_pieza_seleccionada)
        except TypeError:
            self.callback_seleccion()

    def dibujar_hoja_full(
        self,
        hoja,
        clave,
        selected_idx=-1,
        selected_indices=None,
        preserve_view=False,
        drag_preview=False,
    ):
        self.hoja_actual_data = hoja
        self.clave_actual = clave
        selected_set = self._indices_seleccion_activos(selected_idx, selected_indices)
        prev_xlim = self.ax_nest.get_xlim()
        prev_ylim = self.ax_nest.get_ylim()

        self.ax_nest.clear()     
        self.ax_nest.set_aspect('equal', adjustable='datalim')      

        w_mm, h_mm = hoja['placa_w'], hoja['placa_h']
        es_rtz_view = bool(hoja.get("es_retazo", False)) or w_mm < 920.0
        self.ax_nest.add_patch(Rectangle((0, 0), w_mm, h_mm, facecolor='#262626', edgecolor='black', linewidth=3))
        if hoja.get("poly_borde_retazo"):
            try:
                borde_rtz = hoja["poly_borde_retazo"]
                if borde_rtz and len(borde_rtz) >= 3:
                    self.ax_nest.add_patch(
                        MplPolygon(
                            borde_rtz,
                            closed=True,
                            facecolor="none",
                            edgecolor="#94A3B8",
                            linewidth=1.2,
                            linestyle="--",
                        )
                    )
            except Exception:
                pass

        rem_data = None
        resumen = {}
        dims_nom = {}  # caja envolvente real en pulgadas por nombre de pieza
        offset_comp_mm_hoja = float(hoja.get("plasma_offset_mm_manual", 0.0) or 0.0)
        for idx_pieza, p in enumerate(hoja.get("piezas", [])):
            nom = p.get("nombre", "DXF")
            
            es_remanente = nom.startswith("REMANENTE__")
            es_referencia = nom.startswith("REF__")
            es_guillotina = nom.startswith("RETAZO_GUILLOTINA__")
            es_tatuaje = nom.startswith("TATUAJE__")
            
            if not (es_remanente or es_referencia or es_guillotina or es_tatuaje):
                if nom not in resumen: resumen[nom] = {"id": len(resumen)+1, "qty": 0}
                resumen[nom]["qty"] += 1
                if p.get("poligonos"):
                    ext = p["poligonos"][0]
                    if ext and len(ext) >= 2:
                        xs = [t[0] for t in ext]
                        ys = [t[1] for t in ext]
                        dx_mm = max(xs) - min(xs)
                        dy_mm = max(ys) - min(ys)
                        L_in = max(dx_mm, dy_mm) / 25.4
                        W_in = min(dx_mm, dy_mm) / 25.4
                        actual = dims_nom.get(nom, {"L": 0.0, "W": 0.0, "plasma": False})
                        actual["L"] = max(float(actual["L"]), float(L_in))
                        actual["W"] = max(float(actual["W"]), float(W_in))
                        actual["plasma"] = bool(actual["plasma"]) or bool(p.get("plasma_compensada_manual"))
                        dims_nom[nom] = actual
            
            es_compensada = bool(p.get("plasma_compensada_manual"))
            for i, poli in enumerate(p.get("poligonos", [])):
                # En preview de navegación/drag, dibujar solo contorno exterior de piezas
                # normales para aligerar placas con miles de vértices/huecos.
                if drag_preview and i > 0 and not (es_remanente or es_referencia or es_guillotina or es_tatuaje):
                    continue
                if es_remanente:
                    face, edge, lw, ls = 'none', '#94A3B8', 1, '--'
                elif es_referencia: 
                    face, edge, lw, ls = "#BDB07E", "#000000", 1.0, '-'
                elif es_guillotina: 
                    face, edge, lw, ls = 'none', '#EF4444', 2, '-.'
                elif es_tatuaje:
                    face, edge, lw, ls = 'none', 'none', 0, '-'
                else:
                    face = (
                        '#3B82F6'
                        if idx_pieza in selected_set
                        else ('#CFD8DC' if i == 0 else '#0F172A')
                    )
                    edge = '#F97316' if es_compensada else '#37474F'
                    lw = 1.25 if es_compensada and i == 0 else 0.5
                    ls = '-'
                
                if not es_tatuaje:
                    self.ax_nest.add_patch(MplPolygon(poli, closed=True, facecolor=face, edgecolor=edge, linewidth=lw, linestyle=ls))

            # Banda proporcional real de compensación (offset): exterior y orificios.
            if (
                es_compensada
                and (not drag_preview)
                and (not es_remanente)
                and (not es_referencia)
                and (not es_guillotina)
                and (not es_tatuaje)
                and offset_comp_mm_hoja > 1e-6
            ):
                try:
                    poly_comp = self._poly_from_pieza(p)
                    if poly_comp is not None and (not poly_comp.is_empty):
                        poly_base_aprox = poly_comp.buffer(-offset_comp_mm_hoja, join_style=1, quad_segs=16)
                        if poly_base_aprox is not None and (not poly_base_aprox.is_empty):
                            self._dibujar_banda_compensacion(poly_comp, poly_base_aprox)
                except Exception:
                    pass
            
            # En modo preview (drag de pieza o navegación), omitir marcajes para fluidez.
            if not drag_preview:
                for linea in p.get("marcas", []):
                    color_marca = '#FACC15' if es_tatuaje else '#3B82F6'
                    self.ax_nest.plot([pt[0] for pt in linea], [pt[1] for pt in linea], color=color_marca, linestyle='-', linewidth=1.2)
            
            if p['poligonos']:
                v = p['poligonos'][0][:-1]
                cx, cy = sum(pt[0] for pt in v)/len(v), sum(pt[1] for pt in v)/len(v)
                
                if drag_preview:
                    continue
                if es_remanente:
                    minx_r, maxx_r = min(pt[0] for pt in v), max(pt[0] for pt in v)
                    miny_r, maxy_r = min(pt[1] for pt in v), max(pt[1] for pt in v)
                    rem_data = (minx_r, miny_r, maxx_r-minx_r, maxy_r-miny_r)
                    id_rem = nom.split("__")[1]
                    t_rem = self.ax_nest.text(
                        cx, cy, id_rem,
                        color="#0F172A", fontsize=8, fontweight="bold",
                        ha="center", va="center",
                        rotation=90 if rem_data[3] > rem_data[2] else 0,
                    )
                    t_rem.set_path_effects(_path_effects_numero_pieza(lw=2.4))
                elif not es_referencia and not es_guillotina and not es_tatuaje:
                    t_id = self.ax_nest.text(
                        cx, cy, str(resumen[nom]["id"]),
                        color="#0F172A", fontsize=9, fontweight="bold", ha="center", va="center",
                    )
                    t_id.set_path_effects(_path_effects_numero_pieza(lw=2.8))
        self._rebuild_manual_piece_index()

        if not drag_preview:
            # --- FUNCIÓN DE COTAS RESTAURADA ---
            def dibujar_cota(x1, y1, x2, y2, txt, ox=0, oy=0, color_linea='white', color_flecha='white', color_txt='white'):
                self.ax_nest.plot([x1, x1+ox*0.5], [y1, y1+oy*0.5], color=color_linea, lw=1.0, alpha=0.6)
                self.ax_nest.plot([x2, x2+ox*0.5], [y2, y2+oy*0.5], color=color_linea, lw=1.0, alpha=0.6)
                self.ax_nest.annotate('', xy=(x1+ox*0.35, y1+oy*0.35), xytext=(x2+ox*0.35, y2+oy*0.35), arrowprops=dict(arrowstyle='<->', color=color_flecha, shrinkA=0, shrinkB=0, lw=1.0, alpha=0.6))
                self.ax_nest.text((x1+x2)/2 + ox*0.85, (y1+y2)/2 + oy*0.85, txt, color=color_txt, fontsize=9, alpha=0.9, ha='center', va='center', rotation=(0 if y1 == y2 else 90))

            dibujar_cota(0, h_mm, w_mm, h_mm, f"Ancho: {w_mm/25.4:.1f}\"", oy=90)
            dibujar_cota(w_mm, 0, w_mm, h_mm, f"Largo: {h_mm/25.4:.1f}\"", ox=90)

            if rem_data:
                rx, ry, rw, rh = rem_data
                if rx > 0.1:
                    dibujar_cota(0, 0, rx, 0, f"Uso: {rx/25.4:.1f}\"", oy=-90)
                    dibujar_cota(rx, 0, w_mm, 0, f"Sob: {rw/25.4:.1f}\"", oy=-90)
                elif ry > 0.1:
                    dibujar_cota(0, 0, 0, ry, f"Uso: {ry/25.4:.1f}\"", ox=-90)
                    dibujar_cota(0, ry, 0, h_mm, f"Sob: {rh/25.4:.1f}\"", ox=-90)

            instruccion_lote = f" | {hoja['lote_desc']} (Cortar {hoja['lote_mult']} veces)" if 'lote_desc' in hoja else ""
            efi_dir = float(hoja.get("eficiencia_directa", hoja.get("eficiencia", 0)) or 0)
            efi_real = float(hoja.get("eficiencia_real", efi_dir) or 0)
            n_piezas = sum(
                1 for p in hoja['piezas']
                if not (
                    p['nombre'].startswith('REMANENTE')
                    or p['nombre'].startswith('REF__')
                    or p['nombre'].startswith('RETAZO_')
                    or p['nombre'].startswith('TATUAJE_')
                )
            )
            if hoja.get("es_retazo"):
                efi_dir_lbl = "Directa (piezas / rect. RTZ)"
                efi_real_lbl = "Real (piezas / area retazo)"
            else:
                efi_dir_lbl = "Directa (piezas en madre / placa)"
                efi_real_lbl = "Real (madre+RTZ / placa)"
            txt_info = (
                f"INFO DEL NESTING\n{'-'*20}\n"
                f"Nombre: {clave}{instruccion_lote}\n"
                f"Piezas en esta vista: {n_piezas}\n"
                f"{efi_dir_lbl}: {efi_dir:.1f}%\n"
                f"{efi_real_lbl}: {efi_real:.1f}%"
            )
            self.ax_nest.text(0.01, 1.1, txt_info, transform=self.ax_nest.transAxes, color='white', va='top', ha='left', fontsize=8, alpha=0.9)

        # Tabla en mm (transData): debajo de la placa; en RTZ / placas estrechas el ancho mínimo evita tabla ilegible.
        y_tbl_bottom = None
        tbl_x0 = 0.0
        tbl_w = 0.0
        if resumen and not drag_preview:
            job_raw = str(getattr(self.app, "job_activo", "") or "").strip() or "-"
            if len(job_raw) > 30:
                job_cell = job_raw[:27] + "…"
            else:
                job_cell = job_raw
            rows = []
            for nom, data in sorted(resumen.items(), key=lambda kv: kv[1]["id"]):
                dim = dims_nom.get(nom, {"L": 0.0, "W": 0.0, "plasma": False})
                L_in = float(dim.get("L", 0.0) or 0.0)
                W_in = float(dim.get("W", 0.0) or 0.0)
                es_plasma = bool(dim.get("plasma", False))
                item = nom if len(nom) <= 34 else (nom[:31] + "…")
                if es_plasma:
                    item = f"{item} (PLASMA)"
                rows.append(
                    [
                        f"({data['id']})",
                        job_cell,
                        item,
                        f"{L_in:.2f}",
                        f"{W_in:.2f}",
                        str(int(data["qty"])),
                    ]
                )
            nrows = len(rows)
            gap_mm = max(10.0, min(32.0, h_mm * 0.022))
            # Tabla base
            frac_tabla = min(0.28, max(0.14, 0.052 * float(nrows + 1)))
            tbl_h = h_mm * frac_tabla
            es_rtz = bool(hoja.get("es_retazo"))
            narrow_mm = 920.0
            min_tbl_w_mm = 1650.0
            if es_rtz or w_mm < narrow_mm:
                # Acuerdo RTZ: tamaño fijo para legibilidad, independiente del remanente.
                # Evita que remanentes chicos colapsen la tabla o que un x4 fuerce zoom extremo.
                fixed_tbl_w_mm = 1750.0
                fixed_tbl_h_mm = 240.0
                tbl_w = fixed_tbl_w_mm
                tbl_h = max(tbl_h, fixed_tbl_h_mm)
            else:
                tbl_w = w_mm * 0.90
            tbl_x0 = (w_mm - tbl_w) * 0.5
            y_tbl_top = -gap_mm
            y_tbl_bottom = y_tbl_top - tbl_h

            col_labels = ["ID", "JOB", "ITEM", "L (in)", "W (in)", "Cant."]
            col_widths = [0.07, 0.19, 0.34, 0.12, 0.12, 0.16]
            tbl = self.ax_nest.table(
                cellText=rows,
                colLabels=col_labels,
                colWidths=col_widths,
                cellLoc="center",
                loc="lower left",
                bbox=(tbl_x0, y_tbl_bottom, tbl_w, tbl_h),
                transform=self.ax_nest.transData,
            )
            tbl.set_transform(self.ax_nest.transData)
            tbl.auto_set_font_size(False)
            fs = max(7.0, min(12.0, 6.0 + h_mm / 850.0 + 0.35 * float(nrows)))
            tbl.set_fontsize(fs)
            for (r, c), cell in tbl.get_celld().items():
                cell.set_transform(self.ax_nest.transData)
                txt = cell.get_text()
                cell.set_edgecolor("#64748B")
                cell.set_linewidth(max(0.35, min(1.2, h_mm / 1800.0)))
                if r == 0:
                    cell.set_facecolor("#94A3B8")
                    txt.set_color("#0F172A")
                    txt.set_fontweight("bold")
                else:
                    cell.set_facecolor("#F8FAFC" if r % 2 else "#E2E8F0")
                    txt.set_color("#0F172A")
                    if c in (1, 2):
                        txt.set_ha("left")
                if c in (3, 4, 5) and r > 0:
                    txt.set_ha("center")

        padding_x, padding_y = max(150, w_mm * 0.1), max(150, h_mm * 0.1)
        if preserve_view:
            self.ax_nest.set_xlim(prev_xlim)
            self.ax_nest.set_ylim(prev_ylim)
        else:
            x_lo, x_hi = -padding_x, w_mm + padding_x
            if y_tbl_bottom is not None and tbl_w > w_mm - 1:
                spill = (tbl_w - w_mm) * 0.5 + 55.0
                if bool(hoja.get("es_retazo", False)) or w_mm < 920.0:
                    spill += 130.0
                x_lo = min(x_lo, -spill)
                x_hi = max(x_hi, w_mm + spill)
            if y_tbl_bottom is not None:
                ymin = min(-padding_y - 220, y_tbl_bottom - max(40.0, h_mm * 0.04))
            else:
                ymin = -padding_y - 200

            # En mini-nest / RTZ, encuadrar explícitamente la unión placa + tabla para que ambas
            # queden visibles y centradas en pantalla (sin depender solo del spill horizontal).
            if es_rtz_view and y_tbl_bottom is not None:
                c_x0 = min(0.0, tbl_x0) - 40.0
                c_x1 = max(w_mm, tbl_x0 + tbl_w) + 40.0
                c_y0 = min(0.0, y_tbl_bottom) - 28.0
                c_y1 = max(h_mm, 0.0) + 48.0

                c_w = max(1.0, c_x1 - c_x0)
                c_h = max(1.0, c_y1 - c_y0)
                cx = 0.5 * (c_x0 + c_x1)
                cy = 0.5 * (c_y0 + c_y1)

                fig_w_px, fig_h_px = self.canvas_widget.winfo_width(), self.canvas_widget.winfo_height()
                if fig_w_px <= 1 or fig_h_px <= 1:
                    fw_in, fh_in = self.fig_nest.get_size_inches()
                    fig_w_px, fig_h_px = max(1.0, fw_in), max(1.0, fh_in)
                target_ratio = float(fig_w_px) / float(fig_h_px)
                content_ratio = c_w / c_h

                if content_ratio < target_ratio:
                    c_w = c_h * target_ratio
                else:
                    c_h = c_w / target_ratio

                # Margen final visual para que no quede "pegado" al borde.
                c_w *= 1.05
                c_h *= 1.08

                x_lo = cx - 0.5 * c_w
                x_hi = cx + 0.5 * c_w
                ymin = cy - 0.5 * c_h
                ytop = cy + 0.5 * c_h
                self.ax_nest.set_xlim(x_lo, x_hi)
                self.ax_nest.set_ylim(ymin, ytop)
            else:
                self.ax_nest.set_xlim(x_lo, x_hi)
                self.ax_nest.set_ylim(ymin, h_mm + padding_y)
        self.ax_nest.axis('off')
        for spine in self.ax_nest.spines.values(): spine.set_visible(False)
            
        self.ax_nest.set_frame_on(False)
        self.ax_nest.set_position([0, 0, 1, 1]) 
        self.canvas_nest.draw()

    def manejar_teclado(self, event):
        if self.idx_pieza_seleccionada == -1 or not self.hoja_actual_data:
            return

        gui = getattr(event, "guiEvent", None)
        mods = int(gui.state) if gui is not None else 0

        paso_deg = 1.0
        if mods & 0x0001:
            paso_deg = 5.0
        elif mods & 0x0004:
            paso_deg = 0.5

        if event.key in ("left", "right"):
            g = -paso_deg if event.key == "left" else paso_deg
            self.rotar_pieza_seleccionada(g)
            return
        if event.key in ("up", "down"):
            g = paso_deg * 5.0 if event.key == "up" else -paso_deg * 5.0
            self.rotar_pieza_seleccionada(g)
            return
        if event.key == "r" or event.key == "R":
            self.rotar_pieza_seleccionada(90)
            return
            
    def rotar_pieza_seleccionada(self, grados):
        if self.idx_pieza_seleccionada == -1 or not self.hoja_actual_data:
            return
            
        hoja = self.hoja_actual_data
        idx = self.idx_pieza_seleccionada
        pieza_data = hoja['piezas'][idx]
        w_placa, h_placa = hoja['placa_w'], hoja['placa_h']
        
        poly_actual = self._poly_from_pieza(pieza_data)
        centro = poly_actual.centroid
        
        from shapely.affinity import rotate
        poly_nuevo = rotate(poly_actual, grados, origin=centro)

        distancia_seguridad = self._clearance_mm(hoja)
        caja_util = box(
            distancia_seguridad,
            distancia_seguridad,
            w_placa - distancia_seguridad,
            h_placa - distancia_seguridad,
        )
        if not caja_util.contains(poly_nuevo):
            return

        for i in self._candidate_indices_for_poly(poly_nuevo, distancia_seguridad, exclude_idx=idx):
            p_otra = hoja['piezas'][i]
            poly_otro = self._poly_from_pieza(p_otra)
            if poly_nuevo.distance(poly_otro) < distancia_seguridad:
                return 

        hoja['piezas'][idx]['poligonos'] = [list(poly_nuevo.exterior.coords)] + [list(hole.coords) for hole in poly_nuevo.interiors]
        hoja['piezas'][idx]['_poly_cache'] = poly_nuevo
        hoja['piezas'][idx]['_bounds_cache'] = poly_nuevo.bounds
        self._manual_piece_bounds[idx] = poly_nuevo.bounds

        if 'marcas' in pieza_data and pieza_data['marcas']:
            hoja['piezas'][idx]['marcas'] = [list(rotate(LineString(m), grados, origin=centro).coords) for m in pieza_data['marcas']]

        self.dibujar_hoja_full(
            hoja,
            self.clave_actual,
            selected_indices=self.piezas_seleccionadas_indices,
            preserve_view=True,
        )
        self._notificar_cambio_manual()

    def mover_pieza_seleccionada(self, dx, dy):
        hoja = self.hoja_actual_data
        indices = self._indices_para_arrastre()
        if not indices or not hoja:
            return

        w_placa, h_placa = hoja['placa_w'], hoja['placa_h']
        distancia_seguridad = self._clearance_mm(hoja)
        caja_util = box(
            distancia_seguridad,
            distancia_seguridad,
            w_placa - distancia_seguridad,
            h_placa - distancia_seguridad,
        )
        indices_set = set(indices)
        polys_nuevos = {}

        for idx in indices:
            pieza_data = hoja['piezas'][idx]
            poly_actual = self._poly_from_pieza(pieza_data)
            poly_nuevo = translate(poly_actual, xoff=dx, yoff=dy)
            if not caja_util.contains(poly_nuevo):
                return
            polys_nuevos[idx] = poly_nuevo

        for idx, poly_nuevo in polys_nuevos.items():
            for i, p_otra in enumerate(hoja['piezas']):
                if i in indices_set or p_otra['nombre'].startswith("REMANENTE__"):
                    continue
                poly_otro = self._poly_from_pieza(p_otra)
                b1 = poly_nuevo.bounds
                b2 = poly_otro.bounds
                if (
                    b1[2] + distancia_seguridad < b2[0]
                    or b2[2] + distancia_seguridad < b1[0]
                    or b1[3] + distancia_seguridad < b2[1]
                    or b2[3] + distancia_seguridad < b1[1]
                ):
                    continue
                if poly_nuevo.distance(poly_otro) < distancia_seguridad:
                    return

        for idx, poly_nuevo in polys_nuevos.items():
            nuevas_coords = [list(poly_nuevo.exterior.coords)] + [
                list(hole.coords) for hole in poly_nuevo.interiors
            ]
            hoja['piezas'][idx]['poligonos'] = nuevas_coords
            hoja['piezas'][idx]['_poly_cache'] = poly_nuevo
            hoja['piezas'][idx]['_bounds_cache'] = poly_nuevo.bounds
            self._manual_piece_bounds[idx] = poly_nuevo.bounds

        self._drag_total_dx += float(dx)
        self._drag_total_dy += float(dy)

        self._drag_dirty_view = True
        self._render_drag_if_due(force=False)

    def on_scroll(self, event):
        s = 1.2 if event.button == 'down' else 1 / 1.2
        if event.xdata is None:
            return
        l = self.ax_nest.get_xlim(), self.ax_nest.get_ylim()
        self.ax_nest.set_xlim([event.xdata - (event.xdata - l[0][0]) * s, event.xdata + (l[0][1] - event.xdata) * s])
        self.ax_nest.set_ylim([event.ydata - (event.ydata - l[1][0]) * s, event.ydata + (l[1][1] - event.ydata) * s])
        self._render_nav_preview_if_due(force=True)

    def _es_pieza_manual(self, nombre):
        n = str(nombre or "")
        return not (
            n.startswith("REMANENTE__")
            or n.startswith("REF__")
            or n.startswith("RETAZO_")
            or n.startswith("TATUAJE__")
        )

    def _pieza_en_punto(self, x, y):
        if x is None or y is None or not self.hoja_actual_data:
            return -1
        pt = Point(x, y)
        for idx in self._candidate_indices_for_point(x, y):
            p = self.hoja_actual_data["piezas"][idx]
            if self._poly_from_pieza(p).contains(pt):
                return idx
        return -1

    def _normalizar_anillo_para_poly(self, ring):
        if not ring:
            return []
        out = []
        for pt in ring:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                out.append((float(pt[0]), float(pt[1])))
            elif isinstance(pt, dict):
                x = pt.get("x", pt.get("X"))
                y = pt.get("y", pt.get("Y"))
                if x is not None and y is not None:
                    out.append((float(x), float(y)))
        return out

    def _poly_from_pieza(self, pieza):
        poly = pieza.get("_poly_cache")
        if isinstance(poly, Polygon):
            return poly
        if isinstance(poly, str) and poly.strip():
            try:
                geom = shapely_wkt.loads(poly)
                if isinstance(geom, Polygon) and not geom.is_empty:
                    if not geom.is_valid:
                        geom = geom.buffer(0)
                    pieza["_poly_cache"] = geom
                    pieza["_bounds_cache"] = geom.bounds
                    return geom
            except Exception:
                pass
        pieza.pop("_poly_cache", None)
        pieza.pop("_bounds_cache", None)

        pols = pieza.get("poligonos") or []
        if not pols:
            empty = Polygon()
            return empty
        outer = self._normalizar_anillo_para_poly(pols[0])
        holes = [self._normalizar_anillo_para_poly(h) for h in pols[1:]]
        holes = [h for h in holes if len(h) >= 3]
        if len(outer) < 3:
            return Polygon()
        poly = Polygon(outer, holes)
        if not poly.is_valid:
            poly = poly.buffer(0)
        pieza["_poly_cache"] = poly
        pieza["_bounds_cache"] = poly.bounds
        return poly

    def _rebuild_manual_piece_index(self):
        self._manual_piece_indices = []
        self._manual_piece_bounds = {}
        if not self.hoja_actual_data:
            return
        for idx, p in enumerate(self.hoja_actual_data.get("piezas", [])):
            if not self._es_pieza_manual(p.get("nombre")):
                continue
            poly = self._poly_from_pieza(p)
            self._manual_piece_indices.append(idx)
            self._manual_piece_bounds[idx] = p.get("_bounds_cache") or poly.bounds

    def _candidate_indices_for_point(self, x, y):
        out = []
        for idx in self._manual_piece_indices:
            b = self._manual_piece_bounds.get(idx)
            if not b:
                continue
            if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
                out.append(idx)
        return out

    def _candidate_indices_for_poly(self, poly_nuevo, clearance, exclude_idx=-1):
        b = poly_nuevo.bounds
        x0 = b[0] - clearance
        y0 = b[1] - clearance
        x1 = b[2] + clearance
        y1 = b[3] + clearance
        out = []
        for idx in self._manual_piece_indices:
            if idx == exclude_idx:
                continue
            b2 = self._manual_piece_bounds.get(idx)
            if not b2:
                continue
            if b2[2] < x0 or b2[0] > x1 or b2[3] < y0 or b2[1] > y1:
                continue
            out.append(idx)
        return out

    def _clearance_mm(self, hoja):
        # Unifica criterio de separación manual con el kerf real del nesting.
        return float(hoja.get("kerf_usado", 0.2) or 0.2) * 25.4

    def _set_canvas_cursor(self, mode):
        if mode == self._cursor_mode:
            return
        self._cursor_mode = mode
        cursor = ""
        if mode == "hover":
            cursor = "hand2"
        elif mode == "dragging":
            cursor = "fleur"
        elif mode == "panning":
            cursor = "fleur"
        try:
            self.canvas_widget.configure(cursor=cursor)
        except Exception:
            pass

    def _render_drag_if_due(self, force=False):
        if not self._drag_dirty_view:
            return
        now = time.perf_counter()
        if not force and (now - self._drag_last_render_ts) < self._drag_render_interval_s:
            return
        self._drag_last_render_ts = now
        self._drag_dirty_view = False
        self.dibujar_hoja_full(
            self.hoja_actual_data,
            self.clave_actual,
            selected_indices=self.piezas_seleccionadas_indices,
            preserve_view=True,
            drag_preview=True,
        )

    def _render_nav_preview_if_due(self, force=False):
        if not self.hoja_actual_data:
            return
        now = time.perf_counter()
        # Permite primer frame ligero inmediato al iniciar navegación.
        if (
            not force
            and self._nav_preview_active
            and (now - self._nav_last_render_ts) < self._nav_render_interval_s
        ):
            return
        self._nav_last_render_ts = now
        self._nav_preview_active = True
        xlim = self.ax_nest.get_xlim()
        ylim = self.ax_nest.get_ylim()
        self.dibujar_hoja_full(
            self.hoja_actual_data,
            self.clave_actual,
            selected_indices=self.piezas_seleccionadas_indices,
            preserve_view=True,
            drag_preview=True,
        )
        self.ax_nest.set_xlim(xlim)
        self.ax_nest.set_ylim(ylim)
        self.canvas_nest.draw_idle()

    def _dibujar_banda_compensacion(self, poly_comp, poly_base_aprox):
        if poly_comp is None or getattr(poly_comp, "is_empty", True):
            return
        geoms_comp = [poly_comp]
        if hasattr(poly_comp, "geoms"):
            try:
                geoms_comp = list(poly_comp.geoms)
            except Exception:
                geoms_comp = [poly_comp]
        for g in geoms_comp:
            if not isinstance(g, Polygon) or g.is_empty:
                continue
            ext = list(g.exterior.coords)
            if len(ext) >= 3:
                self.ax_nest.add_patch(
                    MplPolygon(
                        ext,
                        closed=True,
                        facecolor='none',
                        edgecolor=(1.0, 0.30, 0.20, 0.95),
                        linewidth=1.0,
                        linestyle='-',
                        zorder=6,
                    )
                )
            for hole in g.interiors:
                ring = list(hole.coords)
                if len(ring) >= 3:
                    self.ax_nest.add_patch(
                        MplPolygon(
                            ring,
                            closed=True,
                            facecolor='none',
                            edgecolor=(1.0, 0.30, 0.20, 0.95),
                            linewidth=1.0,
                            linestyle='-',
                            zorder=6,
                        )
                    )

        # Contorno aproximado de la geometría base (antes de compensar), para visualizar la banda.
        if poly_base_aprox is None or getattr(poly_base_aprox, "is_empty", True):
            return
        geoms_base = [poly_base_aprox]
        if hasattr(poly_base_aprox, "geoms"):
            try:
                geoms_base = list(poly_base_aprox.geoms)
            except Exception:
                geoms_base = [poly_base_aprox]
        for g in geoms_base:
            if not isinstance(g, Polygon) or g.is_empty:
                continue
            ext = list(g.exterior.coords)
            if len(ext) >= 3:
                self.ax_nest.add_patch(
                    MplPolygon(
                        ext,
                        closed=True,
                        facecolor='none',
                        edgecolor=(1.0, 0.45, 0.30, 0.85),
                        linewidth=0.8,
                        linestyle='--',
                        zorder=6,
                    )
                )
            for hole in g.interiors:
                ring = list(hole.coords)
                if len(ring) >= 3:
                    self.ax_nest.add_patch(
                        MplPolygon(
                            ring,
                            closed=True,
                            facecolor='none',
                            edgecolor=(1.0, 0.45, 0.30, 0.85),
                            linewidth=0.8,
                            linestyle='--',
                            zorder=6,
                        )
                    )
        self._schedule_full_after_navigation()

    def _schedule_full_after_navigation(self):
        if self._nav_restore_after_id is not None:
            try:
                self.after_cancel(self._nav_restore_after_id)
            except Exception:
                pass
            self._nav_restore_after_id = None
        self._nav_restore_after_id = self.after(
            self._nav_restore_delay_ms,
            self._render_full_after_navigation,
        )

    def _render_full_after_navigation(self):
        self._nav_restore_after_id = None
        if not self._nav_preview_active or not self.hoja_actual_data:
            return
        self._nav_preview_active = False
        xlim = self.ax_nest.get_xlim()
        ylim = self.ax_nest.get_ylim()
        self.dibujar_hoja_full(
            self.hoja_actual_data,
            self.clave_actual,
            selected_indices=self.piezas_seleccionadas_indices,
            preserve_view=True,
            drag_preview=False,
        )
        self.ax_nest.set_xlim(xlim)
        self.ax_nest.set_ylim(ylim)
        self.canvas_nest.draw_idle()

    def _notificar_cambio_manual(self):
        try:
            vista = getattr(self.app, "vista_nesting", None)
            if vista and hasattr(vista, "_replicar_lote_activo_a_gemelos"):
                vista._replicar_lote_activo_a_gemelos()
        except Exception:
            pass

    def on_press(self, event):
        self.canvas_widget.focus_set()
        if event.button == 3:
            idx = self._pieza_en_punto(event.xdata, event.ydata)
            if idx >= 0:
                self.idx_pieza_seleccionada = idx
                self.info_pieza_seleccionada = self.hoja_actual_data["piezas"][idx]
                self.callback_seleccion(self.info_pieza_seleccionada)
                self.rotar_pieza_seleccionada(90)
            return
        if event.button == 2:
            self._is_panning, self._pan_start, self._lims = (
                True,
                (event.x, event.y),
                (self.ax_nest.get_xlim(), self.ax_nest.get_ylim()),
            )
            self._render_nav_preview_if_due(force=True)
            self._set_canvas_cursor("panning")
            return

        if event.button == 1:
            self._btn1_down = True
            self._dragging_piece = False
            self._drag_last_data = None
            self._is_panning, self._pan_start, self._lims = (
                True,
                (event.x, event.y),
                (self.ax_nest.get_xlim(), self.ax_nest.get_ylim()),
            )
            if event.xdata is None or event.ydata is None or not self.hoja_actual_data:
                return

            pieza_tocada = self._pieza_en_punto(event.xdata, event.ydata)
            ctrl = self._ctrl_presionado(event)

            if pieza_tocada >= 0 and ctrl:
                nombre = self.hoja_actual_data["piezas"][pieza_tocada].get("nombre", "")
                if self._es_pieza_seleccionable(nombre):
                    if pieza_tocada in self.piezas_seleccionadas_indices:
                        self.piezas_seleccionadas_indices.discard(pieza_tocada)
                    else:
                        self.piezas_seleccionadas_indices.add(pieza_tocada)
                self._is_panning = False
                self._dragging_piece = False
                self._notificar_seleccion()
                self.dibujar_hoja_full(
                    self.hoja_actual_data,
                    self.clave_actual,
                    selected_indices=self.piezas_seleccionadas_indices,
                    preserve_view=True,
                )
                return

            if pieza_tocada >= 0:
                nombre = self.hoja_actual_data["piezas"][pieza_tocada].get("nombre", "")
                if self._es_pieza_seleccionable(nombre):
                    en_grupo = (
                        pieza_tocada in self.piezas_seleccionadas_indices
                        and len(self.piezas_seleccionadas_indices) > 1
                    )
                    if not en_grupo:
                        self.piezas_seleccionadas_indices = {pieza_tocada}
                else:
                    self.piezas_seleccionadas_indices = set()
                self.idx_pieza_seleccionada = pieza_tocada
                self._is_panning = False
                self._dragging_piece = True
                self._drag_last_data = (event.xdata, event.ydata)
                self._drag_total_dx = 0.0
                self._drag_total_dy = 0.0
                self._drag_marks_base = {}
                for idx_drag in self._indices_para_arrastre():
                    marcas0 = self.hoja_actual_data["piezas"][idx_drag].get("marcas", [])
                    self._drag_marks_base[idx_drag] = copy.deepcopy(marcas0) if marcas0 else []
                self._set_canvas_cursor("dragging")
            else:
                self.limpiar_seleccion_piezas()
                self._render_nav_preview_if_due(force=True)
                self._set_canvas_cursor("normal")

            self._notificar_seleccion()
            self.dibujar_hoja_full(
                self.hoja_actual_data,
                self.clave_actual,
                selected_indices=self.piezas_seleccionadas_indices,
                preserve_view=True,
            )

    def on_release(self, event):
        if event.button in (1, 2):
            self._btn1_down = False
            self._dragging_piece = False
            self._drag_last_data = None
            if (
                event.button == 1
                and self.idx_pieza_seleccionada >= 0
                and self.hoja_actual_data
                and isinstance(self._drag_marks_base, dict)
            ):
                try:
                    for idx_drag, marcas_base in self._drag_marks_base.items():
                        if marcas_base:
                            self.hoja_actual_data["piezas"][idx_drag]["marcas"] = [
                                list(
                                    translate(
                                        LineString(m),
                                        xoff=self._drag_total_dx,
                                        yoff=self._drag_total_dy,
                                    ).coords
                                )
                                for m in marcas_base
                            ]
                except Exception:
                    pass
            self._drag_total_dx = 0.0
            self._drag_total_dy = 0.0
            self._drag_marks_base = None
            self._render_drag_if_due(force=True)
            if self.hoja_actual_data:
                self.dibujar_hoja_full(
                    self.hoja_actual_data,
                    self.clave_actual,
                    selected_indices=self.piezas_seleccionadas_indices,
                    preserve_view=True,
                    drag_preview=False,
                )
            self._notificar_cambio_manual()
        self._is_panning = False
        if self._nav_restore_after_id is not None:
            try:
                self.after_cancel(self._nav_restore_after_id)
            except Exception:
                pass
            self._nav_restore_after_id = None
        self._render_full_after_navigation()
        self._set_canvas_cursor("normal")

    def on_motion(self, event):
        if event.xdata is not None and event.ydata is not None:
            in_x = event.xdata / 25.4
            in_y = event.ydata / 25.4
            self.coord_text.set_text(f"X: {in_x:.3f}\" ({event.xdata:.1f} mm) | Y: {in_y:.3f}\" ({event.ydata:.1f} mm)")
        else:
            self.coord_text.set_text("")

        if (
            self._dragging_piece
            and self._btn1_down
            and self.idx_pieza_seleccionada >= 0
            and event.xdata is not None
            and event.ydata is not None
            and self._drag_last_data is not None
        ):
            dx = event.xdata - self._drag_last_data[0]
            dy = event.ydata - self._drag_last_data[1]
            self._drag_last_data = (event.xdata, event.ydata)
            if dx != 0 or dy != 0:
                self.mover_pieza_seleccionada(dx, dy)
            self._set_canvas_cursor("dragging")
        elif self._is_panning:
            dx = (event.x - self._pan_start[0]) * (self._lims[0][1] - self._lims[0][0]) / self.canvas_widget.winfo_width()
            dy = (event.y - self._pan_start[1]) * (self._lims[1][1] - self._lims[1][0]) / self.canvas_widget.winfo_height()
            self.ax_nest.set_xlim(self._lims[0][0] - dx, self._lims[0][1] - dx)
            self.ax_nest.set_ylim(self._lims[1][0] - dy, self._lims[1][1] - dy)
            self._render_nav_preview_if_due(force=False)
            self._set_canvas_cursor("panning")
        elif event.xdata is not None and event.ydata is not None and self.hoja_actual_data:
            idx_hover = self._pieza_en_punto(event.xdata, event.ydata)
            if idx_hover != self._hover_idx:
                self._hover_idx = idx_hover
                self._set_canvas_cursor("hover" if idx_hover >= 0 else "normal")
        else:
            self._hover_idx = -1
            self._set_canvas_cursor("normal")

        if not self._dragging_piece and not self._is_panning:
            self.canvas_nest.draw_idle()
            
    def on_leave(self, event):
        self.coord_text.set_text("")
        self._hover_idx = -1
        if self._nav_restore_after_id is not None:
            try:
                self.after_cancel(self._nav_restore_after_id)
            except Exception:
                pass
            self._nav_restore_after_id = None
        self._render_full_after_navigation()
        self._set_canvas_cursor("normal")
        self.canvas_nest.draw_idle()