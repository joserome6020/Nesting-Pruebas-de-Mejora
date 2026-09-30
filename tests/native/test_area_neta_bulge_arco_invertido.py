"""Candado 2026-09-30: FALLO "contorno abierto" en piezas sanas (job 62223).

- 62223-1247-P12 (arandela partida Ø21"): Processed Files guarda el corte como
  LWPOLYLINE con bulge; el visor ignoraba el bulge y el anillo quedaba en su
  cuerda (AREA NETA 1.563 in²) → omitido del nesteo.
- SP-742 (ranuras de 4 ARC): al recorrer un ARC al revés,
  `_arc_points_head_tail` trazaba el arco complementario; cada ranura medía
  21 in² y el área neta daba 0.
"""
from __future__ import annotations

import math
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
for p in (RAIZ, RAIZ / "interface"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import ezdxf  # noqa: E402

from interface.qt.dxf_part_loader import load_dxf_part  # noqa: E402
from modules.nesting_engine.dxf_nesting_audit import _validar_area_neta_pieza  # noqa: E402
from modules.processed_layers import ProcesadorDXF  # noqa: E402

CX, CY, R_EXT, R_INT = 10.4993, -0.125, 10.5, 4.25
AREA_P12 = math.pi * (R_EXT**2 - R_INT**2) - 6.2511 * 0.25


def _doc():
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    return doc


def _p12_origen(ruta: Path) -> None:
    doc = _doc()
    msp = doc.modelspace()
    a = {"layer": "IV_OUTER_PROFILE"}
    msp.add_arc((CX, CY), R_EXT, 180.6821, 179.3179, dxfattribs=a)
    msp.add_line((0.0, 0.0), (6.2511, 0.0), dxfattribs=a)
    msp.add_arc((CX, CY), R_INT, 181.6854, 178.3146, dxfattribs=a)
    msp.add_line((6.2511, -0.25), (0.0, -0.25), dxfattribs=a)
    doc.saveas(ruta)


def _ranura(msp, cx, cy, ang0, ang1, r_med=1.9, ancho=0.3):
    """Ranura curva: 2 arcos concéntricos + 2 casquetes (Inventor)."""
    a = {"layer": "IV_INTERIOR_PROFILES"}
    r1, r2, rc = r_med - ancho / 2, r_med + ancho / 2, ancho / 2
    msp.add_arc((cx, cy), r2, ang0, ang1, dxfattribs=a)
    msp.add_arc((cx, cy), r1, ang0, ang1, dxfattribs=a)
    for ang, sa, ea in ((ang1, ang1, ang1 + 180), (ang0, ang0 + 180, ang0 + 360)):
        t = math.radians(ang)
        msp.add_arc(
            (cx + r_med * math.cos(t), cy + r_med * math.sin(t)), rc, sa, ea, dxfattribs=a
        )


def _sp742_origen(ruta: Path) -> float:
    doc = _doc()
    msp = doc.modelspace()
    cx, cy, R = 2.5, 2.5, 2.5
    msp.add_circle((cx, cy), 1.0, dxfattribs={"layer": "IV_INTERIOR_PROFILES"})
    for a0 in (30.0, 150.0, 270.0):
        _ranura(msp, cx, cy, a0, a0 + 50.0)
    o = {"layer": "IV_OUTER_PROFILE"}
    msp.add_arc((cx, cy), R, -45.0, 225.0, dxfattribs=o)
    t0, t1 = math.radians(225.0), math.radians(-45.0)
    msp.add_line(
        (cx + R * math.cos(t0), cy + R * math.sin(t0)),
        (cx + R * math.cos(t1), cy + R * math.sin(t1)),
        dxfattribs=o,
    )
    doc.saveas(ruta)
    seg = R * R / 2 * (math.radians(90) - math.sin(math.radians(90)))
    ranura = math.radians(50) * 1.9 * 0.3 + math.pi * 0.15**2
    return math.pi * R * R - seg - math.pi * 1.0 - 3 * ranura


def _check(fallos, ruta: Path, esperado: float, tag: str) -> None:
    m = load_dxf_part(str(ruta))
    if m is None or abs(m.area_neta - esperado) > esperado * 0.01:
        area = None if m is None else round(m.area_neta, 3)
        fallos.append(f"{tag}: área neta {area} != {esperado:.3f}")
    err = _validar_area_neta_pieza(str(ruta))
    if err:
        fallos.append(f"{tag}: auditoría la omite: {err}")


def main() -> int:
    fallos: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="area_bulge_"))
    try:
        proc = ProcesadorDXF()
        _p12_origen(tmp / "p12.dxf")
        proc.limpiar_archivo(str(tmp / "p12.dxf"), str(tmp / "p12_proc.dxf"), omit_marcaje=True)
        _check(fallos, tmp / "p12.dxf", AREA_P12, "P12 origen")
        _check(fallos, tmp / "p12_proc.dxf", AREA_P12, "P12 Processed (LWPOLYLINE bulge)")

        esperado = _sp742_origen(tmp / "sp742.dxf")
        proc.limpiar_archivo(str(tmp / "sp742.dxf"), str(tmp / "sp742_proc.dxf"), omit_marcaje=True)
        _check(fallos, tmp / "sp742.dxf", esperado, "SP-742 origen (ARC invertidos)")
        _check(fallos, tmp / "sp742_proc.dxf", esperado, "SP-742 Processed")

        from modules.dxf_export.validate import _entities_bbox_mm

        d = ezdxf.readfile(tmp / "p12_proc.dxf")
        polys = [e for e in d.modelspace().query("LWPOLYLINE") if e.dxf.layer == "CUT_OUTER"]
        bb = _entities_bbox_mm(polys)
        if not bb or bb[3] - bb[1] < 20.0:
            fallos.append(f"validate bbox ignora bulge: {bb}")

        from modules.dxf_lwpoly import puntos_lwpolyline

        d = ezdxf.readfile(tmp / "sp742_proc.dxf")
        outer = [e for e in d.modelspace().query("LWPOLYLINE") if e.dxf.layer == "CUT_OUTER"]
        if not outer or len(puntos_lwpolyline(outer[0], 0.02)) <= 3:
            fallos.append("SP-742 Processed: contorno exterior sin arcos aplanados")
        vis = (RAIZ / "interface/qt/visualizer.py").read_text(encoding="utf-8")
        if "puntos_lwpolyline(e, 0.02)" not in vis:
            fallos.append("miniatura PARTS lee LWPOLYLINE sin bulge (SP-742 sale vacía)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if fallos:
        print("FAIL area neta bulge / arco invertido:")
        for f in fallos:
            print("  -", f)
        return 1
    print("OK area neta con bulge + ARC invertido (62223-1247-P12 / SP-742)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
