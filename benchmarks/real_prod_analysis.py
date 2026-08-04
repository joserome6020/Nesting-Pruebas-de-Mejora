"""Análisis y debug de pruebas reales — ArgaNestCore vs corpus.

Uso:
  python benchmarks/real_prod_analysis.py
  python benchmarks/real_prod_analysis.py --scenarios s0_micro,s1_single_plate,r_1000kva_critical
  python benchmarks/real_prod_analysis.py --all-available
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

IN_TO_MM = 25.4


def _rings_from_piece(piece: dict) -> list:
    from modules.nesting_engine.algorithm_bridge import _piece_to_native

    return _piece_to_native(piece)["rings"]


def _pack_with_core(params: dict, piezas: list[dict]) -> dict[str, Any]:
    from modules.nesting_engine import arga_nest_core as core
    from modules.nesting_engine.algorithm_bridge import _piece_to_native

    native = [_piece_to_native(p) for p in piezas]
    w_mm = float(params["plate_w_in"]) * IN_TO_MM
    h_mm = float(params["plate_h_in"]) * IN_TO_MM
    # Paridad con algorithm_bridge/sim_lab: placa en mm, kerf/margin en pulgadas numéricas.
    kerf = float(params["kerf_in"])
    margin = float(params.get("margin_in") or 0.0)
    req = {
        "engine": "svgnest_ultra",
        "profile": "first",
        "plate_w": w_mm,
        "plate_h": h_mm,
        "kerf": kerf,
        "margin": margin,
        "certify": True,
        "pieces": native,
    }
    t0 = time.perf_counter()
    raw = core.pack_sheet_json(json.dumps(req))
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    result = json.loads(raw)
    return {"elapsed_ms": elapsed_ms, "request": req, "result": result}


def _analyze_case(scenario_id: str) -> dict[str, Any]:
    from benchmarks.corpus_loader import load_scenario

    out: dict[str, Any] = {
        "scenario": scenario_id,
        "ok": False,
        "errors": [],
        "warnings": [],
    }
    try:
        params, piezas = load_scenario(scenario_id)
    except Exception as ex:
        out["errors"].append(f"load_scenario: {ex}")
        out["traceback"] = traceback.format_exc()
        return out

    out["params"] = {
        k: params[k]
        for k in (
            "plate_w_in",
            "plate_h_in",
            "kerf_in",
            "margin_in",
            "require_full_place",
            "level",
            "source_kind",
            "notes",
        )
        if k in params
    }
    out["piece_count"] = len(piezas)
    out["piece_names_sample"] = [str(p.get("nombre")) for p in piezas[:8]]

    # Sanity geometría
    bad_geom = 0
    for p in piezas:
        poly = p.get("poly")
        if poly is None or getattr(poly, "is_empty", True):
            bad_geom += 1
            continue
        if not getattr(poly, "is_valid", True):
            bad_geom += 1
    if bad_geom:
        out["warnings"].append(f"geoms_invalidas={bad_geom}")

    try:
        pack = _pack_with_core(params, piezas)
    except Exception as ex:
        out["errors"].append(f"pack_core: {ex}")
        out["traceback"] = traceback.format_exc()
        return out

    result = pack["result"]
    metrics = result.get("metrics") or {}
    certify = result.get("certify") or {}
    placed = int(metrics.get("placed_count") or 0)
    expected = len(piezas)
    leftovers = result.get("leftovers") or []

    out["elapsed_ms"] = round(pack["elapsed_ms"], 2)
    out["placed"] = f"{placed}/{expected}"
    out["placed_count"] = placed
    out["expected"] = expected
    out["leftover_count"] = len(leftovers)
    out["efi"] = float(metrics.get("eficiencia") or 0.0)
    out["min_gap_mm"] = float(metrics.get("min_gap_mm") or 0.0)
    out["certify_ok"] = bool(certify.get("ok"))
    out["certify_issues"] = certify.get("issues") or []
    out["ok_field"] = bool(result.get("ok"))

    require_full = bool(params.get("require_full_place", True))
    place_ok = (placed == expected) if require_full else (placed >= 1)
    if not place_ok:
        out["errors"].append(
            f"placement: placed={placed} expected={expected} require_full={require_full}"
        )
    if not out["certify_ok"]:
        out["errors"].append(f"certify_fail: {out['certify_issues']}")

    # Debug extra: áreas vs placa
    plate_area = (
        float(params["plate_w_in"]) * IN_TO_MM * float(params["plate_h_in"]) * IN_TO_MM
    )
    used = float(metrics.get("area_usada") or 0.0)
    out["plate_area_mm2"] = plate_area
    out["area_usada"] = used
    if used > plate_area * 1.01:
        out["warnings"].append("area_usada > plate_area (revisar unidades)")

    out["ok"] = place_ok and out["certify_ok"] and not out["errors"]
    return out


def _default_scenarios() -> list[str]:
    # Sintéticos + reales con geometría embebida disponibles offline
    candidates = [
        "s0_micro",
        "s1_single_plate",
        "r_1000kva_critical",
        "r_2500kva_x29_critical",
        "r_2500kva_x30_critical",
    ]
    from benchmarks.corpus_loader import list_scenarios

    available = set(list_scenarios())
    return [s for s in candidates if s in available]


def main() -> int:
    os.environ.setdefault("ARGA_NEST_CORE", "1")
    os.environ.setdefault("ARGA_NEST_CUDA", "1")

    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default="", help="csv de scenario ids")
    ap.add_argument("--all-available", action="store_true")
    ap.add_argument(
        "--out",
        default=str(_ROOT / "_logs" / "real_prod_analysis.json"),
    )
    args = ap.parse_args()

    from modules.nesting_engine import arga_nest_core_bridge as bridge
    from modules.nesting_engine import arga_nest_core as core

    st = bridge.core_status()
    cuda = core.cuda_status()
    print("CORE", st)
    print("CUDA", cuda)
    if not st.get("module_loaded"):
        print("FAIL: arga_nest_core no cargado")
        return 2

    from benchmarks.corpus_loader import list_scenarios

    if args.all_available:
        scenarios = list_scenarios()
    elif args.scenarios.strip():
        scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    else:
        scenarios = _default_scenarios()

    print("SCENARIOS", scenarios)
    rows = []
    for sid in scenarios:
        print(f"\n=== {sid} ===", flush=True)
        row = _analyze_case(sid)
        rows.append(row)
        status = "PASS" if row.get("ok") else "FAIL"
        print(
            f"{status} placed={row.get('placed')} efi={row.get('efi')} "
            f"certify={row.get('certify_ok')} ms={row.get('elapsed_ms')} "
            f"err={row.get('errors')}",
            flush=True,
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "core": st,
        "cuda": cuda,
        "codesign_manifest": None,
        "rows": rows,
        "summary": {
            "total": len(rows),
            "pass": sum(1 for r in rows if r.get("ok")),
            "fail": sum(1 for r in rows if not r.get("ok")),
        },
    }
    man = _ROOT / "native" / "codesign" / "signed_manifest.json"
    if man.is_file():
        report["codesign_manifest"] = json.loads(man.read_text(encoding="utf-8-sig"))

    # Reintentos con profile=standard solo si first falló por placement (no por certify PIP)
    for row in rows:
        if row.get("ok"):
            continue
        errs = " ".join(row.get("errors") or [])
        if "placement" not in errs:
            # Certify overlap en PIP se diagnostica aparte; no quemar 10min en retry
            row["debug_note"] = "skip_retry_standard (no placement error)"
            continue
        sid = row.get("scenario")
        if not sid:
            continue
        try:
            from benchmarks.corpus_loader import load_scenario

            params, piezas = load_scenario(sid)
            from modules.nesting_engine import arga_nest_core as core
            from modules.nesting_engine.algorithm_bridge import _piece_to_native

            native = [_piece_to_native(p) for p in piezas]
            req = {
                "engine": "svgnest_ultra",
                "profile": "standard",
                "plate_w": float(params["plate_w_in"]) * IN_TO_MM,
                "plate_h": float(params["plate_h_in"]) * IN_TO_MM,
                "kerf": float(params["kerf_in"]),
                "margin": float(params.get("margin_in") or 0.0),
                "certify": True,
                "pieces": native,
            }
            t0 = time.perf_counter()
            raw = core.pack_sheet_json(json.dumps(req))
            elapsed = (time.perf_counter() - t0) * 1000.0
            result = json.loads(raw)
            metrics = result.get("metrics") or {}
            certify = result.get("certify") or {}
            placed = int(metrics.get("placed_count") or 0)
            expected = len(piezas)
            require_full = bool(params.get("require_full_place", True))
            place_ok = (placed == expected) if require_full else (placed >= 1)
            retry = {
                "profile": "standard",
                "placed": f"{placed}/{expected}",
                "placed_count": placed,
                "efi": float(metrics.get("eficiencia") or 0.0),
                "certify_ok": bool(certify.get("ok")),
                "elapsed_ms": round(elapsed, 2),
                "ok": place_ok and bool(certify.get("ok")),
            }
            row["retry_standard"] = retry
            print(
                f"  RETRY standard: placed={retry['placed']} certify={retry['certify_ok']} "
                f"ms={retry['elapsed_ms']} ok={retry['ok']}",
                flush=True,
            )
            if retry["ok"]:
                row["ok"] = True
                row["errors"] = [e for e in (row.get("errors") or []) if "placement" not in e]
                row["placed"] = retry["placed"]
                row["placed_count"] = placed
                row["efi"] = retry["efi"]
                row["elapsed_ms"] = retry["elapsed_ms"]
                row["profile_used"] = "standard"
        except Exception as ex:
            row["retry_standard_error"] = str(ex)

    report["summary"] = {
        "total": len(rows),
        "pass": sum(1 for r in rows if r.get("ok")),
        "fail": sum(1 for r in rows if not r.get("ok")),
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Markdown resumen
    md = out_path.with_suffix(".md")
    lines = [
        "# Análisis pruebas reales — ANS C++",
        "",
        f"- Generado: `{report['generated_at']}`",
        f"- Core: `{st.get('version')}`",
        f"- CUDA build/runtime: `{cuda.get('build_has_cuda')}` / `{cuda.get('runtime_available')}`",
        f"- Resumen: **{report['summary']['pass']}/{report['summary']['total']} PASS**",
        "",
        "| Scenario | Result | Placed | Efi % | Certify | ms | Errors |",
        "|----------|--------|--------|-------|---------|----|--------|",
    ]
    for r in rows:
        efi = r.get("efi")
        efi_s = f"{efi:.2f}" if isinstance(efi, (int, float)) else "-"
        lines.append(
            f"| `{r.get('scenario')}` | {'PASS' if r.get('ok') else 'FAIL'} | "
            f"{r.get('placed') or '-'} | {efi_s} | {r.get('certify_ok')} | "
            f"{r.get('elapsed_ms') or '-'} | {'; '.join(r.get('errors') or []) or '-'} |"
        )
    lines.append("")
    lines.append(f"JSON completo: `{out_path}`")
    md.write_text("\n".join(lines), encoding="utf-8")
    print("\nWrote", out_path)
    print("Wrote", md)
    return 0 if report["summary"]["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
