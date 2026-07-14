"""Prueba 1 — tabla GENE en placa 48x120."""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "interface"))

try:
    from modules.win_dll_bootstrap import bootstrap_proceso_nesting

    bootstrap_proceso_nesting()
except Exception:
    pass

os.environ["ARGA_NEST_MODE"] = "max"

from modules.nesting_engine.sim_lab import (  # noqa: E402
    SimPieceEntry,
    build_pieces_from_entries,
    inches_to_mm,
    run_single_sheet_sim,
    save_scenario_json,
    scenario_to_dict,
)

DXF_DIR = r"C:\Users\PC GAMING\Pictures\ANALISIS NESTING\DXFS"
OUT_DIR = os.path.join(_ROOT, "_logs", "sim_gene_prueba1")
PLATE_W_IN = 120.0  # horizontal
PLATE_H_IN = 48.0   # vertical

# ITEM -> Cant. según tabla del usuario
TABLA = [
    ("GENE-VFM-20-101", 3),
    ("GENE-HFM-10-102", 4),
    ("GENE-BKT-295", 1),
    ("GENE-BKT-299", 2),
    ("GENE-BKT-297", 6),
    ("GENE-BKT-294", 12),
    ("GENE-BKT-306", 17),
    ("GENE-BKT-304", 1),
    ("GENE-BKT-270", 3),
    ("GENE-GS-0820-708", 4),
    ("GENE-BKT-CT-103", 5),
    ("GENE-BKT-369", 3),
]


def _find_dxf(item: str) -> str | None:
    item_u = item.upper()
    for name in os.listdir(DXF_DIR):
        if not name.lower().endswith(".dxf"):
            continue
        base = name.split(",")[0].strip().upper()
        if base == item_u or name.upper().startswith(item_u):
            return os.path.join(DXF_DIR, name)
    return None


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    entries: list[SimPieceEntry] = []
    missing: list[str] = []

    print("=" * 72)
    print("SIM PRUEBA 1 — placa 48\" x 120\" | modo max")
    print(f"DXF: {DXF_DIR}")
    print("=" * 72)

    for item, qty in TABLA:
        ruta = _find_dxf(item)
        if not ruta:
            missing.append(item)
            print(f"  FALTA DXF: {item} x{qty}")
            continue
        entries.append(SimPieceEntry(ruta=ruta, qty=qty, nombre=item))
        print(f"  OK {item} x{qty} -> {os.path.basename(ruta)}")

    if missing:
        print("\nERROR: faltan DXF:", ", ".join(missing))
        sys.exit(1)

    total_qty = sum(q for _, q in TABLA)
    print(f"\nTotal piezas (tabla): {total_qty}")

    piezas, errores = build_pieces_from_entries(entries)
    if errores:
        print("\nErrores parseo DXF:")
        for e in errores:
            print(f"  - {e}")
    print(f"Piezas en pool motor: {len(piezas)}")

    # Verificar bbox vs tabla (informativo)
    print("\n--- Bbox DXF (in) vs tabla ---")
    from modules.nesting_engine.sim_lab import IN_TO_MM

    tabla_dims = {
        "GENE-VFM-20-101": (78.35, 12.24),
        "GENE-HFM-10-102": (34.65, 6.29),
        "GENE-BKT-295": (20.97, 4.71),
        "GENE-BKT-299": (6.00, 5.03),
        "GENE-BKT-297": (6.00, 4.50),
        "GENE-BKT-294": (5.18, 4.71),
        "GENE-BKT-306": (7.08, 4.20),
        "GENE-BKT-304": (7.08, 4.20),
        "GENE-BKT-270": (6.79, 3.25),
        "GENE-GS-0820-708": (3.84, 3.61),
        "GENE-BKT-CT-103": (4.26, 2.50),
        "GENE-BKT-369": (3.03, 2.57),
    }
    seen = set()
    for p in piezas:
        base = str(p.get("nombre", "")).split("#")[0]
        if base in seen:
            continue
        seen.add(base)
        poly = p.get("poly")
        if poly is None:
            continue
        minx, miny, maxx, maxy = poly.bounds
        lw = (maxx - minx) / IN_TO_MM
        wh = (maxy - miny) / IN_TO_MM
        tl, tw = tabla_dims.get(base, (0, 0))
        flag = ""
        if tl and tw:
            max_d = max(lw, wh)
            min_d = min(lw, wh)
            max_t = max(tl, tw)
            min_t = min(tl, tw)
            if abs(max_d - max_t) > 0.5 or abs(min_d - min_t) > 0.5:
                flag = " <<< difiere tabla"
        print(f"  {base}: DXF {lw:.2f}x{wh:.2f}\" | tabla {tl}x{tw}\"{flag}")

    result = run_single_sheet_sim(
        piezas,
        w_mm=inches_to_mm(PLATE_W_IN),
        h_mm=inches_to_mm(PLATE_H_IN),
        kerf_in=0.2,
        margin_in=0.15,
        nest_mode="max",
        mc_iterations=30,
    )

    print("\n" + "=" * 72)
    print(result.summary_text())
    print("=" * 72)

    log_path = os.path.join(OUT_DIR, "resultado.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(result.summary_text())
        f.write(f"\n\nok={result.ok} restos={len(result.restos)} elapsed_ms={result.elapsed_ms:.0f}\n")

    scenario_path = os.path.join(OUT_DIR, "escenario.nestsim.json")
    save_scenario_json(
        scenario_path,
        scenario_to_dict(
            plate_w_in=PLATE_W_IN,
            plate_h_in=PLATE_H_IN,
            kerf_in=0.2,
            margin_in=0.15,
            corner="INFERIOR IZQUIERDA",
            opt="OPTIMIZAR LARGO Y ANCHO",
            nest_mode="max",
            mc_iterations=30,
            entries=entries,
            notes="Prueba 1 tabla GENE",
        ),
    )

    if result.hoja:
        with open(os.path.join(OUT_DIR, "hoja_resultado.json"), "w", encoding="utf-8") as f:
            # Solo metadatos + nombres (poligonos muy grandes)
            slim = {
                "placa_w_mm": result.hoja.get("placa_w"),
                "placa_h_mm": result.hoja.get("placa_h"),
                "eficiencia": result.hoja.get("eficiencia_directa") or result.hoja.get("eficiencia"),
                "area_usada": result.hoja.get("area_usada"),
                "piezas": [p.get("nombre") for p in result.hoja.get("piezas") or []],
                "restos": [p.get("nombre") for p in result.restos],
            }
            json.dump(slim, f, ensure_ascii=False, indent=2)

    if not result.ok:
        print(f"\n*** INCOMPLETO: {len(result.restos)} pieza(s) sin colocar ***")
        sys.exit(2)
    print(f"\nOK — todas las piezas colocadas. Log: {log_path}")


if __name__ == "__main__":
    main()
