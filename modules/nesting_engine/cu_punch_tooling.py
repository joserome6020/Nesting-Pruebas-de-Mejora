"""Herramental de la punzonadora de solera (CNC Busbar Punching, Lijian MX602K).

Montaje físico de las 8 estaciones (``Mold1..Mold8`` del CSV), persistido en
``_config/cu_punch_tooling.json`` y editable desde Configuración Global.

Cada estación es un barreno redondo (``C{d}``) o un ovalado (``E{x}X{y}``):
``x`` es la medida a lo largo de la solera e ``y`` a lo ancho, así que
``E11.1X15.9`` y ``E15.9X11.1`` son herramientas distintas (orientación).
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

# Montaje inicial = prueba H28 (pendiente del orden real que manden de planta).
_DEFAULT_ESTACIONES: list[dict[str, Any]] = [
    {"tipo": "C", "x": 11.11},
    {"tipo": "C", "x": 10.31},
    {"tipo": "C", "x": 11.0},
    {"tipo": "E", "x": 11.11, "y": 20.65},
    {"tipo": "E", "x": 11.11, "y": 17.47},
    {"tipo": "E", "x": 11.11, "y": 15.88},
    {"tipo": "E", "x": 11.11, "y": 14.30},
    {"tipo": "E", "x": 10.31, "y": 15.08},
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


def normalizar_estaciones(raw: Any) -> list[dict[str, Any] | None]:
    """Siempre 8 posiciones; las inválidas quedan en ``None`` (estación vacía)."""
    out: list[dict[str, Any] | None] = []
    items = raw if isinstance(raw, list) else []
    for i in range(N_ESTACIONES):
        est = items[i] if i < len(items) else None
        if not isinstance(est, dict):
            out.append(None)
            continue
        tipo = str(est.get("tipo") or "").strip().upper()
        try:
            x = float(est.get("x") or 0.0)
        except (TypeError, ValueError):
            x = 0.0
        if tipo not in ("C", "E") or x <= 0.0:
            out.append(None)
            continue
        if tipo == "E":
            try:
                y = float(est.get("y") or 0.0)
            except (TypeError, ValueError):
                y = 0.0
            if y <= 0.0:
                out.append(None)
                continue
            out.append({"tipo": "E", "x": round(x, 3), "y": round(y, 3)})
        else:
            out.append({"tipo": "C", "x": round(x, 3)})
    return out


def estaciones_default() -> list[dict[str, Any] | None]:
    return normalizar_estaciones(copy.deepcopy(_DEFAULT_ESTACIONES))


def config_path() -> Path:
    try:
        import config as app_config

        return Path(app_config.asegurar_archivo_persistente(_CONFIG_RELATIVE_PATH))
    except Exception:
        return Path(__file__).resolve().parents[2] / _CONFIG_RELATIVE_PATH


def cargar_estaciones() -> list[dict[str, Any] | None]:
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return estaciones_default()
    return normalizar_estaciones((data or {}).get("estaciones"))


def guardar_estaciones(estaciones: list[dict[str, Any] | None]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"estaciones": normalizar_estaciones(estaciones)}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


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
