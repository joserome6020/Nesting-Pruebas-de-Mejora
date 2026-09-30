"""Candado 2026-09-30c: "Inventario incompleto: faltan 2" sin decir cuáles.

Job 62223 Cal 0.375: 62223-1248-P07 y P09 miden 95.558" de ancho y la única
placa es 240"×96". Con margen placa→pieza 0.260" (ni con 0.250") caben; el
aviso no nombraba las piezas ni el motivo.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from shapely.geometry import box  # noqa: E402

from modules.nesting_engine.manager import _piezas_sin_placa  # noqa: E402
from modules.nesting_engine.sheet_integrity import validar_colocacion_completa  # noqa: E402

PLC082 = {"id": "PLC082", "w": 6096.0, "h": 2438.4}


def main() -> int:
    fallos: list[str] = []
    p07 = {"nombre": "62223-1248-P07", "poly": box(0, 0, 2427.173, 3467.559)}
    p09 = {"nombre": "62223-1248-P09", "poly": box(0, 0, 3467.559, 2427.173)}
    p14 = {"nombre": "62223-1248-P14", "poly": box(0, 0, 3422.650, 979.373)}

    sin = _piezas_sin_placa([p07, p09, p14], [PLC082], 0.260)
    if len(sin) != 2 or not all("95.558" in s for s in sin):
        fallos.append(f"P07/P09 deben reportarse sin placa: {sin}")
    if _piezas_sin_placa([p07], [PLC082], 0.220):
        fallos.append("con 0.220\" de margen P07 sí cabe en 96\"")

    hoja = {"piezas": [{"nombre": "62223-1248-P14"}]}
    ok, msg = validar_colocacion_completa([p07, p09, p14], [hoja])
    if ok or "62223-1248-P07" not in msg or "62223-1248-P09" not in msg:
        fallos.append(f"el aviso debe nombrar las piezas que faltan: {msg!r}")

    src = (RAIZ / "modules/nesting_engine/manager.py").read_text(encoding="utf-8")
    if "No caben en ninguna placa disponible" not in src:
        fallos.append("manager no agrega el motivo 'no caben en ninguna placa'")

    if fallos:
        print("FAIL:\n  " + "\n  ".join(fallos))
        return 1
    print("OK nest incompleto nombra piezas y motivo sin placa")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
