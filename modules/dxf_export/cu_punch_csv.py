"""CSV de punzonado (CNC Busbar Punching, Lijian MX602K / LJcad) por barra de cobre.

Un CSV por barra de piezas normales (con_gap) y por RTZCU. Las barras
Zapato/Botella/Z (sin_gap) siguen a láser y no llevan CSV.

Formato LJcad: 283 columnas separadas por tabulador, CRLF, sin BOM,
encabezado + 100 filas. Una fila por pieza en el orden del nest
(``Quantity`` = 1) para que la máquina reproduzca la barra tal cual:

- Primera fila (barras normales enteras): despunte de 50 mm, solo cizalla,
  ``Model`` vacío para que no se marque.
- ``Width`` = ancho de la solera (piezas con decimal se cortan al ancho de la
  solera y se recortan después en láser).
- Golpes ``X``/``Y`` relativos a la pieza (X desde su inicio, Y desde la orilla
  de la solera), ``M{n}`` = estación ``Mold{n}``; último golpe ``C`` en ``X = Length``.
"""
from __future__ import annotations

import math
import os
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from shapely.geometry import Polygon

N_GOLPES = 90
N_FILAS = 100
N_MOLDS = 8
LARGO_MIN_MAQUINA_MM = 50.0
TOL_RECT_MM = 0.05
TOL_ANCHO_MM = 0.5

ENCABEZADO: list[str] = (
    ["Model", "Quantity", "Width", "Thickness", "Length"]
    + [c for n in range(1, N_GOLPES + 1) for c in (f"X{n}", f"Y{n}", f"M{n}")]
    + [f"Mold{n}" for n in range(1, N_MOLDS + 1)]
)


class PunchCsvError(ValueError):
    """La barra no se puede punzonar tal cual (barreno sin herramienta, contorno, límites)."""


def _r2(v: float) -> str:
    q = Decimal(str(round(float(v), 4))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if q == 0:
        q = Decimal("0.00")
    return f"{q:.2f}"


def _es_virtual(nombre: str) -> bool:
    n = str(nombre or "")
    return n.startswith(
        ("CU_CORTE__", "REF__", "TATUAJE__", "RETAZO_GUILLOTINA", "REMANENTE__", "RTZCU_ZONA__")
    )


def hoja_requiere_csv_punzonado(hoja: dict | None) -> bool:
    """Barra normal (con_gap) o RTZCU de cobre largos → CNC Busbar Punching."""
    if not isinstance(hoja, dict) or not hoja.get("modo_largos_cu"):
        return False
    try:
        from modules.nesting_engine.nest_runtime_prefs import is_cu_force_dxf_step_enabled

        if is_cu_force_dxf_step_enabled():
            return False
    except Exception:
        pass
    if hoja.get("cu_rtz_virtual"):
        return True
    if hoja.get("cu_barra_especial"):
        return False
    return str(hoja.get("cu_modo_separacion_barra") or "").strip().lower() != "sin_gap"


def proceso_hoja_cobre(hoja: dict | None) -> str:
    """Etiqueta de proceso para PDF/UI de una barra de cobre largos."""
    if hoja_requiere_csv_punzonado(hoja):
        return "CNC BUSBAR PUNCHING (Normales)"
    return "LÁSER (Zapato / Botella / Z)"


def _piezas_reales(hoja: dict) -> list[dict]:
    out = []
    for p in hoja.get("piezas") or []:
        if not isinstance(p, dict) or _es_virtual(p.get("nombre", "")):
            continue
        pols = p.get("poligonos") or []
        if pols and pols[0]:
            out.append(p)
    out.sort(key=lambda p: min(float(t[0]) for t in p["poligonos"][0]))
    return out


def piezas_recorte_laser(hoja: dict) -> list[dict]:
    """Piezas más angostas que la solera: se punzonan a ancho completo y se recortan en láser."""
    ancho_bar = float(hoja.get("placa_h") or 0.0)
    out = []
    for p in _piezas_reales(hoja):
        ys = [float(t[1]) for t in p["poligonos"][0]]
        ancho = max(ys) - min(ys)
        if ancho_bar > 0 and ancho < ancho_bar - TOL_ANCHO_MM:
            out.append({"nombre": str(p.get("nombre") or ""), "ancho_mm": ancho})
    return out


def _es_rectangulo(exterior: list) -> bool:
    poly = Polygon(exterior)
    if poly.is_empty or not poly.is_valid:
        return False
    pts = list(poly.simplify(TOL_RECT_MM, preserve_topology=True).exterior.coords)[:-1]
    if len(pts) != 4:
        return False
    for i in range(4):
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % 4]
        if abs(x1 - x2) > TOL_RECT_MM and abs(y1 - y2) > TOL_RECT_MM:
            return False
    return True


def _clasificar_barreno(anillo: list) -> tuple[str, float, float, float, float] | None:
    """(tipo, cx, cy, dx, dy) para redondo (``C``) u ovalado alineado a ejes (``E``)."""
    poly = Polygon(anillo)
    if poly.is_empty or poly.area <= 0:
        return None
    c = poly.centroid
    cx, cy = float(c.x), float(c.y)
    minx, miny, maxx, maxy = poly.bounds
    dx, dy = maxx - minx, maxy - miny
    r_max = max(math.hypot(float(x) - cx, float(y) - cy) for x, y in list(poly.exterior.coords))
    if abs(dx - dy) <= 0.2:
        d = 2.0 * r_max
        if abs(poly.area / (math.pi * (d / 2.0) ** 2) - 1.0) <= 0.05:
            return "C", cx, cy, d, d
        return None
    w = min(dx, dy)
    largo = max(dx, dy)
    esperada = w * (largo - w) + math.pi * (w / 2.0) ** 2
    if esperada > 0 and abs(poly.area / esperada - 1.0) <= 0.05:
        rect = poly.minimum_rotated_rectangle
        rx0, ry0, rx1, ry1 = rect.bounds
        if abs((rx1 - rx0) - dx) <= 0.2 and abs((ry1 - ry0) - dy) <= 0.2:
            return "E", cx, cy, dx, dy
    return None


