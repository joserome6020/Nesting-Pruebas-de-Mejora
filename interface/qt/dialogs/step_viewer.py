"""Visor STEP experimental (OCCT + VTK) — pestaña NESTING."""
from __future__ import annotations

from interface.qt.ui_scale import fit_window

import math
import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut, QSurfaceFormat
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from interface.qt.theme import (
    COLOR_GRIS_DARK,
    COLOR_TEXTO_TITULO,
    apply_push_button,
    surface_dialog_stylesheet,
)
from interface.qt.widgets.cad_nav_overlay import (
    AxesTriadWidget,
    ViewCubeWidget,
    face_normal,
    face_view_up,
    stable_view_up,
)

_REPO = Path(__file__).resolve().parents[3]
_CAD_OCCT = _REPO / "CAD (OCCT)"
if str(_CAD_OCCT) not in sys.path:
    sys.path.insert(0, str(_CAD_OCCT))

_COPPER = (0.72, 0.42, 0.20)
_STEEL = (0.52, 0.54, 0.56)
_ALUMINUM = (0.68, 0.70, 0.74)
_DEFAULT = (0.58, 0.60, 0.63)

# Igual que ViewCube / Inventor nest: FRONT = cara de la chapa (+Z)
_FACE_N = {
    "FRONT": (0.0, 0.0, 1.0),
    "BACK": (0.0, 0.0, -1.0),
    "RIGHT": (1.0, 0.0, 0.0),
    "LEFT": (-1.0, 0.0, 0.0),
    "TOP": (0.0, 1.0, 0.0),
    "BOTTOM": (0.0, -1.0, 0.0),
}


def _norm(v):
    L = math.sqrt(max(1e-18, v[0] * v[0] + v[1] * v[1] + v[2] * v[2]))
    return (v[0] / L, v[1] / L, v[2] / L)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _add(*vecs):
    x = y = z = 0.0
    for v in vecs:
        x += v[0]
        y += v[1]
        z += v[2]
    return (x, y, z)


def _rodrigues(v, axis, degrees: float):
    """Rota vector v alrededor de axis (grados)."""
    ax = _norm(axis)
    rad = math.radians(degrees)
    c, s = math.cos(rad), math.sin(rad)
    dot = v[0] * ax[0] + v[1] * ax[1] + v[2] * ax[2]
    cr = _cross(ax, v)
    return (
        v[0] * c + cr[0] * s + ax[0] * dot * (1.0 - c),
        v[1] * c + cr[1] * s + ax[1] * dot * (1.0 - c),
        v[2] * c + cr[2] * s + ax[2] * dot * (1.0 - c),
    )


def nearest_orientation(
    look_target,
    up_standard,
    look_cur,
    up_cur,
    *,
    steps: int = 4,
):
    """
    Como FreeCAD NaviCube getNearestOrientation (rotateToNearest):
    1) alinea el look a la cara/arista/esquina (arco mínimo)
    2) elige el roll (0/90/180/270°) más cercano al up actual
    Así el recorrido cara→arista→cara no “reorienta” al up canónico fijo.
    """
    look_t = _norm(look_target)
    up_std = stable_view_up(look_t, up_standard)
    look0 = _norm(look_cur)
    up0 = stable_view_up(look0, up_cur)

    d = max(-1.0, min(1.0, look0[0] * look_t[0] + look0[1] * look_t[1] + look0[2] * look_t[2]))
    axis = _cross(look0, look_t)
    if axis[0] * axis[0] + axis[1] * axis[1] + axis[2] * axis[2] < 1e-12:
        if d > 0.0:
            up_mid = up0
        else:
            # 180°: girar alrededor de un eje ⊥ a look
            axis = _norm(_cross(look0, up0))
            if axis[0] * axis[0] + axis[1] * axis[1] + axis[2] * axis[2] < 1e-12:
                axis = _norm(_cross(look0, (1.0, 0.0, 0.0)))
            up_mid = _norm(_rodrigues(up0, axis, 180.0))
    else:
        ang = math.degrees(math.acos(d))
        up_mid = _norm(_rodrigues(up0, _norm(axis), ang))
    up_mid = stable_view_up(look_t, up_mid, current_up=up_mid)

    best_up = up_std
    best_dot = -2.0
    step = 360.0 / max(1, steps)
    for i in range(steps):
        cand = _norm(_rodrigues(up_std, look_t, i * step))
        dd = cand[0] * up_mid[0] + cand[1] * up_mid[1] + cand[2] * up_mid[2]
        if dd > best_dot:
            best_dot = dd
            best_up = cand
    return look_t, best_up


def _lerp(a, b, t):
    return a + (b - a) * t

def _lerp3(a, b, t):
    return (_lerp(a[0], b[0], t), _lerp(a[1], b[1], t), _lerp(a[2], b[2], t))


def _slerp_dir(a, b, t):
    """Interpolación de direcciones unitarias (corta)."""
    a = _norm(a)
    b = _norm(b)
    d = max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]))
    if d > 0.9995:
        return _norm(_lerp3(a, b, t))
    if d < -0.9995:
        axis = _cross(a, (0.0, 1.0, 0.0))
        if axis[0] * axis[0] + axis[1] * axis[1] + axis[2] * axis[2] < 1e-8:
            axis = _cross(a, (1.0, 0.0, 0.0))
        mid = _norm(axis)
        if t < 0.5:
            return _slerp_dir(a, mid, t * 2.0)
        return _slerp_dir(mid, b, (t - 0.5) * 2.0)
    omega = math.acos(d)
    s = math.sin(omega)
    w1 = math.sin((1.0 - t) * omega) / s
    w2 = math.sin(t * omega) / s
    return _norm((a[0] * w1 + b[0] * w2, a[1] * w1 + b[1] * w2, a[2] * w1 + b[2] * w2))


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def resolve_display_color(path: str) -> tuple[float, float, float]:
    """
    Color del actor:
    1) COLOUR_RGB de superficie en el STEP (si existe; ignora marcas casi negras)
    2) heurística por nombre/ruta (solo cobre/aluminio claros)
    3) gris neutro — NUNCA cobre por defecto
    """
    p = Path(path)
    colour_re = re.compile(
        r"COLOUR_RGB\(\s*'[^']*'\s*,\s*([0-9.E+-]+)\s*,\s*([0-9.E+-]+)\s*,\s*([0-9.E+-]+)",
        flags=re.I,
    )

    def _pick_from_text(text: str) -> tuple[float, float, float] | None:
        # Preferir el color más “cobre/cálido” entre candidatos no-oscuros
        # (FreeCAD escribe cobre + gris de marca; el cobre suele estar al final).
        best = None
        best_score = -1.0
        for m in colour_re.finditer(text):
            r, g, b = float(m.group(1)), float(m.group(2)), float(m.group(3))
            if r + g + b < 0.35:  # marcas / casi negro
                continue
            # Cobre FreeCAD ≈ (0.78, 0.48, 0.22): R alto, B bajo
            warm = (r - b) + 0.35 * (r - g)
            score = warm if warm > 0.12 else (r + g + b) * 0.05
            if score > best_score:
                best_score = score
                best = (r, g, b)
        return best

    try:
        size = p.stat().st_size
        chunks: list[str] = []
        with p.open("rb") as fh:
            # Cabecera
            chunks.append(fh.read(min(512_000, size)).decode("utf-8", errors="ignore"))
            # COLOUR_RGB de FreeCAD suele ir al FINAL del STEP (tras la geometría)
            if size > 512_000:
                tail = min(1_500_000, size)
                fh.seek(max(0, size - tail))
                chunks.append(fh.read(tail).decode("utf-8", errors="ignore"))
        picked = _pick_from_text("\n".join(chunks))
        if picked is not None:
            return picked
    except Exception:
        pass

    name = p.name.upper()
    full = str(p).upper().replace("\\", "/")
    # Señales fuertes de cobre (ruta completa: NESTEOS DE COBRE/STEP/…)
    if any(
        k in name or k in full
        for k in (
            "RTZCU",
            "COPPER",
            "COBRE",
            "NESTING_0.25_RTZ",
            " LARGOS CU",
            "LARGOS_CU",
            "NESTEOS DE COBRE",
        )
    ):
        return _COPPER
    if name.startswith("CU_") or name.startswith("CU-") or "/CU/" in full:
        return _COPPER
    if "ALUM" in name or "ALUMIN" in name or "/ALUM" in full:
        return _ALUMINUM
    if any(k in name for k in ("A36", "STEEL", "ACERO", "SS316", "INOX", "GALV")):
        return _STEEL
    return _DEFAULT


