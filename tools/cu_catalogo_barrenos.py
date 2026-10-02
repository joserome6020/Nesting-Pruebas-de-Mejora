"""Catálogo de barrenos de las piezas de cobre desde los STEP de GIGA (fuente independiente).

Lee cada STEP de cobre (``GENE-*CU-*``) con OCCT y, en las caras planas de la
solera, toma cada barreno pasado de sus aristas 3D (círculo = redondo; 2 arcos +
2 rectas = ovalado). Guarda tipo, ancho, largo y dirección respecto a la solera
(``eje`` = ``largo`` si lo largo del ovalado va a lo largo de la barra, ``ancho``
si va a lo ancho). El ANS compara contra este catálogo lo que lee del DXF antes
de generar el CSV de la punzonadora.

Uso:
    python tools/cu_catalogo_barrenos.py "Z:\\...\\4. Planos" [--out _config/cu_catalogo_barrenos.json]
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import math
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "_config" / "cu_catalogo_barrenos.json"
DEFAULT_MANUAL = ROOT / "_config" / "cu_catalogo_barrenos_manual.json"
PATRON_PIEZA = re.compile(r"(GENE-[A-Z]*CU-[\d.]+-\d+)", re.I)


def _cargar_step(ruta: str):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    rd = STEPControl_Reader()
    if rd.ReadFile(ruta) != IFSelect_RetDone:
        raise RuntimeError("no se pudo leer el STEP")
    rd.TransferRoots()
    shape = rd.OneShape()
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    # Un STEP ilegible no puede pasar por "pieza sin barrenos".
    if shape.IsNull() or not TopExp_Explorer(shape, TopAbs_FACE).More():
        raise RuntimeError("STEP ilegible (OCCT lo lee vacío)")
    return shape


def _caras_planas(shape):
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        cara = TopoDS.Face_s(exp.Current())
        ad = BRepAdaptor_Surface(cara)
        if ad.GetType() == GeomAbs_Plane:
            pl = ad.Plane()
            yield cara, pl
        exp.Next()


def _wires(cara):
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    exterior = BRepTools.OuterWire_s(cara)
    exp = TopExp_Explorer(cara, TopAbs_WIRE)
    internos = []
    while exp.More():
        w = TopoDS.Wire_s(exp.Current())
        if not w.IsSame(exterior):
            internos.append(w)
        exp.Next()
    return exterior, internos


def _aristas(wire):
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
    from OCP.GCPnts import GCPnts_AbscissaPoint
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    out = []
    exp = TopExp_Explorer(wire, TopAbs_EDGE)
    while exp.More():
        e = TopoDS.Edge_s(exp.Current())
        c = BRepAdaptor_Curve(e)
        t = c.GetType()
        largo = GCPnts_AbscissaPoint.Length_s(c)
        p0 = c.Value(c.FirstParameter())
        p1 = c.Value(c.LastParameter())
        if t == GeomAbs_Circle:
            circ = c.Circle()
            out.append(("arc", circ.Radius(), circ.Location(), largo, p0, p1))
        elif t == GeomAbs_Line:
            out.append(("line", 0.0, None, largo, p0, p1))
        else:
            out.append(("otro", 0.0, None, largo, p0, p1))
        exp.Next()
    return out


def _vec(p):
    return (p.X(), p.Y(), p.Z())


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    n = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


def _clasificar_wire(wire) -> dict | None:
    """Barreno desde sus aristas: redondo o ovalado (2 arcos iguales + 2 rectas paralelas)."""
    ar = _aristas(wire)
    arcos = [a for a in ar if a[0] == "arc"]
    rectas = [a for a in ar if a[0] == "line"]
    if any(a[0] == "otro" for a in ar):
        return {"tipo": "?", "detalle": "arista no recta/circular"}
    if arcos and not rectas:
        r = arcos[0][1]
        if all(abs(a[1] - r) < 0.01 for a in arcos):
            perim = sum(a[3] for a in arcos)
            if abs(perim - 2 * math.pi * r) < 0.05:
                c = _vec(arcos[0][2])
                return {"tipo": "C", "ancho": round(2 * r, 3), "largo": round(2 * r, 3), "centro": c, "dir": None}
        return {"tipo": "?", "detalle": "arcos sin cerrar círculo"}
    # Un semicírculo puede venir partido en varias aristas: se agrupan por centro.
    centros: list[tuple[tuple, float]] = []
    for a in arcos:
        c = _vec(a[2])
        if not any(math.dist(c, cc) < 0.01 and abs(a[1] - rr) < 0.01 for cc, rr in centros):
            centros.append((c, a[1]))
    if len(centros) == 2 and len(rectas) == 2:
        r = centros[0][1]
        if abs(centros[1][1] - r) > 0.01 or abs(rectas[0][3] - rectas[1][3]) > 0.01:
            return {"tipo": "?", "detalle": "ovalado irregular"}
        recto = rectas[0][3]
        d = _norm(_sub(_vec(rectas[0][5]), _vec(rectas[0][4])))
        c1, c2 = centros[0][0], centros[1][0]
        c = tuple((a + b) / 2 for a, b in zip(c1, c2))
        return {
            "tipo": "E",
            "ancho": round(2 * r, 3),
            "largo": round(2 * r + recto, 3),
            "centro": c,
            "dir": d,
        }
    return {"tipo": "?", "detalle": f"{len(centros)} centros de arco + {len(rectas)} rectas"}


def _extension_cara(cara, eje):
    """Extensión (min, max) de la cara proyectada sobre ``eje`` (vector unitario)."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    vals = []
    exp = TopExp_Explorer(cara, TopAbs_VERTEX)
    while exp.More():
        p = BRep_Tool.Pnt_s(TopoDS.Vertex_s(exp.Current()))
        vals.append(_dot(_vec(p), eje))
        exp.Next()
    return (min(vals), max(vals)) if vals else (0.0, 0.0)


