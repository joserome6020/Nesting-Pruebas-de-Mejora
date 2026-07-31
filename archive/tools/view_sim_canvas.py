"""Abre el canvas Qt con el escenario GENE prueba 1 (auto-ejecuta)."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IFACE = os.path.join(_ROOT, "interface")
for _p in (_ROOT, _IFACE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from modules.win_dll_bootstrap import bootstrap_proceso_nesting

    bootstrap_proceso_nesting()
except Exception:
    pass

os.environ["ARGA_NEST_MODE"] = "max"

from PySide6.QtCore import Qt, QTimer, QRectF  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from interface.qt.layout_helpers import make_panel_dark  # noqa: E402
from interface.qt.nesting_graphics import (  # noqa: E402
    NestingDrawParams,
    NestingGraphicsView,
    compute_fit_rect,
    populate_nesting_scene,
)
from interface.qt.theme import apply_theme  # noqa: E402
from modules.nesting_engine.sim_lab import (  # noqa: E402
    build_pieces_from_entries,
    inches_to_mm,
    load_scenario_json,
    run_single_sheet_sim,
    scenario_from_dict,
)

DEFAULT_SCENARIO = os.path.join(_ROOT, "_logs", "sim_gene_prueba1", "escenario.nestsim.json")


class SimCanvasWindow(QMainWindow):
    def __init__(self, scenario_path: str):
        super().__init__()
        self._scenario_path = scenario_path
        self.setWindowTitle("Simulacion GENE — cargando…")
        self.setMinimumSize(1100, 720)
        self.resize(1280, 800)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 10)

        self.lbl_status = QLabel("Preparando motor de nesting…")
        self.lbl_status.setStyleSheet("font-size:13px;font-weight:600;color:#1E293B;")
        root.addWidget(self.lbl_status)

        panel = make_panel_dark()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(6, 6, 6, 6)
        from PySide6.QtWidgets import QGraphicsScene

        self.scene = QGraphicsScene(self)
        self.view = NestingGraphicsView(self)
        self.view.setScene(self.scene)
        pl.addWidget(self.view, 1)
        root.addWidget(panel, 1)

        self.lbl_footer = QLabel("")
        self.lbl_footer.setStyleSheet("color:#64748B;font-size:11px;")
        root.addWidget(self.lbl_footer)

        QTimer.singleShot(120, self._run)

    def _run(self):
        path = self._scenario_path
        if not os.path.isfile(path):
            self.lbl_status.setText(f"No se encontro escenario: {path}")
            return

        params, entries = scenario_from_dict(load_scenario_json(path))
        piezas, errores = build_pieces_from_entries(entries)
        if errores:
            self.lbl_status.setText("Errores DXF: " + "; ".join(errores[:3]))
            return
        if not piezas:
            self.lbl_status.setText("Sin piezas en el escenario.")
            return

        self.lbl_status.setText(
            f"Ejecutando nesteo ({len(piezas)} pzas, placa {params['plate_w_in']}\"x{params['plate_h_in']}\")… "
            "puede tardar ~30 s"
        )
        QApplication.processEvents()

        os.environ["ARGA_NEST_MODE"] = str(params.get("nest_mode") or "max")
        result = run_single_sheet_sim(
            piezas,
            w_mm=inches_to_mm(params["plate_w_in"]),
            h_mm=inches_to_mm(params["plate_h_in"]),
            kerf_in=params["kerf_in"],
            margin_in=params["margin_in"],
            corner=params["corner"],
            opt=params["opt"],
            mc_iterations=params["mc_iterations"],
            nest_mode=params["nest_mode"],
        )

        self.lbl_status.setText(
            f"Placa {params['plate_w_in']}\" x {params['plate_h_in']}\" | "
            f"{len(result.hoja.get('piezas') or []) if result.hoja else 0}/{len(piezas)} colocadas | "
            f"eficiencia {float((result.hoja or {}).get('eficiencia_directa') or (result.hoja or {}).get('eficiencia') or 0):.1f}% | "
            f"restos {len(result.restos)}"
        )
        self.setWindowTitle("Simulacion GENE prueba 1 — 120 x 48")
        self.lbl_footer.setText(
            f"Motor max | {result.elapsed_ms:.0f} ms | Escenario: {path}"
        )

        if not result.hoja or not result.hoja.get("piezas"):
            return

        draw_params = NestingDrawParams(hoja=result.hoja, clave="SIM_GENE_P1", app=None)
        meta = populate_nesting_scene(self.scene, draw_params)
        rect = compute_fit_rect(
            result.hoja,
            meta,
            max(400, self.view.viewport().width()),
            max(300, self.view.viewport().height()),
        )
        if rect:
            if not isinstance(rect, QRectF):
                x0, x1, y0, y1 = rect
                rect = QRectF(float(x0), float(y0), float(x1 - x0), float(y1 - y0))
            self.view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)


def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SCENARIO
    app = QApplication(sys.argv)
    apply_theme(app)
    win = SimCanvasWindow(scenario)
    win.show()
    win.raise_()
    win.activateWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
