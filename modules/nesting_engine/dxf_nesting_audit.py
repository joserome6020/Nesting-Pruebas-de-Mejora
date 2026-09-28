"""Auditoría de DXF listos para nesting (geometría válida vs omitidos)."""
from __future__ import annotations

import os

from modules.nesting_engine.geometry_parser import recuperar_geometria_robusta_detalle

_AUDIT_VACIO = {"total": 0, "ok": 0, "omitidos": []}
_MIN_AREA_MM2 = 1.0
_MIN_EDGE_MM = 0.5


def _fila_parte(item) -> tuple[str, str, str, str, str, str] | None:
    try:
        pieza, mat, qty, cal, st, ruta = item
        return (
            str(pieza or "").strip(),
            str(mat or "").strip(),
            str(qty or "").strip(),
            str(cal or "").strip(),
            str(st or "").strip(),
            str(ruta or "").strip(),
        )
    except Exception:
        return None


def _validar_meta_fila(pieza, mat, qty, cal) -> str | None:
    if not pieza:
        return "Nombre de pieza vacío en PARTS."
    if not mat:
        return "Material vacío en PARTS."
    if not cal:
        return "Calibre vacío en PARTS."
    try:
        q = int(str(qty).strip())
    except Exception:
        return f"Cantidad inválida ({qty!r}); debe ser entero > 0."
    if q <= 0:
        return f"Cantidad inválida ({q}); debe ser > 0."
    return None


def _validar_poly_input(poly) -> str | None:
    """Reglas extra sobre geometría ya parseada (área, bounds, validez)."""
    if poly is None:
        return "Geometría None tras parser."
    try:
        if poly.is_empty:
            return "Geometría vacía."
    except Exception as exc:
        return f"Geometría no usable: {exc}"
    try:
        if not bool(poly.is_valid):
            return "Geometría inválida (self-intersection / topology)."
    except Exception:
        pass
    try:
        area = float(poly.area or 0.0)
    except Exception:
        return "No se pudo medir área de la pieza."
    if area < _MIN_AREA_MM2:
        return f"Área demasiado pequeña ({area:.4f} mm²)."
    try:
        minx, miny, maxx, maxy = poly.bounds
        w = float(maxx - minx)
        h = float(maxy - miny)
    except Exception:
        return "No se pudo medir bounds de la pieza."
    if w < _MIN_EDGE_MM or h < _MIN_EDGE_MM:
        return f"Contorno degenerado ({w:.3f}×{h:.3f} mm)."
    # Contorno abierto / DXF comprometido: bbox grande pero casi sin área.
    bbox_area = max(w * h, 1.0)
    if area < max(_MIN_AREA_MM2, bbox_area * 0.02):
        return (
            f"DXF corrupto o contorno abierto "
            f"(área {area:.2f} mm² vs bbox {w:.1f}×{h:.1f} mm)."
        )
    return None


def _validar_area_neta_pieza(ruta: str) -> str | None:
    """Misma métrica del panel DETALLE: área neta de contornos realmente cerrados.

    El parser de nesting a veces reconstruye un polígono usable a partir de
    fragmentos; el visor (y el export láser) siguen viendo AREA NETA 0. Si el
    detalle muestra área ≈ 0 con span grande, la pieza debe ir a omitidos/FALLO.
    """
    try:
        from interface.qt.dxf_part_loader import load_dxf_part
    except Exception:
        return None
    try:
        model = load_dxf_part(ruta)
    except Exception as exc:
        return f"No se pudo medir área neta del DXF: {exc}"
    if model is None:
        return "No se pudo cargar el DXF para métricas de pieza."
    try:
        area = float(getattr(model, "area_neta", 0.0) or 0.0)
        span_x = abs(float(model.max_x_raw) - float(model.min_x_raw))
        span_y = abs(float(model.max_y_raw) - float(model.min_y_raw))
    except Exception as exc:
        return f"Métricas de pieza ilegibles: {exc}"
    span_max = max(span_x, span_y)
    # Unidades DXF raw (pulgadas típicas Inventor). Umbral holgado vs ruido.
    if span_max < 0.25:
        return None
    bbox_area = max(span_x * span_y, 1e-9)
    if area <= 1e-6:
        return (
            f"DXF corrupto o contorno abierto "
            f"(área neta {area:.4f} vs span {span_x:.2f}×{span_y:.2f})."
        )
    if area < bbox_area * 0.02:
        return (
            f"DXF corrupto o contorno abierto "
            f"(área neta {area:.4f} vs span {span_x:.2f}×{span_y:.2f})."
        )
    return None


def auditar_lista_partes(lista_partes) -> dict:
    """
    Valida cada fila de PARTS contra el parser de geometría usado por el motor.
    Retorna total, ok y omitidos con motivo exacto.
    """
    if not lista_partes:
        return dict(_AUDIT_VACIO)

    ok = 0
    omitidos: list[dict] = []

    for item in lista_partes:
        fila = _fila_parte(item)
        if not fila:
            omitidos.append(
                {
                    "pieza": "(fila inválida)",
                    "ruta": "",
                    "archivo": "",
                    "error": "Formato de fila PARTS inválido.",
                }
            )
            continue

        pieza, mat, qty, cal, _st, ruta = fila
        archivo = os.path.basename(ruta) if ruta else ""

        err_meta = _validar_meta_fila(pieza, mat, qty, cal)
        if err_meta:
            omitidos.append(
                {
                    "pieza": pieza or archivo or "(sin nombre)",
                    "ruta": ruta,
                    "archivo": archivo,
                    "error": err_meta,
                }
            )
            continue

        if not ruta:
            omitidos.append(
                {
                    "pieza": pieza or archivo or "(sin nombre)",
                    "ruta": "",
                    "archivo": archivo,
                    "error": "Sin ruta DXF asociada en PARTS.",
                }
            )
            continue

        if not os.path.isfile(ruta):
            omitidos.append(
                {
                    "pieza": pieza or archivo,
                    "ruta": ruta,
                    "archivo": archivo,
                    "error": f"Archivo no encontrado: {ruta}",
                }
            )
            continue

        poly, _marks, err = recuperar_geometria_robusta_detalle(ruta)
        if poly is None:
            omitidos.append(
                {
                    "pieza": pieza or archivo,
                    "ruta": ruta,
                    "archivo": archivo,
                    "error": err or "No se pudo extraer geometría de corte del DXF.",
                }
            )
            continue

        err_poly = _validar_poly_input(poly)
        if err_poly:
            omitidos.append(
                {
                    "pieza": pieza or archivo,
                    "ruta": ruta,
                    "archivo": archivo,
                    "error": err_poly,
                }
            )
            continue

        err_neta = _validar_area_neta_pieza(ruta)
        if err_neta:
            omitidos.append(
                {
                    "pieza": pieza or archivo,
                    "ruta": ruta,
                    "archivo": archivo,
                    "error": err_neta,
                }
            )
            continue

        ok += 1

    return {
        "total": len(lista_partes),
        "ok": ok,
        "omitidos": omitidos,
    }
