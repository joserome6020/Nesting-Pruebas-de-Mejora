"""Candado: mover, cambiar placa o renestear no pierde compensación de PARTS."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))

from shapely.geometry import LineString, box

from interface.qt.tabs._mixin_nesting_calc import NestingCalcMixin
from interface.utils_nesting import clave_orientacion_cobre_ruta
from modules.nesting_engine.manager import (
    MotorNesting,
    enriquecer_piezas_hoja_con_fuentes,
)
from modules.plasma_compensator import compute_plasma_offset_mm


def test_transfer_preserva_metadatos_plasma():
    """El pack visual de una transferencia debe retener estado y ruta plasma."""
    motor = MotorNesting.__new__(MotorNesting)
    origen = {
        "nombre": "Bracket",
        "debug_id": "0.500_A 36::Bracket::rep1",
        "poligonos": [[[10, 20], [110, 20], [110, 70], [10, 70]]],
        "marcas": [],
        "area": 5000.0,
        "calibre": "0.500",
        "material": "A 36",
        "ruta": r"C:\PARTS\Bracket.dxf",
        "plasma_compensada_manual": True,
        "plasma_offset_mm_manual": 0.3175,
        "plasma_fuente_ya_compensada": True,
        "ruta_plasma": r"C:\PARTS\Plasma Compensated\Bracket.dxf",
    }
    pieza_pack = motor._as_pack_piece_visual(origen)

    for campo in (
        "debug_id",
        "ruta",
        "plasma_compensada_manual",
        "plasma_offset_mm_manual",
        "plasma_fuente_ya_compensada",
        "ruta_plasma",
    ):
        assert pieza_pack.get(campo) == origen[campo], campo

    hoja_reempacada = {
        "piezas": [
            {
                "nombre": "Bracket",
                "poligonos": [[[0, 0], [100, 0], [100, 50], [0, 50]]],
            }
        ]
    }
    enriquecer_piezas_hoja_con_fuentes(hoja_reempacada, [pieza_pack])
    salida = hoja_reempacada["piezas"][0]
    assert salida["plasma_compensada_manual"] is True
    assert salida["ruta_plasma"] == origen["ruta_plasma"]


def test_reconstruccion_desde_parts_reutiliza_dxf_compensado():
    """Cambio de placa/renest debe usar el DXF plasma, no el DXF base."""

    class Motor:
        @staticmethod
        def _parse_thickness_value(_calibre):
            return 0.5

        @staticmethod
        def _extraer_numero(_calibre):
            return 0.5

        @staticmethod
        def recuperar_geometria_robusta(ruta):
            assert ruta == ruta_plasma
            return poligono_compensado, LineString()

    class Fake(NestingCalcMixin):
        pass

    with tempfile.TemporaryDirectory() as td:
        ruta_plasma = str(Path(td) / "Bracket_compensado.dxf")
        Path(ruta_plasma).touch()
        ruta_base = str(Path(td) / "Bracket.dxf")
        poligono_compensado = box(0, 0, 120, 70)

        ui = Fake()
        ui.app = type("App", (), {})()
        ui.app.motor_nesting = Motor()
        clave_ruta = clave_orientacion_cobre_ruta(ruta_base)
        ui.app.plasma_compensada_por_ruta = {clave_ruta: True}
        ui.app.plasma_dxf_por_ruta = {clave_ruta: ruta_plasma}

        salida = ui._pieza_pack_desde_fuente(
            {
                "nombre": "Bracket",
                "poly_base": box(0, 0, 100, 50),
                "marks_base": LineString(),
                "area_base": 5000.0,
                "calibre": "0.500",
                "material": "A 36",
                "ruta": ruta_base,
            }
        )

    assert salida["plasma_compensada_manual"] is True
    assert salida["plasma_fuente_ya_compensada"] is True
    assert salida["ruta_plasma"] == ruta_plasma
    assert salida["poly"].area == poligono_compensado.area
    assert salida["plasma_offset_mm_manual"] == compute_plasma_offset_mm(0.5)


def test_display_refresca_desde_dxf_plasma():
    """Un redraw no puede sustituir una pieza plasma por su DXF base."""
    from modules.nesting_engine import display_geometry

    with tempfile.TemporaryDirectory() as td:
        ruta_base = str(Path(td) / "Bracket.dxf")
        ruta_plasma = str(Path(td) / "Bracket_compensado.dxf")
        Path(ruta_base).touch()
        Path(ruta_plasma).touch()
        pieza = {
            "nombre": "Bracket",
            "ruta": ruta_base,
            "ruta_plasma": ruta_plasma,
            "plasma_fuente_ya_compensada": True,
            "_transform_export_ok": True,
            "rot_deg": 0.0,
            "shift_x": 0.0,
            "shift_y": 0.0,
            "poligonos": [[[0, 0], [120, 0], [120, 70], [0, 70]]],
        }
        rutas_cargadas = []
        cargar_original = display_geometry._cargar_poly_local_dxf

        def cargar_dxf(ruta):
            rutas_cargadas.append(ruta)
            assert ruta == ruta_plasma
            return box(0, 0, 120, 70), LineString(), 0.0, 0.0

        try:
            display_geometry._cargar_poly_local_dxf = cargar_dxf
            poligonos = display_geometry.poligonos_display_desde_dxf(pieza)
        finally:
            display_geometry._cargar_poly_local_dxf = cargar_original

    assert poligonos and poligonos[0]
    assert rutas_cargadas == [ruta_plasma]


def main() -> int:
    test_transfer_preserva_metadatos_plasma()
    test_reconstruccion_desde_parts_reutiliza_dxf_compensado()
    test_display_refresca_desde_dxf_plasma()
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
