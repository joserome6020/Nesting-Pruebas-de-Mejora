"""Herramental de la punzonadora de solera (CNC Busbar Punching, Lijian MX602K).

Montaje base de las 8 estaciones (``Mold1..Mold8`` del CSV) + inventario de
herramientas físicas, persistidos en ``_config/cu_punch_tooling.json`` y
editables desde Configuración Global.

Cada estación es un barreno redondo (``C{d}``) o un ovalado (``E{x}X{y}``):
``x`` es la medida a lo largo de la solera e ``y`` a lo ancho, así que
``E11.1X15.9`` y ``E15.9X11.1`` son montajes distintos (orientación). El
inventario no lleva orientación: un ovalado 11.11×17.47 puede montarse a lo
largo o a lo ancho.

Si una barra necesita una herramienta del inventario que no está en el montaje
base, ``montaje_para_barra`` la coloca en una estación que esa barra no usa
(el CSV lleva su propio ``Mold1..Mold8``) y reporta el cambio.

``M100`` no es estación de punzón: es el grabado del nombre (``Model``).
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

N_ESTACIONES = 8
_CONFIG_RELATIVE_PATH = os.path.join("_config", "cu_punch_tooling.json")
# Holgura DXF ↔ herramienta (mm) al emparejar un barreno con su estación.
TOL_HERRAMIENTA_MM = 0.15

# Montaje de planta (pizarrón 2026-10-01). La orientación de cada ovalado es la que
# usan los planos GIGA (pendiente de confirmar en máquina).
_DEFAULT_ESTACIONES: list[dict[str, Any]] = [
    {"tipo": "C", "x": 11.11},
    {"tipo": "C", "x": 10.31},
    {"tipo": "E", "x": 10.31, "y": 15.08},
    {"tipo": "E", "x": 14.30, "y": 11.11},
    {"tipo": "E", "x": 11.11, "y": 15.88},
    {"tipo": "E", "x": 17.47, "y": 11.11},
    {"tipo": "E", "x": 11.11, "y": 20.33},
    {"tipo": "E", "x": 20.65, "y": 11.11},
]

# Estación de grabado: marca el texto de ``Model`` (vertical, al inicio de la pieza).
CODIGO_GRABADO = "M100"
_DEFAULT_GRABADO: dict[str, Any] = {"habilitado": True, "x_sin_barrenos_mm": 12.7}

# Herramientas físicas disponibles (herramental_cobre_barrenos.csv); ovalado = ancho × largo.
_DEFAULT_INVENTARIO: list[dict[str, Any]] = [
    {"tipo": "C", "x": 11.11},
    {"tipo": "C", "x": 10.31},
    {"tipo": "C", "x": 11.0},
    {"tipo": "E", "x": 11.11, "y": 20.65},
    {"tipo": "E", "x": 11.11, "y": 17.47},
    {"tipo": "E", "x": 11.11, "y": 15.88},
    {"tipo": "E", "x": 11.11, "y": 14.30},
    {"tipo": "E", "x": 11.11, "y": 20.33},
    {"tipo": "E", "x": 10.31, "y": 15.08},
    {"tipo": "E", "x": 11.11, "y": 12.26},
]


def _fmt_dim(v: float) -> str:
    return f"{float(v):.1f}"


def codigo_estacion(est: dict[str, Any] | None) -> str:
    """Código LJcad de la estación (``C11.1``, ``E11.1X15.9``); vacía → ``0``."""
    if not est:
        return "0"
    tipo = str(est.get("tipo") or "").strip().upper()
    try:
        x = float(est.get("x") or 0.0)
    except (TypeError, ValueError):
        x = 0.0
    if x <= 0.0:
        return "0"
    if tipo == "E":
        try:
            y = float(est.get("y") or 0.0)
        except (TypeError, ValueError):
            y = 0.0
        if y <= 0.0:
            return "0"
        return f"E{_fmt_dim(x)}X{_fmt_dim(y)}"
    return f"C{_fmt_dim(x)}"


def _normalizar_herramienta(est: Any) -> dict[str, Any] | None:
    if not isinstance(est, dict):
        return None
    tipo = str(est.get("tipo") or "").strip().upper()
    try:
        x = float(est.get("x") or 0.0)
    except (TypeError, ValueError):
        x = 0.0
    if tipo not in ("C", "E") or x <= 0.0:
        return None
    if tipo == "E":
        try:
            y = float(est.get("y") or 0.0)
        except (TypeError, ValueError):
            y = 0.0
        if y <= 0.0:
            return None
        return {"tipo": "E", "x": round(x, 3), "y": round(y, 3)}
    return {"tipo": "C", "x": round(x, 3)}


def normalizar_estaciones(raw: Any) -> list[dict[str, Any] | None]:
    """Siempre 8 posiciones; las inválidas quedan en ``None`` (estación vacía)."""
    items = raw if isinstance(raw, list) else []
    return [
        _normalizar_herramienta(items[i] if i < len(items) else None)
        for i in range(N_ESTACIONES)
    ]


def normalizar_inventario(raw: Any) -> list[dict[str, Any]]:
    """Lista sin huecos ni duplicados; ovalados guardados como (ancho menor, largo mayor)."""
    out: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        h = _normalizar_herramienta(item)
        if not h:
            continue
        if h["tipo"] == "E":
            a, b = sorted((h["x"], h["y"]))
            h = {"tipo": "E", "x": a, "y": b}
        code = codigo_estacion(h)
        if code in vistos:
            continue
        vistos.add(code)
        out.append(h)
    return out


def estaciones_default() -> list[dict[str, Any] | None]:
    return normalizar_estaciones(copy.deepcopy(_DEFAULT_ESTACIONES))


def inventario_default() -> list[dict[str, Any]]:
    return normalizar_inventario(copy.deepcopy(_DEFAULT_INVENTARIO))


def config_path() -> Path:
    try:
        import config as app_config

        return Path(app_config.asegurar_archivo_persistente(_CONFIG_RELATIVE_PATH))
    except Exception:
        return Path(__file__).resolve().parents[2] / _CONFIG_RELATIVE_PATH


def _leer_config() -> dict[str, Any]:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cargar_estaciones() -> list[dict[str, Any] | None]:
    data = _leer_config()
    if "estaciones" not in data:
        return estaciones_default()
    return normalizar_estaciones(data.get("estaciones"))


def cargar_inventario() -> list[dict[str, Any]]:
    data = _leer_config()
    if "inventario" not in data:
        return inventario_default()
    return normalizar_inventario(data.get("inventario"))


def normalizar_grabado(raw: Any) -> dict[str, Any]:
    out = dict(_DEFAULT_GRABADO)
    if isinstance(raw, dict):
        if "habilitado" in raw:
            out["habilitado"] = bool(raw.get("habilitado"))
        try:
            x = float(raw.get("x_sin_barrenos_mm", out["x_sin_barrenos_mm"]))
            if x > 0:
                out["x_sin_barrenos_mm"] = round(x, 3)
        except (TypeError, ValueError):
            pass
    return out


def cargar_grabado() -> dict[str, Any]:
    return normalizar_grabado(_leer_config().get("grabado"))


def guardar_herramental(
    estaciones: list[dict[str, Any] | None] | None = None,
    inventario: list[dict[str, Any]] | None = None,
    grabado: dict[str, Any] | None = None,
) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "estaciones": normalizar_estaciones(
            estaciones if estaciones is not None else cargar_estaciones()
        ),
        "inventario": normalizar_inventario(
            inventario if inventario is not None else cargar_inventario()
        ),
        "grabado": normalizar_grabado(grabado if grabado is not None else cargar_grabado()),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def guardar_estaciones(estaciones: list[dict[str, Any] | None]) -> Path:
    return guardar_herramental(estaciones=estaciones)


def codigos_molds(estaciones: list[dict[str, Any] | None] | None = None) -> list[str]:
    ests = estaciones if estaciones is not None else cargar_estaciones()
    return [codigo_estacion(e) for e in normalizar_estaciones(ests)]


def estacion_para_barreno(
    tipo: str,
    dx_mm: float,
    dy_mm: float,
    estaciones: list[dict[str, Any] | None] | None = None,
    *,
    tol_mm: float = TOL_HERRAMIENTA_MM,
) -> int | None:
    """Índice 1-based de la estación que hace el barreno (``None`` si no hay).

    ``tipo`` = ``"C"`` (redondo, ``dx_mm`` = diámetro) o ``"E"`` (ovalado,
    ``dx_mm`` a lo largo e ``dy_mm`` a lo ancho de la solera).
    """
    ests = normalizar_estaciones(estaciones if estaciones is not None else cargar_estaciones())
    tipo = str(tipo or "").strip().upper()
    mejor: tuple[float, int] | None = None
    for i, est in enumerate(ests, start=1):
        if not est or est["tipo"] != tipo:
            continue
        if tipo == "C":
            err = abs(float(est["x"]) - float(dx_mm))
        else:
            err = max(
                abs(float(est["x"]) - float(dx_mm)),
                abs(float(est["y"]) - float(dy_mm)),
            )
        if err <= tol_mm and (mejor is None or err < mejor[0]):
            mejor = (err, i)
    return mejor[1] if mejor else None


def herramienta_inventario(
    tipo: str,
    dx_mm: float,
    dy_mm: float,
    inventario: list[dict[str, Any]] | None = None,
    *,
    tol_mm: float = TOL_HERRAMIENTA_MM,
) -> dict[str, Any] | None:
    """Herramienta del inventario montada en la orientación del barreno (``None`` si no hay)."""
    inv = normalizar_inventario(inventario if inventario is not None else cargar_inventario())
    tipo = str(tipo or "").strip().upper()
    mejor: tuple[float, dict[str, Any]] | None = None
    for h in inv:
        if h["tipo"] != tipo:
            continue
        if tipo == "C":
            err = abs(float(h["x"]) - float(dx_mm))
            cand = {"tipo": "C", "x": h["x"]}
        else:
            a, b = h["x"], h["y"]
            if dx_mm >= dy_mm:
                cand = {"tipo": "E", "x": b, "y": a}
            else:
                cand = {"tipo": "E", "x": a, "y": b}
            err = max(abs(cand["x"] - float(dx_mm)), abs(cand["y"] - float(dy_mm)))
        if err <= tol_mm and (mejor is None or err < mejor[0]):
            mejor = (err, cand)
    return mejor[1] if mejor else None


def _error_herramienta(h: dict[str, Any], dx: float, dy: float) -> float:
    if h["tipo"] == "C":
        return abs(float(h["x"]) - float(dx))
    return max(abs(float(h["x"]) - float(dx)), abs(float(h["y"]) - float(dy)))


def montaje_para_barra(
    barrenos: list[tuple[str, float, float]],
    estaciones: list[dict[str, Any] | None] | None = None,
    inventario: list[dict[str, Any]] | None = None,
    *,
    tol_mm: float = TOL_HERRAMIENTA_MM,
) -> tuple[list[dict[str, Any] | None], list[str], list[str]]:
    """Montaje para una barra: ``(estaciones, cambios, faltantes)``.

    Conserva el montaje base; cada barreno sin estación toma la herramienta del
    inventario (en su orientación) y ocupa, de Mold8 hacia Mold1, una estación
    que la barra no usa. ``faltantes`` = descripciones de barrenos que no tienen
    herramienta en el inventario o que no caben en las 8 estaciones.
    """
    ests = list(normalizar_estaciones(estaciones if estaciones is not None else cargar_estaciones()))
    inv = inventario if inventario is not None else cargar_inventario()
    usadas: set[int] = set()
    pendientes: list[tuple[dict[str, Any], str, int | None]] = []
    faltantes: list[str] = []
    vistos: set[str] = set()
    for tipo, dx, dy in barrenos:
        idx = estacion_para_barreno(tipo, dx, dy, ests, tol_mm=tol_mm)
        h = herramienta_inventario(tipo, dx, dy, inv, tol_mm=tol_mm)
        if idx is not None:
            est = ests[idx - 1]
            if (
                h is None
                or codigo_estacion(h) == codigo_estacion(est)
                or _error_herramienta(h, dx, dy) >= _error_herramienta(est, dx, dy) - 0.02
            ):
                usadas.add(idx)
                continue
        if h is None:
            desc = f"Ø{dx:.2f}" if tipo == "C" else f"ovalado {dx:.2f}×{dy:.2f} (largo×ancho)"
            if desc not in vistos:
                vistos.add(desc)
                faltantes.append(f"barreno {desc} mm no existe en el inventario de herramientas")
            continue
        code = codigo_estacion(h)
        if code not in {p[1] for p in pendientes}:
            pendientes.append((h, code, idx))
    # Primero las que no tienen alternativa montada; la estación de respaldo de las
    # demás queda reservada por si no alcanza lugar para la herramienta exacta.
    pendientes.sort(key=lambda p: p[2] is not None)
    reservadas = {p[2] for p in pendientes if p[2] is not None}
    cambios: list[str] = []
    libres = [i for i in range(N_ESTACIONES, 0, -1) if i not in usadas]
    for h, code, respaldo in pendientes:
        disponibles = [i for i in libres if i not in reservadas or respaldo is not None]
        if not disponibles:
            if respaldo is not None:
                cambios.append(
                    f"{code} sin estación libre: se punzona con Mold{respaldo} "
                    f"({codigo_estacion(ests[respaldo - 1])})"
                )
                continue
            faltantes.append(
                f"la barra necesita más de {N_ESTACIONES} herramientas distintas ({code} no cabe)"
            )
            continue
        i = disponibles[0]
        if respaldo is not None and i == respaldo:
            disponibles = [j for j in disponibles if j != respaldo]
            if not disponibles:
                cambios.append(
                    f"{code} sin estación libre: se punzona con Mold{respaldo} "
                    f"({codigo_estacion(ests[respaldo - 1])})"
                )
                continue
            i = disponibles[0]
        libres.remove(i)
        cambios.append(f"Mold{i}: {codigo_estacion(ests[i - 1])} → {code}")
        ests[i - 1] = h
    return ests, cambios, faltantes
