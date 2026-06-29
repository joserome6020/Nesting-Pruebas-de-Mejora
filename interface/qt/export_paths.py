"""Rutas de exportación: servidor vs. escritorio local (Nesteos Locales)."""
from __future__ import annotations

import os
import re

import config

NESTEOS_LOCALES_DIR = "Nesteos Locales"
CONTADOR_WO_LOCAL = "contador_wo_local.txt"
_ONEDRIVE_CORP_NAMES = ("OneDrive - grupoarga.com",)


def _normalizar(p: str) -> str:
    return os.path.normcase(os.path.normpath(str(p or "")))


def _candidatos_onedrive_grupoarga() -> list[str]:
    """Raíces OneDrive corporativo (env + carpeta típica en el perfil)."""
    roots: list[str] = []
    seen: set[str] = set()

    def _add(root: str) -> None:
        key = _normalizar(root)
        if key and key not in seen and os.path.isdir(root):
            seen.add(key)
            roots.append(root)

    for env in ("ONEDRIVECOMMERCIAL", "OneDriveCommercial"):
        _add(str(os.environ.get(env) or "").strip())

    home = os.path.expanduser("~")
    for name in _ONEDRIVE_CORP_NAMES:
        _add(os.path.join(home, name))

    try:
        for name in os.listdir(home):
            low = name.lower()
            if low.startswith("onedrive") and "grupoarga" in low:
                _add(os.path.join(home, name))
    except OSError:
        pass

    return roots


def _candidatos_nesteos_locales() -> list[str]:
    """
    Orden de preferencia:
    1) OneDrive corporativo\\Escritorio|Desktop\\Nesteos Locales
    2) Desktop\\Nesteos Locales (mapeo local actual)
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        key = _normalizar(path)
        if key not in seen:
            seen.add(key)
            out.append(path)

    for root in _candidatos_onedrive_grupoarga():
        for desk in ("Escritorio", "Desktop"):
            desk_dir = os.path.join(root, desk)
            if os.path.isdir(desk_dir):
                _add(os.path.join(desk_dir, NESTEOS_LOCALES_DIR))

    _add(os.path.join(os.path.expanduser("~"), "Desktop", NESTEOS_LOCALES_DIR))
    return out


def _puede_usar_carpeta_local(base: str) -> bool:
    """True si la carpeta existe o se puede crear (OneDrive sincronizado / Desktop)."""
    if not base:
        return False
    parent = os.path.dirname(base)
    if not os.path.isdir(parent):
        return False
    try:
        os.makedirs(base, exist_ok=True)
        probe = os.path.join(base, ".arga_write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(probe)
        return True
    except OSError:
        return os.path.isdir(base)


def desktop_nesteos_locales() -> str:
    for cand in _candidatos_nesteos_locales():
        if _puede_usar_carpeta_local(cand):
            return cand
    return os.path.join(os.path.expanduser("~"), "Desktop", NESTEOS_LOCALES_DIR)


def es_ruta_servidor(ruta: str) -> bool:
    """True si la ruta apunta al servidor de red (UNC / raíz corporativa)."""
    texto = str(ruta or "")
    if not texto.strip():
        return False
    p = _normalizar(texto)
    raiz = _normalizar(config.RUTA_SERVIDOR_RAIZ)
    if raiz and p.startswith(raiz):
        return True
    if "192.168.2.80" in texto.replace("/", "\\"):
        return True
    if texto.startswith("\\\\"):
        return True
    return False


def asegurar_exportacion_local(ruta: str, *, etiqueta: str = "ruta") -> str:
    """
    Falla si en modo local la ruta apunta al servidor o queda fuera de Nesteos Locales.
    """
    if es_ruta_servidor(ruta):
        raise RuntimeError(
            f"Modo local bloqueado: {etiqueta} apunta al servidor ({ruta}). "
            f"Los DXF deben guardarse en {desktop_nesteos_locales()}."
        )
    base = _normalizar(desktop_nesteos_locales())
    p = _normalizar(ruta)
    if not p.startswith(base):
        raise RuntimeError(
            f"Modo local bloqueado: {etiqueta} quedó fuera de Nesteos Locales ({ruta})."
        )
    return ruta


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
    modo_servidor=False -> OneDrive\\Escritorio\\Nesteos Locales (o Desktop si no hay OneDrive)\\{cliente}\\{job}\\...
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
        ruta_local = os.path.join(base_local, "MODEL CORE FILES")
        return asegurar_exportacion_local(ruta_local, etiqueta="r_base")

    rel = relativa_desde_cliente(ruta_ref)
    ruta_local = os.path.join(desktop_nesteos_locales(), os.path.dirname(rel), "MODEL CORE FILES")
    return asegurar_exportacion_local(ruta_local, etiqueta="r_base")


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
