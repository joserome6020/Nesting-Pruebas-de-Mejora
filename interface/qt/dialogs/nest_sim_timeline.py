"""Reproductor de nesteo — canvas + play + barra (sin panel de botones)."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsScene,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from interface.qt.layout_helpers import make_panel_dark
from interface.qt.nesting_graphics import NestingDrawParams, NestingGraphicsView, compute_fit_rect, populate_nesting_scene
from interface.qt.theme import apply_theme
from modules.nesting_engine.sim_lab import (
    SimTimelineResult,
    build_pieces_from_entries,
    hoja_en_paso_timeline,
    inches_to_mm,
    load_scenario_json,
    run_timeline_sim,
    scenario_from_dict,
    texto_paso_timeline,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_SCENARIO = os.path.join(_REPO_ROOT, "_logs", "sim_gene_prueba1", "escenario.nestsim.json")


class _NestView(NestingGraphicsView):
    def __init__(self, player: "NestReplayer"):
        super().__init__(player)
        self._player = player

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key == Qt.Key.Key_Space:
            self._player._toggle_play()
            event.accept()
            return
        if key == Qt.Key.Key_Left:
            self._player._seek(self._player.slider.value() - 1)
            event.accept()
            return
        if key == Qt.Key.Key_Right:
            self._player._seek(self._player.slider.value() + 1)
            event.accept()
            return
        super().keyPressEvent(event)


class NestReplayer(QMainWindow):
    """UI tipo reproductor: play/pausa, barra de tiempo, canvas."""

    def __init__(self, timeline: SimTimelineResult, *, titulo: str = "Nesteo"):
        super().__init__()
        self.timeline = timeline
        self._playing = False
        self._fit_rect = None
        self._total = len(timeline.pasos or [])

        self.setWindowTitle(titulo)
        self.setMinimumSize(1000, 680)
        self.resize(1280, 800)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        panel = make_panel_dark()
        panel_lay = QVBoxLayout(panel)
        panel_lay.setContentsMargins(0, 0, 0, 0)
        panel_lay.setSpacing(0)

        self.scene = QGraphicsScene(self)
        self.view = _NestView(self)
        self.view.setScene(self.scene)
        self.view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        panel_lay.addWidget(self.view, 1)

        self.lbl_caption = QLabel("")
        self.lbl_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_caption.setStyleSheet(
            "background:rgba(15,23,42,0.82);color:#E2E8F0;font-size:12px;"
            "padding:8px 14px;border-top:1px solid #334155;"
        )
        self.lbl_caption.setWordWrap(True)
        panel_lay.addWidget(self.lbl_caption)
        root.addWidget(panel, 1)

        bar = QFrame()
        bar.setStyleSheet("background:#FFFFFF;border-top:1px solid #E2E8F0;")
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(14, 10, 14, 10)
        bar_lay.setSpacing(12)

        self.btn_play = QPushButton("\u25b6")
        self.btn_play.setFixedSize(44, 44)
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.setStyleSheet(
            "QPushButton{background:#2F6DEA;color:white;border:none;border-radius:22px;"
            "font-size:18px;font-weight:700;}"
            "QPushButton:hover{background:#2563EB;}"
        )
        self.btn_play.clicked.connect(self._toggle_play)
        bar_lay.addWidget(self.btn_play)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(self._total)
        self.slider.setValue(0)
        self.slider.sliderMoved.connect(self._on_slider)
        self.slider.valueChanged.connect(self._on_slider)
        bar_lay.addWidget(self.slider, 1)

        self.lbl_time = QLabel(f"0 / {self._total}")
        self.lbl_time.setStyleSheet("color:#475569;font-size:12px;font-weight:600;min-width:64px;")
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar_lay.addWidget(self.lbl_time)

        root.addWidget(bar)

        self._timer = QTimer(self)
        self._timer.setInterval(550)
        self._timer.timeout.connect(self._tick)

        self._render_frame(0)
        QTimer.singleShot(100, self.view.setFocus)

    def _on_slider(self, value: int):
        if self._playing:
            self._stop_play()
        self._render_frame(int(value))

    def _seek(self, value: int):
        self._stop_play()
        self._render_frame(value)

    def _toggle_play(self):
        if self._playing:
            self._stop_play()
        else:
            if self.slider.value() >= self._total:
                self.slider.setValue(0)
            self._playing = True
            self.btn_play.setText("\u23f8")
            self._timer.start()

    def _stop_play(self):
        self._playing = False
        self.btn_play.setText("\u25b6")
        self._timer.stop()

    def _tick(self):
        nxt = self.slider.value() + 1
        if nxt > self._total:
            self._stop_play()
            return
        self.slider.blockSignals(True)
        self.slider.setValue(nxt)
        self.slider.blockSignals(False)
        self._render_frame(nxt)

    def _render_frame(self, paso_idx: int):
        paso_idx = max(0, min(int(paso_idx), self._total))
        self.slider.blockSignals(True)
        self.slider.setValue(paso_idx)
        self.slider.blockSignals(False)
        self.lbl_time.setText(f"{paso_idx} / {self._total}")

        hoja, highlight = hoja_en_paso_timeline(self.timeline, paso_idx)
        selected = {highlight} if highlight is not None else set()

        if paso_idx == 0:
            self.lbl_caption.setText(
                f"Placa {mm_to_in(self.timeline.w_mm):.0f}\" x {mm_to_in(self.timeline.h_mm):.0f}\" "
                f"— inicio (pulsa Play o Espacio)"
            )
        else:
            paso = self.timeline.pasos[paso_idx - 1]
            self.lbl_caption.setText(texto_paso_timeline(paso, paso_idx=paso_idx, total=self._total))

        draw = NestingDrawParams(hoja=hoja, clave="REPLAY", app=None, selected_indices=selected)
        meta = populate_nesting_scene(self.scene, draw)
        if self._fit_rect is None:
            fit = compute_fit_rect(
                hoja,
                meta,
                max(400, self.view.viewport().width()),
                max(300, self.view.viewport().height()),
            )
            if fit:
                if isinstance(fit, QRectF):
                    self._fit_rect = fit
                else:
                    x0, x1, y0, y1 = fit
                    self._fit_rect = QRectF(float(x0), float(y0), float(x1 - x0), float(y1 - y0))
        if self._fit_rect is not None:
            self.view.fitInView(self._fit_rect, Qt.AspectRatioMode.KeepAspectRatio)


def mm_to_in(mm: float) -> float:
    return float(mm or 0) / 25.4


def timeline_desde_escenario(scenario_path: str, *, mc_iterations: int = 1) -> SimTimelineResult | None:
    if not os.path.isfile(scenario_path):
        return None
    params, entries = scenario_from_dict(load_scenario_json(scenario_path))
    piezas, errores = build_pieces_from_entries(entries)
    if errores or not piezas:
        return None
    return run_timeline_sim(
        piezas,
        w_mm=inches_to_mm(params["plate_w_in"]),
        h_mm=inches_to_mm(params["plate_h_in"]),
        kerf_in=params["kerf_in"],
        margin_in=params["margin_in"],
        corner=params["corner"],
        opt=params["opt"],
        mc_iterations=mc_iterations,
    )


def abrir_reproductor(
    scenario_path: str | None = None,
    *,
    parent=None,
    mc_iterations: int = 1,
) -> NestReplayer | None:
    path = scenario_path or DEFAULT_SCENARIO
    if not os.path.isfile(path):
        return None

    params, _ = scenario_from_dict(load_scenario_json(path))
    tl = timeline_desde_escenario(path, mc_iterations=mc_iterations)
    if not tl or not tl.pasos:
        if parent:
            QMessageBox.critical(parent, "Reproductor", tl.error if tl else "Sin pasos de nesteo.")
        return None

    titulo = f"Nesteo {params['plate_w_in']}\" x {params['plate_h_in']}\""
    if str(os.environ.get("ARGA_NEST_LAB", "")).strip().lower() in ("1", "true", "yes", "lab"):
        titulo = "[LAB] " + titulo
    win = NestReplayer(tl, titulo=titulo)
    win.show()
    win.raise_()
    win.activateWindow()
    return win


# Compatibilidad con codigo anterior
NestTimelineWindow = NestReplayer
abrir_timeline_desde_escenario = abrir_reproductor


def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SCENARIO
    mc = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    app = QApplication(sys.argv)
    apply_theme(app)
    win = abrir_reproductor(scenario, mc_iterations=mc)
    if win is None:
        print("No se pudo abrir reproductor:", scenario)
        sys.exit(1)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
