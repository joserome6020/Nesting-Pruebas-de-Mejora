"""Snap de espesor CAD → decimal Herinox/ANS (misma regla que AutoDXF 2.0 iLogic).

Objetivo: Cal DXF y placas materia bruta usen el mismo decimal por calibre
(steel/inox/aluminio), evitando grupos partidos (0.11811 vs 0.1196, etc.).
"""
from __future__ import annotations

from typing import Mapping, Optional

from modules.herinox_sync import HerinoxPlateSync

TOL_GAUGE_IN = 0.008
TOL_EXACT_IN = 0.005

# Placas / fracciones exactas (modo decimal Herinox). Solo ≥ ~3/16":
# no incluir 0.0625/0.125 (son Cal inox/al) — si no, Cal 16 acero 0.060
# caería en 0.0625 en vez de 0.0598.
EXACT_DECIMALS: tuple[float, ...] = (
    0.1875,
    0.188,
    0.25,
    0.3125,
    0.375,
    0.4375,
    0.5,
    0.5625,
    0.625,
    0.6875,
    0.75,
    0.875,
    1.0,
    1.125,
    1.25,
    1.5,
    1.75,
    2.0,
)

# Desvíos CAD típicos de planta (±) que deben caer al gauge Herinox.
_CAD_OFFSETS: tuple[float, ...] = (
    0.0,
    0.0005,
    -0.0005,
    0.001,
    -0.001,
    0.0015,
    -0.0015,
    0.002,
    -0.002,
    0.003,
    -0.003,
)


def fmt_decimal(value: float) -> str:
    """Misma limpieza que HerinoxPlateSync._fmt_decimal / iLogic FmtInchDecimal."""
    try:
        return f"{float(value):.4f}".rstrip("0").rstrip(".")
    except Exception:
        return "0"


def material_family(material: str) -> str:
    """Familia de tabla: stainless | aluminum | steel (Galvanizado / A 36 / default)."""
    m = str(material or "").strip().upper().replace("_", " ")
    if any(t in m for t in ("SSTL", "INOX", "STAINLESS", "INOXIDABLE")):
        return "stainless"
    if "ALUMIN" in m or m.startswith("AL "):
        return "aluminum"
    return "steel"


def gauge_table_for_material(material: str) -> Mapping[int, float]:
    fam = material_family(material)
    if fam == "stainless":
        return HerinoxPlateSync.STAINLESS_GAUGE_TO_INCHES
    if fam == "aluminum":
        return HerinoxPlateSync.ALUMINUM_GAUGE_TO_INCHES
    return HerinoxPlateSync.STEEL_GAUGE_TO_INCHES


def snap_thickness_inches(thk_in: float, material: str = "") -> float:
    """Devuelve el decimal canónico (exacto o gauge) o el CAD si no hay match.

    Si hay candidato exacto (≤0.005\") y gauge (≤0.008\"), gana el más cercano.
    Así Cal 6 CAD 0.1928 → 0.1943 (no cae en placa 0.188) y 3/16\" → 0.1875.
    """
    try:
        thk = float(thk_in)
    except (TypeError, ValueError):
        return 0.0
    if thk <= 0:
        return thk

    best_exact = None
    best_exact_d = 1e9
    for ex in EXACT_DECIMALS:
        d = abs(thk - ex)
        if d < best_exact_d:
            best_exact_d = d
            best_exact = float(ex)
    if best_exact is None or best_exact_d > TOL_EXACT_IN:
        best_exact = None
        best_exact_d = 1e9

    tabla = gauge_table_for_material(material)
    best_gauge = None
    best_gauge_d = 1e9
    for inches in tabla.values():
        d = abs(float(inches) - thk)
        if d < best_gauge_d:
            best_gauge_d = d
            best_gauge = float(inches)
    if best_gauge is None or best_gauge_d > TOL_GAUGE_IN:
        best_gauge = None
        best_gauge_d = 1e9

    if best_exact is not None and best_gauge is not None:
        return best_gauge if best_gauge_d <= best_exact_d else best_exact
    if best_gauge is not None:
        return best_gauge
    if best_exact is not None:
        return best_exact
    return thk


def snap_calibre_token(calibre: str, material: str = "") -> str:
    """Normaliza token de calibre (nombre DXF / carpeta) al decimal Herinox."""
    txt = str(calibre or "").strip().replace(",", ".")
    if not txt:
        return ""
    # Ya es calibre nominal entero → decimal de tabla.
    if txt.isdigit() and len(txt) <= 2:
        try:
            g = int(txt)
        except Exception:
            return txt
        mapped = gauge_table_for_material(material).get(g)
        return fmt_decimal(mapped) if mapped is not None else txt
    try:
        thk = float(txt)
    except Exception:
        return txt
    return fmt_decimal(snap_thickness_inches(thk, material))


def assert_all_gauges_snap_stable() -> None:
    """Candado: cada gauge ± offsets CAD (sin cruzar midpoint al vecino) es estable."""
    families = (
        ("steel", HerinoxPlateSync.STEEL_GAUGE_TO_INCHES),
        ("stainless", HerinoxPlateSync.STAINLESS_GAUGE_TO_INCHES),
        ("aluminum", HerinoxPlateSync.ALUMINUM_GAUGE_TO_INCHES),
    )
    for mat_label, tabla in families:
        vals = sorted(float(v) for v in tabla.values())
        for _g, inches in tabla.items():
            target = float(inches)
            idx = vals.index(target)
            prev_d = (target - vals[idx - 1]) if idx > 0 else 1.0
            next_d = (vals[idx + 1] - target) if idx + 1 < len(vals) else 1.0
            # Solo offsets que quedan del lado correcto del midpoint.
            max_off = min(prev_d, next_d) * 0.5 - 1e-6
            for off in _CAD_OFFSETS:
                if abs(off) > max_off:
                    continue
                cad = target + off
                got = snap_thickness_inches(cad, mat_label)
                if abs(got - target) > 1e-9:
                    raise AssertionError(
                        f"{mat_label} gauge {_g}: CAD {cad} -> {got}, esperado {target}"
                    )


# Casos planta conocidos (no solo Cal 11).
KNOWN_CAD_SNAPS: tuple[tuple[float, str, float], ...] = (
    (0.11811, "Galvanizado", 0.1196),
    (0.118, "GALVANIZADO", 0.1196),
    (0.119, "A 36", 0.1196),
    (0.0747, "A 36", 0.0747),
    (0.075, "A 36", 0.0747),  # Cal 14
    (0.060, "A 36", 0.0598),  # Cal 16
    (0.0598, "A 36 GALV", 0.0598),
    (0.125, "SSTL 304", 0.125),  # Cal 11 inox (= exact)
    (0.1094, "SSTL 304", 0.1094),
    (0.110, "INOXIDABLE", 0.1094),  # Cal 12 inox
    (0.0907, "Aluminio", 0.0907),
    (0.091, "ALUMINIO", 0.0907),  # Cal 11 al
    (0.25, "A 36", 0.25),
    (0.375, "Galvanizado", 0.375),
)
