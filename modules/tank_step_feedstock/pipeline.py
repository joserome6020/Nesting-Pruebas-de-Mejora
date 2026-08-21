"""Orquesta: descubrir STEP en AutoDXF → DXF en FROM_STEP → resumen."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .discover import FROM_STEP_DIRNAME, pick_primary_step
from .plate_dxf import PlateExport, PlateExtractReport, extract_plates_from_step

ProgressCb = Callable[[str, float], None]


@dataclass
class StepFeedstockResult:
    ok: bool
    autodxf: Path
    step_path: Path | None = None
    out_dir: Path | None = None
    report: PlateExtractReport | None = None
    message: str = ""
    exports: list[PlateExport] = field(default_factory=list)


def process_autodxf_step_feedstock(
    carpeta_autodxf: str | Path,
    *,
    step_path: str | Path | None = None,
    material: str = "UNKNOWN",
    clean_out: bool = True,
    progress_cb: ProgressCb | None = None,
) -> StepFeedstockResult:
    """
    Procesa el STEP hallado (o el indicado) y escribe DXF bajo ``AutoDXF/FROM_STEP``.
    No toca los DXF de Inventor fuera de FROM_STEP.
    """
    autodxf = Path(carpeta_autodxf)
    if not autodxf.is_dir():
        return StepFeedstockResult(
            ok=False,
            autodxf=autodxf,
            message=f"No existe carpeta AutoDXF: {autodxf}",
        )

    chosen: Path | None
    if step_path is not None:
        chosen = Path(step_path)
        if not chosen.is_file():
            return StepFeedstockResult(
                ok=False,
                autodxf=autodxf,
                step_path=chosen,
                message=f"STEP no encontrado: {chosen}",
            )
    else:
        chosen = pick_primary_step(autodxf)
        if chosen is None:
            return StepFeedstockResult(
                ok=False,
                autodxf=autodxf,
                message=(
                    "No hay .stp/.step en AutoDXF.\n"
                    "Colócalo en la raíz de AutoDXF o en AutoDXF/STEP/."
                ),
            )

    out_dir = autodxf / FROM_STEP_DIRNAME
    if clean_out and out_dir.exists():
        try:
            shutil.rmtree(out_dir)
        except Exception as exc:
            return StepFeedstockResult(
                ok=False,
                autodxf=autodxf,
                step_path=chosen,
                out_dir=out_dir,
                message=f"No se pudo limpiar {FROM_STEP_DIRNAME}: {exc}",
            )
    out_dir.mkdir(parents=True, exist_ok=True)

    report = extract_plates_from_step(
        chosen,
        out_dir,
        material=material,
        progress_cb=progress_cb,
    )
    n_ok = len(report.exports)
    if n_ok <= 0:
        detail = "; ".join(report.errors[:3]) if report.errors else "sin placas planas"
        return StepFeedstockResult(
            ok=False,
            autodxf=autodxf,
            step_path=chosen,
            out_dir=out_dir,
            report=report,
            exports=[],
            message=(
                f"STEP leído ({report.solids_total} sólidos) pero no se generó DXF "
                f"usable ({detail})."
            ),
        )

    msg = (
        f"OK: {n_ok} pieza(s) DXF desde {chosen.name} "
        f"({report.plates_detected} placas / {report.solids_total} sólidos). "
        f"Salida: {FROM_STEP_DIRNAME}/"
    )
    if report.skipped:
        msg += f" Omitidos: {len(report.skipped)}."
    return StepFeedstockResult(
        ok=True,
        autodxf=autodxf,
        step_path=chosen,
        out_dir=out_dir,
        report=report,
        exports=list(report.exports),
        message=msg,
    )
