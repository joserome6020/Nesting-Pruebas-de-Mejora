"""Captura un escenario .nestsim con sus DXF en un corpus local.

El manifest guarda solo rutas relativas y hashes; no conserva rutas UNC ni de
OneDrive. Los DXF son artefactos locales ignorados por git.

Uso:
  python -m benchmarks.capture_real_corpus \
    --scenario-source _logs/sim_plc152_p3/escenario.nestsim.json \
    --case r_giga --scenario-id r_giga_plc152
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


_BENCHMARKS = Path(__file__).resolve().parent
_ROOT = _BENCHMARKS.parent


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def capture_nestsim(
    *,
    source_path: Path,
    case_id: str,
    scenario_id: str,
    overwrite: bool,
) -> Path:
    data = _read_json(source_path)
    destination = _BENCHMARKS / "corpus_real" / case_id
    dxf_destination = destination / "dxf"
    if destination.exists() and not overwrite:
        raise FileExistsError(
            f"{destination} ya existe. Usa --overwrite para regenerar el snapshot."
        )
    dxf_destination.mkdir(parents=True, exist_ok=True)

    captured: list[dict[str, Any]] = []
    scenario_pieces: list[dict[str, Any]] = []
    missing: list[str] = []
    used_names: set[str] = set()

    for piece in data.get("pieces") or []:
        if not isinstance(piece, dict):
            continue
        source_dxf = Path(str(piece.get("ruta") or ""))
        if not source_dxf.is_file():
            missing.append(str(source_dxf))
            continue

        filename = source_dxf.name
        if filename.lower() in used_names:
            stem, suffix = source_dxf.stem, source_dxf.suffix
            index = 2
            while f"{stem}__{index}{suffix}".lower() in used_names:
                index += 1
            filename = f"{stem}__{index}{suffix}"
        used_names.add(filename.lower())

        local_dxf = dxf_destination / filename
        shutil.copy2(source_dxf, local_dxf)
        captured.append(
            {
                "file": f"dxf/{filename}",
                "sha256": _sha256(local_dxf),
                "bytes": local_dxf.stat().st_size,
                "nombre": str(piece.get("nombre") or ""),
                "qty": int(piece.get("qty") or 1),
            }
        )

        local_piece = dict(piece)
        local_piece["ruta"] = f"dxf/{filename}"
        local_piece.pop("ref_image", None)
        scenario_pieces.append(local_piece)

    if missing:
        raise FileNotFoundError(
            "No se capturó el corpus porque faltan DXF fuente:\n- "
            + "\n- ".join(missing)
        )

    local_scenario = {
        "version": 1,
        "scenario": scenario_id,
        "level": "REAL",
        "source_kind": "nestsim_snapshot",
        "plate_w_in": float(data.get("plate_w_in") or 0),
        "plate_h_in": float(data.get("plate_h_in") or 0),
        "kerf_in": float(data.get("kerf_in") or 0.25),
        "margin_in": float(data.get("margin_in") or 0.15),
        "corner": str(data.get("corner") or "INFERIOR IZQUIERDA"),
        "opt": str(data.get("opt") or "OPTIMIZAR LARGO Y ANCHO"),
        "nest_mode": str(data.get("nest_mode") or "standard"),
        "mc_iterations": int(data.get("mc_iterations") or 1),
        "require_full_place": True,
        "notes": str(data.get("notes") or ""),
        "pieces": scenario_pieces,
    }
    manifest = {
        "case_id": case_id,
        "scenario": scenario_id,
        "source_kind": "nestsim_snapshot",
        "dxf_count": len(captured),
        "files": captured,
    }
    with (destination / "scenario.nestsim.json").open("w", encoding="utf-8") as handle:
        json.dump(local_scenario, handle, ensure_ascii=False, indent=2)
    with (destination / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Captura corpus nesting local")
    parser.add_argument("--scenario-source", required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    destination = capture_nestsim(
        source_path=Path(args.scenario_source).resolve(),
        case_id=str(args.case),
        scenario_id=str(args.scenario_id),
        overwrite=bool(args.overwrite),
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
