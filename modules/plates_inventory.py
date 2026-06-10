"""Mantiene Plates.xlsx alineado entre repo, dist y react-Herinox."""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

REPO_PLATES_REL = Path("modules") / "Plates.xlsx"
DIST_PLATES_REL = Path("dist") / "modules" / "Plates.xlsx"


def project_root(root: Optional[Path] = None) -> Path:
    return (root or _PROJECT_ROOT).resolve()


def repo_plates_path(root: Optional[Path] = None) -> Path:
    return project_root(root) / REPO_PLATES_REL


def dist_plates_path(root: Optional[Path] = None) -> Path:
    return project_root(root) / DIST_PLATES_REL


def mirror_plates_xlsx(src: Path, dst: Path, retries: int = 3) -> bool:
    if not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    last_error: Optional[Exception] = None
    for attempt in range(max(1, retries)):
        try:
            shutil.copy2(src, dst)
            return True
        except PermissionError as exc:
            last_error = exc
            if attempt + 1 >= retries:
                raise
            time.sleep(0.4)
    if last_error:
        raise last_error
    return False


def mirror_repo_to_dist(root: Optional[Path] = None) -> Optional[Path]:
    src = repo_plates_path(root)
    dst = dist_plates_path(root)
    if mirror_plates_xlsx(src, dst):
        return dst
    return None


def mirror_dev_plates_to_dist(root: Optional[Path] = None) -> Optional[Path]:
    """Espeja el inventario del repo hacia dist cuando se ejecuta en desarrollo."""
    if getattr(sys, "frozen", False):
        return None
    dst = dist_plates_path(root)
    if not dst.parent.parent.exists():
        return None
    return mirror_repo_to_dist(root)


def sync_herinox_and_align_dist(
    root: Optional[Path] = None,
    *,
    mirror_to_dist: bool = True,
):
    from modules.herinox_sync import HerinoxPlateSync

    target = repo_plates_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)

    sync = HerinoxPlateSync()
    result = sync.run(str(target))

    mirrored_dst = None
    if mirror_to_dist and result.ok and target.is_file():
        mirrored_dst = mirror_repo_to_dist(root)

    return result, mirrored_dst


def plates_files_are_identical(root: Optional[Path] = None) -> bool:
    src = repo_plates_path(root)
    dst = dist_plates_path(root)
    if not src.is_file() or not dst.is_file():
        return False
    return src.read_bytes() == dst.read_bytes()
