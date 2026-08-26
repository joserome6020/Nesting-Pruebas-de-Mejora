"""Validación PARTS para piezas cobre Amada ESP. (ancho 5\")."""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import ezdxf

from interface.qt.dxf_part_geometry import (
    clasificar_contornos_cerrados,
    es_inner_layer,
    poly_area_2d,
)
from interface.qt.dxf_part_loader import load_dxf_part

from .cu_largos_nesting import (
    AMADA_FIXTURA_ANCHO_IN,
    TOL_ANCHO_IN_MIN,
    _pieza_cu_es_relieve_z,
    _solo_cortes_guillotina_vertical,
)

_CONFIG_REL = os.path.join("_config", "amada_barrenos_catalog.json")
_IN_TO_MM = 25.4

_DEFAULTS: dict[str, Any] = {
    "version": 1,
    "ancho_in": 5.0,
    "ancho_tol_in": 0.02,
    "largo_max_in": 43.125,
    "largo_tol_in": 0.02,
    "require_guillotina_outer": True,
    "min_holes": 1,
    "barreno_tol_in": 0.02,
    "oval_aspect_min": 1.15,
    "oval_aspect_max": 2.0,
    "circle_diam_in": [0.433, 0.438],
    "oval_sizes_in": [
        [0.406, 0.594],
        [0.438, 0.625],
        [0.438, 0.687],
        [0.438, 0.812],
    ],
}


def catalog_path() -> Path:
    try:
        import config as app_config

        return Path(app_config.asegurar_archivo_persistente(_CONFIG_REL))
    except Exception:
        return Path(__file__).resolve().parents[2] / _CONFIG_REL


@lru_cache(maxsize=1)
def load_amada_barrenos_catalog() -> dict[str, Any]:
    path = catalog_path()
    data = dict(_DEFAULTS)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update(raw)
        except Exception:
            pass
    data["ancho_in"] = float(data.get("ancho_in") or AMADA_FIXTURA_ANCHO_IN)
    data["ancho_tol_in"] = float(data.get("ancho_tol_in") or TOL_ANCHO_IN_MIN)
    data["largo_max_in"] = float(data.get("largo_max_in") or 43.125)
    data["largo_tol_in"] = float(data.get("largo_tol_in") or TOL_ANCHO_IN_MIN)
    data["barreno_tol_in"] = float(data.get("barreno_tol_in") or TOL_ANCHO_IN_MIN)
    data["min_holes"] = max(0, int(data.get("min_holes") or 1))
    data["require_guillotina_outer"] = bool(data.get("require_guillotina_outer", True))
    return data


def amada_largo_max_in(catalog: dict[str, Any] | None = None) -> float:
    cat = catalog if isinstance(catalog, dict) else load_amada_barrenos_catalog()
    return float(cat.get("largo_max_in") or 43.125)


def _aspect(w: float, h: float) -> float:
    if w <= 0 or h <= 0:
        return 0.0
    return max(w, h) / min(w, h)


def _dims_pieza_in(ruta_dxf: str, rot_deg: int) -> tuple[float, float] | None:
    try:
        model = load_dxf_part(str(ruta_dxf), int(rot_deg) % 360)
        if model is None:
            return None
        fc = float(model.factor_conversion) or _IN_TO_MM
        snap = model.snap_ctx
        if snap is not None and getattr(snap, "vertices", None) is not None:
            verts = snap.vertices
            if len(verts):
                return (
                    float(abs(float(verts[:, 0].max()) - float(verts[:, 0].min())) / fc),
                    float(abs(float(verts[:, 1].max()) - float(verts[:, 1].min())) / fc),
                )
        return (
            float(abs(model.max_x_raw - model.min_x_raw) / fc),
            float(abs(model.max_y_raw - model.min_y_raw) / fc),
        )
    except Exception:
        return None


