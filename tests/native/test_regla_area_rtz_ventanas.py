"""Candado 2026-09-30e: ventanas y tope 456.954 in² (SP-792_1).

Orificio interior ("ventana") solo admite piezas si su área >= tope. Los
menores son metal para el motor, pero el barreno sigue en la geometría de
salida (visor/export).
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from shapely.geometry import Polygon, box  # noqa: E402

from modules.nesting_engine import regla_area_rtz as r  # noqa: E402

IN = 25.4
IN2 = IN * IN
TOPE = 456.954


def _check_umbral(fallos: list[str]) -> None:
    if not r.ventana_admite_piezas(TOPE * IN2):
        fallos.append("ventana igual al tope debe admitir piezas")
    if r.ventana_admite_piezas(456.8 * IN2):
        fallos.append("ventana menor al tope admitió piezas")


def _host():
    chico = box(5 * IN, 5 * IN, 15 * IN, 15 * IN)      # 100 in²
    grande = box(25 * IN, 5 * IN, 50 * IN, 30 * IN)    # 625 in²
    return Polygon(box(0, 0, 60 * IN, 40 * IN).exterior, [chico.exterior, grande.exterior])


def _check_cavidades(fallos: list[str]) -> None:
    from modules.nesting_engine.venom_hole_fill import (
        list_closed_interior_cavities,
        list_host_cavities,
    )

    for fn, cavs in (
        ("closed", list_closed_interior_cavities(_host())),
        ("host", list_host_cavities(_host(), open_profile=False)),
    ):
        areas = sorted(round(c.area / IN2) for c in cavs)
        if 100 in areas or 625 not in areas:
            fallos.append(f"venom {fn}: cavidades {areas} (solo debe quedar la de 625 in²)")


def _check_motor_cpp(fallos: list[str]) -> None:
    from modules.nesting_engine import algorithm_bridge as ab

    host = _host()
    guest = box(0, 0, 8 * IN, 8 * IN)
    piezas = [{"nombre": "HOST", "poly": host, "area": host.area, "calibre": "0.25", "material": "A 36"}]
    piezas += [
        {"nombre": f"G{i}", "poly": guest, "area": guest.area, "calibre": "0.25", "material": "A 36"}
        for i in range(4)
    ]
    hoja, restos = ab.empaquetar_una_hoja_arga_base(
        piezas, 96 * IN, 48 * IN, kerf_override=0.375, margin_override=0.26
    )
    colocadas = {p["nombre"]: p for p in hoja.get("piezas") or []}
    h = colocadas.get("HOST")
    if h is None or restos:
        fallos.append(f"motor: no colocó todo (restos={len(restos)})")
        return
    rings = h["poligonos"]
    if len(rings) != 3:
        fallos.append(f"motor: el host salió con {len(rings)} anillos (se esperaban 3: barreno restaurado)")
        return
    hp = Polygon(rings[0], rings[1:])
    areas = sorted(round(Polygon(x).area / IN2) for x in rings[1:])
    if areas != [100, 625]:
        fallos.append(f"motor: orificios del host {areas} (esperado [100, 625])")
    ext = Polygon(rings[0])
    for x in rings[1:]:
        if not ext.contains(Polygon(x)):
            fallos.append("motor: barreno restaurado fuera del host")
    for nom, g in colocadas.items():
        if not nom.startswith("G"):
            continue
        c = Polygon(g["poligonos"][0]).centroid
        for x in hp.interiors:
            if Polygon(x).contains(c) and Polygon(x).area < TOPE * IN2:
                fallos.append(f"motor: {nom} entró a la ventana de {Polygon(x).area / IN2:.0f} in²")


def main() -> int:
    fallos: list[str] = []
    _check_umbral(fallos)
    _check_cavidades(fallos)
    _check_motor_cpp(fallos)
    if fallos:
        print("FAIL:\n  " + "\n  ".join(fallos))
        return 1
    print("OK regla área RTZ + ventanas >= 456.954 in²")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
