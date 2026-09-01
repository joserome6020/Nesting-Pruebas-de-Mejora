"""
Split DXF verticales de cobre (CyPTube): Corte vs Marcaje + manifiesto JSON.

CypTube no separa layers de forma fiable → 2 DXF por barra vertical:
  *_Corte.dxf   → CUT_OUTER / CUT_INNER / BAR_START (completo)
  *_Marcaje.dxf → MARK + guillotina de origen y de fin (referencia de contorno)

JSON en NESTEOS DE COBRE/cyptube_verticales.json con A_mm = ancho_mm + 0.2
(parametro A del Standard pipe) y B_mm = 6.0 (espesor ~0.25\").
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import ezdxf

from modules.nest_exporter import (
    DxfExportValidationError,
    TOL_GEOM_MM,
    _clone_prod_entity,
    _save_cobre_dxf_atomic,
)

CYPTUBE_JSON_FILENAME = "cyptube_verticales.json"
CYPTUBE_JSON_VERSION = 1
SUFIJO_CORTE = "Corte"
SUFIJO_MARCAJE = "Marcaje"
PARAM_A_OFFSET_MM = 0.2
PARAM_B_MM_DEFAULT = 6.0


@dataclass
class CyptubeSplitPaths:
    corte: str
    marcaje: str = ""


@dataclass
class CyptubeVerticalRecord:
    base_name: str
    sheet_code: str
    canal: str
    ancho_mm: float
    ancho_in: float
    A_mm: float
    B_mm: float
    corte: str
    marcaje: str = ""
    cu_especial_vertical: bool = False
    thickness_in: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "base_name": self.base_name,
            "sheet_code": self.sheet_code,
            "canal": self.canal,
            "ancho_mm": round(self.ancho_mm, 4),
            "ancho_in": round(self.ancho_in, 6),
            "A_mm": round(self.A_mm, 4),
            "B_mm": round(self.B_mm, 4),
            "cu_especial_vertical": bool(self.cu_especial_vertical),
            "corte": {"ruta": self.corte, "rol": "corte"},
        }
        if self.marcaje:
            d["marcaje"] = {"ruta": self.marcaje, "rol": "marcaje"}
        else:
            d["marcaje"] = None
            d["sin_marcaje"] = True
        if self.thickness_in is not None:
            d["thickness_in"] = self.thickness_in
        if self.extras:
            d.update(self.extras)
        return d

    def flat_archivos(self) -> list[dict[str, Any]]:
        common = {
            "base_name": self.base_name,
            "sheet_code": self.sheet_code,
            "canal": self.canal,
            "ancho_mm": round(self.ancho_mm, 4),
            "ancho_in": round(self.ancho_in, 6),
            "A_mm": round(self.A_mm, 4),
            "B_mm": round(self.B_mm, 4),
            "cu_especial_vertical": bool(self.cu_especial_vertical),
        }
        if self.thickness_in is not None:
            common["thickness_in"] = self.thickness_in
        out = [{**common, "ruta": self.corte, "rol": "corte"}]
        if self.marcaje:
            out.append({**common, "ruta": self.marcaje, "rol": "marcaje"})
        return out


def cyptube_param_A_mm(ancho_mm: float, *, offset_mm: float = PARAM_A_OFFSET_MM) -> float:
    return float(ancho_mm) + float(offset_mm)


def path_con_sufijo(path: str, sufijo: str) -> str:
    base, ext = os.path.splitext(path)
    if not ext:
        ext = ".dxf"
    return f"{base}_{sufijo}{ext}"


def _new_cobre_r2000(src_doc):
    out = ezdxf.new("R2000")
    out.header["$INSUNITS"] = int(src_doc.header.get("$INSUNITS", 4) or 4)
    out.header["$MEASUREMENT"] = 1
    for name, color in (
        ("CUT_OUTER", 1),
        ("CUT_INNER", 3),
        ("MARK", 4),
        ("BAR_START", 8),
        ("CUT_CU", 1),
    ):
        if name not in out.layers:
            out.layers.new(name, dxfattribs={"color": color})
    for layer in out.layers:
        try:
            layer.on()
            layer.thaw()
        except Exception:
            pass
    return out


def _line_xy(ent) -> tuple[float, float, float, float] | None:
    if ent.dxftype() != "LINE":
        return None
    try:
        a, b = ent.dxf.start, ent.dxf.end
        return float(a.x), float(a.y), float(b.x), float(b.y)
    except Exception:
        return None


def _guillotinas_referencia_vertical(src_doc, bar_width_mm: float) -> list:
    """
    Origen + fin para CypTube (barra ya rotada sin_gap):
    LINE a ancho completo ≈ horizontal (Y constante, span X ≈ bar_w).
    Incluye BAR_START (origen) y CUT_OUTER en los extremos.
    """
    bar_w = float(bar_width_mm or 0.0)
    if bar_w <= TOL_GEOM_MM:
        return []
    tol = max(1.0, TOL_GEOM_MM * 4)
    candidates: list[tuple[float, Any]] = []
    for ent in src_doc.modelspace():
        layer = str(getattr(ent.dxf, "layer", "") or "").upper()
        if layer not in {"CUT_OUTER", "BAR_START"}:
            continue
        xy = _line_xy(ent)
        if xy is None:
            continue
        ax, ay, bx, by = xy
        # Tras rotación vertical: guillotina = Y casi constante, span en X ≈ ancho.
        if abs(ay - by) > tol:
            continue
        span = abs(bx - ax)
        if span < bar_w - tol:
            continue
        candidates.append(((ay + by) * 0.5, ent))
    if not candidates:
        return []
    candidates.sort(key=lambda t: t[0])
    y_min = candidates[0][0]
    y_max = candidates[-1][0]
    # Solo extremos (origen + fin); no intermedias entre piezas.
    out: list = []
    seen: set[int] = set()
    for y, ent in candidates:
        if abs(y - y_min) <= tol or abs(y - y_max) <= tol:
            eid = id(ent)
            if eid in seen:
                continue
            seen.add(eid)
            out.append(ent)
    return out


def _doc_corte(src_doc):
    out = _new_cobre_r2000(src_doc)
    msp = out.modelspace()
    for ent in src_doc.modelspace():
        layer = str(getattr(ent.dxf, "layer", "") or "").upper()
        if layer.startswith("CUT_") or layer in {"BAR_START", "CUT_CU"}:
            _clone_prod_entity(msp, ent, str(getattr(ent.dxf, "layer", "") or "0"))
    return out


def _doc_marcaje(src_doc, bar_width_mm: float):
    """MARK + guillotina origen/fin (contorno de referencia CypTube)."""
    out = _new_cobre_r2000(src_doc)
    msp = out.modelspace()
    for ent in src_doc.modelspace():
        layer = str(getattr(ent.dxf, "layer", "") or "").upper()
        if layer == "MARK":
            _clone_prod_entity(msp, ent, str(getattr(ent.dxf, "layer", "") or "0"))
    for ent in _guillotinas_referencia_vertical(src_doc, bar_width_mm):
        _clone_prod_entity(
            msp, ent, str(getattr(ent.dxf, "layer", "") or "CUT_OUTER")
        )
    return out


def split_cyptube_vertical_dxf(
    path_combined: str,
    *,
    bar_width_mm: float,
    canal: str,
    sheet_code: str = "",
    cu_especial_vertical: bool = False,
    thickness_in: float | None = None,
    B_mm: float = PARAM_B_MM_DEFAULT,
    remove_combined: bool = True,
    include_marcaje: bool = True,
) -> tuple[CyptubeSplitPaths, CyptubeVerticalRecord]:
    """
    Parte el DXF vertical combinado en *_Corte y (opcional) *_Marcaje.
    Con ``include_marcaje=False`` (switch cobre sin marcaje) solo escribe Corte;
    CypTube RPA procesa corte y omite marcaje si no hay ruta.
    """
    path_combined = os.path.abspath(str(path_combined))
    if not os.path.isfile(path_combined):
        raise DxfExportValidationError(
            f"CyPTube split: no existe DXF combinado: {path_combined}"
        )
    try:
        src = ezdxf.readfile(path_combined)
    except Exception as exc:
        raise DxfExportValidationError(
            f"CyPTube split: DXF ilegible ({path_combined}): {exc}"
        ) from exc

    path_corte = path_con_sufijo(path_combined, SUFIJO_CORTE)
    path_marcaje = path_con_sufijo(path_combined, SUFIJO_MARCAJE) if include_marcaje else ""

    doc_corte = _doc_corte(src)
    sheet_stub = {"width": float(bar_width_mm), "Width": float(bar_width_mm)}
    _save_cobre_dxf_atomic(doc_corte, path_corte, sheet_stub)

    if include_marcaje:
        doc_marcaje = _doc_marcaje(src, float(bar_width_mm))
        _save_cobre_dxf_atomic(doc_marcaje, path_marcaje, sheet_stub)
    else:
        # Limpia un Marcaje viejo del mismo stem si quedó de un export previo.
        stale = path_con_sufijo(path_combined, SUFIJO_MARCAJE)
        try:
            if os.path.isfile(stale):
                os.remove(stale)
        except OSError:
            pass

    if remove_combined:
        try:
            os.remove(path_combined)
        except OSError:
            pass

    ancho = float(bar_width_mm)
    a_mm = cyptube_param_A_mm(ancho)
    base_name = os.path.splitext(os.path.basename(path_combined))[0]
    record = CyptubeVerticalRecord(
        base_name=base_name,
        sheet_code=str(sheet_code or base_name).strip(),
        canal=str(canal or "").strip(),
        ancho_mm=ancho,
        ancho_in=(ancho / 25.4) if ancho > 0 else 0.0,
        A_mm=a_mm,
        B_mm=float(B_mm),
        corte=path_corte,
        marcaje=path_marcaje,
        cu_especial_vertical=bool(cu_especial_vertical),
        thickness_in=thickness_in,
    )
    return CyptubeSplitPaths(corte=path_corte, marcaje=path_marcaje), record


def escribir_cyptube_verticales_json(
    nesteos_cobre_dir: str,
    records: list[CyptubeVerticalRecord],
) -> str | None:
    """Escribe/reescribe cyptube_verticales.json en NESTEOS DE COBRE."""
    if not records:
        return None
    dest_dir = os.path.abspath(str(nesteos_cobre_dir))
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, CYPTUBE_JSON_FILENAME)
    solo_corte = all(not (r.marcaje or "").strip() for r in records)
    desc = (
        "Barras verticales cobre (Amada ESP / escalón sin_gap). "
        "A_mm = ancho_mm + 0.2 (Standard pipe long side); "
        "B_mm = 6.0 (short side / espesor ~0.25\"). "
    )
    if solo_corte:
        desc += "sin_marcaje=true: solo *_Corte.dxf (RPA corte; sin *_Marcaje)."
    else:
        desc += "Marcaje = MARK + guillotina origen/fin (contorno CypTube)."
    payload = {
        "version": CYPTUBE_JSON_VERSION,
        "software": "CypTube",
        "descripcion": desc,
        "param_A_offset_mm": PARAM_A_OFFSET_MM,
        "param_B_mm_default": PARAM_B_MM_DEFAULT,
        "sin_marcaje": bool(solo_corte),
        "barras": [r.as_dict() for r in records],
        "archivos": [item for r in records for item in r.flat_archivos()],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path
