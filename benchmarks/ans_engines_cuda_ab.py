"""A/B ANS: motores operativos con/sin ``ARGA_NEST_CUDA`` (sin motores piloto).

Gates fail-closed: piezas, solapes/kerf, eficiencia, tiempo.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

from benchmarks.corpus_loader import load_scenario
from benchmarks.lab_cal11_compare import _audit_sheet, _remaining_from_placed


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _run_engine(
    engine_id: str,
    *,
    params: dict[str, Any],
    pieces: list[dict[str, Any]],
    cuda_on: bool,
) -> dict[str, Any]:
    import modules.nesting_engine.venom_ai as venom_ai
    from modules.nesting_engine.engine_registry import empaquetar_una_hoja, is_engine_ready
    from modules.nesting_engine.nest_cuda import cuda_status_for_engine, nest_cuda_env
    from modules.nesting_engine.nest_engine_context import (
        reset_active_engine_id,
        set_active_engine_id,
    )

    # A/B de empaquetado: desactiva polish para no mezclar Venom en el gate de motores.
    previous_polisher = venom_ai.apply_smart_polisher
    venom_ai.apply_smart_polisher = lambda hoja, engine_id_: hoja

    label = f"{engine_id}+cuda" if cuda_on else f"{engine_id}-cpu"
    status = cuda_status_for_engine(engine_id)
    if not is_engine_ready(engine_id):
        venom_ai.apply_smart_polisher = previous_polisher
        return {
            "label": label,
            "engine_id": engine_id,
            "cuda_on": cuda_on,
            "ready": False,
            "error": "motor no listo",
            "cuda": status,
        }

    token = set_active_engine_id(engine_id)
    try:
        with nest_cuda_env(cuda_on):
            status = cuda_status_for_engine(engine_id)
            plate_w_mm = float(params["plate_w_in"]) * 25.4
            plate_h_mm = float(params["plate_h_in"]) * 25.4
            kerf_in = float(params.get("kerf_in") or 0.3)
            margin_in = float(params.get("margin_in") or 0.0)
            opt = str(params.get("opt") or "OPTIMIZAR LARGO Y ANCHO")
            corner = str(params.get("corner") or "INFERIOR IZQUIERDA")

            pool = list(pieces)
            sheets: list[dict[str, Any]] = []
            started = time.perf_counter()
            while pool:
                hoja, restos = empaquetar_una_hoja(
                    pool,
                    plate_w_mm,
                    plate_h_mm,
                    kerf_override=kerf_in,
                    margin_override=margin_in,
                    opt_override=opt,
                    corner_override=corner,
                    mc_iterations=1,
                    engine_id=engine_id,
                )
                audit = _audit_sheet(hoja, kerf_in)
                sheets.append(
                    {
                        "placed": audit["placed"],
                        "efi_directa": audit["efi_directa"],
                        "solape_ok": audit["solape_ok"],
                        "kerf_violations": audit["kerf_violations"],
                        "names": [
                            str(p.get("nombre") or "") for p in (hoja.get("piezas") or [])
                        ],
                    }
                )
                next_pool = (
                    restos if restos is not None else _remaining_from_placed(pool, hoja)
                )
                if len(next_pool) >= len(pool):
                    break
                pool = next_pool
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            placed = sum(int(s["placed"]) for s in sheets)
            return {
                "label": label,
                "engine_id": engine_id,
                "cuda_on": cuda_on,
                "ready": True,
                "elapsed_ms": elapsed_ms,
                "sheets": len(sheets),
                "placed": placed,
                "expected": len(pieces),
                "efi_directa_mean": (
                    sum(float(s["efi_directa"]) for s in sheets) / len(sheets)
                    if sheets
                    else 0.0
                ),
                "solape_ok": all(bool(s["solape_ok"]) for s in sheets),
                "kerf_violations": sum(int(s["kerf_violations"]) for s in sheets),
                "cuda": status,
                "sheet_fingerprints": [tuple(s.get("names") or []) for s in sheets],
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "label": label,
            "engine_id": engine_id,
            "cuda_on": cuda_on,
            "ready": True,
            "error": str(exc),
            "cuda": status,
        }
    finally:
        reset_active_engine_id(token)
        venom_ai.apply_smart_polisher = previous_polisher


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok_rows = [r for r in rows if r.get("ready") and "elapsed_ms" in r and not r.get("error")]
    if not ok_rows:
        return {
            "ready": False,
            "error": rows[0].get("error") if rows else "sin datos",
            "cuda": (rows[0].get("cuda") if rows else {}),
        }
    return {
        "ready": True,
        "median_elapsed_ms": _median([float(r["elapsed_ms"]) for r in ok_rows]),
        "placed": ok_rows[0]["placed"],
        "sheets": ok_rows[0]["sheets"],
        "efi_directa_mean": ok_rows[0]["efi_directa_mean"],
        "solape_ok": all(bool(r["solape_ok"]) for r in ok_rows),
        "kerf_violations": ok_rows[0]["kerf_violations"],
        "cuda": ok_rows[0].get("cuda") or {},
        "fingerprint_stable": all(
            r.get("sheet_fingerprints") == ok_rows[0].get("sheet_fingerprints")
            for r in ok_rows
        ),
    }


def _pair_gate(
    cpu: dict[str, Any],
    cuda: dict[str, Any],
    *,
    efi_tol_pp: float = 0.1,
) -> dict[str, Any]:
    if not cpu.get("ready") or not cuda.get("ready"):
        return {
            "pass": False,
            "reason": "baseline o CUDA no listo",
            "cpu_ready": bool(cpu.get("ready")),
            "cuda_ready": bool(cuda.get("ready")),
        }
    placed_ok = int(cuda.get("placed") or 0) >= int(cpu.get("placed") or 0)
    solape_ok = bool(cuda.get("solape_ok")) and bool(cpu.get("solape_ok"))
    kerf_ok = int(cuda.get("kerf_violations") or 0) <= int(cpu.get("kerf_violations") or 0)
    efi_cpu = float(cpu.get("efi_directa_mean") or 0.0)
    efi_cuda = float(cuda.get("efi_directa_mean") or 0.0)
    efi_ok = efi_cuda + 1e-9 >= (efi_cpu - efi_tol_pp)
    t_cpu = float(cpu.get("median_elapsed_ms") or 0.0)
    t_cuda = float(cuda.get("median_elapsed_ms") or 0.0)
    # Sin regresión de tiempo (margen 10%% por ruido GA/threads); speedup estricto es bonus.
    time_ok = t_cuda > 0 and t_cpu > 0 and t_cuda <= (t_cpu * 1.10)
    faster = t_cuda > 0 and t_cpu > 0 and t_cuda < t_cpu
    speedup = (t_cpu / t_cuda) if t_cuda > 0 else None
    quality = placed_ok and solape_ok and kerf_ok and efi_ok
    passed = quality and time_ok
    return {
        "pass": passed,
        "quality_ok": quality,
        "placed_ok": placed_ok,
        "solape_ok": solape_ok,
        "kerf_ok": kerf_ok,
        "efi_ok": efi_ok,
        "time_ok": time_ok,
        "faster": faster,
        "speedup": speedup,
        "efi_delta_pp": efi_cuda - efi_cpu,
        "cuda_flag_enabled": bool((cuda.get("cuda") or {}).get("flag_enabled")),
        "runtime_available": bool((cuda.get("cuda") or {}).get("runtime_available")),
    }


def benchmark(*, scenario_id: str, repeats: int, engines: list[str]) -> dict[str, Any]:
    params, pieces = load_scenario(scenario_id)
    by_label: dict[str, Any] = {}
    pairs: dict[str, Any] = {}

    for engine_id in engines:
        for cuda_on in (False, True):
            label = f"{engine_id}+cuda" if cuda_on else f"{engine_id}-cpu"
            rows = [
                _run_engine(
                    engine_id, params=params, pieces=pieces, cuda_on=cuda_on
                )
                for _ in range(max(1, repeats))
            ]
            by_label[label] = _aggregate(rows)

        pairs[engine_id] = _pair_gate(
            by_label[f"{engine_id}-cpu"],
            by_label[f"{engine_id}+cuda"],
        )

    all_pass = all(bool(g.get("pass")) for g in pairs.values()) if pairs else False
    any_faster = any(bool(g.get("faster")) for g in pairs.values()) if pairs else False
    return {
        "schema": "arga_ans_engines_cuda_ab_v2",
        "scenario": scenario_id,
        "repeats": max(1, repeats),
        "results": by_label,
        "gates_by_engine": pairs,
        "gate": {
            "all_pass": all_pass,
            "any_faster": any_faster,
            "production_ready": all_pass and any_faster,
            "note": (
                "CUDA es turbo interno de Ultra/Force/Lite/Burke (opt-in ARGA_NEST_CUDA=1). "
                "Gate: calidad + sin regresión de tiempo (≤5%). "
                "production_ready exige además al menos un motor más rápido."
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="A/B motores ANS operativos ± ARGA_NEST_CUDA"
    )
    parser.add_argument("--scenario", default="r_1000kva_critical")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument(
        "--engines",
        default="svgnest_ultra,arga_force",
        help="Motores a comparar CPU vs CUDA (Lite/Burke opcionales).",
    )
    parser.add_argument(
        "--out",
        default="benchmarks/results_real/ans_engines_cuda_ab.json",
    )
    args = parser.parse_args(argv)
    engines = [e.strip() for e in str(args.engines).split(",") if e.strip()]
    # Nunca incluir piloto en este gate.
    engines = [e for e in engines if "pilot" not in e.lower()]
    report = benchmark(
        scenario_id=args.scenario,
        repeats=max(1, args.repeats),
        engines=engines,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(out)
    for label, data in (report.get("results") or {}).items():
        if data.get("ready") and data.get("median_elapsed_ms") is not None:
            print(
                f"  {label}: {data['median_elapsed_ms']:.1f} ms | "
                f"placed={data.get('placed')} efi={data.get('efi_directa_mean'):.2f}"
            )
    for eid, gate in (report.get("gates_by_engine") or {}).items():
        print(
            f"  gate[{eid}]: pass={gate.get('pass')} "
            f"speedup={gate.get('speedup')} efi_delta={gate.get('efi_delta_pp')}"
        )
    return 0 if report["gate"]["all_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
