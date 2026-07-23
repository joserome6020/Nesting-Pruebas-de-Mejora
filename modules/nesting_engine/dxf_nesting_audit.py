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

        ok += 1

    return {
        "total": len(lista_partes),
        "ok": ok,
        "omitidos": omitidos,
    }
