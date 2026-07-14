# Motor LAB SIMULATOR — puente Python
from __future__ import annotations

import importlib.util
import os

_LAB_ROOT = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_LAB_ROOT))


def lab_engine_paths() -> list[str]:
    names = (
        "algorithm_cpp_lab.cp314-win_amd64.pyd",
        "algorithm_cpp_lab.cp313-win_amd64.pyd",
        "algorithm_cpp_lab.pyd",
    )
    out: list[str] = []
    for folder in (
        _LAB_ROOT,
        os.path.join(_LAB_ROOT, "cpp", "build", "Release"),
        os.path.join(_LAB_ROOT, "cpp", "build"),
    ):
        for name in names:
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                out.append(p)
    return out


def load_lab_cpp():
    for path in lab_engine_paths():
        spec = importlib.util.spec_from_file_location("algorithm_cpp_lab", path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise ImportError(
        "algorithm_cpp_lab no compilado. Ejecuta:\n"
        "  LAB SIMULATOR\\build_lab_engine.ps1"
    )


def engine_name() -> str:
    return getattr(load_lab_cpp(), "ENGINE_NAME", "lab_unknown")
