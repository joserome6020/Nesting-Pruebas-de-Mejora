import matplotlib

matplotlib.use("QtAgg")

import matplotlib.pyplot as plt

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from matplotlib.backends.backend_agg import FigureCanvasAgg

from matplotlib.figure import Figure

from matplotlib.patches import PathPatch, Rectangle, Circle, FancyArrowPatch, Arc

from matplotlib.path import Path as MPLPath

import ezdxf

from ezdxf import path

import config

import math

import numpy as np

import io

from PIL import Image

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QImage, QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from interface.qt.mpl_utils import bind_figure_resize
from interface.qt.theme import apply_push_button, COLOR_GRIS_DARK

from modules.plasma_compensator import _arc_points_from_bulge

# Paleta alineada con interface/qt/nesting_graphics.py
CAD_VIEW_BG = "#0B1220"
CAD_PIECE_FILL = "#DDE4EC"
CAD_PIECE_EDGE = "#475569"
CAD_HOLE_FILL = "#0B1220"
CAD_MARK = "#0047AB"


class VisorDXF:

    def __init__(self, master_frame):

        master_lay = master_frame.layout()
        if master_lay is None:
            master_lay = QVBoxLayout(master_frame)
            master_lay.setContentsMargins(0, 0, 0, 0)

        self.frame_seccion_2 = QWidget()
        master_lay.addWidget(self.frame_seccion_2, 1)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#334155;border:none;")
        master_lay.addWidget(sep)

        self.frame_seccion_3 = QFrame()
        self.frame_seccion_3.setObjectName("VisorInfoPanel")
        self.frame_seccion_3.setFixedHeight(76)
        master_lay.addWidget(self.frame_seccion_3)

        sec2_lay = QVBoxLayout(self.frame_seccion_2)
        sec2_lay.setContentsMargins(0, 0, 0, 0)
        sec2_lay.setSpacing(0)

        toolbar = QFrame()
        toolbar.setStyleSheet("background:#0F172A;border-bottom:1px solid #334155;")
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(10, 6, 10, 6)
        tb_lay.setSpacing(8)

        btn_fit = QPushButton("AJUSTAR VISTA")
        btn_fit.setFixedHeight(28)
        apply_push_button(btn_fit, COLOR_GRIS_DARK, font_size=10, padding="4px 12px")
        btn_fit.clicked.connect(self.ajustar_vista)
        tb_lay.addWidget(btn_fit)

        btn_rot = QPushButton("ROTAR 90°")
        btn_rot.setFixedHeight(28)
        apply_push_button(btn_rot, "#334155", font_size=10, padding="4px 12px")
        btn_rot.clicked.connect(self.rotar_vista_90)
        tb_lay.addWidget(btn_rot)

        lbl_hint = QLabel(
            "CLIC: COTA  ·  RUEDA: ZOOM  ·  CENTRAL: PAN  ·  DER: ROTAR  ·  ESC: CANCELAR"
        )
        lbl_hint.setStyleSheet("color:#64748B;font-size:10px;background:transparent;")
        lbl_hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tb_lay.addStretch()
        tb_lay.addWidget(lbl_hint, 1)

        sec2_lay.addWidget(toolbar)

        self.figure = Figure(figsize=(4, 4), dpi=100)

        self.figure.patch.set_facecolor(CAD_VIEW_BG)

        self.ax = self.figure.add_subplot(111)

        self.figure.subplots_adjust(left=0.02, right=0.98, top=0.98, bottom=0.02)

        self.canvas = FigureCanvasQTAgg(self.figure)

        self.widget = self.canvas

        sec2_lay.addWidget(self.canvas, 1)

        try:
            bind_figure_resize(
                self.widget,
                self.figure,
                on_resize=lambda: self.canvas.draw_idle(),
            )
        except Exception:
            pass



        self.vertices_cache = []

        self.linea_medida = None; self.texto_medida = None; self.marker_snap = None
        self._overlay_snap_artists = []
        self._cota_preview_artists = []
        self._circulos_snap = []
        self._geom_segmentos = []
        self._arcos_pick = []
        self._centro_pieza_vista = (0.0, 0.0)
        self._dim_estado = "idle"
        self._dim = {}
        self._is_panning = False
        self._pan_start = (0, 0)
        self._lims = None
        self._cursor_mode = "normal"
        self._ruta_actual = None
        self._rotacion_vista_deg = 0
        self._persist_rotation_hook = None
        self._material = ""
        self._render_all_layers = False
        self._fit_xlim = None
        self._fit_ylim = None
        
        self.factor_conversion = 25.4

        

        self.canvas.mpl_connect('motion_notify_event', self.on_mouse_move)

        self.canvas.mpl_connect('button_press_event', self.on_click)
        self.canvas.mpl_connect('button_release_event', self.on_release)
        self.canvas.mpl_connect('scroll_event', self.on_scroll)

        

        self.construir_tabla_3_columnas()

        self.canvas.mpl_connect("key_press_event", self._on_key_press_visualizer)

        self.mostrar_patron_prueba()

    def _on_key_press_visualizer(self, event):
        if str(getattr(event, "key", "") or "").lower() in ("escape", "esc"):
            self._cancelar_cota_interactiva(event)

    @staticmethod
    def _poly_area_2d(pts):
        if not pts or len(pts) < 3:
            return 0.0
        area = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            area += (x1 * y2) - (x2 * y1)
        return abs(area) * 0.5

    @staticmethod
    def _rotar_punto(x, y, cx, cy, deg):
        rad = math.radians(float(deg))
        dx = x - cx
        dy = y - cy
        xr = (dx * math.cos(rad)) - (dy * math.sin(rad))
        yr = (dx * math.sin(rad)) + (dy * math.cos(rad))
        return (cx + xr, cy + yr)

    @staticmethod
    def _dxf_arc_ccw_sweep_rad(start_deg, end_deg):
        """Arco DXF CCW: barrido en radianes desde start_deg (origen del arco)."""
        sa = math.radians(float(start_deg))
        span_deg = (float(end_deg) - float(start_deg)) % 360.0
        if span_deg < 1e-12:
            span_deg = 360.0
        return sa, math.radians(span_deg)

    def _capa_relevante_visual(self, layer):
        if getattr(self, "_render_all_layers", False):
            return True
        u = layer.upper()
        if "CUT" in u or "IV_OUTER_PROFILE" in u or "IV_INTERIOR_PROFILES" in u:
            return True
        return any(m in u for m in ("MARK", "ETCH", "IV_MARK"))

    @staticmethod
    def _es_mark_layer(layer_upper):
        u = str(layer_upper or "").upper()
        return any(m in u for m in ("MARK", "ETCH", "IV_MARK"))

    @staticmethod
    def _es_outer_layer(layer_upper):
        u = str(layer_upper or "").upper()
        return ("CUT_OUTER" in u) or ("IV_OUTER_PROFILE" in u)

    @staticmethod
    def _es_inner_layer(layer_upper):
        u = str(layer_upper or "").upper()
        return ("CUT_INNER" in u) or ("IV_INTERIOR_PROFILES" in u)

    @classmethod
    def _es_cut_layer(cls, layer_upper):
        u = str(layer_upper or "").upper()
        return ("CUT" in u) or cls._es_outer_layer(u) or cls._es_inner_layer(u)

    def _clear_cota_preview(self):
        for a in self._cota_preview_artists:
            if isinstance(a, (list, tuple)):
                for x in a:
                    self._safe_remove(x)
            else:
                self._safe_remove(a)
        self._cota_preview_artists = []

    def _cancelar_cota_interactiva(self, event=None):
        self._clear_cota_preview()
        self._limpiar_overlays_snap()
        self._dim_estado = "idle"
        self._dim = {}
        self.canvas.draw_idle()
        return "break"

    @staticmethod
    def _dist_punto_segmento(px, py, x1, y1, x2, y2):
        vx, vy = x2 - x1, y2 - y1
        L2 = vx * vx + vy * vy
        if L2 < 1e-18:
            return math.hypot(px - x1, py - y1), (x1, y1)
        t = max(0.0, min(1.0, ((px - x1) * vx + (py - y1) * vy) / L2))
        qx = x1 + t * vx
        qy = y1 + t * vy
        return math.hypot(px - qx, py - qy), (qx, qy)

    def _ajuste_circulo_desde_puntos(self, pts):
        """Ajuste algebraico de circunferencia; devuelve (cx, cy, r, err_max) o None."""
        if len(pts) < 4:
            return None
        arr = np.asarray(pts, dtype=float)
        x, y = arr[:, 0], arr[:, 1]
        x_m, y_m = float(np.mean(x)), float(np.mean(y))
        u, v = x - x_m, y - y_m
        Suu = float(np.sum(u * u))
        Svv = float(np.sum(v * v))
        Suv = float(np.sum(u * v))
        if abs(Suu * Svv - Suv * Suv) < 1e-22:
            return None
        Suuu = float(np.sum(u**3))
        Svvv = float(np.sum(v**3))
        Suvv = float(np.sum(u * v * v))
        Svuu = float(np.sum(v * u * u))
        A = np.array([[Suu, Suv], [Suv, Svv]])
        b = 0.5 * np.array([Suuu + Suvv, Svvv + Svuu])
        try:
            uc, vc = np.linalg.solve(A, b)
        except np.linalg.LinAlgError:
            return None
        cx, cy = uc + x_m, vc + y_m
        npt = len(x)
        r_sq = uc * uc + vc * vc + (Suu + Svv) / max(npt, 1)
        if r_sq <= 1e-18:
            return None
        r = float(math.sqrt(r_sq))
        dists = np.hypot(x - cx, y - cy)
        err_max = float(np.max(np.abs(dists - r)))
        return cx, cy, r, err_max

    def _circulos_snap_agregar_si_circular(self, pts, layer_upper, span_ref):
        """Polilinea cerrada casi circular → mismo snap Ø que entidad CIRCLE (outer/inner/mark)."""
        if len(pts) < 6:
            return
        lu = layer_upper.upper()
        if not self._es_cut_layer(lu) and not self._es_mark_layer(lu):
            return
        fit = self._ajuste_circulo_desde_puntos(pts)
        if fit is None:
            return
        rcx, rcy, rr, err_max = fit
        if rr <= 1e-9:
            return
        tol_abs = max(span_ref * 8e-5, float(self.factor_conversion) * 4e-5)
        tol_rel = 0.014
        if err_max > tol_abs and err_max / rr > tol_rel:
            return
        tag = "inner" if self._es_inner_layer(lu) else ("mark" if self._es_mark_layer(lu) else "outer")
        for ex in self._circulos_snap:
            ecx, ecy, er = float(ex[0]), float(ex[1]), float(ex[2])
            if math.hypot(rcx - ecx, rcy - ecy) < max(rr, er) * 0.04:
                if abs(rr - er) / max(rr, er, 1e-12) < 0.055:
                    return
        self._circulos_snap.append((rcx, rcy, rr, tag))

    def _clasificar_snap_arista(self, x, y, x1, y1, x2, y2, span):
        """Prioridad: extremos → punto medio → proyección sobre el tramo (cuerpo de arista)."""
        tol_ep = span * 0.015
        tol_mid = span * 0.011
        tol_seg = span * 0.032
        vx, vy = x2 - x1, y2 - y1
        L2 = vx * vx + vy * vy
        if L2 < 1e-18:
            return None
        t = max(0.0, min(1.0, ((x - x1) * vx + (y - y1) * vy) / L2))
        qx = x1 + t * vx
        qy = y1 + t * vy
        dseg = math.hypot(x - qx, y - qy)
        da = math.hypot(x - x1, y - y1)
        db = math.hypot(x - x2, y - y2)
        mx, my = (x1 + x2) * 0.5, (y1 + y2) * 0.5
        dm = math.hypot(x - mx, y - my)
        if da <= tol_ep:
            return da, (x1, y1), "endpoint"
        if db <= tol_ep:
            return db, (x2, y2), "endpoint"
        if dm <= tol_mid:
            return dm, (mx, my), "midpoint"
        if dseg <= tol_seg:
            return dseg, (qx, qy), "arista_cuerpo"
        return None

    def _limpiar_overlays_snap(self):
        for a in self._overlay_snap_artists:
            self._safe_remove(a)
        self._overlay_snap_artists = []

    def _aplicar_overlay_snap_visual(self, sc, span):
        """Marca OSNAP (X en vértices), grips en arista y punto de seguimiento."""
        self._limpiar_overlays_snap()
        self.marker_snap = self._safe_remove(self.marker_snap)
        tipo = sc.get("tipo")
        sk = sc.get("snap_kind")
        pt = sc["pt"]

        if (
            tipo == "arista"
            and sk in ("arista_cuerpo", "midpoint")
            and all(k in sc for k in ("x1", "y1", "x2", "y2"))
        ):
            x1, y1, x2, y2 = sc["x1"], sc["y1"], sc["x2"], sc["y2"]
            mx, my = (x1 + x2) * 0.5, (y1 + y2) * 0.5
            for gx, gy in ((x1, y1), (x2, y2), (mx, my)):
                g = self.ax.scatter(
                    [gx],
                    [gy],
                    s=34,
                    c="#38BDF8",
                    marker="s",
                    edgecolors="#0369A1",
                    linewidths=0.85,
                    zorder=11,
                    alpha=0.95,
                )
                self._overlay_snap_artists.append(g)

        if sk == "arista_cuerpo":
            g = self.ax.scatter(
                [pt[0]],
                [pt[1]],
                s=28,
                c="#FACC15",
                zorder=10,
                alpha=0.9,
                edgecolors="#713F12",
                linewidths=0.55,
            )
            self._overlay_snap_artists.append(g)

        if sk in ("endpoint", "vertice"):
            px, py = pt
            r = max(span * 0.0042, 1e-9)
            (l1,) = self.ax.plot(
                [px - r, px + r],
                [py - r, py + r],
                color="#A3E635",
                lw=1.45,
                zorder=12,
                solid_capstyle="round",
            )
            (l2,) = self.ax.plot(
                [px - r, px + r],
                [py + r, py - r],
                color="#A3E635",
                lw=1.45,
                zorder=12,
                solid_capstyle="round",
            )
            self._overlay_snap_artists.extend([l1, l2])
        elif sk in ("rim",) or tipo == "circulo":
            g = self.ax.scatter(
                [pt[0]],
                [pt[1]],
                s=42,
                c="#FDE047",
                zorder=10,
                edgecolors="black",
                linewidths=0.8,
            )
            self._overlay_snap_artists.append(g)
        elif tipo == "arco":
            g = self.ax.scatter(
                [pt[0]],
                [pt[1]],
                s=40,
                c="#FDE047",
                zorder=10,
                edgecolors="black",
                linewidths=0.8,
            )
            self._overlay_snap_artists.append(g)
        elif tipo == "libre" or sk is None:
            g = self.ax.scatter(
                [pt[0]],
                [pt[1]],
                s=30,
                c="#FACC15",
                zorder=9,
                alpha=0.8,
                edgecolors="#713F12",
                linewidths=0.5,
            )
            self._overlay_snap_artists.append(g)

    def _normal_cota_desde_cuerda(self, p1, p2):
        x1, y1 = float(p1[0]), float(p1[1])
        x2, y2 = float(p2[0]), float(p2[1])
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 1e-12:
            return None
        ux, uy = dx / L, dy / L
        nx, ny = -uy, ux
        midx, midy = (x1 + x2) * 0.5, (y1 + y2) * 0.5
        cmx, cmy = self._centro_pieza_vista
        if nx * (midx - cmx) + ny * (midy - cmy) < 0:
            nx, ny = -nx, -ny
        return nx, ny, ux, uy, L

    def _snap_cota(self, x, y):
        """Snap enriquecido: borde de círculo, arista (extremo / medio / cuerpo), vértice poligonal o libre."""
        if x is None or y is None:
            return {"tipo": "libre", "pt": (0.0, 0.0), "snap_kind": None}
        span = max(
            self.ax.get_xlim()[1] - self.ax.get_xlim()[0],
            self.ax.get_ylim()[1] - self.ax.get_ylim()[0],
        )
        tol_seg = span * 0.032
        tol_vert = span * 0.05
        cand = []
        for item in self._circulos_snap:
            cx, cy, r, tag = item[0], item[1], item[2], item[3]
            dc = math.hypot(x - cx, y - cy)
            d_rim = abs(dc - r)
            if dc < 1e-9:
                rim = (cx + r, cy)
            else:
                s = r / dc
                rim = (cx + (x - cx) * s, cy + (y - cy) * s)
            cand.append(
                (
                    d_rim,
                    {
                        "tipo": "circulo",
                        "pt": rim,
                        "cx": cx,
                        "cy": cy,
                        "r": r,
                        "tag": tag,
                        "snap_kind": "rim",
                    },
                )
            )
        for seg in self._geom_segmentos:
            if len(seg) == 5:
                x1, y1, x2, y2, aid = seg
            else:
                x1, y1, x2, y2 = seg[0], seg[1], seg[2], seg[3]
                aid = None
            cl = self._clasificar_snap_arista(x, y, x1, y1, x2, y2, span)
            if cl is None:
                continue
            d_best, pt_snap, kind = cl
            arc_seg = aid is not None
            base = {
                "pt": pt_snap,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "snap_kind": kind,
                "arc_seg": arc_seg,
            }
            if arc_seg and 0 <= int(aid) < len(self._arcos_pick):
                ag = self._arcos_pick[int(aid)]
                info = {
                    "tipo": "arco",
                    "cx": ag["cx"],
                    "cy": ag["cy"],
                    "r": ag["r"],
                    **base,
                }
            else:
                info = {"tipo": "arista", **base}
            cand.append((d_best, info))
        if len(self.vertices_cache) > 0:
            d = np.sqrt(np.sum((self.vertices_cache - np.array([x, y])) ** 2, axis=1))
            idx = int(np.argmin(d))
            if d[idx] < tol_vert:
                p = tuple(self.vertices_cache[idx])
                cand.append(
                    (float(d[idx]), {"tipo": "vertice", "pt": p, "snap_kind": "vertice"})
                )
        if not cand:
            return {"tipo": "libre", "pt": (float(x), float(y)), "snap_kind": None}
        cand.sort(key=lambda t: t[0])
        first_circ = next((c for c in cand if c[1].get("tipo") == "circulo"), None)
        first_arc = next((c for c in cand if c[1].get("tipo") == "arco"), None)
        best_d, best = cand[0]

        # Regla determinística para curvas:
        # si hay arco/círculo local (dentro de tolerancia de snap),
        # debe ganar frente a vértices/aristas facetadas.
        curve_pick = None
        if first_arc is not None and first_arc[0] <= tol_seg:
            curve_pick = first_arc
        if first_circ is not None and first_circ[0] <= tol_seg:
            if curve_pick is None or first_circ[0] <= curve_pick[0]:
                curve_pick = first_circ
        if curve_pick is not None:
            d_curve, info_curve = curve_pick
            if best.get("tipo") in ("arista", "vertice"):
                if d_curve <= max(best_d * 1.45, tol_seg * 0.20):
                    best_d, best = d_curve, info_curve
            elif best.get("tipo") in ("arco", "circulo"):
                if d_curve < best_d:
                    best_d, best = d_curve, info_curve
        if best["tipo"] in ("circulo", "arco", "arista"):
            if best_d > tol_seg:
                return {"tipo": "libre", "pt": (float(x), float(y)), "snap_kind": None}
        elif best["tipo"] == "vertice":
            if best_d > tol_vert:
                return {"tipo": "libre", "pt": (float(x), float(y)), "snap_kind": None}

        # Salvaguarda final (doble seguro) sobre geometría curva local.
        if best.get("tipo") in ("arista", "vertice") and first_arc is not None:
            d_arc, info_arc = first_arc
            if d_arc <= tol_seg * 0.55:
                return info_arc
        return best

    def _marca_centro_cad(self, cx, cy, preview=False):
        span = max(
            self.ax.get_xlim()[1] - self.ax.get_xlim()[0],
            self.ax.get_ylim()[1] - self.ax.get_ylim()[0],
        )
        s = max(span * 0.007, 1e-6)
        color = "#D1D5DB" if preview else "#F9FAFB"
        ls = "--" if preview else "-"
        (h,) = self.ax.plot([cx - s, cx + s], [cy, cy], color=color, ls=ls, lw=0.9, zorder=12)
        (v,) = self.ax.plot([cx, cx], [cy - s, cy + s], color=color, ls=ls, lw=0.9, zorder=12)
        return [h, v]

    def _cota_lineal_autocad(self, e1, e2, nx, ny, off, texto, preview=False):
        """Cota alineada tipo AutoCAD: extensiones con separación, línea de cota discontinua, flechas, texto."""
        x1, y1 = float(e1[0]), float(e1[1])
        x2, y2 = float(e2[0]), float(e2[1])
        dx, dy = x2 - x1, y2 - y1
        Lm = math.hypot(dx, dy)
        artists = []
        if Lm < 1e-12:
            return artists
        ux, uy = dx / Lm, dy / Lm
        span = max(
            self.ax.get_xlim()[1] - self.ax.get_xlim()[0],
            self.ax.get_ylim()[1] - self.ax.get_ylim()[0],
        )
        off_use = max(-span * 0.48, min(span * 0.48, float(off)))
        if abs(off_use) < span * 0.002:
            off_use = math.copysign(span * 0.05, off_use if off_use != 0 else 1.0)
        sg = 1.0 if off_use >= 0 else -1.0
        g0, g1 = span * 0.012, span * 0.022
        d1 = (x1 + nx * off_use, y1 + ny * off_use)
        d2 = (x2 + nx * off_use, y2 + ny * off_use)
        e1s = (x1 + nx * g0 * sg, y1 + ny * g0 * sg)
        e2s = (x2 + nx * g0 * sg, y2 + ny * g0 * sg)
        e1e = (d1[0] + nx * g1 * sg, d1[1] + ny * g1 * sg)
        e2e = (d2[0] + nx * g1 * sg, d2[1] + ny * g1 * sg)
        color = "#94A3B8" if preview else "#E5E7EB"
        ls = "--"
        lw = 0.75 if preview else 0.95
        (ln1,) = self.ax.plot([e1s[0], e1e[0]], [e1s[1], e1e[1]], color=color, ls=ls, lw=lw, zorder=9)
        (ln2,) = self.ax.plot([e2s[0], e2e[0]], [e2s[1], e2e[1]], color=color, ls=ls, lw=lw, zorder=9)
        artists.extend([ln1, ln2])
        mut = max(7.0, min(15.0, span * 0.035))
        fp = FancyArrowPatch(
            d1,
            d2,
            arrowstyle="<|-|>",
            mutation_scale=mut,
            color=color,
            linewidth=1.0 if not preview else 0.85,
            linestyle=ls,
            shrinkA=0,
            shrinkB=0,
            zorder=10,
        )
        self.ax.add_patch(fp)
        artists.append(fp)
        ang = math.degrees(math.atan2(uy, ux))
        if ang > 90:
            ang -= 180
        if ang < -90:
            ang += 180
        mtx = (d1[0] + d2[0]) * 0.5 + nx * sg * span * 0.028
        mty = (d1[1] + d2[1]) * 0.5 + ny * sg * span * 0.028
        t = self.ax.text(
            mtx,
            mty,
            texto,
            color="#F1F5F9" if not preview else "#CBD5E1",
            fontsize=9 if preview else 10,
            fontweight="bold",
            family="monospace",
            ha="center",
            va="center",
            rotation=ang,
            zorder=11,
            bbox=dict(
                facecolor="#0F172A",
                edgecolor="#64748B",
                alpha=0.95,
                pad=2.5,
                linewidth=0.7,
            ),
        )
        artists.append(t)
        return artists

    def _cota_diametro_autocad(self, cx, cy, r, ux, uy, off_n, preview=False):
        """Diámetro: línea por centro, flechas en intersección con círculo, marca + y texto Ø."""
        artists = []
        span = max(
            self.ax.get_xlim()[1] - self.ax.get_xlim()[0],
            self.ax.get_ylim()[1] - self.ax.get_ylim()[0],
        )
        nx, ny = -uy, ux
        cmx, cmy = self._centro_pieza_vista
        if nx * (cx - cmx) + ny * (cy - cmy) < 0:
            nx, ny = -nx, -ny
        p_a = (cx - ux * r, cy - uy * r)
        p_b = (cx + ux * r, cy + uy * r)
        color = "#94A3B8" if preview else "#E5E7EB"
        ls = "--"
        mut = max(7.0, min(14.0, span * 0.032))
        fp = FancyArrowPatch(
            p_a,
            p_b,
            arrowstyle="<|-|>",
            mutation_scale=mut,
            color=color,
            linewidth=1.0 if not preview else 0.85,
            linestyle=ls,
            shrinkA=0,
            shrinkB=0,
            zorder=10,
        )
        self.ax.add_patch(fp)
        artists.append(fp)
        artists.extend(self._marca_centro_cad(cx, cy, preview=preview))
        diam_in = (2.0 * r) / self.factor_conversion
        texto = f"Ø{diam_in:.4f}\""
        off_use = max(-span * 0.48, min(span * 0.48, float(off_n)))
        if abs(off_use) < span * 0.003:
            off_use = math.copysign(max(r, span * 0.04) * 0.4, off_use if off_use != 0 else 1.0)
        rim_x = cx + ux * r
        rim_y = cy + uy * r
        txp = cx + nx * off_use + ux * span * 0.012
        typ = cy + ny * off_use + uy * span * 0.012
        (ld,) = self.ax.plot(
            [rim_x, cx + nx * off_use * 0.55],
            [rim_y, cy + ny * off_use * 0.55],
            color=color,
            ls=ls,
            lw=0.8 if preview else 0.95,
            zorder=9,
        )
        artists.append(ld)
        ang = math.degrees(math.atan2(ny, nx))
        if ang > 90:
            ang -= 180
        if ang < -90:
            ang += 180
        t = self.ax.text(
            txp,
            typ,
            texto,
            color="#F1F5F9" if not preview else "#CBD5E1",
            fontsize=9 if preview else 10,
            fontweight="bold",
            family="monospace",
            ha="center",
            va="center",
            rotation=ang,
            zorder=11,
            bbox=dict(
                facecolor="#0F172A",
                edgecolor="#64748B",
                alpha=0.95,
                pad=2.5,
                linewidth=0.7,
            ),
        )
        artists.append(t)
        return artists

    def _cota_radio_autocad(self, cx, cy, r, rim_x, rim_y, off_n, preview=False):
        """Radio en arco (no cerrado): línea centro→arco, flecha en el borde, marca + y texto R."""
        artists = []
        span = max(
            self.ax.get_xlim()[1] - self.ax.get_xlim()[0],
            self.ax.get_ylim()[1] - self.ax.get_ylim()[0],
        )
        ux = (rim_x - cx) / max(r, 1e-12)
        uy = (rim_y - cy) / max(r, 1e-12)
        lu = math.hypot(ux, uy)
        if lu > 1e-12:
            ux, uy = ux / lu, uy / lu
        nx, ny = -uy, ux
        cmx, cmy = self._centro_pieza_vista
        if nx * (cx - cmx) + ny * (cy - cmy) < 0:
            nx, ny = -nx, -ny
        color = "#94A3B8" if preview else "#E5E7EB"
        ls = "--"
        artists.extend(self._marca_centro_cad(cx, cy, preview=preview))
        ix = cx + ux * max(r - span * 0.004, r * 0.02)
        iy = cy + uy * max(r - span * 0.004, r * 0.02)
        mut = max(7.0, min(13.0, span * 0.03))
        fp = FancyArrowPatch(
            (ix, iy),
            (rim_x, rim_y),
            arrowstyle="-|>",
            mutation_scale=mut,
            color=color,
            linewidth=0.95 if not preview else 0.8,
            linestyle=ls,
            shrinkA=0,
            shrinkB=0,
            zorder=10,
        )
        self.ax.add_patch(fp)
        artists.append(fp)
        rad_in = r / self.factor_conversion
        texto = f"R{rad_in:.4f}\""
        off_use = max(-span * 0.48, min(span * 0.48, float(off_n)))
        if abs(off_use) < span * 0.003:
            off_use = math.copysign(span * 0.06, off_use if off_use != 0 else 1.0)
        mpx = (cx + rim_x) * 0.5 + nx * off_use * 0.35
        mpy = (cy + rim_y) * 0.5 + ny * off_use * 0.35
        txp = mpx + nx * (abs(off_use) * 0.4 + span * 0.014)
        typ = mpy + ny * (abs(off_use) * 0.4 + span * 0.014)
        (ld,) = self.ax.plot(
            [mpx, txp],
            [mpy, typ],
            color=color,
            ls=ls,
            lw=0.75 if preview else 0.9,
            zorder=9,
        )
        artists.append(ld)
        ang = math.degrees(math.atan2(ny, nx))
        if ang > 90:
            ang -= 180
        if ang < -90:
            ang += 180
        t = self.ax.text(
            txp,
            typ,
            texto,
            color="#F1F5F9" if not preview else "#CBD5E1",
            fontsize=9 if preview else 10,
            fontweight="bold",
            family="monospace",
            ha="center",
            va="center",
            rotation=ang,
            zorder=11,
            bbox=dict(
                facecolor="#0F172A",
                edgecolor="#64748B",
                alpha=0.95,
                pad=2.5,
                linewidth=0.7,
            ),
        )
        artists.append(t)
        return artists

    @staticmethod
    def _snap_en_borde_para_lineal(sc):
        """Solo arista, vértice o borde circular/arco — nunca punto libre en el vacío."""
        # Regla: lo curvo (arco/círculo) se acota con radio/diámetro.
        return sc.get("tipo") in ("arista", "vertice")

    @staticmethod
    def _vector_unitario_arista(seg):
        x1, y1, x2, y2 = seg[0], seg[1], seg[2], seg[3]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 1e-12:
            return None
        return (dx / L, dy / L)

    @staticmethod
    def _aristas_misma_geometria(seg1, sc):
        if sc.get("tipo") != "arista":
            return False
        a = (seg1[0], seg1[1], seg1[2], seg1[3])
        b = (sc["x1"], sc["y1"], sc["x2"], sc["y2"])
        eps = 1e-5
        def cerca(p, q):
            return abs(p[0] - q[0]) < eps and abs(p[1] - q[1]) < eps
        return (cerca((a[0], a[1]), (b[0], b[1])) and cerca((a[2], a[3]), (b[2], b[3]))) or (
            cerca((a[0], a[1]), (b[2], b[3])) and cerca((a[2], a[3]), (b[0], b[1]))
        )

    @staticmethod
    def _aristas_paralelas(u1, u2, tol_deg=3.0):
        if u1 is None or u2 is None:
            return False
        c = min(1.0, abs(u1[0] * u2[0] + u1[1] * u2[1]))
        ang = math.degrees(math.acos(c))
        return ang < tol_deg or ang > 180.0 - tol_deg

    @staticmethod
    def _interseccion_lineas_inf(seg1, seg2):
        x1, y1, x2, y2 = float(seg1[0]), float(seg1[1]), float(seg1[2]), float(seg1[3])
        x3, y3, x4, y4 = float(seg2[0]), float(seg2[1]), float(seg2[2]), float(seg2[3])
        rx, ry = x2 - x1, y2 - y1
        sx, sy = x4 - x3, y4 - y3
        den = rx * sy - ry * sx
        if abs(den) < 1e-12:
            return None
        qpx, qpy = x3 - x1, y3 - y1
        t = (qpx * sy - qpy * sx) / den
        return (x1 + t * rx, y1 + t * ry)

    @staticmethod
    def _dir_desde_vertice(seg, vtx, pt_ref=None):
        ax, ay, bx, by = float(seg[0]), float(seg[1]), float(seg[2]), float(seg[3])
        vx, vy = float(vtx[0]), float(vtx[1])
        cand = []
        for ex, ey in ((ax, ay), (bx, by)):
            dx, dy = ex - vx, ey - vy
            L = math.hypot(dx, dy)
            if L > 1e-12:
                cand.append((dx / L, dy / L, L))
        if not cand:
            return None
        if pt_ref is not None:
            rx, ry = float(pt_ref[0]) - vx, float(pt_ref[1]) - vy
            rl = math.hypot(rx, ry)
            if rl > 1e-12:
                rx, ry = rx / rl, ry / rl
                cand.sort(key=lambda it: -(it[0] * rx + it[1] * ry))
                return (cand[0][0], cand[0][1])
        cand.sort(key=lambda it: -it[2])
        return (cand[0][0], cand[0][1])

    def _resolver_angulo_aristas(self, seg1, seg2, p1_ref, p2_ref, span):
        vtx = self._interseccion_lineas_inf(seg1, seg2)
        if vtx is None:
            return None
        v1 = self._dist_punto_segmento(vtx[0], vtx[1], seg1[0], seg1[1], seg1[2], seg1[3])[0]
        v2 = self._dist_punto_segmento(vtx[0], vtx[1], seg2[0], seg2[1], seg2[2], seg2[3])[0]
        if max(v1, v2) > span * 0.10:
            return None
        u1 = self._dir_desde_vertice(seg1, vtx, p1_ref)
        u2 = self._dir_desde_vertice(seg2, vtx, p2_ref)
        if u1 is None or u2 is None:
            return None
        d = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        ang = math.degrees(math.acos(d))
        if ang < 1.0 or ang > 179.0:
            return None
        return {"vtx": vtx, "u1": u1, "u2": u2}

    def _cota_angular_autocad(self, vtx, u1, u2, mx, my, preview=False):
        vx, vy = float(vtx[0]), float(vtx[1])
        a1 = math.atan2(u1[1], u1[0]) % (2.0 * math.pi)
        a2 = math.atan2(u2[1], u2[0]) % (2.0 * math.pi)
        ccw = (a2 - a1) % (2.0 * math.pi)
        cwx = (a1 - a2) % (2.0 * math.pi)
        ac = math.atan2(float(my) - vy, float(mx) - vx) % (2.0 * math.pi)
        in_ccw = ((ac - a1) % (2.0 * math.pi)) <= ccw
        use_ccw = in_ccw
        if use_ccw:
            a_start = a1
            sweep = ccw
        else:
            a_start = a2
            sweep = cwx
        span = max(
            self.ax.get_xlim()[1] - self.ax.get_xlim()[0],
            self.ax.get_ylim()[1] - self.ax.get_ylim()[0],
        )
        r = math.hypot(float(mx) - vx, float(my) - vy)
        r = max(span * 0.04, min(span * 0.45, r))
        color = "#94A3B8" if preview else "#E5E7EB"
        ls = "--"
        lw = 0.8 if preview else 0.95
        artists = []
        ext = r * 1.08
        (l1,) = self.ax.plot([vx, vx + u1[0] * ext], [vy, vy + u1[1] * ext], color=color, ls=ls, lw=lw, zorder=9)
        (l2,) = self.ax.plot([vx, vx + u2[0] * ext], [vy, vy + u2[1] * ext], color=color, ls=ls, lw=lw, zorder=9)
        artists.extend([l1, l2])
        th1 = math.degrees(a_start)
        th2 = math.degrees((a_start + sweep) % (2.0 * math.pi))
        if th2 <= th1:
            th2 += 360.0
        arc = Arc(
            (vx, vy),
            width=2.0 * r,
            height=2.0 * r,
            angle=0.0,
            theta1=th1,
            theta2=th2,
            color=color,
            lw=1.0 if not preview else 0.85,
            linestyle=ls,
            zorder=10,
        )
        self.ax.add_patch(arc)
        artists.append(arc)
        mid = (a_start + sweep * 0.5) % (2.0 * math.pi)
        txr = r + span * 0.03
        tx, ty = vx + txr * math.cos(mid), vy + txr * math.sin(mid)
        deg = math.degrees(sweep)
        t = self.ax.text(
            tx,
            ty,
            f"{deg:.4f}°",
            color="#F1F5F9" if not preview else "#CBD5E1",
            fontsize=9 if preview else 10,
            fontweight="bold",
            family="monospace",
            ha="center",
            va="center",
            rotation=math.degrees(mid),
            zorder=11,
            bbox=dict(
                facecolor="#0F172A",
                edgecolor="#64748B",
                alpha=0.95,
                pad=2.5,
                linewidth=0.7,
            ),
        )
        artists.append(t)
        return artists

    @staticmethod
    def _centro_y_radio_bulge(p1, p2, bulge):
        if abs(bulge) < 1e-12:
            return None
        x1, y1 = p1
        x2, y2 = p2
        dx, dy = x2 - x1, y2 - y1
        chord = math.hypot(dx, dy)
        if chord <= 1e-9:
            return None
        theta = 4.0 * math.atan(bulge)
        half_theta = abs(theta) / 2.0
        sin_half = math.sin(half_theta)
        if abs(sin_half) < 1e-12:
            return None
        r = chord / (2.0 * sin_half)
        mx = (x1 + x2) / 2.0
        my = (y1 + y2) / 2.0
        alpha = math.atan2(dy, dx)
        h_sq = max(r * r - (chord / 2.0) ** 2, 0.0)
        h = math.sqrt(h_sq)
        nx = -math.sin(alpha)
        ny = math.cos(alpha)
        sign = 1.0 if bulge > 0 else -1.0
        cx = mx + sign * h * nx
        cy = my + sign * h * ny
        return cx, cy, r

    def _registrar_arcos_bulge(self, entity, layer, rocx, rocy, rot):
        if not self._capa_relevante_visual(layer):
            return
        typ = entity.dxftype()
        verts = []
        try:
            if typ == "LWPOLYLINE":
                for item in entity.get_points("xyb"):
                    if len(item) >= 3:
                        verts.append((float(item[0]), float(item[1]), float(item[2] or 0.0)))
                    else:
                        verts.append((float(item[0]), float(item[1]), 0.0))
            elif typ == "POLYLINE":
                for v in entity.vertices:
                    p = v.dxf.location
                    b = float(getattr(v.dxf, "bulge", 0.0) or 0.0)
                    verts.append((float(p.x), float(p.y), b))
            else:
                return
        except Exception:
            return
        n = len(verts)
        if n < 2:
            return
        closed = False
        try:
            if typ == "LWPOLYLINE":
                closed = bool(entity.closed)
            elif typ == "POLYLINE":
                closed = bool(getattr(entity, "is_closed", False))
        except Exception:
            closed = False
        nseg = n if closed else n - 1
        for i in range(nseg):
            x1, y1, b = verts[i]
            x2, y2, _ = verts[(i + 1) % n]
            if abs(b) < 1e-12:
                continue
            cr = self._centro_y_radio_bulge((x1, y1), (x2, y2), b)
            if cr is None:
                continue
            cx_m, cy_m, r = cr
            arc_pts = _arc_points_from_bulge((x1, y1), (x2, y2), b, max_deg_step=7.5)
            if len(arc_pts) < 2:
                continue
            if rot:
                cx_v, cy_v = self._rotar_punto(cx_m, cy_m, rocx, rocy, rot)
            else:
                cx_v, cy_v = cx_m, cy_m
            self._arcos_pick.append({"cx": cx_v, "cy": cy_v, "r": float(r)})
            aid = len(self._arcos_pick) - 1
            poly = []
            for px, py in arc_pts:
                if rot:
                    px, py = self._rotar_punto(px, py, rocx, rocy, rot)
                poly.append((px, py))
            for ii in range(len(poly) - 1):
                a, b2 = poly[ii], poly[ii + 1]
                self._geom_segmentos.append((a[0], a[1], b2[0], b2[1], aid))

    def _cota_separacion_paralelas(self, e1, e2, u, n, W, mx, my, texto, preview=False):
        """Separación entre dos rectas paralelas. La posición de la cota sigue al cursor (tipo AutoCAD).

        La recta de medición es paralela a **n** (separación). Su desplazamiento lo fija **u·cursor** (tangente
        a las aristas), no **n·cursor**: este último define rectas paralelas a las aristas y rompe la cota
        entre aristas verticales (ancho) u horizontales (alto).
        """
        nx, ny = n[0], n[1]
        ux, uy = u[0], u[1]
        x1, y1 = float(e1[0]), float(e1[1])
        x2, y2 = float(e2[0]), float(e2[1])
        mxf, myf = float(mx), float(my)
        if W < 1e-15:
            return []
        span = max(
            self.ax.get_xlim()[1] - self.ax.get_xlim()[0],
            self.ax.get_ylim()[1] - self.ax.get_ylim()[0],
        )
        t1 = (mxf - x1) * ux + (myf - y1) * uy
        t2 = (mxf - x2) * ux + (myf - y2) * uy
        q1 = (x1 + ux * t1, y1 + uy * t1)
        q2 = (x2 + ux * t2, y2 + uy * t2)
        color = "#94A3B8" if preview else "#E5E7EB"
        ls = "--"
        lw = 0.75 if preview else 0.95
        artists = []
        g0, g1 = span * 0.012, span * 0.022

        def _seg_short(a, b, ga, gb):
            dx, dy = b[0] - a[0], b[1] - a[1]
            le = math.hypot(dx, dy)
            if le < 1e-12:
                return a, b
            dx, dy = dx / le, dy / le
            return (
                (a[0] + dx * ga, a[1] + dy * ga),
                (b[0] - dx * gb, b[1] - dy * gb),
            )

        e1s, e1e = _seg_short((x1, y1), q1, g0, g1)
        e2s, e2e = _seg_short((x2, y2), q2, g0, g1)
        (ln1,) = self.ax.plot([e1s[0], e1e[0]], [e1s[1], e1e[1]], color=color, ls=ls, lw=lw, zorder=9)
        (ln2,) = self.ax.plot([e2s[0], e2e[0]], [e2s[1], e2e[1]], color=color, ls=ls, lw=lw, zorder=9)
        artists.extend([ln1, ln2])
        mut = max(7.0, min(15.0, span * 0.035))
        fp = FancyArrowPatch(
            q1,
            q2,
            arrowstyle="<|-|>",
            mutation_scale=mut,
            color=color,
            linewidth=1.0 if not preview else 0.85,
            linestyle=ls,
            shrinkA=0,
            shrinkB=0,
            zorder=10,
        )
        self.ax.add_patch(fp)
        artists.append(fp)
        dqx, dqy = q2[0] - q1[0], q2[1] - q1[1]
        Lq = math.hypot(dqx, dqy)
        if Lq > 1e-12:
            ang = math.degrees(math.atan2(dqy / Lq, dqx / Lq))
        else:
            ang = math.degrees(math.atan2(ny, nx))
        if ang > 90:
            ang -= 180
        if ang < -90:
            ang += 180
        qmx, qmy = (q1[0] + q2[0]) * 0.5, (q1[1] + q2[1]) * 0.5
        dnp = (mxf - qmx) * nx + (myf - qmy) * ny
        s_txt = 1.0 if dnp >= 0 else -1.0
        lab = span * 0.03
        mtx = qmx + nx * s_txt * lab
        mty = qmy + ny * s_txt * lab
        t = self.ax.text(
            mtx,
            mty,
            texto,
            color="#F1F5F9" if not preview else "#CBD5E1",
            fontsize=9 if preview else 10,
            fontweight="bold",
            family="monospace",
            ha="center",
            va="center",
            rotation=ang,
            zorder=11,
            bbox=dict(
                facecolor="#0F172A",
                edgecolor="#64748B",
                alpha=0.95,
                pad=2.5,
                linewidth=0.7,
            ),
        )
        artists.append(t)
        return artists

    def _snap_circulo_mas_cercano(self, x, y, thresh):
        best_pt = None
        best_d = thresh
        for it in self._circulos_snap:
            cx, cy, r = float(it[0]), float(it[1]), float(it[2])
            ex = x - cx
            ey = y - cy
            d_center = math.hypot(ex, ey)
            if d_center < 1e-9:
                continue
            d_rim = abs(d_center - r)
            if d_rim < best_d:
                best_d = d_rim
                scale = r / d_center
                best_pt = (cx + ex * scale, cy + ey * scale)
        return best_pt, best_d

    def _snap_arco_mas_cercano(self, x, y, thresh):
        best = None
        best_d = thresh
        for ag in self._arcos_pick:
            cx = float(ag.get("cx", 0.0))
            cy = float(ag.get("cy", 0.0))
            r = float(ag.get("r", 0.0))
            if r <= 1e-12:
                continue
            dx = float(x) - cx
            dy = float(y) - cy
            dc = math.hypot(dx, dy)
            d_rim = abs(dc - r)
            if d_rim >= best_d:
                continue
            if dc < 1e-12:
                rim = (cx + r, cy)
            else:
                s = r / dc
                rim = (cx + dx * s, cy + dy * s)
            best_d = d_rim
            best = {
                "tipo": "arco",
                "pt": rim,
                "cx": cx,
                "cy": cy,
                "r": r,
                "snap_kind": "rim",
                "arc_seg": True,
            }
        return best, best_d

    def construir_tabla_3_columnas(self):
        self.frame_seccion_3.setStyleSheet(
            "QFrame#VisorInfoPanel{background:#0F172A;border:none;}"
        )
        row = QHBoxLayout(self.frame_seccion_3)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(18)

        def _metric(caption: str, attr_name: str, stretch: int = 0):
            wrap = QWidget()
            wrap.setStyleSheet("background:transparent;")
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(1)
            cap = QLabel(caption)
            cap.setStyleSheet("color:#64748B;font-size:10px;font-weight:700;background:transparent;")
            val = QLabel("-")
            val.setStyleSheet("color:#E2E8F0;font-size:13px;font-weight:700;background:transparent;")
            val.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            wl.addWidget(cap)
            wl.addWidget(val)
            setattr(self, attr_name, val)
            row.addWidget(wrap, stretch)

        _metric("LARGO (X)", "lbl_width")
        _metric("ANCHO (Y)", "lbl_height")
        _metric("AREA NETA", "lbl_area", stretch=1)
        _metric("PERIMETRO", "lbl_perim")
        _metric("REFERENCIA", "lbl_ref", stretch=2)



    def actualizar_datos(self, min_x, max_x, min_y, max_y, perimetro, valido, area=None, referencia=""):
        if not valido:
            return

        ancho_in = abs(max_x - min_x) / self.factor_conversion
        alto_in = abs(max_y - min_y) / self.factor_conversion
        perim_in = perimetro / self.factor_conversion
        area_in2 = (float(area) / (self.factor_conversion ** 2)) if area is not None else 0.0

        self.lbl_width.setText(f"{ancho_in:.2f}\"")
        self.lbl_height.setText(f"{alto_in:.2f}\"")
        self.lbl_area.setText(f"{area_in2:.2f} in²")
        self.lbl_perim.setText(f"{perim_in:.2f}\"")
        self.lbl_ref.setText(str(referencia or "-"))

    def actualizar_info_extra(self, area_in2=None, referencia=None):
        if area_in2 is not None:
            self.lbl_area.setText(f"{float(area_in2):.2f} in²")
        if referencia is not None:
            self.lbl_ref.setText(str(referencia or "-"))



    def limpiar_lienzo(self):

        self.ax.clear()

        self.ax.set_facecolor(CAD_VIEW_BG)

        self.ax.set_xticks([]); self.ax.set_yticks([])

        for spine in self.ax.spines.values(): spine.set_visible(False)

        self.vertices_cache = []
        self._circulos_snap = []
        self._geom_segmentos = []
        self._arcos_pick = []
        self._centro_pieza_vista = (0.0, 0.0)
        self._cota_preview_artists = []
        self._overlay_snap_artists = []
        self._dim_estado = "idle"
        self._dim = {}
        self._render_all_layers = False
        self.ax.set_autoscale_on(True)

        self.canvas.draw()



    def mostrar_patron_prueba(self):

        self.limpiar_lienzo()

        self.ax.text(
            0.5,
            0.5,
            "SELECCIONE UNA PIEZA DE LA LISTA",
            ha="center",
            va="center",
            transform=self.ax.transAxes,
            color="#64748B",
            fontsize=11,
        )
        self.lbl_width.setText('-')
        self.lbl_height.setText('-')
        self.lbl_area.setText('-')
        self.lbl_perim.setText('-')
        self.lbl_ref.setText('-')

        self.canvas.draw()

    def _snapshot_metricas_ui(self):
        return (
            self.lbl_width.text(),
            self.lbl_height.text(),
            self.lbl_area.text(),
            self.lbl_perim.text(),
            self.lbl_ref.text(),
        )

    def _restaurar_metricas_ui(self, snap):
        self.lbl_width.setText(snap[0])
        self.lbl_height.setText(snap[1])
        self.lbl_area.setText(snap[2])
        self.lbl_perim.setText(snap[3])
        self.lbl_ref.setText(snap[4])

    def ajustar_vista(self):
        if self._fit_xlim and self._fit_ylim:
            self.ax.set_xlim(self._fit_xlim)
            self.ax.set_ylim(self._fit_ylim)
            self.canvas.draw_idle()

    def set_material(self, material: str | None = None):
        self._material = str(material or "").strip()

    def _paleta_render(self):
        from interface.material_colors import paleta_cad_hex
        return paleta_cad_hex(getattr(self, "_material", ""))

    @staticmethod
    def _centroid_2d(pts):
        if not pts:
            return 0.0, 0.0
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    @staticmethod
    def _punto_en_poligono(x, y, poly):
        inside = False
        n = len(poly)
        if n < 3:
            return False
        j = n - 1
        for i in range(n):
            xi, yi = poly[i][0], poly[i][1]
            xj, yj = poly[j][0], poly[j][1]
            if ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / max(yj - yi, 1e-18) + xi
            ):
                inside = not inside
            j = i
        return inside

    def _rol_capa_pieza(self, layer_upper: str) -> str:
        if self._es_inner_layer(layer_upper):
            return "inner"
        if self._es_outer_layer(layer_upper):
            return "outer"
        if self._es_mark_layer(layer_upper):
            return "mark"
        return "auto"

    def _clasificar_contornos_cerrados(self, shapes: list) -> tuple[list, list]:
        outers: list = []
        inners: list = []
        pendientes: list = []
        for sh in shapes:
            rol = sh.get("rol", "auto")
            if rol == "inner":
                inners.append(sh)
            elif rol == "outer":
                outers.append(sh)
            elif rol == "mark":
                continue
            else:
                pendientes.append(sh)

        if not outers and pendientes:
            pendientes.sort(key=lambda s: float(s.get("area", 0.0) or 0.0), reverse=True)
            outers.append(pendientes.pop(0))
            for sh in pendientes:
                cx, cy = sh.get("centroid", (0.0, 0.0))
                if any(
                    self._punto_en_poligono(cx, cy, o.get("pts") or [])
                    for o in outers
                ):
                    inners.append(sh)
                else:
                    outers.append(sh)
            return outers, inners

        for sh in pendientes:
            cx, cy = sh.get("centroid", (0.0, 0.0))
            if outers and any(
                self._punto_en_poligono(cx, cy, o.get("pts") or []) for o in outers
            ):
                inners.append(sh)
            else:
                outers.append(sh)
        return outers, inners

    def set_persist_rotation_hook(self, hook):
        self._persist_rotation_hook = hook

    def rotar_vista_90(self):
        if not self._ruta_actual:
            return
        snap = self._snapshot_metricas_ui()
        self._rotacion_vista_deg = (self._rotacion_vista_deg + 90) % 360
        if callable(self._persist_rotation_hook):
            try:
                self._persist_rotation_hook(self._rotacion_vista_deg, self._ruta_actual)
            except Exception:
                pass
        self.renderizar_dxf(self._ruta_actual)
        self._restaurar_metricas_ui(snap)



    def renderizar_dxf(self, ruta_dxf, rotacion_vista_deg=None):

        self.limpiar_lienzo()

        try:
            if self._ruta_actual and str(self._ruta_actual) != str(ruta_dxf):
                if rotacion_vista_deg is not None:
                    self._rotacion_vista_deg = int(rotacion_vista_deg) % 360
                else:
                    self._rotacion_vista_deg = 0
            elif rotacion_vista_deg is not None:
                self._rotacion_vista_deg = int(rotacion_vista_deg) % 360
            self._ruta_actual = ruta_dxf

            doc = ezdxf.readfile(ruta_dxf)

            # Detectar unidad de entrada del DXF y convertir a pulgadas en UI.
            insunits = int(doc.header.get('$INSUNITS', 0) or 0)
            # factor_conversion = "cuántas unidades de entrada equivalen a 1 pulgada"
            # 1=in, 4=mm, 5=cm, 2=ft; fallback a mm por compatibilidad.
            if insunits == 1:      # Inches
                self.factor_conversion = 1.0
            elif insunits == 4:    # Millimeters
                self.factor_conversion = 25.4
            elif insunits == 5:    # Centimeters
                self.factor_conversion = 2.54
            elif insunits == 2:    # Feet
                self.factor_conversion = 12.0
            else:
                self.factor_conversion = 25.4

            msp = doc.modelspace()
            entities = list(msp)
            has_relevant_layers = False
            try:
                for e in entities:
                    if self._capa_relevante_visual(str(e.dxf.layer)):
                        has_relevant_layers = True
                        break
            except Exception:
                has_relevant_layers = False
            # Si el DXF no viene con capas CUT/IV_*, dibujamos todas para no perder vista previa.
            self._render_all_layers = not has_relevant_layers

            perimetro_total = 0.0
            area_neta = 0.0
            contornos = []
            all_points_raw = []
            circulos_raw = []
            distancia_suavizado = 0.05 * (self.factor_conversion / 25.4)

            for entity in entities:
                layer = entity.dxf.layer.upper()
                if not self._capa_relevante_visual(layer):
                    continue
                typ = entity.dxftype()

                if typ == "CIRCLE":
                    try:
                        c = entity.dxf.center
                        r = float(entity.dxf.radius)
                        if r <= 0:
                            continue
                        cx, cy = float(c.x), float(c.y)
                        circulos_raw.append((layer, cx, cy, r))
                        all_points_raw.extend(
                            [
                                (cx - r, cy - r),
                                (cx + r, cy - r),
                                (cx - r, cy + r),
                                (cx + r, cy + r),
                            ]
                        )
                        if self._es_cut_layer(layer) or self._render_all_layers:
                            perimetro_total += 2.0 * math.pi * r
                            if self._es_outer_layer(layer):
                                area_neta += math.pi * r * r
                            elif self._es_inner_layer(layer):
                                area_neta -= math.pi * r * r
                    except Exception:
                        pass
                    continue

                if typ == "ARC":
                    try:
                        c = entity.dxf.center
                        r = float(entity.dxf.radius)
                        sa = float(entity.dxf.start_angle)
                        ea = float(entity.dxf.end_angle)
                        if r <= 0:
                            continue
                        cx, cy = float(c.x), float(c.y)
                        t0, sweep = self._dxf_arc_ccw_sweep_rad(sa, ea)
                        if self._es_cut_layer(layer) or self._render_all_layers:
                            perimetro_total += r * sweep
                        n = max(8, int(math.degrees(sweep) / 4) + 1)
                        for i in range(n + 1):
                            u = t0 + sweep * (i / max(1, n))
                            all_points_raw.append((cx + r * math.cos(u), cy + r * math.sin(u)))
                        contornos.append(("ARC", layer, cx, cy, r, sa, ea))
                    except Exception:
                        pass
                    continue

                try:
                    p = path.make_path(entity)
                    vertices = list(p.flattening(distance=distancia_suavizado))
                    v2d = [(v[0], v[1]) for v in vertices]
                    if len(v2d) < 2:
                        continue

                    if self._es_cut_layer(layer) or self._render_all_layers:
                        for i in range(len(v2d) - 1):
                            perimetro_total += math.hypot(
                                v2d[i + 1][0] - v2d[i][0], v2d[i + 1][1] - v2d[i][1]
                            )

                    all_points_raw.extend(v2d)
                    contornos.append(("POLY", layer, v2d, bool(p.is_closed)))
                except Exception:
                    pass

            if all_points_raw:
                xs = [pt[0] for pt in all_points_raw]
                ys = [pt[1] for pt in all_points_raw]
                cx = (min(xs) + max(xs)) * 0.5
                cy = (min(ys) + max(ys)) * 0.5
            else:
                cx, cy = 0.0, 0.0

            all_points = []
            min_x, min_y, max_x, max_y = float("inf"), float("inf"), float("-inf"), float("-inf")
            rot = int(self._rotacion_vista_deg) % 360
            piece_fill, hole_fill, piece_edge = self._paleta_render()
            xs_raw = [pt[0] for pt in all_points_raw] if all_points_raw else []
            ys_raw = [pt[1] for pt in all_points_raw] if all_points_raw else []
            self._circulos_snap = []
            self._geom_segmentos = []
            self._arcos_pick = []

            for entity in entities:
                layer_e = entity.dxf.layer.upper()
                if not self._capa_relevante_visual(layer_e):
                    continue
                if entity.dxftype() in ("LWPOLYLINE", "POLYLINE"):
                    self._registrar_arcos_bulge(entity, layer_e, cx, cy, rot)

            shapes_cerrados: list = []

            def _dibujar_cerrado(shape, *, es_hueco: bool):
                nonlocal area_neta, min_x, max_x, min_y, max_y
                lw = 0.85 if es_hueco else 1.0
                z = 12 if es_hueco else 1
                fc = hole_fill if es_hueco else piece_fill
                if shape.get("kind") == "circle":
                    rcx = float(shape["rcx"])
                    rcy = float(shape["rcy"])
                    rr = float(shape["rr"])
                    self.ax.add_patch(
                        Circle(
                            (rcx, rcy),
                            rr,
                            facecolor=fc,
                            edgecolor=piece_edge,
                            linewidth=lw,
                            zorder=z,
                        )
                    )
                    min_x = min(min_x, rcx - rr, rcx + rr)
                    max_x = max(max_x, rcx - rr, rcx + rr)
                    min_y = min(min_y, rcy - rr, rcy + rr)
                    max_y = max(max_y, rcy - rr, rcy + rr)
                    if es_hueco:
                        area_neta -= math.pi * rr * rr
                    else:
                        area_neta += math.pi * rr * rr
                    return
                pts = shape.get("pts") or []
                if len(pts) < 3:
                    return
                self.ax.add_patch(
                    PathPatch(
                        MPLPath(pts, closed=True),
                        facecolor=fc,
                        edgecolor=piece_edge,
                        linewidth=lw,
                        zorder=z,
                    )
                )
                xs_p = [p[0] for p in pts]
                ys_p = [p[1] for p in pts]
                min_x = min(min_x, min(xs_p))
                max_x = max(max_x, max(xs_p))
                min_y = min(min_y, min(ys_p))
                max_y = max(max_y, max(ys_p))
                if es_hueco:
                    area_neta -= self._poly_area_2d(pts)
                else:
                    area_neta += self._poly_area_2d(pts)

            for layer_c, rcx0, rcy0, rr in circulos_raw:
                rcx, rcy = rcx0, rcy0
                if rot:
                    rcx, rcy = self._rotar_punto(rcx0, rcy0, cx, cy, rot)
                tag = "inner" if self._es_inner_layer(layer_c) else ("mark" if self._es_mark_layer(layer_c) else "outer")
                self._circulos_snap.append((rcx, rcy, rr, tag))
                nang = 48
                poly_circ = []
                for k in range(nang):
                    ang = 2 * math.pi * k / nang
                    px = rcx + rr * math.cos(ang)
                    py = rcy + rr * math.sin(ang)
                    poly_circ.append((px, py))
                    all_points.append((px, py))
                if self._es_mark_layer(layer_c):
                    self.ax.add_patch(
                        Circle(
                            (rcx, rcy),
                            rr,
                            facecolor="none",
                            edgecolor=CAD_MARK,
                            linewidth=1.0,
                            zorder=3,
                        )
                    )
                    min_x = min(min_x, rcx - rr, rcx + rr)
                    max_x = max(max_x, rcx - rr, rcx + rr)
                    min_y = min(min_y, rcy - rr, rcy + rr)
                    max_y = max(max_y, rcy - rr, rcy + rr)
                elif self._es_cut_layer(layer_c) or self._render_all_layers or self._es_outer_layer(layer_c) or self._es_inner_layer(layer_c):
                    shapes_cerrados.append(
                        {
                            "kind": "circle",
                            "rcx": rcx,
                            "rcy": rcy,
                            "rr": rr,
                            "pts": poly_circ,
                            "centroid": (rcx, rcy),
                            "area": math.pi * rr * rr,
                            "rol": self._rol_capa_pieza(layer_c),
                            "layer": layer_c,
                        }
                    )

            for item in contornos:
                if item[0] == "ARC":
                    _, layer_a, acx0, acy0, ar, asa, aea = item
                    n = max(8, int(abs(aea - asa) / 4) + 1)
                    t0, sweep = self._dxf_arc_ccw_sweep_rad(asa, aea)
                    poly = []
                    for i in range(n + 1):
                        u = t0 + sweep * (i / max(1, n))
                        px = acx0 + ar * math.cos(u)
                        py = acy0 + ar * math.sin(u)
                        if rot:
                            px, py = self._rotar_punto(px, py, cx, cy, rot)
                        poly.append((px, py))
                    acxv, acyv = acx0, acy0
                    if rot:
                        acxv, acyv = self._rotar_punto(acx0, acy0, cx, cy, rot)
                    self._arcos_pick.append({"cx": acxv, "cy": acyv, "r": float(ar)})
                    aid = len(self._arcos_pick) - 1
                    all_points.extend(poly)
                    xs_a = [p[0] for p in poly]
                    ys_a = [p[1] for p in poly]
                    min_x = min(min_x, min(xs_a))
                    max_x = max(max_x, max(xs_a))
                    min_y = min(min_y, min(ys_a))
                    max_y = max(max_y, max(ys_a))
                    for ii in range(len(poly) - 1):
                        a, b = poly[ii], poly[ii + 1]
                        self._geom_segmentos.append((a[0], a[1], b[0], b[1], aid))
                    mpl_path = MPLPath(poly, closed=False)
                    if self._es_outer_layer(layer_a):
                        self.ax.add_patch(
                            PathPatch(
                                mpl_path,
                                facecolor="none",
                                edgecolor=piece_edge,
                                linewidth=1.0,
                                zorder=1,
                            )
                        )
                    elif self._es_inner_layer(layer_a):
                        self.ax.add_patch(
                            PathPatch(
                                mpl_path,
                                facecolor="none",
                                edgecolor=piece_edge,
                                linewidth=0.8,
                                zorder=2,
                            )
                        )
                    elif self._es_mark_layer(layer_a):
                        self.ax.add_patch(
                            PathPatch(
                                mpl_path,
                                facecolor="none",
                                edgecolor=CAD_MARK,
                                linewidth=1.0,
                                zorder=3,
                            )
                        )
                    elif self._es_cut_layer(layer_a):
                        self.ax.add_patch(
                            PathPatch(
                                mpl_path,
                                facecolor="none",
                                edgecolor=piece_edge,
                                linewidth=1.0,
                                zorder=1,
                            )
                        )
                    elif self._render_all_layers:
                        self.ax.add_patch(
                            PathPatch(
                                mpl_path,
                                facecolor="none",
                                edgecolor=piece_edge,
                                linewidth=1.0,
                                zorder=1,
                            )
                        )
                    continue

                if item[0] != "POLY":
                    continue
                _, layer_p, pts_raw, is_closed = item
                if rot:
                    pts = [self._rotar_punto(x, y, cx, cy, rot) for (x, y) in pts_raw]
                else:
                    pts = pts_raw
                all_points.extend(pts)
                xs_p = [p[0] for p in pts]
                ys_p = [p[1] for p in pts]
                min_x = min(min_x, min(xs_p))
                max_x = max(max_x, max(xs_p))
                min_y = min(min_y, min(ys_p))
                max_y = max(max_y, max(ys_p))
                span_loc = max(max(xs_p) - min(xs_p), max(ys_p) - min(ys_p), 1e-9)
                if is_closed and len(pts) >= 6:
                    self._circulos_snap_agregar_si_circular(pts, layer_p, span_loc)
                for ii in range(len(pts) - 1):
                    a, b = pts[ii], pts[ii + 1]
                    self._geom_segmentos.append((a[0], a[1], b[0], b[1], None))
                if is_closed and len(pts) >= 2:
                    a, b = pts[-1], pts[0]
                    self._geom_segmentos.append((a[0], a[1], b[0], b[1], None))
                if is_closed and len(pts) >= 3 and (
                    self._es_cut_layer(layer_p)
                    or self._render_all_layers
                    or self._es_outer_layer(layer_p)
                    or self._es_inner_layer(layer_p)
                ):
                    shapes_cerrados.append(
                        {
                            "kind": "poly",
                            "pts": pts,
                            "centroid": self._centroid_2d(pts),
                            "area": abs(self._poly_area_2d(pts)),
                            "rol": self._rol_capa_pieza(layer_p),
                            "layer": layer_p,
                        }
                    )
                    continue
                mpl_path = MPLPath(pts, closed=is_closed)
                if self._es_mark_layer(layer_p):
                    self.ax.add_patch(
                        PathPatch(
                            mpl_path,
                            facecolor="none",
                            edgecolor=CAD_MARK,
                            linewidth=1,
                            zorder=3,
                        )
                    )
                elif not is_closed or self._es_cut_layer(layer_p) or self._render_all_layers:
                    self.ax.add_patch(
                        PathPatch(
                            mpl_path,
                            facecolor="none",
                            edgecolor=piece_edge,
                            linewidth=1.0,
                            zorder=1,
                        )
                    )

            outers_cls, inners_cls = self._clasificar_contornos_cerrados(shapes_cerrados)
            for sh in outers_cls:
                _dibujar_cerrado(sh, es_hueco=False)
            for sh in inners_cls:
                _dibujar_cerrado(sh, es_hueco=True)

            if all_points:
                uniq = {}
                for pt in all_points:
                    key = (round(pt[0], 5), round(pt[1], 5))
                    uniq[key] = pt
                self.vertices_cache = np.array(list(uniq.values()))
                self._centro_pieza_vista = (
                    float(sum(p[0] for p in all_points)) / len(all_points),
                    float(sum(p[1] for p in all_points)) / len(all_points),
                )
                xs_v = [p[0] for p in all_points]
                ys_v = [p[1] for p in all_points]
                vx0, vx1 = min(xs_v), max(xs_v)
                vy0, vy1 = min(ys_v), max(ys_v)
                mw = max(vx1 - vx0, 1e-9)
                mh = max(vy1 - vy0, 1e-9)
                mx_m = mw * 0.10
                my_m = mh * 0.10
                self.ax.set_xlim(vx0 - mx_m, vx1 + mx_m)
                self.ax.set_ylim(vy0 - my_m, vy1 + my_m)
                self._fit_xlim = (vx0 - mx_m, vx1 + mx_m)
                self._fit_ylim = (vy0 - my_m, vy1 + my_m)
                self.ax.set_aspect("equal", adjustable="datalim")
                min_x_d = min(xs_raw) if xs_raw else min_x
                max_x_d = max(xs_raw) if xs_raw else max_x
                min_y_d = min(ys_raw) if ys_raw else min_y
                max_y_d = max(ys_raw) if ys_raw else max_y
                self.actualizar_datos(
                    min_x_d,
                    max_x_d,
                    min_y_d,
                    max_y_d,
                    perimetro_total,
                    True,
                    area=max(0.0, area_neta),
                )
                self.ax.set_autoscale_on(False)
            else:
                self.ax.set_autoscale_on(True)

            self.canvas.draw()

            return True

        except Exception:
            return False

    def _safe_remove(self, artist):

        if artist:

            try: artist.remove()

            except: pass

        return None



    def obtener_vertice_cercano(self, x, y):
        if x is None or y is None:
            return None
        span = self.ax.get_xlim()[1] - self.ax.get_xlim()[0]
        thresh = span * 0.05
        best = None
        best_d = thresh
        if len(self.vertices_cache) > 0:
            d = np.sqrt(np.sum((self.vertices_cache - np.array([x, y])) ** 2, axis=1))
            idx = int(np.argmin(d))
            if d[idx] < best_d:
                best_d = float(d[idx])
                best = tuple(self.vertices_cache[idx])
        circ_pt, circ_d = self._snap_circulo_mas_cercano(x, y, thresh)
        if circ_pt is not None and circ_d < best_d:
            best = circ_pt
        return best



    def on_mouse_move(self, event):

        if not event.inaxes:
            return

        if self._is_panning and self._lims is not None:
            cw = max(1, int(self.widget.width()))
            ch = max(1, int(self.widget.height()))
            dx = (event.x - self._pan_start[0]) * (self._lims[0][1] - self._lims[0][0]) / cw
            dy = (event.y - self._pan_start[1]) * (self._lims[1][1] - self._lims[1][0]) / ch
            self.ax.set_xlim(self._lims[0][0] - dx, self._lims[0][1] - dx)
            self.ax.set_ylim(self._lims[1][0] - dy, self._lims[1][1] - dy)
            self._set_pan_cursor("panning")
            self.canvas.draw_idle()
            return

        sc = self._snap_cota(event.xdata, event.ydata)
        span_ov = max(
            self.ax.get_xlim()[1] - self.ax.get_xlim()[0],
            self.ax.get_ylim()[1] - self.ax.get_ylim()[0],
        )
        self._aplicar_overlay_snap_visual(sc, span_ov)

        self._clear_cota_preview()
        mx, my = float(event.xdata), float(event.ydata)
        est = self._dim_estado

        if est == "lin_p1":
            p1 = self._dim.get("p1")
            if p1:
                (rb,) = self.ax.plot(
                    [p1[0], mx],
                    [p1[1], my],
                    color="#64748B",
                    ls="--",
                    lw=0.75,
                    alpha=0.55,
                    zorder=8,
                )
                self._cota_preview_artists.append(rb)

        elif est == "ang_p3":
            vtx = self._dim.get("vtx")
            u1 = self._dim.get("u1")
            u2 = self._dim.get("u2")
            if vtx and u1 and u2:
                self._cota_preview_artists = self._cota_angular_autocad(
                    vtx, u1, u2, mx, my, preview=True
                )

        elif est == "lin_p2_after_edge":
            nx, ny = self._dim["nx"], self._dim["ny"]
            midx, midy = self._dim["midx"], self._dim["midy"]
            p1, p2 = self._dim["chord_p1"], self._dim["chord_p2"]
            span_mv = max(
                self.ax.get_xlim()[1] - self.ax.get_xlim()[0],
                self.ax.get_ylim()[1] - self.ax.get_ylim()[0],
            )
            off = (mx - midx) * nx + (my - midy) * ny
            off = max(-span_mv * 0.48, min(span_mv * 0.48, off))
            L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            dist_in = L / self.factor_conversion
            self._cota_preview_artists = self._cota_lineal_autocad(
                p1, p2, nx, ny, off, f"{dist_in:.4f}\"", preview=True
            )

        elif est == "lin_p3":
            nx, ny = self._dim["nx"], self._dim["ny"]
            midx, midy = self._dim["midx"], self._dim["midy"]
            span_mv = max(
                self.ax.get_xlim()[1] - self.ax.get_xlim()[0],
                self.ax.get_ylim()[1] - self.ax.get_ylim()[0],
            )
            off = (mx - midx) * nx + (my - midy) * ny
            off = max(-span_mv * 0.48, min(span_mv * 0.48, off))
            if self._dim.get("mode") == "parallel":
                u = self._dim["u"]
                e1, e2 = self._dim["e1"], self._dim["e2"]
                W = self._dim["W"]
                dist_in = W / self.factor_conversion
                self._cota_preview_artists = self._cota_separacion_paralelas(
                    e1, e2, u, (nx, ny), W, mx, my, f"{dist_in:.4f}\"", preview=True
                )
            else:
                p1, p2 = self._dim["p1"], self._dim["p2"]
                L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                dist_in = L / self.factor_conversion
                self._cota_preview_artists = self._cota_lineal_autocad(
                    p1, p2, nx, ny, off, f"{dist_in:.4f}\"", preview=True
                )

        elif est == "dia_p2":
            cx, cy, r = self._dim["cx"], self._dim["cy"], self._dim["r"]
            ux, uy = self._dim["ux"], self._dim["uy"]
            nx, ny = -uy, ux
            cmx, cmy = self._centro_pieza_vista
            if nx * (cx - cmx) + ny * (cy - cmy) < 0:
                nx, ny = -nx, -ny
            off_n = (mx - cx) * nx + (my - cy) * ny
            span_mv = max(
                self.ax.get_xlim()[1] - self.ax.get_xlim()[0],
                self.ax.get_ylim()[1] - self.ax.get_ylim()[0],
            )
            off_n = max(-span_mv * 0.48, min(span_mv * 0.48, off_n))
            self._cota_preview_artists = self._cota_diametro_autocad(
                cx, cy, r, ux, uy, off_n, preview=True
            )

        elif est == "rad_p2":
            cx, cy, r = self._dim["cx"], self._dim["cy"], self._dim["r"]
            rim_x, rim_y = self._dim["rim"]
            ux = (rim_x - cx) / max(r, 1e-12)
            uy = (rim_y - cy) / max(r, 1e-12)
            lu = math.hypot(ux, uy)
            if lu > 1e-12:
                ux, uy = ux / lu, uy / lu
            nx, ny = -uy, ux
            cmx, cmy = self._centro_pieza_vista
            if nx * (cx - cmx) + ny * (cy - cmy) < 0:
                nx, ny = -nx, -ny
            off_n = (mx - cx) * nx + (my - cy) * ny
            span_mv = max(
                self.ax.get_xlim()[1] - self.ax.get_xlim()[0],
                self.ax.get_ylim()[1] - self.ax.get_ylim()[0],
            )
            off_n = max(-span_mv * 0.48, min(span_mv * 0.48, off_n))
            self._cota_preview_artists = self._cota_radio_autocad(
                cx, cy, r, rim_x, rim_y, off_n, preview=True
            )

        self.canvas.draw_idle()



    def on_scroll(self, event):
        if not event.inaxes or event.xdata is None or event.ydata is None:
            return
        step = getattr(event, "step", None)
        if step is None:
            return
        try:
            st = float(step)
        except (TypeError, ValueError):
            return
        if abs(st) < 0.45:
            return
        factor = 1 / 1.35 if st > 0 else 1.35
        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()
        cx, cy = event.xdata, event.ydata

        new_xlim = [cx - (cx - xlim[0]) * factor, cx + (xlim[1] - cx) * factor]
        new_ylim = [cy - (cy - ylim[0]) * factor, cy + (ylim[1] - cy) * factor]
        # Evita bloqueo por zoom extremo, pero permite acercamiento fuerte.
        if abs(new_xlim[1] - new_xlim[0]) < 1e-6 or abs(new_ylim[1] - new_ylim[0]) < 1e-6:
            return
        self.ax.set_xlim(new_xlim)
        self.ax.set_ylim(new_ylim)
        self.canvas.draw_idle()

    def on_click(self, event):
        if not event.inaxes:
            return
        # Botón medio sostenido: paneo.
        if event.button == 2:
            self._is_panning = True
            self._pan_start = (event.x, event.y)
            self._lims = (self.ax.get_xlim(), self.ax.get_ylim())
            self._set_pan_cursor("panning")
            return
        # Click derecho: rotar vista 90° (como referencia visual de nesting).
        if event.button == 3:
            self.rotar_vista_90()
            return
        if event.button != 1:
            return

        sc = self._snap_cota(event.xdata, event.ydata)
        pt = sc["pt"]
        span = max(
            self.ax.get_xlim()[1] - self.ax.get_xlim()[0],
            self.ax.get_ylim()[1] - self.ax.get_ylim()[0],
        )
        eps = max(span * 1e-7, 1e-9)

        if self._dim_estado == "idle":
            if sc["tipo"] == "circulo":
                cx, cy, r = sc["cx"], sc["cy"], sc["r"]
                ux = (pt[0] - cx) / r
                uy = (pt[1] - cy) / r
                lu = math.hypot(ux, uy)
                if lu > 1e-12:
                    ux, uy = ux / lu, uy / lu
                self._dim = {"cx": cx, "cy": cy, "r": r, "ux": ux, "uy": uy}
                self._dim_estado = "dia_p2"
            elif sc["tipo"] == "arco":
                self._dim = {"cx": sc["cx"], "cy": sc["cy"], "r": sc["r"], "rim": (pt[0], pt[1])}
                self._dim_estado = "rad_p2"
            elif self._snap_en_borde_para_lineal(sc):
                if sc["tipo"] == "arista" and not sc.get("arc_seg"):
                    x1, y1, x2, y2 = sc["x1"], sc["y1"], sc["x2"], sc["y2"]
                    p_a, p_b = (x1, y1), (x2, y2)
                    nr = self._normal_cota_desde_cuerda(p_a, p_b)
                    if nr is None:
                        return
                    nx, ny, _ux, _uy, _L = nr
                    self._dim = {
                        "seg1": (x1, y1, x2, y2),
                        "first_pt": (float(pt[0]), float(pt[1])),
                        "p1": p_a,
                        "p2": p_b,
                        "chord_p1": p_a,
                        "chord_p2": p_b,
                        "mode": "chord_pending",
                        "nx": nx,
                        "ny": ny,
                        "midx": (p_a[0] + p_b[0]) * 0.5,
                        "midy": (p_a[1] + p_b[1]) * 0.5,
                    }
                    self._dim_estado = "lin_p2_after_edge"
                else:
                    d0 = {"p1": (pt[0], pt[1])}
                    if sc["tipo"] == "arista":
                        d0["seg1"] = (sc["x1"], sc["y1"], sc["x2"], sc["y2"])
                    self._dim = d0
                    self._dim_estado = "lin_p1"
            else:
                return
            self.canvas.draw()
            return

        if self._dim_estado == "lin_p2_after_edge":
            seg1 = self._dim["seg1"]
            first_pt = self._dim["first_pt"]
            chord_p1 = self._dim["chord_p1"]
            chord_p2 = self._dim["chord_p2"]
            nx0, ny0 = self._dim["nx"], self._dim["ny"]
            midx0, midy0 = self._dim["midx"], self._dim["midy"]

            if (
                sc["tipo"] == "arista"
                and not sc.get("arc_seg")
                and seg1 is not None
            ):
                u1 = self._vector_unitario_arista(seg1)
                u2 = self._vector_unitario_arista(
                    (sc["x1"], sc["y1"], sc["x2"], sc["y2"])
                )
                if (
                    u1
                    and u2
                    and self._aristas_paralelas(u1, u2)
                    and not self._aristas_misma_geometria(seg1, sc)
                ):
                    nxp, nyp = -u1[1], u1[0]
                    vx, vy = pt[0] - first_pt[0], pt[1] - first_pt[1]
                    raw = vx * nxp + vy * nyp
                    if raw < 0:
                        nxp, nyp = -nxp, -nyp
                        raw = -raw
                    W = raw
                    if W >= max(eps * 500, span * 1e-8):
                        midx = (first_pt[0] + pt[0]) * 0.5
                        midy = (first_pt[1] + pt[1]) * 0.5
                        self._dim = {
                            "mode": "parallel",
                            "e1": first_pt,
                            "e2": (pt[0], pt[1]),
                            "u": u1,
                            "n": (nxp, nyp),
                            "W": W,
                            "nx": nxp,
                            "ny": nyp,
                            "midx": midx,
                            "midy": midy,
                            "seg1": seg1,
                        }
                        self._dim_estado = "lin_p3"
                        self._clear_cota_preview()
                        self.canvas.draw()
                        return
                if (
                    u1
                    and u2
                    and (not self._aristas_paralelas(u1, u2))
                    and (not self._aristas_misma_geometria(seg1, sc))
                ):
                    seg2 = (sc["x1"], sc["y1"], sc["x2"], sc["y2"])
                    ang_data = self._resolver_angulo_aristas(seg1, seg2, first_pt, pt, span)
                    if ang_data is not None:
                        self._dim = {
                            "mode": "angle",
                            "vtx": ang_data["vtx"],
                            "u1": ang_data["u1"],
                            "u2": ang_data["u2"],
                        }
                        self._dim_estado = "ang_p3"
                        self._clear_cota_preview()
                        self.canvas.draw()
                        return

            mx_c = float(event.xdata)
            my_c = float(event.ydata)
            off = (mx_c - midx0) * nx0 + (my_c - midy0) * ny0
            off = max(-span * 0.48, min(span * 0.48, off))
            self._clear_cota_preview()
            L = math.hypot(chord_p2[0] - chord_p1[0], chord_p2[1] - chord_p1[1])
            dist_in = L / self.factor_conversion
            self._cota_lineal_autocad(
                chord_p1, chord_p2, nx0, ny0, off, f"{dist_in:.4f}\"", preview=False
            )
            self._dim_estado = "idle"
            self._dim = {}
            self.canvas.draw()
            return

        if self._dim_estado == "lin_p1":
            if not self._snap_en_borde_para_lineal(sc):
                return
            p1 = self._dim["p1"]
            if math.hypot(pt[0] - p1[0], pt[1] - p1[1]) < eps:
                return
            seg1 = self._dim.get("seg1")
            if seg1 is not None and sc["tipo"] == "arista":
                u1 = self._vector_unitario_arista(seg1)
                u2 = self._vector_unitario_arista(
                    (sc["x1"], sc["y1"], sc["x2"], sc["y2"])
                )
                if u1 and u2 and self._aristas_paralelas(u1, u2):
                    midx = (p1[0] + pt[0]) * 0.5
                    midy = (p1[1] + pt[1]) * 0.5
                    nxp, nyp = -u1[1], u1[0]
                    vx, vy = pt[0] - p1[0], pt[1] - p1[1]
                    raw = vx * nxp + vy * nyp
                    if raw < 0:
                        nxp, nyp = -nxp, -nyp
                        raw = -raw
                    W = raw
                    if W < max(eps * 500, span * 1e-8):
                        return
                    self._dim.update(
                        {
                            "mode": "parallel",
                            "e1": p1,
                            "e2": pt,
                            "u": u1,
                            "n": (nxp, nyp),
                            "W": W,
                            "nx": nxp,
                            "ny": nyp,
                            "midx": midx,
                            "midy": midy,
                            "p2": pt,
                        }
                    )
                    self._dim_estado = "lin_p3"
                    self.canvas.draw()
                    return
                if (
                    u1
                    and u2
                    and (not self._aristas_paralelas(u1, u2))
                    and (not self._aristas_misma_geometria(seg1, sc))
                    and (not sc.get("arc_seg"))
                ):
                    seg2 = (sc["x1"], sc["y1"], sc["x2"], sc["y2"])
                    ang_data = self._resolver_angulo_aristas(seg1, seg2, p1, pt, span)
                    if ang_data is not None:
                        self._dim = {
                            "mode": "angle",
                            "vtx": ang_data["vtx"],
                            "u1": ang_data["u1"],
                            "u2": ang_data["u2"],
                        }
                        self._dim_estado = "ang_p3"
                        self.canvas.draw()
                        return
                if self._aristas_misma_geometria(seg1, sc):
                    s = seg1
                    p_a = (s[0], s[1])
                    p_b = (s[2], s[3])
                    nr = self._normal_cota_desde_cuerda(p_a, p_b)
                    if nr is None:
                        return
                    nx, ny, _ux, _uy, _L = nr
                    self._dim["p1"], self._dim["p2"] = p_a, p_b
                    self._dim["mode"] = "chord"
                    self._dim["nx"], self._dim["ny"] = nx, ny
                    self._dim["midx"] = (p_a[0] + p_b[0]) * 0.5
                    self._dim["midy"] = (p_a[1] + p_b[1]) * 0.5
                    self._dim_estado = "lin_p3"
                    self.canvas.draw()
                    return
            nr = self._normal_cota_desde_cuerda(p1, pt)
            if nr is None:
                return
            nx, ny, _ux, _uy, _L = nr
            self._dim["p2"] = pt
            self._dim["nx"], self._dim["ny"] = nx, ny
            self._dim["midx"] = (p1[0] + pt[0]) * 0.5
            self._dim["midy"] = (p1[1] + pt[1]) * 0.5
            self._dim["mode"] = "chord"
            self._dim_estado = "lin_p3"
            self.canvas.draw()
            return

        if self._dim_estado == "ang_p3":
            vtx = self._dim.get("vtx")
            u1 = self._dim.get("u1")
            u2 = self._dim.get("u2")
            if not (vtx and u1 and u2):
                self._dim_estado = "idle"
                self._dim = {}
                self.canvas.draw()
                return
            self._clear_cota_preview()
            self._cota_angular_autocad(vtx, u1, u2, float(event.xdata), float(event.ydata), preview=False)
            self._dim_estado = "idle"
            self._dim = {}
            self.canvas.draw()
            return

        if self._dim_estado == "lin_p3":
            nx, ny = self._dim["nx"], self._dim["ny"]
            midx, midy = self._dim["midx"], self._dim["midy"]
            mx_c = float(event.xdata)
            my_c = float(event.ydata)
            off = (mx_c - midx) * nx + (my_c - midy) * ny
            off = max(-span * 0.48, min(span * 0.48, off))
            self._clear_cota_preview()
            if self._dim.get("mode") == "parallel":
                u = self._dim["u"]
                e1, e2 = self._dim["e1"], self._dim["e2"]
                W = self._dim["W"]
                dist_in = W / self.factor_conversion
                self._cota_separacion_paralelas(
                    e1, e2, u, (nx, ny), W, mx_c, my_c, f"{dist_in:.4f}\"", preview=False
                )
            else:
                p1, p2 = self._dim["p1"], self._dim["p2"]
                L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                dist_in = L / self.factor_conversion
                self._cota_lineal_autocad(p1, p2, nx, ny, off, f"{dist_in:.4f}\"", preview=False)
            self._dim_estado = "idle"
            self._dim = {}
            self.canvas.draw()
            return

        if self._dim_estado == "dia_p2":
            cx, cy, r = self._dim["cx"], self._dim["cy"], self._dim["r"]
            ux, uy = self._dim["ux"], self._dim["uy"]
            nx, ny = -uy, ux
            cmx, cmy = self._centro_pieza_vista
            if nx * (cx - cmx) + ny * (cy - cmy) < 0:
                nx, ny = -nx, -ny
            off_n = (float(event.xdata) - cx) * nx + (float(event.ydata) - cy) * ny
            off_n = max(-span * 0.48, min(span * 0.48, off_n))
            self._clear_cota_preview()
            self._cota_diametro_autocad(cx, cy, r, ux, uy, off_n, preview=False)
            self._dim_estado = "idle"
            self._dim = {}
            self.canvas.draw()
            return

        if self._dim_estado == "rad_p2":
            cx, cy, r = self._dim["cx"], self._dim["cy"], self._dim["r"]
            rim_x, rim_y = self._dim["rim"]
            ux = (rim_x - cx) / max(r, 1e-12)
            uy = (rim_y - cy) / max(r, 1e-12)
            lu = math.hypot(ux, uy)
            if lu > 1e-12:
                ux, uy = ux / lu, uy / lu
            nx, ny = -uy, ux
            cmx, cmy = self._centro_pieza_vista
            if nx * (cx - cmx) + ny * (cy - cmy) < 0:
                nx, ny = -nx, -ny
            off_n = (float(event.xdata) - cx) * nx + (float(event.ydata) - cy) * ny
            off_n = max(-span * 0.48, min(span * 0.48, off_n))
            self._clear_cota_preview()
            self._cota_radio_autocad(cx, cy, r, rim_x, rim_y, off_n, preview=False)
            self._dim_estado = "idle"
            self._dim = {}
            self.canvas.draw()
            return

    def on_release(self, event):
        if event.button == 2:
            self._is_panning = False
            self._lims = None
            self._set_pan_cursor("normal")

    def _set_pan_cursor(self, mode):
        if mode == self._cursor_mode:
            return
        self._cursor_mode = mode
        try:
            if mode == "panning":
                self.widget.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            else:
                self.widget.unsetCursor()
        except Exception:
            pass



