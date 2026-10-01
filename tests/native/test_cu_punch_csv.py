"""Candado: CSV CNC Busbar Punching (Lijian MX602K / LJcad) por barra de cobre.

- Una fila por pieza en orden del nest; primera fila = despunte 50 mm (Model vacío, solo C).
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

        assert codigos_molds() == [
            "C11.1", "C10.3", "C11.0", "E11.1X20.6",
            "E11.1X17.5", "E11.1X15.9", "E11.1X14.3", "E10.3X15.1",
        ], codigos_molds()
        assert estacion_para_barreno("C", 11.11, 11.11) == 1
        assert estacion_para_barreno("E", 11.11, 15.88) == 6
        assert estacion_para_barreno("E", 15.88, 11.11) is None, "orientación del ovalado"

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

        filas = pc.construir_filas_barra(hoja, thickness_mm=6.35)
        assert len(filas) == 3, len(filas)
        desp, f1, f2 = filas
        assert desp["Model"] == "" and desp["Quantity"] == "1"
        assert desp["Length"] == "50.00" and desp["X1"] == "50.00" and desp["M1"] == "C"
        assert desp["X2"] == "0.00" and desp["M2"] == ""

        piezas_orden = [f["Model"] for f in (f1, f2)]
        fila_n = f1 if f1["Model"] == "GENE-FCU-6-106" else f2
        fila_d = f2 if fila_n is f1 else f1
        assert set(piezas_orden) == {"GENE-FCU-6-106", "GENE-DEC"}
        assert fila_n["Width"] == "152.40" and fila_n["Thickness"] == "6.35"
        assert fila_n["Length"] == "600.00"
        golpes = [(fila_n[f"X{n}"], fila_n[f"Y{n}"], fila_n[f"M{n}"]) for n in range(1, 5)]
        assert golpes == [
            ("50.00", "30.00", "M1"),
            ("50.00", "120.00", "M1"),
            ("300.00", "76.20", "M6"),
            ("600.00", "0.00", "C"),
        ], golpes
        assert fila_d["Width"] == "152.40", "decimal se punzona al ancho de la solera"
        assert (fila_d["X1"], fila_d["Y1"], fila_d["M1"]) == ("100.00", "40.00", "M2")
        assert (fila_d["X2"], fila_d["M2"]) == ("500.00", "C")
        assert fila_n["Mold6"] == "E11.1X15.9"

        out = os.path.join(temp_dir, "CSV", "barra.csv")
        pc.escribir_csv(out, filas)
        raw = Path(out).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        lineas = raw.decode("utf-8").split("\r\n")
        assert lineas[-1] == "" and len(lineas) == 102, len(lineas)
        assert all(len(ln.split("\t")) == 283 for ln in lineas[:-1])
        vacia = lineas[5].split("\t")
        assert vacia[0] == "" and vacia[1] == "0" and vacia[-1] == "0"

        # Barreno sin herramienta → bloquea.
        raro = _con_barrenos(400.0, W6, [Point(60.0, 60.0).buffer(4.5, resolution=32)])
        h_raro, _ = empaquetar_largos_cu([_pieza("RARO", raro)], _placas(), separacion_in=0.0)
        try:
            pc.construir_filas_barra(h_raro[0], thickness_mm=6.35)
        except pc.PunchCsvError as exc:
            assert "RARO" in str(exc) and "sin herramienta" in str(exc), exc
        else:
            raise AssertionError("barreno Ø9 sin herramienta debió bloquear")

        # Ovalado a lo largo (E15.9X11.1) no montado → bloquea.
        ov = _con_barrenos(400.0, W6, [_slot(200.0, 76.2, 11.11, 15.88, eje_y=False)])
        h_ov, _ = empaquetar_largos_cu([_pieza("OVAL-X", ov)], _placas(), separacion_in=0.0)
        try:
            pc.construir_filas_barra(h_ov[0], thickness_mm=6.35)
        except pc.PunchCsvError as exc:
            assert "OVAL-X" in str(exc), exc
        else:
            raise AssertionError("ovalado a lo largo sin herramienta debió bloquear")
        ests = cargar_estaciones()
        ests[7] = {"tipo": "E", "x": 15.88, "y": 11.11}
        guardar_estaciones(ests)
        fil_ov = pc.construir_filas_barra(h_ov[0], thickness_mm=6.35)
        assert fil_ov[1]["M1"] == "M8" and fil_ov[1]["Mold8"] == "E15.9X11.1"

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