def analizar_step(ruta: str) -> dict:
    """Barrenos únicos de la pieza (cada barreno aparece en la cara de arriba y la de abajo)."""
    shape = _cargar_step(ruta)
    barrenos = []
    raros = []
    anchos_cara = collections.Counter()
    caras = []
    for cara, pl in _caras_planas(shape):
        _ext, internos = _wires(cara)
        if not internos:
            continue
        n = _norm(_vec(pl.Axis().Direction()))
        x = _norm(_vec(pl.XAxis().Direction()))
        y = _norm(_vec(pl.YAxis().Direction()))
        ex, ey = _extension_cara(cara, x), _extension_cara(cara, y)
        lx, ly = ex[1] - ex[0], ey[1] - ey[0]
        anchos_cara[round(min(lx, ly), 1)] += 1
        caras.append((cara, n, x, y, lx, ly, internos))
    if not caras:
        return {"barrenos": [], "raros": [], "ancho_solera_mm": None}
    # Ancho de solera = lado corto más común de las caras con barrenos (en piezas
    # dobladas cada tramo comparte el ancho de la solera).
    ancho_solera = anchos_cara.most_common(1)[0][0]
    vistos = set()
    for cara, n, x, y, lx, ly, internos in caras:
        # Eje "a lo ancho" de esta cara = el lado que mide el ancho de la solera.
        if abs(min(lx, ly) - ancho_solera) <= 0.6:
            eje_ancho = x if lx <= ly else y
        else:
            eje_ancho = x if abs(lx - ancho_solera) < abs(ly - ancho_solera) else y
        for w in internos:
            info = _clasificar_wire(w)
            if info is None:
                continue
            if info["tipo"] == "?":
                raros.append(info["detalle"])
                continue
            c = info["centro"]
            nn = n if max(n, key=abs) > 0 else tuple(-v for v in n)
            proy = _sub(c, tuple(v * _dot(c, nn) for v in nn))
            clave = (tuple(round(v, 1) for v in proy), tuple(round(v, 3) for v in nn))
            if clave in vistos:
                continue
            vistos.add(clave)
            eje = None
            if info["tipo"] == "E":
                eje = "ancho" if abs(_dot(info["dir"], eje_ancho)) > 0.7 else "largo"
            barrenos.append({"tipo": info["tipo"], "ancho": info["ancho"], "largo": info["largo"], "eje": eje})
    return {"barrenos": barrenos, "raros": raros, "ancho_solera_mm": ancho_solera}


