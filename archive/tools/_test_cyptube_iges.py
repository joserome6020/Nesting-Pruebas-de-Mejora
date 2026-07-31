"""Regenera IGES CypTube de prueba desde DXF cobre y valida bbox en origen."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DXF = ROOT / "_logs" / "h1_step_fix_test" / "NESTING_0.25_W.O. 3 X1-H1.dxf"
OUT = ROOT / "_logs" / "cyptube_iges_test"


def _run_freecad() -> bool:
    from freecad_runner import ejecutar_macro_freecad

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.igs"):
        old.unlink()
    for old in OUT.glob("*.iges"):
        old.unlink()

    os.environ["FREECAD_SKIP_EXISTING"] = "0"
    return ejecutar_macro_freecad(
        str(DXF.parent),
        str(OUT),
        6.35,
        "TR",
        0.0,
        0.0,
        0.0,
        export_format="iges",
        dxf_filter=lambda p: Path(p).name == DXF.name,
    )


def _audit_igs(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    xs, ys, zs = [], [], []
    for m in re.finditer(r"^110,([^;]+);", text, re.M):
        parts = [float(x) for x in m.group(1).split(",")[:3]]
        xs.append(parts[0])
        ys.append(parts[1])
        zs.append(parts[2])
    print(f"Archivo: {path.name} ({path.stat().st_size / 1024 / 1024:.2f} MB)")
    if not xs:
        print("  SIN puntos 110 en IGES")
        return
    print(f"  X [{min(xs):.2f}, {max(xs):.2f}]  Y [{min(ys):.2f}, {max(ys):.2f}]  Z [{min(zs):.2f}, {max(zs):.2f}]")
    ok_origin = min(xs) >= -1.0 and min(ys) >= -1.0 and min(zs) >= -1.0
    print(f"  Origen cerca de cero: {'OK' if ok_origin else 'FAIL'}")


def main() -> int:
    if not DXF.is_file():
        print(f"DXF de prueba no encontrado: {DXF}")
        return 1
    print("Regenerando IGES CypTube...")
    ok = _run_freecad()
    if not ok:
        print("FreeCAD falló — ver _logs/cyptube_iges_test/_logs/")
        return 2
    igs_files = sorted(OUT.glob("*.igs")) + sorted(OUT.glob("*.iges"))
    if not igs_files:
        print("No se generó .igs")
        return 3
    for p in igs_files:
        _audit_igs(p)
    print(f"\nListo: {igs_files[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
