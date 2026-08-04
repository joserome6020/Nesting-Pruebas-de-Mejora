"""Gate explícito para decidir si procede entrenar un ranker de siembra ML."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


MINIMUM_INSTANCES = 30


def deterministic_seed_order(pieces: list[dict[str, Any]]) -> list[int]:
    """Baseline estable: anfitrionas → área descendente → nombre → índice."""
    def key(item: tuple[int, dict[str, Any]]) -> tuple:
        index, piece = item
        feature = piece.get("features") or {}
        is_host = int(feature.get("holes") or 0) > 0
        return (
            0 if is_host else 1,
            -float(feature.get("area_mm2") or 0.0),
            str(piece.get("nombre") or ""),
            index,
        )

    return [index for index, _ in sorted(enumerate(pieces), key=key)]


def decide(dataset: dict[str, Any]) -> dict[str, Any]:
    instances = [
        instance
        for instance in (dataset.get("instances") or [])
        if instance.get("status") == "labeled"
    ]
    sample_orders = {
        str(instance.get("scenario") or ""): deterministic_seed_order(
            list(instance.get("pieces") or [])
        )
        for instance in instances
    }
    if len(instances) < MINIMUM_INSTANCES:
        return {
            "schema": "arga_ml_gate_v1",
            "decision": "defer_ml",
            "reason": "insufficient_labeled_diverse_instances",
            "labeled_instances": len(instances),
            "minimum_instances": MINIMUM_INSTANCES,
            "baseline": "hosts_then_area_desc_then_stable_tiebreak",
            "baseline_seed_orders": sample_orders,
            "model_trained": False,
        }
    return {
        "schema": "arga_ml_gate_v1",
        "decision": "training_required",
        "reason": "dataset_threshold_reached; run offline ranker experiment",
        "labeled_instances": len(instances),
        "minimum_instances": MINIMUM_INSTANCES,
        "baseline": "hosts_then_area_desc_then_stable_tiebreak",
        "baseline_seed_orders": sample_orders,
        "model_trained": False,
        "required_success_gate": {
            "efficiency_gain_pp": 2.0,
            "or_time_reduction": 0.2,
            "inference_ms_per_plate_max": 10.0,
            "fail_closed_regressions": 0,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decisión go/no-go de ML")
    parser.add_argument("--dataset", default="benchmarks/results_real/ml_seed_dataset.json")
    parser.add_argument("--out", default="benchmarks/results_real/ml_decision.json")
    args = parser.parse_args(argv)

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    report = decide(dataset)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0 if report["decision"] == "training_required" else 2


if __name__ == "__main__":
    raise SystemExit(main())