def resumen_barrenos(barrenos: list[dict]) -> list[dict]:
    """Agrupa barrenos iguales: ``[{tipo, ancho, largo, eje, cantidad}]`` ordenado."""
    cnt = collections.Counter(
        (b["tipo"], round(b["ancho"], 2), round(b["largo"], 2), b.get("eje")) for b in barrenos
    )
    return [
        {"tipo": t, "ancho": a, "largo": l, "eje": e, "cantidad": q}
        for (t, a, l, e), q in sorted(cnt.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2], str(kv[0][3])))
    ]


def construir_catalogo(base: str, log=print) -> dict:
    piezas: dict[str, list[dict]] = collections.defaultdict(list)
    for raiz, _dirs, files in os.walk(base):
        for f in files:
            if not f.lower().endswith((".step", ".stp")):
                continue
            m = PATRON_PIEZA.search(f)
            if not m:
                continue
            nombre = m.group(1).upper()
            ruta = os.path.join(raiz, f)
            try:
                res = analizar_step(ruta)
            except Exception as exc:  # noqa: BLE001
                log(f"[ERROR] {nombre}: {exc} ({ruta})")
                continue
            piezas[nombre].append(
                {
                    "fuente": os.path.relpath(ruta, base),
                    "modificado": _dt.datetime.fromtimestamp(os.path.getmtime(ruta)).isoformat(timespec="seconds"),
                    "ancho_solera_mm": res["ancho_solera_mm"],
                    "barrenos": resumen_barrenos(res["barrenos"]),
                    "no_punzonables": sorted(set(res["raros"])),
                }
            )
    catalogo = {}
    for nombre, variantes in sorted(piezas.items()):
        unicas = []
        for v in sorted(variantes, key=lambda v: v["modificado"], reverse=True):
            if not any(u["barrenos"] == v["barrenos"] for u in unicas):
                unicas.append(v)
        catalogo[nombre] = {"variantes": unicas, "fuentes": sorted(v["fuente"] for v in variantes)}
    return catalogo


def mezclar_manual(catalogo: dict, ruta_manual: Path = DEFAULT_MANUAL) -> dict:
    """Agrega las piezas capturadas del plano PDF (STEP vacío) como variante ``PDF``."""
    if not ruta_manual.is_file():
        return catalogo
    manual = json.loads(ruta_manual.read_text(encoding="utf-8")).get("piezas") or {}
    for nombre, m in manual.items():
        v = {
            "fuente": "PDF: " + str(m.get("fuente") or nombre),
            "modificado": "",
            "ancho_solera_mm": m.get("ancho_solera_mm"),
            "barrenos": resumen_barrenos(
                [b for b in m["barrenos"] for _ in range(int(b.get("cantidad", 1)))]
            ),
            "no_punzonables": [],
        }
        ent = catalogo.setdefault(nombre.upper(), {"variantes": [], "fuentes": []})
        if not any(u["barrenos"] == v["barrenos"] for u in ent["variantes"]):
            ent["variantes"].append(v)
            ent["fuentes"].append(v["fuente"])
    return dict(sorted(catalogo.items()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("base", help="carpeta de planos (se recorre completa)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    cat = mezclar_manual(construir_catalogo(args.base))
    data = {
        "generado": _dt.datetime.now().isoformat(timespec="seconds"),
        "origen": args.base,
        "piezas": cat,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"catálogo: {len(cat)} piezas → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