def _chain_inner_shapes(doc) -> list[dict]:
    msp = doc.modelspace()
    inner_line_arc = []
    shapes: list[dict] = []
    for ent in msp:
        layer = str(getattr(ent.dxf, "layer", "") or "")
        if not es_inner_layer(layer):
            continue
        typ = ent.dxftype()
        if typ in ("LINE", "ARC"):
            inner_line_arc.append(ent)
        elif typ == "CIRCLE":
            shapes.append(
                {
                    "kind": "circle",
                    "cx": float(ent.dxf.center.x),
                    "cy": float(ent.dxf.center.y),
                    "r": float(ent.dxf.radius),
                    "area": math.pi * float(ent.dxf.radius) ** 2,
                    "rol": "inner",
                }
            )
        elif typ == "LWPOLYLINE" and ent.closed:
            pts = [(float(p[0]), float(p[1])) for p in ent.get_points("xy")]
            if len(pts) >= 3:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                minx, maxx = min(xs), max(xs)
                miny, maxy = min(ys), max(ys)
                shapes.append(
                    {
                        "kind": "poly",
                        "pts": pts,
                        "cx": (minx + maxx) / 2,
                        "cy": (miny + maxy) / 2,
                        "w": maxx - minx,
                        "h": maxy - miny,
                        "area": abs(poly_area_2d(pts)),
                        "aspect": _aspect(maxx - minx, maxy - miny),
                        "n_vert": len(pts),
                        "rol": "inner",
                    }
                )

    from interface.qt.dxf_part_loader import _agregar_shapes_desde_line_arc

    _agregar_shapes_desde_line_arc(inner_line_arc, "inner", shapes)
    _outers, inners = clasificar_contornos_cerrados(shapes)
    return inners


def _describe_hole(sh: dict, fc: float) -> dict[str, Any]:
    kind = sh.get("kind")
    if kind == "circle":
        d = 2.0 * float(sh["r"]) / fc
        return {"type": "circle", "diam_in": d}
    if kind == "poly":
        pts = sh.get("pts") or []
        if "w" in sh and "h" in sh:
            w_mm, h_mm = float(sh["w"]), float(sh["h"])
        elif pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            w_mm = max(xs) - min(xs)
            h_mm = max(ys) - min(ys)
        else:
            return {"type": "unknown"}
        w = w_mm / fc
        h = h_mm / fc
        aspect = float(sh.get("aspect") or _aspect(w_mm, h_mm))
        sm, lg = sorted([w, h])
        return {
            "type": "oval" if aspect >= 1.15 else "round_poly",
            "w_in": w,
            "h_in": h,
            "small_in": sm,
            "large_in": lg,
            "aspect": aspect,
        }
    return {"type": str(kind or "unknown")}


def extraer_barrenos_dxf(ruta_dxf: str, rot_deg: int = 0) -> list[dict[str, Any]]:
    """Barrenos CUT_INNER como contornos cerrados, dims en pulgadas."""
    model = load_dxf_part(str(ruta_dxf), int(rot_deg) % 360)
    if model is None:
        return []
    fc = float(model.factor_conversion) or _IN_TO_MM
    doc = ezdxf.readfile(str(ruta_dxf))
    inners = _chain_inner_shapes(doc)
    return [_describe_hole(sh, fc) for sh in inners]


def _outer_es_guillotina(ruta_dxf: str, rot_deg: int) -> bool:
    model = load_dxf_part(str(ruta_dxf), int(rot_deg) % 360)
    if model is None or not model.outer_rings:
        return False
    ring = [(float(x), float(y)) for x, y in model.outer_rings[0]]
    if len(ring) < 4:
        return False
    return bool(_solo_cortes_guillotina_vertical(ring))


def _match_circle(diam_in: float, catalog: dict[str, Any]) -> bool:
    tol = float(catalog.get("barreno_tol_in") or TOL_ANCHO_IN_MIN)
    for d in catalog.get("circle_diam_in") or []:
        if abs(float(diam_in) - float(d)) <= tol:
            return True
    return False


def _match_oval(small_in: float, large_in: float, catalog: dict[str, Any]) -> bool:
    tol = float(catalog.get("barreno_tol_in") or TOL_ANCHO_IN_MIN)
    for pair in catalog.get("oval_sizes_in") or []:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        os, ol = sorted([float(pair[0]), float(pair[1])])
        if abs(small_in - os) <= tol and abs(large_in - ol) <= tol:
            return True
    return False


