"""Sim LAB — placa dolorosa VFM+BKT (referencia GIGA / PLC152-like)."""
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
os.environ["ARGA_NEST_MODE"] = "standard"

from shapely.geometry import Polygon  # noqa: E402

from modules.nesting_engine.sim_lab import (  # noqa: E402
    IN_TO_MM,
    SimPieceEntry,
    build_pieces_from_entries,
    inches_to_mm,
    run_timeline_sim,
    save_scenario_json,
    scenario_to_dict,
)

DXF_DIRS = [
    r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals\ARGA METALS CORPORATE SYSTEM\TANKS\GIGA\GIGA FLUIDSTACK\MODEL CORE FILES\AutoDXF\Processed Files",
    r"C:\Users\jose_rosales\Pictures\ANALISIS NESTING\DXFS",
]
OUT_DIR = os.path.join(_ROOT, "_logs", "sim_vfm_dolor")
PLATE_W_IN = 120.0
PLATE_H_IN = 48.0

# Mismo set que la placa de referencia deseable (aprox. cantidades de P2/P3)
TABLA = [
    ("GENE-VFM-20-101", 3),
    ("GENE-HFM-10-102", 4),
    ("GENE-BKT-295", 1),
    ("GENE-BKT-299", 2),
    ("GENE-BKT-297", 6),
    ("GENE-BKT-294", 12),
    ("GENE-BKT-306", 18),
    ("GENE-BKT-304", 8),
    ("GENE-BKT-270", 4),
    ("GENE-BKT-271", 1),
    ("GENE-BKT-CT-103", 5),
]


def _dxf_dir() -> str | None:
    for d in DXF_DIRS:
        if os.path.isdir(d):
            return d
    return None


def _find_dxf(dxf_dir: str, item: str) -> str | None:
    item_u = item.upper()
    for name in os.listdir(dxf_dir):
        if not name.lower().endswith(".dxf"):
            continue
        base = name.split(",")[0].strip().upper()
        if base == item_u or name.upper().startswith(item_u + ",") or name.upper().startswith(item_u + " "):
            return os.path.join(dxf_dir, name)
    return None


