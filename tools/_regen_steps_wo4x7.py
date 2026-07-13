"""Regenera STEP Robot Laser: sanitiza DXF y convierte con FreeCAD (Cama A/B)."""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from freecad_runner import ejecutar_macro_freecad

_tools = Path(__file__).resolve().parent
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
from _sanitize_laser_dxf import sanitize_laser_dxf

PAT = re.compile(r"NESTING_([0-9]+(?:\.[0-9]+)?)_", re.IGNORECASE)

def _group_dxfs_by_thk_in_from_dir(dxf_dir: str) -> dict[float, set[str]]:
    groups: dict[float, set[str]] = defaultdict(set)
    for path in sorted(glob.glob(os.path.join(dxf_dir, "*.dxf"))):
        if ".tmp_" in os.path.basename(path):
            continue
        name = os.path.basename(path)
        m = PAT.search(name)
        thk_in = float(m.group(1)) if m else 0.25
        groups[thk_in].add(name)
    return groups


def _purge_steps(step_dir: str) -> int:
    n = 0
    for path in glob.glob(os.path.join(step_dir, "*.step")):
        try:
            os.remove(path)
            n += 1
        except OSError as exc:
            print(f"[WARN] no se pudo borrar {path}: {exc}")
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description="Sanitiza DXF láser y regenera STEP (Cama A/B).")
    parser.add_argument(
        "base",
        nargs="?",
        default=(
            r"C:\Users\aaron_orrantia\OneDrive - grupoarga.com\Escritorio\Nesteos Locales"
            r"\GIGA\GIGA L3\MODEL CORE FILES\W.O. 4 X7\ARGA MODEL CORE\NESTING"
            r"\ROBOT LASER + MINI NEST"
        ),
        help="Carpeta del canal (contiene DXF/ y STEP/).",
    )
    args = parser.parse_args()

    base = os.path.normpath(args.base)
    dxf_dir = os.path.join(base, "DXF")
    step_a = os.path.join(base, "STEP", "Cama A")
    step_b = os.path.join(base, "STEP", "Cama B")

    if not os.path.isdir(dxf_dir):
        print(f"[ERROR] No existe carpeta DXF: {dxf_dir}")
        return 1

    os.makedirs(step_a, exist_ok=True)
    os.makedirs(step_b, exist_ok=True)

    removed_a = _purge_steps(step_a)
    removed_b = _purge_steps(step_b)
    print(f"[OK] {base}")
    print(f"[OK] STEP viejos eliminados: Cama A={removed_a}, Cama B={removed_b}")

    print("\n=== SANITIZANDO DXF (quitar capas cobre fantasma) ===")
    dxf_files = sorted(
        p
        for p in glob.glob(os.path.join(dxf_dir, "*.dxf"))
        if "_clean" not in p.replace("\\", "/")
        and ".tmp_" not in os.path.basename(p)
    )
    clean_dir = os.path.join(dxf_dir, "_clean")
    use_dxf_dir = dxf_dir
    blocked = 0
    for path in dxf_files:
        try:
            removed, final, _ents = sanitize_laser_dxf(path)
            print(f"[DXF] {os.path.basename(path)}: -{len(removed)} capas -> {final}")
        except PermissionError:
            blocked += 1
            os.makedirs(clean_dir, exist_ok=True)
            clean_path = os.path.join(clean_dir, os.path.basename(path))
            removed, final, _ents = sanitize_laser_dxf(path, out_path=clean_path)
            print(f"[DXF] {os.path.basename(path)} (bloqueado, copia limpia): -{len(removed)} -> {final}")
    if os.path.isdir(clean_dir):
        clean_files = [
            p
            for p in glob.glob(os.path.join(clean_dir, "*.dxf"))
            if ".tmp_" not in os.path.basename(p)
        ]
        if len(clean_files) >= len(dxf_files):
            use_dxf_dir = clean_dir
            print(f"[INFO] STEP desde carpeta DXF limpia: {use_dxf_dir} ({len(clean_files)} archivos)")
    elif blocked:
        use_dxf_dir = clean_dir
        print(f"[WARN] {blocked} DXF bloqueados; STEP se generará desde: {use_dxf_dir}")

    groups = _group_dxfs_by_thk_in_from_dir(use_dxf_dir)
    if not groups:
        print("[ERROR] Sin DXF en la carpeta")
        return 1

    print(f"[INFO] Grupos por espesor: {len(groups)}")
    for thk_in in sorted(groups):
        print(f"  - {thk_in}\" -> {len(groups[thk_in])} DXF")

    os.environ["FREECAD_PLASMA_OFFSET"] = "0.0"
    ok_all = True

    for thk_in in sorted(groups):
        names = groups[thk_in]
        thk_mm = thk_in * 25.4

        def _filt(path: str, allowed: set[str] = names) -> bool:
            return os.path.basename(path) in allowed

        print(f"\n=== Espesor {thk_in}\" ({thk_mm:.3f} mm) | {len(names)} DXF ===")

        for tag, step_dir, origen, ox, oy, oz in (
            ("A", step_a, "TR", 4235.0, -1015.0, -700.0),
            ("B", step_b, "BR", 4235.0, 840.0, -700.0),
        ):
            print(f"[STEP] Cama {tag} ...")
            ok = ejecutar_macro_freecad(
                use_dxf_dir,
                step_dir,
                thk_mm,
                origen,
                ox,
                oy,
                oz,
                prefer_verde=True,
                max_intentos=2,
                material="STEEL",
                export_format="step",
                dxf_filter=_filt,
            )
            print(f"[STEP] Cama {tag} => {'OK' if ok else 'FAIL'}")
            ok_all = ok_all and ok

    # Resumen final
    steps_a = len(glob.glob(os.path.join(step_a, "*.step")))
    steps_b = len(glob.glob(os.path.join(step_b, "*.step")))
    dxfs = len(dxf_files)
    print(f"\n=== RESUMEN ===")
    print(f"DXF: {dxfs} | STEP Cama A: {steps_a} | STEP Cama B: {steps_b}")
    return 0 if ok_all and steps_a >= dxfs and steps_b >= dxfs else 2


if __name__ == "__main__":
    raise SystemExit(main())
