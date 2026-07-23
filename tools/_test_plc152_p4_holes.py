"""Sim ARGA Base — BOM PLC152-P4 (76 PZAS). Métrica clave: in_holes > 0."""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

try:
    from modules.win_dll_bootstrap import bootstrap_proceso_nesting

    bootstrap_proceso_nesting()
except Exception:
    pass

from shapely.geometry import Polygon  # noqa: E402

from modules.nesting_engine.sim_lab import (  # noqa: E402
    SimPieceEntry,
    build_pieces_from_entries,
    inches_to_mm,
    run_plate_sim,
)

DXF_DIR = (
    r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals"
    r"\ARGA METALS CORPORATE SYSTEM\TANKS\GIGA\GIGA FLUIDSTACK"
    r"\MODEL CORE FILES\AutoDXF\Processed Files"
)
OUT_DIR = os.path.join(_ROOT, "_logs", "sim_plc152_p4_holes")
PLATE_W_IN = 120.0
PLATE_H_IN = 48.0
KERF_IN = 0.1
MARGIN_IN = 0.15

# BOM de captura UI PLC152-P4 (76 piezas)
TABLA = [
    ("GENE-VFM-20-101", 3),
    ("GENE-HFM-10-102", 4),
    ("GENE-BKT-295", 1),
    ("GENE-BKT-320", 2),
    ("GENE-BKT-270", 1),
    ("GENE-BKT-271", 6),
    ("GENE-GS-0820-708", 41),
    ("GENE-BKT-369", 18),
]


def _find_dxf(item: str) -> str | None:
    if not os.path.isdir(DXF_DIR):
        return None
    files = os.listdir(DXF_DIR)
    cu = item.upper()
    for name in files:
        if not name.lower().endswith(".dxf"):
            continue
        base = name.split(",")[0].strip().upper()
        if base == cu or name.upper().startswith(cu + ",") or name.upper().startswith(cu + " "):
            return os.path.join(DXF_DIR, name)
    return None


def _hole_stats(hoja: dict) -> dict:
    piezas = list(hoja.get("piezas") or [])
    hosts = []
    open_cavs = []
    hole_areas = []
    for p in piezas:
        base = str(p.get("nombre") or "").split("#")[0]
        rings = p.get("poligonos") or []
        if not (base.startswith("GENE-VFM") or base.startswith("GENE-HFM")):
            continue
        if not rings:
            continue
        try:
            outer = Polygon(rings[0])
            holes = [Polygon(r) for r in rings[1:] if r and len(r) >= 3]
        except Exception:
            continue
        hosts.append(holes)
        hole_areas.extend(float(h.area) / (25.4 * 25.4) for h in holes)
        minx, miny, maxx, maxy = outer.bounds
        from shapely.geometry import box

        free = box(minx, miny, maxx, maxy).difference(outer)
        geoms = [free] if free.geom_type == "Polygon" else list(getattr(free, "geoms", []))
        open_cavs.append([g for g in geoms if g.area / (25.4 * 25.4) >= 5.0])

    inside_closed = inside_open = outside = 0
    for p in piezas:
        base = str(p.get("nombre") or "").split("#")[0]
        if base.startswith("GENE-VFM") or base.startswith("GENE-HFM"):
            continue
        rings = p.get("poligonos") or []
        if not rings:
            continue
        try:
            c = Polygon(rings[0]).centroid
        except Exception:
            continue
        if any(h.contains(c) for holes in hosts for h in holes):
            inside_closed += 1
        elif any(g.contains(c) for cavs in open_cavs for g in cavs):
            inside_open += 1
        else:
            outside += 1
    return {
        "hosts": len(hosts),
        "n_orificios": sum(len(h) for h in hosts),
        "in_holes": inside_closed,
        "in_open_cav": inside_open,
        "out": outside,
        "hole_area_in2_max": max(hole_areas) if hole_areas else 0.0,
        "hole_area_in2_min": min(hole_areas) if hole_areas else 0.0,
    }


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.isdir(DXF_DIR):
        print("ERROR: sin acceso a DXF UNC")
        return 1

    print("=" * 72)
    print("ARGA BASE — PLC152-P4 hole fill check")
    print("=" * 72)

    entries: list[SimPieceEntry] = []
    for item, qty in TABLA:
        ruta = _find_dxf(item)
        if not ruta:
            print(f"  FALTA {item}")
            return 1
        entries.append(SimPieceEntry(ruta=ruta, qty=qty, nombre=item))
        print(f"  OK {item} x{qty}")

    piezas, errs = build_pieces_from_entries(entries)
    for e in errs:
        print("  parse:", e)
    # Contar interiors en pool
    ring_hosts = 0
    for p in piezas:
        n = str(p.get("nombre") or "")
        if "VFM" in n or "HFM" in n:
            rings = []
            try:
                from modules.nesting_engine.algorithm_bridge import _rings_from_shapely_polygon

                rings = _rings_from_shapely_polygon(p.get("poly"))
            except Exception:
                pass
            if len(rings) >= 2:
                ring_hosts += 1
                print(f"  pool {n}: rings={len(rings)} holes={len(rings)-1}")
    print(f"Pool={len(piezas)} hosts_con_orificio={ring_hosts}")

    t0 = time.perf_counter()
    tl = run_plate_sim(
        piezas,
        w_mm=inches_to_mm(PLATE_W_IN),
        h_mm=inches_to_mm(PLATE_H_IN),
        kerf_in=KERF_IN,
        margin_in=MARGIN_IN,
        mc_iterations=1,
        engine_id="arga_base",
        isolate_process=False,
    )
    elapsed = time.perf_counter() - t0
    if tl.error and not tl.hoja:
        print("ERROR:", tl.error)
        return 2

    hoja = tl.hoja or {}
    n_col = len(hoja.get("piezas") or [])
    area = float(hoja.get("area_usada") or 0)
    efi = area / (tl.w_mm * tl.h_mm) * 100.0
    stats = _hole_stats(hoja)
    names = Counter(str(p.get("nombre") or "").split("#")[0] for p in hoja.get("piezas") or [])

    print("-" * 72)
    print(f"ok={tl.ok}  tiempo={elapsed:.1f}s  efi={efi:.1f}%")
    print(f"colocadas={n_col} restos={len(tl.restos or [])}")
    print(
        f"hosts={stats['hosts']} orificios={stats['n_orificios']} "
        f"in_holes={stats['in_holes']} in_open_cav={stats['in_open_cav']} out={stats['out']}"
    )
    print(
        f"orificio area in2 min/max="
        f"{stats['hole_area_in2_min']:.1f}/{stats['hole_area_in2_max']:.1f}"
    )
    for k, v in sorted(names.items()):
        print(f"  {k}: {v}")

    filled = stats["in_holes"] + stats["in_open_cav"]
    slim = {
        "efi": efi,
        "colocadas": n_col,
        "restos": len(tl.restos or []),
        "elapsed_s": elapsed,
        "hole_stats": stats,
        "conteo": dict(names),
        "baseline_efi": 52.5,
        "pass": filled > 0 and efi >= 52.5,
    }
    out = os.path.join(OUT_DIR, "resultado.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=2)
    print(f"Wrote {out}")

    if filled < 1:
        print("FAIL: 0 piezas en orificios/cavidades abiertas")
        return 3
    print(f"PASS: {filled} piezas en cavidades (closed={stats['in_holes']} open={stats['in_open_cav']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
