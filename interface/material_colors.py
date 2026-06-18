"""Colores característicos por familia de material (PARTS, visor CAD y nesting)."""
from __future__ import annotations

import re
from dataclasses import dataclass


CAD_VIEW_BG = "#0B1220"


@dataclass(frozen=True)
class MaterialPalette:
    fill: str
    edge: str
    hole: str
    sel_fill: str
    sel_edge: str
    row_a: str | None = None
    row_b: str | None = None


_PALETTES: dict[str, MaterialPalette] = {
    # Acero genérico — gris neutro
    "default": MaterialPalette(
        "#A3A8B0", "#52525B", CAD_VIEW_BG, "#C4C8CE", "#D4D4D8",
    ),
    # A 36 — gris acero (no azul)
    "a36": MaterialPalette(
        "#9CA3AF", "#4B5563", CAD_VIEW_BG, "#B8BEC8", "#D1D5DB",
        "#F3F4F6", "#E5E7EB",
    ),
    # Galvanizado — gris plata
    "galv": MaterialPalette(
        "#C5C9CE", "#6B7280", CAD_VIEW_BG, "#D8DCE1", "#E5E7EB",
        "#ECEFF3", "#E2E6EC",
    ),
    # Carbono — gris oscuro
    "carbono": MaterialPalette(
        "#8B9099", "#3F3F46", CAD_VIEW_BG, "#A8ADB5", "#D1D5DB",
        "#EEF1F4", "#E4E9EE",
    ),
    # A 514 — gris cálido
    "a514": MaterialPalette(
        "#9A9590", "#57534E", CAD_VIEW_BG, "#B5B0AB", "#D6D3D1",
        "#F0EEEC", "#E7E5E4",
    ),
    # A 572 — gris verdoso suave
    "a572": MaterialPalette(
        "#8A9188", "#4D524A", CAD_VIEW_BG, "#A3AAA1", "#D1D5DB",
        "#E8EDE7", "#DCE4DA",
    ),
    # Aluminio — claro como placas
    "aluminio": MaterialPalette(
        "#DDE4EC", "#64748B", CAD_VIEW_BG, "#EEF2F7", "#94A3B8",
        "#F4F7FA", "#E8EDF3",
    ),
    # Inox — gris cromo
    "inox": MaterialPalette(
        "#B0B8C0", "#52525B", CAD_VIEW_BG, "#CCD2D9", "#D1D5DB",
        "#ECEFF2", "#E2E7EC",
    ),
    # Cobre — cuerpo cobre; huecos siempre fondo oscuro vía paleta_cad_hex / nesting
    "cu": MaterialPalette(
        "#B87333", "#4A2F1A", CAD_VIEW_BG, "#E8A55C", "#FDE68A",
        "#F3E2CF", "#E8D4BC",
    ),
}


def _norm_material(material: str) -> str:
    m = str(material or "").strip().upper().replace("_", " ")
    return re.sub(r"\s+", " ", m)


def clasificar_material_familia(material: str) -> str:
    m = _norm_material(material)
    if not m:
        return "default"

    if m in ("CU", "COBRE", "COPPER") or "COBRE" in m or "COPPER" in m:
        return "cu"

    if m in ("A 36 GALV", "A36 GALV") or ("A 36" in m and "GALV" in m):
        return "galv"
    if m in ("GALVANIZADO",) or re.search(r"\bGALV\b", m):
        return "galv"

    if m in ("A 36", "A36"):
        return "a36"

    if m.startswith("A 514") or m.startswith("A514") or "A 514" in m:
        return "a514"

    if "A 572" in m or "A572" in m:
        return "a572"

    if "ALUMIN" in m or m.startswith("AL "):
        return "aluminio"

    if "INOX" in m or "STAINLESS" in m or "SSTL" in m or "INOXIDABLE" in m:
        return "inox"

    if m in ("CARBONO", "CARBON") or ("CARBON" in m and "STEEL" in m):
        return "carbono"
    if "ACERO" in m and "CARBONO" in m:
        return "carbono"

    return "default"


def material_desde_clave(clave: str) -> str:
    parts = str(clave or "").strip().split("_", 1)
    return parts[1].strip() if len(parts) > 1 else ""


def resolver_material_pieza(
    pieza: dict | None,
    hoja: dict | None = None,
    clave: str = "",
) -> str:
    mat = str((pieza or {}).get("material") or "").strip()
    if mat:
        return mat
    mat_h = str((hoja or {}).get("material") or "").strip()
    if mat_h:
        return mat_h
    return material_desde_clave(clave)


def es_contexto_cobre(
    pieza: dict | None = None,
    hoja: dict | None = None,
    clave: str = "",
) -> bool:
    if bool((hoja or {}).get("modo_largos_cu")):
        return True
    clv = str(clave or "").strip().upper()
    partes = [p for p in clv.replace("|", "_").split("_") if p]
    if "CU" in partes or clv.endswith("_CU"):
        return True
    return clasificar_material_familia(resolver_material_pieza(pieza, hoja, clave)) == "cu"


def paleta_material(material: str) -> MaterialPalette:
    fam = "cu" if clasificar_material_familia(material) == "cu" else clasificar_material_familia(material)
    return _PALETTES.get(fam, _PALETTES["default"])


def paleta_pieza_nesting(
    pieza: dict | None = None,
    hoja: dict | None = None,
    clave: str = "",
) -> MaterialPalette:
    if es_contexto_cobre(pieza, hoja, clave):
        return _PALETTES["cu"]
    return paleta_material(resolver_material_pieza(pieza, hoja, clave))


def paleta_cad_hex(material: str | None) -> tuple[str, str, str]:
    """fill, hole/bg, edge para visor CAD y thumbnails."""
    pal = paleta_material(str(material or ""))
    # Los cortes internos siempre se ven como vacío (fondo oscuro), en todos los materiales.
    return pal.fill, CAD_VIEW_BG, pal.edge


def fila_fondo_material(material: str | None, idx: int) -> str:
    pal = paleta_material(str(material or ""))
    if pal.row_a and pal.row_b:
        return pal.row_a if idx % 2 == 0 else pal.row_b
    return "#FFFFFF" if idx % 2 == 0 else "#F8FAFC"
