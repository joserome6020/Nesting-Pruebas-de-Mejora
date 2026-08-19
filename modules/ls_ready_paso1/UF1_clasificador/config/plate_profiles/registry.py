# -*- coding: utf-8 -*-
"""Registro de perfiles de placa y resolucion desde meta del JSON."""
import importlib

from . import profile_240x48, profile_240x60, profile_240x96, profile_generic

# Perfil activo del paquete exclusivo 240x96
DEFAULT_ACTIVE_PROFILE = "240X96"

# Perfiles pendientes usan el perfil 240x96 dentro de este paquete exclusivo
FALLBACK_FOR_PENDING = "240X96"

_REGISTRY = {
    "240X48": profile_240x48,
    "240X60": profile_240x60,
    "240X96": profile_240x96,
    "generic": profile_generic,
}


def _normalize_key(value):
    if not value:
        return ""
    return str(value).strip().upper().replace(" ", "")


def resolve_profile_key_from_meta(meta):
    """
    Prioridad: scene.scene_size_key > sheet.scene_size_key > sheet.plate_size_in
    """
    meta = meta or {}
    sheet = meta.get("sheet") or {}
    scene = meta.get("scene") or {}

    for raw in (
        scene.get("scene_size_key"),
        sheet.get("scene_size_key"),
        sheet.get("plate_size_in"),
    ):
        key = _normalize_key(raw)
        if key in _REGISTRY:
            return key
        if key.replace("X", "x") == "240x48":
            return "240X48"
        if key.replace("X", "x") == "240x60":
            return "240X60"
        if key.replace("X", "x") == "240x96":
            return "240X96"
    return "generic"


def get_profile_module(profile_key):
    return _REGISTRY.get(_normalize_key(profile_key) or "generic", profile_generic)


def resolve_profile(meta, logs=None):
    """
    Devuelve (profile_key, profile_module, warnings).
    """
    warnings = []
    key = resolve_profile_key_from_meta(meta)
    module = get_profile_module(key)

    if getattr(module, "STATUS", "") == "pending":
        fallback = FALLBACK_FOR_PENDING
        if fallback and fallback in _REGISTRY:
            msg = (
                "Perfil {0} pendiente; aplicando provisionalmente {1}.".format(
                    key, fallback
                )
            )
            warnings.append(msg)
            if logs is not None:
                logs.append("PROFILE_WARN " + msg)
            key = fallback
            module = get_profile_module(fallback)
        else:
            msg = "Perfil {0} pendiente; usando generic.".format(key)
            warnings.append(msg)
            if logs is not None:
                logs.append("PROFILE_WARN " + msg)
            key = "generic"
            module = profile_generic

    if key == "generic":
        msg = "Placa sin perfil especifico; reglas genericas."
        warnings.append(msg)
        if logs is not None:
            logs.append("PROFILE_INFO " + msg)

    return key, module, warnings
