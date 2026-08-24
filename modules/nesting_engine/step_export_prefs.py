"""Preferencias de carpetas STEP en exportación (persistentes)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Clave interna → etiqueta de carpeta (misma que exporter / árbol NESTING).
STEP_FOLDER_SPECS: tuple[tuple[str, str], ...] = (
    ("cama_laser_12kw", "CAMA LASER 12 KW SIN MINI NEST"),
    ("cama_laser", "CAMA LASER SIN MINI NEST"),
    ("nesteos_cobre", "NESTEOS DE COBRE"),
    ("robot_laser", "ROBOT LASER + MINI NEST"),
    ("robot_plasma", "ROBOT PLASMA"),
)

_DEFAULTS: dict[str, bool] = {k: True for k, _ in STEP_FOLDER_SPECS}

_LABEL_TO_KEY = {label.upper(): key for key, label in STEP_FOLDER_SPECS}
# Aliases cortos usados en logs / estimar
_LABEL_TO_KEY.update(
    {
        "CAMA LASER": "cama_laser",
        "CAMA LASER 12KW": "cama_laser_12kw",
        "ROBOT LASER": "robot_laser",
        "ROBOT PLASMA": "robot_plasma",
        "NESTEOS DE COBRE": "nesteos_cobre",
        "NESTEOS DE COBRE STEP": "nesteos_cobre",
        "ROBOT LASER A": "robot_laser",
        "ROBOT LASER B": "robot_laser",
        "ROBOT PLASMA A": "robot_plasma",
        "ROBOT PLASMA B": "robot_plasma",
    }
)


def prefs_path() -> Path:
    """Preferencias persistentes junto al .exe; plantilla desde el bundle si hace falta."""
    try:
        import config as app_config

        return Path(app_config.asegurar_archivo_persistente(os.path.join("_config", "step_export_folders.json")))
    except Exception:
        root = Path(__file__).resolve().parents[2]
        return root / "_config" / "step_export_folders.json"


def default_step_export_prefs() -> dict[str, bool]:
    return dict(_DEFAULTS)


def load_step_export_prefs() -> dict[str, bool]:
    out = default_step_export_prefs()
    path = prefs_path()
    if not path.is_file():
        return out
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return out
    if not isinstance(raw, dict):
        return out
    for key in _DEFAULTS:
        if key in raw:
            out[key] = bool(raw[key])
    return out


def save_step_export_prefs(prefs: dict[str, Any] | None) -> Path:
    path = prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = default_step_export_prefs()
    if isinstance(prefs, dict):
        for key in _DEFAULTS:
            if key in prefs:
                merged[key] = bool(prefs[key])
    path.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def step_folder_enabled(key: str, prefs: dict[str, bool] | None = None) -> bool:
    p = prefs if prefs is not None else load_step_export_prefs()
    k = str(key or "").strip().lower()
    if k not in _DEFAULTS:
        return True
    return bool(p.get(k, True))


def step_enabled_for_label(label: str, prefs: dict[str, bool] | None = None) -> bool:
    """label = carpeta NESTING o etiqueta de conversión STEP."""
    s = str(label or "").strip()
    if not s:
        return True
    key = _LABEL_TO_KEY.get(s.upper())
    if key is None:
        # Prefijo flexible (p.ej. "CAMA LASER SIN …")
        su = s.upper()
        for lab, k in (
            ("CAMA LASER 12 KW", "cama_laser_12kw"),
            ("CAMA LASER", "cama_laser"),
            ("NESTEOS DE COBRE", "nesteos_cobre"),
            ("ROBOT LASER", "robot_laser"),
            ("ROBOT PLASMA", "robot_plasma"),
        ):
            if su.startswith(lab):
                key = k
                break
    if key is None:
        return True
    return step_folder_enabled(key, prefs)


def step_enabled_for_carpeta(carpeta: str, prefs: dict[str, bool] | None = None) -> bool:
    return step_enabled_for_label(carpeta, prefs)


def env_override_all_off() -> bool:
    """ARGA_STEP_FOLDERS=0 desactiva todos (debug)."""
    v = (os.environ.get("ARGA_STEP_FOLDERS") or "").strip().lower()
    return v in ("0", "none", "off", "false")


_LEGACY_FREECAD_MOTOR_ALIASES = frozenset({"freecad", "fc", "verde", "free-cad"})


def motor_3d_crear_steps() -> str:
    """Motor DXF→STEP (Crear STEPs / despachador). Default OCCT."""
    v = (os.environ.get("ARGA_CREAR_STEPS_MOTOR") or "occt").strip().lower()
    if v in ("arga", "nans", "arga_nesting"):
        return "occt"
    if v in _LEGACY_FREECAD_MOTOR_ALIASES:
        return "freecad"
    return "occt"


def motor_3d_export() -> str:
    """Motor DXF→STEP al exportar DXF+3D. Default OCCT."""
    v = (os.environ.get("ARGA_EXPORT_3D_MOTOR") or "occt").strip().lower()
    if v in ("arga", "nans", "arga_nesting"):
        return "occt"
    if v in _LEGACY_FREECAD_MOTOR_ALIASES:
        return "freecad"
    return "occt"