def validar_barrenos_catalogo(
    holes: list[dict[str, Any]],
    catalog: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    cat = catalog if isinstance(catalog, dict) else load_amada_barrenos_catalog()
    min_h = int(cat.get("min_holes") or 1)
    if len(holes) < min_h:
        return (
            False,
            f"Amada ESP. requiere al menos {min_h} barreno(s) CUT_INNER "
            f"(detectados: {len(holes)}).",
        )

    asp_min = float(cat.get("oval_aspect_min") or 1.15)
    asp_max = float(cat.get("oval_aspect_max") or 2.0)

    for i, h in enumerate(holes, start=1):
        typ = str(h.get("type") or "")
        if typ == "circle":
            d = float(h.get("diam_in") or 0)
            if not _match_circle(d, cat):
                return (
                    False,
                    f"Barreno #{i}: círculo Ø{d:.3f}\" no está en catálogo Amada "
                    f"(aprobados: {cat.get('circle_diam_in')}).",
                )
            continue
        if typ in ("oval", "round_poly"):
            sm = float(h.get("small_in") or 0)
            lg = float(h.get("large_in") or 0)
            asp = float(h.get("aspect") or _aspect(sm, lg))
            if typ == "oval" and (asp < asp_min or asp > asp_max):
                return (
                    False,
                    f"Barreno #{i}: óvalo {sm:.3f}\"×{lg:.3f}\" "
                    f"(aspecto {asp:.2f}) fuera de rango Amada "
                    f"({asp_min:.2f}–{asp_max:.2f}).",
                )
            if not _match_oval(sm, lg, cat):
                return (
                    False,
                    f"Barreno #{i}: óvalo {sm:.3f}\"×{lg:.3f}\" no está en catálogo Amada.",
                )
            continue
        return (
            False,
            f"Barreno #{i}: geometría no reconocida ({typ}).",
        )
    return True, ""


def validar_candidato_amada_dxf(
    ruta_dxf: str,
    rot_deg: int = 0,
    *,
    catalog: dict[str, Any] | None = None,
) -> tuple[bool, str, int | None]:
    """
    Valida pieza para marcar ESP. Amada en PARTS.

    Por ahora solo exige ancho 5\" (±tol). Barrenos, largo y contorno no se validan.

    Returns:
        (ok, mensaje, rot_sugerida_o_None)
    """
    cat = catalog if isinstance(catalog, dict) else load_amada_barrenos_catalog()
    ancho_req = float(cat.get("ancho_in") or AMADA_FIXTURA_ANCHO_IN)
    ancho_tol = float(cat.get("ancho_tol_in") or TOL_ANCHO_IN_MIN)

    rot_actual = int(rot_deg) % 360
    dims = _dims_pieza_in(ruta_dxf, rot_actual)
    if dims is None:
        return False, "No se pudo medir el DXF. No se puede marcar ESP. Amada.", None

    _largo_x, ancho_y = dims
    rot_usar: int | None = None

    if abs(ancho_y - ancho_req) > ancho_tol:
        rot_alt = (rot_actual + 90) % 360
        dims_alt = _dims_pieza_in(ruta_dxf, rot_alt)
        if dims_alt is not None:
            largo_alt, ancho_alt = dims_alt
            if abs(ancho_alt - ancho_req) <= ancho_tol:
                return (
                    False,
                    (
                        f"Amada ESP. solo admite ancho exacto de {ancho_req:.0f}\" "
                        f"(actual: {ancho_y:.3f}\").\n\n"
                        f"Al girar 90° el ancho quedaría en {ancho_alt:.3f}\" "
                        f"y el largo en {largo_alt:.3f}\".\n"
                        "¿Girar la pieza y marcar ESP.?"
                    ),
                    rot_alt,
                )

        return (
            False,
            (
                f"Amada ESP. solo admite ancho exacto de {ancho_req:.0f}\" "
                f"(±{ancho_tol:.3f}\").\n\n"
                f"Ancho actual (Y): {ancho_y:.3f}\"."
            ),
            None,
        )

    return True, "", rot_usar
