"""
Experimento paralelo: DXF nest cobre (RTZCU / con_gap) -> STEP estilo FreeCAD batch.

Paridad con freecad_batch_dxf_to_step (modo STEP):
- CUT_OUTER / CUT_INNER extruidos + boolean cut
- MARK como aristas libres en z = espesor (ExportFreeEdges)
- Color cobre XCAF AP214 (0.78, 0.48, 0.22) — mismo COLOUR_RGB que FreeCAD
- No llama FreeCAD

Uso:
  python "CAD (OCCT)/experiments/02_rtzcu_dxf_to_step.py"
  python "CAD (OCCT)/experiments/02_rtzcu_dxf_to_step.py" <dxf> <out_dir>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # CAD (OCCT)/
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO))

from engine.dxf_to_step import (  # noqa: E402
    export_dxf_to_step_freecad_batch,
    thickness_mm_from_dxf_name,
)

DXF_DEFAULT = Path(
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\Nesteos Locales"
    r"\GIGA\GIGA FLUIDSTACK\MODEL CORE FILES\W.O. 32 X6\ARGA MODEL CORE"
    r"\NESTING\NESTEOS DE COBRE\DXF\NESTING_0.25_RTZCU80-H249.dxf"
)
OUT_DEFAULT = Path(
    r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\NANS"
)


def main() -> int:
    dxf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DXF_DEFAULT
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT_DEFAULT
    if not dxf_path.is_file():
        print(f"DXF no encontrado: {dxf_path}")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{dxf_path.stem}_OCCT.step"
    thk = thickness_mm_from_dxf_name(dxf_path.name, default_mm=0.25 * 25.4)

    print("=== OCCT FreeCAD-like (ENGRAVE + cobre VisMaterial) ===")
    print(f"DXF: {dxf_path}")
    print(f"THK: {thk} mm")
    print(f"OUT: {out_path}")

    info = export_dxf_to_step_freecad_batch(
        dxf_path,
        out_path,
        thk_mm=thk,
        material="CU",
        mark_mode="ENGRAVE",
    )
    print(
        f"OUTER={info['outers']} INNER={info['inners']} MARK_SEGS={info['mark_segs']} "
        f"SOLIDS={info.get('solids')} MODE={info.get('mark_mode')}"
    )
    print(f"volume ~= {info['volume']:.3f}")
    print(f"OK STEP -> {info['path']} ({info['bytes']} bytes)")

    # Comparación rápida vs FreeCAD si existe
    fc = dxf_path.parent.parent / "STEP" / f"{dxf_path.stem}.step"
    if not fc.is_file():
        # a veces STEP está hermano de DXF en NESTEOS DE COBRE/STEP
        fc = dxf_path.parent.parent / "STEP" / dxf_path.name.replace(".dxf", ".step")
    alt = list((dxf_path.parent.parent / "STEP").glob(f"*{dxf_path.stem.split('_')[-1]}*.step")) if (dxf_path.parent.parent / "STEP").is_dir() else []
    if fc.is_file() or alt:
        ref = fc if fc.is_file() else alt[0]
        t_oc = out_path.read_text(encoding="utf-8", errors="ignore")
        t_fc = ref.read_text(encoding="utf-8", errors="ignore")
        def _colours(t: str):
            return re.findall(r"COLOUR_RGB\(''[^)]+\)", t, flags=re.I)
        print(f"REF FreeCAD: {ref.name} ({ref.stat().st_size} bytes)")
        print(f"  OCCT COLOUR: {_colours(t_oc)[:3]}")
        print(f"  FC   COLOUR: {_colours(t_fc)[:3]}")
        print(
            f"  OCCT EDGE_CURVE={t_oc.upper().count('EDGE_CURVE')} "
            f"MANIFOLD={t_oc.upper().count('MANIFOLD_SOLID')}"
        )
        print(
            f"  FC   EDGE_CURVE={t_fc.upper().count('EDGE_CURVE')} "
            f"MANIFOLD={t_fc.upper().count('MANIFOLD_SOLID')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