def generar_thumbnail(ruta_dxf, size=(50, 50), material: str | None = None):

    try:
        from interface.material_colors import paleta_cad_hex
        piece_fill, hole_fill, piece_edge = paleta_cad_hex(material)

        fig = Figure(figsize=(2, 2), dpi=50); FigureCanvasAgg(fig)

        fig.patch.set_facecolor(CAD_VIEW_BG); ax = fig.add_subplot(111); ax.axis('off')

        msp = ezdxf.readfile(ruta_dxf).modelspace()

        for e in msp:

            try:
                layer_u = e.dxf.layer.upper()
                if e.dxftype() == "CIRCLE" and "CUT" in layer_u:
                    c = e.dxf.center
                    r = float(e.dxf.radius)
                    if "OUTER" in layer_u:
                        color = piece_fill
                    elif "INNER" in layer_u or "INTERIOR" in layer_u:
                        color = CAD_VIEW_BG
                    else:
                        color = piece_fill
                    ax.add_patch(
                        Circle((float(c.x), float(c.y)), r, facecolor=color, edgecolor=piece_edge, linewidth=0.5)
                    )
                    continue
                # --- AJUSTE PARA EL THUMBNAIL ---
                p = path.make_path(e); pts = [(v[0],v[1]) for v in p.flattening(0.01)]

                if len(pts)>2:

                    from matplotlib.patches import Polygon

                    if "OUTER" in layer_u:
                        color = piece_fill
                    elif "INNER" in layer_u or "INTERIOR" in layer_u:
                        color = CAD_VIEW_BG
                    else:
                        color = piece_fill

                    ax.add_patch(Polygon(pts, closed=True, facecolor=color, edgecolor=piece_edge, linewidth=0.5))

            except: pass

        ax.autoscale(enable=True); ax.set_aspect('equal')

        buf = io.BytesIO(); fig.savefig(buf, format='png', facecolor=CAD_VIEW_BG); buf.seek(0)

        img = Image.open(buf).convert("RGBA")
        data = img.tobytes("raw", "RGBA")
        qimg = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
        pix = QPixmap.fromImage(qimg)
        return pix.scaled(
            size[0], size[1],
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    except Exception:
        return None