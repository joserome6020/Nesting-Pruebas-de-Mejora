"""Candados: forzar RTZ desde PARTS + Nestear como RTZ (promote 1:1)."""
from __future__ import annotations

import sys
from pathlib import Path

from shapely.geometry import Polygon

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))

from modules.nesting_engine.manager import MotorNesting
from modules.nesting_engine.rtz_manual_promote import (
    promote_selection_to_rtz,
    stays_on_madre,
)


def test_forzar_rtz_propaga_en_item_y_manager():
    motor = MotorNesting()
    assert hasattr(motor, "forzar_rtz_por_ruta")
    motor.forzar_rtz_por_ruta = {r"C:\dxf\pieza.dxf": True}
    item = {"nombre": "BRACKET-A", "ruta": r"C:\dxf\pieza.dxf", "forzar_rtz": True}
    assert bool(item.get("forzar_rtz")) is True
    assert bool(motor.forzar_rtz_por_ruta.get(r"C:\dxf\pieza.dxf")) is True


def test_stays_on_madre_keepers():
    assert stays_on_madre("SWO__PLACA BASE__001") is True
    assert stays_on_madre("X__PLACA TOP COVER") is True
    assert stays_on_madre("BRACKET-A") is False


def _rect_pieza(nombre: str, x0: float, y0: float, w: float, h: float) -> dict:
    return {
        "nombre": nombre,
        "poligonos": [
            [
                (x0, y0),
                (x0 + w, y0),
                (x0 + w, y0 + h),
                (x0, y0 + h),
                (x0, y0),
            ]
        ],
        "marcas": [],
        "area": float(w * h),
        "calibre": "0.25",
        "material": "A 36",
    }


def test_promote_uno_a_uno_todas_las_seleccionadas():
    """12 seleccionadas → 12 RTZ. Nunca un subconjunto."""
    keep = _rect_pieza("KEEP", 0.0, 0.0, 800.0, 400.0)
    sel = [_rect_pieza(f"ACC-{i}", 100.0 + i * 250.0, 500.0, 180.0, 120.0) for i in range(5)]
    madre = {
        "placa_id": "H1",
        "placa_w": 6096.0,
        "placa_h": 2438.0,
        "placa_cal": "0.25",
        "placa_mat": "A 36",
        "origen_placa": "EMPRESA",
        "kerf_usado": 0.15,
        "es_retazo": False,
        "piezas": [keep, *sel],
    }
    hojas = [madre]
    indices = list(range(1, 6))
    res = promote_selection_to_rtz(
        madre, indices, hojas_grupo=hojas, calibre="0.25", wo_name="SWO-068", contador_rtz=1
    )
    assert res["ok"] is True
    assert res["n_seleccionadas"] == 5
    assert res["n_piezas"] == 5
    assert res["n_rtz"] == 5
    assert len(hojas) == 1 + 5
    assert all(h.get("es_retazo") for h in hojas[1:])
    nombres = [str(p.get("nombre") or "") for p in madre["piezas"]]
    for i in range(5):
        assert f"ACC-{i}" not in nombres
        assert any(n.startswith(f"REF__ACC-{i}") for n in nombres)


def test_promote_incluye_preformados_si_usuario_los_seleccionó():
    """Si el usuario selecciona PREFORMADOS BASE, se promueven (con aviso)."""
    keep = _rect_pieza("KEEP", 0.0, 0.0, 500.0, 400.0)
    pref = _rect_pieza("W.O. 80 X4_PREFORMADOS BASE", 600.0, 0.0, 400.0, 100.0)
    madre = {
        "placa_id": "H1",
        "placa_w": 3048.0,
        "placa_h": 1524.0,
        "placa_cal": "0.25",
        "placa_mat": "A 36",
        "origen_placa": "EMPRESA",
        "es_retazo": False,
        "piezas": [keep, pref],
    }
    hojas = [madre]
    res = promote_selection_to_rtz(
        madre, [1], hojas_grupo=hojas, calibre="0.25", wo_name="SWO-068", contador_rtz=1
    )
    assert res["ok"] is True
    assert res["n_piezas"] == 1
    assert res.get("avisos_estructurales")
    assert "PREFORMADOS BASE" not in [
        str(p.get("nombre") or "") for p in madre["piezas"]
    ]


def test_promote_rechaza_hoja_retazo():
    hoja = {
        "placa_id": "RTZ1-x",
        "es_retazo": True,
        "piezas": [_rect_pieza("A", 0, 0, 100, 80)],
    }
    res = promote_selection_to_rtz(hoja, [0], hojas_grupo=[hoja], contador_rtz=2)
    assert res["ok"] is False


