"""Catálogo canónico MATERIAL / CALIBRE para edición en PARTS.

Los valores coinciden con lo que el nest usa en la clave `{cal}_{mat}`:
- material vía `normalizar_material_autodxf`
- calibre vía `snap_calibre_token` / tablas Herinox
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from interface.autodxf_metadata import normalizar_material_autodxf
from modules.arga_gauge_snap import (
    EXACT_DECIMALS,
    fmt_decimal,
    gauge_table_for_material,
    snap_calibre_token,
)

# Nombres que el ANS escribe en filas PARTS / claves de nest (post-AutoDXF).
MATERIALES_ANS_BASE: tuple[str, ...] = (
    "GALVANIZADO",
    "A 36",
    "CARBONO",
    "INOXIDABLE",
    "ALUMINIO",
    "CU",
)


def canonizar_material(texto: str, *, default: str = "CARBONO") -> str:
    return normalizar_material_autodxf(texto, default=default)


def canonizar_calibre(calibre: str, material: str = "") -> str:
    mat = canonizar_material(material, default="") if material else ""
    snapped = snap_calibre_token(calibre, mat or material)
    return str(snapped or calibre or "").strip()


def clave_nest_pieza(calibre: str, material: str) -> str:
    """Misma forma que MotorNesting: `{CAL}_{MAT}` en mayúsculas."""
    mat = canonizar_material(material, default="")
    cal = canonizar_calibre(calibre, mat) or str(calibre or "").strip()
    return f"{str(cal).strip().upper()}_{str(mat).strip().upper()}"


def _calibres_tabla(material: str) -> set[str]:
    out: set[str] = set()
    tabla = gauge_table_for_material(material)
    for inches in tabla.values():
        out.add(fmt_decimal(float(inches)))
    for ex in EXACT_DECIMALS:
        out.add(fmt_decimal(float(ex)))
    return out


def _material_coincide_stock(mat_ans: str, mat_stock: str) -> bool:
    """True si el material de placa Herinox pertenece a la familia ANS elegida."""
    a = canonizar_material(mat_ans, default="")
    b = canonizar_material(mat_stock, default="")
    if not a or not b:
        return False
    if a == b:
        return True
    # Nest empata GALVANIZADO ↔ A 36 GALV vía normalize_material; aquí ambos
    # caen en familias steel — incluir stock A 36 GALV al elegir GALVANIZADO.
    if a == "GALVANIZADO" and ("GALV" in b or b == "GALVANIZADO"):
        return True
    if b == "GALVANIZADO" and ("GALV" in a or a == "GALVANIZADO"):
        return True
    if a == "INOXIDABLE" and b == "INOXIDABLE":
        return True
    if a == "ALUMINIO" and b == "ALUMINIO":
        return True
    if a in ("A 36", "CARBONO") and b in ("A 36", "CARBONO"):
        return True
    return False


def _thickness_de_fila(row: Sequence[Any]) -> str:
    if not row:
        return ""
    raw = str(row[0] if len(row) > 0 else "").strip()
    if not raw or raw.lower() == "nan":
        return ""
    try:
        return fmt_decimal(float(raw.replace(",", ".")))
    except Exception:
        return raw


def _material_de_fila(row: Sequence[Any]) -> str:
    if not row or len(row) < 2:
        return ""
    raw = str(row[1] or "").strip()
    if not raw or raw.lower() == "nan":
        return ""
    return raw


def list_materiales_ans(
    plate_rows: Optional[Iterable[Sequence[Any]]] = None,
) -> list[str]:
    """Lista ordenada de materiales canónicos ANS (+ stock normalizado)."""
    seen: dict[str, None] = {}
    for m in MATERIALES_ANS_BASE:
        seen[m] = None
    for row in plate_rows or ():
        raw = _material_de_fila(row)
        if not raw:
            continue
        canon = canonizar_material(raw, default="")
        if canon:
            seen[canon] = None
    return list(seen.keys())


def list_calibres_ans(
    material: str,
    plate_rows: Optional[Iterable[Sequence[Any]]] = None,
) -> list[str]:
    """Decimales snapeados de tabla Herinox + thicknesses de stock para el material."""
    mat = canonizar_material(material, default="CARBONO") if material else "CARBONO"
    vals = _calibres_tabla(mat)
    for row in plate_rows or ():
        raw_mat = _material_de_fila(row)
        if raw_mat and not _material_coincide_stock(mat, raw_mat):
            continue
        thk = _thickness_de_fila(row)
        if thk:
            vals.add(canonizar_calibre(thk, mat) or thk)

    def _key(s: str) -> tuple:
        try:
            return (0, float(s))
        except Exception:
            return (1, s)

    return sorted(vals, key=_key)


def mutar_fila_pieza(
    datos: list,
    ruta: str,
    *,
    material: Optional[str] = None,
    calibre: Optional[str] = None,
) -> bool:
    """Actualiza material/calibre de la fila con esa ruta. Devuelve True si cambió."""
    ruta_n = str(ruta or "").strip()
    if not ruta_n or (material is None and calibre is None):
        return False
    changed = False
    for i, fila in enumerate(list(datos or [])):
        if isinstance(fila, dict):
            r = str(fila.get("ruta") or fila.get("path") or fila.get("dxf") or "")
            if r != ruta_n:
                continue
            mat_cur = str(fila.get("material") or "")
            cal_cur = str(fila.get("calibre") or "")
            mat_new = canonizar_material(material, default=mat_cur) if material is not None else mat_cur
            cal_src = calibre if calibre is not None else cal_cur
            cal_new = canonizar_calibre(cal_src, mat_new) if cal_src else cal_src
            if mat_new != mat_cur or cal_new != cal_cur:
                fila = dict(fila)
                fila["material"] = mat_new
                fila["calibre"] = cal_new
                datos[i] = fila
                changed = True
            break
        if not isinstance(fila, (list, tuple)):
            continue
        vals = list(fila)
        while len(vals) < 6:
            vals.append("")
        if str(vals[5] or "") != ruta_n:
            continue
        mat_cur = str(vals[1] or "")
        cal_cur = str(vals[3] or "")
        mat_new = canonizar_material(material, default=mat_cur) if material is not None else mat_cur
        cal_src = calibre if calibre is not None else cal_cur
        cal_new = canonizar_calibre(cal_src, mat_new) if cal_src else cal_src
        if mat_new != mat_cur or cal_new != cal_cur:
            vals[1] = mat_new
            vals[3] = cal_new
            datos[i] = type(fila)(vals) if isinstance(fila, tuple) else vals
            changed = True
        break
    return changed


def mutar_pieza_en_listas(
    *listas: Optional[list],
    ruta: str,
    material: Optional[str] = None,
    calibre: Optional[str] = None,
) -> bool:
    """Aplica la misma mutación a varias listas (partes / editable_inputs / lotes)."""
    any_changed = False
    for datos in listas:
        if datos is None:
            continue
        if mutar_fila_pieza(datos, ruta, material=material, calibre=calibre):
            any_changed = True
    return any_changed
