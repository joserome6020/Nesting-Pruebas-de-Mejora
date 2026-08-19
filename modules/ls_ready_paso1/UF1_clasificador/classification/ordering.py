# -*- coding: utf-8 -*-
"""Orden exclusivo cama A: MARK derecha->izquierda y CUT prioridad tamaño (Grandes Izq->Der, Pequeñas Der->Izq)."""


def _piece_area(p):
    bbox = p.get("bbox_dxf") or [0.0, 0.0, 0.0, 0.0]
    w = max(0.0, float(bbox[2]) - float(bbox[0]))
    h = max(0.0, float(bbox[3]) - float(bbox[1]))
    return w * h


def sort_pieces_for_mark(pieces):
    """Cama A MARK: derecha -> izquierda físico, en DXF X descendente."""
    return sorted(
        pieces,
        key=lambda p: (
            -float(p.get("max_x") or 0.0),
            -float((p.get("bbox_dxf") or [0, 0, 0, 0])[3]),
            str(p.get("id") or ""),
        ),
    )


def sort_pieces_for_cut(pieces_mark_order):
    """Cama A CUT:
    Si hay diferencia significativa de tamaño (max_area / min_area >= 2.5):
      1. Piezas GRANDES: Izquierda -> Derecha (X ascendente).
      2. Piezas PEQUEÑAS: Derecha -> Izquierda (X descendente).
    Si no hay gran diferencia de tamaño:
      Izquierda -> Derecha continuo.
    """
    pieces = list(pieces_mark_order or [])
    if len(pieces) <= 1:
        return pieces

    areas = [_piece_area(p) for p in pieces]
    max_area = max(areas)
    min_area = min(areas)

    # Si hay diferencia de tamaño de al menos 2.5x entre la pieza mayor y menor
    if max_area > 0 and (max_area / max(min_area, 1e-6)) >= 2.5:
        threshold = 0.25 * max_area
        big_pieces = [p for p, a in zip(pieces, areas) if a >= threshold]
        small_pieces = [p for p, a in zip(pieces, areas) if a < threshold]

        if big_pieces and small_pieces:
            # Pasada 1: Grandes Izquierda -> Derecha (X ascendente)
            sorted_big = sorted(
                big_pieces,
                key=lambda p: (
                    float((p.get("bbox_dxf") or [0, 0, 0, 0])[0]),
                    -float((p.get("bbox_dxf") or [0, 0, 0, 0])[3]),
                    str(p.get("id") or ""),
                ),
            )
            # Pasada 2: Pequeñas Derecha -> Izquierda (X descendente)
            sorted_small = sorted(
                small_pieces,
                key=lambda p: (
                    -float((p.get("bbox_dxf") or [0, 0, 0, 0])[2]),
                    -float((p.get("bbox_dxf") or [0, 0, 0, 0])[3]),
                    str(p.get("id") or ""),
                ),
            )
            return sorted_big + sorted_small

    # Fallback sin clasificación de tamaño: Izquierda -> Derecha estándar
    return list(reversed(pieces_mark_order))


def sort_figure_strokes_der_to_izq(figure_strokes):
    """Figuras en sentido MARK: centro X descendente."""
    return sorted(
        figure_strokes or [],
        key=lambda s: (
            -float((s.get("center_dxf") or [0.0, 0.0])[0]),
            -float((s.get("center_dxf") or [0.0, 0.0])[1]),
            int(s.get("source_index") or 0),
        ),
    )

