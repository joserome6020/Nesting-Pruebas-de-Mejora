# -*- coding: utf-8 -*-
"""Carga configuracion de runtime segun perfil de placa."""
import types

import classification_config as base_cfg
from config.plate_profiles.registry import resolve_profile, resolve_profile_key_from_meta


def _namespace_from_module(module):
    ns = types.SimpleNamespace()
    for name in dir(module):
        if name.isupper():
            setattr(ns, name, getattr(module, name))
    return ns


def apply_overrides(ns, overrides):
    if not overrides:
        return
    for key, value in overrides.items():
        setattr(ns, key, value)


def load_runtime_config(meta, logs=None):
    """
    Combina classification_config + overrides del perfil de placa.
    """
    requested_key = resolve_profile_key_from_meta(meta)
    profile_key, profile_module, warnings = resolve_profile(meta, logs=logs)

    ns = _namespace_from_module(base_cfg)
    apply_overrides(ns, getattr(profile_module, "OVERRIDES", None))

    ns.PLATE_PROFILE_KEY = profile_key
    ns.PLATE_PROFILE_REQUESTED_KEY = requested_key
    ns.PLATE_PROFILE_STATUS = getattr(profile_module, "STATUS", "unknown")
    ns.PLATE_PROFILE_DESCRIPTION = getattr(profile_module, "DESCRIPTION", "")
    ns.PLATE_PROFILE_FLOW = getattr(profile_module, "FLOW", None)

    sheet = (meta or {}).get("sheet") or {}
    scene = (meta or {}).get("scene") or {}
    return ns, {
        "requested_key": requested_key,
        "key": profile_key,
        "ruleset_module": getattr(profile_module, "__name__", ""),
        "status": ns.PLATE_PROFILE_STATUS,
        "description": ns.PLATE_PROFILE_DESCRIPTION,
        "plate_size_in": sheet.get("plate_size_in"),
        "scene_size_key": sheet.get("scene_size_key") or scene.get("scene_size_key"),
        "flow": ns.PLATE_PROFILE_FLOW,
        "warnings": warnings,
    }
