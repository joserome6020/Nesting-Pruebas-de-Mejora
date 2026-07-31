"""Audit rapido 62135 2.arganest — multiplaca, RTZ, huecos vacios."""
from __future__ import annotations

import gzip
import json
import os
import sys
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from shapely.geometry import Polygon

ARG = (
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\NANS"
    r"\62135 2.arganest"
)
IN = 25.4
IN2 = IN * IN


def base(n):
    return str(n or "").split("#")[0]


def poly(p):
    rings = p.get("poligonos") or []
    if not rings:
        return None
    try:
        g = Polygon(rings[0], rings[1:] if len(rings) > 1 else None)
        return None if g.is_empty else g
    except Exception:
        return None


def hole_areas(p):
    rings = p.get("poligonos") or []
    out = []
    for i, r in enumerate(rings[1:], 1):
        try:
            h = Polygon(r)
            if not h.is_empty:
                out.append(h.area / IN2)
        except Exception:
            pass
    return out


def main():
    payload = json.loads(gzip.decompress(open(ARG, "rb").read()).decode("utf-8"))
    data = payload["resultados_multilote"][0]["data"]
    print("job", payload.get("job_activo"), "engine", data.get("_nest_engine_id"))
    ui = payload.get("ui_state") or {}
    print("kerf", ui.get("global_kerf_val"), "margin", ui.get("global_margin_val"))

    for mat, block in data.items():
        if mat.startswith("_") or not isinstance(block, dict):
            continue
        hojas = block.get("hojas") or []
        print(f"\n=== {mat} hojas={len(hojas)} eff_tanque={block.get('eficiencia_tanque_real')}")
        for i, h in enumerate(hojas):
            piezas = h.get("piezas") or []
            n = len([p for p in piezas if not str(p.get("nombre", "")).startswith("REMANENTE")])
            ef = h.get("eficiencia_real") or h.get("eficiencia")
            code = h.get("sheet_code") or h.get("placa_id")
            rtz = bool(h.get("es_retazo") or h.get("is_rtz"))
            w = float(h.get("placa_w") or 0) / IN
            hh = float(h.get("placa_h") or 0) / IN
            print(
                f"  [{i}] {code} {'RTZ' if rtz else 'MADRE'} "
                f"{w:.1f}x{hh:.1f}\" n={n} ef={float(ef or 0):.1f}%"
            )
            if rtz:
                print(f"       min_side={min(w, hh):.2f}\" (>=20? {min(w, hh) >= 20})")

            # Huecos grandes vacios (circulos / orificios)
            if not rtz and n:
                empty_big = 0
                filled = 0
                for p in piezas:
                    holes = p.get("poligonos") or []
                    if len(holes) < 2:
                        continue
                    g = poly(p)
                    if g is None:
                        continue
                    for ha in hole_areas(p):
                        if ha < 20:  # in2
                            continue
                        # Contar si hay piezas cuyo centro cae en el hole ring exterior bbox
                        # aproximacion: rings[k] as hole
                    for hi, hr in enumerate(holes[1:], 1):
                        try:
                            hole = Polygon(hr)
                        except Exception:
                            continue
                        if hole.area / IN2 < 50:
                            continue
                        nin = 0
                        for q in piezas:
                            if q is p:
                                continue
                            gq = poly(q)
                            if gq is None:
                                continue
                            if hole.contains(gq.centroid) or hole.covers(gq.centroid):
                                nin += 1
                        if nin == 0:
                            empty_big += 1
                            print(
                                f"       HOLE VACIO area={hole.area/IN2:.0f}in2 "
                                f"en {base(p.get('nombre'))}"
                            )
                        else:
                            filled += 1
                if empty_big or filled:
                    print(f"       holes_big filled={filled} empty={empty_big}")


if __name__ == "__main__":
    raise SystemExit(main())
