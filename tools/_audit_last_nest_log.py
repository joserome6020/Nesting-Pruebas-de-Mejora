"""Audita la última corrida en nesting_debug_geometry.txt."""
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOG = Path(r"C:\NEST_EXPORTS\nesting_debug_geometry.txt")


def main() -> int:
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    start = 0
    for i, ln in enumerate(lines):
        if "[RESUMEN-GRUPOS] Inicio" in ln:
            start = i
            break

    chunk = "\n".join(lines[start:])
    resumen = {}
    for m in re.finditer(r"\[RESUMEN-GRUPO\] clave=([^|]+) \| total_piezas=(\d+)", chunk):
        resumen[m.group(1).strip()] = int(m.group(2))

    match_ok = Counter()
    for m in re.finditer(r"\[MATCH-OK\] clave=([^|]+) \| nombre_final=", chunk):
        match_ok[m.group(1).strip()] += 1

    print("=== Ultima corrida: RESUMEN vs MATCH-OK ===")
    total_e = total_m = 0
    ok_all = True
    for clave in sorted(resumen):
        e = resumen[clave]
        m = match_ok.get(clave, 0)
        tag = "OK" if e == m else "MISMATCH"
        if e != m:
            ok_all = False
        print(f"  {clave}: esperadas={e} match_ok={m} [{tag}]")
        total_e += e
        total_m += m
    print(f"TOTAL: esperadas={total_e} match_ok={total_m}")

    errs = [ln for ln in lines if "INVENTARIO-INCOMPLETO" in ln or "MATCH-FAIL" in ln]
    print(f"INVENTARIO-INCOMPLETO/MATCH-FAIL en archivo: {len(errs)}")
    if errs:
        for ln in errs[-5:]:
            print(f"  {ln.strip()}")

    # 0.1046: dos Pared lateral deben tener MATCH-OK distinto
    lat = [
        ln
        for ln in chunk.splitlines()
        if "[MATCH-OK] clave=0.1046_A 36" in ln and "Pared lateral ATC" in ln
    ]
    print(f"0.1046 Pared lateral ATC MATCH-OK: {len(lat)} (esperado 2)")

    return 0 if ok_all and total_e == total_m and len(errs) == 0 and len(lat) >= 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
