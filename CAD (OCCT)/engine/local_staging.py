"""Staging local de archivos en red (UNC / unidad remota) hacia %TEMP%.

Convierte DXF/STEP de forma más estable y rápida: el trabajo pesado
(ezdxf + OCCT) corre en disco local; la red solo copia al inicio y al final.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _env_stage_mode() -> str:
    """
    ARGA_STAGE_DXF:
      unset / auto → UNC y unidades de red
      1 / force / all → siempre stage
      0 / off / no → nunca stage
    """
    return str(os.environ.get("ARGA_STAGE_DXF", "") or "").strip().lower()


def is_unc_path(path: str | Path) -> bool:
    s = str(path).replace("/", "\\")
    return s.startswith("\\\\")


def is_network_drive(path: str | Path) -> bool:
    """True si la letra de unidad es remota (DRIVE_REMOTE=4 en Win32)."""
    if sys.platform != "win32":
        return False
    s = str(path)
    if len(s) < 2 or s[1] != ":":
        return False
    try:
        import ctypes

        root = f"{s[0].upper()}:\\"
        # DRIVE_REMOTE = 4
        return int(ctypes.windll.kernel32.GetDriveTypeW(root)) == 4
    except Exception:
        return False


def needs_local_staging(path: str | Path) -> bool:
    mode = _env_stage_mode()
    if mode in ("0", "off", "no", "false", "never"):
        return False
    if mode in ("1", "force", "all", "yes", "true", "on"):
        return True
    return is_unc_path(path) or is_network_drive(path)


@contextmanager
def stage_file_to_temp(
    src: str | Path,
    *,
    prefix: str = "arga_stage_",
    suffix: str | None = None,
    force: bool = False,
) -> Iterator[Path]:
    """
    Si `src` está en red (o force/ARGA_STAGE_DXF), copia a %TEMP% y yield
    la ruta local; al salir borra el temp. Si no hace falta, yield `src`.
    """
    source = Path(src)
    if not force and not needs_local_staging(source):
        yield source
        return

    if not source.is_file():
        raise FileNotFoundError(f"No se puede stagear (no existe): {source}")

    ext = suffix if suffix is not None else source.suffix or ".bin"
    fd, tmp_name = tempfile.mkstemp(prefix=prefix, suffix=ext)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copy2(str(source), str(tmp_path))
        print(
            f"[STAGE] {source.name} -> %TEMP% "
            f"({tmp_path.stat().st_size} bytes) desde red/UNC",
            flush=True,
        )
        yield tmp_path
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass


@contextmanager
def staged_local_dxf(dxf_path: str | Path) -> Iterator[Path]:
    """Atajo: stage DXF de red a %TEMP% para ezdxf/OCCT."""
    with stage_file_to_temp(
        dxf_path, prefix="arga_dxf_", suffix=".dxf"
    ) as local:
        yield local
