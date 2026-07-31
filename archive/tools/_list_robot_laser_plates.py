"""Lista placas Herinox candidatas a ROBOT LASER + MINI NEST (misma regla que exporter)."""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "interface"))

from modules.nesting_engine.exporter import RUTA_ROBOT_LASER, _resolver_carpeta_principal
from interface.utils_nesting import _espesor_pulgadas_desde_texto

COLS = [
    "Thickness",
    "Material",
    "Arga Code",
    "Length",
    "Width",
    "LB",
    "MXN",
    "$$/LB",
    "Stock",
]


def row_dict(row: list) -> dict:
    return {c: row[i] if i < len(row) else "" for i, c in enumerate(COLS)}


def clasificar(row: dict, origen: str) -> dict | None:
    thk_txt = str(row.get("Thickness") or "").strip()
    mat = str(row.get("Material") or "").strip()
    cod = str(row.get("Arga Code") or "").strip()
    stock = str(row.get("Stock") or "").strip().upper()
    try:
        len_in = float(row.get("Length") or 0)
        wid_in = float(row.get("Width") or 0)
    except Exception:
        return None
    if len_in <= 0 or wid_in <= 0:
        return None

    clave = f"{thk_txt}_{mat}"
    hoja = {
        "placa_w": min(len_in, wid_in) * 25.4,
        "placa_h": max(len_in, wid_in) * 25.4,
        "modo_largos_cu": False,
    }
    dest = _resolver_carpeta_principal(clave, hoja)
    w_in = min(len_in, wid_in)
    l_in = max(len_in, wid_in)
    thk_val = _espesor_pulgadas_desde_texto(thk_txt)

    return {
        "codigo": cod,
        "espesor": thk_txt,
        "espesor_in": thk_val,
        "material": mat,
        "largo_in": l_in,
        "ancho_in": w_in,
        "dims": f"{l_in:.0f}x{w_in:.0f}",
        "stock": stock,
        "origen": origen,
        "destino": dest,
        "mxn": float(row.get("MXN") or 0),
        "lb": float(row.get("LB") or 0),
    }


def main() -> None:
    cache = os.path.join(ROOT, "cache", "herinox_plates_snapshot.json")
    if not os.path.isfile(cache):
        print(f"No hay snapshot en {cache}. Sincroniza Herinox primero.")
        return

    with open(cache, encoding="utf-8") as f:
        snap = json.load(f)

    all_rows: list[dict] = []
    for r in snap.get("empresa") or []:
        x = clasificar(row_dict(r), "EMPRESA")
        if x:
            all_rows.append(x)
    for r in snap.get("proveedor") or []:
        x = clasificar(row_dict(r), "PROVEEDOR")
        if x:
            all_rows.append(x)

    robot = [x for x in all_rows if x["destino"] == RUTA_ROBOT_LASER]
    disp = [x for x in robot if x["stock"] == "DISPONIBLE"]

    from collections import defaultdict

    by_dims: dict[str, list[dict]] = defaultdict(list)
    for x in disp:
        by_dims[x["dims"]].append(x)

    print(f"Snapshot: {snap.get('updated_at')}")
    print("ROBOT LASER — formatos L x W (in) | DISPONIBLE")
    print(f"Total: {len(disp)} placas en {len(by_dims)} formatos\n")
    print(f"{'L x W':<12} {'#':>3}  {'Espesores':<36} Codigos")
    print("-" * 90)
    for dims in sorted(by_dims.keys(), key=lambda d: (int(d.split("x")[0]), int(d.split("x")[1]))):
        items = by_dims[dims]
        thks = sorted(
            {i["espesor"] for i in items},
            key=lambda t: _espesor_pulgadas_desde_texto(t) or 0,
        )
        cods = ", ".join(i["codigo"] for i in sorted(items, key=lambda i: i["espesor_in"] or 0))
        print(f"{dims:<12} {len(items):>3}  {', '.join(thks):<36} {cods}")

    print("\n--- Proporcion ancho / largo (W/L) ---")
    for dims in sorted(by_dims.keys(), key=lambda d: (int(d.split("x")[0]), int(d.split("x")[1]))):
        largo, ancho = (float(x) for x in dims.split("x"))
        ratio = ancho / largo if largo else 0
        print(f"  {dims}: ancho = {100 * ratio:.1f}% del largo (ratio {ratio:.3f})")

    by_all: dict[str, dict[str, int]] = defaultdict(lambda: {"disp": 0, "nodisp": 0})
    for x in robot:
        if x["stock"] == "DISPONIBLE":
            by_all[x["dims"]]["disp"] += 1
        else:
            by_all[x["dims"]]["nodisp"] += 1

    print("\n--- Todos los formatos Robot Laser (disp + no disp) ---")
    for dims in sorted(by_all.keys(), key=lambda d: (int(d.split("x")[0]), int(d.split("x")[1]))):
        d = by_all[dims]
        print(f"  {dims}: {d['disp']} disponible, {d['nodisp']} no disponible")


if __name__ == "__main__":
    main()
