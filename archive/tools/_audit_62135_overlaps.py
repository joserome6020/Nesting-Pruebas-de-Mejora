"""Audit 62135 nest: RTZ overlays, piece overlaps, H10/H11 split."""
from __future__ import annotations

import gzip
import json
import os
import sys

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


def is_virt(n):
    s = str(n or "")
    return s.startswith(("REMANENTE", "TATUAJE", "RETAZO", "CU_CORTE"))


def poly(p):
    rings = p.get("poligonos") or []
    if not rings:
        return None
    try:
        g = Polygon(rings[0], rings[1:] if len(rings) > 1 else None)
        return None if g.is_empty else g
    except Exception:
        return None


def main():
    print("mtime", os.path.getmtime(ARG))
    payload = json.loads(gzip.decompress(open(ARG, "rb").read()).decode("utf-8"))
    data = payload["resultados_multilote"][0]["data"]
    print("engine", data.get("_nest_engine_id"), "job", payload.get("job_activo"))

    for mat in ["0.375_A 36", "0.5_A 36", "0.25_A 36", "1_A 36"]:
        block = data.get(mat) or {}
        hojas = block.get("hojas") or []
        print(
            f"\n=== {mat} n={len(hojas)} "
            f"tanque={block.get('eficiencia_tanque_real')}"
        )
        for i, h in enumerate(hojas):
            piezas = h.get("piezas") or []
            real = [p for p in piezas if not is_virt(p.get("nombre"))]
            virt = [p for p in piezas if is_virt(p.get("nombre"))]
            ef = float(h.get("eficiencia_real") or h.get("eficiencia") or 0)
            w = float(h.get("placa_w") or 0) / IN
            hh = float(h.get("placa_h") or 0) / IN
            print(
                f"  [{i}] {h.get('sheet_code') or h.get('placa_id')} "
                f"rtz={bool(h.get('es_retazo'))} ef={ef:.1f} "
                f"n={len(real)} virt={len(virt)} {w:.1f}x{hh:.1f}"
            )
            for v in virt:
                print("     VIRT", str(v.get("nombre"))[:90])

    print("\n=== OVERLAP + TATUAJE vs REAL (0.375 madres) ===")
    block = data["0.375_A 36"]
    for i, h in enumerate(block["hojas"]):
        if h.get("es_retazo"):
            continue
        reales = []
        for p in h.get("piezas") or []:
            if is_virt(p.get("nombre")):
                continue
            g = poly(p)
            if g is not None:
                reales.append((base(p.get("nombre")), g))
        ov = 0
        samples = []
        for a in range(len(reales)):
            for b in range(a + 1, len(reales)):
                inter = reales[a][1].intersection(reales[b][1])
                if inter.area > 50.0:
                    ov += 1
                    if len(samples) < 6:
                        samples.append(
                            (reales[a][0], reales[b][0], inter.area / IN2)
                        )
        tats = []
        for p in h.get("piezas") or []:
            n = str(p.get("nombre") or "")
            if n.startswith("TATUAJE__") or n.startswith("RETAZO_GUILLOTINA__"):
                g = poly(p)
                if g is not None:
                    tats.append((n, g))
        # also check RTZ label coords via marks?
        rtz_hojas = [
            x
            for x in block["hojas"]
            if x.get("es_retazo")
            and (
                abs(float(x.get("global_x") or 0) - float(h.get("placa_w") or -1)) < 1e9
            )
        ]
        print(
            f"  idx={i} {h.get('sheet_code')} overlaps={ov} "
            f"samples={samples} tats={len(tats)}"
        )
        for tn, tg in tats:
            hits = []
            for rn, rg in reales:
                if tg.intersects(rg) and tg.intersection(rg).area > 1:
                    hits.append(rn)
            print(f"    {tn[:60]} intersects_pieces={hits[:4]}")

    # Hole fill on H10
    print("\n=== H10 hole occupancy (0.5) ===")
    h10 = (data.get("0.5_A 36") or {}).get("hojas") or []
    for hi, h in enumerate(h10):
        if h.get("es_retazo"):
            continue
        piezas = [p for p in (h.get("piezas") or []) if not is_virt(p.get("nombre"))]
        empty = filled = 0
        for p in piezas:
            rings = p.get("poligonos") or []
            if len(rings) < 2:
                continue
            for hr in rings[1:]:
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
                    empty += 1
                    print(
                        f"  [{hi}] EMPTY hole {hole.area/IN2:.0f}in2 "
                        f"in {base(p.get('nombre'))}"
                    )
                else:
                    filled += 1
        print(f"  [{hi}] filled={filled} empty={empty} n={len(piezas)}")

    # Compare RTZ H9 vs madre tatuajes
    print("\n=== RTZ sheets vs global coords ===")
    for h in block["hojas"]:
        if not h.get("es_retazo"):
            continue
        print(
            f"  {h.get('placa_id')} gx={h.get('global_x')} gy={h.get('global_y')} "
            f"n={len(h.get('piezas') or [])} "
            f"w={float(h.get('placa_w') or 0)/IN:.2f} "
            f"h={float(h.get('placa_h') or 0)/IN:.2f}"
        )
        for p in h.get("piezas") or []:
            if is_virt(p.get("nombre")):
                continue
            print(f"    piece {base(p.get('nombre'))}")


if __name__ == "__main__":
    raise SystemExit(main())
