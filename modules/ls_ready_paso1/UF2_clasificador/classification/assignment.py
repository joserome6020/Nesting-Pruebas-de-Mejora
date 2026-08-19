# -*- coding: utf-8 -*-
"""Asignacion de geometrias a piezas (cascada poligono -> mayoría -> bbox)."""
from .geometry import (
    bbox_center,
    bbox_of_points,
    dist2d,
    point_in_bbox,
    point_in_polygon,
    points_inside_ratio,
    polygon_centroid,
)


def _piece_polygon(piece):
    """
    Devuelve el poligono real del contorno exterior de la pieza.

    En la estructura actual, cut_outer guarda el contorno dentro de
    cut_outer["contour"]. Antes se buscaba cut_outer["points"], lo cual
    dejaba vacia la prueba por poligono y obligaba a caer a bbox/fallback.
    """
    outer = piece.get("cut_outer") or {}
    contour = outer.get("contour") or {}
    return contour.get("points") or outer.get("points") or []


def _piece_bbox(piece):
    return piece.get("bbox_dxf")


def assign_geometry_to_piece(geometry_points, pieces, margin_mm, majority_ratio):
    """
    Devuelve (piece_index, method, warnings).
    """
    warnings = []
    points = geometry_points or []
    bb = bbox_of_points(points)
    centroid = polygon_centroid(points) if points else (0.0, 0.0)

    # 1) Centroide dentro del poligono cut_outer
    hits_poly = []
    for idx, piece in enumerate(pieces):
        poly = _piece_polygon(piece)
        if poly and point_in_polygon(centroid, poly):
            hits_poly.append(idx)
    if len(hits_poly) == 1:
        return hits_poly[0], "centroid_in_polygon", warnings
    if len(hits_poly) > 1:
        best = _closest_piece_by_center(centroid, [pieces[i] for i in hits_poly])
        piece_idx = pieces.index(best)
        warnings.append("centroid_in_multiple_polygons; chose nearest center")
        return piece_idx, "centroid_in_polygon_nearest", warnings

    # 2) Mayoría de puntos dentro del poligono
    ratio_hits = []
    for idx, piece in enumerate(pieces):
        poly = _piece_polygon(piece)
        if not poly:
            continue
        ratio = points_inside_ratio(points, poly)
        if ratio >= float(majority_ratio):
            ratio_hits.append((idx, ratio))
    if ratio_hits:
        ratio_hits.sort(key=lambda t: (-t[1], t[0]))
        if len(ratio_hits) == 1 or ratio_hits[0][1] > ratio_hits[1][1]:
            return ratio_hits[0][0], "points_majority_in_polygon", warnings
        best_idx = ratio_hits[0][0]
        warnings.append("points_majority_tie; chose highest ratio")
        return best_idx, "points_majority_in_polygon", warnings

    # 3) Centro del bbox dentro del bbox de la pieza
    if bb:
        c = bbox_center(bb)
        hits_bbox = []
        for idx, piece in enumerate(pieces):
            pbb = _piece_bbox(piece)
            if pbb and point_in_bbox(c, pbb, margin=margin_mm):
                hits_bbox.append(idx)
        if len(hits_bbox) == 1:
            return hits_bbox[0], "bbox_center_in_piece_bbox", warnings
        if len(hits_bbox) > 1:
            best = _closest_piece_by_center(c, [pieces[i] for i in hits_bbox])
            warnings.append("bbox_center_in_multiple_pieces; chose nearest center")
            return pieces.index(best), "bbox_center_in_piece_bbox_nearest", warnings

    # 4) Pieza mas cercana por centro
    if not pieces:
        return 0, "no_pieces_fallback", ["no pieces defined"]
    best_piece = _closest_piece_by_center(centroid, pieces)
    warnings.append("fallback_nearest_piece_center")
    return pieces.index(best_piece), "nearest_piece_center", warnings


def _closest_piece_by_center(point, piece_list):
    return min(
        piece_list,
        key=lambda p: dist2d(point, tuple(p.get("center_dxf") or (0.0, 0.0))),
    )
