"""CSV de punzonado (CNC Busbar Punching, Lijian MX602K / LJcad) por barra de cobre.

Un CSV por barra de piezas normales (con_gap) y por RTZCU. Las barras
Zapato/Botella/Z (sin_gap) siguen a láser y no llevan CSV.

Formato LJcad igual al archivo de planta (``pruebas jose.csv``): 283 columnas
separadas por tabulador (``Name, Num, Width, High, Length, X1, Y1, M1 … M90,
TOOL1..TOOL8``), CRLF, sin BOM, solo las filas con piezas. Una fila por pieza
en el orden del nest (``Num`` = 1) para que la máquina reproduzca la barra:

- Sin fila de despunte: el despunte (6 mm) es solo visual en el nest; el
  software de la máquina hace el suyo por defecto (~5.8 mm).
- Primer golpe de cada pieza = grabado ``M100`` del ``Name`` (vertical) en
  ``Y`` fija (30 mm); X = centro de la franja libre antes del primer barreno.
- ``Width`` = ancho de la solera (piezas con decimal se cortan al ancho de la
  solera y se recortan después en láser).
- Golpes ``X``/``Y`` relativos a la pieza (X desde su inicio, Y desde la orilla
  de la solera), ``M{n}`` = estación ``TOOL{n}``. Sin golpe ``C``: la máquina
  corta sola en ``Length``.
"""
from __future__ import annotations

import math
import os
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from shapely.geometry import LineString, Point, Polygon

N_GOLPES = 90
N_FILAS = 100
N_MOLDS = 8
LARGO_MIN_MAQUINA_MM = 50.0
TOL_RECT_MM = 0.05
TOL_ANCHO_MM = 0.5

ENCABEZADO: list[str] = (
    ["Name", "Num", "Width", "High", "Length"]
    + [c for n in range(1, N_GOLPES + 1) for c in (f"X{n}", f"Y{n}", f"M{n}")]
    + [f"TOOL{n}" for n in range(1, N_MOLDS + 1)]
)


class PunchCsvError(ValueError):
    """La barra no se puede punzonar tal cual (barreno sin herramienta, contorno, límites)."""


def _r2(v: float) -> str:
    """2 decimales (ROUND_HALF_UP) sin ceros sobrantes: ``152.4``, ``900``, ``95.25``."""
    q = Decimal(str(round(float(v), 4))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if q == 0:
        return "0"
    txt = f"{q:.2f}".rstrip("0").rstrip(".")
    return txt or "0"


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
        return "CNC BUSBAR PUNCHING"
    return "LÁSER"


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
    fila["Num"] = "0"
    for c in ("Width", "High", "Length"):
        fila[c] = "0"
    for n in range(1, N_GOLPES + 1):
        fila[f"X{n}"] = "0"
        fila[f"Y{n}"] = "0"
    for n in range(1, N_MOLDS + 1):
        fila[f"TOOL{n}"] = "0"
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
        Name=model,
        Num="1",
        Width=_r2(width_mm),
        High=_r2(thickness_mm),
        Length=_r2(length_mm),
    )
    for n, (x, y, m) in enumerate(golpes, start=1):
        fila[f"X{n}"] = _r2(x)
        fila[f"Y{n}"] = _r2(y)
        fila[f"M{n}"] = m
    for n, code in enumerate(molds[:N_MOLDS], start=1):
        fila[f"TOOL{n}"] = code
    return fila


def _model_texto(nombre: str) -> str:
    return " ".join(str(nombre or "").replace("\t", " ").split())


def _agrupar_errores(errores: list[str]) -> list[str]:
    conteo: dict[str, int] = {}
    for e in errores:
        conteo[e] = conteo.get(e, 0) + 1
    return [f"{e} (x{n})" if n > 1 else e for e, n in conteo.items()]


def _analizar_barra(hoja: dict) -> tuple[list[tuple], list[str]]:
    """Piezas punzonables ``(nombre, x0, largo, barrenos)`` + errores de geometría."""
    piezas: list[tuple] = []
    errores: list[str] = []
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
        barrenos = []
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
            barrenos.append(info)
        if len(barrenos) > N_GOLPES:
            errores.append(
                f"{nombre}: {len(barrenos)} barrenos; la máquina admite {N_GOLPES} por pieza"
            )
            continue
        piezas.append((nombre, x0, largo, barrenos))
    return piezas, errores


