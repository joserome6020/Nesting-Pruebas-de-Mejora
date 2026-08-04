"""Evaluación geométrica post-nest: solapes, kerf/gap e in_holes."""
from __future__ import annotations

from typing import Any

from shapely.geometry import Polygon


IN_TO_MM = 25.4


def _piece_polys(hoja: dict) -> list[tuple[str, Polygon]]:
    out: list[tuple[str, Polygon]] = []
    for p in (hoja or {}).get("piezas") or []:
        if not isinstance(p, dict):
            continue
        nombre = str(p.get("nombre") or "")
        if nombre.startswith(("REF__", "TATUAJE__", "RETAZO_", "REMANENTE__", "CU_CORTE__")):
            continue
        rings = p.get("poligonos") or []
        if not rings or not rings[0]:
            continue
        try:
            poly = Polygon(rings[0], rings[1:] if len(rings) > 1 else None)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly is None or poly.is_empty:
                continue
            out.append((nombre, poly))
        except Exception:
            continue
    return out


def count_in_holes(hoja: dict) -> int:
    """Piezas cuyo centroide cae dentro de un hueco de otra pieza HOST* o con holes."""
    hosts: list[list[Polygon]] = []
    for nombre, poly in _piece_polys(hoja):
        if not poly.interiors:
            continue
        holes = []
        for ring in poly.interiors:
            try:
                h = Polygon(ring)
                if not h.is_empty:
                    holes.append(h)
            except Exception:
                pass
        if holes:
            hosts.append(holes)

    inside = 0
    for nombre, poly in _piece_polys(hoja):
        if nombre.upper().startswith("HOST"):
            continue
        if poly.interiors:
            # anfitrionas no cuentan como "dentro"
            continue
        c = poly.centroid
        if any(h.contains(c) for holes in hosts for h in holes):
            inside += 1
    return inside


def gap_audit(hoja: dict, *, kerf_in: float) -> dict[str, Any]:
    """Audita solapes metal y gaps entre sólidos.

    Criterio kerf: gap >= 0.92 * kerf (mm), igual que tools/_test_kerf_gap.py.
    """
    polys = _piece_polys(hoja)
    kerf_mm = max(0.0, float(kerf_in or 0.0)) * IN_TO_MM
    min_gap_mm = kerf_mm * 0.92 if kerf_mm > 0 else 0.0
    violations: list[dict[str, Any]] = []
    min_gap = None

    for i in range(len(polys)):
        ni, pi = polys[i]
        for j in range(i + 1, len(polys)):
            nj, pj = polys[j]
            try:
                if pi.intersects(pj) and not pi.touches(pj):
                    inter = pi.intersection(pj)
                    if float(inter.area) > 1.0:
                        violations.append(
                            {
                                "a": ni,
                                "b": nj,
                                "kind": "OVERLAP",
                                "area_mm2": float(inter.area),
                            }
                        )
                        continue
                gap = float(pi.distance(pj))
            except Exception:
                continue
            if min_gap is None or gap < min_gap:
                min_gap = gap
            if kerf_mm > 0 and gap + 1e-6 < min_gap_mm:
                violations.append(
                    {
                        "a": ni,
                        "b": nj,
                        "kind": "KERF",
                        "gap_mm": gap,
                        "min_gap_mm": min_gap_mm,
                    }
                )

    min_gap_in = (min_gap / IN_TO_MM) if min_gap is not None else None
    return {
        "kerf_violations": sum(1 for v in violations if v.get("kind") == "KERF"),
        "overlap_violations": sum(1 for v in violations if v.get("kind") == "OVERLAP"),
        "min_gap_in": min_gap_in,
        "min_gap_mm": min_gap,
        "details": violations[:40],
    }
