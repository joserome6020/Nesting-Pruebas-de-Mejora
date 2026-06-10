#!/usr/bin/env python3
"""Puente Herinox en carpeta AutoDXF 2.0 (materiales + largos -> herinox_sync.local.json)."""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules.consulta_herinox_bridge import main

if __name__ == "__main__":
    raise SystemExit(main())