def _x_grabado(x0: float, largo: float, barrenos: list[tuple], grabado: dict) -> float:
    """X del grabado (relativo a la pieza): centro de la franja libre antes del primer barreno."""
    from modules.nesting_engine.cu_punch_tooling import x_grabado_mm

    borde = None
    if barrenos:
        borde = min(float(cx) - float(dx) / 2.0 for _t, cx, _cy, dx, _dy in barrenos) - x0
    return x_grabado_mm(largo, borde, grabado)


def montaje_barra(
    hoja: dict,
    *,
    estaciones: list | None = None,
    inventario: list | None = None,
) -> tuple[list, list[str], list[str]]:
    """``(estaciones, cambios, faltantes)`` del herramental que necesita la barra."""
    from modules.nesting_engine.cu_punch_tooling import montaje_para_barra

    piezas, _err = _analizar_barra(hoja)
    barrenos = [(b[0], b[3], b[4]) for _n, _x0, _l, bs in piezas for b in bs]
    return montaje_para_barra(barrenos, estaciones, inventario)


def barrenos_poligono(poly) -> list[tuple[str, float, float]]:
    """``(tipo, dx, dy)`` de los barrenos punzonables de un polígono shapely (ejes de la barra)."""
    out: list[tuple[str, float, float]] = []
    for geom in getattr(poly, "geoms", None) or [poly]:
        for ring in getattr(geom, "interiors", None) or []:
            info = _clasificar_barreno(list(ring.coords))
            if info is not None:
                out.append((info[0], info[3], info[4]))
    return out


def resumen_herramental_barra(
    hoja: dict,
    *,
    estaciones: list | None = None,
    inventario: list | None = None,
) -> dict:
    """Herramental de la barra para el reporte.

    ``por_pieza``: nombre → estaciones que usa (``"M1 M5"``); ``cambios``: cambios
    respecto al montaje base con las piezas que los piden; ``mixta``: la barra
    junta piezas con juegos de herramental distintos; ``detalle``: nombre →
    ``[{cantidad, barreno, estacion, herramienta}]``; ``catalogo``: nombre →
    estado contra el catálogo de barrenos de los planos.
    """
    from modules.nesting_engine.cu_catalogo_barrenos import verificar_pieza
    from modules.nesting_engine.cu_punch_tooling import codigos_molds, estacion_para_barreno

    ests, cambios, _falt = montaje_barra(hoja, estaciones=estaciones, inventario=inventario)
    codigos = codigos_molds(ests)
    piezas, _err = _analizar_barra(hoja)
    por_pieza: dict[str, str] = {}
    detalle: dict[str, list[dict]] = {}
    catalogo: dict[str, str] = {}
    usos: dict[int, dict[str, int]] = {}
    for nombre, _x0, _l, bs in piezas:
        idxs = sorted({estacion_para_barreno(b[0], b[3], b[4], ests) for b in bs} - {None})
        por_pieza[nombre] = (
            " ".join(f"M{i}={codigos[i - 1]}" if i <= len(codigos) else f"M{i}" for i in idxs) or "-"
        )
        grupos: dict[tuple, int] = {}
        for t, _cx, _cy, dx, dy in bs:
            k = (t, round(dx, 2), round(dy, 2), estacion_para_barreno(t, dx, dy, ests))
            grupos[k] = grupos.get(k, 0) + 1
        detalle[nombre] = [
            {
                "cantidad": q,
                "barreno": (
                    f"Ø{dx:.2f}" if t == "C"
                    else f"ov {max(dx, dy):.2f}x{min(dx, dy):.2f} a lo {'largo' if dx >= dy else 'ancho'}"
                ),
                "estacion": f"M{i}" if i else "-",
                "herramienta": codigos[i - 1] if i and i <= len(codigos) else "SIN HERRAMIENTA",
            }
            for (t, dx, dy, i), q in sorted(grupos.items(), key=lambda kv: (kv[0][3] or 99, kv[0][:3]))
        ]
        catalogo[nombre] = verificar_pieza(nombre, [(b[0], b[3], b[4]) for b in bs])[0]
        for i in idxs:
            cnt = usos.setdefault(i, {})
            cnt[nombre] = cnt.get(nombre, 0) + 1
    cambios_det = []
    for cambio in cambios:
        m = re.match(r"M(\d+):", cambio)
        quien = usos.get(int(m.group(1))) if m else None
        if quien:
            lista = ", ".join(f"{n} x{q}" if q > 1 else n for n, q in quien.items())
            cambio = f"{cambio} (para {lista})"
        cambios_det.append(cambio)
    juegos = {v for v in por_pieza.values() if v != "-"}
    return {
        "por_pieza": por_pieza,
        "cambios": cambios_det,
        "mixta": len(juegos) > 1,
        "detalle": detalle,
        "catalogo": catalogo,
    }


