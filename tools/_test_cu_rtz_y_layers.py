"""
Prueba integración: nesteo láser normal + cobre (con_gap / sin_gap + RTZ).

Ejecutar: python tools/_test_cu_rtz_y_layers.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf
from shapely.geometry import Polygon

from modules.nest_exporter import export_nest_to_dxf
from modules.dxf_export.cobre_nest import export_cobre_hoja_to_dxf
from modules.nesting_engine.cu_largos_nesting import empaquetar_largos_cu
from modules.nesting_engine.cu_rtz_sin_gap import (
    CU_SIN_GAP_LARGO_CORTE_MAX_IN,
    asignar_rtz_cu_sin_gap_ids,
)
from modules.nesting_engine.sheet_numbering import asignar_numeracion_global_hojas
from modules.nesting_engine.sheet_integrity import validar_colocacion_completa

COBRE_ONLY = frozenset({"CUT_CU", "BAR_START", "ARGA_META"})
RTZ_OVERLAY_PREFIXES = ("RETAZO_GUILLOTINA__RTZCU", "TATUAJE__RTZCU")


def _layers(path: str) -> set[str]:
    doc = ezdxf.readfile(path)
    return {str(l.dxf.name) for l in doc.layers}


def _used_layers(path: str) -> set[str]:
    doc = ezdxf.readfile(path)
    out: set[str] = set()
    for e in doc.modelspace():
        layer = getattr(e.dxf, "layer", None)
        if layer is None:
            continue
        out.add(str(layer).strip("'\""))
    return out


def _piece(nombre: str, largo_in: float, ancho_in: float = 4.0) -> dict:
    lx = largo_in * 25.4
    wy = ancho_in * 25.4
    poly = Polygon([(0, 0), (lx, 0), (lx, wy), (0, wy)])
    return {
        "nombre": nombre,
        "poly": poly,
        "marks": None,
        "area": float(poly.area),
        "calibre": "CU",
        "material": "CU",
        "ruta": "",
    }


def _barra_stock(ancho_in: float = 4.0, largo_in: float = 144.0) -> dict:
    w = ancho_in * 25.4
    h = largo_in * 25.4
    return {
        "id": f"CU-{ancho_in}x{largo_in}",
        "w": h,
        "h": w,
        "origen": "EMPRESA",
        "precio": 100.0,
    }


def test_laser_normal_sin_capas_cobre() -> None:
    sheet = {
        "length": 1854.0,
        "width": 914.0,
        "material": "A36",
        "thickness": 0.25,
        "arga_code": "TEST-STEEL",
    }
    placements = [
        {
            "part_name": "PIEZA-A",
            "outer": [(10, 10), (110, 10), (110, 60), (10, 60)],
            "holes": [[(30, 25), (50, 25), (50, 45), (30, 45)]],
        }
    ]
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "laser.dxf")
        export_nest_to_dxf(
            out,
            sheet,
            placements,
            title="ROBOT LASER + MINI NEST",
            canal="ROBOT LASER + MINI NEST",
        )
        layers = _layers(out)
        leaked = layers & COBRE_ONLY
        assert not leaked, f"láser: capas cobre en tabla: {sorted(leaked)}"
        used = _used_layers(out)
        assert not (used & COBRE_ONLY), f"láser: geometría en capas cobre: {sorted(used & COBRE_ONLY)}"
        assert "CUT_OUTER" in used and "CUT_INNER" in used


def test_cobre_con_gap() -> None:
    sheet = {
        "length": 6000.0,
        "width": 40.0,
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "con_gap",
        "export_3d_format": "step",
    }
    placements = [
        {
            "part_name": "CU-1",
            "cu_largos_piece": True,
            "outer": [(0, 0), (100, 0), (100, 40), (0, 40)],
        }
    ]
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "cobre_gap.dxf")
        export_cobre_hoja_to_dxf(
            out,
            sheet,
            placements,
            title="CU LARGOS",
            strict=False,
        )
        layers = _layers(out)
        used = _used_layers(out)
        assert "CUT_CU" not in used, "con_gap STEP: sin capa CUT_CU"
        assert "CUT_OUTER" in used, "con_gap STEP: contorno en CUT_OUTER"
        assert "BAR_START" in layers


def test_cobre_sin_gap_rtz_nesting() -> None:
    """Piezas cortas → sin_gap; madre ≤114\"; overflow con piezas en RTZCU."""
    piezas = [_piece(f"P{i}", 8.0) for i in range(16)]
    hojas, sin = empaquetar_largos_cu(
        piezas,
        [_barra_stock()],
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    assert not sin, f"piezas sin colocar: {len(sin)}"
    assert hojas, "sin hojas generadas"

    resultados = {"0.25_CU": {"hojas": hojas}}
    asignar_numeracion_global_hojas(resultados, "W.O. TEST", sobrescribir=True)
    n_rtz = asignar_rtz_cu_sin_gap_ids(resultados)
    assert n_rtz >= 1, "debe haber al menos un RTZ de cobre"

    h = hojas[0]
    assert h.get("cu_modo_separacion_barra") == "sin_gap"
    if h.get("cu_rtz_activo"):
        assert float(h.get("cu_fin_piezas_mm") or 0) / 25.4 <= CU_SIN_GAP_LARGO_CORTE_MAX_IN + 0.05

    rtz_piezas = h.get("cu_rtz_piezas_hoja") or []
    assert h.get("cu_rtz_activo") == bool(rtz_piezas), "RTZ solo si hay piezas overflow"
    if rtz_piezas:
        assert len(rtz_piezas) >= 1
        rtz_id = str(h.get("cu_rtz_id") or "")
        assert rtz_id.startswith("RTZCU"), rtz_id

    pool = [{"nombre": f"P{i}"} for i in range(16)]
    ok, msg = validar_colocacion_completa(
        pool, [h for h in hojas if not h.get("cu_rtz_virtual")]
    )
    assert ok, f"validación inventario falló: {msg}"

    virtuales = [h for h in hojas if h.get("cu_rtz_virtual")]
    if rtz_piezas:
        assert len(virtuales) >= 1, "falta hoja RTZ (ACCESORIOS) con piezas"
        assert len(virtuales[0].get("piezas") or []) >= 1
        assert "-H" in str(virtuales[0].get("placa_id") or "")


def test_export_rtz_cu_dxf() -> None:
    """DXF aparte para tramo RTZ de cobre (nombre RTZCU{n}-H{m})."""
    inicio = 114.0 * 25.4
    pieza_rtz = {
        "nombre": "P-OVER",
        "poligonos": [
            [
                (inicio, 0.0),
                (inicio + 8.0 * 25.4, 0.0),
                (inicio + 8.0 * 25.4, 4.0 * 25.4),
                (inicio, 4.0 * 25.4),
            ]
        ],
        "area": 8.0 * 4.0 * 25.4 * 25.4,
    }
    madre = {
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "sin_gap",
        "cu_rtz_activo": True,
        "cu_rtz_id": "RTZCU1-H3",
        "cu_rtz_inicio_mm": inicio,
        "cu_rtz_fin_piezas_mm": inicio + 8.0 * 25.4,
        "cu_rtz_largo_mm": 8.0 * 25.4,
        "cu_rtz_largo_in": 8.0,
        "cu_rtz_piezas_hoja": [pieza_rtz],
        "placa_h": 4 * 25.4,
        "sheet_seq": 3,
    }
    from modules.nesting_engine.cu_rtz_sin_gap import construir_hoja_rtz_cu_virtual

    virtual = construir_hoja_rtz_cu_virtual(madre)
    assert virtual and virtual.get("cu_rtz_virtual")
    sheet = {
        "length": float(virtual["placa_w"]),
        "width": float(virtual["placa_h"]),
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "sin_gap",
        "export_3d_format": "dxf",
        "cu_rtz_virtual": True,
    }
    span = 8.0 * 25.4
    ancho = 4.0 * 25.4
    margin = 8.0
    cx, cy = span / 2.0, ancho / 2.0
    hole_r = 6.0
    placements = [
        {
            "part_name": "P-OVER",
            "cu_largos_piece": True,
            "outer": [
                (margin, margin),
                (span - margin, margin),
                (span - margin, ancho - margin),
                (margin, ancho - margin),
            ],
            "holes": [
                [
                    (cx - hole_r, cy - hole_r),
                    (cx + hole_r, cy - hole_r),
                    (cx + hole_r, cy + hole_r),
                    (cx - hole_r, cy + hole_r),
                ]
            ],
            "cu_bar_w_mm": ancho,
            "cu_bar_l_mm": span,
        }
    ]
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "rtz_cu.dxf")
        export_cobre_hoja_to_dxf(
            out,
            sheet,
            placements,
            title="RTZ CU",
            strict=False,
        )
        assert os.path.isfile(out)
        used = _used_layers(out)
        assert "Plate" not in used, "RTZ cobre: sin capa Plate de acero"
        assert "CUT_OUTER" in used or "CUT_INNER" in used
        assert "BAR_START" in used, "RTZCU: barra independiente con marcador inicio"
        doc = ezdxf.readfile(out)
        ys = []
        for e in doc.modelspace():
            if str(getattr(e.dxf, "layer", "")) != "CUT_OUTER":
                continue
            if e.dxftype() == "LINE":
                ys.extend([float(e.dxf.start.y), float(e.dxf.end.y)])
        assert ys, "sin geometría CUT_OUTER"
        ymax = max(ys)
        assert ymax <= 8.0 * 25.4 + 5.0, f"RTZ vertical debe caber en ~8\": ymax={ymax/25.4:.2f}\""


