"""Candado: Ultra de planta no empaca por ArgaNestCore.

Motivo real: 0.105 GALVANIZADO / GENE-SIVC-40-40-113 salió a
gaps_mm=(4.86, 4.86, …) vs margin_tabla=0.250in (6.35 mm). El packer
algorithm_cpp ya colocaba ≥6.35; ANS con ARGA_NEST_CORE=1 desviaba el
pack de hoja a ArgaNestCore (sin la tabla) y el pokayoke lo mandaba a
BORRADOR.

El wrapper Ultra debe ir a algorithm_cpp aunque el env pida CORE=1.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MARGIN_IN = 0.250
MARGIN_MM = MARGIN_IN * 25.4
# Caso real: AABB SIVC-113 (~70.24" × 5.94") en placa 120" × 48".
PIECE_W_MM = 1784.148
PIECE_H_MM = 150.790
PLATE_W_MM = 120.0 * 25.4
PLATE_H_MM = 48.0 * 25.4


def _assert_source_routes_to_algorithm_cpp() -> None:
    bridge = (ROOT / "modules" / "nesting_engine" / "algorithm_bridge.py").read_text(
        encoding="utf-8"
    )
    ultra_src = bridge.split("def empaquetar_una_hoja_svgnest_ultra(", 1)[1]
    ultra_src = ultra_src.split("\ndef empaquetar_una_hoja_", 1)[0]
    assert "pack_sheet_json" not in ultra_src, (
        "empaquetar_una_hoja_svgnest_ultra volvió a desviar el pack a ArgaNestCore. "
        "Eso deja Galv a 4.86 mm del borde."
    )
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'setdefault("ARGA_NEST_CORE", "0")' in main_py, (
        "main.py debe arrancar con ARGA_NEST_CORE=0 (algorithm_cpp). "
        "CORE=1 no respeta 0.250\" de placa."
    )


def _min_xy_from_hoja(hoja: dict) -> tuple[float, float]:
    minx = miny = 1e9
    for p in hoja.get("piezas") or []:
        poly = p.get("poly")
        if poly is not None and not getattr(poly, "is_empty", True):
            bx = poly.bounds
            minx = min(minx, float(bx[0]))
            miny = min(miny, float(bx[1]))
            continue
        for ring in p.get("poligonos") or []:
            for pt in ring:
                minx = min(minx, float(pt[0]))
                miny = min(miny, float(pt[1]))
    return minx, miny


def _register_native_dlls() -> None:
    import glob

    engine_dir = ROOT / "modules" / "nesting_engine"
    if not hasattr(os, "add_dll_directory"):
        return
    dirs = [engine_dir]
    cuda_path = os.environ.get("CUDA_PATH") or ""
    if cuda_path:
        dirs.append(Path(cuda_path) / "bin")
    dirs.extend(Path(p) / "bin" for p in glob.glob(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*"))
    for d in dirs:
        if d.is_dir():
            try:
                os.add_dll_directory(str(d))
            except OSError:
                pass


def main() -> int:
    _assert_source_routes_to_algorithm_cpp()

    os.environ["ARGA_NEST_CORE"] = "1"
    _register_native_dlls()
    from shapely.geometry import box

    from modules.nesting_engine.algorithm_bridge import empaquetar_una_hoja_svgnest_ultra

    pieza = {
        "nombre": "GENE-SIVC-40-40-113",
        "calibre": "0.105",
        "material": "GALVANIZADO",
        "poly": box(0.0, 0.0, PIECE_W_MM, PIECE_H_MM),
        "area": PIECE_W_MM * PIECE_H_MM,
    }
    try:
        hoja, _restos = empaquetar_una_hoja_svgnest_ultra(
            [pieza],
            PLATE_W_MM,
            PLATE_H_MM,
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
        print(f"ULTRA_PACK_NOT_CORE PASS (source) skip_pack={type(exc).__name__}: {exc}")
        return 0

    assert hoja.get("piezas"), "Ultra no colocó GENE-SIVC-40-40-113"
    minx, miny = _min_xy_from_hoja(hoja)
    assert minx + 1e-6 >= MARGIN_MM, (
        f"con ARGA_NEST_CORE=1 el metal X={minx:.4f} mm < 0.250in "
        f"({MARGIN_MM:.4f}); el caso real era 4.86 mm"
    )
    assert miny + 1e-6 >= MARGIN_MM, (
        f"con ARGA_NEST_CORE=1 el metal Y={miny:.4f} mm < 0.250in "
        f"({MARGIN_MM:.4f}); el caso real era 4.86 mm"
    )
    print(
        f"ULTRA_PACK_NOT_CORE PASS xy=({minx:.3f},{miny:.3f}) "
        f"min={MARGIN_MM:.3f} CORE_env={os.environ.get('ARGA_NEST_CORE')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
