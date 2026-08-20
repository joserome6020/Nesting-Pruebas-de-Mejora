"""Metadata de piezas AutoDXF desde nombre de archivo y carpetas de ruta."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Tuple

RE_CARPETA_CAL = re.compile(
    r"^Cal\s+([0-9]+(?:\.[0-9]+)?(?:\s*/\s*[0-9]+)?)\s+(.+?)\s*$",
    re.IGNORECASE,
)
RE_CARPETA_ANCHO = re.compile(
    r"^([0-9]+(?:\.[0-9]+)?)\s*X\s*([0-9]+(?:\.[0-9]+)?)\s*IN\s*$",
    re.IGNORECASE,
)
RE_QTY_TOKEN = re.compile(
    r"\b(?:QTY|QUANTITY|CANT|CANTIDAD)\b\s*[:=]?\s*(\d+)",
    re.IGNORECASE,
)
RE_CAL_TOKEN = re.compile(
    r"\b(?:CAL|CALIBRE|GA|GAUGE|THK|THICK|THICKNESS|ESP|ESPESOR)\b\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)


def normalizar_material_autodxf(texto_material: str, *, default: str = "CARBONO") -> str:
    import unicodedata

    mat = str(texto_material or "").strip().upper()
    mat = mat.replace("_", " ")
    mat = re.sub(r"\s+", " ", mat)
    mat = "".join(
        c for c in unicodedata.normalize("NFD", mat) if unicodedata.category(c) != "Mn"
    )
    if not mat:
        return default

    if mat in ("CU", "COPPER") or "COBRE" in mat:
        return "CU"

    # Galv antes que A 36 (misma regla AutoDXF / consulta_herinox_bridge).
    if (
        "GALVAN" in mat
        or "GALVANIZADO" in mat
        or "G90" in mat
        or "HDG" in mat
        or "GALVANNEAL" in mat
        or "ZINCADO" in mat
        or ("GALV" in mat and ("A 36" in mat or "A36" in mat or "ZINC" in mat))
        or ("ZINC" in mat and "BRONZE" not in mat)
    ):
        return "GALVANIZADO"

    if re.fullmatch(r"A\s*36(?:\s+GALV)?", mat) or mat in ("A36", "A36GALV"):
        return "A 36 GALV" if "GALV" in mat else "A 36"

    if ("CARBON" in mat and "STEEL" in mat) or ("STEEL" in mat and "CARBON" in mat):
        return "CARBONO"
    if "ACERO" in mat and "CARBONO" in mat:
        return "CARBONO"
    if "CARBON" in mat or mat == "CARBONO":
        return "CARBONO"
    if "MILD STEEL" in mat or "HOT ROLLED" in mat or "COLD ROLLED" in mat or "HRPO" in mat:
        return "CARBONO"
    if "SUAVE" in mat or "FORJAD" in mat or "FORGED" in mat or "GENERIC" in mat or "GENERICO" in mat:
        return "CARBONO"
    if "ACERO" in mat and "INOX" not in mat and "STAINLESS" not in mat and "ALUMIN" not in mat:
        return "CARBONO"
    if (
        "STEEL" in mat
        and "STAINLESS" not in mat
        and "INOX" not in mat
        and "ALUMIN" not in mat
        and "TOOL" not in mat
    ):
        return "CARBONO"

    if "STAINLESS" in mat or "INOX" in mat or "INOXIDABLE" in mat or "SSTL" in mat:
        return "INOXIDABLE"

    if "ALUMINUM" in mat or "ALUMINIO" in mat or mat.startswith("AL ") or mat == "AL":
        return "ALUMINIO"

    return mat


def parsear_nombre_archivo_dxf(nombre_archivo: str) -> Dict[str, str]:
    nombre_base = os.path.splitext(os.path.basename(str(nombre_archivo)))[0]
    partes = [p.strip() for p in nombre_base.split(",") if p.strip()]

    if not partes:
        return {
            "pieza": nombre_base or "PIEZA",
            "material": "",
            "qty": "1",
            "calibre": "",
        }

    pieza = partes[0]
    qty_str = "1"
    cal = ""
    material_tokens = []

    for token in partes[1:]:
        token_limpio = token.strip()
        token_up = token_limpio.upper()

        m_qty = RE_QTY_TOKEN.search(token_up)
        if m_qty:
            qty_str = m_qty.group(1)
            continue

        m_cal = RE_CAL_TOKEN.search(token_up)
        if m_cal:
            cal = m_cal.group(1)
            continue

        if token_up in ("CU", "COPPER", "COBRE"):
            material_tokens.append("Copper")
            continue

        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", token_up):
            cal = token_up
            continue

        material_tokens.append(token_limpio)

    material_crudo = ", ".join(material_tokens)
    material_final = normalizar_material_autodxf(material_crudo, default="") if material_crudo else ""

    return {
        "pieza": pieza,
        "material": material_final,
        "qty": qty_str,
        "calibre": cal,
    }


def extraer_metadata_carpetas_autodxf(ruta_dxf: str) -> Dict[str, str]:
    """Lee `Cal {esp} {material}` desde la ruta (ej. carpeta `Cal 0.25 CU` en AutoDXF)."""
    result = {"material": "", "calibre": "", "ancho_largo_in": ""}

    try:
        cur = os.path.dirname(os.path.abspath(str(ruta_dxf or "")))
    except Exception:
        return result

    vistos = set()
    while cur and cur not in vistos:
        vistos.add(cur)
        part = os.path.basename(cur).strip()
        if part:
            m_cal = RE_CARPETA_CAL.match(part)
            if m_cal and not result["calibre"]:
                result["calibre"] = str(m_cal.group(1)).strip()
                if not result["material"]:
                    result["material"] = normalizar_material_autodxf(
                        m_cal.group(2), default=""
                    )

            m_ancho = RE_CARPETA_ANCHO.match(part)
            if m_ancho and not result["ancho_largo_in"]:
                result["ancho_largo_in"] = str(m_ancho.group(2)).strip()

        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    return result


def combinar_metadata_dxf(
    ruta_dxf: str,
    nombre_archivo: Optional[str] = None,
    *,
    default_material: str = "CARBONO",
    default_calibre: str = "0.375",
) -> Tuple[str, str, str, str, Dict[str, Any]]:
    """
    Combina metadata de carpeta AutoDXF + nombre de archivo.
    Prioridad: carpeta Cal/material > nombre DXF > defaults.
    """
    nombre = nombre_archivo or os.path.basename(str(ruta_dxf))
    meta_archivo = parsear_nombre_archivo_dxf(nombre)
    meta_carpeta = extraer_metadata_carpetas_autodxf(ruta_dxf)

    material = (
        meta_archivo.get("material")
        or meta_carpeta.get("material")
        or default_material
    )
    calibre = (
        meta_archivo.get("calibre")
        or meta_carpeta.get("calibre")
        or default_calibre
    )
    # Empatar materia bruta Herinox: Cal CAD crudo → decimal de tabla ANS.
    try:
        from modules.arga_gauge_snap import snap_calibre_token

        calibre = snap_calibre_token(str(calibre), str(material)) or calibre
    except Exception:
        pass

    extras: Dict[str, Any] = {}
    ancho = str(meta_carpeta.get("ancho_largo_in") or "").strip()
    if ancho:
        extras["ancho_largo_in"] = ancho

    return (
        str(meta_archivo.get("pieza") or os.path.splitext(nombre)[0]),
        str(material),
        str(meta_archivo.get("qty") or "1"),
        str(calibre),
        extras,
    )
