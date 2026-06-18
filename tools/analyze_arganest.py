"""Analiza métricas de acomodo en workspaces .arganest."""
from __future__ import annotations

import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "interface"))

from nesting_workspace import cargar_workspace_desde_archivo  # noqa: E402

FILES = [
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\NANS\62174.arganest",
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\NANS\1000 kva de prueba.arganest",
]


def norm_clave(cal, mat) -> str:
    return f"{str(cal).strip()}_{str(mat).strip().upper()}"


def expected_pieces(partes) -> Counter:
    by_clave: Counter = Counter()
    for row in partes:
        if len(row) >= 3:
            qty = row[2]
            cal = row[3] if len(row) > 3 else ""
            mat = row[1]
            by_clave[norm_clave(cal, mat)] += int(qty or 0)
    return by_clave


def analyze(path: str) -> None:
    p = cargar_workspace_desde_archivo(path)
    name = os.path.basename(path)
    print("=" * 70)
    print(f'JOB: {p.get("job_activo")}  ({name})')
    print(f'Guardado: {p.get("saved_at")}')
    ui = p.get("ui_state") or {}
    print(
        f'Config: opt={ui.get("cmb_opt_val")} margin={ui.get("global_margin_val")} '
        f'corner={ui.get("global_corner_val")}'
    )

    partes = p.get("datos_partes_actuales") or []
    exp = expected_pieces(partes)
    print(f"Piezas en BOM: {sum(exp.values())} instancias en {len(exp)} grupos calibre/material")

    ml = p.get("resultados_multilote") or []
    total_hojas = 0
    total_colocadas = 0
    all_efis: list[float] = []
    low_sheets: list[tuple] = []
    unplaced_groups: list[tuple] = []

    for lote in ml:
        data = lote.get("data") or {}
        for clave, info in sorted(data.items()):
            if not isinstance(info, dict):
                continue
            hojas = info.get("hojas") or []
            placed: Counter = Counter()
            for h in hojas:
                piezas = h.get("piezas") or []
                efi_d = float(h.get("eficiencia_directa") or h.get("eficiencia") or 0)
                es_rtz = bool(h.get("es_retazo"))
                pw = float(h.get("placa_w") or 0)
                ph = float(h.get("placa_h") or 0)
                pid = h.get("placa_id") or h.get("origen_placa") or "?"
                efi_r = float(h.get("eficiencia_real") or efi_d)
                for pz in piezas:
                    placed[pz.get("nombre", "?")] += 1
                total_colocadas += len(piezas)
                all_efis.append(efi_d)
                total_hojas += 1
                if efi_d < 55.0 and not es_rtz:
                    low_sheets.append((clave, len(low_sheets) + 1, pid, pw, ph, len(piezas), efi_d, efi_r, es_rtz))

            exp_clave = exp.get(clave, 0)
            placed_total = sum(placed.values())
            efi_tanque = float(
                info.get("eficiencia_tanque_real") or info.get("eficiencia_tanque_directa") or 0
            )
            costo = float(info.get("costo_total") or 0)
            print(
                f"\n  [{clave}] placas={len(hojas)} colocadas={placed_total}/{exp_clave} "
                f"efi_tanque={efi_tanque:.1f}% costo=${costo:,.0f}"
            )
            if exp_clave and placed_total < exp_clave:
                unplaced_groups.append((clave, placed_total, exp_clave, exp_clave - placed_total))
                print(f"    *** FALTAN {exp_clave - placed_total} piezas sin colocar ***")
            for hi, h in enumerate(hojas):
                piezas = h.get("piezas") or []
                efi_d = float(h.get("eficiencia_directa") or h.get("eficiencia") or 0)
                efi_r = float(h.get("eficiencia_real") or efi_d)
                pw = float(h.get("placa_w") or 0)
                ph = float(h.get("placa_h") or 0)
                rtz = " RTZ" if h.get("es_retazo") else ""
                flag = " <<<" if efi_d < 55.0 and not h.get("es_retazo") else ""
                print(
                    f"    placa {hi + 1}: {pw:.0f}x{ph:.0f} id={h.get('placa_id', '?')}{rtz} | "
                    f"{len(piezas)} pzas | efi={efi_d:.1f}% real={efi_r:.1f}%{flag}"
                )

    print(f"\nRESUMEN GLOBAL: {total_hojas} placas, {total_colocadas} piezas colocadas")
    if all_efis:
        print(
            f"  Eficiencia por placa: media={sum(all_efis) / len(all_efis):.1f}% "
            f"min={min(all_efis):.1f}% max={max(all_efis):.1f}%"
        )
    if low_sheets:
        print(f"\n  PLACAS CON BAJA EFICIENCIA (<55%, no RTZ): {len(low_sheets)}")
        for row in low_sheets:
            print(f"    {row[0]} placa#{row[1]} {row[3]:.0f}x{row[4]:.0f} {row[5]}pzas efi={row[6]:.1f}%")
    if unplaced_groups:
        print(f"\n  GRUPOS CON PIEZAS SIN COLOCAR: {len(unplaced_groups)}")
    else:
        print("\n  Todas las piezas del BOM fueron colocadas.")


if __name__ == "__main__":
    for f in FILES:
        analyze(f)
        print()
