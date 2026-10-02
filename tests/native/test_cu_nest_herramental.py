"""Candado: el nest de cobre agrupa piezas por herramental de la punzonadora.

- Piezas con el mismo juego de herramientas van juntas en la barra; las que
  piden una herramienta fuera del montaje base no contaminan las demás barras
  (antes: orden solo por largo → cada barra mezclada pedía cambio).
- Una barra nunca junta más de 8 herramientas distintas (antes el nest las
  juntaba y la exportación del CSV se bloqueaba con "más de 8").
- Barras con el mismo cambio de herramental quedan seguidas; las del montaje
  base primero.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shapely.geometry import LineString, Point, box  # noqa: E402

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


def _redondo(cx: float, cy: float, d: float):
    return Point(cx, cy).buffer(d / 2.0, resolution=32)


def _slot(cx: float, cy: float, a_lo_largo: float, a_lo_ancho: float):
    w = min(a_lo_largo, a_lo_ancho)
    d = (max(a_lo_largo, a_lo_ancho) - w) / 2.0
    if a_lo_largo >= a_lo_ancho:
        seg = LineString([(cx - d, cy), (cx + d, cy)])
    else:
        seg = LineString([(cx, cy - d), (cx, cy + d)])
    return seg.buffer(w / 2.0, resolution=32)


def _con(largo: float, huecos: list):
    poly = box(0, 0, largo, W6)
    for h in huecos:
        poly = poly.difference(h)
    return poly


def _nombres_por_barra(hojas: list[dict]) -> list[list[str]]:
    from modules.dxf_export.cu_punch_csv import _piezas_reales

    return [[str(p["nombre"]) for p in _piezas_reales(h)] for h in hojas]


def main() -> None:
    previous_data_dir = os.environ.get("ARGA_NEST_DATA_DIR")
    with tempfile.TemporaryDirectory() as temp_dir:
        os.environ["ARGA_NEST_DATA_DIR"] = temp_dir
        os.environ.pop("ARGA_CU_FORCE_DXF_STEP", None)

        from modules.dxf_export import cu_punch_csv as pc
        from modules.nesting_engine.cu_largos_nesting import (
            empaquetar_largos_cu,
            ordenar_hojas_largos_cu_por_ancho,
        )
        from modules.nesting_engine.nest_runtime_prefs import save_nest_runtime_prefs

        save_nest_runtime_prefs({"cu_force_dxf_step": False, "cu_ancho_modo": "exacto"})

        # A: solo C11.1 (montaje base). B: Ø11.00 → C11.0 del inventario (cambio).
        # Entrada alternada A, B, A, B… con el mismo largo: el orden por largo
        # dejaba ambas barras mezcladas y las dos pedían cambio de herramental.
        piezas = []
        for i in range(4):
            piezas.append(_pieza(f"A{i}", _con(800.0, [_redondo(60.0, 40.0, 11.11)])))
            piezas.append(_pieza(f"B{i}", _con(800.0, [_redondo(60.0, 40.0, 11.0)])))
        hojas, sin = empaquetar_largos_cu(piezas, _placas(), separacion_in=0.0)
        assert not sin and len(hojas) == 2, (len(hojas), sin)
        barras = [sorted(n[0] for n in b) for b in _nombres_por_barra(hojas)]
        assert sorted(barras) == [["A"] * 4, ["B"] * 4], _nombres_por_barra(hojas)
        cambios = sorted(pc.montaje_barra(h)[1] for h in hojas)
        assert cambios == [[], ["M8: quitar E20.6X11.1, poner C11.0"]], cambios

        # Barras ordenadas: primero la del montaje base, luego la del cambio.
        orden = ordenar_hojas_largos_cu_por_ancho(list(reversed(hojas)))
        assert [pc.montaje_barra(h)[1] for h in orden][0] == [], "montaje base primero"

        # P8 usa las 8 estaciones del montaje; Q pide C11.0 + ovalado 12.26.
        # Juntas serían 10 herramientas: el nest las separa (antes bloqueaba el CSV).
        p8 = _con(
            900.0,
            [
                _redondo(60.0, 30.0, 11.11),
                _redondo(60.0, 120.0, 10.31),
                _slot(150.0, 40.0, 10.31, 15.08),
                _slot(250.0, 40.0, 14.30, 11.11),
                _slot(350.0, 40.0, 11.11, 15.88),
                _slot(450.0, 40.0, 17.47, 11.11),
                _slot(550.0, 40.0, 11.11, 20.33),
                _slot(650.0, 40.0, 20.65, 11.11),
            ],
        )
        q = _con(600.0, [_redondo(60.0, 40.0, 11.0), _slot(300.0, 76.2, 11.11, 12.26)])
        hojas2, sin2 = empaquetar_largos_cu(
            [_pieza("P8", p8), _pieza("Q", q)], _placas(), separacion_in=0.0
        )
        assert not sin2 and len(hojas2) == 2, _nombres_por_barra(hojas2)
        for h in hojas2:
            filas = pc.construir_filas_barra(h, thickness_mm=6.35)
            assert filas, "cada barra debe exportar su CSV sin bloquear"

        # Barra mezclada (sin cambio): el PDF marca piezas con distinto herramental.
        mix = [
            _pieza("M1X", _con(700.0, [_redondo(60.0, 40.0, 11.11)])),
            _pieza("M5X", _con(700.0, [_slot(60.0, 76.2, 11.11, 15.88)])),
        ]
        hojas3, _ = empaquetar_largos_cu(mix, _placas(), separacion_in=0.0)
        assert len(hojas3) == 1
        res = pc.resumen_herramental_barra(hojas3[0])
        assert res["por_pieza"] == {"M1X": "M1=C11.1", "M5X": "M5=E11.1X15.9"}, res
        assert res["mixta"] and res["cambios"] == []

        # PDF: tabla general pieza → barrenos → estación/herramienta.
        import reporte_pdf_nesting as rp

        # Una tabla por tipo de pieza: encabezado (piezas, barras, plano) + fila por barreno.
        placa_mix = {
            "id": "W.O. 1 X1-H3", "piezas": hojas3[0]["piezas"],
            "cu_herr_detalle": res["detalle"], "cu_herr_catalogo": res["catalogo"],
        }
        tablas = rp._cu_herramental_piezas([placa_mix])
        assert [t["nombre"] for t in tablas] == ["M1X", "M5X"], tablas
        assert tablas[0]["piezas"] == 1 and tablas[0]["barras"] == "H3" and tablas[0]["plano"] == "Analizador"
        assert tablas[0]["filas"] == [["Redondo", "Ø11.11", "-", "1", "M1", "C11.1"]], tablas[0]
        assert tablas[1]["filas"] == [["Ovalado", "15.88 x 11.11", "A lo ancho", "1", "M5", "E11.1X15.9"]], tablas[1]
        # Hoja de la barra: apartado compacto bajo «Proceso», piezas agrupadas por juego de herramental.
        reales = [p for p in hojas3[0]["piezas"] if rp._is_real_piece(p)]
        rows_h, _ids = rp._build_group_summary(reales, herr_por_pieza=res["por_pieza"])
        assert rp._cu_herr_grupos_hoja(rows_h) == [("M1=C11.1", ["(1)"]), ("M5=E11.1X15.9", ["(2)"])]
        rows_eq = [
            {"displayId": "(1)", "herramental": "M1=C11.1 M6=E17.5X11.1"},
            {"displayId": "(2)", "herramental": "M1=C11.1"},
            {"displayId": "(3)", "herramental": "M1=C11.1 M6=E17.5X11.1"},
        ]
        assert rp._cu_herr_grupos_hoja(rows_eq) == [
            ("M1=C11.1 M6=E17.5X11.1", ["(1)", "(3)"]), ("M1=C11.1", ["(2)"])
        ], "piezas con el mismo juego comparten línea (no satura la hoja)"
        # W.O. 92: placa "SCO014 P<n>", hoja "W.O. 92 X1-H<n>" → barras en rangos de hoja.
        placas_wo92 = [
            {"id": f"SCO014 P{n}", "sheet_code": f"W.O. 92 X1-H{n}", "piezas": hojas3[0]["piezas"],
             "cu_herr_detalle": res["detalle"], "cu_herr_catalogo": res["catalogo"]}
            for n in list(range(1, 55)) + [60]
        ]
        t92 = rp._cu_herramental_piezas(placas_wo92)
        assert t92[0]["barras"] == "H1-H54, H60" and t92[0]["piezas"] == 55, t92[0]

    if previous_data_dir is None:
        os.environ.pop("ARGA_NEST_DATA_DIR", None)
    else:
        os.environ["ARGA_NEST_DATA_DIR"] = previous_data_dir
    print("SMOKE OK")


if __name__ == "__main__":
    main()
