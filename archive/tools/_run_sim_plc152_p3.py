"""Sim LAB — exactamente PLC152-P3 (captura usuario, 75 PZAS)."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

try:
    from modules.win_dll_bootstrap import bootstrap_proceso_nesting

    bootstrap_proceso_nesting()
except Exception:
    pass

os.environ["ARGA_NEST_LAB"] = "1"

from shapely.geometry import Polygon  # noqa: E402

from modules.nesting_engine.sim_lab import (  # noqa: E402
    SimPieceEntry,
    build_pieces_from_entries,
    inches_to_mm,
    run_timeline_sim,
    save_scenario_json,
    scenario_to_dict,
)

DXF_DIR = (
    r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals"
    r"\ARGA METALS CORPORATE SYSTEM\TANKS\GIGA\GIGA FLUIDSTACK"
    r"\MODEL CORE FILES\AutoDXF\Processed Files"
)
OUT_DIR = os.path.join(_ROOT, "_logs", "sim_plc152_p3")
PLATE_W_IN = 120.0
PLATE_H_IN = 48.0

# BOM de la placa seleccionada (tabla UI) — total objetivo 75
TABLA = [
    ("GENE-VFM-20-101", 3),
    ("GENE-HFM-10-102", 4),
    ("GENE-BKT-295", 1),   # UI a veces muestra BKT-205 (posible OCR); en DXF es 295
    ("GENE-BKT-304", 10),
    ("GENE-BKT-321", 12),
    ("GENE-BKT-320", 11),
    ("GENE-BKT-271", 5),
    ("GENE-GS-0820-708", 15),
    ("GENE-BKT-CT-103", 1),
    ("GENE-BKT-369", 13),  # 75 - 62 = 13 (cant. parcialmente oculta en captura)
]

ALIASES = {
    "GENE-BKT-295": ["GENE-BKT-205", "GENE-BKT-295"],
}


def _find_dxf(item: str) -> str | None:
    names = ALIASES.get(item, [item])
    files = os.listdir(DXF_DIR)
    for cand in names:
        cu = cand.upper()
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
    for p in piezas:
        base = str(p.get("nombre") or "").split("#")[0]
        rings = p.get("poligonos") or []
        if len(rings) < 2:
            continue
        if not (base.startswith("GENE-VFM") or base.startswith("GENE-HFM")):
            continue
        try:
            holes = [Polygon(r) for r in rings[1:] if r and len(r) >= 3]
        except Exception:
            continue
        hosts.append(holes)
    inside = outside = 0
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
        hit = any(h.contains(c) for holes in hosts for h in holes)
        if hit:
            inside += 1
        else:
            outside += 1
    return {"hosts": len(hosts), "in_holes": inside, "out": outside}


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.isdir(DXF_DIR):
        print("ERROR: sin acceso a DXF")
        return 1

    print("=" * 72)
    print("LAB SIM — PLC152-P3 (75 PZAS, 120x48)")
    print("=" * 72)

    entries: list[SimPieceEntry] = []
    missing = []
    total = 0
    for item, qty in TABLA:
        ruta = _find_dxf(item)
        if not ruta:
            missing.append(item)
            print(f"  FALTA {item} x{qty}")
            continue
        entries.append(SimPieceEntry(ruta=ruta, qty=qty, nombre=item))
        total += qty
        print(f"  OK {item} x{qty} -> {os.path.basename(ruta)}")

    if missing:
        print("Faltan:", ", ".join(missing))
        return 1

    print(f"Total BOM: {total}")
    piezas, errs = build_pieces_from_entries(entries)
    for e in errs:
        print("  parse:", e)
    print(f"Pool: {len(piezas)}")

    scenario = os.path.join(OUT_DIR, "escenario.nestsim.json")
    save_scenario_json(
        scenario,
        scenario_to_dict(
            plate_w_in=PLATE_W_IN,
            plate_h_in=PLATE_H_IN,
            kerf_in=0.25,
            margin_in=0.15,
            corner="INFERIOR IZQUIERDA",
            opt="OPTIMIZAR LARGO Y ANCHO",
            nest_mode="standard",
            mc_iterations=1,
            entries=entries,
            notes="PLC152-P3 captura UI 75 PZAS",
        ),
    )

    tl = run_timeline_sim(
        piezas,
        w_mm=inches_to_mm(PLATE_W_IN),
        h_mm=inches_to_mm(PLATE_H_IN),
        kerf_in=0.25,
        margin_in=0.15,
        mc_iterations=1,
    )
    if tl.error and not tl.hoja:
        print("ERROR:", tl.error)
        return 2

    n_col = len((tl.hoja or {}).get("piezas") or [])
    n_rest = len(tl.restos or [])
    area = float((tl.hoja or {}).get("area_usada") or 0)
    efi = area / (tl.w_mm * tl.h_mm) * 100.0
    stats = _hole_stats(tl.hoja or {})
    names = Counter(str(p.get("nombre") or "").split("#")[0] for p in (tl.hoja or {}).get("piezas") or [])

    print("-" * 72)
    print(f"ok={tl.ok}  tiempo={tl.elapsed_ms/1000:.1f}s")
    print(f"colocadas={n_col}/{len(piezas)}  restos={n_rest}  efi~{efi:.1f}%  (UI ref 57.0%)")
    print(f"cavidades hosts={stats['hosts']} in_holes={stats['in_holes']} out={stats['out']}")
    for k, v in sorted(names.items()):
        print(f"  {k}: {v}")

    slim = {
        "ok": tl.ok,
        "efi": efi,
        "colocadas": n_col,
        "restos": n_rest,
        "elapsed_ms": tl.elapsed_ms,
        "hole_stats": stats,
        "conteo": dict(names),
        "escenario": scenario,
        "ui_ref_efi": 57.0,
    }
    with open(os.path.join(OUT_DIR, "resultado_lab.json"), "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=2)

    print(f"\nEscenario: {scenario}")
    print("Abrir reproductor:")
    print(f'  $env:ARGA_NEST_LAB="1"; python tools\\nest_sim_lab.py "{scenario}"')
    return 0 if tl.ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