def _fila_vacia() -> dict[str, str]:
    fila = {c: "" for c in ENCABEZADO}
    fila["Quantity"] = "0"
    for c in ("Width", "Thickness", "Length"):
        fila[c] = "0.00"
    for n in range(1, N_GOLPES + 1):
        fila[f"X{n}"] = "0.00"
        fila[f"Y{n}"] = "0.00"
    for n in range(1, N_MOLDS + 1):
        fila[f"Mold{n}"] = "0"
    return fila


def _fila(
    model: str,
    width_mm: float,
    thickness_mm: float,
    length_mm: float,
    golpes: list[tuple[float, float, str]],
    molds: list[str],
) -> dict[str, str]:
    fila = _fila_vacia()
    fila.update(
        Model=model,
        Quantity="1",
        Width=_r2(width_mm),
        Thickness=_r2(thickness_mm),
        Length=_r2(length_mm),
    )
    for n, (x, y, m) in enumerate(golpes, start=1):
        fila[f"X{n}"] = _r2(x)
        fila[f"Y{n}"] = _r2(y)
        fila[f"M{n}"] = m
    for n, code in enumerate(molds[:N_MOLDS], start=1):
        fila[f"Mold{n}"] = code
    return fila


def _model_texto(nombre: str) -> str:
    return " ".join(str(nombre or "").replace("\t", " ").split())


def construir_filas_barra(
    hoja: dict,
    *,
    thickness_mm: float,
    estaciones: list | None = None,
    etiqueta: str = "",
) -> list[dict[str, str]]:
    """Filas CSV de una barra. Lanza ``PunchCsvError`` con todos los problemas juntos."""
    from modules.nesting_engine.cu_punch_tooling import (
        cargar_estaciones,
        codigos_molds,
        estacion_para_barreno,
    )

    ests = estaciones if estaciones is not None else cargar_estaciones()
    molds = codigos_molds(ests)
    ancho_bar = float(hoja.get("placa_h") or 0.0)
    tag = etiqueta or str(hoja.get("sheet_code") or hoja.get("placa_id") or "barra")
    errores: list[str] = []
    filas: list[dict[str, str]] = []

    despunte = float(hoja.get("cu_despunte_mm") or 0.0)
    if despunte > 0.5 and not hoja.get("cu_rtz_virtual"):
        filas.append(_fila("", ancho_bar, thickness_mm, despunte, [(despunte, 0.0, "C")], molds))

    for p in _piezas_reales(hoja):
        nombre = str(p.get("nombre") or "PIEZA")
        pols = p["poligonos"]
        exterior = pols[0]
        xs = [float(t[0]) for t in exterior]
        x0, x1 = min(xs), max(xs)
        largo = x1 - x0
        if not _es_rectangulo(exterior):
            errores.append(
                f"{nombre}: contorno no rectangular (muesca/chaflán/radio); "
                "la punzonadora solo corta a escuadra"
            )
            continue
        if largo < LARGO_MIN_MAQUINA_MM - 0.01:
            errores.append(
                f"{nombre}: largo {largo:.2f} mm < mínimo de la máquina "
                f"({LARGO_MIN_MAQUINA_MM:.0f} mm)"
            )
            continue
        golpes: list[tuple[float, float, str]] = []
        for anillo in pols[1:]:
            info = _clasificar_barreno(anillo)
            if info is None:
                xs_h = [float(t[0]) for t in anillo]
                ys_h = [float(t[1]) for t in anillo]
                errores.append(
                    f"{nombre}: recorte interior no punzonable "
                    f"({max(xs_h) - min(xs_h):.2f}×{max(ys_h) - min(ys_h):.2f} mm); "
                    "solo redondos u ovalados"
                )
                continue
            tipo, cx, cy, dx, dy = info
            idx = estacion_para_barreno(tipo, dx, dy, ests)
            if idx is None:
                desc = f"Ø{dx:.2f}" if tipo == "C" else f"ovalado {dx:.2f}×{dy:.2f} (largo×ancho)"
                errores.append(f"{nombre}: barreno {desc} mm sin herramienta montada")
                continue
            golpes.append((cx - x0, cy, f"M{idx}"))
        golpes.sort(key=lambda g: (round(g[0], 3), round(g[1], 3)))
        if len(golpes) + 1 > N_GOLPES:
            errores.append(
                f"{nombre}: {len(golpes)} barrenos; la máquina admite {N_GOLPES - 1} por pieza"
            )
            continue
        golpes.append((largo, 0.0, "C"))
        filas.append(_fila(_model_texto(nombre), ancho_bar, thickness_mm, largo, golpes, molds))

    if len(filas) > N_FILAS:
        errores.append(f"{len(filas)} filas; LJcad admite {N_FILAS} por archivo")
    if errores:
        raise PunchCsvError(f"[{tag}] " + " | ".join(errores))
    return filas


def escribir_csv(path: str, filas: list[dict[str, str]]) -> str:
    vacias = [_fila_vacia() for _ in range(max(0, N_FILAS - len(filas)))]
    lineas = ["\t".join(ENCABEZADO)]
    for f in list(filas) + vacias:
        lineas.append("\t".join(f.get(c, "") for c in ENCABEZADO))
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\r\n".join(lineas) + "\r\n")
    return path
