#!/usr/bin/env python3
"""CLI: inyecta marcaje stick en un DXF (capa MARK, 0.25 in visible)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Raíz del proyecto en sys.path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from modules.dxf_mark.inject import (
    DEFAULT_CLEARANCE_IN,
    DEFAULT_TEXT_HEIGHT_IN,
    inject_mark_into_dxf,
    mark_text_from_dxf_path,
    prompt_dxf,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Inyecta marcaje monolinea (palitos) en capa MARK. "
            "Texto = primer segmento del nombre del DXF (antes de la coma)."
        )
    )
    p.add_argument("input_dxf", nargs="?", type=Path, help="DXF de entrada")
    p.add_argument("-o", "--output", type=Path, default=None, help="DXF de salida")
    p.add_argument(
        "--text",
        type=str,
        default=None,
        help="Texto MARK forzado (si se omite, se toma del nombre del archivo)",
    )
    p.add_argument(
        "--height-in",
        type=float,
        default=DEFAULT_TEXT_HEIGHT_IN,
        help=f"Altura visible de tipografía en pulgadas (default {DEFAULT_TEXT_HEIGHT_IN})",
    )
    p.add_argument(
        "--clearance-in",
        type=float,
        default=DEFAULT_CLEARANCE_IN,
        help=f"Holgura mínima contra geometría (default {DEFAULT_CLEARANCE_IN} in)",
    )
    p.add_argument(
        "--replace-mark",
        action="store_true",
        help="Borra entidades existentes en capa MARK antes de inyectar",
    )
    p.add_argument("--pick", action="store_true", help="Forzar diálogo de selección")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    path = args.input_dxf
    if args.pick or path is None:
        path = prompt_dxf()
        if path is None:
            print("Cancelado: no se eligió DXF.")
            return 1

    if not path.is_file():
        print(f"No existe: {path}")
        return 1

    preview = args.text or mark_text_from_dxf_path(path)
    print(f"DXF: {path}")
    print(f"MARK texto: {preview!r}")
    print(f"Altura: {args.height_in} in | clearance: {args.clearance_in} in")

    try:
        result = inject_mark_into_dxf(
            path,
            args.output,
            mark_text=args.text,
            text_height_in=args.height_in,
            clearance_in=args.clearance_in,
            replace_existing_mark=args.replace_mark,
        )
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2

    print(
        f"OK: marcadas={result.components_marked} skipped={result.components_skipped} "
        f"altura_du={result.height_du:.4f}"
    )
    print(f"Salida: {result.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
