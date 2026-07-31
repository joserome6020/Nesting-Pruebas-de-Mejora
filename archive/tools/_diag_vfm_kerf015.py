"""Fill real VFM channels with thin corpus guests at kerf 0.1 / 0.15."""
from __future__ import annotations

import glob
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from shapely import affinity

from modules.nesting_engine.display_geometry import _cargar_poly_local_dxf
from modules.nesting_engine.geometry_parser import poligonos_desde_shapely
from modules.nesting_engine.venom_hole_fill import fill_host_cavities

INCH = 25.4
DXF = _ROOT / "benchmarks/corpus_real/r_giga_cal11/dxf"


def _mk(nombre, gp, x, y):
    g = affinity.translate(gp, x, y)
    return {
        "nombre": nombre,
        "area": float(g.area),
        "poly": g,
        "poly_exact": g,
        "poligonos": poligonos_desde_shapely(g),
    }


def _load_one(pattern: str):
    for p in sorted(glob.glob(str(DXF / pattern))):
        if "__" in Path(p).name:
            continue
        loaded = _cargar_poly_local_dxf(p)
        if loaded:
            return Path(p).stem.split(",")[0], loaded[0]
    return None, None


def main() -> int:
    vname, vpoly = _load_one("GENE-VFM-20-102*.dxf")
    assert vpoly is not None
    guests_src = []
    for pat in ("GENE-BKT-287*.dxf", "GENE-GS*.dxf", "GENE-BKT-308*.dxf", "GENE-BKT-304*.dxf"):
        n, g = _load_one(pat)
        if g is not None:
            guests_src.append((n, g))
            print(f"guest {n} wh={((g.bounds[2]-g.bounds[0])/INCH):.2f}x{((g.bounds[3]-g.bounds[1])/INCH):.2f}")

    for kerf in (0.1, 0.15, 0.3):
        h1 = _mk("GENE-VFM-20-102", vpoly, 5 * INCH, 2 * INCH)
        h2 = _mk("GENE-VFM-20-102#2", vpoly, 5 * INCH, 16 * INCH)
        guests = []
        for i, (n, g) in enumerate(guests_src * 3):  # several copies
            guests.append(_mk(f"{n}#{i}", g, (2 + (i % 5) * 8) * INCH, (32 + (i // 5) * 6) * INCH))
        hoja = {"placa_w": 120 * INCH, "placa_h": 60 * INCH, "kerf_usado": kerf, "piezas": [h1, h2] + guests}
        st = fill_host_cavities(hoja, f"k{kerf}")
        print(f"kerf={kerf} filled={st['filled']} area_in2={st['area_filled']/(INCH*INCH):.1f} gout={st['guests_out']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
