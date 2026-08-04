"""Extrae una hoja de un .arganest a un escenario geométrico local.

Permite validar el packer contra geometría real aun cuando los DXF originales
estén archivados o la ruta de red no esté disponible. El snapshot no conserva
rutas de red ni el workspace original.

Uso:
  python -m benchmarks.workspace_geometry_snapshot \
    --workspace "C:\\...\\2500 KVA X30.arganest" \
    --case r_2500kva_x30 --scenario-id r_2500kva_x30_max_sheet
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_BENCHMARKS = Path(__file__).resolve().parent
_ROOT = _BENCHMARKS.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

IN_TO_MM = 25.4
_NON_CUT_PREFIXES = ("REF__", "TATUAJE__", "RETAZO_", "REMANENTE__", "CU_CORTE__")


def _is_cut_piece(piece: dict[str, Any]) -> bool:
    return not str(piece.get("nombre") or "").upper().startswith(_NON_CUT_PREFIXES)


def _iter_sheets(payload: dict[str, Any]) -> list[tuple[str, int, dict[str, Any]]]:
    """Lista hojas únicas de resultados_multilote."""
    out: list[tuple[str, int, dict[str, Any]]] = []
    seen: set[str] = set()
    for slot in payload.get("resultados_multilote") or []:
        if not isinstance(slot, dict):
            continue
        for group, info in (slot.get("data") or {}).items():
            if not isinstance(info, dict):
                continue
            for index, sheet in enumerate(info.get("hojas") or []):
                if not isinstance(sheet, dict) or sheet.get("es_retazo"):
                    continue
                uid = str(sheet.get("sheet_uid") or f"{group}:{index}")
                if uid in seen:
                    continue
                seen.add(uid)
                out.append((str(group), index, sheet))
    return out


def _normalized_rings(rings: list) -> list[list[list[float]]]:
    valid = [
        [(float(point[0]), float(point[1])) for point in ring if len(point) >= 2]
        for ring in (rings or [])
        if isinstance(ring, list) and len(ring) >= 3
    ]
    if not valid or len(valid[0]) < 3:
        return []
    min_x = min(point[0] for ring in valid for point in ring)
    min_y = min(point[1] for ring in valid for point in ring)
    return [
        [[round(x - min_x, 6), round(y - min_y, 6)] for x, y in ring]
        for ring in valid
    ]


def _representative_pieces(pieces: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Subconjunto estable con variedad de nombres antes de repetir geometrías."""
    if limit <= 0 or len(pieces) <= limit:
        return pieces
    buckets: dict[str, list[dict[str, Any]]] = {}
    for piece in pieces:
        buckets.setdefault(str(piece.get("nombre") or ""), []).append(piece)
    selected: list[dict[str, Any]] = []
    depth = 0
    while len(selected) < limit:
        added = False
        for bucket in buckets.values():
            if depth < len(bucket):
                selected.append(bucket[depth])
                added = True
                if len(selected) >= limit:
                    return selected
        if not added:
            break
        depth += 1
    return selected


