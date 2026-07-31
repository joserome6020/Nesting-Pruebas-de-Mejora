"""Diagnostico fill con DXF real GENE-VFM-20-102 + guests del corpus."""
from __future__ import annotations

import copy
import glob
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from shapely import affinity
from shapely.geometry import Polygon

from modules.nesting_engine.display_geometry import _cargar_poly_local_dxf
from modules.nesting_engine.geometry_parser import poligonos_desde_shapely
from modules.nesting_engine.venom_hole_fill import (
    _candidate_translations,
    _guest_variants,
    _host_open_profile,
    _place_ok,
    fill_host_cavities,
    list_host_cavities,
)

INCH = 25.4
DXF_DIR = _ROOT / "benchmarks/corpus_real/r_giga_cal11/dxf"
VFM = DXF_DIR / "GENE-VFM-20-102, A 36 Galv, QTY 8, Cal 0.11811.dxf"


def _mk(nombre: str, gp, x: float, y: float) -> dict:
    g = affinity.translate(gp, x, y)
    return {
        "nombre": nombre,
        "area": float(g.area),
        "poly": g,
        "poly_exact": g,
        "poligonos": poligonos_desde_shapely(g),
    }


def _load_guests() -> list[dict]:
    guests = []
    for i, p in enumerate(sorted(glob.glob(str(DXF_DIR / "GENE-BKT*.dxf")))[:10]):
        loaded = _cargar_poly_local_dxf(p)
        if not loaded:
            continue
        gp = loaded[0]
        w = (gp.bounds[2] - gp.bounds[0]) / INCH
        h = (gp.bounds[3] - gp.bounds[1]) / INCH
        print(f"BKT {Path(p).name[:48]} wh={w:.2f}x{h:.2f} area={gp.area/(INCH*INCH):.1f}")
        guests.append(
            _mk(Path(p).stem.split(",")[0] + f"#{i}", gp, (2 + (i % 5) * 8) * INCH, (28 + (i // 5) * 6) * INCH)
        )
    for i, p in enumerate(sorted(glob.glob(str(DXF_DIR / "GENE-35*.dxf")))[:12]):
        loaded = _cargar_poly_local_dxf(p)
        if not loaded:
            continue
        gp = loaded[0]
        w = (gp.bounds[2] - gp.bounds[0]) / INCH
        h = (gp.bounds[3] - gp.bounds[1]) / INCH
        print(f"35  {Path(p).name[:48]} wh={w:.2f}x{h:.2f} area={gp.area/(INCH*INCH):.1f}")
        guests.append(
            _mk(Path(p).stem.split(",")[0] + f"#g{i}", gp, (2 + (i % 6) * 7) * INCH, (42 + (i // 6) * 5) * INCH)
        )
    return guests


def main() -> int:
    loaded = _cargar_poly_local_dxf(str(VFM))
    assert loaded, "VFM DXF not loaded"
    poly = loaded[0]
    op = _host_open_profile(poly)
    cavs = list_host_cavities(poly, open_profile=op)
    print(
        f"VFM open={op} fill_ratio={poly.area / max((poly.bounds[2]-poly.bounds[0])*(poly.bounds[3]-poly.bounds[1]),1):.3f} "
        f"interiors={len(list(poly.interiors))} ncav={len(cavs)}"
    )
    for i, c in enumerate(cavs):
        print(
            f"  cav{i} in2={c.area/(INCH*INCH):.1f} "
            f"w={(c.bounds[2]-c.bounds[0])/INCH:.2f} h={(c.bounds[3]-c.bounds[1])/INCH:.2f}"
        )

    guests = _load_guests()
    print(f"guests={len(guests)}")

    for kerf in (0.006, 0.02, 0.118, 0.3):
        h1 = _mk("GENE-VFM-20-102", poly, 10 * INCH, 2 * INCH)
        h2 = _mk("GENE-VFM-20-102#2", poly, 10 * INCH, 14 * INCH)
        gcopy = [_mk(g["nombre"], g["poly"], 0, 0) for g in guests]  # already placed
        # rebuild clean guests
        guests_k = _load_guests() if kerf == 0.006 else guests
        if kerf != 0.006:
            guests_k = []
            for i, p in enumerate(sorted(glob.glob(str(DXF_DIR / "GENE-BKT*.dxf")))[:10]):
                loaded_g = _cargar_poly_local_dxf(p)
                if not loaded_g:
                    continue
                guests_k.append(
                    _mk(
                        Path(p).stem.split(",")[0] + f"#{i}",
                        loaded_g[0],
                        (2 + (i % 5) * 8) * INCH,
                        (28 + (i // 5) * 6) * INCH,
                    )
                )
            for i, p in enumerate(sorted(glob.glob(str(DXF_DIR / "GENE-35*.dxf")))[:12]):
                loaded_g = _cargar_poly_local_dxf(p)
                if not loaded_g:
                    continue
                guests_k.append(
                    _mk(
                        Path(p).stem.split(",")[0] + f"#g{i}",
                        loaded_g[0],
                        (2 + (i % 6) * 7) * INCH,
                        (42 + (i // 6) * 5) * INCH,
                    )
                )
        hoja = {
            "placa_w": 120 * INCH,
            "placa_h": 60 * INCH,
            "kerf_usado": kerf,
            "piezas": [h1, h2] + guests_k,
        }
        st = fill_host_cavities(hoja, f"kerf{kerf}")
        print(
            f"KERF={kerf} filled={st['filled']} area_in2={st['area_filled']/(INCH*INCH):.1f} "
            f"cav={st['cavities']} gout={st['guests_out']} reverted={st.get('reverted')}"
        )

    # Reject reasons @ kerf 0.3
    print("--- reject @0.3 ---")
    kerf_half = (0.3 * INCH) / 2.0
    hpoly = affinity.translate(poly, 10 * INCH, 2 * INCH)
    hcavs = list_host_cavities(hpoly, open_profile=True)
    other_host = affinity.translate(poly, 10 * INCH, 14 * INCH)
    guest_polys = []
    for i, p in enumerate(sorted(glob.glob(str(DXF_DIR / "GENE-BKT*.dxf")))[:10]):
        loaded_g = _cargar_poly_local_dxf(p)
        if loaded_g:
            guest_polys.append(
                (
                    Path(p).stem.split(",")[0],
                    affinity.translate(loaded_g[0], (2 + (i % 5) * 8) * INCH, (28 + (i // 5) * 6) * INCH),
                )
            )
    for i, p in enumerate(sorted(glob.glob(str(DXF_DIR / "GENE-35*.dxf")))[:12]):
        loaded_g = _cargar_poly_local_dxf(p)
        if loaded_g:
            guest_polys.append(
                (
                    Path(p).stem.split(",")[0],
                    affinity.translate(loaded_g[0], (2 + (i % 6) * 7) * INCH, (42 + (i // 6) * 5) * INCH),
                )
            )

    for cavity in sorted(hcavs, key=lambda c: c.area, reverse=True):
        cw = cavity.bounds[2] - cavity.bounds[0]
        ch = cavity.bounds[3] - cavity.bounds[1]
        print(f"CAV in2={cavity.area/(INCH*INCH):.1f} wh={cw/INCH:.2f}x{ch/INCH:.2f}")
        legal = cavity.buffer(-max(kerf_half, 0.5), join_style=2)
        print(
            f"  legal_empty={legal is None or legal.is_empty} "
            f"legal_in2={0 if legal is None or legal.is_empty else legal.area/(INCH*INCH):.1f}"
        )
        n_place = 0
        n_bbox = 0
        n_fail = 0
        fail_samples = []
        for name, gpoly in guest_polys:
            if gpoly.area > cavity.area * 0.95:
                continue
            placed = False
            fits = False
            why = "nobbox"
            for angle, centered in _guest_variants(gpoly):
                gw = centered.bounds[2] - centered.bounds[0]
                gh = centered.bounds[3] - centered.bounds[1]
                if not (
                    (gw + kerf_half <= cw + 0.5 and gh + kerf_half <= ch + 0.5)
                    or (gh + kerf_half <= cw + 0.5 and gw + kerf_half <= ch + 0.5)
                ):
                    continue
                fits = True
                cands = _candidate_translations(centered, cavity, kerf_half)
                if not cands:
                    why = "nocands"
                    continue
                others = [other_host] + [gp for n, gp in guest_polys if n != name or True]
                # fix others: all other guests
                others = [other_host] + [gp for n2, gp in guest_polys if gp is not gpoly]
                for cx, cy in cands[:60]:
                    test = affinity.translate(centered, cx, cy)
                    if _place_ok(test, cavity, others, hpoly, kerf_half):
                        placed = True
                        n_place += 1
                        print(f"  OK {name} ang={angle} wh={gw/INCH:.2f}x{gh/INCH:.2f}")
                        break
                    # classify first failure mode once
                    if why == "nobbox" or why == "nocands":
                        c = test.centroid
                        if not (cavity.contains(c) or cavity.covers(c)):
                            why = "centroid"
                        else:
                            inside = test.intersection(cavity)
                            if getattr(inside, "area", 0) < float(test.area) * 0.95:
                                why = f"inside95={getattr(inside,'area',0)/max(test.area,1):.2f}"
                            else:
                                inter_m = test.intersection(hpoly)
                                if getattr(inter_m, "area", 0) > 0.05:
                                    why = f"metal={inter_m.area:.1f}"
                                else:
                                    why = "guest_buf"
                if placed:
                    break
            if not placed:
                if not fits:
                    n_bbox += 1
                else:
                    n_fail += 1
                    if len(fail_samples) < 5:
                        fail_samples.append((name, why, (gpoly.bounds[2]-gpoly.bounds[0])/INCH, (gpoly.bounds[3]-gpoly.bounds[1])/INCH))
        print(f"  summary placeable_alone={n_place} nobbox={n_bbox} fail={n_fail} samples={fail_samples}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
