"""Puntos de LWPOLYLINE respetando bulge (arcos).

Processed Files y el join de export guardan arcos como bulge dentro de la
polilínea. Leer solo ``get_points("xy")`` reduce cada arco a su cuerda: un
anillo o una ranura queda casi sin área y el bbox pierde los lóbulos.
"""
from __future__ import annotations


def puntos_lwpolyline(entity, distance: float = 0.01) -> list[tuple[float, float]]:
    """Vértices XY con los arcos aplanados a ``distance`` (unidades del DXF).

    Una polilínea cerrada devuelve el primer punto repetido al final.
    """
    xyb = list(entity.get_points("xyb"))
    if any(abs(float(p[-1] or 0.0)) > 1e-12 for p in xyb):
        try:
            from ezdxf import path

            return [
                (float(v[0]), float(v[1]))
                for v in path.make_path(entity).flattening(distance=max(float(distance), 1e-6))
            ]
        except Exception:
            pass
    pts = [(float(p[0]), float(p[1])) for p in xyb]
    if pts and bool(getattr(entity, "closed", False)) and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts
