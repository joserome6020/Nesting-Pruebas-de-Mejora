"""Utilidades de inventario de placas (fuente: react-Herinox)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def project_root(root: Optional[Path] = None) -> Path:
    return (root or _PROJECT_ROOT).resolve()


def refresh_plates_from_herinox(root: Optional[Path] = None):
    """Carga inventario de placas directamente desde Herinox."""
    from modules.herinox_sync import HerinoxPlateSync

    _ = root
    sync = HerinoxPlateSync()
    result = sync.refresh()
    return result, sync.get_sheet_rows()


def sync_herinox_and_align_dist(
    root: Optional[Path] = None,
    *,
    mirror_to_dist: bool = True,
):
    """Compatibilidad con build scripts: refresca desde Herinox (sin Plates.xlsx)."""
    _ = mirror_to_dist
    result, rows = refresh_plates_from_herinox(root)
    return result, rows[0] if result.ok else None
