"""Candado: BLOQUEAR ORIENTACIÓN DE CORTE fija la vista PARTS; el pack no gira más."""
from __future__ import annotations

import sys
from pathlib import Path

from shapely.geometry import box
from shapely import affinity

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))

from interface.utils_nesting import clave_orientacion_cobre_ruta
from modules.nesting_engine.algorithm_bridge import (
    _orientation_lock_violated,
    _piece_to_native,
    reject_locked_orientation_violations,
)
from modules.nesting_engine.manager import MotorNesting


def _rect(w: float, h: float):
    return box(0.0, 0.0, float(w), float(h))


def test_piece_to_native_propaga_grain_locked_y_solo_0():
    poly = _rect(200.0, 50.0)
    piece = {
        "nombre": "RECT-90",
        "poly": poly,
        "area": poly.area,
        "calibre": "0.250",
        "material": "A 36",
        "grain_locked": True,
        "allowed_rotations": [0],
    }
    native = _piece_to_native(piece)
    assert native["grain_locked"] is True
    assert list(native["allowed_rotations"]) == [0]
    rings = native["rings"]
    assert rings and len(rings[0]) >= 4
    xs = [p[0] for p in rings[0]]
    ys = [p[1] for p in rings[0]]
    # Tras bake de 90° el AABB de pack a 0° debe ser el de la vista (alto×ancho).
    assert abs((max(xs) - min(xs)) - 200.0) < 1e-6
    assert abs((max(ys) - min(ys)) - 50.0) < 1e-6


def test_bake_90_equivale_a_pack_0_en_orientacion_visual():
    """Rect 100×40 girado 90° en PARTS → geometría de nest 40×100; pack solo 0°."""
    poly0 = _rect(100.0, 40.0)
    cx, cy = poly0.centroid.x, poly0.centroid.y
    poly90 = affinity.rotate(poly0, 90, origin=(cx, cy), use_radians=False)
    minx, miny, maxx, maxy = poly90.bounds
    # Normaliza como el manager (translate a origen).
    poly_exact = affinity.translate(poly90, -minx, -miny)
    w = poly_exact.bounds[2] - poly_exact.bounds[0]
    h = poly_exact.bounds[3] - poly_exact.bounds[1]
    assert abs(w - 40.0) < 1e-6
    assert abs(h - 100.0) < 1e-6

    piece = {
        "nombre": "BRACKET",
        "poly": poly_exact,
        "area": poly_exact.area,
        "calibre": "0.375",
        "material": "A 36",
        "grain_locked": True,
        "allowed_rotations": [0],
        "orientacion_corte_bloqueada": True,
        "orientacion_corte_deg": 90,
    }
    native = _piece_to_native(piece)
    assert native["grain_locked"] is True
    assert native["allowed_rotations"] == [0]

    # Un resultado que intercambia largo/ancho viola el bloqueo.
    swapped_out = {
        "nombre": "BRACKET",
        "poligonos": [
            [(0.0, 0.0), (100.0, 0.0), (100.0, 40.0), (0.0, 40.0), (0.0, 0.0)]
        ],
    }
    assert _orientation_lock_violated(native, swapped_out) is True

    same_out = {
        "nombre": "BRACKET",
        "poligonos": [
            [(0.0, 0.0), (40.0, 0.0), (40.0, 100.0), (0.0, 100.0), (0.0, 0.0)]
        ],
    }
    assert _orientation_lock_violated(native, same_out) is False


def test_reject_locked_orientation_violations_devuelve_a_restos():
    poly = _rect(40.0, 100.0)
    pin = {
        "nombre": "LOCK",
        "poly": poly,
        "area": poly.area,
        "calibre": "0.250",
        "material": "A 36",
        "grain_locked": True,
        "allowed_rotations": [0],
    }
    hoja = {
        "piezas": [
            {
                "nombre": "LOCK",
                "poligonos": [
                    [(0.0, 0.0), (100.0, 0.0), (100.0, 40.0), (0.0, 40.0), (0.0, 0.0)]
                ],
            }
        ],
        "area_usada": 4000.0,
        "eficiencia": 0.5,
    }
    hoja2, restos = reject_locked_orientation_violations(hoja, [], [pin])
    assert hoja2["piezas"] == []
    assert len(restos) == 1
    assert restos[0]["nombre"] == "LOCK"
    assert restos[0].get("grain_locked") is True


def test_motor_aplica_bloqueo_metal_antes_del_pack(tmp_path: Path | None = None):
    """Simula mapas PARTS → item con grain_locked y rotación horneada."""
    ruta = r"C:\PARTS\Metal\RECT_PART.dxf"
    clave = clave_orientacion_cobre_ruta(ruta)
    motor = MotorNesting.__new__(MotorNesting)
    motor.orientacion_cobre_por_ruta = {}
    motor.orientacion_corte_por_ruta = {clave: 90}
    motor.orientacion_corte_bloqueada_por_ruta = {clave: True}

    poly = _rect(100.0, 40.0)
    # Réplica mínima del tramo metal-lock de analizar_piezas.
    rot_deg = int(motor.orientacion_corte_por_ruta.get(clave, 0)) % 360
    assert rot_deg == 90
    cx, cy = poly.centroid.x, poly.centroid.y
    poly_r = affinity.rotate(poly, rot_deg, origin=(cx, cy), use_radians=False)
    minx, miny, _, _ = poly_r.bounds
    poly_exact = affinity.translate(poly_r, -minx, -miny)

    item = {
        "nombre": "RECT_PART",
        "poly": poly_exact,
        "material": "A 36",
        "calibre": "0.250",
        "ruta": ruta,
        "grain_locked": True,
        "allowed_rotations": [0],
    }
    native = _piece_to_native(item)
    assert native["grain_locked"] is True
    assert native["allowed_rotations"] == [0]
    xs = [p[0] for p in native["rings"][0]]
    ys = [p[1] for p in native["rings"][0]]
    assert abs((max(xs) - min(xs)) - 40.0) < 1e-6
    assert abs((max(ys) - min(ys)) - 100.0) < 1e-6


def main() -> int:
    test_piece_to_native_propaga_grain_locked_y_solo_0()
    test_bake_90_equivale_a_pack_0_en_orientacion_visual()
    test_reject_locked_orientation_violations_devuelve_a_restos()
    test_motor_aplica_bloqueo_metal_antes_del_pack()
    print("OK orientacion_corte_bloqueada")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
