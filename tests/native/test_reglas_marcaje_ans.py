"""Candado 2026-09-29e: reglas del marcaje stick del ANS + área neta del visor.

1) Pieza compensada para plasma: su DXF compensado sale sin el stick del ANS;
   el marcaje que trae el DXF de origen se conserva.
2) Pieza con área neta <= 456.954 in² (SP-792_1) no lleva stick del ANS
   (2026-09-30: la regla es "igual o menor", no "igual o mayor").
3) El detalle de pieza contaba 2 veces los CIRCLE: SP-792_1 (disco 24.25" con
   16 barrenos) mostraba 913.909 in² en vez de 456.954.
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

from modules.dxf_mark.inject import _entity_has_stick_tag  # noqa: E402
from modules.dxf_mark.pipeline import aplicar_marcaje_nesting  # noqa: E402

R_DISCO, R_BARRENO = 12.125, 0.3125
AREA_SP792 = math.pi * R_DISCO**2 - 16 * math.pi * R_BARRENO**2


def _doc():
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1
    return doc


def _disco(path: Path, r: float = R_DISCO) -> None:
    doc = _doc()
    msp = doc.modelspace()
    msp.add_circle((0, 0), r, dxfattribs={"layer": "CUT_OUTER"})
    for i in range(16):
        a = 2 * math.pi * i / 16
        msp.add_circle(((r - 1.625) * math.cos(a), (r - 1.625) * math.sin(a)), R_BARRENO, dxfattribs={"layer": "CUT_INNER"})
    doc.saveas(path)


def _placa_grande(path: Path) -> None:
    doc = _doc()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (30, 0), (30, 20), (0, 20)], close=True, dxfattribs={"layer": "CUT_OUTER"})
    msp.add_circle((2, 3), 0.5, dxfattribs={"layer": "CUT_INNER"})
    msp.add_line((25, 1), (27, 1), dxfattribs={"layer": "MARK"})  # marcaje de origen
    doc.saveas(path)


def _n_stick(path: Path) -> int:
    return sum(1 for e in ezdxf.readfile(path).modelspace() if _entity_has_stick_tag(e))


def _check_area_visor(fallos, tmp: Path) -> None:
    from interface.qt.dxf_part_loader import load_dxf_part

    f = tmp / "SP-792_1.dxf"
    _disco(f)
    area = load_dxf_part(str(f)).area_neta
    if abs(area - AREA_SP792) > 0.01:
        fallos.append(f"visor: área neta SP-792_1 {area:.3f} in² (real {AREA_SP792:.3f})")
    g = tmp / "RECT.dxf"
    doc = _doc()
    doc.modelspace().add_lwpolyline([(0, 0), (30, 0), (30, 20), (0, 20)], close=True, dxfattribs={"layer": "CUT_OUTER"})
    doc.modelspace().add_circle((5, 5), 1, dxfattribs={"layer": "CUT_INNER"})
    doc.saveas(g)
    area = load_dxf_part(str(g)).area_neta
    if abs(area - (600 - math.pi)) > 0.01:
        fallos.append(f"visor: barreno restado de más ({area:.3f} vs {600 - math.pi:.3f})")


def _check_area(fallos, tmp: Path) -> None:
    tope = tmp / "SP-792_1.dxf"
    _disco(tope)
    res = aplicar_marcaje_nesting(tope)
    if not res.omitido_por_area or _n_stick(tope):
        fallos.append(f"SP-792_1 (área {AREA_SP792:.1f} in²) recibió stick ANS")
    menor = tmp / "SP-MENOR_1.dxf"
    _disco(menor, r=12.0)
    res = aplicar_marcaje_nesting(menor)
    if not res.omitido_por_area or _n_stick(menor):
        fallos.append("pieza bajo el tope recibió stick ANS")
    mayor = tmp / "SP-MAYOR_1.dxf"
    _disco(mayor, r=12.5)
    res = aplicar_marcaje_nesting(mayor)
    if res.omitido_por_area or not _n_stick(mayor):
        fallos.append("pieza sobre el tope se quedó sin stick ANS")


def _check_compensada(fallos, tmp: Path) -> None:
    from modules.plasma_compensator import asegurar_dxf_plasma_compensado

    f = tmp / "PL-GRANDE_1.dxf"
    _placa_grande(f)
    aplicar_marcaje_nesting(f)
    if not _n_stick(f):
        fallos.append("pieza de 600 in² sin stick ANS (precondición)")
        return
    out, err = asegurar_dxf_plasma_compensado(f, 0.0625 * 25.4, forzar=True)
    if not out:
        fallos.append(f"no se compensó: {err}")
        return
    msp = ezdxf.readfile(out).modelspace()
    if any(_entity_has_stick_tag(e) for e in msp):
        fallos.append("DXF compensado conserva el stick del ANS")
    origen = [e for e in msp if e.dxf.layer.upper() == "MARK" and e.dxftype() == "LINE"]
    if len(origen) != 1:
        fallos.append(f"DXF compensado perdió el marcaje de origen ({len(origen)} LINE MARK)")
    if not _n_stick(f):
        fallos.append("se borró el stick del DXF original (solo debe salir del compensado)")
    src = (RAIZ / "modules/plasma_dxf_export.py").read_text(encoding="utf-8")
    if "_entity_has_stick_tag(entity)" not in src:
        fallos.append("export plasma desde original debe omitir stick ANS")


def main() -> int:
    fallos: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="ans_reglas_mark_"))
    try:
        _check_area_visor(fallos, tmp)
        _check_area(fallos, tmp)
        _check_compensada(fallos, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if fallos:
        print("FAIL:\n  " + "\n  ".join(fallos))
        return 1
    print("OK reglas marcaje ANS (compensadas / área) + área neta visor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
