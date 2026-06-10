#!/usr/bin/env python3
"""
Descarga psycopg2-binary en AutoDXF 2.0\\_vendor\\Python312|313|314
para que Consulta_Herinox.py funcione sin pip en cada PC de planta.

Uso (una vez, desde cualquier PC con internet):
  python tools/bootstrap_herinox_vendor.py
  python tools/bootstrap_herinox_vendor.py "Z:\\...\\AutoDXF 2.0"
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = Path(
    r"Z:\♦♦GRUPO ARGA CARPETAS COMPARTIDAS♦♦\BIENVENIDO\Departamentos _antes TIK"
    r"\21. Desarrollo y Tecnologia\2.- Códigos Desarrollo y Tecnología"
    r"\7.- Configuración para equipos de Computo\AutoDXF 2.0"
)
BRIDGE_SRC = ROOT / "modules" / "consulta_herinox_bridge.py"
CATALOG_SRC = ROOT / "modules" / "largos_espesor_catalog.json"
BRIDGE_DEST_NAME = "Consulta_Herinox.py"


def _python_candidates() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
    names = ("Python314", "Python313", "Python312", "Python311")
    found: list[Path] = []
    seen: set[str] = set()
    for base in (local, Path("C:/")):
        for name in names:
            exe = base / name / "python.exe"
            key = str(exe).lower()
            if exe.is_file() and key not in seen:
                seen.add(key)
                found.append(exe)
    if Path(sys.executable).is_file():
        key = str(Path(sys.executable)).lower()
        if key not in seen:
            found.insert(0, Path(sys.executable))
    return found


def _vendor_tag(py_exe: Path) -> str:
    out = subprocess.check_output(
        [str(py_exe), "-c", "import sys; print(f'Python{sys.version_info.major}{sys.version_info.minor}')"],
        text=True,
    )
    return out.strip()


def install_vendor_for(py_exe: Path, target_dir: Path) -> tuple[bool, str]:
    tag = _vendor_tag(py_exe)
    dest = target_dir / "_vendor" / tag
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(py_exe),
        "-m",
        "pip",
        "install",
        "psycopg2-binary",
        "-t",
        str(dest),
        "--upgrade",
        "--no-cache-dir",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit {proc.returncode}"
        return False, f"{tag}: {tail}"
    if not (dest / "psycopg2").exists():
        return False, f"{tag}: pip OK pero sin carpeta psycopg2"
    (dest / ".vendor_ok").write_text("psycopg2-binary\n", encoding="utf-8")
    return True, f"{tag} -> {dest}"


def sync_bridge_script(target_dir: Path) -> str:
    if not BRIDGE_SRC.is_file():
        return "sin fuente consulta_herinox_bridge.py"
    dest = target_dir / BRIDGE_DEST_NAME
    shutil.copy2(BRIDGE_SRC, dest)
    if CATALOG_SRC.is_file():
        shutil.copy2(CATALOG_SRC, target_dir / "largos_espesor_catalog.json")
    return str(dest)


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TARGET
    if not target.is_dir():
        print(f"ERROR: carpeta destino no existe: {target}")
        print("Pase la ruta como argumento o mapee la unidad Z:.")
        return 1

    print(f"Destino: {target}")
    synced = sync_bridge_script(target)
    print(f"Script:  {synced}")

    ok_n = 0
    for py in _python_candidates():
        ok, msg = install_vendor_for(py, target)
        mark = "OK" if ok else "SKIP"
        print(f"[{mark}] {py.name} ({py.parent.name}): {msg}")
        if ok:
            ok_n += 1

    if ok_n == 0:
        print("ERROR: no se pudo instalar _vendor para ningun Python.")
        return 1

    print(f"\nListo: {ok_n} version(es) de Python con psycopg2 en {target / '_vendor'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
