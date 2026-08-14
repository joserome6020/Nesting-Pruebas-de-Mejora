"""Offset 2D tipo Autodesk OFFSET para compensación plasma.

Backends:
  1. FreeCAD ``Part.Shape.makeOffset2D`` (primario, fidelidad CAD)
  2. Semántica Clipper2/Ultra: outer+/holes− con joins redondos y densificación
     fina (fallback empaquetable; no usa ``largest polygon wins``)

Shapely ``buffer`` solo se usa en el fallback poligonal con topología Ultra;
nunca como único camino que aplana arcos a 7.5° y reescribe LWPOLYLINE.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import MultiPolygon, Polygon

# Bump al cambiar el algoritmo: invalida DXFs en Plasma Compensated/.
PLASMA_OFFSET_ALGO_VERSION = "offset2d-v5-clipper2-fallback"

Point = tuple[float, float]
Ring = list[Point]


@dataclass
class OffsetResult:
    rings: list[Ring] = field(default_factory=list)
    backend: str = ""
    collapsed_holes: int = 0
    components: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.rings) and not self.error


def densify_ring(points: Sequence[Point], *, max_seg: float) -> Ring:
    """Densifica segmentos rectos para que el offset no facetice curvas muestreadas."""
    pts = [(float(p[0]), float(p[1])) for p in points or [] if len(p) >= 2]
    if len(pts) < 2:
        return pts
    if pts[0] != pts[-1]:
        pts = pts + [pts[0]]
    step = max(float(max_seg), 1e-5)
    out: Ring = [pts[0]]
    for i in range(1, len(pts)):
        x0, y0 = out[-1]
        x1, y1 = pts[i]
        dx, dy = x1 - x0, y1 - y0
        dist = math.hypot(dx, dy)
        if dist <= step:
            if (x1, y1) != out[-1]:
                out.append((x1, y1))
            continue
        n = max(1, int(math.ceil(dist / step)))
        for k in range(1, n + 1):
            t = k / n
            out.append((x0 + dx * t, y0 + dy * t))
    if len(out) >= 2 and out[0] == out[-1]:
        out = out[:-1]
    return out


def _clean_closed_ring(points: Sequence[Point]) -> Ring | None:
    pts = [(float(p[0]), float(p[1])) for p in points or [] if len(p) >= 2]
    if len(pts) < 3:
        return None
    if pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    return pts


def _signed_area(ring: Sequence[Point]) -> float:
    if len(ring) < 3:
        return 0.0
    a = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return 0.5 * a


def _as_ccw(ring: Ring) -> Ring:
    return ring if _signed_area(ring) >= 0 else list(reversed(ring))


def _as_cw(ring: Ring) -> Ring:
    return ring if _signed_area(ring) <= 0 else list(reversed(ring))


def _poly_from_rings(outer: Ring, holes: Sequence[Ring] | None = None) -> Polygon | None:
    try:
        o = _as_ccw(list(outer))
        hs = [_as_cw(list(h)) for h in (holes or []) if len(h) >= 3]
        poly = Polygon(o, hs or None)
        if poly.is_empty:
            return None
        if not poly.is_valid:
            fixed = poly.buffer(0)
            if fixed is None or fixed.is_empty:
                return None
            if isinstance(fixed, MultiPolygon):
                # Conservar todos los componentes: no “largest wins”.
                return fixed  # type: ignore[return-value]
            poly = fixed
        return poly
    except Exception:
        return None


def _rings_from_polygon(geom) -> tuple[list[Ring], int]:
    """Extrae todos los exteriores (+huecos por cara). No descarta islas."""
    if geom is None or getattr(geom, "is_empty", True):
        return [], 0
    polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    rings: list[Ring] = []
    collapsed = 0
    for pg in polys:
        try:
            coords = list(pg.exterior.coords)
            if len(coords) >= 4:
                if coords[0] == coords[-1]:
                    coords = coords[:-1]
                rings.append([(float(x), float(y)) for x, y in coords])
            for interior in pg.interiors:
                ic = list(interior.coords)
                if len(ic) < 4:
                    collapsed += 1
                    continue
                if ic[0] == ic[-1]:
                    ic = ic[:-1]
                rings.append([(float(x), float(y)) for x, y in ic])
        except Exception:
            continue
    return rings, collapsed


def offset_closed_profile(
    outer: Sequence[Point],
    *,
    delta: float,
    holes: Sequence[Sequence[Point]] | None = None,
    prefer_freecad: bool | None = None,
    densify_max_seg: float | None = None,
) -> OffsetResult:
    """Desplaza un contorno cerrado. ``delta>0`` expande (OUTER); ``delta<0`` contrae."""
    if prefer_freecad is None:
        prefer_freecad = os.environ.get("ARGA_PLASMA_OFFSET_FREECAD", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    outer_r = _clean_closed_ring(outer)
    if outer_r is None:
        return OffsetResult(error="contorno outer inválido")
    hole_rs = [_clean_closed_ring(h) for h in (holes or [])]
    hole_rs = [h for h in hole_rs if h is not None]
    d = float(delta)
    if abs(d) <= 1e-15:
        return OffsetResult(
            rings=[outer_r] + hole_rs,
            backend="identity",
            components=1,
        )

    max_seg = densify_max_seg
    if max_seg is None:
        max_seg = max(abs(d) * 0.25, 1e-4)

    fc_err = ""
    if prefer_freecad:
        fc = _offset_freecad(outer_r, hole_rs, d, max_seg=max_seg)
        if fc.ok:
            return fc
        fc_err = fc.error or "FreeCAD offset falló"

    clip = _offset_clipper_semantics(outer_r, hole_rs, d, max_seg=max_seg)
    if clip.ok:
        if fc_err:
            clip.backend = f"{clip.backend}|freecad_fail:{fc_err[:80]}"
        return clip

    err = clip.error or fc_err or "offset falló"
    return OffsetResult(error=err)


def offset_simple_ring(
    points: Sequence[Point],
    *,
    delta: float,
    prefer_freecad: bool | None = None,
) -> OffsetResult:
    """Offset de un solo anillo cerrado (sin huecos embebidos)."""
    return offset_closed_profile(points, delta=delta, holes=None, prefer_freecad=prefer_freecad)


def offset_rings_batch(
    jobs: Sequence[dict[str, Any]],
    *,
    prefer_freecad: bool | None = None,
) -> list[OffsetResult]:
    """Offset de varios anillos. Intenta un solo FreeCADCmd (arranque único).

    Cada job: ``{"outer": [...], "delta": float, "holes": optional}``.
    """
    if prefer_freecad is None:
        flag = os.environ.get("ARGA_PLASMA_OFFSET_FREECAD", "auto").strip().lower()
        if flag in ("0", "false", "no", "off", "geos"):
            prefer_freecad = False
        elif flag in ("1", "true", "yes", "on", "freecad"):
            prefer_freecad = True
        else:
            # auto: FreeCAD si está instalado (fidelidad OFFSET); GEOS si no.
            prefer_freecad = freecad_offset_available()
    if prefer_freecad and jobs:
        batch = _offset_freecad_batch(list(jobs))
        if batch is not None and len(batch) == len(jobs) and all(r.ok for r in batch):
            return batch
    return [
        offset_closed_profile(
            j.get("outer") or [],
            delta=float(j.get("delta") or 0.0),
            holes=j.get("holes"),
            prefer_freecad=False,
        )
        for j in jobs
    ]


def _resolve_freecadcmd() -> str | None:
    try:
        from freecad_runner import _iter_freecad_candidates

        cmds = [
            p
            for p in _iter_freecad_candidates()
            if os.path.isfile(p) and "freecadcmd" in os.path.basename(p).lower()
        ]
        if cmds:
            return cmds[0]
    except Exception:
        pass
    for cand in (
        r"C:\Program Files\FreeCAD 1.0\bin\FreeCADCmd.exe",
        r"C:\Program Files\FreeCAD 0.21\bin\FreeCADCmd.exe",
        r"C:\Program Files\FreeCAD 0.22\bin\FreeCADCmd.exe",
    ):
        if os.path.isfile(cand):
            return cand
    return None


def _offset_freecad_batch(jobs: list[dict[str, Any]]) -> list[OffsetResult] | None:
    """Un solo FreeCADCmd para N anillos. None si falla el lote completo."""
    exe = _resolve_freecadcmd()
    if not exe or not jobs:
        return None
    prepared = []
    for j in jobs:
        d = float(j.get("delta") or 0.0)
        max_seg = max(abs(d) * 0.25, 1e-4)
        outer = _clean_closed_ring(j.get("outer") or [])
        if outer is None:
            return None
        holes = [_clean_closed_ring(h) for h in (j.get("holes") or [])]
        holes = [h for h in holes if h is not None]
        prepared.append(
            {
                "outer": densify_ring(outer, max_seg=max_seg),
                "holes": [densify_ring(h, max_seg=max_seg) for h in holes],
                "delta": d,
            }
        )
    with tempfile.TemporaryDirectory(prefix="arga_plasma_batch_") as td:
        td_path = Path(td)
        in_json = td_path / "in.json"
        out_json = td_path / "out.json"
        script = td_path / "offset2d_batch.py"
        in_json.write_text(json.dumps({"jobs": prepared}), encoding="utf-8")
        script_body = _FREECAD_BATCH_SCRIPT.replace(
            "__ARGA_IN_JSON__", json.dumps(str(in_json))
        ).replace("__ARGA_OUT_JSON__", json.dumps(str(out_json)))
        script.write_text(script_body, encoding="utf-8")
        try:
            proc = subprocess.run(
                [exe, str(script)],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(td_path),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
        except Exception:
            return None
        if not out_json.is_file():
            return None
        try:
            data = json.loads(out_json.read_text(encoding="utf-8"))
        except Exception:
            return None
        if data.get("error"):
            return None
        results_raw = data.get("results") or []
        if len(results_raw) != len(jobs):
            return None
        out: list[OffsetResult] = []
        for item in results_raw:
            if item.get("error"):
                out.append(OffsetResult(error=str(item["error"])))
                continue
            rings = []
            for ring in item.get("rings") or []:
                cleaned = _clean_closed_ring(ring)
                if cleaned:
                    rings.append(cleaned)
            if not rings:
                out.append(OffsetResult(error="FreeCAD batch sin anillos"))
                continue
            out.append(
                OffsetResult(
                    rings=rings,
                    backend="freecad_makeOffset2D_batch",
                    collapsed_holes=int(item.get("collapsed_holes") or 0),
                    components=int(item.get("components") or len(rings)),
                )
            )
        return out


def _offset_freecad(
    outer: Ring,
    holes: list[Ring],
    delta: float,
    *,
    max_seg: float,
) -> OffsetResult:
    exe = _resolve_freecadcmd()
    if not exe:
        return OffsetResult(error="FreeCAD no disponible")

    payload = {
        "outer": densify_ring(outer, max_seg=max_seg),
        "holes": [densify_ring(h, max_seg=max_seg) for h in holes],
        "delta": float(delta),
    }
    with tempfile.TemporaryDirectory(prefix="arga_plasma_off_") as td:
        td_path = Path(td)
        in_json = td_path / "in.json"
        out_json = td_path / "out.json"
        script = td_path / "offset2d.py"
        in_json.write_text(json.dumps(payload), encoding="utf-8")
        # Embebemos rutas en el script: FreeCADCmd a veces no hereda env bien.
        script_body = _FREECAD_OFFSET_SCRIPT.replace(
            "__ARGA_IN_JSON__",
            json.dumps(str(in_json)),
        ).replace(
            "__ARGA_OUT_JSON__",
            json.dumps(str(out_json)),
        )
        script.write_text(script_body, encoding="utf-8")
        try:
            proc = subprocess.run(
                [exe, str(script)],
                capture_output=True,
                text=True,
                timeout=90,
                cwd=str(td_path),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
        except Exception as exc:
            return OffsetResult(error=f"FreeCAD spawn: {exc}")

        if not out_json.is_file():
            err = (proc.stderr or proc.stdout or "").strip()[:400]
            return OffsetResult(error=f"FreeCAD sin salida ({proc.returncode}): {err}")
        try:
            data = json.loads(out_json.read_text(encoding="utf-8"))
        except Exception as exc:
            return OffsetResult(error=f"FreeCAD JSON inválido: {exc}")
        if data.get("error"):
            return OffsetResult(error=str(data["error"]))
        rings = []
        for ring in data.get("rings") or []:
            cleaned = _clean_closed_ring(ring)
            if cleaned:
                rings.append(cleaned)
        if not rings:
            return OffsetResult(error="FreeCAD devolvió anillos vacíos")
        return OffsetResult(
            rings=rings,
            backend="freecad_makeOffset2D",
            collapsed_holes=int(data.get("collapsed_holes") or 0),
            components=int(data.get("components") or max(1, len(rings))),
        )


_FREECAD_OFFSET_SCRIPT = r'''
import json
import os
import sys

def main():
    in_path = __ARGA_IN_JSON__
    out_path = __ARGA_OUT_JSON__
    if not in_path:
        in_path = os.environ.get("ARGA_PLASMA_OFFSET_IN", "")
    if not out_path:
        out_path = os.environ.get("ARGA_PLASMA_OFFSET_OUT", "")
    result = {"rings": [], "components": 0, "collapsed_holes": 0, "error": ""}
    try:
        import FreeCAD  # noqa: F401
        import Part
    except Exception as exc:
        result["error"] = "import FreeCAD/Part: %s" % exc
        if out_path:
            open(out_path, "w", encoding="utf-8").write(json.dumps(result))
        return

    try:
        data = json.loads(open(in_path, "r", encoding="utf-8").read())
        outer = data.get("outer") or []
        holes = data.get("holes") or []
        delta = float(data.get("delta") or 0.0)

        def wire_from_pts(pts):
            if len(pts) < 3:
                return None
            vecs = [FreeCAD.Vector(float(p[0]), float(p[1]), 0.0) for p in pts]
            if vecs[0].distanceToPoint(vecs[-1]) > 1e-9:
                vecs.append(vecs[0])
            edges = []
            for i in range(len(vecs) - 1):
                edges.append(Part.makeLine(vecs[i], vecs[i + 1]))
            return Part.Wire(edges)

        ow = wire_from_pts(outer)
        if ow is None:
            result["error"] = "outer wire inválido"
            open(out_path, "w", encoding="utf-8").write(json.dumps(result))
            return
        try:
            face = Part.Face(ow)
            for h in holes:
                hw = wire_from_pts(h)
                if hw is None:
                    continue
                try:
                    hf = Part.Face(hw)
                    face = face.cut(hf)
                except Exception:
                    pass
            shape = face
        except Exception:
            shape = ow

        # join=0 arc (más cercano a OFFSET Autodesk); intersection=True para compuestos.
        try:
            off = shape.makeOffset2D(delta, join=0, fill=False, openResult=False, intersection=True)
        except TypeError:
            off = shape.makeOffset2D(delta)

        rings = []
        collapsed = 0
        components = 0
        solids = []
        if hasattr(off, "Wires"):
            solids = list(getattr(off, "Wires") or []) or [off]
        elif hasattr(off, "Edges"):
            solids = [off]
        else:
            solids = [off]

        for w in solids:
            try:
                pts = [(float(v.X), float(v.Y)) for v in w.Vertexes]
                # Ordenar vértices a lo largo del wire si hay Oriented
                if hasattr(w, "OrderedVertexes"):
                    pts = [(float(v.X), float(v.Y)) for v in w.OrderedVertexes]
                if len(pts) < 3:
                    collapsed += 1
                    continue
                rings.append(pts)
                components += 1
            except Exception:
                collapsed += 1

        # Fallback: discretizar edges
        if not rings:
            try:
                for edge in getattr(off, "Edges", []) or []:
                    curve_pts = edge.discretize(Deflection=max(abs(delta) * 0.05, 1e-4))
                    if len(curve_pts) >= 3:
                        rings.append([(float(p.x), float(p.y)) for p in curve_pts])
                        components += 1
            except Exception as exc:
                result["error"] = "discretize: %s" % exc

        result["rings"] = rings
        result["components"] = max(components, len(rings))
        result["collapsed_holes"] = collapsed
        if not rings:
            result["error"] = result.get("error") or "makeOffset2D sin anillos"
    except Exception as exc:
        result["error"] = str(exc)

    open(out_path, "w", encoding="utf-8").write(json.dumps(result))

if True:
    main()
'''

_FREECAD_BATCH_SCRIPT = r'''
import json
import os

def _offset_one(Part, FreeCAD, outer, holes, delta):
    def wire_from_pts(pts):
        if len(pts) < 3:
            return None
        vecs = [FreeCAD.Vector(float(p[0]), float(p[1]), 0.0) for p in pts]
        if vecs[0].distanceToPoint(vecs[-1]) > 1e-9:
            vecs.append(vecs[0])
        edges = [Part.makeLine(vecs[i], vecs[i + 1]) for i in range(len(vecs) - 1)]
        return Part.Wire(edges)

    ow = wire_from_pts(outer)
    if ow is None:
        return {"rings": [], "error": "outer inválido", "components": 0, "collapsed_holes": 0}
    try:
        face = Part.Face(ow)
        for h in holes or []:
            hw = wire_from_pts(h)
            if hw is None:
                continue
            try:
                face = face.cut(Part.Face(hw))
            except Exception:
                pass
        shape = face
    except Exception:
        shape = ow
    try:
        off = shape.makeOffset2D(delta, join=0, fill=False, openResult=False, intersection=True)
    except TypeError:
        off = shape.makeOffset2D(delta)
    rings = []
    collapsed = 0
    solids = list(getattr(off, "Wires", None) or []) or [off]
    for w in solids:
        try:
            vs = getattr(w, "OrderedVertexes", None) or w.Vertexes
            pts = [(float(v.X), float(v.Y)) for v in vs]
            if len(pts) < 3:
                collapsed += 1
                continue
            rings.append(pts)
        except Exception:
            collapsed += 1
    if not rings:
        try:
            for edge in getattr(off, "Edges", []) or []:
                curve_pts = edge.discretize(Deflection=max(abs(delta) * 0.05, 1e-4))
                if len(curve_pts) >= 3:
                    rings.append([(float(p.x), float(p.y)) for p in curve_pts])
        except Exception as exc:
            return {"rings": [], "error": str(exc), "components": 0, "collapsed_holes": collapsed}
    return {
        "rings": rings,
        "error": "" if rings else "sin anillos",
        "components": len(rings),
        "collapsed_holes": collapsed,
    }

def main():
    in_path = __ARGA_IN_JSON__
    out_path = __ARGA_OUT_JSON__
    result = {"results": [], "error": ""}
    try:
        import FreeCAD
        import Part
        data = json.loads(open(in_path, "r", encoding="utf-8").read())
        for job in data.get("jobs") or []:
            result["results"].append(
                _offset_one(
                    Part,
                    FreeCAD,
                    job.get("outer") or [],
                    job.get("holes") or [],
                    float(job.get("delta") or 0.0),
                )
            )
    except Exception as exc:
        result["error"] = str(exc)
    open(out_path, "w", encoding="utf-8").write(json.dumps(result))

main()
'''


def _offset_clipper_semantics(
    outer: Ring,
    holes: list[Ring],
    delta: float,
    *,
    max_seg: float,
) -> OffsetResult:
    """Misma topología que Ultra ``buffer_paths``: outer inflate, holes shrink, Difference.

    Evita ``largest polygon wins`` sobre MultiPolygon del resultado compuesto.
    """
    abs_d = abs(float(delta))
    sign = 1.0 if delta >= 0 else -1.0
    outer_d = densify_ring(outer, max_seg=max_seg)
    holes_d = [densify_ring(h, max_seg=max_seg) for h in holes]

    try:
        # Un solo anillo: buffer directo (join redondo = Autodesk-like en vértices).
        if not holes_d:
            poly = Polygon(_as_ccw(outer_d))
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                return OffsetResult(error="outer vacío tras repair")
            buff = poly.buffer(sign * abs_d, join_style=1, quad_segs=32, mitre_limit=5.0)
            rings, collapsed = _rings_from_polygon(buff)
            if not rings:
                return OffsetResult(error="offset poligonal vacío")
            return OffsetResult(
                rings=rings,
                backend="clipper_semantics_geos",
                collapsed_holes=collapsed,
                components=len(rings),
            )

        # Outer + holes: expandir/contraer por separado y recombinar.
        outer_poly = Polygon(_as_ccw(outer_d))
        if not outer_poly.is_valid:
            outer_poly = outer_poly.buffer(0)
        if outer_poly.is_empty:
            return OffsetResult(error="outer inválido")

        outer_buff = outer_poly.buffer(abs_d, join_style=1, quad_segs=32)
        if outer_buff.is_empty:
            return OffsetResult(error="outer offset vacío")

        hole_geoms = []
        collapsed = 0
        for h in holes_d:
            hp = Polygon(_as_ccw(h))
            if not hp.is_valid:
                hp = hp.buffer(0)
            if hp.is_empty:
                collapsed += 1
                continue
            # Huecos siempre se contraen en metal (radio − abs_d) cuando OUTER se
            # expande (plasma outer+ / inner−). Si delta es negativo (anillo
            # inner solo), el caller ya pasó delta negativo sin holes.
            hc = hp.buffer(-abs_d, join_style=1, quad_segs=32)
            if hc is None or hc.is_empty:
                collapsed += 1
                continue
            hole_geoms.append(hc)

        result_geom = outer_buff
        for hg in hole_geoms:
            try:
                result_geom = result_geom.difference(hg)
            except Exception:
                continue

        # Si delta era negativo sobre perfil con huecos (poco común), invertir
        # sentido del outer ya aplicado positivamente: no aplica en plasma PARTS.
        if sign < 0 and holes_d:
            # Contraer el sólido completo.
            result_geom = result_geom.buffer(-abs_d, join_style=1, quad_segs=32)

        rings, extra_collapsed = _rings_from_polygon(result_geom)
        collapsed += extra_collapsed
        if not rings:
            return OffsetResult(error="difference offset vacío", collapsed_holes=collapsed)
        return OffsetResult(
            rings=rings,
            backend="clipper_semantics_geos",
            collapsed_holes=collapsed,
            components=len(rings),
        )
    except Exception as exc:
        return OffsetResult(error=f"clipper_semantics: {exc}")


def version_sidecar_path(dxf_path: str | Path) -> Path:
    p = Path(dxf_path)
    return p.with_suffix(p.suffix + ".offset_ver")


def write_version_sidecar(dxf_path: str | Path, *, backend: str = "") -> None:
    path = version_sidecar_path(dxf_path)
    payload = {
        "algo": PLASMA_OFFSET_ALGO_VERSION,
        "backend": backend,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def compensated_dxf_is_current(src: str | Path, dst: str | Path) -> bool:
    """True si el DXF compensado existe, es más nuevo que el origen y usa el algo actual."""
    src_p, dst_p = Path(src), Path(dst)
    if not dst_p.is_file() or dst_p.stat().st_size <= 0:
        return False
    try:
        if dst_p.stat().st_mtime < src_p.stat().st_mtime:
            return False
    except OSError:
        return False
    ver = version_sidecar_path(dst_p)
    if not ver.is_file():
        return False
    try:
        data = json.loads(ver.read_text(encoding="utf-8"))
    except Exception:
        return False
    return str(data.get("algo") or "") == PLASMA_OFFSET_ALGO_VERSION


def freecad_offset_available() -> bool:
    return bool(_resolve_freecadcmd())
