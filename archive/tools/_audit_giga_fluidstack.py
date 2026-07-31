"""Analiza GIGA FLUIDSTACK.arganest: eficiencia, cavidades VFM, gaps vs kerf, re-nest offline."""
from __future__ import annotations

import gzip
import json
import os
import sys
from collections import Counter, defaultdict
from copy import deepcopy

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from shapely.geometry import Polygon, box

from modules.nesting_engine.sim_lab import (
    piezas_pack_desde_hoja,
    run_plate_sim,
)

ARGANEST = (
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\NANS"
    r"\GIGA FLUIDSTACK.arganest"
)
OUT = os.path.join(_ROOT, "_logs", "giga_fluidstack_audit.json")
IN = 25.4
IN2 = IN * IN


def load_payload():
    raw = open(ARGANEST, "rb").read()
    return json.loads(gzip.decompress(raw).decode("utf-8"))


def poly_of(p: dict) -> Polygon | None:
    rings = p.get("poligonos") or []
    if not rings:
        return None
    try:
        poly = Polygon(rings[0], rings[1:] if len(rings) > 1 else None)
        if poly.is_empty:
            return None
        return poly
    except Exception:
        return None


def base_name(n: str) -> str:
    return str(n or "").split("#")[0]


def open_cavs(host: Polygon, min_in2: float = 5.0):
    free = box(*host.bounds).difference(host)
    geoms = [free] if free.geom_type == "Polygon" else list(getattr(free, "geoms", []) or [])
    out = []
    for g in geoms:
        if g.is_empty or g.area / IN2 < min_in2:
            continue
        if g.area > host.envelope.area * 0.85:
            continue
        minx, miny, maxx, maxy = g.bounds
        w, h = (maxx - minx) / IN, (maxy - miny) / IN
        out.append({"g": g, "area": g.area / IN2, "w": w, "h": h})
    return sorted(out, key=lambda d: -d["area"])


def sheet_gap_audit(piezas: list, kerf_in: float) -> dict:
    polys = []
    for p in piezas:
        poly = poly_of(p)
        if poly is None:
            continue
        polys.append((base_name(p.get("nombre")), poly))
    min_gap = 1e9
    violations = 0
    worst = []
    min_req = kerf_in * 0.92 * IN
    for i in range(len(polys)):
        ni, pi = polys[i]
        for j in range(i + 1, len(polys)):
            nj, pj = polys[j]
            if pi.intersects(pj) and not pi.touches(pj):
                inter = pi.intersection(pj)
                if inter.area > 1.0:
                    violations += 1
                    worst.append((ni, nj, "OVERLAP", inter.area))
                    continue
            gap = pi.distance(pj)
            if gap < min_gap:
                min_gap = gap
            if gap + 1e-6 < min_req:
                violations += 1
                if len(worst) < 8:
                    worst.append((ni, nj, "GAP", round(gap / IN, 4)))
    return {
        "n": len(polys),
        "min_gap_in": None if min_gap > 1e8 else round(min_gap / IN, 4),
        "violations": violations,
        "worst": worst,
    }


def cavity_fill_stats(piezas: list) -> dict:
    hosts = []
    smalls = []
    for p in piezas:
        poly = poly_of(p)
        if poly is None:
            continue
        nom = base_name(p.get("nombre"))
        if "VFM" in nom or "HFM" in nom:
            hosts.append({"nom": nom, "poly": poly})
        else:
            smalls.append({"nom": nom, "c": poly.centroid, "poly": poly})
    per = []
    in_cav = 0
    assigned = set()
    for h in hosts:
        cavs = open_cavs(h["poly"])
        n = 0
        kinds = Counter()
        for cav in cavs:
            for si, s in enumerate(smalls):
                if si in assigned:
                    continue
                if cav["g"].contains(s["c"]):
                    assigned.add(si)
                    kinds[s["nom"]] += 1
                    n += 1
        in_cav += n
        per.append(
            {
                "host": h["nom"],
                "n_cav": len(cavs),
                "cav_area_in2": round(sum(c["area"] for c in cavs), 1),
                "filled": n,
                "kinds": dict(kinds),
                "cav_dims": [
                    {"w": round(c["w"], 2), "h": round(c["h"], 2), "a": round(c["area"], 1)}
                    for c in cavs[:6]
                ],
            }
        )
    return {"n_hosts": len(hosts), "in_cavity": in_cav, "per_host": per}


def piece_to_sim(p: dict) -> dict:
    """Formato PieceIn / sim pieza desde colocado en hoja."""
    return {
        "nombre": base_name(p.get("nombre")),
        "poligonos": deepcopy(p.get("poligonos") or []),
        "marcas": deepcopy(p.get("marcas") or []),
        "area": float(p.get("area") or 0.0),
        "calibre": p.get("calibre"),
        "material": p.get("material"),
        "cantidad": 1,
    }


