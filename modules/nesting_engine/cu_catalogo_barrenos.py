"""Catálogo de barrenos de las piezas de cobre (segunda protección del CSV de punzonado).

``_config/cu_catalogo_barrenos.json`` se genera con ``tools/cu_catalogo_barrenos.py``
a partir de los planos PDF de cobre (manda el plano sobre el 3D). Antes de mandar
una pieza a la punzonadora se compara lo que el analizador leyó del DXF contra lo
que dice el plano: tipo, medida, dirección respecto a la barra (un ovalado girado
90° es error) y cantidad. Si no coincide, el CSV no sale. Una pieza que no está en
el catálogo (GIGA nuevo) se valida solo con el analizador, sin avisos.
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_RELATIVO = os.path.join("_config", "cu_catalogo_barrenos.json")
PATRON_PIEZA = re.compile(r"(GENE-[A-Z]*CU-[\d.]+-\d+)", re.I)
# Holgura de medida catálogo ↔ DXF (mm); la misma que usa el herramental.
TOL_MEDIDA_MM = 0.15

_cache: dict[str, Any] = {}


def ruta_catalogo() -> Path:
    candidatos = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidatos.append(Path(meipass) / _RELATIVO)
    candidatos.append(Path(__file__).resolve().parents[2] / _RELATIVO)
    for c in candidatos:
        if c.is_file():
            return c
    return candidatos[-1]


def cargar_catalogo(ruta: str | os.PathLike | None = None) -> dict[str, Any]:
    """``{nombre: {"variantes": [...]}}``; vacío si no hay catálogo."""
    p = Path(ruta) if ruta else ruta_catalogo()
    clave = str(p)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    hit = _cache.get(clave)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        piezas = json.loads(p.read_text(encoding="utf-8")).get("piezas") or {}
    except (OSError, ValueError):
        piezas = {}
    piezas = {str(k).upper(): v for k, v in piezas.items()}
    _cache[clave] = (mtime, piezas)
    return piezas


def codigo_pieza(nombre: str, catalogo: dict | None = None) -> str | None:
    """Número de parte del catálogo contenido en ``nombre`` (el más largo, como palabra completa)."""
    n = str(nombre or "").upper()
    mejor = None
    for k in catalogo or {}:
        i = n.find(k)
        while i >= 0:
            antes = n[i - 1] if i > 0 else ""
            despues = n[i + len(k)] if i + len(k) < len(n) else ""
            if not antes.isalnum() and not despues.isalnum():
                if mejor is None or len(k) > len(mejor):
                    mejor = k
                break
            i = n.find(k, i + 1)
    if mejor:
        return mejor
    m = PATRON_PIEZA.search(n)
    return m.group(1).upper() if m else None


def _clave_dxf(tipo: str, dx: float, dy: float) -> tuple:
    """(tipo, ancho, largo, eje) de un barreno del DXF; X = largo de la barra."""
    if tipo == "C":
        d = (dx + dy) / 2.0
        return ("C", d, d, None)
    return ("E", min(dx, dy), max(dx, dy), "largo" if dx >= dy else "ancho")


def _igual(a: tuple, b: tuple, tol: float) -> bool:
    return a[0] == b[0] and a[3] == b[3] and abs(a[1] - b[1]) <= tol and abs(a[2] - b[2]) <= tol


def _desc(k: tuple) -> str:
    if k[0] == "C":
        return f"Ø{k[1]:.2f}"
    return f"ovalado {k[1]:.2f}×{k[2]:.2f} a lo {k[3]}"


def _diferencias(dxf: list[tuple], variante: dict, tol: float) -> list[str]:
    esperados = collections.Counter()
    for b in variante.get("barrenos") or []:
        k = (b["tipo"], float(b["ancho"]), float(b["largo"]), b.get("eje") if b["tipo"] == "E" else None)
        esperados[k] += int(b.get("cantidad", 1))
    leidos: list[tuple] = []
    for k in dxf:
        for e in leidos:
            if _igual(e[0], k, tol):
                e[1] += 1
                break
        else:
            leidos.append([k, 1])
    sobran: list[list] = []
    pendientes = dict(esperados)
    for k, n in leidos:
        match = next((e for e in pendientes if _igual(e, k, tol)), None)
        esp = pendientes.pop(match, 0) if match else 0
        if n > esp:
            sobran.append([k, n - esp])
        elif esp > n:
            pendientes[match] = esp - n
    faltan = [[e, n] for e, n in pendientes.items() if n]
    difs = []
    # Mismo ovalado con el otro eje: es el slot girado 90°, no otra medida.
    for s in sobran:
        for f in faltan:
            girado = (s[0][0], s[0][1], s[0][2], f[0][3])
            if s[0][0] == "E" and s[1] and f[1] and s[0][3] != f[0][3] and _igual(girado, f[0], tol):
                q = min(s[1], f[1])
                difs.append(
                    f"{q}× ovalado {f[0][1]:.2f}×{f[0][2]:.2f} GIRADO: el plano lo pide a lo "
                    f"{f[0][3]} de la barra y el DXF lo trae a lo {s[0][3]}"
                )
                s[1] -= q
                f[1] -= q
    difs += [f"{n}× {_desc(k)} de más (no está en el plano)" for k, n in sobran if n]
    difs += [f"faltan {n}× {_desc(e)} que pide el plano" for e, n in faltan if n]
    return difs


def verificar_pieza(
    nombre: str,
    barrenos: list[tuple[str, float, float]],
    *,
    catalogo: dict | None = None,
    tol_mm: float = TOL_MEDIDA_MM,
) -> tuple[str, str]:
    """``(estado, detalle)`` de una pieza contra el catálogo.

    ``barrenos``: ``(tipo, dx, dy)`` leídos del DXF con X a lo largo de la barra.
    ``estado``: ``ok`` | ``sin_catalogo`` (pieza nueva: manda solo el analizador) |
    ``no_coincide``.
    """
    cat = cargar_catalogo() if catalogo is None else catalogo
    codigo = codigo_pieza(nombre, cat)
    ent = cat.get(codigo) if codigo else None
    if not ent or not ent.get("variantes"):
        return "sin_catalogo", ""
    dxf = [_clave_dxf(t, float(dx), float(dy)) for t, dx, dy in barrenos]
    mejor: list[str] | None = None
    for v in ent["variantes"]:
        difs = _diferencias(dxf, v, tol_mm)
        if not difs:
            return "ok", ""
        if mejor is None or len(difs) < len(mejor):
            mejor = difs
    return "no_coincide", f"{nombre}: barrenos del DXF no coinciden con el plano de {codigo}: " + "; ".join(mejor or [])
