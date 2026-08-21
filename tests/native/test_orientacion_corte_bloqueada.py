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

from interface.utils_nesting import clave_orientacion_cobre_ruta, clave_orientacion_pieza
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


def test_bloqueo_persiste_con_dxf_compensado():
    """El visor pinta el compensado: debe compartir clave con el DXF original.

    Sin esto, marcar la casilla o girar sobre una pieza plasma guardaba con la
    ruta de `Plasma Compensated` y al volver a la pieza se perdía todo.
    """
    origen = r"C:\JOBS\AutoDXF\Processed Files\SWITCH PATCH 1.dxf"
    compensado = r"C:\JOBS\AutoDXF\Processed Files\Plasma Compensated\SWITCH PATCH 1.dxf"
    clave = clave_orientacion_cobre_ruta(origen)
    mapa_plasma = {clave: compensado}

    assert clave_orientacion_pieza(origen, mapa_plasma) == clave
    assert clave_orientacion_pieza(compensado, mapa_plasma) == clave
    # Sin mapa la clave es la del propio archivo (piezas sin plasma).
    assert clave_orientacion_pieza(origen, {}) == clave

    # Ciclo real: se guarda desde el compensado y se lee desde el original.
    bloqueadas: dict[str, bool] = {}
    grados: dict[str, int] = {}
    bloqueadas[clave_orientacion_pieza(compensado, mapa_plasma)] = True
    grados[clave_orientacion_pieza(compensado, mapa_plasma)] = 90
    assert bloqueadas.get(clave_orientacion_pieza(origen, mapa_plasma)) is True
    assert grados.get(clave_orientacion_pieza(origen, mapa_plasma)) == 90

    # Y una pieza distinta no hereda el bloqueo.
    otra = r"C:\JOBS\AutoDXF\Processed Files\TOP BOX.dxf"
    assert bloqueadas.get(clave_orientacion_pieza(otra, mapa_plasma)) is None


def test_pieza_pack_desde_fuente_respeta_bloqueo_orien():
    """Renest calibre/placa debe hornear BLOQUEAR ORIEN (no solo nest completo)."""
    from shapely.geometry import box

    class _App:
        orientacion_corte_bloqueada_por_ruta = {}
        orientacion_corte_por_ruta = {}
        plasma_compensada_por_ruta = {}

    class _Tab:
        def __init__(self):
            self.app = _App()

    # Reusar la lógica real del mixin sin Qt.
    from interface.qt.tabs._mixin_nesting_calc import NestingCalcMixin

    tab = _Tab()
    ruta = r"C:\PARTS\Metal\62140-1359-P01.dxf"
    clave = clave_orientacion_cobre_ruta(ruta)
    tab.app.orientacion_corte_bloqueada_por_ruta[clave] = True
    tab.app.orientacion_corte_por_ruta[clave] = 90

    poly = box(0.0, 0.0, 100.0, 40.0)  # L=100, A=40 → tras 90° queda 40×100
    src = {
        "nombre": "62140-1359-P01",
        "ruta": ruta,
        "material": "A 36",
        "calibre": "0.105",
        "poly_base": poly,
        "marks_base": None,
        "area_base": float(poly.area),
    }
    item = NestingCalcMixin._pieza_pack_desde_fuente(tab, src)
    assert item.get("grain_locked") is True
    assert list(item.get("allowed_rotations") or []) == [0]
    assert int(item.get("orientacion_corte_deg") or 0) == 90
    minx, miny, maxx, maxy = item["poly"].bounds
    assert abs((maxx - minx) - 40.0) < 1e-6
    assert abs((maxy - miny) - 100.0) < 1e-6


def test_tooltip_de_panel_oscuro_tiene_contraste():
    """Sobre panel oscuro el tooltip debe declarar fondo oscuro + letra clara."""
    from interface.qt.theme import TOOLTIP_OSCURO_QSS

    assert "color:#F8FAFC" in TOOLTIP_OSCURO_QSS.replace(" ", "")
    assert "background-color:#1E293B" in TOOLTIP_OSCURO_QSS.replace(" ", "")

    src = (RAIZ / "interface" / "qt" / "visualizer.py").read_text(encoding="utf-8")
    assert src.count("TOOLTIP_OSCURO_QSS") >= 3, (
        "el panel del visor y la casilla de orientación deben aplicar el tooltip oscuro"
    )
    bloque = src.split("self.chk_orientacion_corte.setStyleSheet(")[1].split(")")[0]
    assert "TOOLTIP_OSCURO_QSS" in bloque


def main() -> int:
    test_piece_to_native_propaga_grain_locked_y_solo_0()
    test_bake_90_equivale_a_pack_0_en_orientacion_visual()
    test_reject_locked_orientation_violations_devuelve_a_restos()
    test_motor_aplica_bloqueo_metal_antes_del_pack()
    test_bloqueo_persiste_con_dxf_compensado()
    test_tooltip_de_panel_oscuro_tiene_contraste()
    print("OK orientacion_corte_bloqueada")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
