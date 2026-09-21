"""Helpers Windows MAX_PATH (>260) para UNC / OneDrive / rutas profundas."""
from __future__ import annotations

import os


def win_long_path(path: str) -> str:
    """Prefijo \\\\?\\ / \\\\?\\UNC\\ para APIs Win32 sin límite MAX_PATH."""
    texto = str(path or "").strip()
    if not texto or os.name != "nt":
        return texto
    if texto.startswith("\\\\?\\"):
        return texto
    if texto.startswith("\\\\"):
        return "\\\\?\\UNC\\" + texto[2:]
    return "\\\\?\\" + os.path.abspath(texto)


def needs_win_long_path(path: str, *, threshold: int = 240) -> bool:
    """True si la ruta es UNC o supera el umbral práctico de MAX_PATH."""
    texto = str(path or "").strip()
    if not texto or os.name != "nt":
        return False
    if texto.startswith("\\\\?\\"):
        return False
    if texto.startswith("\\\\"):
        return True
    return len(texto) >= int(threshold)


def asegurar_ruta_escritura(path: str, *, threshold: int = 240) -> str:
    """Crea el directorio padre y, si hace falta, devuelve la ruta con \\\\?\\."""
    texto = str(path or "").strip()
    if not texto:
        raise ValueError("ruta vacía")
    parent = os.path.dirname(texto)
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            os.makedirs(win_long_path(parent), exist_ok=True)
    if needs_win_long_path(texto, threshold=threshold):
        return win_long_path(texto)
    return texto
