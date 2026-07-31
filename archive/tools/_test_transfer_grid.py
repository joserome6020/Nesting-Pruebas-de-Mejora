"""Prueba rápida: transferencia incremental con barrido en grilla."""
import copy
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shapely.geometry import box

from modules.nesting_engine.geometry_parser import poligonos_desde_shapely
from modules.nesting_engine.manager import MotorNesting


def _pieza_rect(nombre, x, y, w, h):
    poly = box(x, y, x + w, y + h)
    return {
        "nombre": nombre,
        "poligonos": poligonos_desde_shapely(poly),
        "marcas": [],
        "area": float(poly.area),
    }


def main():
    motor = MotorNesting()
    w_mm = 120 * 25.4
    h_mm = 48 * 25.4
    margin = 0.15 * 25.4

    piezas = []
    # Solo bloque izquierdo: franja libre aislada a la derecha (sin ancla adyacente).
    for i in range(8):
        piezas.append(_pieza_rect(f"LONG-{i}", margin, margin + i * 75, 700, 65))
    # Cluster central deja >600 mm libres a la derecha sin piezas contiguas.
    for j in range(8):
        piezas.append(_pieza_rect(f"CLU-{j}", 1200 + (j % 2) * 80, margin + (j // 2) * 60, 55, 45))

    hoja_dest = {
        "placa_id": "PLC152-TEST",
        "placa_w": w_mm,
        "placa_h": h_mm,
        "kerf_usado": 0.3,
        "margin_usado": 0.15,
        "opt_usado": "OPTIMIZAR LARGO Y ANCHO",
        "corner_usado": "INFERIOR IZQUIERDA",
        "piezas": piezas,
    }

    pieza_mover = _pieza_rect("GENE-SIVC-40-40-144", 0, 0, 840.409, 116.548)

    t0 = time.perf_counter()
    result = motor._intentar_colocacion_incremental(hoja_dest, pieza_mover)
    elapsed = time.perf_counter() - t0
    if result is None:
        print("FAIL: no se encontró posición")
        return 1

    nuevas = result.get("piezas") or []
    print(f"OK: {len(nuevas)} piezas en destino, eficiencia={result.get('eficiencia', 0):.1f}% ({elapsed:.2f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
