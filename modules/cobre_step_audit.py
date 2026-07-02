"""Auditoría DXF/STEP cobre largos — detecta pérdida de piezas antes y después de FreeCAD."""
from __future__ import annotations

import os
import re
from pathlib import Path

import ezdxf

from modules.nest_exporter import DxfExportValidationError

_CUT_CU_LAYER = "CUT_CU"
_META_LAYER = "ARGA_META"


def expected_cu_solid_pieces(placements: list) -> int:
    """Piezas cobre que deben generar un sólido STEP (excluye guillotinas CU_CORTE__)."""
    n = 0
    for p in placements or []:
        if not bool(p.get("cu_largos_piece")):
            continue
        name = str(p.get("part_name", p.get("name", "")) or "")
        if name.startswith("CU_CORTE__"):
            continue
        n += 1
    return n


def count_cut_cu_piece_islands(msp) -> int:
    """
    Cuenta islas CUT_CU: 1 LWPOLYLINE cerrada = 1 pieza STEP.
    LINE/ARC sueltos en modelspace se consideran formato inválido (fusiones FreeCAD).
    """
    closed_polys = 0
    loose = 0
    for e in msp:
        layer = str(getattr(e.dxf, "layer", "") or "").upper()
        if layer != _CUT_CU_LAYER:
            continue
        typ = e.dxftype()
        if typ == "LWPOLYLINE" and bool(getattr(e, "closed", False) or e.closed):
            closed_polys += 1
        elif typ in ("LINE", "ARC", "CIRCLE"):
            loose += 1
    if loose:
        return -loose
    return closed_polys


def write_cu_piece_meta(msp, *, expected_pieces: int, sheet_label: str = "") -> None:
    """Metadato legible en DXF para auditoría (no participa en STEP)."""
    if expected_pieces <= 0:
        return
    label = str(sheet_label or "").strip()
    text = f"CU_PIECES={int(expected_pieces)}"
    if label:
        text = f"{text}|SHEET={label}"
    msp.add_text(text, dxfattribs={"layer": _META_LAYER, "height": 1.0}).set_placement((0.0, -5.0))


def read_cu_piece_meta(msp) -> int | None:
    for e in msp:
        if str(getattr(e.dxf, "layer", "") or "").upper() != _META_LAYER:
            continue
        if e.dxftype() not in ("TEXT", "MTEXT"):
            continue
        raw = str(getattr(e.dxf, "text", "") or "")
        if e.dxftype() == "MTEXT":
            try:
                raw = str(e.plain_text() or "")
            except Exception:
                pass
        m = re.search(r"CU_PIECES=(\d+)", raw.upper())
        if m:
            return int(m.group(1))
    return None


def validate_cut_cu_piece_count(
    msp,
    placements: list,
    *,
    sheet_label: str = "",
    strict: bool = True,
) -> int:
    """Falla si el DXF no tiene una isla CUT_CU por cada pieza cobre esperada."""
    expected = expected_cu_solid_pieces(placements)
    if expected <= 0:
        return 0

    found = count_cut_cu_piece_islands(msp)
    if found < 0:
        loose = -found
        msg = (
            f"{sheet_label or 'Hoja cobre'}: CUT_CU fragmentado ({loose} LINE/ARC sueltos). "
            f"Se requiere 1 LWPOLYLINE cerrada por pieza ({expected} esperadas). "
            "Re-exporte con la versión actual del exportador."
        )
        if strict:
            raise DxfExportValidationError(msg)
        return expected

    if found != expected:
        msg = (
            f"{sheet_label or 'Hoja cobre'}: CUT_CU incompleto "
            f"({found}/{expected} contornos cerrados por pieza)."
        )
        if strict:
            raise DxfExportValidationError(msg)
    return expected


def count_step_solids(step_path: str | Path) -> int:
    path = Path(step_path)
    if not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8", errors="ignore")
    return len(re.findall(r"MANIFOLD_SOLID_BREP", text, re.I))


def parse_macro_log_piece_stats(macro_log_path: str | Path) -> dict[str, dict[str, int]]:
    """
    Por DXF procesado: outer_wires, solids, lost.
    Busca líneas del generador_verde.FCMacro.
    """
    path = Path(macro_log_path)
    if not path.is_file():
        return {}

    stats: dict[str, dict[str, int]] = {}
    current: str | None = None

    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line.startswith("[") and "] Nombre STEP" in line:
            m = re.match(r"\[([^\]]+)\]", line)
            current = m.group(1) if m else None
            if current and current not in stats:
                stats[current] = {"outer": 0, "solids": 0}
            continue
        if not current:
            continue
        if "-> OUTER:" in line:
            m = re.search(r"OUTER:\s*(\d+)", line)
            if m:
                stats[current]["outer"] = int(m.group(1))
        if "Piezas solidas en STEP:" in line:
            m = re.search(r"Piezas solidas en STEP:\s*(\d+)", line)
            if m:
                stats[current]["solids"] = int(m.group(1))

    for data in stats.values():
        data["lost"] = max(0, int(data.get("outer", 0)) - int(data.get("solids", 0)))
    return stats


def audit_macro_log_for_losses(
    macro_log_path: str | Path,
    *,
    material: str = "",
) -> list[str]:
    """Lista de mensajes de error si algún DXF cobre perdió piezas en STEP."""
    if str(material or "").strip().upper() not in ("CU", "COBRE", "COPPER"):
        return []

    issues: list[str] = []
    for dxf_name, data in parse_macro_log_piece_stats(macro_log_path).items():
        outer = int(data.get("outer") or 0)
        solids = int(data.get("solids") or 0)
        if outer <= 0:
            continue
        if solids < outer:
            issues.append(
                f"{dxf_name}: STEP perdió {outer - solids} pieza(s) "
                f"({solids}/{outer} sólidos; revise CUT_CU en DXF)"
            )
    return issues


def audit_dxf_cut_cu_file(dxf_path: str | Path) -> tuple[int | None, int]:
    """Devuelve (meta esperada, islas CUT_CU encontradas)."""
    path = Path(dxf_path)
    if not path.is_file():
        return None, 0
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    found = count_cut_cu_piece_islands(msp)
    if found < 0:
        found = 0
    return read_cu_piece_meta(msp), found


def step_matches_dxf_meta(dxf_path: str | Path, step_path: str | Path) -> bool:
    expected, islands = audit_dxf_cut_cu_file(dxf_path)
    if expected is None:
        expected = islands
    if expected <= 0:
        return True
    solids = count_step_solids(step_path)
    return solids >= expected
