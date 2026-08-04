"""Genera etiquetas de orden de siembra a partir de corridas multi-semilla.

Nunca deduce el orden desde las posiciones finales: persiste exclusivamente el
`seed_order` devuelto por el motor nativo para la mejor semilla auditada.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from benchmarks.corpus_loader import load_scenario
from benchmarks.runner import IN_TO_MM, evaluate_run


def _piece_features(piece: dict) -> dict[str, float | int]:
    poly = piece.get("poly")
    if poly is None or poly.is_empty:
        return {"area_mm2": 0.0, "perimeter_mm": 0.0, "vertices": 0, "holes": 0}
    min_x, min_y, max_x, max_y = poly.bounds
    width = max(0.0, float(max_x - min_x))
    height = max(0.0, float(max_y - min_y))
    return {
        "area_mm2": float(poly.area),
        "perimeter_mm": float(poly.length),
        "vertices": max(0, len(poly.exterior.coords) - 1),
        "holes": len(poly.interiors),
        "width_mm": width,
        "height_mm": height,
        "aspect_ratio": (max(width, height) / min(width, height)) if min(width, height) else 0.0,
    }


def _run_seed(
    pieces: list[dict],
    params: dict[str, Any],
    seed: int,
) -> tuple[dict[str, Any], list[int]]:
    from modules.nesting_engine import algorithm_cpp
    from modules.nesting_engine.algorithm_bridge import _assemble_pack_result, _piece_to_native

    native = [_piece_to_native(piece) for piece in pieces]
    started = time.perf_counter()
    raw = algorithm_cpp.empaquetar_una_hoja_svgnest_ultra(
        piezas=native,
        w_placa=float(params["plate_w_in"]) * IN_TO_MM,
        h_placa=float(params["plate_h_in"]) * IN_TO_MM,
        kerf_override=float(params["kerf_in"]),
        margin_override=float(params["margin_in"]),
        opt_override=str(params["opt"]),
        corner_override=str(params["corner"]),
        limite_rings=None,
        ga_population=8,
        ga_generations=2,
        rotation_step_deg=90.0,
        part_in_part=True,
        ga_seed=int(seed),
        seed_order=None,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        raise RuntimeError("El algoritmo nativo no devolvió seed_order; etiqueta inválida.")
    hoja, restos = _assemble_pack_result(raw[0], raw[1], pieces)
    row = evaluate_run(
        scenario=str(params["scenario"]),
        engine_id="svgnest_ultra_seed_label",
        hoja=hoja,
        restos=restos,
        expected=len(pieces),
        elapsed_ms=elapsed_ms,
        kerf_in=float(params["kerf_in"]),
        require_full_place=bool(params.get("require_full_place", True)),
    )
    order = [int(value) for value in (raw[2] or [])]
    if len(order) != len(pieces) or sorted(order) != list(range(len(pieces))):
        raise RuntimeError("seed_order nativo incompleto o inválido; etiqueta descartada.")
    return row, order


def generate_dataset(scenarios: list[str], seeds: int) -> dict[str, Any]:
    instances: list[dict[str, Any]] = []
    for scenario_id in scenarios:
        params, pieces = load_scenario(scenario_id)
        candidates: list[dict[str, Any]] = []
        for seed in range(1, seeds + 1):
            row, order = _run_seed(pieces, params, seed)
            candidates.append(
                {
                    "seed": seed,
                    "metrics": row,
                    "seed_order": order,
                }
            )
        valid = [candidate for candidate in candidates if candidate["metrics"].get("pass_ok")]
        if not valid:
            instances.append(
                {
                    "scenario": scenario_id,
                    "status": "no_valid_seed",
                    "candidates": candidates,
                }
            )
            continue
        winner = max(
            valid,
            key=lambda item: (
                int(item["metrics"].get("placed") or 0),
                float(item["metrics"].get("efi_directa") or 0.0),
                -float(item["metrics"].get("elapsed_ms") or 0.0),
            ),
        )
        instances.append(
            {
                "scenario": scenario_id,
                "status": "labeled",
                "plate": {
                    key: params[key]
                    for key in ("plate_w_in", "plate_h_in", "kerf_in", "margin_in")
                },
                "pieces": [
                    {
                        "index": index,
                        "nombre": str(piece.get("nombre") or ""),
                        "calibre": str(piece.get("calibre") or ""),
                        "material": str(piece.get("material") or ""),
                        "features": _piece_features(piece),
                    }
                    for index, piece in enumerate(pieces)
                ],
                "winner": winner,
                "candidates": candidates,
            }
        )
    labeled = sum(1 for instance in instances if instance.get("status") == "labeled")
    return {
        "schema": "arga_seed_dataset_v1",
        "instances": instances,
        "labeled_instances": labeled,
        "minimum_instances_for_training": 30,
        "training_ready": labeled >= 30,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Etiquetas seed_order multi-semilla")
    parser.add_argument("--scenario", action="append", required=True)
    parser.add_argument("--seeds", type=int, default=6)
    parser.add_argument("--out", default="benchmarks/results_real/ml_seed_dataset.json")
    args = parser.parse_args(argv)

    report = generate_dataset(list(args.scenario), max(1, int(args.seeds)))
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0 if report["training_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