def snapshot_workspace(
    *,
    workspace: Path,
    case_id: str,
    scenario_id: str,
    group: str = "",
    sheet_index: int | None = None,
    max_pieces: int = 0,
    overwrite: bool = False,
) -> Path:
    from interface.nesting_workspace import cargar_workspace_desde_archivo

    payload = cargar_workspace_desde_archivo(str(workspace))
    candidates = _iter_sheets(payload)
    if group:
        candidates = [candidate for candidate in candidates if candidate[0] == group]
    if sheet_index is not None:
        candidates = [candidate for candidate in candidates if candidate[1] == sheet_index]
    if not candidates:
        raise ValueError("No existe una hoja compatible con los filtros indicados.")

    selected_group, selected_index, sheet = max(
        candidates,
        key=lambda candidate: sum(
            1
            for piece in (candidate[2].get("piezas") or [])
            if isinstance(piece, dict) and _is_cut_piece(piece)
        ),
    )
    w_mm = float(sheet.get("placa_w") or sheet.get("w") or 0.0)
    h_mm = float(sheet.get("placa_h") or sheet.get("h") or 0.0)
    if w_mm <= 0 or h_mm <= 0:
        raise ValueError("La hoja seleccionada no tiene dimensiones válidas.")

    all_piece_count = len(sheet.get("piezas") or [])
    source_pieces = [
        piece
        for piece in (sheet.get("piezas") or [])
        if isinstance(piece, dict) and _is_cut_piece(piece)
    ]
    selected_pieces = _representative_pieces(source_pieces, max_pieces)
    pieces: list[dict[str, Any]] = []
    for index, piece in enumerate(selected_pieces):
        if not isinstance(piece, dict):
            continue
        rings = _normalized_rings(piece.get("poligonos") or [])
        if not rings:
            continue
        name = str(piece.get("nombre") or f"piece_{index + 1}")
        pieces.append(
            {
                "nombre": f"{name}#{index + 1}",
                "qty": 1,
                "rings": rings,
                "area": float(piece.get("area") or 0.0),
                "calibre": str(piece.get("calibre") or ""),
                "material": str(piece.get("material") or ""),
            }
        )
    if not pieces:
        raise ValueError("La hoja seleccionada no contiene polígonos utilizables.")

    destination = _BENCHMARKS / "corpus_real" / case_id
    if destination.exists() and not overwrite:
        raise FileExistsError(f"{destination} ya existe. Usa --overwrite.")
    destination.mkdir(parents=True, exist_ok=True)
    scenario = {
        "scenario": scenario_id,
        "level": "REAL",
        "source_kind": "workspace_geometry_snapshot",
        "plate_w_in": w_mm / IN_TO_MM,
        "plate_h_in": h_mm / IN_TO_MM,
        "kerf_in": float(sheet.get("kerf_usado") or 0.3),
        "margin_in": float(sheet.get("margin_usado") or 0.0),
        "corner": str(sheet.get("corner_usado") or "INFERIOR IZQUIERDA"),
        "opt": str(sheet.get("opt_usado") or "OPTIMIZAR LARGO Y ANCHO"),
        "nest_mode": "standard",
        "mc_iterations": 1,
        # Los snapshots se evalúan como comparación de una hoja: placed/expected
        # se registra explícitamente, sin convertir restos en falso fallo geométrico.
        "require_full_place": False,
        "notes": (
            "Snapshot geométrico local extraído de una hoja real; "
            "sin rutas de red ni DXF originales. "
            "El gate compara placed/expected, eficiencia y geometría."
        ),
        "pieces": pieces,
    }
    manifest = {
        "case_id": case_id,
        "scenario": scenario_id,
        "source_kind": "workspace_geometry_snapshot",
        "workspace_job": str(payload.get("job_activo") or ""),
        "group": selected_group,
        "sheet_index": selected_index,
        "sheet_code": str(sheet.get("sheet_code") or sheet.get("sheet_uid") or ""),
        "all_piece_count": all_piece_count,
        "source_piece_count": len(source_pieces),
        "piece_count": len(pieces),
        "plate_w_mm": w_mm,
        "plate_h_mm": h_mm,
    }
    with (destination / "scenario.json").open("w", encoding="utf-8") as handle:
        json.dump(scenario, handle, ensure_ascii=False, indent=2)
    with (destination / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extrae snapshot geométrico de .arganest")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--group", default="")
    parser.add_argument("--sheet-index", type=int)
    parser.add_argument(
        "--max-pieces",
        type=int,
        default=0,
        help="0 conserva la hoja completa; otro valor toma un subset diverso y estable.",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    destination = snapshot_workspace(
        workspace=Path(args.workspace),
        case_id=str(args.case),
        scenario_id=str(args.scenario_id),
        group=str(args.group),
        sheet_index=args.sheet_index,
        max_pieces=max(0, int(args.max_pieces)),
        overwrite=bool(args.overwrite),
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
