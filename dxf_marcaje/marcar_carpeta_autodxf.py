#!/usr/bin/env python3
"""
Marcaje stick para carpeta AutoDXF (Inventor).

Especificación actual:
- Texto = primer segmento del nombre DXF (antes de la 1ª coma)
  ej. "62135-1247-P01, A 36, QTY 1, Cal 0.375.dxf" -> "62135-1247-P01"
- Tipografía monolinea (palitos)
- Altura visible: 0.25–0.35 in (reescala al tamaño; piezas chicas <0.25)
- Clearance: 0.04 in
- Capa destino AutoDXF: IV_MARK_SURFACE_BACK
- No solapa con cortes / marks / cualquier geometría
- Busca DXF recursivamente en subcarpetas

Uso:
  python dxf_marcaje/marcar_carpeta_autodxf.py
  python dxf_marcaje/marcar_carpeta_autodxf.py "\\\\servidor\\...\\AutoDXF"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from modules.dxf_mark.inject import (  # noqa: E402
    AUTODXF_MARK_LAYER,
    DEFAULT_CLEARANCE_IN,
    DEFAULT_TEXT_HEIGHT_IN,
    mark_text_from_dxf_path,
)
from modules.dxf_mark.pipeline import aplicar_marcaje_autodxf  # noqa: E402


def seleccionar_carpeta() -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(title="Seleccionar carpeta AutoDXF / DXF")
        root.destroy()
        return Path(folder) if folder else None
    except Exception:
        raw = input("Carpeta con DXF: ").strip().strip('"')
        return Path(raw) if raw else None


def salida_marked(path: Path, root: Path, out_dir: Path | None) -> Path:
    nombre = f"{path.stem}_MARKED{path.suffix}"
    if out_dir is None:
        return path.with_name(nombre)
    rel_parent = path.parent.relative_to(root)
    target_dir = out_dir / rel_parent
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / nombre


def iter_dxf(folder: Path, include_marked: bool) -> list[Path]:
    files = sorted(p for p in folder.rglob("*.dxf") if p.is_file())
    if include_marked:
        return files
    return [p for p in files if "_MARKED" not in p.stem.upper()]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Marca recursivamente DXF de AutoDXF en capa IV_MARK_SURFACE_BACK. "
            "Pide carpeta si no se pasa argumento."
        )
    )
    p.add_argument("folder", nargs="?", type=Path, help="Carpeta raíz a procesar")
    p.add_argument(
        "--height-in",
        type=float,
        default=DEFAULT_TEXT_HEIGHT_IN,
        help=f"Altura visible MARK (default {DEFAULT_TEXT_HEIGHT_IN} in)",
    )
    p.add_argument(
        "--clearance-in",
        type=float,
        default=DEFAULT_CLEARANCE_IN,
        help=f"Holgura mínima (default {DEFAULT_CLEARANCE_IN} in)",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Carpeta de salida. Si se omite, guarda *_MARKED junto a cada DXF.",
    )
    p.add_argument(
        "--include-marked",
        action="store_true",
        help="También procesa archivos con _MARKED en el nombre.",
    )
    p.add_argument(
        "--skip-existing",
        action="store_true",
        help="No sobrescribe salidas *_MARKED existentes.",
    )
    p.add_argument("--pick", action="store_true", help="Forzar diálogo de carpeta")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    folder = args.folder
    if args.pick or folder is None:
        folder = seleccionar_carpeta()
        if folder is None:
            print("Cancelado: no se eligió carpeta.")
            return 1

    folder = folder.resolve()
    if not folder.is_dir():
        print(f"No existe carpeta: {folder}")
        return 1

    files = iter_dxf(folder, include_marked=args.include_marked)
    print("===========================================")
    print(" Marcaje AutoDXF (stick)")
    print(f" Capa: {AUTODXF_MARK_LAYER}")
    print(f" Altura visible: {args.height_in} in")
    print(f" Clearance: {args.clearance_in} in")
    print(f" Carpeta: {folder}")
    print(f" DXF encontrados: {len(files)}")
    print("===========================================")

    ok = 0
    skipped = 0
    errors = 0

    for i, path in enumerate(files, start=1):
        out = salida_marked(path, folder, args.out_dir)
        text = mark_text_from_dxf_path(path)
        if args.skip_existing and out.exists():
            skipped += 1
            print(f"[{i}/{len(files)}] SKIP existe: {out.name}")
            continue
        try:
            result = aplicar_marcaje_autodxf(
                path,
                out,
                text_height_in=args.height_in,
                clearance_in=args.clearance_in,
            )
            if result.already_marked:
                skipped += 1
                print(
                    f"[{i}/{len(files)}] SKIP ya marcado: {path.name} "
                    f"(capa {AUTODXF_MARK_LAYER} / tag Arga)"
                )
                continue
            ok += 1
            print(
                f"[{i}/{len(files)}] OK {path.name} -> {out.name} "
                f"MARK={result.mark_text!r} layer={AUTODXF_MARK_LAYER}"
            )
        except Exception as exc:
            errors += 1
            print(f"[{i}/{len(files)}] ERROR {path.name}: {exc}")

    print("===========================================")
    print(f"Resumen: OK={ok} | SKIP={skipped} | ERROR={errors} | TOTAL={len(files)}")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
