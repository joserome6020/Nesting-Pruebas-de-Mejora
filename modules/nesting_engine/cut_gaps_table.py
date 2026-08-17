"""Tabla oficial de gaps de corte para nesting de placas.

La regla vive fuera de Qt para que el mismo contrato aplique a preflight,
nesting, renesteos y ejecuciones sin interfaz.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import Any


class CutGapTableError(ValueError):
    """No fue posible resolver o validar la tabla oficial de gaps."""


PLATE_TO_PIECE_DEFAULT_IN = 0.250
_EDIT_PASSWORD_SHA256 = "be3e6cd9bc366459b5b9316047c55585ec1c43da97096270acd85ea0ca271b2c"
_CONFIG_RELATIVE_PATH = os.path.join("_config", "cut_gaps_table.json")

# key identifica una regla persistente; label es la presentación aprobada en UI.
# Tabla oficial (foto de planta). PLACA A PIEZA = 0.250" fija.
# Cal 18 no aparece en la foto; se mantiene para Herinox con el mismo
# gap entre piezas del grupo delgado (Cal 16..0.188).
CUT_GAP_RULES: tuple[dict[str, Any], ...] = (
    {"key": "cal_18", "label": "Cal 18", "kerf_in": 0.150, "gauges": ("18",)},
    {"key": "cal_16", "label": "Cal 16", "kerf_in": 0.150, "gauges": ("16",)},
    {"key": "cal_14", "label": "Cal 14", "kerf_in": 0.150, "gauges": ("14",)},
    {"key": "cal_12", "label": "Cal 12", "kerf_in": 0.150, "gauges": ("12",)},
    {"key": "cal_11", "label": "Cal 11", "kerf_in": 0.150, "gauges": ("11",)},
    {"key": "cal_10", "label": "Cal 10", "kerf_in": 0.150, "gauges": ("10",)},
    {"key": "thk_0188", "label": 'Cal 0.188"', "kerf_in": 0.150, "thicknesses": (0.188,)},
    {"key": "thk_0250", "label": 'Cal 0.250"', "kerf_in": 0.200, "thicknesses": (0.250,)},
    {"key": "thk_03125", "label": 'Cal 0.3125"', "kerf_in": 0.200, "thicknesses": (0.3125,)},
    {"key": "thk_0375", "label": 'Cal 0.375"', "kerf_in": 0.200, "thicknesses": (0.375,)},
    {"key": "thk_0500", "label": 'Cal 0.500"', "kerf_in": 0.250, "thicknesses": (0.500,)},
    {"key": "thk_0625", "label": 'Cal 0.625"', "kerf_in": 0.250, "thicknesses": (0.625,)},
    {"key": "thk_0750", "label": 'Cal 0.750"', "kerf_in": 0.250, "thicknesses": (0.750,)},
    {"key": "thk_1000", "label": 'Cal 1.000"', "kerf_in": 0.313, "thicknesses": (1.000,)},
    {"key": "thk_1250", "label": 'Cal 1.250"', "kerf_in": 0.313, "thicknesses": (1.250,)},
    {"key": "thk_1500", "label": 'Cal 1.500"', "kerf_in": 0.375, "thicknesses": (1.500,)},
    {"key": "thk_1750", "label": 'Cal 1.750"', "kerf_in": 0.375, "thicknesses": (1.750,)},
    {"key": "thk_2000", "label": 'Cal 2.000"', "kerf_in": 0.375, "thicknesses": (2.000,)},
)

_RULE_BY_KEY = {str(row["key"]): row for row in CUT_GAP_RULES}
_GAUGE_TO_KEY = {
    gauge: str(row["key"])
    for row in CUT_GAP_RULES
    for gauge in tuple(row.get("gauges") or ())
}
# Espesores que React-Herinox puede generar desde sus gauges de acero,
# inoxidable y aluminio. Solo se resuelven los gauges explícitos de la tabla.
_GAUGE_DECIMALS: tuple[tuple[float, str], ...] = (
    (0.0478, "cal_18"),
    (0.0500, "cal_18"),
    (0.0598, "cal_16"),
    (0.0625, "cal_16"),
    (0.0641, "cal_16"),
    (0.0747, "cal_14"),
    (0.0781, "cal_14"),
    (0.1019, "cal_10"),
    (0.1046, "cal_12"),
    (0.1094, "cal_12"),
    (0.1196, "cal_11"),
    (0.1250, "cal_11"),
    (0.1345, "cal_10"),
    (0.1406, "cal_10"),
)
_DECIMAL_MATCH_TOLERANCE_IN = 0.0035
_GAUGE_DECIMAL_MATCH_TOLERANCE_IN = 0.005


def _config_path() -> Path:
    try:
        import config as app_config

        return Path(app_config.asegurar_archivo_persistente(_CONFIG_RELATIVE_PATH))
    except Exception:
        return Path(__file__).resolve().parents[2] / _CONFIG_RELATIVE_PATH


def default_cut_gap_settings() -> dict[str, Any]:
    return {
        "version": 1,
        "plate_to_piece_in": PLATE_TO_PIECE_DEFAULT_IN,
        "kerf_by_rule": {
            str(row["key"]): float(row["kerf_in"])
            for row in CUT_GAP_RULES
        },
    }


def _positive_inches(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CutGapTableError(f"{field} debe ser un número en pulgadas.") from exc
    if not 0.0 < number <= 5.0:
        raise CutGapTableError(f"{field} debe estar entre 0 y 5 pulgadas.")
    return number


def normalize_cut_gap_settings(raw: Any) -> dict[str, Any]:
    """Valida el payload persistente sin permitir borrar reglas oficiales."""
    if not isinstance(raw, dict):
        raise CutGapTableError("La tabla de gaps debe ser un objeto JSON.")

    defaults = default_cut_gap_settings()
    margin = _positive_inches(
        raw.get("plate_to_piece_in", defaults["plate_to_piece_in"]),
        field="Placa a pieza",
    )
    provided_kerfs = raw.get("kerf_by_rule", {})
    if provided_kerfs is None:
        provided_kerfs = {}
    if not isinstance(provided_kerfs, dict):
        raise CutGapTableError("Los gaps entre piezas deben ser un objeto JSON.")

    kerfs = dict(defaults["kerf_by_rule"])
    for key in kerfs:
        if key in provided_kerfs:
            kerfs[key] = _positive_inches(
                provided_kerfs[key],
                field=f'Entre piezas ({_RULE_BY_KEY[key]["label"]})',
            )
    return {
        "version": 1,
        "plate_to_piece_in": margin,
        "kerf_by_rule": kerfs,
    }


def load_cut_gap_settings() -> dict[str, Any]:
    path = _config_path()
    if not path.is_file():
        return default_cut_gap_settings()
    try:
        return normalize_cut_gap_settings(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        # Un archivo local inválido no puede degradar a un kerf global incorrecto.
        return default_cut_gap_settings()


def save_cut_gap_settings(settings: dict[str, Any]) -> Path:
    normalized = normalize_cut_gap_settings(settings)
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def cut_gap_display_rows(settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    current = normalize_cut_gap_settings(settings) if settings is not None else load_cut_gap_settings()
    return [
        {
            "key": str(rule["key"]),
            "label": str(rule["label"]),
            "kerf_in": float(current["kerf_by_rule"][str(rule["key"])]),
        }
        for rule in CUT_GAP_RULES
    ]


def verify_cut_gap_edit_password(password: str) -> bool:
    digest = hashlib.sha256(str(password or "").encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, _EDIT_PASSWORD_SHA256)


def _parse_inches(value: Any) -> float | None:
    text = str(value or "").strip().upper().replace(",", ".").replace('"', "")
    text = re.sub(r"^(?:CAL(?:IBRE)?|GA(?:UGE)?)[.\s]*", "", text).strip()
    if not text or re.search(r"[A-Z]", text):
        return None
    try:
        if " " in text and "/" in text:
            whole, fraction = text.split(" ", 1)
            numerator, denominator = fraction.split("/", 1)
            return float(whole) + (float(numerator) / float(denominator))
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            return float(numerator) / float(denominator)
        return float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _rule_key_for_calibre(calibre: Any) -> str | None:
    raw = str(calibre or "").strip().upper()
    normalized = re.sub(r"^(?:CAL(?:IBRE)?|GA(?:UGE)?)[.\s]*", "", raw).strip()
    if normalized in _GAUGE_TO_KEY:
        return _GAUGE_TO_KEY[normalized]

    thickness = _parse_inches(normalized)
    if thickness is None:
        return None

    for rule in CUT_GAP_RULES:
        for expected in tuple(rule.get("thicknesses") or ()):
            if abs(thickness - float(expected)) <= _DECIMAL_MATCH_TOLERANCE_IN:
                return str(rule["key"])
    for expected, key in _GAUGE_DECIMALS:
        if abs(thickness - expected) <= _GAUGE_DECIMAL_MATCH_TOLERANCE_IN:
            return key
    return None


def gaps_for_calibre(
    calibre: Any,
    *,
    settings: dict[str, Any] | None = None,
) -> tuple[float, float, dict[str, Any]]:
    """Retorna ``(kerf_entre_piezas, margin_placa, regla)`` en pulgadas."""
    rule_key = _rule_key_for_calibre(calibre)
    if rule_key is None:
        raise CutGapTableError(
            f'El calibre {calibre!r} no tiene una regla en la TABLA GAPS DE CORTE.'
        )
    current = normalize_cut_gap_settings(settings) if settings is not None else load_cut_gap_settings()
    rule = copy.deepcopy(_RULE_BY_KEY[rule_key])
    kerf = float(current["kerf_by_rule"][rule_key])
    margin = float(current["plate_to_piece_in"])
    return kerf, margin, rule
