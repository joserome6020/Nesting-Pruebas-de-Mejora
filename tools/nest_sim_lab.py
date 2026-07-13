"""Lanza el reproductor de nesteo (play + barra)."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IFACE = os.path.join(_ROOT, "interface")
for _p in (_ROOT, _IFACE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if __name__ == "__main__":
    try:
        from modules.win_dll_bootstrap import bootstrap_proceso_nesting

        bootstrap_proceso_nesting()
    except Exception:
        pass

    os.environ["ARGA_NEST_LAB"] = "1"

    from PySide6.QtWidgets import QApplication, QFileDialog

    from interface.qt.dialogs.nest_sim_timeline import DEFAULT_SCENARIO, abrir_reproductor
    from interface.qt.theme import apply_theme

    scenario = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SCENARIO

    app = QApplication(sys.argv)
    apply_theme(app)

    if not os.path.isfile(scenario):
        scenario, _ = QFileDialog.getOpenFileName(
            None,
            "Escenario de nesteo",
            os.path.join(_ROOT, "_logs"),
            "Escenario (*.nestsim.json *.json)",
        )
        if not scenario:
            sys.exit(0)

    win = abrir_reproductor(scenario)
    if win is None:
        sys.exit(1)
    sys.exit(app.exec())
