"""Candado: piezas <= 456.954 in² se reservan a RTZ (flujo nativo de retazos).

- Madre con RTZ permitido: la pieza chica no va física en la madre; va en RTZ
  (<= 120x60", >= 2 piezas) con REF__ en la madre, o en placa de cama 120x60.
- Madre "cama láser sin mini nest" (<= 3/8" y largo <= 120"): la chica va
  directo en la placa (sin RTZ).
- Grupo solo de chicas: placa 120x60 por defecto aunque haya 96x240.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from shapely.geometry import box

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "interface"))

os.environ.setdefault("ARGA_ALLOW_INCOMPLETE_NEST", "0")

from modules.nesting_engine.manager import MotorNesting  # noqa: E402
from modules.nesting_engine.regla_area_rtz import (  # noqa: E402
    AREA_PIEZA_RTZ_MAX_MM2,
    pieza_reservada_rtz,
    placas_cama_laser,
)

IN = 25.4
TOL = 2.0


def _pieza(nombre, w_in, h_in, cal):
    poly = box(0.0, 0.0, w_in * IN, h_in * IN)
    return {
        "nombre": nombre,
        "poly": poly,
        "marks": box(0, 0, 0, 0).boundary,
        "area": poly.area,
        "calibre": cal,
        "material": "Carbono",
        "ruta": f"C:/dxf/{nombre}.dxf",
        "orig_minx": 0.0,
        "orig_miny": 0.0,
        "poly_exact": poly,
        "debug_id": f"{nombre}",
    }


def _placa(cal, pid, w_in, h_in, precio):
    return [cal, "Carbono", pid, w_in, h_in, 500.0, precio, 0.5, "DISPONIBLE", "EMPRESA", 0.5]


def _nest(cal, piezas, placas):
    motor = MotorNesting()
    motor._preflight_done = True
    clave, res = motor._procesar_grupo_parallel(
        f"{cal}_Carbono",
        piezas,
        placas,
        0.15,
        0.25,
        "OPTIMIZAR LARGO Y ANCHO",
        "INFERIOR IZQUIERDA",
        wo_name="SWO-TEST",
    )
    assert not res.get("error"), res.get("error")
    return res["hojas"]


def _fisicas(hoja):
    out = []
    for p in hoja.get("piezas") or []:
        n = str(p.get("nombre") or "")
        if n.startswith(("REF__", "TATUAJE__", "REMANENTE__", "RETAZO_GUILLOTINA__", "CU_CORTE__")):
            continue
        out.append(n)
    return out


def test_helpers():
    assert pieza_reservada_rtz({"area": AREA_PIEZA_RTZ_MAX_MM2 - 1})
    assert pieza_reservada_rtz({"area": 456.954 * 645.16})
    assert not pieza_reservada_rtz({"area": 457.5 * 645.16})
    placas = [
        {"id": "A", "w": 96 * IN, "h": 240 * IN},
        {"id": "B", "w": 60 * IN, "h": 120 * IN},
        {"id": "C", "w": 48 * IN, "h": 96 * IN},
    ]
    assert [p["id"] for p in placas_cama_laser(placas)] == ["B"]
    assert [p["id"] for p in placas_cama_laser(placas[::2])] == ["C"]
    assert placas_cama_laser(placas[:1]) == []


def test_madre_con_rtz_no_lleva_chicas_fisicas():
    cal = "0.5"
    grandes = [_pieza(f"BIG-{i}", 90, 90, cal) for i in range(2)]
    chicas = [_pieza(f"SMALL-{i}", 10, 10, cal) for i in range(8)]
    placas = [_placa(cal, "P96x240", 96, 240, 9000.0), _placa(cal, "P60x120", 60, 120, 3000.0)]
    hojas = _nest(cal, grandes + chicas, placas)

    colocadas = []
    for h in hojas:
        fis = _fisicas(h)
        colocadas.extend(fis)
        if h.get("es_retazo"):
            w, hh = float(h["placa_w"]), float(h["placa_h"])
            assert max(w, hh) <= 120 * IN + TOL and min(w, hh) <= 60 * IN + TOL, h["placa_id"]
            assert min(w, hh) >= 20 * IN - TOL, h["placa_id"]
            assert len(fis) >= 2, (h["placa_id"], fis)
        elif any(n.startswith("BIG-") for n in fis):
            assert not [n for n in fis if n.startswith("SMALL-")], (h["placa_id"], fis)
        else:
            w, hh = float(h["placa_w"]), float(h["placa_h"])
            assert max(w, hh) <= 120 * IN + TOL and min(w, hh) <= 60 * IN + TOL, h["placa_id"]
    assert sorted(colocadas) == sorted(p["nombre"] for p in grandes + chicas), colocadas
    _check_nombre_rtz_coincide(hojas)


def _check_nombre_rtz_coincide(hojas):
    for h in hojas:
        if not h.get("es_retazo"):
            continue
        dims = f"-{float(h['placa_h']) / IN:.1f}x{float(h['placa_w']) / IN:.1f}-"
        assert dims in str(h["placa_id"]), (h["placa_id"], dims)


def test_nombre_rtz_con_medidas_tras_clamp_120x60():
    """W.O. 89: sobrante 100.5x96 recortado a 100.5x60 se nombraba RTZ…-96.0x100.5."""
    cal = "0.375"
    grandes = [_pieza("BIG-0", 90, 130, cal)]
    chicas = [_pieza(f"SMALL-{i}", 10, 10, cal) for i in range(10)]
    placas = [_placa(cal, "P96x240", 96, 240, 9000.0)]
    hojas = _nest(cal, grandes + chicas, placas)
    rtz = [h for h in hojas if h.get("es_retazo")]
    assert rtz, [h.get("placa_id") for h in hojas]
    for h in rtz:
        w, hh = float(h["placa_w"]), float(h["placa_h"])
        assert max(w, hh) <= 120 * IN + TOL and min(w, hh) <= 60 * IN + TOL, h["placa_id"]
    _check_nombre_rtz_coincide(hojas)


def test_cama_sin_mini_nest_chicas_directo():
    cal = "0.25"
    grandes = [_pieza("BIG-0", 40, 90, cal)]
    chicas = [_pieza(f"SMALL-{i}", 10, 10, cal) for i in range(4)]
    placas = [_placa(cal, "P60x120", 60, 120, 3000.0)]
    hojas = _nest(cal, grandes + chicas, placas)
    assert not any(h.get("es_retazo") for h in hojas)
    colocadas = [n for h in hojas for n in _fisicas(h)]
    assert sorted(colocadas) == sorted(p["nombre"] for p in grandes + chicas), colocadas
    assert len(hojas) == 1, [h.get("placa_id") for h in hojas]


def test_solo_chicas_usan_placa_120x60():
    cal = "0.5"
    chicas = [_pieza(f"SMALL-{i}", 12, 12, cal) for i in range(6)]
    placas = [_placa(cal, "P96x240", 96, 240, 1000.0), _placa(cal, "P60x120", 60, 120, 3000.0)]
    hojas = _nest(cal, chicas, placas)
    assert hojas
    for h in hojas:
        assert not h.get("es_retazo")
        assert str(h.get("placa_id")) == "P60x120", h.get("placa_id")


if __name__ == "__main__":
    test_helpers()
    test_madre_con_rtz_no_lleva_chicas_fisicas()
    test_nombre_rtz_con_medidas_tras_clamp_120x60()
    test_cama_sin_mini_nest_chicas_directo()
    test_solo_chicas_usan_placa_120x60()
    print("OK test_rtz_reserva_piezas_chicas")
