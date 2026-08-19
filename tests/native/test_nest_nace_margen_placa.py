"""Candado: el nest NACE con metal ≥ 0.250\" de placa (sin empujar después).

Motivo real: Galv SIVC-113 / OP-1220 / BKS salían a ~4.86 mm. El globo de
kerf se empaquetaba contra 0.250\" y el metal quedaba a 0.250\" − kerf/2.
Compact/Lite/Ultra deben aceptar solo poses cuyo METAL ya cumple la tabla.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MARGIN_IN = 0.250
MARGIN_MM = MARGIN_IN * 25.4
PLATE_W = 120.0 * 25.4
PLATE_H = 48.0 * 25.4
PIECE_W = 1784.148
PIECE_H = 150.790


def _register_native_dlls() -> None:
    import glob

    if not hasattr(os, "add_dll_directory"):
        return
    dirs = [ROOT / "modules" / "nesting_engine"]
    cuda = os.environ.get("CUDA_PATH") or ""
    if cuda:
        dirs.append(Path(cuda) / "bin")
    dirs.extend(Path(p) / "bin" for p in glob.glob(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*"))
    for d in dirs:
        if d.is_dir():
            try:
                os.add_dll_directory(str(d))
            except OSError:
                pass


def _min_xy(hoja: dict) -> tuple[float, float]:
    minx = miny = 1e9
    for p in hoja.get("piezas") or []:
        for ring in p.get("poligonos") or []:
            for pt in ring:
                minx = min(minx, float(pt[0]))
                miny = min(miny, float(pt[1]))
        poly = p.get("poly")
        if poly is not None and not getattr(poly, "is_empty", True):
            b = poly.bounds
            minx = min(minx, float(b[0]))
            miny = min(miny, float(b[1]))
    return minx, miny


def _assert_legal(hoja: dict, tag: str) -> tuple[float, float]:
    assert hoja.get("piezas"), f"{tag}: no colocó"
    minx, miny = _min_xy(hoja)
    assert minx + 1e-6 >= MARGIN_MM, f"{tag} metal X={minx:.4f} < 0.250in (era 4.86 mm en planta)"
    assert miny + 1e-6 >= MARGIN_MM, f"{tag} metal Y={miny:.4f} < 0.250in (era 4.86 mm en planta)"
    return minx, miny


def main() -> int:
    _register_native_dlls()
    os.environ["ARGA_NEST_CORE"] = "0"
    os.environ.setdefault("ARGA_NEST_COMPACT", "1")
    from shapely.geometry import box

    from modules.nesting_engine.algorithm_bridge import (
        empaquetar_una_hoja_arga_lite,
        empaquetar_una_hoja_svgnest_ultra,
    )
    from modules.nesting_engine.engine_registry import empaquetar_una_hoja
    from modules.nesting_engine import compact_lite

    pieza = {
        "nombre": "GENE-SIVC-40-40-113",
        "calibre": "0.105",
        "material": "GALVANIZADO",
        "poly": box(0.0, 0.0, PIECE_W, PIECE_H),
        "area": PIECE_W * PIECE_H,
    }
    try:
        hoja_u, _ = empaquetar_una_hoja_svgnest_ultra(
            [dict(pieza)],
            PLATE_W,
            PLATE_H,
            0.15,
            MARGIN_IN,
            "OPTIMIZAR LARGO Y ANCHO",
            "INFERIOR IZQUIERDA",
            None,
            1,
            4,
            90.0,
            False,
            None,
        )
    except Exception as exc:
        print(f"NEST_NACE_MARGEN skip_ultra={type(exc).__name__}: {exc}")
        return 0
    xu, yu = _assert_legal(hoja_u, "ultra")

    try:
        hoja_r, _ = empaquetar_una_hoja(
            [dict(pieza)],
            PLATE_W,
            PLATE_H,
            kerf_override=0.15,
            margin_override=MARGIN_IN,
            engine_id="svgnest_ultra",
            mc_iterations=1,
        )
        xr, yr = _assert_legal(hoja_r, "registry")
    except Exception as exc:
        print(f"NEST_NACE_MARGEN skip_registry={type(exc).__name__}: {exc}")
        xr, yr = xu, yu

    hoja_l, _ = empaquetar_una_hoja_arga_lite(
        [dict(pieza)],
        PLATE_W,
        PLATE_H,
        0.15,
        MARGIN_IN,
        "OPTIMIZAR LARGO Y ANCHO",
        "INFERIOR IZQUIERDA",
        None,
        1,
    )
    xl, yl = _assert_legal(hoja_l, "lite")

    hoja_l.setdefault("placa_w", PLATE_W)
    hoja_l.setdefault("placa_h", PLATE_H)
    hoja_l.setdefault("kerf_usado", 0.15)
    hoja_l.setdefault("margin_usado", MARGIN_IN)
    compact_lite.apply_band_compact(hoja_l, engine_id="arga_lite")
    compact_lite._gravity_slide_exterior(
        hoja_l, skip_idxs=set(), rigid_children={}, engine_id="arga_lite"
    )
    xg, yg = _assert_legal(hoja_l, "lite+gravity")

    print(
        f"NEST_NACE_MARGEN PASS ultra=({xu:.3f},{yu:.3f}) "
        f"registry=({xr:.3f},{yr:.3f}) lite=({xl:.3f},{yl:.3f}) "
        f"grav=({xg:.3f},{yg:.3f}) min={MARGIN_MM:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
