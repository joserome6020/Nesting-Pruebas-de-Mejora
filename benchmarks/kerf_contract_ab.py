"""A/B contrato kerf: legacy vs identity vs physical_mm sobre corpus real.

Uso:
  python benchmarks/kerf_contract_ab.py
  python benchmarks/kerf_contract_ab.py --scenarios s0_micro,s1_single_plate
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

IN_TO_MM = 25.4
CONTRACTS = ("legacy", "identity", "physical_mm")


def _pack(params: dict, piezas: list[dict], contract: str) -> dict[str, Any]:
    from modules.nesting_engine import arga_nest_core as core
    from modules.nesting_engine.algorithm_bridge import _piece_to_native
    from modules.nesting_engine import arga_nest_core_bridge as bridge

    os.environ["ARGA_NEST_KERF_CONTRACT"] = contract
    native = [_piece_to_native(p) for p in piezas]
    kerf_in = float(params["kerf_in"])
    req = bridge.prepare_pack_request(
        plate_w=float(params["plate_w_in"]) * IN_TO_MM,
        plate_h=float(params["plate_h_in"]) * IN_TO_MM,
        pieces=native,
        kerf=kerf_in,
        margin=float(params.get("margin_in") or 0.0),
        profile="first",
        ga_population=8,
        ga_generations=6,
        enable_tabu=False,
        certify=True,
        rank_order=True,
    )
    t0 = time.perf_counter()
    raw = core.pack_sheet_json(json.dumps(req))
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    result = json.loads(raw)
    metrics = result.get("metrics") or {}
    certify = result.get("certify") or {}
    placed = int(metrics.get("placed_count") or 0)
    expected = len(piezas)
    require_full = bool(params.get("require_full_place", True))
    place_ok = (placed == expected) if require_full else (placed >= 1)
    return {
        "contract": contract,
        "kerf_sent": req.get("kerf"),
        "kerf_mm_sent": req.get("kerf_mm"),
        "kerf_used": result.get("kerf_used"),
        "elapsed_ms": round(elapsed_ms, 2),
        "placed": f"{placed}/{expected}",
        "placed_count": placed,
        "expected": expected,
        "efi": float(metrics.get("eficiencia") or 0.0),
        "min_gap_mm": float(metrics.get("min_gap_mm") or 0.0),
        "common_line_mm": float(metrics.get("common_line_mm") or 0.0),
        "pierce_saved": int(metrics.get("pierce_saved") or 0),
        "certify_ok": bool(certify.get("ok")),
        "ok": place_ok and bool(certify.get("ok")),
        "features": result.get("features"),
    }


def main() -> int:
    os.environ.setdefault("ARGA_NEST_CORE", "1")
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default="s0_micro,s1_single_plate,r_1000kva_critical")
    ap.add_argument(
        "--out",
        default=str(_ROOT / "_logs" / "kerf_contract_ab.json"),
    )
    args = ap.parse_args()

    from modules.nesting_engine import arga_nest_core_bridge as bridge
    from benchmarks.corpus_loader import list_scenarios, load_scenario

    st = bridge.core_status()
    print("CORE", st)
    available = set(list_scenarios())
    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip() and s.strip() in available]
    print("SCENARIOS", scenarios)

    rows = []
    for sid in scenarios:
        print(f"\n=== {sid} ===", flush=True)
        params, piezas = load_scenario(sid)
        case = {"scenario": sid, "kerf_in": params.get("kerf_in"), "variants": {}}
        for contract in CONTRACTS:
            try:
                v = _pack(params, piezas, contract)
            except Exception as ex:
                v = {"contract": contract, "ok": False, "error": str(ex)}
            case["variants"][contract] = v
            print(
                f"  {contract}: ok={v.get('ok')} placed={v.get('placed')} "
                f"efi={v.get('efi')} kerf_used={v.get('kerf_used')} "
                f"gap={v.get('min_gap_mm')} ms={v.get('elapsed_ms')} "
                f"err={v.get('error')}",
                flush=True,
            )
        leg = case["variants"].get("legacy") or {}
        phy = case["variants"].get("physical_mm") or {}
        ident = case["variants"].get("identity") or {}
        case["verdict"] = {
            "identity_matches_legacy": (
                bool(leg.get("ok"))
                and bool(ident.get("ok"))
                and int(leg.get("placed_count") or -1) == int(ident.get("placed_count") or -2)
            ),
            "physical_diverges": (
                int(leg.get("placed_count") or 0) != int(phy.get("placed_count") or -1)
                or bool(leg.get("ok")) != bool(phy.get("ok"))
            ),
            "recommend": "identity"
            if (leg.get("ok") and ident.get("ok"))
            else ("legacy" if leg.get("ok") else "investigate"),
        }
        print("  VERDICT", case["verdict"], flush=True)
        rows.append(case)

    # Restore default
    os.environ.pop("ARGA_NEST_KERF_CONTRACT", None)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "core": st,
        "note": (
            "legacy: kerf numérico de planta (pulgadas) sobre geometría mm. "
            "identity: marca kerf_mm=kerf (mismo valor). "
            "physical_mm: kerf_mm=kerf_in*25.4 (kerf físico real)."
        ),
        "rows": rows,
        "summary": {
            "scenarios": len(rows),
            "legacy_pass": sum(1 for r in rows if (r["variants"].get("legacy") or {}).get("ok")),
            "identity_pass": sum(
                1 for r in rows if (r["variants"].get("identity") or {}).get("ok")
            ),
            "physical_pass": sum(
                1 for r in rows if (r["variants"].get("physical_mm") or {}).get("ok")
            ),
            "recommend_identity": sum(
                1 for r in rows if (r.get("verdict") or {}).get("recommend") == "identity"
            ),
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    md = out.with_suffix(".md")
    lines = [
        "# A/B contrato kerf — ANS C++",
        "",
        f"- Generado: `{report['generated_at']}`",
        f"- Core: `{st.get('version')}`",
        f"- Legacy PASS: **{report['summary']['legacy_pass']}/{len(rows)}**",
        f"- Identity PASS: **{report['summary']['identity_pass']}/{len(rows)}**",
        f"- Physical_mm PASS: **{report['summary']['physical_pass']}/{len(rows)}**",
        "",
        report["note"],
        "",
        "| Scenario | legacy | identity | physical_mm | recommend |",
        "|----------|--------|----------|-------------|-----------|",
    ]
    for r in rows:
        def cell(name: str) -> str:
            v = r["variants"].get(name) or {}
            if "error" in v:
                return f"ERR"
            ok = "PASS" if v.get("ok") else "FAIL"
            return f"{ok} {v.get('placed')} efi={v.get('efi')}"

        lines.append(
            f"| `{r['scenario']}` | {cell('legacy')} | {cell('identity')} | "
            f"{cell('physical_mm')} | {(r.get('verdict') or {}).get('recommend')} |"
        )
    lines.append("")
    lines.append(f"JSON: `{out}`")
    md.write_text("\n".join(lines), encoding="utf-8")
    print("\nWrote", out)
    print("Wrote", md)

    # Exit 0 if legacy still healthy (physical may fail by design)
    return 0 if report["summary"]["legacy_pass"] == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
