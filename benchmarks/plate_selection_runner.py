"""Harness de evidencia: selección de placas (acomodo + costo).

Captura baseline del ANS actual, corre candidatos y aplica gate fail-closed.
Sin mejora fehaciente ⇒ exit code ≠ 0 (no se puede activar el cambio).

Ejemplos:
  python -m benchmarks.plate_selection_runner --list
  python -m benchmarks.plate_selection_runner --case ps_synth_cost_trap --write-baseline
  python -m benchmarks.plate_selection_runner --case ps_synth_cost_trap --compare
  python -m benchmarks.plate_selection_runner --case ps_giga_cal11_x1 --write-baseline
  python -m benchmarks.plate_selection_runner --all --write-baseline
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(_ROOT))

from benchmarks.plate_selection_metrics import (  # noqa: E402
    SCHEMA,
    compare_gate,
    extract_from_nest_result,
)

_CASES_DIR = Path(__file__).resolve().parent / "plate_selection_cases"
_BASELINES_DIR = Path(__file__).resolve().parent / "baselines" / "plate_selection"
_RESULTS_DIR = Path(__file__).resolve().parent / "results_real" / "plate_selection"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__geo_interface__"):
        return str(value)
    return value


def list_cases() -> list[str]:
    out: list[str] = []
    if not _CASES_DIR.is_dir():
        return out
    for path in sorted(_CASES_DIR.glob("*.json")):
        out.append(path.stem)
    return out


def load_case(case_id: str) -> dict[str, Any]:
    path = _CASES_DIR / f"{case_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Caso no encontrado: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def baseline_path(case_id: str, engine_id: str) -> Path:
    safe_engine = str(engine_id).replace("/", "_")
    return _BASELINES_DIR / f"{case_id}__{safe_engine}.json"


def _write_rect_dxf(path: Path, *, w_in: float, h_in: float) -> None:
    """DXF mínimo válido (ezdxf): rectángulo en pulgadas, capa CUT.

    El parser de ANS interpreta coords DXF como pulgadas y las pasa a mm.
    """
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1  # inches
    msp = doc.modelspace()
    w = float(w_in)
    h = float(h_in)
    msp.add_lwpolyline(
        [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h), (0.0, 0.0)],
        close=True,
        dxfattribs={"layer": "CUT"},
    )
    doc.saveas(str(path))


def _plates_from_case(case: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for plate in case.get("plates") or []:
        rows.append(
            [
                str(plate.get("calibre") or case.get("calibre") or ""),
                str(plate.get("material") or case.get("material") or ""),
                str(plate.get("id") or ""),
                plate.get("length_in"),
                plate.get("width_in"),
                plate.get("lb", 0),
                plate.get("mxn", 0),
                plate.get("usd_lb", 0),
                str(plate.get("stock") or "DISPONIBLE"),
                str(plate.get("origen") or "EMPRESA"),
                float(plate.get("usd_lb") or 0.0),
            ]
        )
    return rows


def _parts_from_synthetic(case: dict[str, Any], tmp: Path) -> tuple[list[tuple], int]:
    calibre = str(case.get("calibre") or "0.1196")
    material = str(case.get("material") or "A36")
    parts: list[tuple] = []
    expected = 0
    for index, spec in enumerate(case.get("pieces") or []):
        w_in = float(spec.get("w_in") or 0.0)
        h_in = float(spec.get("h_in") or 0.0)
        qty = max(1, int(spec.get("qty") or 1))
        name = str(spec.get("nombre") or f"P{index}")
        dxf_path = tmp / f"{name}.dxf"
        _write_rect_dxf(dxf_path, w_in=w_in, h_in=h_in)
        parts.append((name, material, str(qty), calibre, "LISTO", str(dxf_path)))
        expected += qty
    return parts, expected


def _parts_from_autodxf(case: dict[str, Any]) -> tuple[list[tuple], int]:
    from benchmarks.run_autodxf_ans_x5 import _build_parts

    source = Path(case["source_dir"])
    if not source.is_absolute():
        source = _ROOT / source
    parts = _build_parts(
        source,
        calibre=str(case.get("calibre") or ""),
        multiplier=max(1, int(case.get("multiplier") or 1)),
        material=str(case.get("material") or ""),
        steel_gauge=int(case.get("steel_gauge") or 0),
    )
    expected = sum(int(p[2]) for p in parts)
    return parts, expected


def _load_plates_snapshot(path: Path) -> list[list[Any]]:
    from modules.sheets_manager import PlatesManager

    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = list(raw.get("empresa") or []) + list(raw.get("proveedor") or [])
    available = PlatesManager().filtrar_placas_para_nesting(
        list(raw.get("empresa") or [])
    )
    # Auto selección real: EMPRESA DISPONIBLE. Si vacío, falla explícito.
    if not available:
        raise ValueError(f"Sin placas DISPONIBLE en snapshot: {path} (empresa={len(rows)})")
    return available


def run_case(
    case_id: str,
    *,
    engine_id: str | None = None,
    label: str = "",
) -> dict[str, Any]:
    from modules.nesting_engine.efficiency_metrics import actualizar_eficiencias_resultados
    from modules.nesting_engine.manager import MotorNesting
    from modules.nesting_engine.nest_poka_yoke import listar_fallas_resultados_nest
    from modules.nesting_engine.plate_selection_probe import plate_selection_probe
    from modules.nesting_engine.sheet_integrity import (
        asegurar_identidad_hojas,
        deduplicar_resultados_nesting,
    )

    case = load_case(case_id)
    resolved_engine = str(engine_id or case.get("engine_id") or "arga_force")
    kind = str(case.get("kind") or "synthetic_multiformat")
    kerf = float(case.get("kerf_in") or 0.25)
    margin = float(case.get("margin_in") or 0.15)
    corner = str(case.get("corner") or "INFERIOR IZQUIERDA")
    opt = str(case.get("opt") or "OPTIMIZAR LARGO Y ANCHO")

    tmp_ctx = tempfile.TemporaryDirectory(prefix=f"arga_ps_{case_id}_")
    try:
        tmp = Path(tmp_ctx.name)
        if kind == "autodxf_real":
            parts, expected = _parts_from_autodxf(case)
            snap = Path(case.get("plates_snapshot") or "cache/herinox_plates_snapshot.json")
            if not snap.is_absolute():
                snap = _ROOT / snap
            plates = _load_plates_snapshot(snap)
        else:
            parts, expected = _parts_from_synthetic(case, tmp)
            plates = _plates_from_case(case)

        motor = MotorNesting()
        started = time.perf_counter()
        probe_file = tmp / "plate_sel_probe.jsonl"
        if probe_file.exists():
            probe_file.unlink()
        previous_probe = os.environ.get("ARGA_PLATE_SEL_PROBE_FILE")
        os.environ["ARGA_PLATE_SEL_PROBE_FILE"] = str(probe_file)
        try:
            with plate_selection_probe(label=label or "run") as probe:
                result = motor.ejecutar_nesting_visual(
                    parts,
                    plates,
                    progress_callback=lambda msg, pct: print(
                        f"[{pct:05.1%}] {msg}", flush=True
                    ),
                    config_kerf=kerf,
                    config_margin=margin,
                    config_corner=corner,
                    config_opt=opt,
                    wo_name=f"PS_EVIDENCE_{case_id}",
                    engine_id=resolved_engine,
                )
        finally:
            if previous_probe is None:
                os.environ.pop("ARGA_PLATE_SEL_PROBE_FILE", None)
            else:
                os.environ["ARGA_PLATE_SEL_PROBE_FILE"] = previous_probe
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        from modules.nesting_engine.plate_selection_probe import load_probe_file

        file_summary = load_probe_file(str(probe_file))
        probe_summary = probe.summary()
        # Preferir JSONL (sobrevive ProcessPool); completar si vacío.
        if int(file_summary.get("sim_candidates_started") or 0) > 0:
            probe_summary = file_summary
            probe_summary["label"] = label or "run"

        failures: list = []
        if isinstance(result, dict) and not result.get("error"):
            deduplicar_resultados_nesting(result, kerf_global=kerf)
            for key, group in result.items():
                if isinstance(group, dict) and isinstance(group.get("hojas"), list):
                    asegurar_identidad_hojas(group["hojas"], clave=str(key))
            actualizar_eficiencias_resultados(result)
            failures = listar_fallas_resultados_nest(result)

        metrics = extract_from_nest_result(
            result if isinstance(result, dict) else {"error": "bad_result"},
            case_id=case_id,
            engine_id=resolved_engine,
            expected_pieces=expected,
            elapsed_ms_total=elapsed_ms,
            probe_summary=probe_summary,
            label=label or "run",
            oracle=dict(case.get("oracle") or {}),
            integrity_failures=failures,
        )
        metrics["case_title"] = str(case.get("title") or case_id)
        metrics["case_kind"] = kind
        metrics["created_at"] = datetime.now(timezone.utc).isoformat()
        metrics["dual_objective"] = ["mejor_acomodo", "menor_impacto_costo"]
        return metrics
    finally:
        tmp_ctx.cleanup()


def write_baseline(metrics: dict[str, Any]) -> Path:
    path = baseline_path(str(metrics["case_id"]), str(metrics["engine_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(metrics)
    payload["baseline_role"] = "production_reference"
    # No guardar probe events gigantes en baseline (mantener summary).
    probe = payload.get("probe")
    if isinstance(probe, dict):
        payload["probe"] = {
            k: probe.get(k)
            for k in (
                "label",
                "sim_candidates_started",
                "sim_candidates_finished",
                "sim_candidates_skipped",
                "sim_winners",
                "sim_elapsed_ms_total",
                "sim_elapsed_ms_max",
                "sim_elapsed_ms_mean",
                "winner_plate_ids",
            )
        }
    path.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_baseline(case_id: str, engine_id: str) -> dict[str, Any]:
    path = baseline_path(case_id, engine_id)
    if not path.is_file():
        raise FileNotFoundError(
            f"Baseline ausente: {path}. Corre primero --write-baseline."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def save_result(metrics: dict[str, Any], *, tag: str) -> Path:
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = _RESULTS_DIR / f"{metrics['case_id']}__{metrics['engine_id']}__{tag}__{stamp}.json"
    path.write_text(
        json.dumps(_json_safe(metrics), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


_MEDIAN_KEYS = (
    "elapsed_ms_total",
    "sim_elapsed_ms_total",
    "cost_total",
    "mean_efi_direct",
    "mean_efi_real",
    "sheet_count",
    "placed_pieces",
    "sim_candidates_started",
    "sim_candidates_finished",
    "sim_candidates_skipped",
)


def _median(values: list[float]) -> float:
    ordered = sorted(float(v) for v in values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def aggregate_runs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Mediana de métricas numéricas; fail-closed si alguna corrida falla."""
    if not rows:
        raise ValueError("Sin corridas para agregar")
    base = dict(rows[-1])
    base["runs"] = len(rows)
    base["pass_ok_all"] = all(bool(r.get("pass_ok")) for r in rows)
    base["integrity_ok_all"] = all(bool(r.get("integrity_ok")) for r in rows)
    base["pass_ok"] = bool(base["pass_ok_all"] and base["integrity_ok_all"])
    for key in _MEDIAN_KEYS:
        base[key] = _median([float(r.get(key) or 0.0) for r in rows])
    # Enteros coherentes
    base["sheet_count"] = int(round(float(base["sheet_count"])))
    base["placed_pieces"] = int(round(float(base["placed_pieces"])))
    base["run_details"] = [
        {
            "pass_ok": r.get("pass_ok"),
            "cost_total": r.get("cost_total"),
            "sheet_count": r.get("sheet_count"),
            "sim_elapsed_ms_total": r.get("sim_elapsed_ms_total"),
            "elapsed_ms_total": r.get("elapsed_ms_total"),
            "winner_plate_ids": r.get("winner_plate_ids"),
        }
        for r in rows
    ]
    return base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evidencia fehaciente de selección de placas (acomodo + costo)"
    )
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--engine", default="")
    parser.add_argument("--label", default="")
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Repeticiones; se reporta mediana (recomendado 3+ para compare).",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Congela la corrida actual como referencia de producción.",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compara corrida vs baseline; FAIL si no hay mejora fehaciente.",
    )
    parser.add_argument(
        "--allow-equal",
        action="store_true",
        help="En --compare, hard_ok basta (sin exigir mejora). Solo para smoke.",
    )
    parser.add_argument(
        "--set-env",
        action="append",
        default=[],
        help="VAR=valor antes de nestear (p.ej. ARGA_PLATE_SEL_PARALLEL=1).",
    )
    args = parser.parse_args(argv)

    if args.list:
        for case_id in list_cases():
            case = load_case(case_id)
            print(f"{case_id}\t{case.get('kind')}\t{case.get('title')}")
        return 0

    case_ids = list(args.case or [])
    if args.all:
        case_ids = list_cases()
    if not case_ids:
        parser.error("Indica --case ID, --all o --list")

    runs = max(1, int(args.runs or 1))
    for item in list(args.set_env or []):
        if "=" not in str(item):
            parser.error(f"--set-env invalido: {item}")
        key, value = str(item).split("=", 1)
        os.environ[str(key).strip()] = str(value).strip()
        print(f"[ENV] {key.strip()}={value.strip()}")
    exit_code = 0
    reports: list[dict[str, Any]] = []
    for case_id in case_ids:
        case = load_case(case_id)
        engine = str(args.engine or case.get("engine_id") or "arga_force")
        rows: list[dict[str, Any]] = []
        for index in range(runs):
            print(f"== RUN {case_id} / {engine} / {index + 1}/{runs} ==")
            rows.append(
                run_case(case_id, engine_id=engine, label=args.label or "candidate")
            )
        metrics = aggregate_runs(rows) if runs > 1 else rows[0]
        result_path = save_result(
            metrics, tag="baseline" if args.write_baseline else "run"
        )
        print(
            json.dumps(
                {
                    "case_id": metrics["case_id"],
                    "runs": runs,
                    "pass_ok": metrics["pass_ok"],
                    "oracle_ok": metrics["oracle_ok"],
                    "cost_total": metrics["cost_total"],
                    "sheet_count": metrics["sheet_count"],
                    "placed_pieces": metrics["placed_pieces"],
                    "expected_pieces": metrics["expected_pieces"],
                    "mean_efi_direct": metrics["mean_efi_direct"],
                    "sim_elapsed_ms_total": metrics["sim_elapsed_ms_total"],
                    "elapsed_ms_total": metrics["elapsed_ms_total"],
                    "winner_plate_ids": metrics["winner_plate_ids"],
                    "integrity_ok": metrics["integrity_ok"],
                    "error": metrics["error"],
                    "result": str(result_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        if args.write_baseline:
            if not metrics.get("pass_ok"):
                print(
                    "BASELINE RECHAZADA: la corrida no paso pass_ok "
                    "(integridad/piezas/oracle)."
                )
                exit_code = 2
            else:
                bpath = write_baseline(metrics)
                print(f"BASELINE OK -> {bpath}")

        if args.compare:
            baseline = load_baseline(case_id, engine)
            gate = compare_gate(
                baseline,
                metrics,
                require_improvement=not bool(args.allow_equal),
            )
            print(json.dumps(gate, ensure_ascii=False, indent=2))
            reports.append({"case_id": case_id, "gate": gate, "metrics": metrics})
            if not gate.get("activate_ok"):
                exit_code = 3

        if not metrics.get("pass_ok") and not args.compare:
            exit_code = max(exit_code, 2)

    if args.out and reports:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "reports": _json_safe(reports),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(out)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
