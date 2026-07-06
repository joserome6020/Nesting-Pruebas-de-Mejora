"""Persistencia de jobs ya nesteados (nombres, no rutas completas)."""
from __future__ import annotations

import json
import os
from typing import Iterable

import config


def norm_job_name(nombre: str) -> str:
    return str(nombre or "").strip().upper()


def extraer_nombre_job(valor: str) -> str:
    """Acepta nombre de job o ruta completa (formato legado)."""
    v = str(valor or "").strip()
    if not v:
        return ""
    if os.sep in v or (os.altsep and os.altsep in v):
        return os.path.basename(v.rstrip("\\/"))
    if len(v) >= 3 and v[1] == ":" and v[2] in ("\\", "/"):
        return os.path.basename(v.rstrip("\\/"))
    return v


def _deduplicar_nombres(valores: Iterable[str]) -> list[str]:
    nombres: list[str] = []
    visto: set[str] = set()
    for item in valores:
        nombre = extraer_nombre_job(str(item))
        clave = norm_job_name(nombre)
        if not nombre or clave in visto:
            continue
        visto.add(clave)
        nombres.append(nombre)
    return nombres


def cargar_nombres_jobs(path: str | None = None) -> list[str]:
    """Lee historial_jobs.json: lista JSON o legado (rutas / CSV en una línea)."""
    ruta = path or config.DB_HISTORIAL
    if not os.path.isfile(ruta):
        return []
    try:
        with open(ruta, encoding="utf-8") as f:
            raw = f.read().strip()
    except Exception:
        return []
    if not raw:
        return []

    data = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = [p.strip() for p in raw.split(",") if p.strip()]

    if isinstance(data, str):
        data = [data]
    if not isinstance(data, list):
        return []
    return _deduplicar_nombres(data)


def guardar_nombres_jobs(nombres: list[str], path: str | None = None) -> None:
    ruta = path or config.DB_HISTORIAL
    lista = _deduplicar_nombres(nombres)
    carpeta = os.path.dirname(ruta)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    tmp = f"{ruta}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lista, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, ruta)
    except Exception as exc:
        print(f"[HISTORIAL] WARN: no se pudo guardar {ruta}: {exc}")
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass


def registrar_job_nesteado(nombres_actuales: list[str], job_ref: str) -> list[str]:
    """Agrega un job al historial si aún no está; retorna la lista actualizada."""
    nombre = extraer_nombre_job(job_ref)
    if not nombre:
        return cargar_nombres_jobs()
    # Disco manda: respeta borrados manuales en historial_jobs.json.
    historial = _deduplicar_nombres(cargar_nombres_jobs())
    if norm_job_name(nombre) in {norm_job_name(n) for n in historial}:
        return historial
    historial.append(nombre)
    guardar_nombres_jobs(historial)
    return historial


def eliminar_jobs_del_historial(
    jobs_a_quitar: Iterable[str],
    path: str | None = None,
) -> list[str]:
    """Quita jobs del historial para que vuelvan a aparecer en IMPORTAR."""
    quitar = {
        norm_job_name(extraer_nombre_job(str(j)))
        for j in jobs_a_quitar
        if str(j or "").strip()
    }
    if not quitar:
        return cargar_nombres_jobs(path)
    historial = [
        n
        for n in cargar_nombres_jobs(path)
        if norm_job_name(n) not in quitar
    ]
    guardar_nombres_jobs(historial, path)
    return historial
