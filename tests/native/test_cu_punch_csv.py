"""Candado: CSV CNC Busbar Punching (Lijian MX602K / LJcad) por barra de cobre.

- Una fila por pieza en orden del nest; sin fila de despunte (6 mm solo visual en el nest).
- Primer golpe = grabado M100 en Y = 0, antes del primer barreno.
- Width = solera, Thickness = 6.35 (1/4"), golpes relativos a la pieza, C en X = Length.
- Redondo → C{d}; ovalado E{x}X{y} con x a lo largo de la solera (orientación).
- Barreno sin herramienta / contorno no rectangular → PunchCsvError (bloquea export).
- Barras Z (sin_gap) no llevan CSV; RTZCU sí; modo forzado DXF+STEP no.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shapely.geometry import LineString, Point, Polygon, box  # noqa: E402

W6 = 152.4


def _placas() -> list[dict]:
    return [
        {
            "w": 3657.6,
            "h": W6,
            "precio": 100.0,
            "precio_lb": 1.0,
            "origen_placa": "EMPRESA",
            "material": "CU",
            "calibre": "0.25",
        }
    ]


def _pieza(nombre: str, poly) -> dict:
    return {
        "nombre": nombre,
        "poly": poly,
        "marks": None,
        "area": poly.area,
        "calibre": "0.25",
        "material": "CU",
        "ruta": f"{nombre}.dxf",
    }


def _con_barrenos(largo: float, ancho: float, barrenos: list) -> Polygon:
    poly = box(0, 0, largo, ancho)
    for b in barrenos:
        poly = poly.difference(b)
    return poly


def _slot(cx: float, cy: float, w: float, largo: float, *, eje_y: bool) -> Polygon:
    d = (largo - w) / 2.0
    seg = (
        LineString([(cx, cy - d), (cx, cy + d)])
        if eje_y
        else LineString([(cx - d, cy), (cx + d, cy)])
    )
    return seg.buffer(w / 2.0, resolution=32)


def main() -> None:
    previous_data_dir = os.environ.get("ARGA_NEST_DATA_DIR")
    with tempfile.TemporaryDirectory() as temp_dir:
        os.environ["ARGA_NEST_DATA_DIR"] = temp_dir
        os.environ.pop("ARGA_CU_FORCE_DXF_STEP", None)

        from modules.dxf_export import cu_punch_csv as pc
        from modules.nesting_engine.cu_largos_nesting import empaquetar_largos_cu
        from modules.nesting_engine.cu_punch_tooling import (
            cargar_estaciones,
            codigos_molds,
            estacion_para_barreno,
            guardar_estaciones,
        )
        from modules.nesting_engine.nest_runtime_prefs import save_nest_runtime_prefs

        # Montaje de planta (pizarrón 2026-10-01).
        assert codigos_molds() == [
            "C11.1", "C10.3", "E10.3X15.1", "E14.3X11.1",
            "E11.1X15.9", "E17.5X11.1", "E11.1X20.3", "E20.6X11.1",
        ], codigos_molds()
        assert estacion_para_barreno("C", 11.11, 11.11) == 1
        assert estacion_para_barreno("E", 11.11, 15.88) == 5
        assert estacion_para_barreno("E", 15.88, 11.11) is None, "orientación del ovalado"
        # Caso W.O. 90 X2 (GENE-FCU-4-109, ABB-22-U-BCK-72x): 17.46 a lo largo.
        assert estacion_para_barreno("E", 17.46, 11.11) == 6

        normal = _con_barrenos(
            600.0,
            W6,
            [
                Point(50.0, 30.0).buffer(11.11 / 2.0, resolution=32),
                Point(50.0, 120.0).buffer(11.11 / 2.0, resolution=32),
                _slot(300.0, 76.2, 11.11, 15.88, eje_y=True),
            ],
        )
        decimal = _con_barrenos(
            500.0, 146.05, [Point(100.0, 40.0).buffer(10.31 / 2.0, resolution=32)]
        )
        hojas, sin = empaquetar_largos_cu(
            [_pieza("GENE-FCU-6-106", normal), _pieza("GENE-DEC", decimal)],
            _placas(),
            separacion_in=0.0,
        )
        assert not sin and len(hojas) == 1, (len(hojas), sin)
        hoja = hojas[0]
        assert pc.hoja_requiere_csv_punzonado(hoja)
        assert pc.proceso_hoja_cobre(hoja).startswith("CNC BUSBAR PUNCHING")
        recorte = pc.piezas_recorte_laser(hoja)
        assert [r["nombre"] for r in recorte] == ["GENE-DEC"], recorte

        assert abs(float(hoja["cu_despunte_mm"]) - 6.0) < 0.01, "despunte visual 6 mm en el nest"
        filas = pc.construir_filas_barra(hoja, thickness_mm=6.35)
        assert len(filas) == 2, "el CSV no lleva fila de despunte"
        f1, f2 = filas

        piezas_orden = [f["Model"] for f in (f1, f2)]
        fila_n = f1 if f1["Model"] == "GENE-FCU-6-106" else f2
        fila_d = f2 if fila_n is f1 else f1
        assert set(piezas_orden) == {"GENE-FCU-6-106", "GENE-DEC"}
        assert fila_n["Width"] == "152.40" and fila_n["Thickness"] == "6.35"
        assert fila_n["Length"] == "600.00"
        golpes = [(fila_n[f"X{n}"], fila_n[f"Y{n}"], fila_n[f"M{n}"]) for n in range(1, 6)]
        # Grabado M100 primero: centro de la franja libre antes del barreno (50 - 5.555) / 2.
        assert golpes == [
            ("22.22", "0.00", "M100"),
            ("50.00", "30.00", "M1"),
            ("50.00", "120.00", "M1"),
            ("300.00", "76.20", "M5"),
            ("600.00", "0.00", "C"),
        ], golpes
        assert fila_d["Width"] == "152.40", "decimal se punzona al ancho de la solera"
        assert (fila_d["X1"], fila_d["M1"]) == ("47.42", "M100")
        assert (fila_d["X2"], fila_d["Y2"], fila_d["M2"]) == ("100.00", "40.00", "M2")
        assert (fila_d["X3"], fila_d["M3"]) == ("500.00", "C")
        assert fila_n["Mold5"] == "E11.1X15.9"
        assert all(fila_n[f"Mold{n}"] != "M100" for n in range(1, 9))

        out = os.path.join(temp_dir, "CSV", "barra.csv")
        pc.escribir_csv(out, filas)
        raw = Path(out).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        lineas = raw.decode("utf-8").split("\r\n")
        assert lineas[-1] == "" and len(lineas) == 102, len(lineas)
        assert all(len(ln.split("\t")) == 283 for ln in lineas[:-1])
        vacia = lineas[5].split("\t")
        assert vacia[0] == "" and vacia[1] == "0" and vacia[-1] == "0"

        # Ovalado 17.46 a lo largo (caso real que bloqueaba) → Mold6 sin cambios.
        ab = _con_barrenos(
            300.0, W6,
            [_slot(40.0, 30.0, 11.11, 17.46, eje_y=False), _slot(40.0, 120.0, 11.11, 17.46, eje_y=False)],
        )
        h_ab, _ = empaquetar_largos_cu([_pieza("ABB-22-U-BCK-721", ab)], _placas(), separacion_in=0.0)
        fil_ab = pc.construir_filas_barra(h_ab[0], thickness_mm=6.35)
        assert [fil_ab[0][f"M{n}"] for n in (1, 2, 3, 4)] == ["M100", "M6", "M6", "C"], fil_ab[0]
        # Franja libre antes del slot: 40 - 17.46/2 = 31.27 → grabado en X = 15.64.
        assert fil_ab[0]["X1"] == "15.64" and fil_ab[0]["Y1"] == "0.00", fil_ab[0]["X1"]
        assert h_ab[0]["cu_punch_cambios_herramental"] == []

        # Barreno fuera del inventario → bloquea, mensaje agrupado por pieza.
        raro = _con_barrenos(
            400.0, W6,
            [Point(60.0, 60.0).buffer(4.5, resolution=32), Point(160.0, 60.0).buffer(4.5, resolution=32)],
        )
        h_raro, _ = empaquetar_largos_cu([_pieza("RARO", raro)], _placas(), separacion_in=0.0)
        try:
            pc.construir_filas_barra(h_raro[0], thickness_mm=6.35)
        except pc.PunchCsvError as exc:
            assert "RARO" in str(exc) and "inventario" in str(exc) and "(x2)" in str(exc), exc
        else:
            raise AssertionError("barreno Ø9 fuera del inventario debió bloquear")

        # Ovalado 15.88 a lo largo: no montado pero sí en inventario → cambio automático.
        ov = _con_barrenos(400.0, W6, [_slot(200.0, 76.2, 11.11, 15.88, eje_y=False)])
        h_ov, _ = empaquetar_largos_cu([_pieza("OVAL-X", ov)], _placas(), separacion_in=0.0)
        fil_ov = pc.construir_filas_barra(h_ov[0], thickness_mm=6.35)
        assert fil_ov[0]["M2"] == "M8" and fil_ov[0]["Mold8"] == "E15.9X11.1", fil_ov[0]
        assert h_ov[0]["cu_punch_cambios_herramental"] == ["Mold8: E20.6X11.1 → E15.9X11.1"]
        assert pc.montaje_barra(h_ov[0])[1] == ["Mold8: E20.6X11.1 → E15.9X11.1"]
        # Sin esa herramienta en el inventario → bloquea.
        try:
            pc.construir_filas_barra(
                h_ov[0], thickness_mm=6.35, inventario=[{"tipo": "C", "x": 11.11}]
            )
        except pc.PunchCsvError as exc:
            assert "OVAL-X" in str(exc), exc
        else:
            raise AssertionError("ovalado fuera del inventario debió bloquear")
        # Montado por el usuario en el base → sin cambio.
        ests = cargar_estaciones()
        ests[7] = {"tipo": "E", "x": 15.88, "y": 11.11}
        guardar_estaciones(ests)
        fil_ov = pc.construir_filas_barra(h_ov[0], thickness_mm=6.35)
        assert fil_ov[0]["M2"] == "M8" and h_ov[0]["cu_punch_cambios_herramental"] == []

        # Grabado desactivado → sin M100; pieza sin barrenos → X por default.
        liso = box(0, 0, 300.0, W6)
        h_liso, _ = empaquetar_largos_cu([_pieza("LISO", liso)], _placas(), separacion_in=0.0)
        fil_l = pc.construir_filas_barra(h_liso[0], thickness_mm=6.35)
        assert (fil_l[0]["X1"], fil_l[0]["M1"], fil_l[0]["M2"]) == ("12.70", "M100", "C")
        fil_l = pc.construir_filas_barra(
            h_liso[0], thickness_mm=6.35, grabado={"habilitado": False}
        )
        assert (fil_l[0]["X1"], fil_l[0]["M1"]) == ("300.00", "C")

        # Más de 8 herramientas distintas en una barra → bloquea.
        from modules.nesting_engine.cu_punch_tooling import estaciones_default, montaje_para_barra

        base = estaciones_default()
        ocho = [("C", 11.11, 11.11), ("C", 10.31, 10.31), ("E", 10.31, 15.08),
                ("E", 14.30, 11.11), ("E", 11.11, 15.88), ("E", 17.47, 11.11),
                ("E", 11.11, 20.33), ("E", 20.65, 11.11)]
        _e, _c, falt = montaje_para_barra(ocho + [("E", 11.11, 12.26)], estaciones=base)
        assert any("más de 8" in f for f in falt), falt

        # Ø11.00 no montado: no se punzona con C11.1 en silencio → toma C11.0 del inventario.
        e11, c11, f11 = montaje_para_barra(
            [("C", 11.11, 11.11), ("C", 11.0, 11.0)], estaciones=base
        )
        assert not f11 and c11 == ["Mold8: E20.6X11.1 → C11.0"], c11
        assert estacion_para_barreno("C", 11.0, 11.0, e11) == 8
        assert estacion_para_barreno("C", 11.11, 11.11, e11) == 1
        # Sin estación libre: respaldo con C11.1, avisado.
        e11, c11, f11 = montaje_para_barra(ocho + [("C", 11.0, 11.0)], estaciones=base)
        assert not f11 and c11 == ["C11.0 sin estación libre: se punzona con Mold1 (C11.1)"], c11

        # Contorno con muesca → bloquea.
        muesca = Polygon([(0, 0), (400, 0), (400, W6), (20, W6), (20, W6 - 10), (0, W6 - 10)])
        hoja_m = {
            "modo_largos_cu": True,
            "cu_modo_separacion_barra": "con_gap",
            "placa_h": W6,
            "piezas": [{"nombre": "MUESCA", "poligonos": [list(muesca.exterior.coords)]}],
        }
        try:
            pc.construir_filas_barra(hoja_m, thickness_mm=6.35)
        except pc.PunchCsvError as exc:
            assert "MUESCA" in str(exc) and "rectangular" in str(exc), exc
        else:
            raise AssertionError("contorno con muesca debió bloquear")

        # MARK vertical (nest/visor) en la misma X que el M100 del CSV.
        from modules.dxf_mark.cu_mark_vertical import mark_cu_vertical_para_poly

        abb = _con_barrenos(
            489.79, 101.6,
            [_slot(25.4, 25.4, 11.11, 17.46, eje_y=False), _slot(25.4, 76.2, 11.11, 17.46, eje_y=False),
             Point(419.1, 25.4).buffer(5.155, resolution=32), Point(469.9, 25.4).buffer(5.155, resolution=32)],
        )
        mk = mark_cu_vertical_para_poly(abb, "ABB-22-U-BCK-721")
        assert not mk.is_empty
        mb = mk.bounds
        assert mb[3] - mb[1] > mb[2] - mb[0], "MARK debe ir vertical"
        assert mb[2] < 25.4 - 17.46 / 2, "MARK antes del primer barreno"
        placas4 = [dict(_placas()[0], h=101.6)]
        p_abb = dict(_pieza("ABB-22-U-BCK-721", abb), marks=mk)
        h_abb, _ = empaquetar_largos_cu([p_abb], placas4, separacion_in=0.0)
        f_abb = pc.construir_filas_barra(h_abb[0], thickness_mm=6.35)[0]
        pz = [p for p in h_abb[0]["piezas"] if p["nombre"] == "ABB-22-U-BCK-721"][0]
        x0 = min(t[0] for t in pz["poligonos"][0])
        mxs = [pt[0] - x0 for s in pz["marcas"] for pt in s]
        assert f_abb["M1"] == "M100"
        assert abs((min(mxs) + max(mxs)) / 2 - float(f_abb["X1"])) < 0.02, (mxs, f_abb["X1"])

        import ezdxf

        from interface.qt.dxf_part_loader import load_dxf_part

        doc = ezdxf.new("R2000")
        doc.header["$INSUNITS"] = 1
        msp = doc.modelspace()
        msp.add_lwpolyline([(0, 0), (10, 0), (10, 4), (0, 4)], close=True, dxfattribs={"layer": "CUT_OUTER"})
        msp.add_circle((2.0, 2.0), 0.219, dxfattribs={"layer": "CUT_INNER"})
        msp.add_line((4, 2), (7, 2), dxfattribs={"layer": "MARK"})
        ruta_v = os.path.join(temp_dir, "VISOR-CU.dxf")
        doc.saveas(ruta_v)
        model = load_dxf_part(ruta_v, 0, "VISOR-CU")
        segs = [e for e in model.msp if e.dxf.layer.upper() == "MARK"]
        vx = [v for e in segs for v in (e.dxf.start.x, e.dxf.end.x)]
        vy = [v for e in segs for v in (e.dxf.start.y, e.dxf.end.y)]
        borde_mm = (2.0 - 0.219) * 25.4
        assert abs((min(vx) + max(vx)) / 2 * 25.4 - borde_mm / 2) < 0.05, vx
        assert max(vy) - min(vy) > 1.0 and max(vx) < 2.0 - 0.219, "visor: MARK vertical antes del barreno"

        # Qué barras llevan CSV.
        assert pc.hoja_requiere_csv_punzonado(
            {"modo_largos_cu": True, "cu_modo_separacion_barra": "sin_gap"}
        ) is False
        rtz = {"modo_largos_cu": True, "cu_rtz_virtual": True, "cu_despunte_mm": 50.0,
               "placa_h": W6, "piezas": hoja["piezas"]}
        assert pc.hoja_requiere_csv_punzonado(rtz) is True
        assert pc.construir_filas_barra(rtz, thickness_mm=6.35)[0]["Model"] != "", (
            "RTZCU sin despunte"
        )
        save_nest_runtime_prefs({"cu_force_dxf_step": True})
        assert pc.hoja_requiere_csv_punzonado(hoja) is False
        save_nest_runtime_prefs({"cu_force_dxf_step": False})

    if previous_data_dir is None:
        os.environ.pop("ARGA_NEST_DATA_DIR", None)
    else:
        os.environ["ARGA_NEST_DATA_DIR"] = previous_data_dir
    print("SMOKE OK")


if __name__ == "__main__":
    main()
