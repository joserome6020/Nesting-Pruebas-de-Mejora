"""Valida que STEP (Inventor) e IGES (CypTube) exportan sin bloquearse."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DXF = ROOT / "_logs" / "h1_step_fix_test" / "NESTING_0.25_W.O. 3 X1-H1.dxf"
OUT_STEP = ROOT / "_logs" / "export_audit_step"
OUT_IGES = ROOT / "_logs" / "export_audit_iges"


def _run(fmt: str, out: Path) -> tuple[bool, list[Path]]:
    from freecad_runner import ejecutar_macro_freecad

    out.mkdir(parents=True, exist_ok=True)
    for pat in ("*.step", "*.igs", "*.iges"):
        for f in out.glob(pat):
            f.unlink()

    os.environ["FREECAD_SKIP_EXISTING"] = "0"
    ok = ejecutar_macro_freecad(
        str(DXF.parent),
        str(out),
        6.35,
        "TR",
        0.0,
        0.0,
        0.0,
        export_format=fmt,
        dxf_filter=lambda p: Path(p).name == DXF.name,
    )
    files = list(out.glob("*.step")) + list(out.glob("*.igs")) + list(out.glob("*.iges"))
    valid = [f for f in files if f.stat().st_size > 512]
    return ok and bool(valid), valid


def main() -> int:
    if not DXF.is_file():
        print(f"Falta DXF de prueba: {DXF}")
        return 1

    print("=== STEP (flujo Inventor / PQart) ===")
    ok_step, step_files = _run("step", OUT_STEP)
    for f in step_files:
        print(f"  OK {f.name} ({f.stat().st_size / 1024:.1f} KB)")
    if not ok_step:
        print("  FAIL STEP")
        return 2

    print("\n=== IGES (flujo CypTube) ===")
    ok_iges, iges_files = _run("iges", OUT_IGES)
    for f in iges_files:
        print(f"  OK {f.name} ({f.stat().st_size / 1024:.1f} KB)")
    if not ok_iges:
        print("  FAIL IGES")
        return 3

    print("\nAmbos formatos exportaron correctamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