def construir_filas_barra(
    hoja: dict,
    *,
    thickness_mm: float,
    estaciones: list | None = None,
    inventario: list | None = None,
    grabado: dict | None = None,
    etiqueta: str = "",
    catalogo: dict | None = None,
) -> list[dict[str, str]]:
    """Filas CSV de una barra. Lanza ``PunchCsvError`` con todos los problemas juntos.

    Deja en ``hoja["cu_punch_cambios_herramental"]`` los cambios respecto al
    montaje base (herramientas del inventario que la barra necesita). Una pieza
    que no coincide con su plano en el catálogo bloquea el CSV; una pieza que no
    está en el catálogo pasa solo con el analizador y la simulación.
    """
    from modules.nesting_engine.cu_catalogo_barrenos import verificar_pieza
    from modules.nesting_engine.cu_punch_tooling import (
        CODIGO_GRABADO,
        cargar_grabado,
        codigos_molds,
        estacion_para_barreno,
        herramienta_inventario,
        montaje_para_barra,
        y_grabado_mm,
    )

    ancho_bar = float(hoja.get("placa_h") or 0.0)
    tag = etiqueta or str(hoja.get("sheet_code") or hoja.get("placa_id") or "barra")
    piezas, errores = _analizar_barra(hoja)
    barrenos = [(b[0], b[3], b[4]) for _n, _x0, _l, bs in piezas for b in bs]
    ests, cambios, faltantes = montaje_para_barra(barrenos, estaciones, inventario)
    errores.extend(f for f in faltantes if f.startswith("la barra"))
    molds = codigos_molds(ests)
    filas: list[dict[str, str]] = []

    grab = grabado if grabado is not None else cargar_grabado()

    for nombre, x0, largo, barrenos_p in piezas:
        estado, detalle = verificar_pieza(
            nombre, [(b[0], b[3], b[4]) for b in barrenos_p], catalogo=catalogo
        )
        if estado == "no_coincide":
            errores.append(detalle)
            continue
        golpes: list[tuple[float, float, str]] = []
        if grab.get("habilitado"):
            golpes.append(
                (_x_grabado(x0, largo, barrenos_p, grab), y_grabado_mm(ancho_bar, grab), CODIGO_GRABADO)
            )
        for tipo, cx, cy, dx, dy in barrenos_p:
            idx = estacion_para_barreno(tipo, dx, dy, ests)
            if idx is None:
                desc = f"Ø{dx:.2f}" if tipo == "C" else f"ovalado {dx:.2f}×{dy:.2f} (largo×ancho)"
                if herramienta_inventario(tipo, dx, dy, inventario) is None:
                    errores.append(f"{nombre}: barreno {desc} mm sin herramienta en el inventario")
                continue
            golpes.append((cx - x0, cy, f"M{idx}"))
        golpes.sort(key=lambda g: (g[2] != CODIGO_GRABADO, round(g[0], 3), round(g[1], 3)))
        if len(golpes) > N_GOLPES:
            errores.append(
                f"{nombre}: {len(golpes)} golpes (barrenos + grabado); la máquina admite "
                f"{N_GOLPES} por pieza"
            )
            continue
        filas.append(_fila(_model_texto(nombre), ancho_bar, thickness_mm, largo, golpes, molds))

    if len(filas) > N_FILAS:
        errores.append(f"{len(filas)} filas; LJcad admite {N_FILAS} por archivo")
    if not errores:
        errores.extend(verificar_filas_barra(hoja, filas))
    if errores:
        raise PunchCsvError(f"[{tag}] " + " | ".join(_agrupar_errores(errores)))
    hoja["cu_punch_cambios_herramental"] = cambios
    return filas


# Simulación del punzonado: área que puede diferir entre lo que deja el golpe y el
# barreno del DXF (los códigos LJcad van a 1 decimal: 15.08 → 15.1).
TOL_SIM_FRACCION = 0.06
TOL_SIM_CENTRO_MM = 0.05


def _forma_herramienta(code: str, x: float, y: float):
    """Huella del punzón ``code`` (``C11.1`` / ``E15.1X10.3``) centrada en (x, y)."""
    m = re.fullmatch(r"C(\d+(?:\.\d+)?)", code or "")
    if m:
        return Point(x, y).buffer(float(m.group(1)) / 2.0, resolution=64)
    m = re.fullmatch(r"E(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)", code or "")
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    w = min(a, b)
    d = (max(a, b) - w) / 2.0
    seg = LineString([(x - d, y), (x + d, y)]) if a >= b else LineString([(x, y - d), (x, y + d)])
    return seg.buffer(w / 2.0, resolution=64)


