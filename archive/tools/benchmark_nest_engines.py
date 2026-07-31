"""Smoke test / diagnóstico del registro de motores de nesting (Fase 0)."""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules.nesting_engine.engine_registry import list_engine_metas, probe_engines
from modules.nesting_engine.nest_engine_context import STEEL_ENGINE_IDS


def main() -> int:
    print("== ARGA Nesting — Registro de motores (Fase 0) ==")
    print("Motores de acero:", ", ".join(STEEL_ENGINE_IDS))
    print()
    for meta in list_engine_metas():
        print(
            f"- {meta.engine_id}: {meta.display_name} | "
            f"status={meta.status} | phase={meta.phase}"
        )
    print()
    rows = probe_engines()
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    failed = [r for r in rows if r.get("ready") and not r.get("probe_ok", True)]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
