"""Congela un lote AutoDXF en un corpus de benchmark reproducible.

Las cantidades se leen de nombres como ``..., QTY 24, Cal 0.11811.dxf``.
El resultado copia los DXF a ``benchmarks/corpus_real/<case>/dxf`` y deja
rutas relativas + hashes, por lo que los benchmarks nunca dependen de la UNC.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any


_BENCHMARKS = Path(__file__).resolve().parent
_QTY_RE = re.compile(r"\bQTY\s*(\d+)\b", re.IGNORECASE)
_MATERIAL_RE = re.compile(
    r",\s*(?P<material>[^,]+?),\s*QTY\s*\d+\s*,",
    re.IGNORECASE,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quantity_from_name(name: str) -> int:
    match = _QTY_RE.search(name)
    return max(1, int(match.group(1))) if match else 1


def _material_from_name(name: str) -> str:
    match = _MATERIAL_RE.search(name)
    return str(match.group("material")).strip() if match else ""


def _unique_name(source: Path, used_names: set[str]) -> str:
    filename = source.name
    if filename.casefold() not in used_names:
        used_names.add(filename.casefold())
        return filename
    index = 2
    while True:
        candidate = f"{source.stem}__{index}{source.suffix}"
        if candidate.casefold() not in used_names:
            used_names.add(candidate.casefold())
            return candidate
        index += 1


def _validate_single_piece_dxf(source: Path) -> str:
    """Devuelve vacío si el DXF representa exactamente una pieza anidable."""
    from modules.nesting_engine.sim_lab import SimPieceEntry, build_pieces_from_entries

    _pieces, errors = build_pieces_from_entries(
        [SimPieceEntry(ruta=str(source), qty=1, nombre=source.stem)]
    )
    return " | ".join(str(error) for error in errors)


def capture_autodxf_batch(
    *,
    source_dir: Path,
    case_id: str,
    scenario_id: str,
    calibre_token: str,
    plate_w_in: float,
    plate_h_in: float,
    kerf_in: float,
    margin_in: float,
    overwrite: bool,
) -> Path:
    if not source_dir.is_dir():
        raise FileNotFoundError(f"No existe AutoDXF: {source_dir}")
    if plate_w_in <= 0 or plate_h_in <= 0:
        raise ValueError("Las dimensiones de placa deben ser positivas.")

    destination = _BENCHMARKS / "corpus_real" / case_id
    if destination.exists() and not overwrite:
        raise FileExistsError(f"{destination} ya existe. Usa --overwrite para regenerar.")
    if destination.exists():
        shutil.rmtree(destination)
    dxf_destination = destination / "dxf"
    dxf_destination.mkdir(parents=True, exist_ok=True)

    token = calibre_token.casefold().strip()
    sources = sorted(
        source
        for source in source_dir.rglob("*.dxf")
        if token in source.name.casefold()
    )
    if not sources:
        raise ValueError(f"No hay DXF con calibre/token '{calibre_token}'.")

    pieces: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    used_names: set[str] = set()
    for source in sources:
        validation_error = _validate_single_piece_dxf(source)
        if validation_error:
            skipped.append({"source_name": source.name, "reason": validation_error})
            continue
        filename = _unique_name(source, used_names)
        local_dxf = dxf_destination / filename
        shutil.copy2(source, local_dxf)
        qty = _quantity_from_name(source.name)
        material = _material_from_name(source.name)
        pieces.append(
            {
                "ruta": f"dxf/{filename}",
                "qty": qty,
                "nombre": source.stem,
                "material": material,
                "calibre_source": calibre_token,
            }
        )
        files.append(
            {
                "file": f"dxf/{filename}",
                "source_name": source.name,
                "sha256": _sha256(local_dxf),
                "bytes": local_dxf.stat().st_size,
                "qty": qty,
            }
        )
    if not pieces:
        raise ValueError("Ningún DXF representa una pieza anidable individual.")

    scenario = {
        "version": 1,
        "scenario": scenario_id,
        "level": "REAL",
        "source_kind": "nestsim_snapshot",
        "plate_w_in": plate_w_in,
        "plate_h_in": plate_h_in,
        "kerf_in": kerf_in,
        "margin_in": margin_in,
        "corner": "INFERIOR IZQUIERDA",
        "opt": "OPTIMIZAR LARGO Y ANCHO",
        "nest_mode": "standard",
        "mc_iterations": 1,
        "require_full_place": False,
        "notes": (
            "Snapshot AutoDXF local, calibre "
            f"{calibre_token}; qty extraída del nombre de archivo."
        ),
        "pieces": pieces,
    }
    manifest = {
        "case_id": case_id,
        "scenario": scenario_id,
        "source_kind": "autodxf_snapshot",
        "source_dir": source_dir.name,
        "calibre_token": calibre_token,
        "dxf_count": len(files),
        "total_requested_pieces": sum(int(item["qty"]) for item in files),
        "skipped_dxf_count": len(skipped),
        "skipped": skipped,
        "plate_w_in": plate_w_in,
        "plate_h_in": plate_h_in,
        "kerf_in": kerf_in,
        "margin_in": margin_in,
        "files": files,
    }
    (destination / "scenario.nestsim.json").write_text(
        json.dumps(scenario, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Captura lote AutoDXF para benchmark")
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--calibre-token", default="0.1181")
    parser.add_argument("--plate-w-in", type=float, default=120.0)
    parser.add_argument("--plate-h-in", type=float, default=48.0)
    parser.add_argument("--kerf-in", type=float, default=0.3)
    parser.add_argument("--margin-in", type=float, default=0.15)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    destination = capture_autodxf_batch(
        source_dir=Path(args.source_dir),
        case_id=str(args.case),
        scenario_id=str(args.scenario_id),
        calibre_token=str(args.calibre_token),
        plate_w_in=float(args.plate_w_in),
        plate_h_in=float(args.plate_h_in),
        kerf_in=float(args.kerf_in),
        margin_in=float(args.margin_in),
        overwrite=bool(args.overwrite),
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
