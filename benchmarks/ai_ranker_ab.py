"""A/B ranker L1 + bandit L3-lite vs largest-first (punto 4 IA).

Uso:
  py -3.14 benchmarks/ai_ranker_ab.py
  py -3.14 benchmarks/ai_ranker_ab.py --scenarios s5_tight_order,s2_host_fill
  py -3.14 benchmarks/ai_ranker_ab.py --calibrate-pack
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


def _pack(params, piezas, *, use_ai: bool, ga_pop: int, ga_gen: int, engine: str) -> dict:
    from modules.nesting_engine import arga_nest_core_bridge as bridge
    from modules.nesting_engine.algorithm_bridge import _piece_to_native

    os.environ["ARGA_NEST_AI"] = "1" if use_ai else "0"
    os.environ["ARGA_NEST_AI_BANDIT"] = "1" if use_ai else "0"
    os.environ["ARGA_NEST_WORKER"] = "0"
    if use_ai:
        os.environ.setdefault("ARGA_NEST_AI_EPSILON", "0")
        if not (os.environ.get("ARGA_NEST_AI_POLICY") or "").strip():
            try:
                from modules.nesting_engine.ai_ranker import choose_policy

                os.environ["ARGA_NEST_AI_POLICY"] = choose_policy(epsilon=0.0)
            except Exception:
                pass
    native = [_piece_to_native(p) for p in piezas]
    req = bridge.prepare_pack_request(
        plate_w=float(params["plate_w_in"]) * IN_TO_MM,
        plate_h=float(params["plate_h_in"]) * IN_TO_MM,
        pieces=native,
        kerf=float(params["kerf_in"]),
        margin=float(params.get("margin_in") or 0.0),
        engine=engine,
        profile="first",
        ga_population=ga_pop,
        ga_generations=ga_gen,
        enable_tabu=False,
        rank_order=True,
        extra={
            "preserve_order": True,
            "ga_seed": int(os.environ.get("ARGA_NEST_GA_SEED") or 42),
            "enable_sa_refine": False,
            "hill_climb_iterations": 1 if engine == "burke_blf" and ga_gen <= 1 else max(1, ga_gen),
        },
    )
    t0 = time.perf_counter()
    raw = bridge.pack_sheet_json(req)
    ms = (time.perf_counter() - t0) * 1000.0
    metrics = raw.get("metrics") or {}
    return {
        "ok": bool((raw.get("certify") or {}).get("ok")),
        "placed": int(metrics.get("placed_count") or 0),
        "efi": float(metrics.get("eficiencia") or 0.0),
        "elapsed_ms": round(ms, 2),
        "seed_sample": [p.get("nombre") for p in (req.get("pieces") or [])[:8]],
        "seed_policy": req.get("seed_policy"),
        "engine": engine,
    }


def _pack_median(params, piezas, *, use_ai: bool, ga_pop: int, ga_gen: int, engine: str, trials: int) -> dict:
    """Mediana de efi/placed para amortiguar RNG de burke_blf."""
    runs = [
        _pack(params, piezas, use_ai=use_ai, ga_pop=ga_pop, ga_gen=ga_gen, engine=engine)
        for _ in range(max(1, trials))
    ]
    runs_sorted = sorted(runs, key=lambda r: (r["efi"], r["placed"]))
    mid = runs_sorted[len(runs_sorted) // 2]
    mid = dict(mid)
    mid["trials"] = trials
    mid["efi_mean"] = sum(r["efi"] for r in runs) / len(runs)
    mid["placed_mean"] = sum(r["placed"] for r in runs) / len(runs)
    return mid


def main() -> int:
    os.environ.setdefault("ARGA_NEST_CORE", "1")
    os.environ.setdefault("ARGA_NEST_TELEMETRY", "1")
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--scenarios",
        default="s5_tight_order,s0_micro,s2_host_fill,s3_strips,s4_mixed_qty",
    )
    ap.add_argument("--ga-pop", type=int, default=4)
    ap.add_argument("--ga-gen", type=int, default=2)
    ap.add_argument("--trials", type=int, default=3, help="Trials por brazo (mediana; amortigua RNG burke)")
    ap.add_argument(
        "--engine",
        default="burke_blf",
        help="burke_blf (orden-sensible) o svgnest_ultra",
    )
    ap.add_argument("--out", default=str(_ROOT / "_logs" / "ai_ab_summary.md"))
    ap.add_argument("--skip-calibrate", action="store_true")
    ap.add_argument(
        "--calibrate-pack",
        action="store_true",
        help="Calibrar bandit con packs reales antes del A/B",
    )
    args = ap.parse_args()

    from benchmarks.corpus_loader import list_scenarios, load_scenario
    from modules.nesting_engine.ai_ranker import (
        calibrate_by_pack,
        calibrate_default_corpus,
        train_from_telemetry,
    )
    from modules.nesting_engine.ai_telemetry import summarize

    cal: dict = {}
    if args.calibrate_pack:
        cal = calibrate_by_pack(engine=args.engine)
        print("CALIBRATE_PACK", {k: cal.get(k) for k in ("ok", "best_policy", "policy_scores", "path")}, flush=True)
        if cal.get("ok") and cal.get("best_policy"):
            os.environ["ARGA_NEST_AI_POLICY"] = str(cal["best_policy"])
            os.environ["ARGA_NEST_AI_EPSILON"] = "0"
            print(f"A/B force policy={cal['best_policy']}", flush=True)
    elif not args.skip_calibrate:
        cal = calibrate_default_corpus()
        print("CALIBRATE", {k: cal.get(k) for k in ("ok", "best_proxy", "path")}, flush=True)

    available = set(list_scenarios())
    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip() in available]
    rows = []
    for sid in scenarios:
        print(f"\n=== {sid} ===", flush=True)
        params, piezas = load_scenario(sid)
        pop, gen = args.ga_pop, args.ga_gen
        eng = args.engine
        if sid.startswith("r_") or sid == "s1_single_plate":
            eng = "svgnest_ultra"
            pop, gen = min(pop, 5), min(gen, 3)
        # En stress tight: burke + GA mínimo para exponer orden
        if sid == "s5_tight_order":
            eng = "burke_blf"
            pop, gen = 1, 1
        base = _pack_median(
            params, piezas, use_ai=False, ga_pop=pop, ga_gen=gen, engine=eng, trials=args.trials
        )
        ai = _pack_median(
            params, piezas, use_ai=True, ga_pop=pop, ga_gen=gen, engine=eng, trials=args.trials
        )
        # Comparar por media de trials (más estable que una sola mediana)
        delta_efi = float(ai.get("efi_mean", ai["efi"])) - float(base.get("efi_mean", base["efi"]))
        delta_placed = int(round(float(ai.get("placed_mean", ai["placed"])) - float(base.get("placed_mean", base["placed"]))))
        time_ratio = (ai["elapsed_ms"] / base["elapsed_ms"]) if base["elapsed_ms"] > 1 else 1.0
        row = {
            "scenario": sid,
            "baseline": base,
            "ai": ai,
            "delta_efi": delta_efi,
            "delta_placed": delta_placed,
            "time_ratio": round(time_ratio, 3),
            "order_changed": base["seed_sample"] != ai["seed_sample"],
        }
        rows.append(row)
        print(
            f"  baseline efi={base.get('efi_mean', base['efi']):.2f} placed={base.get('placed_mean', base['placed']):.1f} "
            f"ms={base['elapsed_ms']} seed={base['seed_sample'][:4]}",
            flush=True,
        )
        print(
            f"  ai       efi={ai.get('efi_mean', ai['efi']):.2f} placed={ai.get('placed_mean', ai['placed']):.1f} "
            f"pol={ai.get('seed_policy')} ms={ai['elapsed_ms']} d_efi={delta_efi:+.2f} "
            f"d_pl={delta_placed:+d} t={time_ratio:.2f} order_chg={row['order_changed']}",
            flush=True,
        )

    train = train_from_telemetry(min_events=2)
    print("TRAIN", {k: train.get(k) for k in ("ok", "n_events", "mean_reward", "bandit_updates")}, flush=True)
    tel = summarize()

    win = tie = lose = 0
    for r in rows:
        de, dp = r["delta_efi"], r["delta_placed"]
        # Prioridad: efi (aprovechamiento). placed solo desempata.
        if de > 0.05:
            win += 1
        elif de < -0.05:
            lose += 1
        elif dp > 0:
            win += 1
        elif dp < 0:
            lose += 1
        else:
            tie += 1
    time_ok = all(r["time_ratio"] <= 1.25 or r["baseline"]["elapsed_ms"] < 500 for r in rows)
    mean_delta = sum(r["delta_efi"] for r in rows) / len(rows) if rows else 0.0
    success = bool(rows) and lose == 0 and mean_delta >= -0.05 and time_ok
    # Candidato opt-in (NO cambia default UI automáticamente)
    promote_ui = success and win >= 1 and mean_delta >= 0.5

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# A/B AI ranker+bandit vs largest-first",
        "",
        f"- Generado: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Engine default A/B: `{args.engine}`",
        f"- Telemetría: `{tel}`",
        f"- Calibrate: `{ {k: cal.get(k) for k in ('ok', 'best_policy', 'best_proxy', 'path') if k in cal} }`",
        f"- Train: ok={train.get('ok')} n={train.get('n_events')} bandit_upd={train.get('bandit_updates')}",
        f"- Escenarios: {len(rows)} · trials/brazo: {args.trials}",
        "",
        "| Scenario | base efi | AI efi | d efi | d placed | pol | order | t-ratio |",
        "|----------|----------|--------|-------|----------|-----|-------|---------|",
    ]
    for r in rows:
        b, a = r["baseline"], r["ai"]
        be = float(b.get("efi_mean", b["efi"]))
        ae = float(a.get("efi_mean", a["efi"]))
        lines.append(
            f"| `{r['scenario']}` | {be:.2f} | {ae:.2f} | "
            f"{r['delta_efi']:+.2f} | {r['delta_placed']:+d} | {a.get('seed_policy')} | "
            f"{r['order_changed']} | {r['time_ratio']:.2f} |"
        )
    lines.extend(
        [
            "",
            f"**Win/Tie/Lose (efi|placed):** {win}/{tie}/{lose}",
            f"**Mean d efi:** {mean_delta:+.3f} pp",
            f"**Tiempo <= +25%:** {time_ok}",
            f"**L1/L3 success (empate+):** {success}",
            f"**Promover UI default:** {promote_ui}",
            "",
            "## Veredicto",
            "",
        ]
    )
    if promote_ui:
        lines.append("Mejora medible; candidato opt-in documentado. **No** cambia default UI aquí.")
    elif success:
        lines.append("Empate/aceptable. Ranker+bandit opt-in; no forzar UI default.")
    else:
        lines.append("Sin mejora o regresión. Mantener `ARGA_NEST_AI=0` por default.")
    out.write_text("\n".join(lines), encoding="utf-8")
    (out.with_suffix(".json")).write_text(
        json.dumps(
            {
                "rows": rows,
                "train": train,
                "calibrate": cal,
                "telemetry": tel,
                "win_tie_lose": [win, tie, lose],
                "mean_delta_efi": mean_delta,
                "time_ok": time_ok,
                "success": success,
                "promote_ui": promote_ui,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Wrote", out)
    print(f"VERDICT success={success} promote_ui={promote_ui} W/T/L={win}/{tie}/{lose}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