class _LoadStepWorker(QThread):
    finished_ok = Signal(object, str)
    finished_err = Signal(str, str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            from engine.step_io import load_step_display

            # Sin aristas de medición aquí: se cargan al pulsar M (más rápido / no congela).
            data = load_step_display(
                self.path, deflection=0.10, edge_deflection=0.04, include_measure=False
            )
            if data.n_tris <= 0 and data.n_mark_segs <= 0:
                raise RuntimeError("STEP sin geometría mallable ni marcaje.")
            if data.n_tris <= 0:
                raise RuntimeError("STEP sin caras mallables (¿solo aristas/MARK?).")
            self.finished_ok.emit(data, self.path)
        except Exception as exc:
            self.finished_err.emit(str(exc), self.path)


class _LoadMeasureEdgesWorker(QThread):
    finished_ok = Signal(object, object, float)  # edges, faces, to_inch_factor
    finished_err = Signal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            from engine.step_io import load_measure_data_for_path

            edges, faces, to_inch = load_measure_data_for_path(
                self.path, deflection=0.04
            )
            self.finished_ok.emit(edges, faces, float(to_inch))
        except Exception as exc:
            self.finished_err.emit(str(exc))


class StepViewerDialog(QDialog):
    """Lista STEPs + VTK + ViewCube Qt (caras/aristas/esquinas) + tríada XYZ."""

    def __init__(
        self,
        parent=None,
        *,
        initial_files: list[Path] | None = None,
        context: str = "",
        app=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Ver STEP — OCCT (experimental)")
        # Ventana normal (min/max/cerrar), no diálogo fijo
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        fit_window(self, 1280, 800)
        self.setStyleSheet(surface_dialog_stylesheet())

        self._app = app
        self._worker: _LoadStepWorker | None = None
        self._measure_worker: _LoadMeasureEdgesWorker | None = None
        self._actor = None
        self._edge_actor = None
        self._mark_actor = None
        self._vtk_ready = False
        self._view_cube: ViewCubeWidget | None = None
        self._triad: AxesTriadWidget | None = None
        self._current_path = ""
        self._cam_anim: QTimer | None = None
        self._cam_anim_state = None
        self._vtk_obs_ids: list[int] = []
        self._nav_sync_timer = QTimer(self)
        self._nav_sync_timer.setInterval(33)  # ~30 Hz basta para ViewCube/tríada
        self._nav_sync_timer.timeout.connect(self._sync_nav_overlays)
        self._pending_recenter = False
        self._syncing_nav = False
        # Medición (tecla M) — aristas + caras B-Rep en pulgadas
        self._measure_mode = False
        self._measure_edges: list = []
        self._measure_faces: list = []
        # Selecciones: ("e", edge_id) | ("f", face_id)
        self._measure_selected: list[tuple[str, int]] = []
        self._measure_pick_actor = None
        self._measure_pick_poly = None
        self._measure_hl_actors: list = []
        self._measure_dim_actors: list = []
        self._measure_label_actors: list = []
        self._measure_to_inch: float = 1.0 / 25.4  # FreeCAD STEP suele ser mm

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.lbl_ctx = QLabel(context or "Visor 3D experimental (OCCT). No usa FreeCAD.")
        self.lbl_ctx.setWordWrap(True)
        self.lbl_ctx.setStyleSheet(f"color:{COLOR_TEXTO_TITULO};font-weight:600;")
        root.addWidget(self.lbl_ctx)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)

        self.lista = QListWidget()
        self.lista.setMinimumWidth(280)
        self.lista.currentItemChanged.connect(self._on_select)
        left_lay.addWidget(self.lista, 1)

        row = QHBoxLayout()
        self.btn_abrir = QPushButton("Abrir STEP…")
        apply_push_button(self.btn_abrir, COLOR_GRIS_DARK, font_size=11, padding="6px 10px")
        self.btn_abrir.setToolTip("Abrir un STEP concreto desde disco")
        self.btn_abrir.clicked.connect(self._abrir_archivo)
        row.addWidget(self.btn_abrir)
        self.btn_nest_steps = QPushButton("STEPS de W.O.")
        apply_push_button(self.btn_nest_steps, COLOR_GRIS_DARK, font_size=11, padding="6px 10px")
        self.btn_nest_steps.setToolTip(
            "Elige Local/Remoto y la W.O. del job para cargar sus STEP"
        )
        self.btn_nest_steps.clicked.connect(self._cargar_steps_wo)
        row.addWidget(self.btn_nest_steps)
        self.btn_swo_steps = QPushButton("STEPS de S.W.O.")
        apply_push_button(self.btn_swo_steps, COLOR_GRIS_DARK, font_size=11, padding="6px 10px")
        self.btn_swo_steps.setToolTip(
            "Elige Local/Remoto y una S.W.O. con STEP (export o despachador nocturno)"
        )
        self.btn_swo_steps.clicked.connect(self._cargar_steps_swo)
        row.addWidget(self.btn_swo_steps)
        left_lay.addLayout(row)

        # Botón Medir (Inventor: tecla M) — visible aunque VTK robe el foco
        self.btn_measure = QPushButton("Medir (M)")
        apply_push_button(self.btn_measure, COLOR_GRIS_DARK, font_size=11, padding="6px 10px")
        self.btn_measure.setToolTip(
            "Modo medición estilo Inventor: aristas / círculos en pulgadas (tecla M)"
        )
        self.btn_measure.setCheckable(True)
        self.btn_measure.clicked.connect(self._on_measure_button)
        left_lay.addWidget(self.btn_measure)

        # Panel Measure (como Inventor)
        self._measure_panel = QFrame()
        self._measure_panel.setVisible(False)
        self._measure_panel.setStyleSheet(
            "QFrame{background:#1E293B;border:1px solid #334155;border-radius:6px;}"
            "QLabel{color:#E2E8F0;}"
        )
        mp_lay = QVBoxLayout(self._measure_panel)
        mp_lay.setContentsMargins(8, 8, 8, 8)
        mp_lay.setSpacing(4)
        title = QLabel("Measure")
        title.setStyleSheet("font-weight:700;font-size:13px;color:#F8FAFC;")
        mp_lay.addWidget(title)
        self.lbl_measure_hint = QLabel("Selecciona arista, círculo o cara…")
        self.lbl_measure_hint.setWordWrap(True)
        self.lbl_measure_hint.setStyleSheet("color:#94A3B8;font-size:11px;")
        mp_lay.addWidget(self.lbl_measure_hint)
        self.lbl_measure_sel1 = QLabel("Selection 1: —")
        self.lbl_measure_sel1.setWordWrap(True)
        mp_lay.addWidget(self.lbl_measure_sel1)
        self.lbl_measure_sel2 = QLabel("Selection 2: —")
        self.lbl_measure_sel2.setWordWrap(True)
        mp_lay.addWidget(self.lbl_measure_sel2)
        self.lbl_measure_result = QLabel("Result: —")
        self.lbl_measure_result.setWordWrap(True)
        self.lbl_measure_result.setStyleSheet("font-weight:600;color:#FDE68A;")
        mp_lay.addWidget(self.lbl_measure_result)
        btn_done = QPushButton("Done")
        apply_push_button(btn_done, COLOR_GRIS_DARK, font_size=11, padding="5px 10px")
        btn_done.clicked.connect(self._exit_measure_mode)
        mp_lay.addWidget(btn_done)
        left_lay.addWidget(self._measure_panel)

        split.addWidget(left)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(4)

        self.lbl_status = QLabel("Selecciona un STEP o ábrelo desde disco.")
        self.lbl_status.setStyleSheet("color:#94A3B8;")
        right_lay.addWidget(self.lbl_status)

        self._vtk_host = QWidget()
        self._vtk_host.setMinimumSize(640, 480)
        vtk_lay = QVBoxLayout(self._vtk_host)
        vtk_lay.setContentsMargins(0, 0, 0, 0)

        try:
            import vtk
            from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

            # MSAA: hay que pedir samples a Qt ANTES de crear el QVTK
            try:
                fmt = QSurfaceFormat()
                fmt.setDepthBufferSize(24)
                fmt.setStencilBufferSize(8)
                fmt.setSamples(8)
                fmt.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
                QSurfaceFormat.setDefaultFormat(fmt)
            except Exception:
                pass

            self._vtk = vtk
            self.vtk_widget = QVTKRenderWindowInteractor(self._vtk_host)
            try:
                self.vtk_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            except Exception:
                pass
            vtk_lay.addWidget(self.vtk_widget)
            self.renderer = vtk.vtkRenderer()
            self.renderer.SetBackground(0.14, 0.14, 0.14)
            self.renderer.SetBackground2(0.28, 0.28, 0.28)
            try:
                self.renderer.GradientBackgroundOn()
            except Exception:
                pass
            rw = self.vtk_widget.GetRenderWindow()
            rw.AddRenderer(self.renderer)
            self._enable_antialiasing(rw)
            self.iren = rw.GetInteractor()
            # Estilo CAD propio: zoom FreeCAD + medición M (sin SetAbortFlag, no existe aquí)
            self._cad_style = self._make_cad_interactor_style()
            self.iren.SetInteractorStyle(self._cad_style)
            # Ortográfica estilo Inventor/FreeCAD (sin “lente” perspectiva)
            try:
                cam0 = self.renderer.GetActiveCamera()
                cam0.ParallelProjectionOn()
            except Exception:
                pass
            self._setup_cad_lights()
            self._vtk_ready = True
        except Exception as exc:
            self.vtk_widget = None
            self.renderer = None
            self.iren = None
            err = QLabel(
                f"No se pudo iniciar VTK/QVTK:\n{exc}\n\n"
                "Revisa que cadquery-ocp / vtk estén instalados."
            )
            err.setWordWrap(True)
            err.setStyleSheet("color:#FCA5A5;")
            vtk_lay.addWidget(err)

        right_lay.addWidget(self._vtk_host, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        btn_cerrar = QPushButton("Cerrar")
        apply_push_button(btn_cerrar, COLOR_GRIS_DARK, font_size=11, padding="6px 14px")
        btn_cerrar.clicked.connect(self.accept)
        footer.addWidget(btn_cerrar)
        root.addLayout(footer)

        if initial_files:
            self.set_files(initial_files)

        if self._vtk_ready and self.vtk_widget is not None:
            self.vtk_widget.Initialize()
            self.vtk_widget.Start()
            self._install_nav_overlays()
            try:
                self.vtk_widget.installEventFilter(self)
            except Exception:
                pass

        # Atajos de ventana: M funciona aunque el foco esté en VTK (como Inventor)
        self._sc_measure = QShortcut(QKeySequence(Qt.Key.Key_M), self)
        self._sc_measure.setContext(Qt.ShortcutContext.WindowShortcut)
        self._sc_measure.activated.connect(self._toggle_measure_mode)
        self._sc_measure_esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._sc_measure_esc.setContext(Qt.ShortcutContext.WindowShortcut)
        self._sc_measure_esc.activated.connect(self._on_escape_shortcut)

    def _install_nav_overlays(self) -> None:
        """
        Overlays como ventanas Tool sobre el diálogo (VTK HWND no las tapa)
        + sync continuo con la cámara al orbitar con el mouse.
        """
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )

        self._view_cube = ViewCubeWidget(self)
        self._view_cube.setWindowFlags(flags)
        self._view_cube.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._view_cube.set_camera_provider(self._camera_basis)
        self._view_cube.viewClicked.connect(self._on_viewcube)

        self._triad = AxesTriadWidget(self)
        self._triad.setWindowFlags(flags)
        self._triad.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._triad.set_camera_provider(self._camera_basis)

        self._vtk_host.installEventFilter(self)
        self.installEventFilter(self)
        self._place_nav_overlays()
        self._view_cube.show()
        self._triad.show()
        self._view_cube.raise_()
        self._triad.raise_()

        # VTK: refrescar overlays mientras se orbita / pan / zoom
        try:
            vtk = self._vtk

            def _on_vtk_interact(obj=None, event=None):
                self._sync_nav_overlays()

            for ev in (
                "InteractionEvent",
                "EndInteractionEvent",
                "MouseWheelForwardEvent",
                "MouseWheelBackwardEvent",
            ):
                try:
                    oid = self.iren.AddObserver(ev, _on_vtk_interact)
                    self._vtk_obs_ids.append(oid)
                except Exception:
                    pass
            # No observar Camera ModifiedEvent ni MouseMove: generan tormentas
            # de callbacks y pueden congelar el UI (Ctrl+C / KeyboardInterrupt).
        except Exception:
            pass

        self._nav_sync_timer.start()

    def _sync_nav_overlays(self) -> None:
        if getattr(self, "_syncing_nav", False):
            return
        self._syncing_nav = True
        try:
            if self._view_cube is not None:
                self._view_cube.sync_from_camera()
            if self._triad is not None:
                self._triad.sync_from_camera()
        finally:
            self._syncing_nav = False

    def eventFilter(self, obj, event):
        # Clic Qt en el viewport (más fiable que solo el estilo VTK con QVTK)
        if (
            self._measure_mode
            and self.vtk_widget is not None
            and obj is self.vtk_widget
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            try:
                # Qt: origen arriba-izquierda → VTK: origen abajo-izquierda
                pos = event.position() if hasattr(event, "position") else event.pos()
                qx = float(pos.x())
                qy = float(pos.y())
                ww = max(1, int(self.vtk_widget.width()))
                wh = max(1, int(self.vtk_widget.height()))
                rw_w, rw_h = self.renderer.GetSize() if self.renderer else (ww, wh)
                sx = float(rw_w) / float(ww)
                sy = float(rw_h) / float(wh)
                vx = int(qx * sx)
                vy = int((wh - qy - 1.0) * sy)
                eid = self._pick_measure_entity_at(vx, vy)
                if eid is not None:
                    self._select_measure_entity(eid)
                else:
                    self.lbl_status.setText(
                        "Medición: sin arista/cara cercana — acerca el zoom y reintenta."
                    )
                return True  # consumir: no orbitar
            except Exception as exc:
                self.lbl_status.setText(f"Medición: error de pick ({exc})")
                return True
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_M:
                self._toggle_measure_mode()
                return True
            if key == Qt.Key.Key_Escape and self._measure_mode:
                self._exit_measure_mode()
                return True
        if event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Move,
            QEvent.Type.Show,
        ):
            self._place_nav_overlays()
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._place_nav_overlays()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_nav_overlays()

    def _place_nav_overlays(self) -> None:
        if not self._vtk_host.isVisible() or not self.isVisible():
            return
        # Esquina superior derecha / inferior izquierda del viewport VTK (coords globales)
        top_left = self._vtk_host.mapToGlobal(QPoint(0, 0))
        if self._view_cube is not None:
            m = 12
            self._view_cube.move(
                top_left.x() + self._vtk_host.width() - self._view_cube.width() - m,
                top_left.y() + m,
            )
            if not self._view_cube.isVisible():
                self._view_cube.show()
            self._view_cube.raise_()
        if self._triad is not None:
            m = 10
            self._triad.move(
                top_left.x() + m,
                top_left.y() + self._vtk_host.height() - self._triad.height() - m,
            )
            if not self._triad.isVisible():
                self._triad.show()
            self._triad.raise_()

    def _camera_basis(self):
        cam = self.renderer.GetActiveCamera()
        pos = cam.GetPosition()
        fp = cam.GetFocalPoint()
        up = cam.GetViewUp()
        fwd = _norm((fp[0] - pos[0], fp[1] - pos[1], fp[2] - pos[2]))
        right = _norm(_cross(fwd, up))
        up_n = _norm(_cross(right, fwd))
        return fwd, right, up_n

    def _make_cad_interactor_style(self):
        """Trackball + zoom al cursor + clic medir (compatible vtkGeneric… sin AbortFlag)."""
        vtk = self._vtk
        owner = self

        class _CadStyle(vtk.vtkInteractorStyleTrackballCamera):
            def OnMouseWheelForward(self):  # noqa: N802
                owner._zoom_toward_cursor_freecad(True)

            def OnMouseWheelBackward(self):  # noqa: N802
                owner._zoom_toward_cursor_freecad(False)

            def OnLeftButtonDown(self):  # noqa: N802
                # En modo Measure (Inventor): clic izquierdo SOLO selecciona, nunca orbita.
                if owner._measure_mode:
                    iren = self.GetInteractor()
                    if iren is not None:
                        try:
                            x, y = iren.GetEventPosition()
                            self.FindPokedRenderer(int(x), int(y))
                        except Exception:
                            pass
                    eid = owner._pick_measure_entity()
                    if eid is not None:
                        owner._select_measure_entity(eid)
                    else:
                        owner.lbl_status.setText(
                            "Medición: sin arista/cara cercana — acerca el zoom y reintenta."
                        )
                    return
                super().OnLeftButtonDown()

            def OnKeyPress(self):  # noqa: N802
                iren = self.GetInteractor()
                sym = ""
                try:
                    sym = str(iren.GetKeySym() or "") if iren else ""
                except Exception:
                    pass
                low = sym.lower()
                if low == "m":
                    owner._toggle_measure_mode()
                    return
                if low == "escape" and owner._measure_mode:
                    owner._exit_measure_mode()
                    return
                super().OnKeyPress()

        style = _CadStyle()
        return style

    def _ensure_orthographic(self) -> None:
        if not self._vtk_ready or self.renderer is None:
            return
        try:
            self.renderer.GetActiveCamera().ParallelProjectionOn()
        except Exception:
            pass

    def _event_pos_normalized(self) -> tuple[float, float]:
        """Coords normalizadas [0,1] estilo FreeCAD (VTK: Y desde abajo)."""
        x, y = self.iren.GetEventPosition()
        size = self.renderer.GetSize()
        w = float(size[0] or 1)
        h = float(size[1] or 1)
        return (float(x) / w, float(y) / h)

    def _display_to_world_focal(self, x: float, y: float) -> tuple[float, float, float]:
        """Punto mundo bajo display (x,y) en el plano del focal (Coin panplane)."""
        ren = self.renderer
        cam = ren.GetActiveCamera()
        fp = cam.GetFocalPoint()
        ren.SetWorldPoint(float(fp[0]), float(fp[1]), float(fp[2]), 1.0)
        ren.WorldToDisplay()
        _dx, _dy, z = ren.GetDisplayPoint()
        ren.SetDisplayPoint(float(x), float(y), float(z))
        ren.DisplayToWorld()
        wx, wy, wz, w = ren.GetWorldPoint()
        if abs(w) > 1e-12:
            return (wx / w, wy / w, wz / w)
        return (wx, wy, wz)

    def _norm_to_world_focal(self, nx: float, ny: float) -> tuple[float, float, float]:
        size = self.renderer.GetSize()
        w = float(size[0] or 1)
        h = float(size[1] or 1)
        return self._display_to_world_focal(nx * w, ny * h)

    def _pan_camera_freecad(
        self,
        curr_norm: tuple[float, float],
        prev_norm: tuple[float, float],
    ) -> None:
        """
        Equivalente a FreeCAD NavigationStyle::panCamera:
        position -= (world(curr) - world(prev)) sobre el plano focal.
        En VTK hay que mover también el FocalPoint para no cambiar el look.
        """
        if abs(curr_norm[0] - prev_norm[0]) < 1e-12 and abs(curr_norm[1] - prev_norm[1]) < 1e-12:
            return
        p_curr = self._norm_to_world_focal(*curr_norm)
        p_prev = self._norm_to_world_focal(*prev_norm)
        dx = p_curr[0] - p_prev[0]
        dy = p_curr[1] - p_prev[1]
        dz = p_curr[2] - p_prev[2]
        cam = self.renderer.GetActiveCamera()
        pos = cam.GetPosition()
        fp = cam.GetFocalPoint()
        cam.SetPosition(pos[0] - dx, pos[1] - dy, pos[2] - dz)
        cam.SetFocalPoint(fp[0] - dx, fp[1] - dy, fp[2] - dz)

    def _on_mouse_wheel(self, zoom_in: bool) -> None:
        """Compat: el estilo CAD llama directo a _zoom_toward_cursor_freecad."""
        self._zoom_toward_cursor_freecad(zoom_in)

    def _zoom_toward_cursor_freecad(self, zoom_in: bool) -> None:
        """
        FreeCAD NavigationStyle::doZoom (1.0.0):
          1) pan cursor → centro
          2) height *= exp(±zoomStep)   (zoomStep por defecto 0.2)
          3) pan centro → cursor
        """
        if not self._vtk_ready or self.renderer is None or self.iren is None:
            return
        self._ensure_orthographic()
        cam = self.renderer.GetActiveCamera()
        pos_n = self._event_pos_normalized()
        center = (0.5, 0.5)
        # FreeCAD: zoomIn → logfactor negativo → height más pequeño
        zoom_step = 0.2
        logfactor = -zoom_step if zoom_in else zoom_step

        # 1) Llevar el punto bajo el cursor al centro
        self._pan_camera_freecad(center, pos_n)
        # 2) Escalar FOV ortográfico (ParallelScale ≈ height/2 de Coin)
        mult = math.exp(logfactor)
        scale = float(cam.GetParallelScale() or 1.0) * mult
        cam.SetParallelScale(max(1e-6, min(scale, 1e7)))
        # 3) Devolver el centro al cursor
        self._pan_camera_freecad(pos_n, center)

        self.renderer.ResetCameraClippingRange()
        self.vtk_widget.GetRenderWindow().Render()
        self._sync_nav_overlays()

    # ── Medición (Inventor-like, tecla M) ─────────────────────────────

    @staticmethod
    def _fmt_in(val_model: float, to_inch: float = 1.0) -> str:
        """val_model en unidades del STEP → pulgadas reales."""
        inches = float(val_model) * float(to_inch)
        return f"{inches:.4f} in"

    def _inches(self, val_model: float) -> str:
        return self._fmt_in(val_model, self._measure_to_inch)

    def _inches_area(self, area_model: float) -> str:
        """Área modelo² → in²."""
        f = float(self._measure_to_inch)
        return f"{float(area_model) * f * f:.4f} in²"

    def _format_edge_measure(self, me) -> str:
        if me.kind == "circle" and me.is_full_circle and me.radius_in:
            return f"Ø {self._inches(me.radius_in * 2.0)}"
        if me.kind in ("arc", "circle") and me.radius_in:
            return f"R {self._inches(me.radius_in)}"
        return f"L {self._inches(me.length_in)}"

    def _inventor_dim_text(self, me) -> str:
        """Texto de cota como en Inventor (viewport)."""
        to_in = float(self._measure_to_inch)
        if me.kind == "circle" and me.is_full_circle and me.radius_in:
            d = float(me.radius_in) * 2.0 * to_in
            return f"{d:.4f} in Diameter"
        if me.kind in ("arc", "circle") and me.radius_in:
            r = float(me.radius_in) * to_in
            return f"{r:.4f} in Radius"
        L = float(me.length_in) * to_in
        return f"{L:.4f} in"

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_M:
            self._toggle_measure_mode()
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self._measure_mode:
            self._exit_measure_mode()
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_escape_shortcut(self) -> None:
        if self._measure_mode:
            self._exit_measure_mode()

    def _on_measure_button(self, checked: bool = False) -> None:
        # El botón es checkable; sincronizar con el modo real
        if bool(checked) and not self._measure_mode:
            self._enter_measure_mode()
        elif (not bool(checked)) and self._measure_mode:
            self._exit_measure_mode()
        else:
            # Qt puede emitir con estado desfasado; forzar toggle coherente
            self.btn_measure.blockSignals(True)
            self.btn_measure.setChecked(self._measure_mode)
            self.btn_measure.blockSignals(False)

    def _update_measure_panel(self) -> None:
        if not hasattr(self, "_measure_panel"):
            return
        if not self._measure_mode and not (
            self._measure_worker and self._measure_worker.isRunning()
        ):
            self._measure_panel.setVisible(False)
            if hasattr(self, "btn_measure"):
                self.btn_measure.blockSignals(True)
                self.btn_measure.setChecked(False)
                self.btn_measure.blockSignals(False)
            return

        self._measure_panel.setVisible(True)
        if hasattr(self, "btn_measure"):
            self.btn_measure.blockSignals(True)
            self.btn_measure.setChecked(True)
            self.btn_measure.blockSignals(False)

        if self._measure_worker and self._measure_worker.isRunning():
            self.lbl_measure_hint.setText("Indexando aristas y caras B-Rep…")
            self.lbl_measure_sel1.setText("Selection 1: —")
            self.lbl_measure_sel2.setText("Selection 2: —")
            self.lbl_measure_result.setText("Result: —")
            return

        n_e = len(self._measure_edges)
        n_f = len(self._measure_faces)
        self.lbl_measure_hint.setText(
            f"Clic en arista / círculo / cara ({n_e} aristas, {n_f} caras). Esc o Done para salir."
        )

        def _sel_text(sel: tuple[str, int] | None, slot: int) -> str:
            if sel is None:
                return f"Selection {slot}: —"
            kind, idx = sel
            if kind == "f":
                if idx < 0 or idx >= len(self._measure_faces):
                    return f"Selection {slot}: —"
                mf = self._measure_faces[idx]
                fkind = {"plane": "Face (Plane)", "cylinder": "Face (Cylinder)"}.get(
                    mf.kind, "Face"
                )
                extra = f"\n  Area: {self._inches_area(mf.area)}"
                if mf.kind == "cylinder" and mf.radius_in:
                    extra += f"\n  Radius: {self._inches(mf.radius_in)}"
                return f"Selection {slot} ({fkind}):{extra}"
            if idx < 0 or idx >= len(self._measure_edges):
                return f"Selection {slot}: —"
            me = self._measure_edges[idx]
            ekind = {
                "line": "Edge",
                "circle": "Circle",
                "arc": "Arc",
            }.get(me.kind, "Edge")
            extra = ""
            if me.kind in ("circle", "arc") and me.radius_in:
                if me.is_full_circle:
                    extra = (
                        f"\n  Diameter: {self._inches(me.radius_in * 2)}\n"
                        f"  Radius: {self._inches(me.radius_in)}\n"
                        f"  Length: {self._inches(me.length_in)}"
                    )
                else:
                    extra = (
                        f"\n  Radius: {self._inches(me.radius_in)}\n"
                        f"  Length: {self._inches(me.length_in)}"
                    )
            else:
                extra = f"\n  Length: {self._inches(me.length_in)}"
            return f"Selection {slot} ({ekind}):{extra}"

        s = self._measure_selected
        self.lbl_measure_sel1.setText(_sel_text(s[0] if len(s) >= 1 else None, 1))
        self.lbl_measure_sel2.setText(_sel_text(s[1] if len(s) >= 2 else None, 2))

        if len(s) == 2:
            a = self._resolve_measure_sel(s[0])
            b = self._resolve_measure_sel(s[1])
            p0, p1, d_draw, mode = self._measure_pair_anchors(a, b)
            d_min = d_draw
            try:
                from engine.step_io import shape_min_distance

                sa = self._entity_brep(a)
                sb = self._entity_brep(b)
                info = shape_min_distance(sa, sb) if sa is not None and sb is not None else None
                if info is not None:
                    d_min = info[0]
            except Exception:
                pass

            if mode == "center":
                self.lbl_measure_result.setText(
                    f"Center Distance: {self._inches(d_draw)}\n"
                    f"Minimum Distance: {self._inches(d_min)}"
                )
            elif mode == "center_edge":
                self.lbl_measure_result.setText(
                    f"Center to Edge: {self._inches(d_draw)}\n"
                    f"Minimum Distance: {self._inches(d_min)}"
                )
            elif mode == "face_face":
                self.lbl_measure_result.setText(
                    f"Face Distance: {self._inches(d_draw)}"
                )
            elif mode == "face_entity":
                self.lbl_measure_result.setText(
                    f"Distance: {self._inches(d_draw)}"
                )
            else:
                self.lbl_measure_result.setText(
                    f"Minimum Distance: {self._inches(d_draw)}"
                )
        elif len(s) == 1:
            ent = self._resolve_measure_sel(s[0])
            if s[0][0] == "f":
                self.lbl_measure_result.setText(
                    f"Face ({ent.kind})  Area: {self._inches_area(ent.area)}"
                )
            else:
                self.lbl_measure_result.setText(self._format_edge_measure(ent))
        else:
            self.lbl_measure_result.setText("Result: —")

    def _toggle_measure_mode(self) -> None:
        if self._measure_mode or (
            self._measure_worker and self._measure_worker.isRunning()
        ):
            self._exit_measure_mode()
        else:
            self._enter_measure_mode()

    def _enter_measure_mode(self) -> None:
        if not self._vtk_ready:
            self.lbl_status.setText("Medición: VTK no está listo.")
            return
        if not self._current_path:
            self.lbl_status.setText("Medición: carga un STEP primero (lista o Abrir STEP…).")
            QMessageBox.information(
                self,
                "Measure",
                "Primero carga un STEP en el visor.\n\n"
                "Luego pulsa M o el botón «Medir (M)» para seleccionar aristas o caras.",
            )
            if hasattr(self, "btn_measure"):
                self.btn_measure.blockSignals(True)
                self.btn_measure.setChecked(False)
                self.btn_measure.blockSignals(False)
            return
        if self._measure_worker and self._measure_worker.isRunning():
            self.lbl_status.setText("Medición: preparando aristas/caras…")
            self._update_measure_panel()
            return
        if not self._measure_edges and not self._measure_faces:
            self.lbl_status.setText("Medición: indexando aristas y caras B-Rep…")
            self._update_measure_panel()
            self._measure_worker = _LoadMeasureEdgesWorker(self._current_path, self)
            self._measure_worker.finished_ok.connect(self._on_measure_edges_ok)
            self._measure_worker.finished_err.connect(self._on_measure_edges_err)
            self._measure_worker.start()
            # Mostrar panel ya en “preparando”
            self._measure_panel.setVisible(True)
            self.btn_measure.blockSignals(True)
            self.btn_measure.setChecked(True)
            self.btn_measure.blockSignals(False)
            self.lbl_measure_hint.setText("Indexando aristas y caras B-Rep…")
            return
        self._activate_measure_mode()

    def _on_measure_edges_ok(self, edges, faces=None, to_inch: float = 1.0 / 25.4) -> None:
        # Compat: Signal antiguo (edges, to_inch) no debería llegar; aceptar ambos.
        if isinstance(faces, (int, float)) and to_inch == 1.0 / 25.4:
            to_inch = float(faces)
            faces = []
        self._measure_edges = list(edges or [])
        self._measure_faces = list(faces or [])
        self._measure_to_inch = float(to_inch) if to_inch else (1.0 / 25.4)
        unit = "mm→in" if self._measure_to_inch < 0.1 else "in"
        if not self._measure_edges and not self._measure_faces:
            self.lbl_status.setText("Medición: este STEP no tiene aristas/caras medibles.")
            self._measure_panel.setVisible(False)
            self.btn_measure.blockSignals(True)
            self.btn_measure.setChecked(False)
            self.btn_measure.blockSignals(False)
            return
        self.lbl_status.setText(
            f"Medición: {len(self._measure_edges)} aristas, {len(self._measure_faces)} caras "
            f"(unidades STEP → in, {unit})."
        )
        self._activate_measure_mode()

    def _on_measure_edges_err(self, msg: str) -> None:
        self.lbl_status.setText(f"Medición: error al indexar — {msg}")
        self._measure_panel.setVisible(False)
        self.btn_measure.blockSignals(True)
        self.btn_measure.setChecked(False)
        self.btn_measure.blockSignals(False)

    def _activate_measure_mode(self) -> None:
        if not self._measure_edges and not self._measure_faces:
            self.lbl_status.setText("Medición: no hay geometría medible en este STEP.")
            return
        self._measure_mode = True
        self._measure_selected.clear()
        self._clear_measure_overlays()
        if self._measure_pick_actor is not None:
            try:
                self._measure_pick_actor.SetVisibility(0)
            except Exception:
                pass
        self.lbl_status.setText(
            f"Medición ON ({len(self._measure_edges)} aristas, {len(self._measure_faces)} caras) "
            "— clic en arista/círculo/cara."
        )
        self._update_measure_panel()
        try:
            if self.vtk_widget is not None:
                self.vtk_widget.setFocus(Qt.FocusReason.OtherFocusReason)
        except Exception:
            pass
        self.vtk_widget.GetRenderWindow().Render()

    def _exit_measure_mode(self) -> None:
        # Cancelar indexado en curso
        w = self._measure_worker
        if w is not None and w.isRunning():
            try:
                w.finished_ok.disconnect(self._on_measure_edges_ok)
                w.finished_err.disconnect(self._on_measure_edges_err)
            except Exception:
                pass
        self._measure_mode = False
        self._measure_selected.clear()
        self._clear_measure_overlays()
        if self._measure_pick_actor is not None:
            try:
                self._measure_pick_actor.SetVisibility(0)
            except Exception:
                pass
        self._update_measure_panel()
        base = os.path.basename(self._current_path) if self._current_path else "STEP"
        self.lbl_status.setText(f"{base} — medición OFF (M o «Medir»).")
        if self._vtk_ready and self.vtk_widget is not None:
            self.vtk_widget.GetRenderWindow().Render()

    def _clear_measure_overlays(self) -> None:
        if not self._vtk_ready or self.renderer is None:
            self._measure_hl_actors.clear()
            self._measure_dim_actors.clear()
            self._measure_label_actors.clear()
            return
        for lst in (
            self._measure_hl_actors,
            self._measure_dim_actors,
            self._measure_label_actors,
        ):
            for a in lst:
                try:
                    self.renderer.RemoveActor(a)
                except Exception:
                    pass
            lst.clear()

    def _build_measure_pick_actor(self, edges: list) -> None:
        """Actor pickable (líneas) con cell data edge_id; invisible salvo modo M."""
        vtk = self._vtk
        if self._measure_pick_actor is not None:
            try:
                self.renderer.RemoveActor(self._measure_pick_actor)
            except Exception:
                pass
            self._measure_pick_actor = None
            self._measure_pick_poly = None

        if not edges:
            return

        pts = vtk.vtkPoints()
        lines = vtk.vtkCellArray()
        edge_ids = vtk.vtkIntArray()
        edge_ids.SetName("edge_id")
        edge_ids.SetNumberOfComponents(1)

        for me in edges:
            pl = me.polyline or []
            if len(pl) < 2:
                continue
            ids = []
            for x, y, z in pl:
                ids.append(pts.InsertNextPoint(float(x), float(y), float(z)))
            lines.InsertNextCell(len(ids))
            for i in ids:
                lines.InsertCellPoint(i)
            edge_ids.InsertNextValue(int(me.id))

        poly = vtk.vtkPolyData()
        poly.SetPoints(pts)
        poly.SetLines(lines)
        poly.GetCellData().AddArray(edge_ids)
        poly.GetCellData().SetActiveScalars("edge_id")
        self._measure_pick_poly = poly

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(poly)
        mapper.ScalarVisibilityOff()
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetColor(0.2, 0.85, 1.0)
        prop.SetOpacity(0.0)  # invisible; solo picking
        prop.SetLineWidth(6.0)
        try:
            actor.SetPickable(1)
        except Exception:
            pass
        actor.SetVisibility(0)
        self.renderer.AddActor(actor)
        self._measure_pick_actor = actor

    def _pick_measure_edge(self) -> int | None:
        """Compat: solo arista."""
        ent = self._pick_measure_entity()
        if ent is not None and ent[0] == "e":
            return ent[1]
        return None

    def _pick_measure_entity(self) -> tuple[str, int] | None:
        if self.iren is None:
            return None
        x, y = self.iren.GetEventPosition()
        return self._pick_measure_entity_at(int(x), int(y))

    @staticmethod
    def _dist2_point_segment(
        px: float, py: float, pz: float,
        ax: float, ay: float, az: float,
        bx: float, by: float, bz: float,
    ) -> float:
        abx, aby, abz = bx - ax, by - ay, bz - az
        apx, apy, apz = px - ax, py - ay, pz - az
        ab2 = abx * abx + aby * aby + abz * abz
        if ab2 < 1e-18:
            return apx * apx + apy * apy + apz * apz
        t = max(0.0, min(1.0, (apx * abx + apy * aby + apz * abz) / ab2))
        dx = ax + t * abx - px
        dy = ay + t * aby - py
        dz = az + t * abz - pz
        return dx * dx + dy * dy + dz * dz

    def _model_pick_tolerance(self) -> float:
        """Tolerancia 3D (~Inventor): fracción del tamaño del modelo."""
        try:
            if self._actor is not None:
                b = self._actor.GetBounds()
                dx = abs(b[1] - b[0])
                dy = abs(b[3] - b[2])
                dz = abs(b[5] - b[4])
                diag = math.sqrt(dx * dx + dy * dy + dz * dz)
                return max(0.05, 0.04 * diag)
        except Exception:
            pass
        return 0.25

    @staticmethod
    def _bbox_dist(bbox, p) -> float:
        """Distancia de punto a AABB (0 si está dentro). OCCT: xmin,ymin,zmin,xmax,ymax,zmax."""
        if bbox is None:
            return 0.0
        x0, y0, z0, x1, y1, z1 = bbox
        dx = max(x0 - p[0], 0.0, p[0] - x1)
        dy = max(y0 - p[1], 0.0, p[1] - y1)
        dz = max(z0 - p[2], 0.0, p[2] - z1)
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def _nearest_edge_to_world_point(
        self,
        hit: tuple[float, float, float],
        *,
        max_dist: float,
    ) -> tuple[int | None, float]:
        """Arista B-Rep más cercana a un punto 3D (post raycast Inventor-like)."""
        hx, hy, hz = hit
        best_id: int | None = None
        best_d2 = float(max_dist) ** 2
        for me in self._measure_edges:
            pl = me.polyline or []
            if len(pl) < 2:
                continue
            xs = [p[0] for p in pl]
            ys = [p[1] for p in pl]
            zs = [p[2] for p in pl]
            pad = max_dist
            if (
                hx < min(xs) - pad
                or hx > max(xs) + pad
                or hy < min(ys) - pad
                or hy > max(ys) + pad
                or hz < min(zs) - pad
                or hz > max(zs) + pad
            ):
                continue
            if me.center and me.radius_in:
                cx, cy, cz = me.center
                vx, vy, vz = hx - cx, hy - cy, hz - cz
                vlen = math.sqrt(vx * vx + vy * vy + vz * vz)
                d_ring = abs(vlen - float(me.radius_in))
                d2 = d_ring * d_ring
                if d2 < best_d2:
                    best_d2 = d2
                    best_id = int(me.id)
            for i in range(len(pl) - 1):
                a = pl[i]
                b = pl[i + 1]
                d2 = self._dist2_point_segment(
                    hx, hy, hz, a[0], a[1], a[2], b[0], b[1], b[2]
                )
                if d2 < best_d2:
                    best_d2 = d2
                    best_id = int(me.id)
        return best_id, math.sqrt(best_d2) if best_id is not None else 1e9

    def _nearest_face_to_world_point(
        self,
        hit: tuple[float, float, float],
        *,
        max_dist: float,
    ) -> tuple[int | None, float]:
        """Cara B-Rep más cercana al punto de impacto."""
        if not self._measure_faces:
            return None, 1e9
        try:
            from engine.step_io import point_to_shape_closest
        except Exception:
            point_to_shape_closest = None

        best_id: int | None = None
        best_d = float(max_dist)
        # Prefiltro por bbox; DistShapeShape solo en candidatas
        candidates: list[tuple[float, object]] = []
        for mf in self._measure_faces:
            bd = self._bbox_dist(mf.bbox, hit)
            if bd > max_dist:
                continue
            # plano: distancia analítica rápida
            if mf.kind == "plane" and mf.normal is not None:
                n = mf.normal
                c = mf.center
                d_plane = abs(
                    (hit[0] - c[0]) * n[0]
                    + (hit[1] - c[1]) * n[1]
                    + (hit[2] - c[2]) * n[2]
                )
                if d_plane <= max_dist and bd <= max_dist * 0.15:
                    # punto proyectado cerca del bbox → buena candidata
                    candidates.append((d_plane, mf))
                else:
                    candidates.append((max(bd, d_plane), mf))
            else:
                candidates.append((bd, mf))

        candidates.sort(key=lambda t: t[0])
        for _approx, mf in candidates[:24]:
            d = _approx
            if point_to_shape_closest is not None and mf.face is not None:
                try:
                    info = point_to_shape_closest(hit, mf.face)
                    if info is not None:
                        d = float(info[0])
                except Exception:
                    pass
            if d < best_d:
                best_d = d
                best_id = int(mf.id)
        return best_id, best_d if best_id is not None else 1e9

    def _pick_measure_edge_at(self, x: int, y: int) -> int | None:
        ent = self._pick_measure_entity_at(x, y)
        if ent is not None and ent[0] == "e":
            return ent[1]
        return None

    def _pick_measure_entity_at(self, x: int, y: int) -> tuple[str, int] | None:
        """
        Picking estilo Inventor:
        1) Raycast a la malla
        2) Si hay arista cerca → arista; si no → cara bajo el impacto
        """
        if (not self._measure_edges and not self._measure_faces) or self.renderer is None:
            return None
        vtk = self._vtk
        ren = self.renderer
        tol3d = self._model_pick_tolerance()
        edge_prefer = max(0.02, 0.012 * self._model_diag())
        face_hit = max(0.05, 0.004 * self._model_diag())

        hit = None
        try:
            if self._actor is not None:
                try:
                    self._actor.SetPickable(1)
                except Exception:
                    pass
            picker = vtk.vtkCellPicker()
            picker.SetTolerance(0.01)
            if self._actor is not None:
                try:
                    picker.AddPickList(self._actor)
                    picker.PickFromListOn()
                except Exception:
                    pass
            ok = picker.Pick(float(x), float(y), 0.0, ren)
            if ok and int(picker.GetCellId()) >= 0:
                hit = tuple(float(v) for v in picker.GetPickPosition())
        except Exception:
            hit = None

        if hit is not None:
            eid, ed = self._nearest_edge_to_world_point(hit, max_dist=tol3d)
            fid, fd = self._nearest_face_to_world_point(hit, max_dist=tol3d)
            if eid is not None and ed <= edge_prefer:
                return ("e", eid)
            if fid is not None and fd <= face_hit:
                return ("f", fid)
            if eid is not None and ed <= tol3d:
                return ("e", eid)
            if fid is not None and fd <= tol3d:
                return ("f", fid)

        # Fallback pantalla: solo aristas (caras son difíciles en 2D)
        size = ren.GetSize()
        h = float(size[1] or 1)
        candidates_y = [float(y), float(h - float(y) - 1.0)]
        max_px = max(28.0, 0.03 * max(float(size[0] or 1), h))
        best_id: int | None = None
        best_d2 = max_px * max_px

        def _world_to_display(wx: float, wy: float, wz: float) -> tuple[float, float] | None:
            try:
                ren.SetWorldPoint(float(wx), float(wy), float(wz), 1.0)
                ren.WorldToDisplay()
                dx, dy, _dz = ren.GetDisplayPoint()
                return float(dx), float(dy)
            except Exception:
                return None

        for my in candidates_y:
            mx = float(x)
            for me in self._measure_edges:
                pl = me.polyline or []
                if len(pl) < 2:
                    continue
                n = len(pl)
                sample_idx = {0, n - 1, n // 2}
                if me.kind in ("circle", "arc") and n > 6:
                    step = max(1, n // 16)
                    sample_idx.update(range(0, n, step))
                for i in sample_idx:
                    disp = _world_to_display(*pl[i])
                    if disp is None:
                        continue
                    d2 = (disp[0] - mx) ** 2 + (disp[1] - my) ** 2
                    if d2 < best_d2:
                        best_d2 = d2
                        best_id = int(me.id)
                if n >= 2:
                    d0 = _world_to_display(*pl[0])
                    d1 = _world_to_display(*pl[-1])
                    if d0 and d1:
                        ax, ay = d0
                        bx, by = d1
                        abx, aby = bx - ax, by - ay
                        denom = abx * abx + aby * aby
                        if denom > 1e-9:
                            t = max(
                                0.0,
                                min(1.0, ((mx - ax) * abx + (my - ay) * aby) / denom),
                            )
                            qx, qy = ax + t * abx, ay + t * aby
                            d2 = (qx - mx) ** 2 + (qy - my) ** 2
                            if d2 < best_d2:
                                best_d2 = d2
                                best_id = int(me.id)
        if best_id is not None:
            return ("e", best_id)
        return None

    def _select_measure_edge(self, edge_id: int) -> None:
        self._select_measure_entity(("e", edge_id))

    def _select_measure_entity(self, sel: tuple[str, int]) -> None:
        kind, idx = sel
        if kind == "e":
            if idx < 0 or idx >= len(self._measure_edges):
                return
        elif kind == "f":
            if idx < 0 or idx >= len(self._measure_faces):
                return
        else:
            return
        if len(self._measure_selected) >= 2:
            self._measure_selected = [sel]
        elif sel in self._measure_selected:
            return
        else:
            self._measure_selected.append(sel)
        self._refresh_measure_overlays()

    # ── Cotas estilo Inventor (extension + dim line + flechas + texto) ──

    _DIM_CYAN = (0.05, 0.82, 0.95)

    @staticmethod
    def _v_add(a, b):
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

    @staticmethod
    def _v_sub(a, b):
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    @staticmethod
    def _v_mul(a, s: float):
        return (a[0] * s, a[1] * s, a[2] * s)

    @staticmethod
    def _v_dot(a, b) -> float:
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    @staticmethod
    def _v_cross(a, b):
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    @staticmethod
    def _v_len(a) -> float:
        return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])

    @classmethod
    def _v_norm(cls, a):
        L = cls._v_len(a)
        if L < 1e-12:
            return (0.0, 0.0, 0.0)
        return (a[0] / L, a[1] / L, a[2] / L)

    def _model_diag(self) -> float:
        try:
            if self._actor is not None:
                b = self._actor.GetBounds()
                return math.sqrt(
                    (b[1] - b[0]) ** 2 + (b[3] - b[2]) ** 2 + (b[5] - b[4]) ** 2
                )
        except Exception:
            pass
        return 100.0 if self._measure_to_inch < 0.1 else 10.0

    def _view_dir(self):
        """Dirección cámara → escena (hacia el modelo)."""
        try:
            cam = self.renderer.GetActiveCamera()
            pos = cam.GetPosition()
            fp = cam.GetFocalPoint()
            return self._v_norm(self._v_sub(fp, pos))
        except Exception:
            return (0.0, 0.0, -1.0)

    def _view_up(self):
        try:
            cam = self.renderer.GetActiveCamera()
            u = cam.GetViewUp()
            return self._v_norm((float(u[0]), float(u[1]), float(u[2])))
        except Exception:
            return (0.0, 1.0, 0.0)

    def _dim_params(self, feature_len: float) -> dict:
        """Proporciones tipo Inventor según tamaño de la cota / modelo."""
        diag = self._model_diag()
        mm = self._measure_to_inch < 0.1
        fl = max(float(feature_len), 1e-6)
        arrow = min(max(0.045 * fl, 0.008 * diag), 0.12 * fl)
        arrow = max(arrow, 1.4 if mm else 0.055)
        offset = min(max(0.12 * fl, 0.035 * diag), 0.28 * diag)
        offset = max(offset, 3.0 if mm else 0.12)
        gap = max(0.18 * arrow, 0.6 if mm else 0.025)
        overhang = max(0.35 * arrow, 0.8 if mm else 0.03)
        text_scale = max(0.022 * diag, 3.0 if mm else 0.12)
        text_scale = min(text_scale, 0.08 * diag)
        return {
            "arrow": arrow,
            "arrow_w": arrow * 0.38,
            "offset": offset,
            "gap": gap,
            "overhang": overhang,
            "line_w": 2.0,
            "text_scale": text_scale,
            "hl_w": 2.2,
        }

    def _perp_in_view(self, along):
        """Vector unitario ⊥ a ``along``, en el plano de la vista."""
        u = self._v_norm(along)
        vd = self._view_dir()
        n = self._v_cross(u, vd)
        if self._v_len(n) < 1e-8:
            n = self._v_cross(u, self._view_up())
        if self._v_len(n) < 1e-8:
            n = self._v_cross(u, (0.0, 0.0, 1.0))
        if self._v_len(n) < 1e-8:
            n = self._v_cross(u, (0.0, 1.0, 0.0))
        return self._v_norm(n)

    def _make_polyline_line_actor(
        self,
        polyline: list,
        *,
        color=None,
        width: float = 2.0,
    ):
        color = color or self._DIM_CYAN
        vtk = self._vtk
        pts = vtk.vtkPoints()
        lines = vtk.vtkCellArray()
        ids = []
        for x, y, z in polyline:
            ids.append(pts.InsertNextPoint(float(x), float(y), float(z)))
        if len(ids) < 2:
            return None
        for i in range(len(ids) - 1):
            lines.InsertNextCell(2)
            lines.InsertCellPoint(ids[i])
            lines.InsertCellPoint(ids[i + 1])
        pdata = vtk.vtkPolyData()
        pdata.SetPoints(pts)
        pdata.SetLines(lines)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(pdata)
        mapper.ScalarVisibilityOff()
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetColor(*color)
        prop.SetLineWidth(float(width))
        prop.LightingOff()
        prop.SetAmbient(1.0)
        prop.SetDiffuse(0.0)
        try:
            prop.RenderLinesAsTubesOff()
        except Exception:
            pass
        try:
            actor.SetPickable(0)
        except Exception:
            pass
        return actor

    def _make_filled_arrowhead(
        self,
        tip,
        direction,
        plane_n,
        *,
        length: float,
        half_width: float,
        color=None,
    ):
        """Triángulo relleno (flecha de cota Inventor), punta en ``tip``."""
        color = color or self._DIM_CYAN
        vtk = self._vtk
        u = self._v_norm(direction)
        if self._v_len(u) < 1e-12 or length <= 0:
            return None
        n = self._v_norm(plane_n)
        side = self._v_cross(u, n)
        if self._v_len(side) < 1e-8:
            side = self._v_cross(u, (0.0, 0.0, 1.0))
        if self._v_len(side) < 1e-8:
            side = self._v_cross(u, (0.0, 1.0, 0.0))
        side = self._v_norm(side)
        base = self._v_sub(tip, self._v_mul(u, length))
        left = self._v_add(base, self._v_mul(side, half_width))
        right = self._v_sub(base, self._v_mul(side, half_width))

        pts = vtk.vtkPoints()
        pts.InsertNextPoint(*tip)
        pts.InsertNextPoint(*left)
        pts.InsertNextPoint(*right)
        polys = vtk.vtkCellArray()
        polys.InsertNextCell(3)
        polys.InsertCellPoint(0)
        polys.InsertCellPoint(1)
        polys.InsertCellPoint(2)
        # reverso para que se vea desde ambos lados
        polys.InsertNextCell(3)
        polys.InsertCellPoint(0)
        polys.InsertCellPoint(2)
        polys.InsertCellPoint(1)
        pdata = vtk.vtkPolyData()
        pdata.SetPoints(pts)
        pdata.SetPolys(polys)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(pdata)
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetColor(*color)
        prop.LightingOff()
        prop.SetAmbient(1.0)
        prop.SetDiffuse(0.0)
        prop.EdgeVisibilityOff()
        try:
            actor.SetPickable(0)
        except Exception:
            pass
        return actor

    def _make_dim_label(self, text: str, pos, *, scale: float):
        """Texto cyan estilo Inventor (sin caja blanca)."""
        vtk = self._vtk
        color = self._DIM_CYAN
        try:
            actor = vtk.vtkBillboardTextActor3D()
        except Exception:
            return None
        actor.SetInput(str(text))
        actor.SetPosition(float(pos[0]), float(pos[1]), float(pos[2]))
        try:
            actor.SetScale(float(scale))
        except Exception:
            pass
        try:
            tp = actor.GetTextProperty()
            tp.SetFontSize(18)
            tp.SetBold(1)
            tp.SetColor(*color)
            tp.SetJustificationToCentered()
            tp.SetVerticalJustificationToCentered()
            try:
                tp.SetBackgroundOpacity(0.0)
            except Exception:
                pass
            try:
                tp.FrameOff()
            except Exception:
                pass
        except Exception:
            pass
        try:
            actor.SetPickable(0)
        except Exception:
            pass
        return actor

    def _make_inventor_linear_dim(
        self,
        p0,
        p1,
        text: str,
        *,
        offset_sign: float = 1.0,
        color=None,
    ) -> list:
        """
        Cota lineal Inventor:
        - líneas de extensión (con gap)
        - línea de cota paralela desplazada
        - flechas triangulares en extremos
        - texto centrado (línea rota)
        """
        color = color or self._DIM_CYAN
        actors: list = []
        delta = self._v_sub(p1, p0)
        seg = self._v_len(delta)
        if seg < 1e-9:
            return actors
        u = self._v_norm(delta)
        n = self._perp_in_view(u)
        n = self._v_mul(n, 1.0 if offset_sign >= 0 else -1.0)
        p = self._dim_params(seg)
        arrow = min(p["arrow"], 0.28 * seg)
        aw = arrow * 0.38
        offset = p["offset"]
        gap = p["gap"]
        overhang = p["overhang"]
        lw = p["line_w"]

        d0 = self._v_add(p0, self._v_mul(n, offset))
        d1 = self._v_add(p1, self._v_mul(n, offset))
        e0a = self._v_add(p0, self._v_mul(n, gap))
        e1a = self._v_add(p1, self._v_mul(n, gap))
        e0b = self._v_add(p0, self._v_mul(n, offset + overhang))
        e1b = self._v_add(p1, self._v_mul(n, offset + overhang))

        for a, b in ((e0a, e0b), (e1a, e1b)):
            act = self._make_polyline_line_actor([a, b], color=color, width=lw)
            if act is not None:
                actors.append(act)

        # bases de flecha (hacia el centro)
        base0 = self._v_add(d0, self._v_mul(u, arrow))
        base1 = self._v_sub(d1, self._v_mul(u, arrow))
        mid = self._v_mul(self._v_add(d0, d1), 0.5)
        # hueco para texto — nunca comerse toda la línea (bug: solo quedaba "in")
        char_w = p["text_scale"] * 0.55
        half_txt = max(0.5 * len(text) * char_w * 0.5, p["text_scale"] * 1.2)
        usable = max(0.0, seg - 2.0 * arrow)
        if half_txt * 2.0 >= usable * 0.75:
            # línea continua; texto encima
            act = self._make_polyline_line_actor([base0, base1], color=color, width=lw)
            if act is not None:
                actors.append(act)
        else:
            half_txt = min(half_txt, 0.35 * usable)
            t0 = self._v_sub(mid, self._v_mul(u, half_txt))
            t1 = self._v_add(mid, self._v_mul(u, half_txt))
            if self._v_dot(self._v_sub(t0, base0), u) > 0:
                act = self._make_polyline_line_actor([base0, t0], color=color, width=lw)
                if act is not None:
                    actors.append(act)
            if self._v_dot(self._v_sub(base1, t1), u) > 0:
                act = self._make_polyline_line_actor([t1, base1], color=color, width=lw)
                if act is not None:
                    actors.append(act)

        # flechas planas en el plano de la vista (como Inventor)
        face = self._view_dir()
        a0 = self._make_filled_arrowhead(
            d0, self._v_mul(u, -1.0), face, length=arrow, half_width=aw, color=color
        )
        a1 = self._make_filled_arrowhead(
            d1, u, face, length=arrow, half_width=aw, color=color
        )
        if a0 is not None:
            actors.append(a0)
        if a1 is not None:
            actors.append(a1)

        # texto ligeramente “arriba” de la línea de cota
        tpos = self._v_add(mid, self._v_mul(n, p["text_scale"] * 0.35))
        lab = self._make_dim_label(text, tpos, scale=p["text_scale"])
        if lab is not None:
            actors.append(lab)
        return actors

    def _make_inventor_diameter_dim(
        self,
        me,
        *,
        color=None,
    ) -> list:
        """
        Diámetro / radio estilo Inventor:
        - contorno fino del círculo
        - leader con flecha + texto «X.XXXX in Diameter»
        """
        color = color or self._DIM_CYAN
        actors: list = []
        pl = me.polyline or []
        if len(pl) >= 2:
            hl = self._make_polyline_line_actor(
                pl, color=color, width=self._dim_params(me.radius_in or 1.0)["hl_w"]
            )
            if hl is not None:
                actors.append(hl)
        if not (me.center and me.radius_in):
            return actors

        c = me.center
        r = float(me.radius_in)
        p = self._dim_params(r * 2.0)
        # dirección de salida: del centro al punto medio del polyline (hacia afuera)
        mid = self._polyline_midpoint(pl) if pl else self._v_add(c, (r, 0.0, 0.0))
        radial = self._v_norm(self._v_sub(mid, c))
        if self._v_len(radial) < 1e-8:
            radial = self._perp_in_view((1.0, 0.0, 0.0))
        attach = self._v_add(c, self._v_mul(radial, r))

        is_dia = bool(me.is_full_circle or me.kind == "circle")
        text = self._inventor_dim_text(me)

        # Leader: flecha en el borde → codo → landing horizontal
        leader_len = max(p["offset"] * 0.85, r * 0.55)
        elbow = self._v_add(attach, self._v_mul(radial, leader_len))
        # horizontal en pantalla
        right = self._v_norm(self._v_cross(self._view_dir(), self._view_up()))
        if self._v_len(right) < 1e-8:
            right = (1.0, 0.0, 0.0)
        # elegir lado según vista (texto a la derecha del leader)
        land_len = max(p["text_scale"] * (0.55 * len(text) * 0.55 + 0.8), leader_len * 0.7)
        landing = self._v_add(elbow, self._v_mul(right, land_len))

        for a, b in ((attach, elbow), (elbow, landing)):
            act = self._make_polyline_line_actor([a, b], color=color, width=p["line_w"])
            if act is not None:
                actors.append(act)

        arrow = min(p["arrow"], 0.35 * r)
        ah = self._make_filled_arrowhead(
            attach,
            self._v_mul(radial, -1.0),  # punta hacia el círculo (desde fuera)
            self._view_dir(),
            length=arrow,
            half_width=arrow * 0.38,
            color=color,
        )
        if ah is not None:
            actors.append(ah)

        # texto junto al landing (estilo Inventor)
        tpos = self._v_add(landing, self._v_mul(self._view_up(), p["text_scale"] * 0.15))
        # centrar sobre el landing
        tpos = self._v_mul(self._v_add(elbow, landing), 0.5)
        tpos = self._v_add(tpos, self._v_mul(self._view_up(), p["text_scale"] * 0.25))
        lab = self._make_dim_label(text, tpos, scale=p["text_scale"])
        if lab is not None:
            actors.append(lab)

        # Para diámetros grandes, opcionalmente no dibujar diámetro cruzado (leader basta)
        _ = is_dia
        return actors

    def _make_edge_selection_hl(self, me, *, color=None) -> list:
        """Resaltado fino de la arista seleccionada (no es la cota)."""
        color = color or self._DIM_CYAN
        pl = me.polyline or []
        if len(pl) < 2:
            return []
        fl = float(me.length_in or me.radius_in or 1.0)
        act = self._make_polyline_line_actor(
            pl, color=color, width=self._dim_params(fl)["hl_w"]
        )
        return [act] if act is not None else []

    def _make_face_selection_hl(self, mf, *, color=(0.20, 0.85, 0.35), opacity=0.42) -> list:
        """Resaltado de cara (relleno semitransparente + contorno), estilo Inventor."""
        actors: list = []
        vtk = self._vtk
        verts = mf.mesh_verts or []
        tris = mf.mesh_tris or []
        if verts and tris:
            pts = vtk.vtkPoints()
            for x, y, z in verts:
                pts.InsertNextPoint(float(x), float(y), float(z))
            cells = vtk.vtkCellArray()
            for a, b, c in tris:
                cells.InsertNextCell(3)
                cells.InsertCellPoint(int(a))
                cells.InsertCellPoint(int(b))
                cells.InsertCellPoint(int(c))
            pdata = vtk.vtkPolyData()
            pdata.SetPoints(pts)
            pdata.SetPolys(cells)
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(pdata)
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            prop = actor.GetProperty()
            prop.SetColor(*color)
            prop.SetOpacity(float(opacity))
            prop.SetAmbient(0.65)
            prop.SetDiffuse(0.55)
            prop.EdgeVisibilityOff()
            try:
                actor.SetPickable(0)
            except Exception:
                pass
            actors.append(actor)
        if mf.outline and len(mf.outline) >= 2:
            ol = self._make_polyline_line_actor(mf.outline, color=color, width=2.4)
            if ol is not None:
                actors.append(ol)
        return actors

    def _make_edge_measure_actors(self, me, *, color=None) -> list:
        """Cota Inventor para una arista (lineal / diámetro / radio)."""
        color = color or self._DIM_CYAN
        if me.kind in ("circle", "arc") and me.center and me.radius_in:
            return self._make_inventor_diameter_dim(me, color=color)
        pl = me.polyline or []
        if len(pl) < 2:
            return []
        text = self._inventor_dim_text(me)
        # Empujar la cota hacia afuera del modelo (no sobre la pieza)
        p0, p1 = pl[0], pl[-1]
        mid = self._v_mul(self._v_add(p0, p1), 0.5)
        u = self._v_norm(self._v_sub(p1, p0))
        n = self._perp_in_view(u)
        sign = 1.0
        try:
            if self._actor is not None:
                b = self._actor.GetBounds()
                center = (
                    0.5 * (b[0] + b[1]),
                    0.5 * (b[2] + b[3]),
                    0.5 * (b[4] + b[5]),
                )
                # si n apunta hacia el centro del modelo, invertir
                if self._v_dot(n, self._v_sub(center, mid)) > 0:
                    sign = -1.0
        except Exception:
            pass
        return self._make_inventor_linear_dim(
            p0, p1, text, offset_sign=sign, color=color
        )

    def _polyline_midpoint(self, polyline: list):
        if not polyline:
            return (0.0, 0.0, 0.0)
        i = len(polyline) // 2
        return polyline[i]

    @staticmethod
    def _edge_has_center(me) -> bool:
        return bool(
            getattr(me, "center", None) is not None
            and getattr(me, "kind", "") in ("circle", "arc")
        )

    def _resolve_measure_sel(self, sel: tuple[str, int]):
        kind, idx = sel
        if kind == "f":
            return self._measure_faces[idx]
        return self._measure_edges[idx]

    @staticmethod
    def _entity_brep(ent):
        if getattr(ent, "face", None) is not None and hasattr(ent, "area"):
            return ent.face
        return getattr(ent, "edge", None)

    def _edge_endpoints(self, me):
        pl = getattr(me, "polyline", None) or []
        if len(pl) >= 2:
            return pl[0], pl[-1]
        return None

    def _closest_point_on_segment(self, point, a, b):
        """Pie perpendicular del punto al segmento (clamp a extremos)."""
        ab = self._v_sub(b, a)
        den = self._v_dot(ab, ab)
        if den < 1e-18:
            return a
        t = self._v_dot(self._v_sub(point, a), ab) / den
        t = max(0.0, min(1.0, t))
        return self._v_add(a, self._v_mul(ab, t))

    def _measure_pair_anchors(self, a, b):
        """
        Anclas de cota entre dos selecciones (arista o cara).
        Returns (p0, p1, dist_model, mode).
        """
        fa = hasattr(a, "area") and getattr(a, "face", None) is not None
        fb = hasattr(b, "area") and getattr(b, "face", None) is not None
        ca = (not fa) and self._edge_has_center(a)
        cb = (not fb) and self._edge_has_center(b)

        # Cara ↔ cara (Inventor: distancia entre caras / planos paralelos)
        if fa and fb:
            # Planos casi paralelos → distancia por normal
            if (
                a.kind == "plane"
                and b.kind == "plane"
                and a.normal is not None
                and b.normal is not None
            ):
                n = self._v_norm(a.normal)
                if abs(self._v_dot(n, self._v_norm(b.normal))) > 0.985:
                    signed = self._v_dot(self._v_sub(b.center, a.center), n)
                    p0 = a.center
                    p1 = self._v_add(a.center, self._v_mul(n, signed))
                    return p0, p1, abs(signed), "face_face"
            try:
                from engine.step_io import shape_min_distance

                info = shape_min_distance(a.face, b.face)
                if info is not None:
                    return info[1], info[2], float(info[0]), "face_face"
            except Exception:
                pass
            d = self._v_len(self._v_sub(a.center, b.center))
            return a.center, b.center, d, "face_face"

        # Cara ↔ círculo/arco → centro a cara
        if fa and cb:
            try:
                from engine.step_io import point_to_shape_closest

                info = point_to_shape_closest(b.center, a.face)
                if info is not None:
                    return b.center, info[1], float(info[0]), "face_entity"
            except Exception:
                pass
        if fb and ca:
            try:
                from engine.step_io import point_to_shape_closest

                info = point_to_shape_closest(a.center, b.face)
                if info is not None:
                    return a.center, info[1], float(info[0]), "face_entity"
            except Exception:
                pass

        # Cara ↔ arista → DistShapeShape
        if fa or fb:
            try:
                from engine.step_io import shape_min_distance

                sa = self._entity_brep(a)
                sb = self._entity_brep(b)
                info = shape_min_distance(sa, sb)
                if info is not None:
                    return info[1], info[2], float(info[0]), "face_entity"
            except Exception:
                pass

        if ca and cb:
            p0, p1 = a.center, b.center
            return p0, p1, self._v_len(self._v_sub(p0, p1)), "center"

        if ca and not cb and not fb:
            ends = self._edge_endpoints(b)
            if ends is not None:
                foot = self._closest_point_on_segment(a.center, ends[0], ends[1])
                return (
                    a.center,
                    foot,
                    self._v_len(self._v_sub(a.center, foot)),
                    "center_edge",
                )

        if cb and not ca and not fa:
            ends = self._edge_endpoints(a)
            if ends is not None:
                foot = self._closest_point_on_segment(b.center, ends[0], ends[1])
                return (
                    b.center,
                    foot,
                    self._v_len(self._v_sub(b.center, foot)),
                    "center_edge",
                )

        try:
            from engine.step_io import shape_min_distance

            info = shape_min_distance(self._entity_brep(a), self._entity_brep(b))
            if info is not None:
                return info[1], info[2], float(info[0]), "min"
        except Exception:
            pass
        ma = self._polyline_midpoint(getattr(a, "polyline", None) or [getattr(a, "center", (0, 0, 0))])
        mb = self._polyline_midpoint(getattr(b, "polyline", None) or [getattr(b, "center", (0, 0, 0))])
        return ma, mb, self._v_len(self._v_sub(ma, mb)), "min"

    def _refresh_measure_overlays(self) -> None:
        self._clear_measure_overlays()
        self._update_measure_panel()
        if not self._measure_selected:
            self.lbl_status.setText("Medición ON — selecciona arista o cara.")
            if self.vtk_widget is not None:
                self.vtk_widget.GetRenderWindow().Render()
            return

        cyan = self._DIM_CYAN
        face_colors = [(0.18, 0.85, 0.35), (0.55, 0.40, 0.95)]
        status_parts: list[str] = []

        if len(self._measure_selected) == 1:
            sel = self._measure_selected[0]
            ent = self._resolve_measure_sel(sel)
            if sel[0] == "f":
                status_parts.append(f"[F{sel[1]}] Face {ent.kind}")
                for act in self._make_face_selection_hl(ent, color=face_colors[0]):
                    self.renderer.AddActor(act)
                    self._measure_hl_actors.append(act)
            else:
                label = self._format_edge_measure(ent)
                status_parts.append(f"[{sel[1]}] {label}")
                for act in self._make_edge_measure_actors(ent, color=cyan):
                    self.renderer.AddActor(act)
                    self._measure_hl_actors.append(act)
        else:
            for i, sel in enumerate(self._measure_selected):
                ent = self._resolve_measure_sel(sel)
                if sel[0] == "f":
                    status_parts.append(f"[F{sel[1]}] Face")
                    col = face_colors[i % 2]
                    for act in self._make_face_selection_hl(ent, color=col):
                        self.renderer.AddActor(act)
                        self._measure_hl_actors.append(act)
                else:
                    label = self._format_edge_measure(ent)
                    status_parts.append(f"[{sel[1]}] {label}")
                    for act in self._make_edge_selection_hl(ent, color=cyan):
                        self.renderer.AddActor(act)
                        self._measure_hl_actors.append(act)

            a = self._resolve_measure_sel(self._measure_selected[0])
            b = self._resolve_measure_sel(self._measure_selected[1])
            draw_p0, draw_p1, draw_val, mode = self._measure_pair_anchors(a, b)

            dim_txt = self._inches(draw_val)
            for act in self._make_inventor_linear_dim(
                draw_p0, draw_p1, dim_txt, color=cyan
            ):
                self.renderer.AddActor(act)
                self._measure_dim_actors.append(act)

            if mode == "center":
                status_parts.append(f"centro–centro={dim_txt}")
            elif mode == "center_edge":
                status_parts.append(f"centro–arista={dim_txt}")
            elif mode == "face_face":
                status_parts.append(f"cara–cara={dim_txt}")
            else:
                status_parts.append(f"dist={dim_txt}")

        self.lbl_status.setText("Medición: " + "  |  ".join(status_parts))
        self._update_measure_panel()
        self.vtk_widget.GetRenderWindow().Render()
        self._sync_nav_overlays()

    def set_refresh_callback(self, cb) -> None:
        """Compat: ya no hay Refrescar; se ignora."""
        pass

    def _preguntar_origen_export(self, titulo: str) -> bool | None:
        """True=remoto, False=local, None=cancelado."""
        box = QMessageBox(self)
        box.setWindowTitle(titulo)
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            "¿La exportación de DXF/STEP es Local o Remota?\n\n"
            "• Local → Nesteos Locales (escritorio / OneDrive)\n"
            "• Remoto → servidor (ARGA METALS CORPORATE SYSTEM)"
        )
        btn_local = box.addButton("Local", QMessageBox.ButtonRole.AcceptRole)
        btn_remoto = box.addButton("Remoto", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_local:
            return False
        if clicked is btn_remoto:
            return True
        return None

    def _elegir_de_lista(self, titulo: str, etiqueta: str, items: list[str]) -> str | None:
        if not items:
            return None
        if len(items) == 1:
            return items[0]
        elegido, ok = QInputDialog.getItem(self, titulo, etiqueta, items, 0, False)
        if not ok or not elegido:
            return None
        return str(elegido)

    def _cargar_steps_wo(self) -> None:
        """Local/Remoto → elige W.O. del job → lista STEP."""
        from engine.step_paths import listar_wo_con_steps, resolver_model_core_files

        modo = self._preguntar_origen_export("STEPS de W.O. — origen")
        if modo is None:
            return
        mcf, nota = resolver_model_core_files(self._app, modo_servidor=modo)
        origen = "Remoto" if modo else "Local"
        if mcf is None:
            self.lbl_ctx.setText(nota or f"{origen}: sin MODEL CORE FILES")
            QMessageBox.warning(
                self,
                "STEPS de W.O.",
                f"No se encontró MODEL CORE FILES del job ({origen}).\n\n"
                f"{nota or ''}\n\n"
                "Abre el job / .arganest o verifica la carpeta de exportación.",
            )
            return
        wos = listar_wo_con_steps(mcf)
        if not wos:
            self.lbl_ctx.setText(f"{origen} | {mcf}")
            QMessageBox.information(
                self,
                "STEPS de W.O.",
                f"No hay W.O. con STEP en:\n{mcf}\n\n"
                "Exporta el nesting a CAD (con STEP) o espera al despachador nocturno.",
            )
            return
        labels = [f"{w['nombre']}  ({w['n_steps']} STEP)" for w in wos]
        elegido = self._elegir_de_lista("Elegir W.O.", "W.O. con STEP:", labels)
        if not elegido:
            return
        idx = labels.index(elegido)
        wo = wos[idx]
        files = wo["steps"]
        self.lbl_ctx.setText(f"{origen} | {wo['nombre']} | {wo['export_cad']}")
        self.set_files(files, select_first=True)
        self.lbl_status.setText(f"{len(files)} STEP(s) de {wo['nombre']} cargados.")

    def _cargar_steps_swo(self) -> None:
        """Local/Remoto → elige S.W.O. con STEP → lista."""
        from engine.step_paths import listar_swo_con_steps

        modo = self._preguntar_origen_export("STEPS de S.W.O. — origen")
        if modo is None:
            return
        origen = "Remoto" if modo else "Local"
        swos = listar_swo_con_steps(modo_servidor=modo)
        if not swos:
            self.lbl_ctx.setText(f"{origen} | Máxima Optimización (sin S.W.O. con STEP)")
            QMessageBox.information(
                self,
                "STEPS de S.W.O.",
                f"No hay S.W.O. con STEP en Máxima Optimización ({origen}).\n\n"
                "Solo aparecen carpetas S.W.O. que ya tengan STEP "
                "(export 3D o despachador nocturno).",
            )
            return
        labels = [f"{s['nombre']}  ({s['n_steps']} STEP)" for s in swos]
        elegido = self._elegir_de_lista("Elegir S.W.O.", "S.W.O. con STEP:", labels)
        if not elegido:
            return
        idx = labels.index(elegido)
        swo = swos[idx]
        files = swo["steps"]
        self.lbl_ctx.setText(f"{origen} | {swo['nombre']} | {swo['export_cad']}")
        self.set_files(files, select_first=True)
        self.lbl_status.setText(f"{len(files)} STEP(s) de {swo['nombre']} cargados.")

    def set_files(self, files: list[Path], *, select_first: bool = True) -> None:
        self.lista.clear()
        for p in files:
            item = QListWidgetItem(p.name)
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            item.setToolTip(str(p))
            self.lista.addItem(item)
        self.lbl_status.setText(f"{len(files)} STEP(s) en lista.")
        if select_first and self.lista.count() > 0:
            self.lista.setCurrentRow(0)

    def _abrir_archivo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Abrir STEP",
            "",
            "STEP (*.step *.stp *.STEP *.STP);;Todos (*.*)",
        )
        if not path:
            return
        for i in range(self.lista.count()):
            if os.path.normcase(self.lista.item(i).data(Qt.ItemDataRole.UserRole)) == os.path.normcase(
                path
            ):
                self.lista.setCurrentRow(i)
                return
        item = QListWidgetItem(os.path.basename(path))
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        self.lista.insertItem(0, item)
        self.lista.setCurrentRow(0)

    def _on_select(self, current: QListWidgetItem | None, _prev=None) -> None:
        if current is None:
            return
        path = current.data(Qt.ItemDataRole.UserRole)
        if path:
            self._load_path(str(path))

    def _load_path(self, path: str) -> None:
        if not self._vtk_ready:
            return
        if self._worker and self._worker.isRunning():
            return
        self._current_path = path
        self._measure_edges = []
        self._measure_faces = []
        self._measure_mode = False
        self._measure_selected.clear()
        self.lbl_status.setText(f"Cargando… {os.path.basename(path)}")
        self._worker = _LoadStepWorker(path, self)
        self._worker.finished_ok.connect(self._on_mesh_ok)
        self._worker.finished_err.connect(self._on_mesh_err)
        self._worker.start()

    def _on_mesh_ok(self, data, path: str) -> None:
        try:
            self._show_display(data, path)
            marks = getattr(data, "n_mark_segs", 0) or 0
            extra = f"  ·  {marks} segs MARK" if marks else "  ·  sin MARK libre"
            self.lbl_status.setText(
                f"{os.path.basename(path)}  ·  {data.n_tris} tris  ·  {data.n_verts} verts"
                f"{extra}  ·  M=medir"
            )
            self._place_nav_overlays()
        except Exception as exc:
            self.lbl_status.setText(f"Error al dibujar: {exc}")

    def _on_mesh_err(self, msg: str, path: str) -> None:
        self.lbl_status.setText(f"Error: {msg}")
        QMessageBox.warning(self, "Ver STEP", f"No se pudo cargar:\n{path}\n\n{msg}")

    def _enable_antialiasing(self, rw) -> None:
        """MSAA + suavizado de líneas (menos pixelado en contornos/marcaje)."""
        for samples in (8, 4, 2):
            try:
                rw.SetMultiSamples(samples)
                break
            except Exception:
                continue
        try:
            rw.LineSmoothingOn()
            rw.PolygonSmoothingOn()
            rw.PointSmoothingOn()
        except Exception:
            pass
        try:
            from vtkmodules.vtkRenderingOpenGL2 import vtkOpenGLFXAAPass, vtkRenderStepsPass

            fxaa = vtkOpenGLFXAAPass()
            basic = vtkRenderStepsPass()
            fxaa.SetDelegatePass(basic)
            self.renderer.SetPass(fxaa)
        except Exception:
            pass
        try:
            rw.SetDesiredUpdateRate(30.0)
            rw.SetStillUpdateRate(0.0001)
        except Exception:
            pass

    def _setup_cad_lights(self) -> None:
        """Iluminación tipo CAD (menos lavado blanco / metálico)."""
        vtk = self._vtk
        try:
            self.renderer.RemoveAllLights()
        except Exception:
            pass
        key = vtk.vtkLight()
        key.SetLightTypeToSceneLight()
        key.SetPosition(2.5, 3.0, 4.0)
        key.SetFocalPoint(0, 0, 0)
        key.SetColor(1.0, 1.0, 0.98)
        key.SetIntensity(0.78)
        self.renderer.AddLight(key)
        fill = vtk.vtkLight()
        fill.SetLightTypeToSceneLight()
        fill.SetPosition(-2.0, 1.2, 1.5)
        fill.SetFocalPoint(0, 0, 0)
        fill.SetColor(0.85, 0.90, 1.0)
        fill.SetIntensity(0.32)
        self.renderer.AddLight(fill)
        rim = vtk.vtkLight()
        rim.SetLightTypeToSceneLight()
        rim.SetPosition(0.5, -2.5, 1.0)
        rim.SetFocalPoint(0, 0, 0)
        rim.SetIntensity(0.18)
        self.renderer.AddLight(rim)
        try:
            self.renderer.LightFollowCameraOff()
        except Exception:
            pass

    def _tone_color(self, rgb: tuple[float, float, float]) -> tuple[float, float, float]:
        """Baja un poco la luminancia para evitar el aspecto blanquecino."""
        r, g, b = rgb
        return (
            r * 0.82 + 0.08,
            g * 0.82 + 0.08,
            b * 0.82 + 0.08,
        )

    def _clear_geom_actors(self) -> None:
        self._measure_mode = False
        self._measure_selected.clear()
        self._clear_measure_overlays()
        self._measure_edges = []
        self._measure_faces = []
        if self._measure_pick_actor is not None and self.renderer is not None:
            try:
                self.renderer.RemoveActor(self._measure_pick_actor)
            except Exception:
                pass
            self._measure_pick_actor = None
            self._measure_pick_poly = None
        for attr in ("_actor", "_edge_actor", "_mark_actor"):
            a = getattr(self, attr, None)
            if a is not None:
                try:
                    self.renderer.RemoveActor(a)
                except Exception:
                    pass
                setattr(self, attr, None)

    def _make_edge_actor(self, normals_filter) -> object:
        """Contornos / feature edges estilo Inventor High Quality."""
        vtk = self._vtk
        edges = vtk.vtkFeatureEdges()
        edges.SetInputConnection(normals_filter.GetOutputPort())
        edges.BoundaryEdgesOn()
        edges.FeatureEdgesOn()
        edges.ManifoldEdgesOff()
        edges.NonManifoldEdgesOn()
        edges.SetFeatureAngle(35.0)
        edges.ColoringOff()
        edges.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(edges.GetOutputPort())
        mapper.ScalarVisibilityOff()

        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetColor(0.05, 0.05, 0.07)
        prop.SetLineWidth(2.0)
        prop.SetOpacity(0.95)
        prop.LightingOff()
        prop.SetAmbient(1.0)
        prop.SetDiffuse(0.0)
        prop.SetSpecular(0.0)
        try:
            prop.RenderLinesAsTubesOn()
        except Exception:
            pass
        try:
            actor.SetPickable(0)
        except Exception:
            pass
        return actor

    def _show_display(self, data, path: str) -> None:
        vtk = self._vtk
        mesh = data.mesh if hasattr(data, "mesh") else data
        pts = vtk.vtkPoints()
        for x, y, z in mesh.vertices:
            pts.InsertNextPoint(float(x), float(y), float(z))

        cells = vtk.vtkCellArray()
        for a, b, c in mesh.triangles:
            cells.InsertNextCell(3)
            cells.InsertCellPoint(int(a))
            cells.InsertCellPoint(int(b))
            cells.InsertCellPoint(int(c))

        poly = vtk.vtkPolyData()
        poly.SetPoints(pts)
        poly.SetPolys(cells)

        normals = vtk.vtkPolyDataNormals()
        normals.SetInputData(poly)
        normals.ComputePointNormalsOn()
        normals.ComputeCellNormalsOn()
        normals.SplittingOn()
        normals.SetFeatureAngle(35.0)
        normals.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(normals.GetOutputPort())
        mapper.ScalarVisibilityOff()

        color = self._tone_color(resolve_display_color(path))
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetColor(*color)
        # Acabado mate CAD (no metálico brillante)
        prop.SetSpecular(0.08)
        prop.SetSpecularPower(8)
        prop.SetAmbient(0.22)
        prop.SetDiffuse(0.78)
        prop.SetInterpolationToPhong()
        prop.EdgeVisibilityOff()  # contorno va en actor aparte (más limpio)

        self._clear_geom_actors()
        self.renderer.AddActor(actor)
        self._actor = actor

        self._edge_actor = self._make_edge_actor(normals)
        self.renderer.AddActor(self._edge_actor)

        polylines = getattr(data, "polylines", None) or []
        if polylines:
            self._mark_actor = self._make_mark_actor(polylines)
            self.renderer.AddActor(self._mark_actor)

        self._measure_edges = list(getattr(data, "measure_edges", None) or [])
        self._measure_faces = list(getattr(data, "measure_faces", None) or [])
        self._measure_mode = False
        self._measure_selected.clear()

        self.renderer.ResetCamera()
        self._ensure_orthographic()
        self._orient_face("FRONT", animate=False)
        self.vtk_widget.GetRenderWindow().Render()
        if self._view_cube:
            self._view_cube.update()
        if self._triad:
            self._triad.update()

    def _make_mark_actor(self, polylines: list) -> object:
        """Marcaje más visible (contorno oscuro tipo High Quality)."""
        vtk = self._vtk
        pts = vtk.vtkPoints()
        lines = vtk.vtkCellArray()
        z_lift = 0.03
        for pl in polylines:
            if len(pl) < 2:
                continue
            ids = []
            for x, y, z in pl:
                ids.append(pts.InsertNextPoint(float(x), float(y), float(z) + z_lift))
            for i in range(len(ids) - 1):
                lines.InsertNextCell(2)
                lines.InsertCellPoint(ids[i])
                lines.InsertCellPoint(ids[i + 1])

        pdata = vtk.vtkPolyData()
        pdata.SetPoints(pts)
        pdata.SetLines(lines)

        tubes = vtk.vtkTubeFilter()
        tubes.SetInputData(pdata)
        tubes.SetRadius(0.020)
        tubes.SetNumberOfSides(12)
        tubes.SetVaryRadiusToVaryRadiusOff()
        tubes.CappingOn()
        tubes.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(tubes.GetOutputPort())
        mapper.ScalarVisibilityOff()
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetColor(0.05, 0.05, 0.07)
        prop.SetOpacity(1.0)
        prop.SetSpecular(0.0)
        prop.SetAmbient(0.60)
        prop.SetDiffuse(0.50)
        prop.LightingOn()
        try:
            prop.SetInterpolationToPhong()
        except Exception:
            pass
        return actor

    def _on_viewcube(self, kind: str, payload, recenter: bool = False) -> None:
        self._pending_recenter = bool(recenter)
        if kind == "face":
            self._orient_face(str(payload), animate=True)
        elif kind == "nav":
            self._orient_face(str(payload), animate=True)
        elif kind == "roll":
            # El fit (si doble clic) lo aplica _animate_camera_to al terminar
            self._roll_view_animated(-45.0 if payload == "cw" else 45.0)
        elif kind == "edge":
            f1, f2 = payload
            n = _norm(_add(_FACE_N[f1], _FACE_N[f2]))
            self._orient_by_look(
                (-n[0], -n[1], -n[2]),
                face_view_up(f1),
                animate=True,
                steps=4,
            )
        elif kind == "corner":
            f1, f2, f3 = payload
            n = _norm(_add(_FACE_N[f1], _FACE_N[f2], _FACE_N[f3]))
            preferred = face_view_up("FRONT")
            if "TOP" in (f1, f2, f3):
                preferred = face_view_up("TOP")
            elif "BOTTOM" in (f1, f2, f3):
                preferred = face_view_up("BOTTOM")
            self._orient_by_look(
                (-n[0], -n[1], -n[2]),
                preferred,
                animate=True,
                steps=6,
            )

    def _fit_keep_orientation(self) -> None:
        """Enfoca/centra el modelo conservando look + up actuales (Inventor doble clic)."""
        if not self._vtk_ready or self._actor is None:
            return
        cam = self.renderer.GetActiveCamera()
        pos0, fp0, up0, _d = self._current_cam_pose()
        look = _norm((fp0[0] - pos0[0], fp0[1] - pos0[1], fp0[2] - pos0[2]))
        self.renderer.ResetCamera()
        self._ensure_orthographic()
        fp1 = list(cam.GetFocalPoint())
        dist = cam.GetDistance() or 1.0
        # Un poco de margen como Fit
        dist *= 1.05
        pos1 = (
            fp1[0] - look[0] * dist,
            fp1[1] - look[1] * dist,
            fp1[2] - look[2] * dist,
        )
        up1 = stable_view_up(look, up0, current_up=up0)
        self._apply_cam_pose(pos1, tuple(fp1), up1)

    def _orient_face(self, name: str, *, animate: bool = True) -> None:
        n = face_normal(name)
        look = (-n[0], -n[1], -n[2])
        self._orient_by_look(look, face_view_up(name), animate=animate, steps=4)

    def _orient_by_look(
        self,
        look_dir,
        up_standard,
        *,
        animate: bool = True,
        steps: int = 4,
    ) -> None:
        """Orienta como FreeCAD rotateToNearest (cara/arista/esquina)."""
        if not self._vtk_ready or self._actor is None:
            return
        pos0, fp0, up0, dist = self._current_cam_pose()
        look0 = _norm((fp0[0] - pos0[0], fp0[1] - pos0[1], fp0[2] - pos0[2]))
        look1, up1 = nearest_orientation(look_dir, up_standard, look0, up0, steps=steps)
        # pos = fp - look * dist  (cámara mira hacia el foco)
        pos1 = (
            fp0[0] - look1[0] * dist,
            fp0[1] - look1[1] * dist,
            fp0[2] - look1[2] * dist,
        )
        if animate:
            self._animate_camera_to(pos1, fp0, up1, duration_ms=300)
        else:
            self._apply_cam_pose(pos1, fp0, up1)
            if self._pending_recenter:
                self._pending_recenter = False
                self._fit_keep_orientation()

    def _current_cam_pose(self):
        cam = self.renderer.GetActiveCamera()
        return (
            tuple(cam.GetPosition()),
            tuple(cam.GetFocalPoint()),
            tuple(cam.GetViewUp()),
            float(cam.GetDistance() or 1.0),
        )

    def _apply_cam_pose(self, pos, fp, up) -> None:
        cam = self.renderer.GetActiveCamera()
        cam.SetPosition(*pos)
        cam.SetFocalPoint(*fp)
        cam.SetViewUp(*up)
        self._ensure_orthographic()
        self.renderer.ResetCameraClippingRange()
        self.vtk_widget.GetRenderWindow().Render()
        self._sync_nav_overlays()

    def _stop_cam_anim(self) -> None:
        if self._cam_anim is not None:
            self._cam_anim.stop()
            self._cam_anim.deleteLater()
            self._cam_anim = None
        self._cam_anim_state = None

    def _animate_camera_to(self, pos_to, fp_to, up_to, *, duration_ms: int = 280) -> None:
        if not self._vtk_ready or self._actor is None:
            return
        pos0, fp0, up0, _dist = self._current_cam_pose()
        self._stop_cam_anim()

        # Si ya estamos casi ahí, snap
        def _close(a, b, eps=1e-3):
            return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) < eps

        if _close(pos0, pos_to, 1e-2) and _close(up0, up_to, 0.05):
            self._apply_cam_pose(pos_to, fp_to, up_to)
            if self._pending_recenter:
                self._pending_recenter = False
                self._fit_keep_orientation()
            return

        steps = max(8, int(duration_ms / 16))
        state = {
            "i": 0,
            "n": steps,
            "pos0": pos0,
            "fp0": fp0,
            "up0": up0,
            "pos1": pos_to,
            "fp1": fp_to,
            "up1": _norm(up_to),
            "recenter": bool(self._pending_recenter),
        }
        self._pending_recenter = False
        self._cam_anim_state = state
        timer = QTimer(self)
        timer.setInterval(16)

        def _tick():
            st = self._cam_anim_state
            if st is None:
                timer.stop()
                return
            st["i"] += 1
            t = _ease(st["i"] / st["n"])
            dir0 = _norm(
                (
                    st["pos0"][0] - st["fp0"][0],
                    st["pos0"][1] - st["fp0"][1],
                    st["pos0"][2] - st["fp0"][2],
                )
            )
            dir1 = _norm(
                (
                    st["pos1"][0] - st["fp1"][0],
                    st["pos1"][1] - st["fp1"][1],
                    st["pos1"][2] - st["fp1"][2],
                )
            )
            dist0 = math.sqrt(
                max(1e-12, sum((st["pos0"][k] - st["fp0"][k]) ** 2 for k in range(3)))
            )
            dist1 = math.sqrt(
                max(1e-12, sum((st["pos1"][k] - st["fp1"][k]) ** 2 for k in range(3)))
            )
            dist = _lerp(dist0, dist1, t)
            d = _slerp_dir(dir0, dir1, t)
            fp = _lerp3(st["fp0"], st["fp1"], t)
            pos = (fp[0] + d[0] * dist, fp[1] + d[1] * dist, fp[2] + d[2] * dist)
            up = _slerp_dir(st["up0"], st["up1"], t)
            look = _norm((fp[0] - pos[0], fp[1] - pos[1], fp[2] - pos[2]))
            up = stable_view_up(look, up, current_up=up)
            self._apply_cam_pose(pos, fp, up)
            if st["i"] >= st["n"]:
                self._apply_cam_pose(st["pos1"], st["fp1"], st["up1"])
                do_fit = bool(st.get("recenter"))
                self._stop_cam_anim()
                if do_fit:
                    self._fit_keep_orientation()

        timer.timeout.connect(_tick)
        self._cam_anim = timer
        timer.start()

    def _roll_view_animated(self, degrees: float) -> None:
        if not self._vtk_ready or self._actor is None:
            return
        pos0, fp0, up0, _dist = self._current_cam_pose()
        look = _norm((fp0[0] - pos0[0], fp0[1] - pos0[1], fp0[2] - pos0[2]))
        up1 = _norm(_rodrigues(up0, look, degrees))
        self._animate_camera_to(pos0, fp0, up1, duration_ms=220)

    def closeEvent(self, event) -> None:
        self._stop_cam_anim()
        try:
            self._nav_sync_timer.stop()
        except Exception:
            pass
        for w in (self._view_cube, self._triad):
            if w is not None:
                try:
                    w.hide()
                    w.setParent(None)
                    w.deleteLater()
                except Exception:
                    pass
        self._view_cube = None
        self._triad = None
        try:
            if self.vtk_widget is not None:
                self.vtk_widget.Finalize()
        except Exception:
            pass
        super().closeEvent(event)


def abrir_visor_step(tab) -> None:
    app = getattr(tab, "app", None)
    job = str(getattr(app, "job_activo", "") or "").strip() if app else ""
    ctx = f"Job: {job}" if job else "Visor STEP experimental (OCCT)"
    dlg = StepViewerDialog(tab, initial_files=[], context=ctx, app=app)
    dlg.lbl_status.setText(
        "Usa «STEPS de W.O.» / «STEPS de S.W.O.» o «Abrir STEP…».  "
        "Con un STEP cargado: tecla M = medir aristas y caras."
    )
    dlg.showMaximized()
    dlg.exec()
