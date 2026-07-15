"""Helpers para iterar grupos de material en resultados de nesting.

El dict de resultados mezcla grupos de calibre (dicts con ``hojas``) y
metadatos por motor (p. ej. ``_nest_engine_id="arga_force"``). Cualquier
export / trazabilidad / UI debe usar estos helpers para no llamar ``.get``
sobre strings u otros metadatos.
"""
from __future__ import annotations

from typing import Any, Iterator


def es_grupo_material_nesting(clave: Any, valor: Any) -> bool:
    """True solo para grupos de material/calibre nestables (no metadatos de motor)."""
    if str(clave or "").startswith("_"):
        return False
    if not isinstance(valor, dict):
        return False
    return isinstance(valor.get("hojas"), list)


def iter_grupos_material(resultados: Any) -> Iterator[tuple[str, dict]]:
    """Yield (clave, grupo) omitiendo metadatos de motor / valores no-dict."""
    if not isinstance(resultados, dict):
        return
    for clave, valor in resultados.items():
        if es_grupo_material_nesting(clave, valor):
            yield str(clave), valor


def primer_grupo_con_hojas(resultados: Any) -> tuple[dict | None, str | None]:
    """Primera hoja y clave de grupo, o (None, None)."""
    for clave, grupo in iter_grupos_material(resultados):
        hojas = grupo.get("hojas") or []
        for hoja in hojas:
            if isinstance(hoja, dict):
                return hoja, clave
    return None, None
