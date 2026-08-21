"""Localiza .stp/.step dentro de la carpeta AutoDXF del job."""
from __future__ import annotations

import os
from pathlib import Path

STEP_SUBDIR_NAME = "STEP"
FROM_STEP_DIRNAME = "FROM_STEP"
_STEP_EXTS = {".stp", ".step"}
# No bajar a salidas generadas ni basura de nest.
_SKIP_DIRNAMES = {
    FROM_STEP_DIRNAME.lower(),
    "processed files",
    "procesados",
    "nesting",
    "__pycache__",
}


def discover_steps_in_autodxf(carpeta_autodxf: str | os.PathLike[str]) -> list[Path]:
    """
    Busca materia prima STEP bajo AutoDXF:

    1) ``AutoDXF/STEP/**/*.stp|step``
    2) ``AutoDXF/*.stp|step`` (solo raíz; no dentro de Cal …)
    """
    root = Path(carpeta_autodxf)
    if not root.is_dir():
        return []

    found: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        try:
            key = str(path.resolve()).lower()
        except Exception:
            key = str(path).lower()
        if key in seen:
            return
        if path.suffix.lower() not in _STEP_EXTS:
            return
        if not path.is_file():
            return
        seen.add(key)
        found.append(path)

    step_dir = root / STEP_SUBDIR_NAME
    if step_dir.is_dir():
        for dirpath, dirnames, filenames in os.walk(step_dir):
            dirnames[:] = [d for d in dirnames if d.strip().lower() not in _SKIP_DIRNAMES]
            for name in filenames:
                _add(Path(dirpath) / name)

    for child in root.iterdir():
        if child.is_file():
            _add(child)

    found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
    return found


def pick_primary_step(carpeta_autodxf: str | os.PathLike[str]) -> Path | None:
    """El STEP más reciente bajo AutoDXF, o None."""
    steps = discover_steps_in_autodxf(carpeta_autodxf)
    return steps[0] if steps else None
