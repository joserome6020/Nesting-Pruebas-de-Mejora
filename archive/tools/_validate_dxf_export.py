"""
Valida exportación DXF 1:1 (job 1000 kva de prueba / CU largos).
Ejecutar: python tools/_validate_dxf_export.py
"""
from __future__ import annotations

import os
import sys
import math
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf
from ezdxf import bbox as ezb
from shapely import affinity
from shapely.geometry import Point

from interface.autodxf_metadata import combinar_metadata_dxf
from modules.nest_exporter import export_nest_to_dxf
from modules.nesting_engine.cu_inventory import inventario_barras_largos_cu
from modules.nesting_engine.cu_largos_nesting import procesar_grupo_largos_cu
from modules.nesting_engine.exporter import _clean_profile_for_production
from modules.nesting_engine.geometry_parser import recuperar_geometria_robusta
from modules.nesting_engine.manager import reconstruir_poly_seguro
from modules.nest_exporter import (
    _export_placed_geometry,
    _export_source_dxf_at_placement,
    _poly_bounds,
)
from openpyxl import load_workbook

AUTODXF = Path(
    r"//192.168.2.80/Users/Administrator/Desktop/Grupo Arga Metals/"
    r"ARGA METALS CORPORATE SYSTEM/PRODUCTO_TEST/CLIENTE_TEST/"
    r"1000 kva de prueba/MODEL CORE FILES/AutoDXF"
)
EXCL = {"processed files", "procesados", "nesting", "__pycache__"}
TOL_MM = 2.5
MAX_PIECES = 12
MAX_HOJAS = 2


def _duplicate_circles(ents, center_tol: float = 0.35, radius_tol: float = 0.35) -> int:
    """Cuenta pares de CIRCLE casi coincidentes (empalme por reconstrucción)."""
    circles = []
    for e in ents:
        if e.dxftype() != "CIRCLE":
            continue
        circles.append(
            (
                float(e.dxf.center.x),
                float(e.dxf.center.y),
                float(e.dxf.radius),
            )
        )
    dupes = 0
    for i in range(len(circles)):
        cx1, cy1, r1 = circles[i]
        for j in range(i + 1, len(circles)):
            cx2, cy2, r2 = circles[j]
            if (
                math.hypot(cx1 - cx2, cy1 - cy2) <= center_tol
                and abs(r1 - r2) <= radius_tol
            ):
                dupes += 1
    return dupes


def _corner_circle_spurious(ents, piece_bbox) -> int:
    if not piece_bbox:
        return 0
    minx, miny, maxx, maxy = piece_bbox
    corners = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
    n = 0
    for e in ents:
        if e.dxftype() != "CIRCLE":
            continue
        cx, cy = float(e.dxf.center.x), float(e.dxf.center.y)
        r = float(e.dxf.radius)
        for vx, vy in corners:
            if (cx - vx) ** 2 + (cy - vy) ** 2 < max(9.0, (r * 0.35) ** 2):
                n += 1
                break
    return n


def _load_cu_sample():
    piezas = []
    for rootd, dirs, files in os.walk(str(AUTODXF)):
        dirs[:] = [d for d in dirs if d.strip().lower() not in EXCL]
        for f in files:
            if not f.lower().endswith(".dxf"):
                continue
            ruta = os.path.join(rootd, f)
            pieza, mat, qty, cal, _ = combinar_metadata_dxf(ruta)
            m = str(mat).strip().upper()
            if m not in ("CU", "COPPER") and "COBRE" not in m:
                continue
            poly, marks = recuperar_geometria_robusta(ruta)
            if poly is None:
                continue
            minx, miny, _, _ = poly.bounds
            poly_e = affinity.translate(poly, -minx, -miny)
            marks_e = (
                affinity.translate(marks, -minx, -miny)
                if marks is not None and not getattr(marks, "is_empty", True)
                else marks
            )
            piezas.append(
                {
                    "nombre": pieza,
                    "poly": poly_e,
                    "marks": marks_e,
                    "area": float(poly_e.area),
                    "calibre": cal,
                    "material": mat,
                    "ruta": ruta,
                    "orig_minx": minx,
                    "orig_miny": miny,
                }
            )
            if len(piezas) >= MAX_PIECES:
                return piezas
    return piezas