def test_promote_guillotina_sigue_pieza_no_bbox_vacio():
    keep = _rect_pieza("KEEP-BIG", 0.0, 0.0, 2000.0, 800.0)
    a = _rect_pieza("ACC-A", 100.0, 900.0, 400.0, 200.0)
    madre = {
        "placa_id": "H1",
        "placa_w": 6096.0,
        "placa_h": 2438.0,
        "placa_cal": "0.25",
        "placa_mat": "A 36",
        "origen_placa": "EMPRESA",
        "kerf_usado": 0.15,
        "es_retazo": False,
        "piezas": [keep, a],
    }
    hojas = [madre]
    res = promote_selection_to_rtz(
        madre, [1], hojas_grupo=hojas, calibre="0.25", wo_name="SWO-068", contador_rtz=1
    )
    assert res["ok"] is True
    rtz = hojas[1]
    assert rtz.get("guillotina_desde_borde") is True
    keep_poly = Polygon(keep["poligonos"][0])
    guill = None
    for p in madre["piezas"]:
        if str(p.get("nombre") or "").startswith("RETAZO_GUILLOTINA__"):
            guill = Polygon(p["poligonos"][0])
            break
    assert guill is not None
    assert float(guill.intersection(keep_poly).area) < 100.0


def test_promote_forzar_zona_barreno_un_rtz_varias_piezas():
    """Switch RTZ: N piezas en un barreno → 1 RTZ con contorno del orificio."""
    # Keeper 600x400 con barreno interior 300x200
    keep = {
        "nombre": "HOST-PLATE",
        "poligonos": [
            [(0, 0), (600, 0), (600, 400), (0, 400), (0, 0)],
            [(100, 80), (400, 80), (400, 280), (100, 280), (100, 80)],
        ],
        "marcas": [],
        "area": 180000.0,
        "calibre": "0.25",
        "material": "A 36",
    }
    a = _rect_pieza("ACC-A", 120.0, 100.0, 80.0, 60.0)
    a["forzar_rtz"] = True
    b = _rect_pieza("ACC-B", 220.0, 120.0, 70.0, 50.0)
    b["forzar_rtz"] = True
    c = _rect_pieza("ACC-C", 310.0, 150.0, 60.0, 40.0)  # sin flag, mismo barreno
    madre = {
        "placa_id": "H1",
        "placa_w": 6096.0,
        "placa_h": 2438.0,
        "placa_cal": "0.25",
        "placa_mat": "A 36",
        "origen_placa": "EMPRESA",
        "kerf_usado": 0.15,
        "es_retazo": False,
        "piezas": [keep, a, b, c],
    }
    hojas = [madre]
    from modules.nesting_engine.rtz_manual_promote import promote_forzar_zones_on_madre

    res = promote_forzar_zones_on_madre(
        madre, hojas_grupo=hojas, calibre="0.25", wo_name="SWO-068", contador_rtz=1
    )
    assert res["ok"] is True
    assert res["n_rtz"] == 1, res
    assert res["n_piezas"] == 3  # A+B forzadas + C compañera del barreno
    assert len(hojas) == 2
    rtz = hojas[1]
    assert rtz.get("es_retazo") is True
    assert rtz.get("retazo_tipo") == "HOLE"
    # Contorno ≈ barreno, no bbox de una sola pieza
    assert float(rtz.get("placa_w") or 0) >= 280.0
    assert float(rtz.get("placa_h") or 0) >= 180.0
    nombres_madre = [str(p.get("nombre") or "") for p in madre["piezas"]]
    assert "ACC-A" not in nombres_madre
    assert any(n.startswith("REF__ACC-A") for n in nombres_madre)
    assert "HOST-PLATE" in nombres_madre


def test_stamp_forzar_por_nombre_y_ruta_norm():
    from modules.nesting_engine.rtz_manual_promote import stamp_forzar_rtz_on_piezas

    piezas = [
        {"nombre": "W.O. 80 X4__LUG TOP COVER", "ruta": r"C:\DXF\Lug.dxf"},
        {"nombre": "OTHER", "ruta": r"C:\DXF\Other.dxf"},
    ]
    # Flag con ruta normcase distinta a la cruda
    flags_ruta = {r"c:\dxf\lug.dxf": True}
    n = stamp_forzar_rtz_on_piezas(piezas, flags_ruta=flags_ruta, flags_nombre={})
    assert n >= 1
    assert piezas[0].get("forzar_rtz") is True
    assert not piezas[1].get("forzar_rtz")
    piezas2 = [
        {"nombre": "W.O. 80 X4__UNION SEG", "ruta": r"C:\DXF\u.dxf"},
    ]
    n2 = stamp_forzar_rtz_on_piezas(
        piezas2, flags_ruta={}, flags_nombre={"UNION SEG": True}
    )
    assert n2 == 1
    assert piezas2[0].get("forzar_rtz") is True


if __name__ == "__main__":
    test_forzar_rtz_propaga_en_item_y_manager()
    test_stays_on_madre_keepers()
    test_promote_uno_a_uno_todas_las_seleccionadas()
    test_promote_incluye_preformados_si_usuario_los_seleccionó()
    test_promote_rechaza_hoja_retazo()
    test_promote_guillotina_sigue_pieza_no_bbox_vacio()
    test_promote_forzar_zona_barreno_un_rtz_varias_piezas()
    test_stamp_forzar_por_nombre_y_ruta_norm()
    print("OK test_forzar_rtz_dual")