def test_cobre_sin_gap_dxf_madre_sin_rtz_ni_cut_cu() -> None:
    piezas = [_piece(f"P{i}", 8.0) for i in range(10)]
    hojas, _ = empaquetar_largos_cu(
        piezas,
        [_barra_stock()],
        separacion_in=0.375,
        largo_sin_separacion_in=10.0,
    )
    h = hojas[0]
    placements = []
    for p in h.get("piezas") or []:
        nom = str(p.get("nombre") or "")
        if p.get("cu_zona_rtz"):
            continue
        if nom.startswith("RETAZO_GUILLOTINA__") or nom.startswith("TATUAJE__"):
            if "RTZCU" in nom.upper():
                continue
        if nom.startswith("CU_CORTE__"):
            continue
        pols = p.get("poligonos") or []
        if not pols or not pols[0]:
            continue
        placements.append(
            {
                "part_name": nom,
                "cu_largos_piece": True,
                "outer": pols[0],
                "holes": pols[1:] if len(pols) > 1 else [],
                "marks": p.get("marcas") or [],
                "cu_slice_idx": int(p.get("cu_slice_idx", 0) or 0),
                "cu_slice_count": int(p.get("cu_slice_count", 1) or 1),
            }
        )

    sheet = {
        "length": float(h.get("placa_w") or 0),
        "width": float(h.get("placa_h") or 0),
        "modo_largos_cu": True,
        "cu_modo_separacion_barra": "sin_gap",
        "export_3d_format": "dxf",
    }
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "cobre_singap.dxf")
        export_cobre_hoja_to_dxf(
            out,
            sheet,
            placements,
            title="CU SIN GAP",
            strict=False,
        )
        layers = _layers(out)
        used = _used_layers(out)
        assert "CUT_CU" not in used, "sin_gap: CUT_CU no debe exportarse"
        assert "BAR_START" in used, "sin_gap: marcador inicio barra"
        for bad in RTZ_OVERLAY_PREFIXES:
            assert not any(bad in u for u in used), f"overlay en DXF: {used}"


def main() -> int:
    test_laser_normal_sin_capas_cobre()
    print("[OK] 1/5 Nesteo láser normal: sin capas cobre fantasma")

    test_cobre_con_gap()
    print("[OK] 2/5 Cobre con_gap: CUT_OUTER cerrado + BAR_START (sin CUT_CU ni divisorias)")

    test_cobre_sin_gap_rtz_nesting()
    print("[OK] 3/5 Cobre sin_gap: madre ≤114\", RTZCU con piezas overflow")

    test_export_rtz_cu_dxf()
    print("[OK] 4/5 DXF RTZ cobre: archivo separado RTZCU-Hn")

    test_cobre_sin_gap_dxf_madre_sin_rtz_ni_cut_cu()
    print("[OK] 5/5 DXF madre sin_gap: BAR_START sí; CUT_CU y overlays RTZ no")

    print("\n=== TODAS LAS PRUEBAS PASARON ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
