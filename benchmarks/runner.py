"""Runner CLI de benchmarks de nesting.

Uso:
  python -m benchmarks.runner --list
  python -m benchmarks.runner --scenario s0_micro --engine arga_force
  python -m benchmarks.runner --all --engines arga_force,svgnest_ultra,cpp_v2_poc --write-baselines
  python -m benchmarks.runner --scenario s0_micro --engine cpp_v2_poc --compare
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

# Repo root en sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from benchmarks.corpus_loader import (  # noqa: E402
    baselines_dir,
    list_scenarios,
    load_scenario,
    pieces_to_native,
)
from benchmarks.geom_audit import count_in_holes, gap_audit  # noqa: E402
from benchmarks.metrics_contract import empty_result, result_pass_ok, validate_result  # noqa: E402


IN_TO_MM = 25.4


def _enrich_hoja(hoja: dict, *, w_mm: float, h_mm: float, kerf_in: float, margin_in: float) -> dict:
    out = dict(hoja or {})
    out.update(
        {
            "placa_w": float(w_mm),
            "placa_h": float(h_mm),
            "kerf_usado": float(kerf_in),
            "margin_usado": float(margin_in),
            "es_retazo": False,
        }
    )
    return out


def _run_production(
    piezas: list[dict],
    *,
    engine_id: str,
    w_mm: float,
    h_mm: float,
    kerf_in: float,
    margin_in: float,
    corner: str,
    opt: str,
    mc_iterations: int,
) -> tuple[dict, list, float, str]:
    from modules.nesting_engine.sim_lab import run_plate_sim

    tl = run_plate_sim(
        piezas,
        w_mm=w_mm,
        h_mm=h_mm,
        kerf_in=kerf_in,
        margin_in=margin_in,
        corner=corner,
        opt=opt,
        mc_iterations=mc_iterations,
        engine_id=engine_id,
        isolate_process=False,
    )
    # sim_lab marca ok=False si hay restos aunque la hoja sea válida.
    # Solo tratamos como hard-fail si no hay hoja o hay error de motor.
    hoja = dict(tl.hoja or {})
    restos = list(tl.restos or [])
    hard_err = str(tl.error or "").strip()
    if not hoja.get("piezas") and hard_err:
        return {}, list(piezas), float(tl.elapsed_ms or 0.0), hard_err or "sim_failed"
    if not hoja.get("piezas") and not tl.ok and not restos:
        # Fallo total sin colocadas
        return {}, list(piezas), float(tl.elapsed_ms or 0.0), hard_err or "sim_failed"
    return hoja, restos, float(tl.elapsed_ms or 0.0), hard_err


def _run_cpp_v2_poc(
    piezas: list[dict],
    *,
    w_mm: float,
    h_mm: float,
    kerf_in: float,
    margin_in: float,
    corner: str,
    opt: str,
) -> tuple[dict, list, float, str]:
    from modules.nesting_engine.algorithm_bridge_v2 import empaquetar_una_hoja_poc

    native = pieces_to_native(piezas)
    t0 = time.perf_counter()
    try:
        hoja_native, restos_native = empaquetar_una_hoja_poc(
            native,
            w_mm,
            h_mm,
            kerf_in,
            margin_in,
            opt,
            corner,
            None,
        )
    except Exception as exc:  # noqa: BLE001
        return {}, list(piezas), (time.perf_counter() - t0) * 1000.0, str(exc)
    elapsed = (time.perf_counter() - t0) * 1000.0
    hoja = _enrich_hoja(
        dict(hoja_native or {}),
        w_mm=w_mm,
        h_mm=h_mm,
        kerf_in=kerf_in,
        margin_in=margin_in,
    )
    # Restos nativos → count; reconstruimos restos por nombre desde pool
    placed_names = [str(p.get("nombre") or "") for p in (hoja.get("piezas") or [])]
    placed_ctr: dict[str, int] = {}
    for n in placed_names:
        placed_ctr[n] = placed_ctr.get(n, 0) + 1
    restos = []
    for p in piezas:
        n = str(p.get("nombre") or "")
        if placed_ctr.get(n, 0) > 0:
            placed_ctr[n] -= 1
        else:
            restos.append(p)
    return hoja, restos, elapsed, ""


def evaluate_run(
    *,
    scenario: str,
    engine_id: str,
    hoja: dict,
    restos: list,
    expected: int,
    elapsed_ms: float,
    kerf_in: float,
    error: str = "",
    require_full_place: bool = True,
    notes: str = "",
) -> dict[str, Any]:
    from modules.nesting_engine.efficiency_metrics import actualizar_eficiencias_hoja
    from modules.nesting_engine.sheet_integrity import hoja_tiene_solapes_metal

    row = empty_result(scenario=scenario, engine_id=engine_id, expected=expected, error=error)
    row["elapsed_ms"] = float(elapsed_ms or 0.0)
    row["notes"] = notes
    if error:
        row["pass_ok"] = False
        return row

    hoja = dict(hoja or {})
    try:
        actualizar_eficiencias_hoja(hoja)
    except Exception:
        pass

    placed = len(
        [
            p
            for p in (hoja.get("piezas") or [])
            if isinstance(p, dict)
            and not str(p.get("nombre") or "").startswith(
                ("REF__", "TATUAJE__", "RETAZO_", "REMANENTE__", "CU_CORTE__")
            )
        ]
    )
    row["placed"] = placed
    row["restos"] = len(restos or [])
    row["efi_directa"] = float(hoja.get("eficiencia_directa") or hoja.get("eficiencia") or 0.0)
    row["efi_real"] = float(hoja.get("eficiencia_real") or row["efi_directa"])

    has_overlap, detail = hoja_tiene_solapes_metal(hoja, kerf_in=kerf_in)
    gap = gap_audit(hoja, kerf_in=kerf_in)
    # Fail-closed: solape_ok False si hay solape metal O overlap del gap audit
    solape_ok = (not has_overlap) and int(gap.get("overlap_violations") or 0) == 0
    if str(detail or "").startswith("validacion_solape_no_disponible"):
        solape_ok = False

    row["solape_ok"] = bool(solape_ok)
    row["kerf_violations"] = int(gap.get("kerf_violations") or 0)
    row["min_gap_in"] = gap.get("min_gap_in")
    row["in_holes"] = int(count_in_holes(hoja))
    row["overlap_detail"] = detail or ""
    row["gap_details"] = gap.get("details") or []
    row["pass_ok"] = result_pass_ok(row, require_full_place=require_full_place)
    missing = validate_result(row)
    if missing:
        row["error"] = f"contrato_incompleto:{','.join(missing)}"
        row["pass_ok"] = False
    return row


def run_scenario(scenario_id: str, engine_id: str) -> dict[str, Any]:
    params, piezas = load_scenario(scenario_id)
    w_mm = float(params["plate_w_in"]) * IN_TO_MM
    h_mm = float(params["plate_h_in"]) * IN_TO_MM
    expected = len(piezas)
    eid = str(engine_id).strip().lower()
    nfp_cache: dict[str, Any] | None = None

    if eid in ("cpp_v2_poc", "algorithm_cpp_v2", "v2", "new_poc"):
        from modules.nesting_engine.algorithm_bridge_v2 import (
            nfp_cache_stats,
            reset_nfp_cache,
        )

        # Una corrida de benchmark mide sus propios hits/misses; el motor real
        # puede conservar el caché entre hojas y lotes.
        reset_nfp_cache()
        hoja, restos, elapsed_ms, error = _run_cpp_v2_poc(
            piezas,
            w_mm=w_mm,
            h_mm=h_mm,
            kerf_in=float(params["kerf_in"]),
            margin_in=float(params["margin_in"]),
            corner=str(params["corner"]),
            opt=str(params["opt"]),
        )
        eid = "cpp_v2_poc"
        nfp_cache = nfp_cache_stats()
    else:
        hoja, restos, elapsed_ms, error = _run_production(
            piezas,
            engine_id=eid,
            w_mm=w_mm,
            h_mm=h_mm,
            kerf_in=float(params["kerf_in"]),
            margin_in=float(params["margin_in"]),
            corner=str(params["corner"]),
            opt=str(params["opt"]),
            mc_iterations=int(params["mc_iterations"]),
        )

    row = evaluate_run(
        scenario=params["scenario"],
        engine_id=eid,
        hoja=hoja,
        restos=restos,
        expected=expected,
        elapsed_ms=elapsed_ms,
        kerf_in=float(params["kerf_in"]),
        error=error,
        require_full_place=bool(params.get("require_full_place", True)),
        notes=str(params.get("notes") or ""),
    )
    row["nest_mode"] = params.get("nest_mode")
    row["level"] = params.get("level")
    if isinstance(hoja, dict) and hoja.get("cuda_raster"):
        row["cuda_raster"] = dict(hoja["cuda_raster"])
    if isinstance(hoja, dict) and hoja.get("packer_timing"):
        row["packer_timing"] = dict(hoja["packer_timing"])
    if nfp_cache is not None:
        row["nfp_cache"] = nfp_cache
    return row


def baseline_path(scenario_id: str, engine_id: str) -> str:
    safe_eng = str(engine_id).replace("/", "_").replace("\\", "_")
    return os.path.join(baselines_dir(), f"{scenario_id}__{safe_eng}.json")


def write_baseline(row: dict[str, Any]) -> str:
    os.makedirs(baselines_dir(), exist_ok=True)
    path = baseline_path(row["scenario"], row["engine_id"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(row, f, ensure_ascii=False, indent=2)
    return path


def load_baseline(scenario_id: str, engine_id: str) -> dict[str, Any] | None:
    path = baseline_path(scenario_id, engine_id)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compare_to_baseline(row: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    base_efi = float(baseline.get("efi_directa") or 0.0)
    cur_efi = float(row.get("efi_directa") or 0.0)
    out["baseline_delta_pp"] = cur_efi - base_efi
    out["baseline_engine_id"] = baseline.get("engine_id")
    out["baseline_efi_directa"] = base_efi
    out["baseline_placed"] = baseline.get("placed")
    out["baseline_elapsed_ms"] = baseline.get("elapsed_ms")

    regressions = []
    if not row.get("solape_ok", False):
        regressions.append("solape")
    if int(row.get("kerf_violations") or 0) > 0:
        regressions.append("kerf")
    if int(row.get("placed") or 0) < int(baseline.get("placed") or 0):
        regressions.append("placed")
    # tolerancia 2 pp vs baseline
    if cur_efi + 2.0 < base_efi:
        regressions.append("efi_directa")
    out["regressions"] = regressions
    out["compare_ok"] = (not regressions) and bool(row.get("pass_ok"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ARGA nesting benchmark runner")
    ap.add_argument("--list", action="store_true", help="Lista escenarios del corpus")
    ap.add_argument("--scenario", action="append", default=[], help="ID de escenario (repitable)")
    ap.add_argument("--all", action="store_true", help="Todos los escenarios")
    ap.add_argument(
        "--engines",
        default="arga_force,svgnest_ultra",
        help="Lista de motores separados por coma",
    )
    ap.add_argument("--engine", default="", help="Un solo motor (override)")
    ap.add_argument("--write-baselines", action="store_true")
    ap.add_argument("--compare", action="store_true", help="Compara vs baseline del mismo motor")
    ap.add_argument(
        "--compare-vs",
        default="",
        help="Compara cada corrida vs baseline de este motor (ej. arga_force)",
    )
    ap.add_argument("--out", default="", help="JSON de salida agregado")
    args = ap.parse_args(argv)

    if args.list:
        for s in list_scenarios():
            print(s)
        return 0

    scenarios = list(args.scenario)
    if args.all or not scenarios:
        scenarios = list_scenarios() if args.all else list_scenarios()
    if args.engine:
        engines = [args.engine]
    else:
        engines = [e.strip() for e in str(args.engines).split(",") if e.strip()]

    results: list[dict[str, Any]] = []
    failed = 0
    for sc in scenarios:
        for eng in engines:
            print(f"== {sc} / {eng} ==")
            row = run_scenario(sc, eng)
            if args.write_baselines and row.get("pass_ok"):
                path = write_baseline(row)
                print(f"  baseline -> {path}")
            if args.compare:
                base = load_baseline(sc, row["engine_id"])
                if base:
                    row = compare_to_baseline(row, base)
                else:
                    row["compare_ok"] = False
                    row["regressions"] = ["missing_baseline"]
            if args.compare_vs:
                base = load_baseline(sc, args.compare_vs)
                if base:
                    row = compare_to_baseline(row, base)
                else:
                    row["compare_ok"] = False
                    row["regressions"] = [f"missing_baseline:{args.compare_vs}"]

            results.append(row)
            status = "PASS" if row.get("pass_ok") else "FAIL"
            if args.compare or args.compare_vs:
                status = "PASS" if row.get("compare_ok") else "FAIL"
            print(
                f"  {status} placed={row.get('placed')}/{row.get('expected')} "
                f"efi={row.get('efi_directa'):.2f}% "
                f"solape_ok={row.get('solape_ok')} "
                f"kerf_viol={row.get('kerf_violations')} "
                f"in_holes={row.get('in_holes')} "
                f"ms={row.get('elapsed_ms'):.1f} "
                f"err={row.get('error')!r}"
            )
            if row.get("nfp_cache"):
                cache = row["nfp_cache"]
                print(
                    f"  nfp_cache hits={cache.get('hits')} misses={cache.get('misses')} "
                    f"hit_rate={float(cache.get('hit_rate') or 0.0):.1%} "
                    f"entries={cache.get('entries')}"
                )
            if status != "PASS":
                failed += 1

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"results": results}, f, ensure_ascii=False, indent=2)
        print(f"wrote {args.out}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
