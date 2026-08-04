"""Piloto comparación ANS C++ vs baseline corpus (proxy de WO).

Uso:
  python benchmarks/pilot_wo_compare.py
  python benchmarks/pilot_wo_compare.py --scenarios s0_micro,r_1000kva_critical

Para WO real de planta: seguir ``_logs/PILOT_WO_CHECKLIST.md``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

IN_TO_MM = 25.4


def _run(params, piezas) -> dict:
    from modules.nesting_engine import arga_nest_core_bridge as bridge
    from modules.nesting_engine.algorithm_bridge import _piece_to_native

    native = [_piece_to_native(p) for p in piezas]
    req = bridge.prepare_pack_request(
        plate_w=float(params["plate_w_in"]) * IN_TO_MM,
        plate_h=float(params["plate_h_in"]) * IN_TO_MM,
        pieces=native,
        kerf=float(params["kerf_in"]),
        margin=float(params.get("margin_in") or 0.0),
        profile="first",
        ga_population=8,
        ga_generations=6,
        enable_tabu=True,
        tabu_seed_trials=2,
    )
    t0 = time.perf_counter()
    raw = bridge.pack_sheet_json(req)
    ms = (time.perf_counter() - t0) * 1000.0
    metrics = raw.get("metrics") or {}
    certify = raw.get("certify") or {}
    return {
        "ok": bool(certify.get("ok")),
        "placed": f"{int(metrics.get('placed_count') or 0)}/{len(piezas)}",
        "placed_count": int(metrics.get("placed_count") or 0),
        "efi": float(metrics.get("eficiencia") or 0.0),
        "common_line_mm": float(metrics.get("common_line_mm") or 0.0),
        "pierce_saved": int(metrics.get("pierce_saved") or 0),
        "elapsed_ms": round(ms, 2),
        "kerf_used": raw.get("kerf_used"),
        "features": raw.get("features"),
    }


def main() -> int:
    os.environ.setdefault("ARGA_NEST_CORE", "1")
    os.environ.setdefault("ARGA_NEST_WORKER", "0")
    os.environ.setdefault("ARGA_NEST_TELEMETRY", "1")
    os.environ.setdefault("ARGA_NEST_AI", "0")

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--scenarios",
        default="s0_micro,s1_single_plate,r_1000kva_critical",
    )
    ap.add_argument(
        "--out",
        default=str(_ROOT / "_logs" / "pilot_wo_compare.md"),
    )
    args = ap.parse_args()

    from benchmarks.corpus_loader import list_scenarios, load_scenario
    from modules.nesting_engine import arga_nest_core_bridge as bridge

    st = bridge.core_status()
    print("CORE", st)
    available = set(list_scenarios())
    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip() in available]
    rows = []
    for sid in scenarios:
        print(f"\n=== PILOT {sid} ===", flush=True)
        params, piezas = load_scenario(sid)
        try:
            r = _run(params, piezas)
            r["scenario"] = sid
            r["error"] = None
        except Exception as ex:
            r = {"scenario": sid, "ok": False, "error": str(ex)}
        rows.append(r)
        print(r, flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Piloto ANS C++ (proxy corpus)",
        "",
        f"- Generado: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Core: `{st.get('version')}` kerf=`{st.get('kerf_contract')}` worker=`{(st.get('worker') or {}).get('active')}`",
        "",
        "Checklist WO real: `_logs/PILOT_WO_CHECKLIST.md`",
        "",
        "| Scenario | OK | Placed | Efi | common_line | pierce_saved | ms |",
        "|----------|----|--------|-----|-------------|--------------|----|",
    ]
    for r in rows:
        if r.get("error"):
            lines.append(f"| `{r['scenario']}` | ERR | - | - | - | - | {r['error'][:40]} |")
        else:
            lines.append(
                f"| `{r['scenario']}` | {r.get('ok')} | {r.get('placed')} | "
                f"{r.get('efi'):.2f} | {r.get('common_line_mm')} | {r.get('pierce_saved')} | "
                f"{r.get('elapsed_ms')} |"
            )
    pass_n = sum(1 for r in rows if r.get("ok"))
    lines.append("")
    lines.append(f"**PASS:** {pass_n}/{len(rows)}")
    out.write_text("\n".join(lines), encoding="utf-8")
    out.with_suffix(".json").write_text(
        json.dumps({"core": st, "rows": rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print("Wrote", out)
    return 0 if pass_n == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
