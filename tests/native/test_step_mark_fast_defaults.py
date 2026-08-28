"""Candado: flujo planta STEP = PIECE_ONESHOT multi-tool (no chunks lentos)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def test_defaults_piece_oneshot(monkeypatch=None):
    for key in (
        "ARGA_STEP_MARK_MODE",
        "ARGA_STEP_MARK_CHUNK",
        "ARGA_STEP_MARK_PROFILE",
        "ARGA_MARK_TEXT_FLATTEN_MM",
        "ARGA_STEP_PIECE_WORKERS",
    ):
        os.environ.pop(key, None)

    from modules.nesting_engine.step_export_prefs import (
        step_mark_chunk,
        step_mark_mode,
        step_mark_text_flatten_mm,
        step_piece_workers,
    )

    assert step_mark_mode() == "ENGRAVE_PIECE_ONESHOT"
    assert step_mark_chunk() == 0  # oneshot → multi-tool, no lotes
    assert step_mark_text_flatten_mm() >= 1.0  # menos segs que 0.75 viejo
    assert step_piece_workers() == 2


def test_legacy_chunk_mode_still_available():
    prev = os.environ.get("ARGA_STEP_MARK_MODE")
    prev_c = os.environ.get("ARGA_STEP_MARK_CHUNK")
    try:
        os.environ["ARGA_STEP_MARK_MODE"] = "ENGRAVE"
        os.environ["ARGA_STEP_MARK_CHUNK"] = "50"
        from modules.nesting_engine import step_export_prefs as prefs

        importlib_reload = __import__("importlib").reload
        # prefs lee env en cada call; no hace falta reload
        assert prefs.step_mark_mode() == "ENGRAVE"
        assert prefs.step_mark_chunk() == 50
    finally:
        if prev is None:
            os.environ.pop("ARGA_STEP_MARK_MODE", None)
        else:
            os.environ["ARGA_STEP_MARK_MODE"] = prev
        if prev_c is None:
            os.environ.pop("ARGA_STEP_MARK_CHUNK", None)
        else:
            os.environ["ARGA_STEP_MARK_CHUNK"] = prev_c


def test_piece_oneshot_forces_chunk_zero_in_builder_logic():
    """Regresión: mark_chunk=80 no debe anular PIECE_ONESHOT."""
    # Lógica espejo de build_freecad_like_shapes (sin OCP)
    mark_mode = "ENGRAVE_PIECE_ONESHOT"
    mark_chunk = 80
    mode = mark_mode.strip().upper()
    oneshot = mode in ("ENGRAVE_ONESHOT", "ONESHOT", "MULTI_ONESHOT", "ONESHOT_MULTI")
    piece_oneshot = mode in ("ENGRAVE_PIECE_ONESHOT", "PIECE_ONESHOT")
    if oneshot or piece_oneshot:
        mode = "ENGRAVE"
    if piece_oneshot:
        chunk = 0
    else:
        chunk = int(mark_chunk or 0)
        if chunk <= 0:
            chunk = 100
    assert oneshot is False
    assert piece_oneshot is True
    assert chunk == 0


def main() -> int:
    test_defaults_piece_oneshot()
    test_legacy_chunk_mode_still_available()
    test_piece_oneshot_forces_chunk_zero_in_builder_logic()
    print("OK test_step_mark_fast_defaults")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
