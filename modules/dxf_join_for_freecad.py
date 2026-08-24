"""Convierte DXF nest (LINE/ARC) a LWPOLYLINE cerradas para importDXF de FreeCAD.

FreeCAD no une bien LINE/ARC sueltos del export ANS (OUTER:0 en generador_verde).
OCCT ya hace el stitch en memoria; aquí reescribimos un DXF temporal legible por FreeCAD.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_CUT_OUTER_LAYERS = frozenset({"CUT_OUTER", "OUTER", "EXTER", "CUT_CU"})
_CUT_INNER_LAYERS = frozenset({"CUT_INNER", "INNER", "INTER", "HOLE"})
_CUT_ENTITY_TYPES = frozenset({"LINE", "ARC", "CIRCLE"})
_REPLACE_LAYERS = _CUT_OUTER_LAYERS | _CUT_INNER_LAYERS


def _layer_u(name: str) -> str:
    return str(name or "").upper().strip()


def _is_cut_layer(layer: str) -> bool:
    u = _layer_u(layer)
    return u in _REPLACE_LAYERS or any(k in u for k in ("CUT_OUTER", "CUT_INNER", "CUT_CU"))


def _layer_kind(layer: str) -> str | None:
    u = _layer_u(layer)
    if u in _CUT_OUTER_LAYERS or "CUT_OUTER" in u or u.endswith("_CUT") or u in ("CUT",):
        return "outer"
    if u in _CUT_INNER_LAYERS or any(k in u for k in ("CUT_INNER", "INNER", "INTER", "HOLE")):
        return "inner"
    return None


def _needs_join_dxf(doc) -> bool:
    msp = doc.modelspace()
    for entity in msp:
        if entity.dxftype() not in _CUT_ENTITY_TYPES:
            continue
        if _layer_kind(str(getattr(entity.dxf, "layer", "") or "")):
            return True
    return False


def _joined_paths(dxf_path: str, cache_dir: str) -> str:
    base = os.path.splitext(os.path.basename(dxf_path))[0]
    return os.path.join(cache_dir, f"_joined_{base}.dxf")


def _needs_regen(source_path: str, out_path: str) -> bool:
    try:
        if not os.path.isfile(out_path):
            return True
        return os.path.getmtime(out_path) < os.path.getmtime(source_path)
    except OSError:
        return True


def _ensure_cad_engine():
    root = Path(__file__).resolve().parents[1]
    cad = root / "CAD (OCCT)"
    cad_s = str(cad)
    if cad_s not in sys.path:
        sys.path.insert(0, cad_s)
    from engine.dxf_to_step import collect_dxf_nest  # noqa: WPS433

    return collect_dxf_nest


def _wire_to_xy_points(wire: Any, *, deflection: float = 0.002) -> list[tuple[float, float]]:
    """Discretiza wire OCCT a puntos XY (DXF en pulgadas; deflection ~0.002 in)."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_UniformDeflection
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    points: list[tuple[float, float]] = []
    exp = TopExp_Explorer(wire, TopAbs_EDGE)
    while exp.More():
        edge = TopoDS.Edge_s(exp.Current())
        curve = BRepAdaptor_Curve(edge)
        try:
            defl = GCPnts_UniformDeflection(curve, float(deflection), True)
            n = int(defl.NbPoints())
        except Exception:
            n = 0
        if n <= 0:
            exp.Next()
            continue
        for i in range(1, n + 1):
            p = defl.Value(i)
            xy = (round(float(p.X()), 6), round(float(p.Y()), 6))
            if not points or xy != points[-1]:
                points.append(xy)
        exp.Next()
    return points


def _write_joined_dxf(source_path: str, out_path: str, collect_fn) -> tuple[int, int]:
    import shutil

    import ezdxf

    geom = collect_fn(source_path)
    shutil.copy2(source_path, out_path)
    doc = ezdxf.readfile(out_path)
    msp = doc.modelspace()

    to_delete = []
    for entity in msp:
        layer = str(getattr(entity.dxf, "layer", "") or "")
        if entity.dxftype() in _CUT_ENTITY_TYPES and _layer_kind(layer):
            to_delete.append(entity)
    for entity in to_delete:
        msp.delete_entity(entity)

    n_outer = n_inner = 0
    for wire in geom.outer_wires or []:
        pts = _wire_to_xy_points(wire)
        if len(pts) >= 3:
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "CUT_OUTER"})
            n_outer += 1
    for wire in geom.inner_wires or []:
        pts = _wire_to_xy_points(wire)
        if len(pts) >= 3:
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "CUT_INNER"})
            n_inner += 1

    if n_outer <= 0 and n_inner <= 0:
        raise RuntimeError("sin contornos CUT tras join")

    # Capa CUT_CU vacía en tabla DXF (legacy) confundía perfil cobre en FreeCAD.
    if "CUT_CU" in doc.layers and not any(
        str(getattr(e.dxf, "layer", "") or "").upper() == "CUT_CU" for e in msp
    ):
        try:
            doc.layers.remove("CUT_CU")
        except Exception:
            pass

    doc.saveas(out_path)
    return n_outer, n_inner


def maybe_join_nest_dxf_for_freecad(
    dxf_path: str,
    cache_dir: str,
) -> tuple[str, str]:
    """Devuelve (ruta_import, nota). Si no hace falta join, devuelve la original."""
    dxf_path = os.path.normpath(str(dxf_path))
    if not os.path.isfile(dxf_path):
        return dxf_path, "join omitido: DXF no encontrado"

    try:
        import ezdxf
    except ImportError:
        return dxf_path, "join omitido: ezdxf no disponible"

    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception as exc:
        return dxf_path, f"join omitido: lectura DXF ({exc})"

    if not _needs_join_dxf(doc):
        return dxf_path, "join omitido: ya tiene LWPOLY en CUT"

    os.makedirs(cache_dir, exist_ok=True)
    out_path = _joined_paths(dxf_path, cache_dir)
    if not _needs_regen(dxf_path, out_path):
        return out_path, f"join cache ({os.path.basename(out_path)})"

    try:
        collect_fn = _ensure_cad_engine()
        n_outer, n_inner = _write_joined_dxf(dxf_path, out_path, collect_fn)
    except Exception as exc:
        return dxf_path, f"join falló ({exc})"

    kb = max(1, os.path.getsize(out_path) // 1024)
    return (
        out_path,
        f"join LINE/ARC->LWPOLY: outer={n_outer} inner={n_inner} ({kb} KB)",
    )
