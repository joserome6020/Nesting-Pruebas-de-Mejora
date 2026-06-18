"""Re-ejecuta nesting 62174 en modo max y compara vs workspace guardado (standard)."""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "interface"))

from nesting_workspace import cargar_workspace_desde_archivo  # noqa: E402
from modules.nesting_engine.efficiency_metrics import actualizar_eficiencias_resultados  # noqa: E402
from modules.nesting_engine.manager import MotorNesting  # noqa: E402
from modules.sheets_manager import PlatesManager  # noqa: E402

WORKSPACE = r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\NANS\62174.arganest"
CACHE_MAX = os.path.join(os.path.dirname(__file__), "compare_62174_max_summary.json")


def _summarize_resultados(data: dict) -> dict[str, dict]:
    """Resumen por grupo calibre/material."""
    out: dict[str, dict] = {}
    for clave, info in sorted((data or {}).items()):
        if not isinstance(info, dict):
            continue
        hojas = info.get("hojas") or []
        efis_madre: list[float] = []
        efis_all: list[float] = []
        piezas = 0
        for h in hojas:
            piezas += len(h.get("piezas") or [])
            efi = float(h.get("eficiencia_directa") or h.get("eficiencia") or 0)
            efis_all.append(efi)
            if not h.get("es_retazo"):
                efis_madre.append(efi)
        out[clave] = {
            "placas": len(hojas),
            "madre": sum(1 for h in hojas if not h.get("es_retazo")),
            "rtz": sum(1 for h in hojas if h.get("es_retazo")),
            "piezas": piezas,
            "efi_tanque": float(
                info.get("eficiencia_tanque_real") or info.get("eficiencia_tanque_directa") or 0
            ),
            "costo": float(info.get("costo_total") or 0),
            "efi_madre_min": min(efis_madre) if efis_madre else 0.0,
            "efi_madre_media": (sum(efis_madre) / len(efis_madre)) if efis_madre else 0.0,
            "efi_all_min": min(efis_all) if efis_all else 0.0,
            "hojas_detalle": [
                {
                    "idx": i + 1,
                    "rtz": bool(h.get("es_retazo")),
                    "id": h.get("placa_id"),
                    "w": h.get("placa_w"),
                    "h": h.get("placa_h"),
                    "n": len(h.get("piezas") or []),
                    "efi": float(h.get("eficiencia_directa") or h.get("eficiencia") or 0),
                    "efi_real": float(h.get("eficiencia_real") or 0),
                }
                for i, h in enumerate(hojas)
            ],
        }
    return out


def _print_detalle(label: str, grupos: dict[str, dict], claves_focus: set[str]) -> None:
    print(f"\n--- Detalle {label} (grupos foco) ---")
    for clave in sorted(claves_focus):
        g = grupos.get(clave)
        if not g:
            print(f"  [{clave}] (sin datos)")
            continue
        print(f"  [{clave}] {g['placas']} placas ({g['madre']} madre + {g['rtz']} RTZ) | efi_tanque={g['efi_tanque']:.1f}%")
        for d in g["hojas_detalle"]:
            tag = "RTZ" if d["rtz"] else "MAD"
            flag = " <<<" if (not d["rtz"] and d["efi"] < 55.0) else ""
            print(
                f"    #{d['idx']} {tag} {int(d['w'] or 0)}x{int(d['h'] or 0)} "
                f"{d['id']} | {d['n']}pz efi={d['efi']:.1f}% real={d['efi_real']:.1f}%{flag}"
            )