def _load_barras():
    from modules.sheets_manager import PlatesManager

    emp, _ = PlatesManager().obtener_datos_placas_divididos()
    rows = []
    for row in emp:
        if not row or len(row) < 10:
            continue
        if str(row[1]).strip().upper() != "CU":
            continue
        w_in = float(row[3] or 0)
        h_in = float(row[4] or 0)
        rows.append(
            {
                "data": row,
                "w": w_in * 25.4,
                "h": h_in * 25.4,
                "precio": float(row[6] or 0),
                "id": str(row[2]),
                "origen": str(row[9] or "EMPRESA"),
                "precio_lb": float(row[10] or 0) if len(row) > 10 else 0.0,
            }
        )
    return inventario_barras_largos_cu(rows)


def _placement_from_pz(pz: dict) -> dict:
    nom = str(pz.get("nombre", ""))
    pols = pz.get("poligonos") or []
    outer, holes = _clean_profile_for_production(
        pols[0], pols[1:] if len(pols) > 1 else []
    )
    ruta = str(pz.get("ruta") or "").strip()
    use_source = bool(ruta) and os.path.isfile(ruta) and not nom.startswith("CU_CORTE__")
    if nom.startswith("CU_CORTE__"):
        layer, closed, cu_largos = "CUT_OUTER", False, False
    else:
        layer, closed, cu_largos = "CUT_OUTER", True, True
    return {
        "part_name": nom,
        "outer": outer,
        "holes": holes,
        "marks": pz.get("marcas", []),
        "ruta": ruta if use_source else "",
        "prefer_source_dxf": use_source,
        "compensated": False,
        "cu_largos_piece": cu_largos,
        "cu_slice_idx": int(pz.get("cu_slice_idx", 0) or 0),
        "cu_slice_count": int(pz.get("cu_slice_count", 1) or 1),
        "orig_minx": pz.get("orig_minx", 0.0),
        "orig_miny": pz.get("orig_miny", 0.0),
        "shift_x": pz.get("shift_x", 0.0),
        "shift_y": pz.get("shift_y", 0.0),
        "rot_deg": pz.get("rot_deg", 0.0),
        "rot_origin_cx": pz.get("rot_origin_cx", 0.0),
        "rot_origin_cy": pz.get("rot_origin_cy", 0.0),
        "layer_override": layer,
        "closed": closed,
    }


def _validate_piece_isolated(pz: dict) -> list[str]:
    nom = str(pz.get("nombre", ""))
    if nom.startswith("CU_CORTE__"):
        return []
    pols = pz.get("poligonos") or []
    if not pols:
        return [f"{nom}: sin polígonos"]
    poly = reconstruir_poly_seguro(pols)
    if poly is None or poly.is_empty:
        return [f"{nom}: polígono inválido"]

    pl = _placement_from_pz(pz)
    doc = ezdxf.new()
    msp = doc.modelspace()
    mode = "source"
    if not _export_source_dxf_at_placement(msp, doc, pl):
        mode = "lines"
        _export_placed_geometry(msp, pl)

    ents = list(msp)
    if not ents:
        return [f"{nom}: export vacío"]

    nbb = poly.bounds
    ext = ezb.extents(ents)
    ebb = (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)
    delta = max(abs(ebb[i] - nbb[i]) for i in range(4))
    spurious = _corner_circle_spurious(ents, nbb)
    dupes = _duplicate_circles(ents)
    errs = []
    if delta > TOL_MM:
        errs.append(f"{nom}: delta bbox {delta:.2f}mm > {TOL_MM} ({mode})")
    if spurious:
        errs.append(f"{nom}: {spurious} círculo(s) espurio(s) en esquina ({mode})")
    if dupes:
        errs.append(f"{nom}: {dupes} par(es) de círculos empalados ({mode})")
    return errs


