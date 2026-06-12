"""Rutas de exportación: servidor vs. escritorio local (Nesteos Locales)."""
from __future__ import annotations

import os
import re

import config

NESTEOS_LOCALES_DIR = "Nesteos Locales"
CONTADOR_WO_LOCAL = "contador_wo_local.txt"


def desktop_nesteos_locales() -> str:
    return os.path.join(os.path.expanduser("~"), "Desktop", NESTEOS_LOCALES_DIR)


def _normalizar(p: str) -> str:
    return os.path.normcase(os.path.normpath(str(p or "")))


def resolver_carpeta_job_desde_ruta_dxf(ruta_dxf: str) -> str | None:
    """Sube desde el DXF hasta la carpeta del job (padre de MODEL CORE FILES)."""
    try:
        actual = os.path.normpath(str(ruta_dxf))
        if os.path.isfile(actual):
            actual = os.path.dirname(actual)
        for _ in range(12):
            if not actual or actual.endswith(":"):
                break
            if os.path.basename(actual).strip().upper() == "MODEL CORE FILES":
                return os.path.dirname(actual)
            parent = os.path.dirname(actual)
            if parent == actual:
                break
            actual = parent
    except Exception:
        pass
    return None


def _partes_ruta_job(ruta_absoluta: str) -> list[str]:
    partes = [x for x in re.split(r"[\\/]+", str(ruta_absoluta or "")) if x]
    for i, seg in enumerate(partes):
        if seg.upper() in {"TANKS", "TANK"} and i + 1 < len(partes):
            return partes[i + 1 :]
    return partes


def relativa_desde_cliente(ruta_absoluta: str) -> str:
    """
    Recorta la ruta del servidor desde la carpeta cliente.
    TANKS\\VANTRAN\\JOB\\... -> VANTRAN\\JOB\\...
    """
    raiz = _normalizar(config.RUTA_SERVIDOR_RAIZ)
    p = _normalizar(ruta_absoluta)
    if p.startswith(raiz):
        rel = os.path.relpath(ruta_absoluta, config.RUTA_SERVIDOR_RAIZ)
        partes = [x for x in rel.split(os.sep) if x]
        if len(partes) >= 2 and partes[0].upper() in {"TANKS", "TANK"}:
            partes = partes[1:]
        return os.path.join(*partes) if partes else os.path.basename(ruta_absoluta)

    partes = _partes_ruta_job(ruta_absoluta)
    if len(partes) >= 2:
        return os.path.join(*partes)
    return os.path.basename(ruta_absoluta)


def resolver_ruta_base_exportacion(app, *, modo_servidor: bool) -> str:
    """
    modo_servidor=True  -> r_base actual (cerca del job en red).
    modo_servidor=False -> Desktop\\Nesteos Locales\\{cliente}\\{job}\\...
    """
    rutas = []
    for pieza in getattr(app, "datos_partes_actuales", []) or []:
        if len(pieza) > 5 and pieza[5]:
            rutas.append(str(pieza[5]))

    if not rutas:
        return desktop_nesteos_locales() if not modo_servidor else os.path.expanduser("~/Desktop")

    ruta_ref = rutas[0]
    job_dir = resolver_carpeta_job_desde_ruta_dxf(ruta_ref)

    if modo_servidor:
        try:
            return os.path.dirname(os.path.dirname(os.path.dirname(ruta_ref)))
        except Exception:
            return job_dir or os.path.dirname(ruta_ref)

    if job_dir:
        rel = relativa_desde_cliente(job_dir)
        base_local = os.path.join(desktop_nesteos_locales(), rel)
        return os.path.join(base_local, "MODEL CORE FILES")

    rel = relativa_desde_cliente(ruta_ref)
    return os.path.join(desktop_nesteos_locales(), os.path.dirname(rel), "MODEL CORE FILES")


def obtener_consecutivo_wo_local() -> int:
    base = desktop_nesteos_locales()
    os.makedirs(base, exist_ok=True)
    ruta = os.path.join(base, CONTADOR_WO_LOCAL)
    try:
        if os.path.exists(ruta):
            with open(ruta, "r", encoding="utf-8") as f:
                return int(str(f.read()).strip() or "0") + 1
    except Exception:
        pass
    return 1


def guardar_consecutivo_wo_local(consecutivo_usado: int) -> None:
    base = desktop_nesteos_locales()
    os.makedirs(base, exist_ok=True)
    ruta = os.path.join(base, CONTADOR_WO_LOCAL)
    try:
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(str(int(consecutivo_usado)))
    except Exception:
        pass
