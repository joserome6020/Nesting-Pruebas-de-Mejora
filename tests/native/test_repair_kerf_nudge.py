#!/usr/bin/env python
"""Candado: repair kerf empuja (~0.18→0.25\") antes de expulsar; guest se mueve si cabe."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from shapely.geometry import box, Polygon

    from modules.nesting_engine.nest_poka_yoke import (
        reparar_separacion_minima_hoja,
        validar_separacion_minima_hoja,
    )
    from modules.nesting_engine.compact_lite import densificar_nido_en_placa

    # --- Exterior: gap 0.183" (caso WO62176 / Lite buffer) → nudge, 0 expulsadas ---
    gap183 = 0.183 * 25.4
    a = box(10, 10, 210, 110)
    b = box(10, 110 + gap183, 210, 210 + gap183)
    hoja_ext = {
        "placa_cal": "0.5",
        "placa_w": 3000.0,
        "placa_h": 1500.0,
        "piezas": [
            {"nombre": "P13", "poly": a, "area": float(a.area), "poligonos": [list(a.exterior.coords)]},
            {"nombre": "P32", "poly": b, "area": float(b.area), "poligonos": [list(b.exterior.coords)]},
        ],
        "kerf_usado": 0.15,
        "margin_usado": 0.15,
    }
    ok0, det0 = validar_separacion_minima_hoja(hoja_ext, 0.15, margin_in=0.15, clave="0.5_A36")
    assert ok0 is False, det0
    ok, det, expelled = reparar_separacion_minima_hoja(
        hoja_ext, 0.15, margin_in=0.15, clave="0.5_A36"
    )
    assert ok is True, (ok, det, expelled)
    assert len(expelled) == 0, f"debe empujar, no expulsar: {expelled} {det}"
    assert len(hoja_ext["piezas"]) == 2
    ok2, det2 = validar_separacion_minima_hoja(hoja_ext, 0.15, margin_in=0.15, clave="0.5_A36")
    assert ok2 is True, det2

    # --- Homónimos: 1er P13 y 1er P32 están lejos; el par real es el 2º ---
    far = box(2000, 10, 2200, 110)
    far2 = box(2000, 400, 2200, 500)
    close_a = box(10, 10, 210, 110)
    close_b = box(10, 110 + gap183, 210, 210 + gap183)
    hoja_dup = {
        "placa_cal": "0.5",
        "placa_w": 3000.0,
        "placa_h": 1500.0,
        "piezas": [
            {"nombre": "62176-1247-P13", "poly": far, "area": float(far.area)},
            {"nombre": "62176-1248-P32", "poly": far2, "area": float(far2.area)},
            {"nombre": "62176-1247-P13", "poly": close_a, "area": float(close_a.area)},
            {"nombre": "62176-1248-P32", "poly": close_b, "area": float(close_b.area)},
        ],
        "kerf_usado": 0.15,
        "margin_usado": 0.15,
    }
    ok, det, expelled = reparar_separacion_minima_hoja(
        hoja_dup, 0.15, margin_in=0.15, clave="0.5_A36"
    )
    assert ok is True, (ok, det, expelled)
    assert len(expelled) == 0, f"debe mover el par cercano, no homónimos lejanos: {det}"
    assert len(hoja_dup["piezas"]) == 4
    okd, detd = validar_separacion_minima_hoja(
        hoja_dup, 0.15, margin_in=0.15, clave="0.5_A36"
    )
    assert okd is True, detd

    # --- Cal 2 espejo: 4 copias a 0.355" (tabla 0.375") en placa grande → nudge, 0 expulsadas ---
    gap355 = 0.355 * 25.4
    kw, kh = 360.0, 300.0
    p64s = []
    for ix in range(2):
        for iy in range(2):
            x0 = 20.0 + ix * (kw + gap355)
            y0 = 20.0 + iy * (kh + gap355)
            g = box(x0, y0, x0 + kw, y0 + kh)
            p64s.append(
                {
                    "nombre": "62176-1248-P64",
                    "poly": g,
                    "area": float(g.area),
                    "poligonos": [list(g.exterior.coords)],
                }
            )
    hoja64 = {
        "placa_cal": "2",
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "piezas": p64s,
        "kerf_usado": 0.15,
        "margin_usado": 0.15,
    }
    ok64, det64, exp64 = reparar_separacion_minima_hoja(
        hoja64, 0.15, margin_in=0.15, clave="2_A 36"
    )
    assert ok64 is True, (ok64, det64, exp64)
    assert len(exp64) == 0, f"Cal 2 P64 debía abrirse a 0.375\", no expulsar: {det64}"
    assert len(hoja64["piezas"]) == 4

    # --- Cal 2 Ultra real: 2×2 PEGADO al 0.250" de placa (no hay 20 mm de holgura) ---
    # El split simétrico empujaba la P64 del rincón fuera de placa y expulsaba 3.
    margin250 = 0.250 * 25.4
    p64_edge = []
    for ix in range(2):
        for iy in range(2):
            x0 = margin250 + ix * (kw + gap355)
            y0 = margin250 + iy * (kh + gap355)
            g = box(x0, y0, x0 + kw, y0 + kh)
            p64_edge.append(
                {
                    "nombre": "62176-1248-P64",
                    "poly": g,
                    "area": float(g.area),
                    "poligonos": [list(g.exterior.coords)],
                }
            )
    hoja_edge = {
        "placa_cal": "2",
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "piezas": p64_edge,
        "kerf_usado": 0.15,
        "margin_usado": 0.15,
    }
    ok_e, det_e, exp_e = reparar_separacion_minima_hoja(
        hoja_edge, 0.15, margin_in=0.15, clave="2_A 36"
    )
    assert ok_e is True, (ok_e, det_e, exp_e)
    assert len(exp_e) == 0, f"P64 en rincón debía abrirse hacia el centro, no expulsar: {det_e}"
    assert len(hoja_edge["piezas"]) == 4
    oke2, dete2 = validar_separacion_minima_hoja(
        hoja_edge, 0.15, margin_in=0.15, clave="2_A 36"
    )
    assert oke2 is True, dete2

    # --- Columna Cal 2 trabada en alto de placa: reubicar a la derecha, 0 expulsadas ---
    kh_lock = (1219.2 - 2 * margin250 - 3 * gap355) / 4.0
    col_p64 = []
    for iy in range(4):
        y0 = margin250 + iy * (kh_lock + gap355)
        g = box(margin250, y0, margin250 + 220.0, y0 + kh_lock)
        col_p64.append(
            {
                "nombre": "62176-1248-P64",
                "poly": g,
                "area": float(g.area),
                "poligonos": [list(g.exterior.coords)],
            }
        )
    hoja_col = {
        "placa_cal": "2",
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "piezas": col_p64,
        "kerf_usado": 0.15,
        "margin_usado": 0.15,
    }
    ok_c, det_c, exp_c = reparar_separacion_minima_hoja(
        hoja_col, 0.15, margin_in=0.15, clave="2_A 36"
    )
    assert ok_c is True, (ok_c, det_c, exp_c)
    assert len(exp_c) == 0, f"columna trabada debía reubicarse en la placa, no expulsar: {det_c}"
    assert len(hoja_col["piezas"]) == 4
    okc2, detc2 = validar_separacion_minima_hoja(
        hoja_col, 0.15, margin_in=0.15, clave="2_A 36"
    )
    assert okc2 is True, detc2
    maxx_col = max(p["poly"].bounds[2] for p in hoja_col["piezas"])
    assert maxx_col < 800, f"reubicar no debe desparramar: maxx={maxx_col:.1f}"

    # --- Renest: permitir_expulsar=False no reubica / no expulsa (deja el nest) ---
    col_keep = []
    for iy in range(4):
        y0 = margin250 + iy * (kh_lock + gap355)
        g = box(margin250, y0, margin250 + 220.0, y0 + kh_lock)
        col_keep.append(
            {
                "nombre": "62176-1248-P64",
                "poly": g,
                "area": float(g.area),
                "poligonos": [list(g.exterior.coords)],
            }
        )
    xs0 = [p["poly"].bounds[0] for p in col_keep]
    hoja_keep = {
        "placa_cal": "2",
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "piezas": col_keep,
        "kerf_usado": 0.15,
        "margin_usado": 0.15,
    }
    _ok_k, _det_k, exp_k = reparar_separacion_minima_hoja(
        hoja_keep,
        0.15,
        margin_in=0.15,
        clave="2_A 36",
        permitir_expulsar=False,
    )
    assert len(exp_k) == 0, f"renest no debe expulsar: {_det_k}"
    assert len(hoja_keep["piezas"]) == 4
    xs1 = [p["poly"].bounds[0] for p in hoja_keep["piezas"]]
    assert max(abs(a - b) for a, b in zip(xs0, xs1)) < 50.0, (
        f"renest no debe saltar piezas a otro hueco: xs0={xs0} xs1={xs1}"
    )

    # --- Guest en orificio casi pegado: hay espacio → se mueve, no se expulsa ---
    outer = box(20, 20, 520, 520)
    hole = box(120, 120, 420, 420)
    host = Polygon(outer.exterior.coords, [hole.exterior.coords])
    guest = box(120.8, 220, 200, 300)  # ~0.031" de pared
    hoja_h = {
        "placa_cal": "0.375",
        "placa_w": 3000.0,
        "placa_h": 2000.0,
        "piezas": [
            {
                "nombre": "P05",
                "poly": host,
                "area": float(host.area),
                "poligonos": [list(host.exterior.coords)],
            },
            {
                "nombre": "FPP-PELSUE",
                "poly": guest,
                "area": float(guest.area),
                "poligonos": [list(guest.exterior.coords)],
            },
        ],
    }
    ok, det, expelled = reparar_separacion_minima_hoja(hoja_h, 0.25, margin_in=0.25)
    assert ok is True, (ok, det, expelled)
    assert len(expelled) == 0, f"guest debía moverse: {det}"
    assert any(p.get("nombre") == "FPP-PELSUE" for p in hoja_h["piezas"])

    # --- Sin espacio (guest casi llena el hueco legal): sí expulsa ---
    tight_hole = box(120, 120, 200, 200)
    host2 = Polygon(outer.exterior.coords, [tight_hole.exterior.coords])
    guest2 = box(121.0, 121.0, 199.0, 199.0)  # llena el orificio
    hoja_t = {
        "placa_cal": "0.375",
        "placa_w": 3000.0,
        "placa_h": 2000.0,
        "piezas": [
            {
                "nombre": "HOST",
                "poly": host2,
                "area": float(host2.area),
                "poligonos": [list(host2.exterior.coords)],
            },
            {
                "nombre": "GUEST",
                "poly": guest2,
                "area": float(guest2.area),
                "poligonos": [list(guest2.exterior.coords)],
            },
        ],
    }
    ok, det, expelled = reparar_separacion_minima_hoja(hoja_t, 0.25, margin_in=0.25)
    assert ok is True, (ok, det, expelled)
    assert any(p.get("nombre") == "GUEST" for p in expelled), expelled
    assert all(p.get("nombre") != "GUEST" for p in hoja_t["piezas"])

    # --- Galv 0.105: 6.29 mm al borde es ILEGAL (tabla 6.35 mm / 0.250").
    # El packer C++ debe colocar ≥0.250"; pokayoke AVISA, no empuja.
    sivc = box(6.29, 50.0, 206.29, 150.0)
    otra = box(400.0, 50.0, 600.0, 150.0)
    hoja_galv = {
        "placa_cal": "0.105",
        "placa_w": 3048.0,
        "placa_h": 1524.0,
        "clave": "0.105_GALVANIZADO",
        "kerf_usado": 0.15,
        "margin_usado": 0.25,
        "piezas": [
            {
                "nombre": "GENE-SIVC-40-40-136",
                "poly": sivc,
                "area": float(sivc.area),
                "poligonos": [list(sivc.exterior.coords)],
            },
            {
                "nombre": "OTRA",
                "poly": otra,
                "area": float(otra.area),
                "poligonos": [list(otra.exterior.coords)],
            },
        ],
    }
    okg0, detg0 = validar_separacion_minima_hoja(
        hoja_galv, 0.15, margin_in=0.25, clave="0.105_GALVANIZADO"
    )
    assert okg0 is False and "margen_placa" in detg0, (okg0, detg0)
    minx_antes = float(hoja_galv["piezas"][0]["poly"].bounds[0])
    okg, detg, expg = reparar_separacion_minima_hoja(
        hoja_galv, 0.15, margin_in=0.25, clave="0.105_GALVANIZADO"
    )
    assert okg is False and "margen_placa" in detg, (okg, detg, expg)
    assert len(expg) == 0, f"no expulsar por margen placa: {detg}"
    minx_despues = float(hoja_galv["piezas"][0]["poly"].bounds[0])
    assert abs(minx_despues - minx_antes) < 1e-9, (
        f"pokayoke no debe empujar: {minx_antes} → {minx_despues}"
    )

    # --- Nest desparramado (Ultra 18%): gravedad debe juntar al origen, kerf tabla ---
    margin250 = 0.250 * 25.4
    a = box(margin250, margin250, margin250 + 200, margin250 + 100)
    b = box(1800, margin250, 2000, margin250 + 100)
    c = box(margin250, 700, margin250 + 200, 800)
    hoja_sp = {
        "placa_cal": "2",
        "placa_w": 3048.0,
        "placa_h": 1219.2,
        "clave": "2_A 36",
        "kerf_usado": 0.375,
        "margin_usado": 0.25,
        "piezas": [
            {"nombre": "P64", "poly": a, "area": float(a.area), "poligonos": [list(a.exterior.coords)]},
            {"nombre": "P28", "poly": b, "area": float(b.area), "poligonos": [list(b.exterior.coords)]},
            {"nombre": "P01", "poly": c, "area": float(c.area), "poligonos": [list(c.exterior.coords)]},
        ],
    }
    st = densificar_nido_en_placa(hoja_sp, engine_id="svgnest_ultra")
    assert not st.get("reverted"), st
    maxx = max(p["poly"].bounds[2] for p in hoja_sp["piezas"])
    maxy = max(p["poly"].bounds[3] for p in hoja_sp["piezas"])
    assert maxx < 500, f"siguio desparramado en X: maxx={maxx:.1f} {st}"
    assert maxy < 350, f"siguio desparramado en Y: maxy={maxy:.1f} {st}"
    oks, dets = validar_separacion_minima_hoja(hoja_sp, 0.15, margin_in=0.15, clave="2_A 36")
    assert oks is True, dets
    minx_sp = min(p["poly"].bounds[0] for p in hoja_sp["piezas"])
    miny_sp = min(p["poly"].bounds[1] for p in hoja_sp["piezas"])
    assert minx_sp + 1e-6 >= margin250, f"gravedad bajo 0.250in: minx={minx_sp:.4f}"
    assert miny_sp + 1e-6 >= margin250, f"gravedad bajo 0.250in: miny={miny_sp:.4f}"

    print("REPAIR_KERF_NUDGE PASS", det)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
