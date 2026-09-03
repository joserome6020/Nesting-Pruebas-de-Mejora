"""Candado: Ultra no rechaza una pieza que cabe metal+margen por inflar con kerf.

Caso real (Cambiar de placa → PLC058 120×48, cal 0.3125 A36):
  pieza 81.037\" × 47.374\", margen PLACA→PIEZA 0.200\", ENTRE PIEZAS 0.250\".

  metal + 2*margen = 47.374 + 0.400 = 47.774 < 48  → DEBE caber 1
  metal + kerf + 2*margen = 47.374 + 0.250 + 0.400 = 48.024  (falsa exclusión)

Bug: generate_variations medía el AABB del buffer de kerf contra placa−2·margen
y descartaba todas las rotaciones → placed=0 / \"Ninguna pieza cupo\".
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

IN_TO_MM = 25.4
PLATE_W_IN = 120.0
PLATE_H_IN = 48.0
PIECE_W_IN = 81.037
PIECE_H_IN = 47.374
MARGIN_IN = 0.200
KERF_IN = 0.250  # ENTRE PIEZAS — no debe tumbar el cupo al borde


def _register_native_dlls() -> None:
    import glob

    if not hasattr(os, "add_dll_directory"):
        return
    dirs = [ROOT / "modules" / "nesting_engine"]
    cuda = os.environ.get("CUDA_PATH") or ""
    if cuda:
        dirs.append(Path(cuda) / "bin")
    dirs.extend(
        Path(p) / "bin"
        for p in glob.glob(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*")
    )
    for d in dirs:
        if d.is_dir():
            try:
                os.add_dll_directory(str(d))
            except OSError:
                pass


def _rect_piece(name: str, w_in: float, h_in: float) -> dict:
    w = w_in * IN_TO_MM
    h = h_in * IN_TO_MM
    ring = [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h), (0.0, 0.0)]
    return {
        "nombre": name,
        "area": w * h,
        "calibre": "0.3125",
        "material": "A36",
        "rings": [ring],
        "marks": [],
    }


def main() -> int:
    _register_native_dlls()
    os.environ["ARGA_NEST_CORE"] = "0"

    usable_h = PLATE_H_IN - (2.0 * MARGIN_IN)
    assert PIECE_H_IN < usable_h, "precondición del candado: metal debe caber con margen"
    assert (PIECE_H_IN + KERF_IN) > usable_h, (
        "precondición: metal+kerf debe NO caber (reproduce el bug viejo)"
    )

    from modules.nesting_engine import algorithm_cpp as cpp

    piezas = [
        _rect_piece("PLC058-FIT-A", PIECE_W_IN, PIECE_H_IN),
        _rect_piece("PLC058-FIT-B", PIECE_W_IN, PIECE_H_IN),
    ]
    w_mm = PLATE_W_IN * IN_TO_MM
    h_mm = PLATE_H_IN * IN_TO_MM

    hoja, restos, *_ = cpp.empaquetar_una_hoja_svgnest_ultra(
        piezas,
        w_mm,
        h_mm,
        KERF_IN,
        MARGIN_IN,
        "OPTIMIZAR LARGO Y ANCHO",
        "INFERIOR IZQUIERDA",
        None,
        4,
        1,
        90.0,
        False,
        1,
        None,
    )
    colocadas = list(hoja.get("piezas") or [])
    n_restos = len(restos or [])
    assert len(colocadas) == 1, (
        f"Ultra debe colocar exactamente 1 (metal+margen cabe; 2 no caben en 120×48); "
        f"got placed={len(colocadas)} restos={n_restos}"
    )
    assert n_restos == 1, f"la segunda queda en restos; got restos={n_restos}"

    # Metal ≥ margen en ambos ejes
    minx = miny = 1e18
    maxx = maxy = -1e18
    for p in colocadas:
        for ring in p.get("poligonos") or []:
            for pt in ring:
                x, y = float(pt[0]), float(pt[1])
                minx, miny = min(minx, x), min(miny, y)
                maxx, maxy = max(maxx, x), max(maxy, y)
    margin_mm = MARGIN_IN * IN_TO_MM
    assert minx + 1e-6 >= margin_mm, f"metal X={minx:.4f} < margen {margin_mm:.4f}"
    assert miny + 1e-6 >= margin_mm, f"metal Y={miny:.4f} < margen {margin_mm:.4f}"
    assert maxx <= w_mm - margin_mm + 1e-6, f"metal maxX={maxx:.4f} > placa−margen"
    assert maxy <= h_mm - margin_mm + 1e-6, f"metal maxY={maxy:.4f} > placa−margen"

    print(
        f"ULTRA_PLATE_FIT_METAL_NOT_KERF PASS placed=1 restos=1 "
        f"metal_y=[{miny:.3f},{maxy:.3f}] margin={margin_mm:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
