"""Candado: UI scale respeta pantallas chicas (VM) sin agrandar sobre 1920x1080."""
from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_ui_factor_caps_at_one_and_floors() -> None:
    from PySide6.QtWidgets import QApplication

    from interface.qt.ui_scale import (
        DESIGN_HEIGHT,
        DESIGN_WIDTH,
        MIN_UI_FACTOR,
        available_size,
        fit_window,
        s,
        ui_factor,
    )

    app = QApplication.instance() or QApplication([])
    f = ui_factor()
    assert MIN_UI_FACTOR <= f <= 1.0, f
    assert s(100) == max(1, int(round(100 * f)))
    # En diseño de referencia, factor ~1
    aw, ah = available_size()
    if aw >= DESIGN_WIDTH and ah >= DESIGN_HEIGHT:
        assert f == 1.0

    from PySide6.QtWidgets import QDialog

    dlg = QDialog()
    w, h = fit_window(dlg, 2000, 1600, max_frac=0.9)
    assert w <= int(aw * 0.9) + 1
    assert h <= int(ah * 0.9) + 1
    assert w >= 320 and h >= 240
    print(f"[OK] ui_scale factor={f:.3f} avail={aw}x{ah} fit={w}x{h}")


if __name__ == "__main__":
    test_ui_factor_caps_at_one_and_floors()