def main() -> None:
    os.environ["ARGA_NEST_MODE"] = "max"

    payload = cargar_workspace_desde_archivo(WORKSPACE)
    ui = payload.get("ui_state") or {}
    baseline_data = payload["resultados_multilote"][0]["data"]
    baseline = _summarize_resultados(baseline_data)

    partes = payload.get("datos_partes_actuales") or []
    kerf = float(ui.get("global_kerf_val", 0.2) or 0.2)
    margin = float(ui.get("global_margin_val", 0.15) or 0.15)
    corner = str(ui.get("global_corner_val", "INFERIOR IZQUIERDA"))
    opt = str(ui.get("cmb_opt_val", "OPTIMIZAR LARGO Y ANCHO"))

    # Kerf puede estar solo en UI del tab; fallback desde primera hoja baseline
    if kerf == 0.2:
        for info in baseline_data.values():
            for h in (info.get("hojas") or [])[:1]:
                kerf = float(h.get("kerf_usado") or kerf)
                break

    print("=" * 72)
    print("COMPARATIVA 62174: baseline (guardado) vs ARGA_NEST_MODE=max")
    print(f"Config: kerf={kerf} margin={margin} corner={corner} opt={opt}")
    print(f"Piezas BOM: {sum(int(r[2]) for r in partes if len(r) >= 3)}")
    print("=" * 72)

    placas_mgr = PlatesManager()
    datos_placas = placas_mgr.obtener_datos_placas()
    motor = MotorNesting()

    t0 = time.time()
    print("\nEjecutando nesting modo MAX (30 iter MC)... puede tardar varios minutos.\n")

    def progress(msg, pct):
        if pct <= 0 or pct >= 0.99 or int(pct * 100) % 10 == 0:
            print(f"  [{pct * 100:5.1f}%] {msg}")

    resultado = motor.ejecutar_nesting_visual(
        partes,
        datos_placas,
        progress_callback=progress,
        config_kerf=kerf,
        config_margin=margin,
        config_corner=corner,
        config_opt=opt,
        wo_name=str(payload.get("job_activo", "62174")),
    )
    elapsed = time.time() - t0

    if isinstance(resultado, dict) and resultado.get("error"):
        print(f"\nERROR: {resultado['error']}")
        sys.exit(1)

    actualizar_eficiencias_resultados(resultado)
    max_run = _summarize_resultados(resultado)
    with open(CACHE_MAX, "w", encoding="utf-8") as f:
        json.dump(max_run, f, ensure_ascii=False, indent=2)

    print(f"\nCompletado en {elapsed:.1f}s ({elapsed / 60:.1f} min)")

    claves = sorted(set(baseline.keys()) | set(max_run.keys()))
    focus = {"0.25_A 36", "0.375_A 36", "0.75_A 36", "1_A 36"}

    print("\n" + "=" * 72)
    print(
        f"{'GRUPO':<16} {'BASE placas':>11} {'MAX placas':>10} {'dPl':>4} "
        f"{'BASE efi%':>10} {'MAX efi%':>9} {'dEfi':>7} {'BASE $':>10} {'MAX $':>10}"
    )
    print("-" * 72)

    total_base_placas = total_max_placas = 0
    total_base_costo = total_max_costo = 0.0
    mejoras: list[str] = []
    regresiones: list[str] = []

    for clave in claves:
        b = baseline.get(clave, {})
        m = max_run.get(clave, {})
        bp, mp = b.get("placas", 0), m.get("placas", 0)
        be, me = b.get("efi_tanque", 0), m.get("efi_tanque", 0)
        bc, mc = b.get("costo", 0), m.get("costo", 0)
        total_base_placas += bp
        total_max_placas += mp
        total_base_costo += bc
        total_max_costo += mc
        delta_p = mp - bp
        delta_e = me - be
        print(
            f"{clave:<16} {bp:>11} {mp:>10} {delta_p:+4} "
            f"{be:>9.1f}% {me:>8.1f}% {delta_e:+6.1f} "
            f"{bc:>10,.0f} {mc:>10,.0f}"
        )
        if delta_e > 1.0 and delta_p <= 0:
            mejoras.append(f"{clave}: efi +{delta_e:.1f}% sin más placas")
        elif delta_e > 2.0:
            mejoras.append(f"{clave}: efi +{delta_e:.1f}%")
        if delta_e < -2.0:
            regresiones.append(f"{clave}: efi {delta_e:.1f}%")
        if delta_p > 0 and delta_e <= 0:
            regresiones.append(f"{clave}: +{delta_p} placas sin ganar efi")

    print("-" * 72)
    print(
        f"{'TOTAL':<16} {total_base_placas:>11} {total_max_placas:>10} "
        f"{total_max_placas - total_base_placas:+4} "
        f"{'':>10} {'':>9} {'':>7} "
        f"{total_base_costo:>10,.0f} {total_max_costo:>10,.0f}"
    )
    print(f"\nTiempo max: {elapsed:.1f}s | Ahorro costo: ${total_base_costo - total_max_costo:,.0f}")

    _print_detalle("BASELINE (standard guardado)", baseline, focus)
    _print_detalle("MAX (nueva corrida)", max_run, focus)

    print("\n" + "=" * 72)
    print("INTERPRETACIÓN")
    if mejoras:
        print("Mejoras con max:")
        for x in mejoras:
            print(f"  + {x}")
    if regresiones:
        print("Regresiones:")
        for x in regresiones:
            print(f"  - {x}")
    if not mejoras and not regresiones:
        print("  Sin cambios significativos (>2% efi o placas).")

    # Placas madre problemáticas en baseline vs max
    print("\nPlacas madre <55% eficiencia:")
    for label, grupos in [("BASE", baseline), ("MAX", max_run)]:
        lows = []
        for clave, g in grupos.items():
            for d in g["hojas_detalle"]:
                if not d["rtz"] and d["efi"] < 55.0:
                    lows.append(f"{clave}#{d['idx']} {d['n']}pz {d['efi']:.1f}%")
        print(f"  {label}: {len(lows)} -> {', '.join(lows) if lows else 'ninguna'}")


if __name__ == "__main__":
    main()