def verificar_filas_barra(hoja: dict, filas: list[dict[str, str]]) -> list[str]:
    """Validación independiente del CSV contra la geometría de la barra.

    Simula cada golpe con la herramienta de su ``TOOLn`` (lo que hará la máquina) y
    exige que cada barreno de la pieza quede reproducido por exactamente un golpe
    (posición, medida y orientación), sin golpes de más, con ``Length``/``Width``
    correctos y el grabado ``M100`` dentro de la pieza y fuera de los barrenos.
    No reutiliza la clasificación de barrenos que generó el CSV.
    """
    errores: list[str] = []
    ancho_bar = float(hoja.get("placa_h") or 0.0)
    piezas = [p for p in _piezas_reales(hoja)]
    if len(piezas) != len(filas):
        return [f"validación: {len(filas)} filas para {len(piezas)} piezas en la barra"]
    for p, fila in zip(piezas, filas):
        nombre = str(p.get("nombre") or "PIEZA")
        if fila.get("Name") != _model_texto(nombre):
            errores.append(f"validación: fila '{fila.get('Name')}' no corresponde a {nombre}")
            continue
        exterior = p["poligonos"][0]
        xs = [float(t[0]) for t in exterior]
        x0, largo = min(xs), max(xs) - min(xs)
        if abs(float(fila["Length"]) - largo) > 0.01:
            errores.append(f"validación {nombre}: Length {fila['Length']} ≠ pieza {largo:.2f}")
        if ancho_bar > 0 and abs(float(fila["Width"]) - ancho_bar) > 0.01:
            errores.append(f"validación {nombre}: Width {fila['Width']} ≠ solera {ancho_bar:.2f}")
        tools = [fila.get(f"TOOL{n}", "") for n in range(1, N_MOLDS + 1)]
        huecos = [Polygon(r) for r in p["poligonos"][1:]]
        usados = [0] * len(huecos)
        for n in range(1, N_GOLPES + 1):
            m = fila.get(f"M{n}", "")
            if not m:
                continue
            gx, gy = float(fila[f"X{n}"]), float(fila[f"Y{n}"])
            if m == "M100":
                pt = Point(x0 + gx, gy)
                if not (0.0 <= gx <= largo and 0.0 <= gy <= (ancho_bar or gy)):
                    errores.append(f"validación {nombre}: grabado M100 fuera de la pieza")
                elif any(h.buffer(1.0).contains(pt) for h in huecos):
                    errores.append(f"validación {nombre}: grabado M100 encima de un barreno")
                continue
            k = int(m[1:]) if m[1:].isdigit() else 0
            code = tools[k - 1] if 1 <= k <= N_MOLDS else ""
            huella = _forma_herramienta(code, x0 + gx, gy)
            if huella is None:
                errores.append(f"validación {nombre}: golpe {m} sin herramienta válida ({code or 'vacía'})")
                continue
            cerca = [
                i for i, h in enumerate(huecos)
                if Point(x0 + gx, gy).distance(h.centroid) <= TOL_SIM_CENTRO_MM
            ]
            if not cerca:
                errores.append(
                    f"validación {nombre}: golpe {m} {code} en X{gx:g} Y{gy:g} no cae en ningún barreno"
                )
                continue
            i = cerca[0]
            usados[i] += 1
            dif = huella.symmetric_difference(huecos[i]).area / max(huecos[i].area, 1e-9)
            if dif > TOL_SIM_FRACCION:
                bx0, by0, bx1, by1 = huecos[i].bounds
                errores.append(
                    f"validación {nombre}: {m} = {code} no reproduce el barreno "
                    f"{bx1 - bx0:.2f}×{by1 - by0:.2f} mm (largo×ancho) en X{gx:g} Y{gy:g}"
                )
        for i, u in enumerate(usados):
            if u != 1:
                c = huecos[i].centroid
                errores.append(
                    f"validación {nombre}: barreno en X{c.x - x0:.2f} Y{c.y:.2f} con {u} golpes (debe ser 1)"
                )
    return errores


def escribir_csv(path: str, filas: list[dict[str, str]]) -> str:
    lineas = ["\t".join(ENCABEZADO)]
    for f in filas:
        lineas.append("\t".join(f.get(c, "") for c in ENCABEZADO))
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\r\n".join(lineas) + "\r\n")
    return path
