"""Candado: progreso FreeCAD STEP se deriva de STEP vigentes en carpeta.

Bug: el diálogo de export quedaba en STEP 0/N porque FreeCAD no habla con el
ANS y lanzar_freecad_robotica solo mandaba `mensaje=` (sin step_done) mientras
los .step ya aparecían en disco.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from freecad_runner import (  # noqa: E402
    _cad_path_for_dxf,
    contar_cad_vigentes,
)


def _touch(path: Path, *, age_s: float = 0.0, size: int = 1024) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * max(512, int(size)))
    if age_s:
        ts = time.time() - float(age_s)
        os.utime(path, (ts, ts))


def test_contar_cad_vigentes_por_carpeta() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dxf_dir = root / "DXF"
        step_dir = root / "STEP"
        dxfs = []
        for i in range(1, 5):
            dxf = dxf_dir / f"NESTING_0.25_W.O. 52 X3-H{i}.dxf"
            _touch(dxf, age_s=10.0)
            dxfs.append(str(dxf))

        # 2 STEP vigentes (mtime >= DXF) y uno viejo/inválido
        for i in (1, 2):
            step = Path(_cad_path_for_dxf(dxfs[i - 1], str(step_dir), "step"))
            _touch(step, age_s=0.0, size=2048)
        stale = Path(_cad_path_for_dxf(dxfs[2], str(step_dir), "step"))
        _touch(stale, age_s=60.0, size=2048)  # más viejo que su DXF → no vigente

        assert contar_cad_vigentes(dxfs, str(step_dir), "step") == 2

        # Al aparecer H3 vigente, el conteo sube (simula sondeo de carpeta).
        _touch(stale, age_s=0.0, size=2048)
        assert contar_cad_vigentes(dxfs, str(step_dir), "step") == 3


def test_progress_cb_recibe_step_done_por_conteo() -> None:
    """El callback de UI debe recibir step_done = base + vigentes."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dxf_dir = root / "DXF"
        step_dir = root / "STEP"
        dxfs = []
        for i in range(1, 4):
            dxf = dxf_dir / f"NESTING_0.1046_W.O. 1-H{i}.dxf"
            _touch(dxf, age_s=5.0)
            dxfs.append(str(dxf))
            step = Path(_cad_path_for_dxf(str(dxf), str(step_dir), "step"))
            _touch(step, age_s=0.0)

        vistos: list[dict] = []

        def fake_cb(**kwargs):
            vistos.append(dict(kwargs))

        base = 10
        hechos = contar_cad_vigentes(dxfs, str(step_dir), "step")
        fake_cb(
            mensaje=f"FreeCAD STEP: {hechos}/{len(dxfs)}",
            step_done=base + hechos,
        )
        assert hechos == 3
        assert vistos[-1]["step_done"] == 13
        assert "3/3" in str(vistos[-1]["mensaje"])


if __name__ == "__main__":
    test_contar_cad_vigentes_por_carpeta()
    test_progress_cb_recibe_step_done_por_conteo()
    print("OK freecad step progress folder poll")
