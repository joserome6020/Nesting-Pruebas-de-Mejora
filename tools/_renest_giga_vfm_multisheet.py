"""Re-nest completo 0.1196_A 36 GALV (multi-hoja) con kerf 0.3 legal."""
from __future__ import annotations

import gzip
import json
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from shapely.geometry import Polygon, box

from modules.nesting_engine.sim_lab import (
    piece_from_dxf,
    piezas_pack_desde_hoja,
    run_plate_sim,
)

ARGANEST = (
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\NANS"
    r"\GIGA FLUIDSTACK.arganest"
)
DXF_DIR = (
    r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals"
    r"\ARGA METALS CORPORATE SYSTEM\TANKS\GIGA\GIGA FLUIDSTACK"
    r"\MODEL CORE FILES\AutoDXF\Processed Files"
)
OUT = os.path.join(_ROOT, "_logs", "giga_vfm_multSheet.json")
MAT = "0.1196_A 36 GALV"
IN = 25.4
KERF = 0.3
MARGIN = 0.15


def base(n: str) -> str:
    return str(n or "").split("#")[0]


def find_dxf(item: str) -> str | None:
    for n in os.listdir(DXF_DIR):
        if n.upper().startswith(item.upper()) and n.lower().endswith(".dxf"):
            return os.path.join(DXF_DIR, n)
    return None


def poly_of(p):
    rings = p.get("poligonos") or []
    if not rings:
        return None
    try:
        g = Polygon(rings[0], rings[1:] if len(rings) > 1 else None)
        return None if g.is_empty else g
    except Exception:
        return None


def cavity_fill(piezas):
    hosts, smalls = [], []
    for p in piezas:
        g = poly_of(p)
        if g is None:
            continue
        n = base(p.get("nombre"))
        if "VFM" in n or "HFM" in n:
            hosts.append(g)
        else:
            smalls.append(g.centroid)
    total = 0
    free_all = []
    for h in hosts:
        free = box(*h.bounds).difference(h)
        geoms = [free] if free.geom_type == "Polygon" else list(getattr(free, "geoms", []) or [])
        for g in geoms:
            if g.is_empty or g.area < 5 * IN * IN:
                continue
            if g.area > h.envelope.area * 0.85:
                continue
            free_all.append(g)
    assigned = set()
    for cav in free_all:
        for i, c in enumerate(smalls):
            if i in assigned:
                continue
            if cav.contains(c):
                assigned.add(i)
                total += 1
    return total


def gap_stats(piezas):
    polys = [poly_of(p) for p in piezas]
    polys = [g for g in polys if g is not None]
    min_g, viol = 1e9, 0
    req = KERF * 0.92 * IN
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            d = polys[i].distance(polys[j])
            min_g = min(min_g, d)
            if d + 1e-6 < req:
                viol += 1
    return (None if min_g > 1e8 else round(min_g / IN, 4)), viol


def expand_pool_from_sheets(hojas) -> list[dict]:
    """Une todo el material desde hojas (re-lee DXF cuando hay ruta)."""
    by_name: Counter = Counter()
    # Preferir rutas del primer ejemplar
    ruta_por: dict[str, str] = {}
    for h in hojas:
        for p in h.get("piezas") or []:
            n = base(p.get("nombre"))
            by_name[n] += 1
            ruta = str(p.get("ruta") or p.get("dxf_path") or "").strip()
            if n not in ruta_por and ruta and os.path.isfile(ruta):
                ruta_por[n] = ruta
            elif n not in ruta_por:
                found = find_dxf(n)
                if found:
                    ruta_por[n] = found

    out: list[dict] = []
    missing = []
    for n, qty in by_name.items():
        ruta = ruta_por.get(n) or find_dxf(n)
        if not ruta:
            missing.append((n, qty))
            continue
        batch, err = piece_from_dxf(ruta, nombre=n, qty=qty)
        if err:
            missing.append((n, qty, err))
            continue
        out.extend(batch)
    return out, by_name, missing


