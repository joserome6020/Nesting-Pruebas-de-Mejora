"""Registra rutas de DLL para extensiones nativas en procesos Windows."""
from __future__ import annotations

import os


def registrar_dll_paquete(nombre_paquete: str) -> None:
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    try:
        import importlib.util

        spec = importlib.util.find_spec(nombre_paquete)
        if not spec or not spec.origin:
            return
        base = os.path.dirname(os.path.abspath(spec.origin))
        if base:
            os.add_dll_directory(base)
    except Exception:
        pass


def bootstrap_proceso_nesting() -> None:
    """Llamar al inicio de procesos hijo que puedan cargar extensiones .pyd."""
    if os.name != "nt":
        return
    for pkg in ("psycopg2",):
        registrar_dll_paquete(pkg)
