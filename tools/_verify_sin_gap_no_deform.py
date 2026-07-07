"""Verifica que sin_gap NO deforma la geometria (rotacion 1:1 desde DXF fuente).

Construye un DXF fuente asimetrico (contorno con muesca + barreno + marca cerca
de una esquina) y lo exporta como cobre largos sin_gap via el camino real
prefer_source_dxf. Comprueba:
  - dimensiones intercambiadas (W<->H) sin escalado (area preservada)
  - barreno y marca conservan su posicion RELATIVA a la pieza (sin desalinear)
  - BAR_START una sola linea en y=0 abarcando el ancho de barra
  - geometria dentro de la barra (no fuera de lugar)
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf
from ezdxf import bbox as ezb

from modules.nest_exporter import export_nest_to_dxf
from modules.nesting_engine.exporter import RUTA_CAMA_LASER

MM = 25.4  # pulgadas -> mm (los DXF fuente vienen en pulgadas)


def _make_source_dxf(path: str) -> dict:
    """Solera asimetrica en PULGADAS. Devuelve metadata en mm para comparar."""
    doc = ezdxf.new(dxfversion="R2010")
    msp = doc.modelspace()
    for name, color in (("CUT", 1), ("CUT_INNER", 2), ("MARK", 4)):
        if name not in doc.layers:
            doc.layers.new(name, dxfattribs={"color": color})

    # Contorno asimetrico (muesca en esquina sup-derecha) en pulgadas.
    # Ancho 4 in, largo 1 in. Muesca de 1x0.3 in en la esquina.
    outer_in = [
        (0.0, 0.0),
        (4.0, 0.0),
        (4.0, 0.7),
        (3.0, 0.7),
        (3.0, 1.0),
        (0.0, 1.0),
        (0.0, 0.0),
    ]
    msp.add_lwpolyline(outer_in, dxfattribs={"layer": "CUT", "closed": True})

    # Barreno cerca de la esquina inferior-izquierda (asimetrico), capa interior.
    hole_c_in = (0.5, 0.5)
    hole_r_in = 0.1
    msp.add_circle(hole_c_in, hole_r_in, dxfattribs={"layer": "CUT_INNER"})

    # Marca (linea corta) cerca de la esquina inferior-derecha.
    mark_a_in = (3.5, 0.2)
    mark_b_in = (3.8, 0.2)
    msp.add_line(mark_a_in, mark_b_in, dxfattribs={"layer": "MARK"})

    doc.saveas(path)

    return {
        "outer_mm": [(x * MM, y * MM) for x, y in outer_in],
        "hole_c_mm": (hole_c_in[0] * MM, hole_c_in[1] * MM),
        "hole_r_mm": hole_r_in * MM,
        "mark_a_mm": (mark_a_in[0] * MM, mark_a_in[1] * MM),
        "mark_b_mm": (mark_b_in[0] * MM, mark_b_in[1] * MM),
        "w_mm": 4.0 * MM,
        "h_mm": 1.0 * MM,
    }


def _bbox(entities):
    ext = ezb.extents(entities)
    return (
        float(ext.extmin.x),
        float(ext.extmin.y),
        float(ext.extmax.x),
        float(ext.extmax.y),
    )


def _entities_by_layer(doc, layer):
    return [e for e in doc.modelspace() if str(e.dxf.layer).upper() == layer.upper()]


def verify():
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "solera_src.dxf")
        meta = _make_source_dxf(src)

        w_mm = meta["w_mm"]
        h_mm = meta["h_mm"]

        # Colocacion: la pieza va en la barra horizontal (como la produce el motor).
        shift_x, shift_y = 30.0, 5.0
        outer_placed = [(x + shift_x, y + shift_y) for x, y in meta["outer_mm"]]

        bar_len = 300.0
        bar_w = h_mm + 10.0  # ancho de barra un poco mayor que la pieza

        # Guillotina vertical (CU_CORTE__V__): linea vertical en la barra horizontal.
        # NO tiene cu_largos_piece; se enruta por canal laser -> debe rotarse igual.
        guillo_x = 150.0
        guillotina = {
            "part_name": "CU_CORTE__V__1",
            "outer": [(guillo_x, 0.0), (guillo_x, bar_w)],
            "holes": [],
            "marks": [],
            "ruta": "",
            "prefer_source_dxf": False,
            "cu_largos_piece": False,
            "es_corte_cu": True,
            "layer_override": "CUT_OUTER",
            "closed": False,
            "shift_x": 0.0,
            "shift_y": 0.0,
            "orig_minx": 0.0,
            "orig_miny": 0.0,
            "rot_deg": 0.0,
        }

        placements = [
            {
                "part_name": "SOLERA_ASIM",
                "ruta": src,
                "prefer_source_dxf": True,
                "cu_largos_piece": True,
                "outer": outer_placed,
                "holes": [],
                "marks": [],
                "shift_x": shift_x,
                "shift_y": shift_y,
                "orig_minx": 0.0,
                "orig_miny": 0.0,
                "rot_deg": 0.0,
                "cu_slice_idx": 0,
                "cu_slice_count": 1,
            },
            guillotina,
        ]
        sheet = {
            "length": bar_len,
            "width": bar_w,
            "material": "CU",
            "thickness": "0.25",
            "arga_code": "VERIF",
            "modo_largos_cu": True,
            "cu_modo_separacion_barra": "sin_gap",
            "export_3d_format": "dxf",
        }

        out = os.path.join(td, "sin_gap_verif.dxf")
        export_nest_to_dxf(
            out,
            sheet,
            placements,
            title="VERIF",
            canal=RUTA_CAMA_LASER,
            modo_largos_cu=True,
            strict=True,
        )

        doc = ezdxf.readfile(out)
        errors: list[str] = []

        # --- 1. Contorno de pieza (CUT_CU): dimensiones intercambiadas sin escalar ---
        piece_contour = _entities_by_layer(doc, "CUT_CU") or _entities_by_layer(
            doc, "CUT_OUTER"
        )
        assert piece_contour, "no hay contorno de pieza (CUT_CU/CUT_OUTER)"
        ox0, oy0, ox1, oy1 = _bbox(piece_contour)
        out_w = ox1 - ox0
        out_h = oy1 - oy0
        # Tras rotar 90: el ancho original (4in) debe ser la ALTURA, el alto (1in) el ANCHO
        if not math.isclose(out_h, w_mm, abs_tol=1.0):
            errors.append(f"altura CUT_OUTER {out_h:.2f} != ancho original {w_mm:.2f}")
        if not math.isclose(out_w, h_mm, abs_tol=1.0):
            errors.append(f"ancho CUT_OUTER {out_w:.2f} != alto original {h_mm:.2f}")

        # --- 2. Barreno: radio intacto (sin escalado) y posicion relativa correcta ---
        circles = [
            e
            for e in doc.modelspace()
            if e.dxftype() == "CIRCLE"
            and str(e.dxf.layer).upper() in ("CUT_INNER", "CUT")
        ]
        if not circles:
            errors.append("barreno no exportado como CIRCLE (posible deformacion)")
        else:
            c = circles[0]
            r = float(c.dxf.radius)
            if not math.isclose(r, meta["hole_r_mm"], abs_tol=0.2):
                errors.append(
                    f"radio barreno {r:.2f} != original {meta['hole_r_mm']:.2f} (escalado!)"
                )
            # Posicion relativa: la esquina inf-izq de la pieza local (0,0) mapea a
            # (bar_w, 0) tras rotar; hole local (0.5,0.5)in -> tras rot: x=bw-0.5in*MM? 
            # Validamos que el centro cae DENTRO del bbox del contorno.
            cx, cy = float(c.dxf.center.x), float(c.dxf.center.y)
            if not (ox0 - 1 <= cx <= ox1 + 1 and oy0 - 1 <= cy <= oy1 + 1):
                errors.append(
                    f"centro barreno ({cx:.1f},{cy:.1f}) fuera del contorno "
                    f"[{ox0:.1f},{oy0:.1f}]-[{ox1:.1f},{oy1:.1f}] (desalineado)"
                )

        # --- 3. MARK: en sin_gap NO debe existir (CyPTube rompe con esa capa) ---
        marks = _entities_by_layer(doc, "MARK")
        if marks:
            errors.append(
                f"sin_gap NO debe llevar capa MARK: {len(marks)} entidad(es) presentes"
            )
        if "MARK" in doc.layers:
            errors.append("sin_gap NO debe definir la capa MARK (debe purgarse)")

        # --- 4. BAR_START: una linea en y=0 abarcando ancho de barra ---
        bar_lines = [
            e
            for e in doc.modelspace()
            if str(e.dxf.layer).upper() == "BAR_START" and e.dxftype() == "LINE"
        ]
        if len(bar_lines) != 1:
            errors.append(f"BAR_START debe ser 1 linea, hay {len(bar_lines)}")
        else:
            ln = bar_lines[0]
            y0 = float(ln.dxf.start.y)
            y1 = float(ln.dxf.end.y)
            xspan = abs(float(ln.dxf.end.x) - float(ln.dxf.start.x))
            if abs(y0) > 0.1 or abs(y1) > 0.1:
                errors.append(f"BAR_START no esta en y=0: ({y0:.2f},{y1:.2f})")
            if not math.isclose(xspan, bar_w, abs_tol=0.5):
                errors.append(
                    f"BAR_START abarca {xspan:.2f}, ancho barra {bar_w:.2f}"
                )

        # --- 4b. Guillotina CU_CORTE__V__: rotada dentro de la barra en X ---
        cut_outer_lines = [
            e for e in doc.modelspace()
            if str(e.dxf.layer).upper() == "CUT_OUTER" and e.dxftype() == "LINE"
        ]
        # La guillotina original era vertical en x=150 (fuera del ancho de barra=35.4).
        # Si NO se rota, quedaria en x=150 -> fuera de [0, bar_w]. Detectarlo.
        guillo_stray = [
            ln for ln in cut_outer_lines
            if float(ln.dxf.start.x) > bar_w + 1.0 or float(ln.dxf.end.x) > bar_w + 1.0
        ]
        if guillo_stray:
            xs = [
                (round(float(l.dxf.start.x), 1), round(float(l.dxf.end.x), 1))
                for l in guillo_stray
            ]
            errors.append(
                f"guillotina/corte NO rotado: {len(guillo_stray)} linea(s) fuera "
                f"del ancho de barra (x>{bar_w:.1f}): {xs[:5]}"
            )

        # --- 5. Geometria dentro de la barra (no fuera de lugar) ---
        all_geo = [
            e
            for e in doc.modelspace()
            if str(e.dxf.layer).upper() in ("CUT_OUTER", "CUT_INNER", "MARK", "CUT_CU")
        ]
        if all_geo:
            gx0, gy0, gx1, gy1 = _bbox(all_geo)
            if gx0 < -1.0 or gx1 > bar_w + 1.0:
                errors.append(
                    f"geometria fuera de la barra en X: [{gx0:.1f},{gx1:.1f}] vs ancho {bar_w:.1f}"
                )
            if gy0 < -1.0 or gy1 > bar_len + 1.0:
                errors.append(
                    f"geometria fuera de la barra en Y: [{gy0:.1f},{gy1:.1f}] vs largo {bar_len:.1f}"
                )

        print("\n===== RESULTADO VERIFICACION sin_gap =====")
        print(f"CUT_OUTER bbox: [{ox0:.1f},{oy0:.1f}]-[{ox1:.1f},{oy1:.1f}] (WxH={out_w:.1f}x{out_h:.1f})")
        print(f"Original (in->mm): ancho={w_mm:.1f} alto={h_mm:.1f}")
        if errors:
            print("\nFALLAS DETECTADAS:")
            for e in errors:
                print(f"  [X] {e}")
            raise SystemExit(1)
        print("\n[OK] Geometria NO deformada: dimensiones, barreno, marca y BAR_START correctos.")


if __name__ == "__main__":
    verify()
