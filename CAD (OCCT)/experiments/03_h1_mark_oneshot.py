"""
Prueba H1: MARK del multibody en UN solo boolean CUT (compound piezas − compound ranuras).

Salida fija:
  C:\\Users\\jose_rosales\\OneDrive - grupoarga.com\\Escritorio\\NANS

Uso:
  python "CAD (OCCT)/experiments/03_h1_mark_oneshot.py"
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO))

from engine.dxf_to_step import (  # noqa: E402
    export_dxf_to_step_freecad_batch,
    thickness_mm_from_dxf_name,
)

OUT_DIR = Path(r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\NANS")

DXF_SRC = Path(
    r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals"
    r"\ARGA METALS CORPORATE SYSTEM\TANKS\VANTRAN\06_30_2322_TANK_251007"
    r"\MODEL CORE FILES\W.O. 1 X11\ARGA MODEL CORE\NESTING"
    r"\CAMA LASER SIN MINI NEST\DXF\NESTING_0.1046_W.O. 1 X11-H1.dxf"
)

MODES = (
    "ENGRAVE_ONESHOT",
)


def _copy_dxf_local() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    local = OUT_DIR / DXF_SRC.name
    if not DXF_SRC.is_file():
        raise FileNotFoundError(f"DXF H1 no encontrado: {DXF_SRC}")
    if (not local.is_file()) or local.stat().st_size != DXF_SRC.stat().st_size:
        print(f"Copiando DXF local -> {local}")
        shutil.copy2(DXF_SRC, local)
    return local


def main() -> int:
    dxf = _copy_dxf_local()
    thk = thickness_mm_from_dxf_name(dxf.name, default_mm=2.6568)
    print("=== H1 MARK ONESHOT (multibody 1 boolean) ===")
    print(f"DXF: {dxf}")
    print(f"THK: {thk:.4f} mm")
    print(f"OUT: {OUT_DIR}")

    last_err: Exception | None = None
    for mode in MODES:
        out = OUT_DIR / f"W.O. 1 X11-H1_OCCT_{mode}.step"
        print(f"\n--- intento mark_mode={mode} ---")
        t0 = time.perf_counter()
        try:
            info = export_dxf_to_step_freecad_batch(
                dxf,
                out,
                thk_mm=thk,
                material="STEEL",
                origen="NONE",
                mark_mode=mode,
                include_plate=False,
            )
            dt = time.perf_counter() - t0
            note = str(info.get("engrave_note") or "")
            print(
                f"OK outer={info['outers']} inner={info['inners']} "
                f"marks={info['mark_segs']} solids={info.get('solids')} "
                f"bytes={info['bytes']} sec={dt:.1f}"
            )
            print(f"engrave_note: {note}")
            print(f"STEP -> {info['path']}")
            # Éxito real del oneshot multibody
            if note.startswith("oneshot_ok"):
                # Nombre estable para la prueba
                final = OUT_DIR / "W.O. 1 X11-H1_OCCT_ONESHOT.step"
                try:
                    if Path(info["path"]).resolve() != final.resolve():
                        import shutil

                        shutil.copy2(info["path"], final)
                except Exception:
                    final = Path(info["path"])
                summary = OUT_DIR / "H1_ONESHOT_OK.txt"
                summary.write_text(
                    f"mode={mode}\n"
                    f"engrave_note={note}\n"
                    f"step={final}\n"
                    f"bytes={info['bytes']}\n"
                    f"marks={info['mark_segs']}\n"
                    f"solids={info.get('solids')}\n"
                    f"sec={dt:.3f}\n",
                    encoding="utf-8",
                )
                print(f"ÉXITO ONESHOT MULTIBODY -> {final}")
                print(f"RESUMEN -> {summary}")
                return 0
            print(f"ONESHOT no puro ({note}); reintento no aplica en este script")
            return 1
        except Exception as exc:
            last_err = exc
            print(f"FAIL {mode}: {exc}")

    if last_err:
        print(f"ERROR FINAL: {last_err}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