def main() -> int:
    payload = load_payload()
    ui = payload.get("ui_state") or {}
    kerf = float(ui.get("global_kerf_val") or 0.3)
    margin = float(ui.get("global_margin_val") or 0.15)
    data = payload["resultados_multilote"][0]["data"]
    report = {
        "job": payload.get("job_activo"),
        "kerf": kerf,
        "margin": margin,
        "engine_saved": data.get("_nest_engine_id"),
        "materials": {},
        "focus_renest": [],
    }

    print(f"JOB={report['job']} kerf={kerf} margin={margin} engine={report['engine_saved']}")

    for mat, block in data.items():
        if mat.startswith("_") or not isinstance(block, dict):
            continue
        hojas = block.get("hojas") or []
        mat_sum = {
            "n_hojas": len(hojas),
            "eff_real": block.get("eficiencia_tanque_real"),
            "eff_dir": block.get("eficiencia_tanque_directa"),
            "placa": block.get("placa"),
            "dim": block.get("dim"),
            "sheets": [],
        }
        print(f"\n=== {mat} hojas={len(hojas)} eff_real={mat_sum['eff_real']} dim={block.get('dim')}")
        for hi, h in enumerate(hojas):
            piezas = h.get("piezas") or []
            names = Counter(base_name(p.get("nombre")) for p in piezas)
            has_vfm = any("VFM" in n for n in names)
            gaps = sheet_gap_audit(piezas, kerf)
            cav = cavity_fill_stats(piezas) if has_vfm else None
            row = {
                "i": hi,
                "code": h.get("sheet_code"),
                "eff": h.get("eficiencia"),
                "eff_real": h.get("eficiencia_real"),
                "eff_dir": h.get("eficiencia_directa"),
                "kerf_usado": h.get("kerf_usado"),
                "w": h.get("placa_w"),
                "h": h.get("placa_h"),
                "n_piezas": len(piezas),
                "mix": dict(names),
                "gaps": gaps,
                "cavity": cav,
            }
            mat_sum["sheets"].append(row)
            flag = ""
            if gaps["violations"]:
                flag += " KERF_FAIL"
            if has_vfm:
                flag += f" VFM_fill={cav['in_cavity']}"
            print(
                f"  [{hi}] {h.get('sheet_code')} eff={h.get('eficiencia_real') or h.get('eficiencia')} "
                f"n={len(piezas)} min_gap={gaps['min_gap_in']} viol={gaps['violations']}{flag}"
            )
            if has_vfm and cav:
                for ph in cav["per_host"]:
                    print(
                        f"      {ph['host']}: filled={ph['filled']} cavs={ph['n_cav']} "
                        f"area={ph['cav_area_in2']} kinds={ph['kinds']}"
                    )
        report["materials"][mat] = mat_sum

    # Re-nest peores hojas VFM (0.0747 principal) con motor actual
    focus_mat = "0.0747_A 36"
    block = data.get(focus_mat) or {}
    hojas = block.get("hojas") or []
    vfm_sheets = [
        (i, h)
        for i, h in enumerate(hojas)
        if any("VFM" in base_name(p.get("nombre")) for p in (h.get("piezas") or []))
    ]
    print(f"\n--- Re-nest motor actual: {len(vfm_sheets)} hojas con VFM en {focus_mat} ---")
    # Ordenar por menor fill cavity / menor eff
    scored = []
    for i, h in vfm_sheets:
        cav = cavity_fill_stats(h.get("piezas") or [])
        scored.append((cav["in_cavity"], float(h.get("eficiencia_real") or h.get("eficiencia") or 0), i, h, cav))
    scored.sort()  # peor fill primero

    for fill0, eff0, i, h, cav0 in scored[:4]:
        piezas_src = h.get("piezas") or []
        mix = Counter(base_name(p.get("nombre")) for p in piezas_src)
        piezas_in = piezas_pack_desde_hoja(h)
        w = float(h.get("placa_w") or 120 * IN)
        hh = float(h.get("placa_h") or 48 * IN)
        print(f"  sheet[{i}] pack_pool={len(piezas_in)} (src={len(piezas_src)})…", flush=True)

        tl = run_plate_sim(
            piezas_in,
            w_mm=w,
            h_mm=hh,
            kerf_in=kerf,
            margin_in=margin,
            engine_id="arga_base",
            isolate_process=False,
        )
        placed = list((tl.hoja or {}).get("piezas") or [])
        restos = list(tl.restos or [])
        gaps = sheet_gap_audit(placed, kerf)
        cav1 = cavity_fill_stats(placed)
        eff1 = (tl.hoja or {}).get("eficiencia")
        row = {
            "sheet_i": i,
            "code": h.get("sheet_code"),
            "mix": dict(mix),
            "before": {
                "n": len(piezas_src),
                "eff": eff0,
                "in_cavity": fill0,
                "per_host": cav0["per_host"],
            },
            "after": {
                "n": len(placed),
                "restos": len(restos),
                "eff": eff1,
                "in_cavity": cav1["in_cavity"],
                "per_host": cav1["per_host"],
                "gaps": gaps,
            },
        }
        report["focus_renest"].append(row)
        print(
            f"  sheet[{i}] {h.get('sheet_code')}: "
            f"before n={len(piezas_src)} cav={fill0} eff={eff0:.1f} → "
            f"after n={len(placed)} restos={len(restos)} cav={cav1['in_cavity']} "
            f"eff={eff1} min_gap={gaps['min_gap_in']} viol={gaps['violations']}"
        )
        for ph in cav1["per_host"]:
            print(f"      {ph['host']}: filled={ph['filled']} kinds={ph['kinds']}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
