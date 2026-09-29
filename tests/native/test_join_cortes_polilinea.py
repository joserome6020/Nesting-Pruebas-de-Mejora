"""Candado 2026-09-29c: JOIN de contornos de corte (CUT_OUTER / CUT_INNER).

Los contornos salían como cientos de LINE/ARC sueltas por pieza (H31: 3890
entidades, 802 KB). Ahora cada contorno cerrado sale como una LWPOLYLINE con
bulge; las cadenas abiertas se quedan como LINE/ARC.

Consumidores que deben leerla igual que antes:
- LS-READY (robot láser): mismos tramos nativos LINE/ARC, mismo punto de
  inicio y sentido que el stitch de las entidades sueltas.
- OCCT dxf_to_step: la polilínea con bulge se discretiza por arcos, no se
  aplana a cuerdas entre vértices.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
for p in (RAIZ, RAIZ / "CAD (OCCT)"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import ezdxf  # noqa: E402

from modules.dxf_mark_join import join_cut_layers  # noqa: E402


def _cargar_lector(uf: str):
    ruta = RAIZ / f"modules/ls_ready_paso1/{uf}_clasificador/dxf/official_lector/lector_dxf.py"
    spec = importlib.util.spec_from_file_location(f"_lector_{uf}_candado", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _doc_original():
    """Rectángulo 100x40 con esquinas R5, entidades desordenadas y en sentidos mixtos."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    o = {"layer": "CUT_OUTER"}
    msp.add_line((95, 40), (5, 40), dxfattribs=o)
    msp.add_arc((95, 5), 5, 270, 360, dxfattribs=o)
    msp.add_line((5, 0), (95, 0), dxfattribs=o)
    msp.add_arc((5, 35), 5, 90, 180, dxfattribs=o)
    msp.add_line((0, 5), (0, 35), dxfattribs=o)
    msp.add_arc((95, 35), 5, 0, 90, dxfattribs=o)
    msp.add_line((100, 35), (100, 5), dxfattribs=o)
    msp.add_arc((5, 5), 5, 180, 270, dxfattribs=o)
    i = {"layer": "CUT_INNER"}
    msp.add_arc((30, 20), 5, 90, 270, dxfattribs=i)
    msp.add_line((30, 15), (60, 15), dxfattribs=i)
    msp.add_line((30, 25), (60, 25), dxfattribs=i)
    msp.add_arc((60, 20), 5, 270, 90, dxfattribs=i)
    msp.add_line((200, 0), (210, 0), dxfattribs=o)
    msp.add_line((210, 0), (210, 10), dxfattribs=o)
    return doc


def _largo(msp, layer):
    tot = 0.0
    for e in msp.query(f'*[layer=="{layer}"]'):
        for v in (e.virtual_entities() if e.dxftype() == "LWPOLYLINE" else [e]):
            if v.dxftype() == "LINE":
                tot += math.dist(v.dxf.start, v.dxf.end)
            else:
                tot += math.radians((v.dxf.end_angle - v.dxf.start_angle) % 360) * v.dxf.radius
    return tot


def _firma(segs):
    out = []
    for s in segs:
        f = [s["type"], tuple(round(c, 3) for c in s["start_dxf"]), tuple(round(c, 3) for c in s["end_dxf"])]
        if s["type"] == "arc":
            f.append(s["direction"])
            f.append(round(s["sweep_deg"], 3))
        out.append(tuple(f))
    return out


def _check_join(fallos, doc_o, doc_j):
    mo, mj = doc_o.modelspace(), doc_j.modelspace()
    tipos = {ly: sorted(e.dxftype() for e in mj.query(f'*[layer=="{ly}"]')) for ly in ("CUT_OUTER", "CUT_INNER")}
    if tipos["CUT_OUTER"] != ["LINE", "LINE", "LWPOLYLINE"]:
        fallos.append(f"CUT_OUTER: se esperaba 1 polilínea + cadena abierta en LINE, hubo {tipos['CUT_OUTER']}")
    if tipos["CUT_INNER"] != ["LWPOLYLINE"]:
        fallos.append(f"CUT_INNER no se unió: {tipos['CUT_INNER']}")
    for e in mj.query("LWPOLYLINE"):
        if not e.closed:
            fallos.append(f"{e.dxf.layer}: polilínea de contorno cerrado sin flag closed")
    for ly in ("CUT_OUTER", "CUT_INNER"):
        a, b = _largo(mo, ly), _largo(mj, ly)
        if abs(a - b) > 1e-6:
            fallos.append(f"JOIN cambió la longitud de corte en {ly}: {a:.4f} -> {b:.4f}")


def _check_ls_ready(fallos, doc_o, doc_j, uf):
    lec = _cargar_lector(uf)
    for ly in ("CUT_OUTER", "CUT_INNER"):
        sueltas = [lec.entity_to_contour(e) for e in doc_o.modelspace().query(f'LINE ARC[layer=="{ly}"]')]
        cerrados = [c for c in lec.stitch_open_contours(sueltas) if c.get("closed")]
        polis = list(doc_j.modelspace().query(f'LWPOLYLINE[layer=="{ly}"]'))
        if len(cerrados) != 1 or len(polis) != 1:
            fallos.append(f"{uf} {ly}: contornos cerrados {len(cerrados)} / polilíneas {len(polis)}")
            continue
        c = lec.entity_to_contour(polis[0])
        if not c.get("native_geometry_preserved") or not c.get("native_segments"):
            fallos.append(f"{uf} {ly}: la polilínea perdió los tramos nativos LINE/ARC para el robot")
            continue
        fo, fj = _firma(cerrados[0]["native_segments"]), _firma(c["native_segments"])
        if fo != fj:
            fallos.append(f"{uf} {ly}: tramos del robot distintos (inicio/sentido)\n    orig={fo[:3]}\n    join={fj[:3]}")


def _check_occt(fallos, doc_j):
    try:
        from engine.dxf_to_step import _lw_points_xy
    except Exception as exc:
        fallos.append(f"no se pudo importar engine.dxf_to_step: {exc}")
        return
    poli = next(iter(doc_j.modelspace().query('LWPOLYLINE[layer=="CUT_OUTER"]')))
    pts = _lw_points_xy(poli)
    per = sum(math.dist(a, b) for a, b in zip(pts, pts[1:] + pts[:1]))
    esperado = 2 * 90 + 2 * 30 + 2 * math.pi * 5
    if abs(per - esperado) > 0.05:
        fallos.append(f"OCCT aplana los arcos de la polilínea: perímetro {per:.3f} vs {esperado:.3f}")
    if pts and math.dist(pts[0], pts[-1]) < 1e-9:
        fallos.append("OCCT: polilínea cerrada con punto final duplicado")


def main() -> int:
    fallos: list[str] = []
    doc_o = _doc_original()
    doc_j = _doc_original()
    join_cut_layers(doc_j.modelspace())
    _check_join(fallos, doc_o, doc_j)
    for uf in ("UF1", "UF2"):
        _check_ls_ready(fallos, doc_o, doc_j, uf)
    _check_occt(fallos, doc_j)
    src = (RAIZ / "modules/nest_exporter.py").read_text(encoding="utf-8")
    if "join_cut_layers(msp)" not in src:
        fallos.append("nest_exporter debe unir CUT_OUTER/CUT_INNER al guardar acero")
    if fallos:
        print("FAIL:\n  " + "\n  ".join(fallos))
        return 1
    print("OK JOIN de contornos de corte (LS-READY + OCCT)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