def _hole_fill_stats(hoja: dict) -> dict:
    """Cuenta BKTs cuyo centro cae en cavidades de VFM/HFM."""
    piezas = list(hoja.get("piezas") or [])
    hosts = []
    for p in piezas:
        nom = str(p.get("nombre") or "")
        rings = p.get("poligonos") or []
        if len(rings) < 2:
            continue
        if not (nom.startswith("GENE-VFM") or nom.startswith("GENE-HFM") or "VFM" in nom or "HFM" in nom):
            # nombre puede traer #n
            base = nom.split("#")[0]
            if not (base.startswith("GENE-VFM") or base.startswith("GENE-HFM")):
                continue
        try:
            holes = [Polygon(r) for r in rings[1:] if r and len(r) >= 3]
        except Exception:
            continue
        hosts.append((nom, holes))

    inside = 0
    outside_small = 0
    for p in piezas:
        nom = str(p.get("nombre") or "").split("#")[0]
        if nom.startswith("GENE-VFM") or nom.startswith("GENE-HFM"):
            continue
        rings = p.get("poligonos") or []
        if not rings:
            continue
        try:
            c = Polygon(rings[0]).centroid
        except Exception:
            continue
        hit = False
        for _, holes in hosts:
            for h in holes:
                if h.contains(c) or h.intersects(Polygon(rings[0]).buffer(0)):
                    # contain centroid is enough
                    if h.contains(c):
                        hit = True
                        break
            if hit:
                break
        if hit:
            inside += 1
        else:
            outside_small += 1
    return {"hosts": len(hosts), "small_in_holes": inside, "small_outside": outside_small}


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    dxf_dir = _dxf_dir()
    if not dxf_dir:
        print("ERROR: no hay carpeta DXF accesible")
        return 1

    print("=" * 72)
    print("LAB SIM — VFM + BKT (placa dolorosa)")
    print(f"DXF: {dxf_dir}")
    print(f"Placa: {PLATE_W_IN}\" x {PLATE_H_IN}\" | ARGA_NEST_LAB=1")
    print("=" * 72)

    entries: list[SimPieceEntry] = []
    missing: list[str] = []
    for item, qty in TABLA:
        ruta = _find_dxf(dxf_dir, item)
        if not ruta:
            # fallback VFM-20-102 if 101 missing
            if item == "GENE-VFM-20-101":
                ruta = _find_dxf(dxf_dir, "GENE-VFM-20-102")
                if ruta:
                    entries.append(SimPieceEntry(ruta=ruta, qty=qty, nombre="GENE-VFM-20-101"))
                    print(f"  OK {item} x{qty} (via VFM-20-102) -> {os.path.basename(ruta)}")
                    continue
            missing.append(item)
            print(f"  FALTA: {item} x{qty}")
            continue
        entries.append(SimPieceEntry(ruta=ruta, qty=qty, nombre=item))
        print(f"  OK {item} x{qty} -> {os.path.basename(ruta)}")

    if missing:
        print("Faltan DXF:", ", ".join(missing))
        return 1

    piezas, errores = build_pieces_from_entries(entries)
    for e in errores:
        print("  parse:", e)
    print(f"Pool motor: {len(piezas)}")

    scenario_path = os.path.join(OUT_DIR, "escenario.nestsim.json")
    save_scenario_json(
        scenario_path,
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
            notes="Placa dolorosa VFM+BKT — LAB SIMULATOR",
        ),
    )
    print(f"Escenario: {scenario_path}")

    tl = run_timeline_sim(
        piezas,
        w_mm=inches_to_mm(PLATE_W_IN),
        h_mm=inches_to_mm(PLATE_H_IN),
        kerf_in=0.25,
        margin_in=0.15,
        mc_iterations=1,
    )

    print("-" * 72)
    if not tl.hoja and tl.error:
        print("ERROR:", tl.error)
        return 2

    n_col = len((tl.hoja or {}).get("piezas") or [])
    n_rest = len(tl.restos or [])
    area = float((tl.hoja or {}).get("area_usada") or 0)
    denom = float(tl.w_mm) * float(tl.h_mm)
    efi = (area / denom * 100.0) if denom else 0.0
    stats = _hole_fill_stats(tl.hoja or {})
    names = Counter(str(p.get("nombre") or "").split("#")[0] for p in (tl.hoja or {}).get("piezas") or [])

    print(f"ok={tl.ok}  tiempo={tl.elapsed_ms/1000:.1f}s  motor_lab=1")
    print(f"colocadas={n_col}  restos={n_rest}  efi~{efi:.1f}%")
    print(
        f"cavidades: hosts={stats['hosts']}  "
        f"pequenas_en_hueco={stats['small_in_holes']}  "
        f"pequenas_fuera={stats['small_outside']}"
    )
    print("conteo en hoja:")
    for k, v in sorted(names.items()):
        print(f"  {k}: {v}")
    if tl.restos:
        print("restos:", [str(p.get("nombre")) for p in tl.restos[:20]])

    # primeros pasos con estrategia si existe
    print("\nPrimeros 12 pasos timeline:")
    for i, paso in enumerate((tl.pasos or [])[:12], start=1):
        est = paso.get("estrategia") or paso.get("categoria") or ""
        estado = "OK" if paso.get("colocada") else "NO"
        print(f"  {i:02d}. [{estado}] {paso.get('nombre')}  {est}")

    slim = {
        "ok": tl.ok,
        "elapsed_ms": tl.elapsed_ms,
        "efi": efi,
        "colocadas": n_col,
        "restos": n_rest,
        "hole_stats": stats,
        "conteo": dict(names),
        "escenario": scenario_path,
    }
    with open(os.path.join(OUT_DIR, "resultado_lab.json"), "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=2)
    print(f"\nResultado: {os.path.join(OUT_DIR, 'resultado_lab.json')}")
    print("Reproductor:")
    print(f'  $env:ARGA_NEST_LAB="1"; python tools\\nest_sim_lab.py "{scenario_path}"')
    return 0 if tl.ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
