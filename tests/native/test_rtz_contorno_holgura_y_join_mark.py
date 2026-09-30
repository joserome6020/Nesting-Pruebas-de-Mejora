"""Candado SWO-068 H21 (2026-09-28): contorno RTZ y peso del DXF.

1) El corte RTZ en la madre (RETAZO_GUILLOTINA__) caía sobre el borde de la
   pieza o, con varias piezas, en su casco: pisaba piezas vecinas de la madre y
   RTZ contiguos compartían línea de corte. Ahora: casco + margen, recortado a
   gap/2 de las piezas que quedan y a 1.6 mm de otros RTZ; el borde del RTZ
   (capa Plate de su DXF) es el mismo contorno.
2) MARK / RTZ_LABEL salían como miles de LINE sueltas (DXF de 1.5 MB). Al
   exportar acero se unen en LWPOLYLINE sin cambiar la geometría.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import ezdxf  # noqa: E402
from shapely import affinity  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

from modules.dxf_mark_join import join_mark_layers  # noqa: E402
from modules.nesting_engine.rtz_manual_promote import (  # noqa: E402
    promote_forzar_zones_on_madre,
    promote_selection_to_rtz,
)

RTZ_SEP_MM = 1.6
KERF_IN = 0.25
GAP = KERF_IN * 25.4


def _pieza(nombre, coords):
    return {"nombre": nombre, "poligonos": [list(coords)], "marcas": [], "calibre": "1/4", "material": "A 36"}


def _rect(x, y, w, h):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]


def _madre():
    # L grande a promover; KEEP_NOTCH vive en su hueco (el casco lo pisaría).
    l_shape = [(0, 0), (300, 0), (300, 100), (100, 100), (100, 300), (0, 300), (0, 0)]
    off = (20, 20)
    l_shape = [(x + off[0], y + off[1]) for x, y in l_shape]
    notch = _rect(20 + 100 + GAP, 20 + 100 + GAP, 120, 120)
    vecino = _rect(20 + 300 + GAP, 20, 80, 90)  # otro RTZ contiguo
    keep_lejos = _rect(700, 20, 200, 200)
    return {
        "placa_id": "PLC001 P1",
        "placa_w": 1200.0,
        "placa_h": 600.0,
        "kerf_usado": KERF_IN,
        "placa_cal": "1/4",
        "placa_mat": "A 36",
        "piezas": [
            _pieza("W.O. 1__L PLATE", l_shape),
            _pieza("W.O. 1__KEEP NOTCH", notch),
            _pieza("W.O. 1__VECINO", vecino),
            _pieza("W.O. 1__KEEP LEJOS", keep_lejos),
        ],
    }


def _check_rtz(fallos: list[str]) -> None:
    madre = _madre()
    orig = {p["nombre"]: Polygon(p["poligonos"][0]) for p in madre["piezas"]}
    hojas = [madre]
    res = promote_selection_to_rtz(madre, [0, 2], hojas_grupo=hojas, calibre="1/4", wo_name="W.O. 1")
    if not res.get("ok") or len(res["rtz_hojas"]) != 2:
        fallos.append(f"promote no creó 2 RTZ: {res.get('motivo')}")
        return
    guill = {
        p["nombre"].split("__", 1)[1]: Polygon(p["poligonos"][0])
        for p in madre["piezas"]
        if p["nombre"].startswith("RETAZO_GUILLOTINA__")
    }
    keepers = [orig["W.O. 1__KEEP NOTCH"], orig["W.O. 1__KEEP LEJOS"]]
    propios = {res["rtz_ids"][0]: orig["W.O. 1__L PLATE"], res["rtz_ids"][1]: orig["W.O. 1__VECINO"]}
    for rid, g in guill.items():
        own = propios[rid]
        if not g.contains(own):
            fallos.append(f"{rid}: el contorno no contiene su pieza")
        margen = g.exterior.distance(own)
        if margen < 0.5:
            fallos.append(f"{rid}: contorno pegado a la pieza (margen {margen:.2f} mm)")
        for k in keepers:
            d = g.distance(k)
            if g.intersects(k) or d < GAP / 2 - 0.05:
                fallos.append(f"{rid}: pisa/roza pieza de la madre (dist {d:.2f} mm)")
    a, b = list(guill.values())
    if a.distance(b) < RTZ_SEP_MM - 0.05:
        fallos.append(f"RTZ contiguos comparten corte (sep {a.distance(b):.2f} mm)")
    for h in res["rtz_hojas"]:
        borde = affinity.translate(Polygon(h["poly_borde_retazo"]), h["global_x"], h["global_y"])
        g = guill[h["placa_id"]]
        if borde.symmetric_difference(g).area > 1e-3:
            fallos.append(f"{h['placa_id']}: Plate del RTZ != corte en la madre")


def _check_forzar_cluster(fallos: list[str]) -> None:
    a = _rect(20, 20, 100, 300)
    b = _rect(20 + 100 + GAP, 20, 250, 100)
    notch = _rect(20 + 100 + GAP, 20 + 100 + GAP, 120, 120)
    madre = _madre()
    madre["piezas"] = [
        dict(_pieza("W.O. 1__A", a), forzar_rtz=True),
        dict(_pieza("W.O. 1__B", b), forzar_rtz=True),
        _pieza("W.O. 1__KEEP NOTCH", notch),
    ]
    hojas = [madre]
    res = promote_forzar_zones_on_madre(madre, hojas_grupo=hojas, calibre="1/4", wo_name="W.O. 1")
    guill = [
        Polygon(p["poligonos"][0])
        for p in madre["piezas"]
        if p["nombre"].startswith("RETAZO_GUILLOTINA__")
    ]
    if res.get("n_rtz") != 1 or len(guill) != 1:
        fallos.append(f"forzar: se esperaba 1 RTZ con A+B, hubo {res.get('n_rtz')}")
        return
    g = guill[0]
    for nom, coords in (("A", a), ("B", b)):
        if not g.contains(Polygon(coords)):
            fallos.append(f"forzar: el contorno RTZ no contiene la pieza {nom}")
    k = Polygon(notch)
    if g.intersects(k) or g.distance(k) < GAP / 2 - 0.05:
        fallos.append(f"forzar: el contorno pisa la pieza de la madre (dist {g.distance(k):.2f})")


def _check_join(fallos: list[str]) -> None:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    pts = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    for a, b in zip(pts, pts[1:]):
        msp.add_line(a, b, dxfattribs={"layer": "RTZ_LABEL"})
    msp.add_line((20, 0), (30, 0), dxfattribs={"layer": "MARK"})
    msp.add_arc((30, 5), 5, 270, 90, dxfattribs={"layer": "MARK"})
    msp.add_line((30, 10), (20, 10), dxfattribs={"layer": "MARK"})
    for a, b in zip(pts, pts[1:]):
        msp.add_line(a, b, dxfattribs={"layer": "CUT_OUTER"})

    def largo(layer):
        tot = 0.0
        for e in msp.query(f'*[layer=="{layer}"]'):
            for v in (e.virtual_entities() if e.dxftype() == "LWPOLYLINE" else [e]):
                if v.dxftype() == "LINE":
                    tot += math.dist(v.dxf.start, v.dxf.end)
                else:
                    sw = (v.dxf.end_angle - v.dxf.start_angle) % 360
                    tot += math.radians(sw) * v.dxf.radius
        return tot

    antes = {ly: largo(ly) for ly in ("MARK", "RTZ_LABEL", "CUT_OUTER")}
    join_mark_layers(msp)
    tipos = {ly: sorted(e.dxftype() for e in msp.query(f'*[layer=="{ly}"]')) for ly in antes}
    if tipos["RTZ_LABEL"] != ["LWPOLYLINE"] or tipos["MARK"] != ["LWPOLYLINE"]:
        fallos.append(f"JOIN no unió marcaje: {tipos}")
    if tipos["CUT_OUTER"] != ["LINE"] * 4:
        fallos.append(f"JOIN tocó capa de corte: {tipos['CUT_OUTER']}")
    for ly, v in antes.items():
        if abs(largo(ly) - v) > 1e-6:
            fallos.append(f"JOIN cambió geometría en {ly}: {v:.4f} -> {largo(ly):.4f}")
    src = (RAIZ / "modules/nest_exporter.py").read_text(encoding="utf-8")
    if "join_mark_layers(msp)" not in src:
        fallos.append("nest_exporter debe unir MARK/RTZ_LABEL al guardar acero")


def main() -> int:
    fallos: list[str] = []
    _check_rtz(fallos)
    _check_forzar_cluster(fallos)
    _check_join(fallos)
    if fallos:
        print("FAIL:\n  " + "\n  ".join(fallos))
        return 1
    print("OK contorno RTZ con holgura + JOIN MARK/RTZ_LABEL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
