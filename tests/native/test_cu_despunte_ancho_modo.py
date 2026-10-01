"""Candado: cobre sin gap + despunte 6 mm + modos de ancho (Conf1/Conf2) + MARK por pieza.

Reglas (2026-10-01):
- Piezas normales (rectangulares) van pegadas (gap 0) y la barra entera lleva
  un despunte de 6 mm antes de la primera pieza (zona + guillotina).
- Barras Zapato/Botella/Z (laser) no llevan despunte.
- Configuración 1 (exacto, default): piezas con decimal no entran a barras de
  exactas; las exactas sobrantes sí pueden rellenar barras residuales.
- Configuración 2 (mixto): se mezclan como antes.
- ``cu_sin_marcaje`` solo quita MARK a Z/especial; las normales siempre llevan MARK.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shapely.geometry import Polygon, box  # noqa: E402

BAR_6IN_MM = 152.4
DEC_575_MM = 146.05


def _placas() -> list[dict]:
    return [
        {
            "w": 3657.6,
            "h": BAR_6IN_MM,
            "precio": 100.0,
            "precio_lb": 1.0,
            "origen_placa": "EMPRESA",
            "material": "CU",
            "calibre": "0.25",
        }
    ]


def _pieza(nombre: str, largo: float, ancho: float = BAR_6IN_MM, poly=None) -> dict:
    poly = poly if poly is not None else box(0, 0, largo, ancho)
    return {
        "nombre": nombre,
        "poly": poly,
        "marks": None,
        "area": poly.area,
        "calibre": "0.25",
        "material": "CU",
        "ruta": f"{nombre}.dxf",
    }


def _reales(hoja: dict) -> list[dict]:
    return [
        p
        for p in hoja.get("piezas") or []
        if not str(p.get("nombre") or "").startswith("CU_CORTE__")
    ]


def _minx(p: dict) -> float:
    return min(float(pt[0]) for pt in p["poligonos"][0])


def _maxx(p: dict) -> float:
    return max(float(pt[0]) for pt in p["poligonos"][0])


def _es_dec(p: dict) -> bool:
    return str(p.get("nombre") or "").startswith("DEC")


def main() -> None:
    previous_data_dir = os.environ.get("ARGA_NEST_DATA_DIR")
    saved_env = {k: os.environ.get(k) for k in ("ARGA_CU_FORCE_DXF_STEP", "ARGA_CU_ANCHO_MODO")}
    with tempfile.TemporaryDirectory() as temp_dir:
        os.environ["ARGA_NEST_DATA_DIR"] = temp_dir
        for k in saved_env:
            os.environ.pop(k, None)

        from modules.nesting_engine import cu_largos_nesting as cln
        from modules.nesting_engine.nest_runtime_prefs import (
            cu_ancho_modo,
            load_nest_runtime_prefs,
            normalize_cu_ancho_modo,
            save_nest_runtime_prefs,
            should_omit_copper_marks,
            should_omit_copper_marks_pieza,
        )

        prefs = load_nest_runtime_prefs()
        assert prefs.get("cu_ancho_modo") == "exacto", prefs
        assert cu_ancho_modo() == "exacto"
        assert normalize_cu_ancho_modo("mixto") == "mixto"
        assert normalize_cu_ancho_modo("2") == "mixto"
        assert normalize_cu_ancho_modo("basura") == "exacto"
        assert cln.DEFAULT_SEPARACION_CU_IN == 0.0

        # 1) Gap 0 + despunte 6 mm; Conf1 separa exactas de decimales.
        piezas = [_pieza(f"EX{i}", 1000.0) for i in range(3)]
        piezas += [_pieza(f"DEC{i}", 1000.0, DEC_575_MM) for i in range(2)]
        hojas, sin = cln.empaquetar_largos_cu(piezas, _placas(), separacion_in=0.0)
        assert not sin, sin
        assert len(hojas) == 2, [len(_reales(h)) for h in hojas]
        for h in hojas:
            reales = sorted(_reales(h), key=_minx)
            assert abs(_minx(reales[0]) - cln.DESPUNTE_CU_MM) < 0.01, _minx(reales[0])
            for a, b in zip(reales, reales[1:]):
                assert abs(_minx(b) - _maxx(a)) < 0.01, "cobre normal debe ir sin gap"
            clases = {_es_dec(p) for p in reales}
            assert len(clases) == 1, f"Conf1 mezcló exactas con decimales: {clases}"
            nombres = {p.get("nombre") for p in h["piezas"]}
            assert cln.NOMBRE_DESPUNTE_ZONA_CU in nombres
            assert cln.NOMBRE_DESPUNTE_CORTE_CU in nombres
            assert abs(float(h.get("cu_despunte_mm") or 0.0) - 6.0) < 0.01
            assert float(h.get("separacion_cu_in")) == 0.0

        # 2) Conf1: exactas que sobran (barra incompleta) rellenan la residual.
        piezas = [_pieza(f"EX{i}", 1200.0) for i in range(4)]
        piezas.append(_pieza("DEC0", 1000.0, DEC_575_MM))
        hojas, sin = cln.empaquetar_largos_cu(piezas, _placas(), separacion_in=0.0)
        assert not sin, sin
        assert len(hojas) == 2, [[p["nombre"] for p in _reales(h)] for h in hojas]
        residual = [h for h in hojas if any(_es_dec(p) for p in _reales(h))]
        assert len(residual) == 1
        assert len(_reales(residual[0])) == 2, [p["nombre"] for p in _reales(residual[0])]
        llenas = [h for h in hojas if h is not residual[0]]
        assert all(not _es_dec(p) for p in _reales(llenas[0]))

        # 3) Conf2 (mixto): sin restricción de clase de ancho.
        save_nest_runtime_prefs({"cu_ancho_modo": "mixto"})
        assert cu_ancho_modo() == "mixto"
        barra_ex = {"colocados": [({"wid_mm": BAR_6IN_MM, "barra_objetivo_in": 6.0}, 50.0, 0.0)]}
        item_dec = {"wid_mm": DEC_575_MM, "barra_objetivo_in": 6.0}
        assert cln._barra_acepta_clase_ancho(barra_ex, item_dec) is True
        piezas = [_pieza("EX0", 1500.0), _pieza("DEC0", 1500.0, DEC_575_MM)]
        hojas, sin = cln.empaquetar_largos_cu(piezas, _placas(), separacion_in=0.0)
        assert not sin and len(hojas) == 1, len(hojas)
        save_nest_runtime_prefs({"cu_ancho_modo": "exacto"})
        assert cln._barra_acepta_clase_ancho(barra_ex, item_dec) is False

        # 4) Z / Zapato: sin despunte (laser), empieza en 0.
        z_poly = Polygon(
            [(0, 0), (1000, 0), (1000, 100), (500, 100), (500, BAR_6IN_MM), (0, BAR_6IN_MM)]
        )
        hojas, sin = cln.empaquetar_largos_cu(
            [_pieza("ZAP0", 1000.0, poly=z_poly)], _placas(), separacion_in=0.0
        )
        assert not sin and len(hojas) == 1
        reales = _reales(hojas[0])
        assert abs(_minx(reales[0])) < 0.01, _minx(reales[0])
        nombres = {p.get("nombre") for p in hojas[0]["piezas"]}
        assert cln.NOMBRE_DESPUNTE_ZONA_CU not in nombres
        assert float(hojas[0].get("cu_despunte_mm") or 0.0) == 0.0

        # 5) MARK por pieza: switch ON solo quita MARK a Z/especial.
        save_nest_runtime_prefs({"cu_sin_marcaje": True})
        normal = {"nombre": "N", "poligonos": [[(0, 0), (100, 0), (100, 50), (0, 50), (0, 0)]]}
        zeta = {"nombre": "Z", "cu_perfil_relieve": True}
        assert should_omit_copper_marks("CU") is False, "material-only debe conservar MARK"
        assert should_omit_copper_marks("CU", poly=box(0, 0, 100, 50)) is False
        assert should_omit_copper_marks("CU", poly=z_poly) is True
        assert should_omit_copper_marks("CU", especial=True) is True
        assert should_omit_copper_marks_pieza({**normal, "material": "CU"}) is False
        assert should_omit_copper_marks_pieza({**zeta, "material": "CU"}) is True
        assert should_omit_copper_marks("AL", poly=z_poly) is False
        save_nest_runtime_prefs({"cu_sin_marcaje": False})
        assert should_omit_copper_marks("CU", poly=z_poly) is False

        # 6) Modo forzado DXF+STEP: sin despunte (comportamiento legacy).
        save_nest_runtime_prefs({"cu_force_dxf_step": True})
        assert cln._despunte_inicio_mm([{"wid_mm": BAR_6IN_MM}]) == 0.0
        save_nest_runtime_prefs({"cu_force_dxf_step": False})

    if previous_data_dir is None:
        os.environ.pop("ARGA_NEST_DATA_DIR", None)
    else:
        os.environ["ARGA_NEST_DATA_DIR"] = previous_data_dir
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    print("SMOKE OK")


if __name__ == "__main__":
    main()