def main() -> int:
    payload = json.loads(gzip.decompress(open(ARGANEST, "rb").read()).decode("utf-8"))
    block = payload["resultados_multilote"][0]["data"][MAT]
    hojas = block.get("hojas") or []
    w = float((hojas[0] or {}).get("placa_w") or 120 * IN)
    h = float((hojas[0] or {}).get("placa_h") or 48 * IN)

    pool, mix, missing = expand_pool_from_sheets(hojas)
    print(f"MIX total piezas={sum(mix.values())} tipos={len(mix)}")
    for n, c in mix.most_common():
        print(f"  {n}: {c}")
    if missing:
        print("MISSING", missing)
    print(f"pool_expanded={len(pool)} placa={w/IN:.1f}x{h/IN:.1f}\"")

    # Baseline guardado
    base_eff = []
    base_cav = 0
    base_viol = 0
    for sh in hojas:
        piezas = sh.get("piezas") or []
        base_eff.append(float(sh.get("eficiencia_real") or sh.get("eficiencia") or 0))
        base_cav += cavity_fill(piezas)
        _, v = gap_stats(piezas)
        base_viol += v
    print(
        f"SAVED: hojas={len(hojas)} eff_avg={sum(base_eff)/len(base_eff):.1f} "
        f"cav_total={base_cav} viol={base_viol}"
    )

    remaining = list(pool)
    new_sheets = []
    sheet_i = 0
    while remaining:
        sheet_i += 1
        print(f"\n--- Packing sheet {sheet_i} from {len(remaining)} remaining ---", flush=True)
        tl = run_plate_sim(
            remaining,
            w_mm=w,
            h_mm=h,
            kerf_in=KERF,
            margin_in=MARGIN,
            engine_id="arga_base",
            isolate_process=False,
        )
        placed = list((tl.hoja or {}).get("piezas") or [])
        restos = list(tl.restos or [])
        if not placed:
            print("STOP: no placement progress")
            break
        cav = cavity_fill(placed)
        g, v = gap_stats(placed)
        eff = float((tl.hoja or {}).get("eficiencia") or 0)
        mix_p = Counter(base(p.get("nombre")) for p in placed)
        print(
            f"  sheet{sheet_i}: n={len(placed)} restos={len(restos)} "
            f"eff={eff:.1f} cav={cav} gap={g} viol={v}"
        )
        print(f"  mix={dict(mix_p)}")
        new_sheets.append(
            {
                "i": sheet_i,
                "n": len(placed),
                "eff": eff,
                "cav": cav,
                "gap": g,
                "viol": v,
                "mix": dict(mix_p),
            }
        )
        # Advance: restos become remaining (already unplaced). If restos empty, done.
        # run_plate_sim restos should be pieces not placed; placed removed.
        if len(restos) >= len(remaining):
            print("STOP: no progress (restos >= remaining)")
            break
        remaining = restos
        if sheet_i >= 40:
            print("STOP: sheet cap")
            break

    report = {
        "saved": {
            "hojas": len(hojas),
            "eff_avg": sum(base_eff) / max(len(base_eff), 1),
            "eff_mat": block.get("eficiencia_tanque_real"),
            "cav_total": base_cav,
            "viol": base_viol,
        },
        "renest": {
            "hojas": len(new_sheets),
            "eff_avg": sum(s["eff"] for s in new_sheets) / max(len(new_sheets), 1),
            "cav_total": sum(s["cav"] for s in new_sheets),
            "viol_total": sum(s["viol"] for s in new_sheets),
            "restos_final": len(remaining),
            "sheets": new_sheets,
            "restos_mix": dict(Counter(base(p.get("nombre")) for p in remaining)),
        },
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print("\n==== SUMMARY ====")
    print(
        f"SAVED  hojas={report['saved']['hojas']} eff_mat={report['saved']['eff_mat']:.1f} "
        f"cav={report['saved']['cav_total']} viol={report['saved']['viol']}"
    )
    print(
        f"RENEST hojas={report['renest']['hojas']} eff_avg={report['renest']['eff_avg']:.1f} "
        f"cav={report['renest']['cav_total']} viol={report['renest']['viol_total']} "
        f"restos={report['renest']['restos_final']}"
    )
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