def _validate_full_sheet(dxf_path: str, hoja: dict) -> list[str]:
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    cut_ents = [
        e
        for e in msp
        if e.dxftype() in ("LINE", "CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE")
        and str(e.dxf.layer).upper() in ("CUT_OUTER", "CUT_INNER", "CUT_CU")
    ]
    errs = []
    for pz in hoja.get("piezas") or []:
        nom = str(pz.get("nombre", ""))
        if nom.startswith("CU_CORTE__"):
            continue
        pols = pz.get("poligonos") or []
        if not pols or len(pols[0]) < 3:
            continue
        poly = reconstruir_poly_seguro(pols)
        if poly is None:
            continue
        region = poly.buffer(2.0)
        mine = []
        for e in cut_ents:
            try:
                ext = ezb.extents([e])
                cx = (ext.extmin.x + ext.extmax.x) / 2.0
                cy = (ext.extmin.y + ext.extmax.y) / 2.0
                if region.contains(Point(cx, cy)):
                    mine.append(e)
            except Exception:
                pass
        if not mine:
            errs.append(f"{nom}: sin entidades en hoja completa")
            continue
        nbb = poly.bounds
        ext = ezb.extents(mine)
        ebb = (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)
        delta = max(abs(ebb[i] - nbb[i]) for i in range(4))
        spurious = _corner_circle_spurious(mine, nbb)
        dupes = _duplicate_circles(mine)
        if delta > TOL_MM:
            errs.append(f"{nom}: hoja completa delta {delta:.2f}mm")
        if spurious:
            errs.append(f"{nom}: hoja completa {spurious} círculo(s) espurio(s)")
        if dupes:
            errs.append(f"{nom}: hoja completa {dupes} par(es) de círculos empalados")
    return errs


def main() -> int:
    print("=== VALIDACIÓN EXPORT DXF (CU largos, job prueba) ===\n")
    piezas = _load_cu_sample()
    if not piezas:
        print("FAIL: sin piezas CU en AutoDXF")
        return 1
    barras = _load_barras()
    if not barras:
        print("FAIL: sin barras CU")
        return 1

    _, resultado = procesar_grupo_largos_cu("0.25_CU", piezas, barras, wo_name="VALID")
    if resultado.get("error"):
        print(f"FAIL nesting: {resultado['error']}")
        return 1

    hojas = (resultado.get("hojas") or [])[:MAX_HOJAS]
    all_errors: list[str] = []

    print(f"Piezas: {len(piezas)} | Barras: {len(barras)} | Hojas: {len(hojas)}")
    print("\n--- Aisladas (1 pieza = 1 DXF) ---")
    tested = 0
    for hoja in hojas:
        for pz in hoja.get("piezas") or []:
            nom = str(pz.get("nombre", ""))
            if nom.startswith("CU_CORTE__"):
                continue
            errs = _validate_piece_isolated(pz)
            tested += 1
            if errs:
                all_errors.extend(errs)
                print(f"  FAIL {nom}")
                for e in errs:
                    print(f"       {e}")
            else:
                print(f"  OK   {nom}")

    print("\n--- Hoja completa (multi-pieza) ---")
    with tempfile.TemporaryDirectory() as tmp:
        for i, hoja in enumerate(hojas, start=1):
            placements = [
                _placement_from_pz(pz)
                for pz in (hoja.get("piezas") or [])
                if str(pz.get("nombre", "")).startswith("CU_CORTE__")
                or (pz.get("poligonos") or [])
            ]
            out = os.path.join(tmp, f"sheet_{i:02d}.dxf")
            sheet = {
                "length": float(hoja.get("placa_w", 0)),
                "width": float(hoja.get("placa_h", 0)),
                "material": "CU",
                "thickness": "0.25",
                "arga_code": f"VALID-H{i}",
            }
            export_nest_to_dxf(
                out,
                {**sheet, "modo_largos_cu": True},
                placements,
                title="VALIDACION",
                modo_largos_cu=True,
            )
            errs = _validate_full_sheet(out, hoja)
            n_real = sum(
                1
                for p in hoja.get("piezas") or []
                if not str(p.get("nombre", "")).startswith("CU_CORTE__")
            )
            if errs:
                all_errors.extend(errs)
                print(f"  FAIL hoja {i} ({n_real} piezas): {len(errs)} error(es)")
                for e in errs[:6]:
                    print(f"       {e}")
            else:
                print(f"  OK   hoja {i} ({n_real} piezas)")

    print(f"\n=== RESUMEN: {tested} piezas aisladas + {len(hojas)} hojas ===")
    if all_errors:
        print(f"FAIL: {len(all_errors)} problema(s)")
        return 1
    print("OK: DXF exportados 1:1 (bbox <=2.5mm, sin circulos espurios en esquinas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
