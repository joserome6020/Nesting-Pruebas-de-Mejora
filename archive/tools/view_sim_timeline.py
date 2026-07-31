"""Lanza visor timeline del escenario GENE."""
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

if __name__ == "__main__":
    from interface.qt.dialogs.nest_sim_timeline import main

    main()
