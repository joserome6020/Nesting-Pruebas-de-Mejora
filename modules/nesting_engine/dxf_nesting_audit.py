"""Auditoría de DXF listos para nesting (geometría válida vs omitidos)."""
from __future__ import annotations

import os

from modules.nesting_engine.geometry_parser import recuperar_geometria_robusta_detalle

_AUDIT_VACIO = {"total": 0, "ok": 0, "omitidos": []}


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

        pieza, _mat, _qty, _cal, _st, ruta = fila
        archivo = os.path.basename(ruta) if ruta else ""

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

        ok += 1

    return {
        "total": len(lista_partes),
        "ok": ok,
        "omitidos": omitidos,
    }
