"""
Log en tiempo real de exportación DXF — terminal (flush) + archivo rastreable.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import datetime
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOG_DIR = os.path.join(_ROOT, "_logs")
_LOG_FILE = os.path.join(_LOG_DIR, "dxf_export.log")


def _write_line(line: str) -> None:
    print(line, flush=True)
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
    except Exception:
        pass
    _pump_ui()


def _pump_ui() -> None:
    """Permite que Qt procese eventos mientras el export corre en hilo de fondo."""
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.processEvents()
    except Exception:
        pass


def log(msg: str, *, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _write_line(f"[DXF-EXPORT][{ts}][{level}] {msg}")


def log_section(title: str) -> None:
    bar = "=" * 70
    _write_line(bar)
    log(title)
    _write_line(bar)


def _poly_bounds_mm(outer) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for pt in outer or []:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            try:
                xs.append(float(pt[0]))
                ys.append(float(pt[1]))
            except (TypeError, ValueError):
                continue
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _fmt_bounds(b: tuple[float, float, float, float] | None) -> str:
    if not b:
        return "sin_bbox"
    minx, miny, maxx, maxy = b
    return (
        f"({minx:.1f},{miny:.1f})-({maxx:.1f},{maxy:.1f}) "
        f"w={maxx - minx:.1f} h={maxy - miny:.1f}"
    )


def resolve_export_mode(p: dict) -> str:
    if bool(p.get("plasma_export")):
        if str(p.get("ruta") or "").strip():
            return "PLASMA:desfase_dxf_fuente"
        return "PLASMA:poligono_nest_sin_fuente"
    if bool(p.get("compensated_plasma_source")) and str(p.get("ruta") or "").strip():
        return "PLASMA_LEGACY:fuente_compensada"
    if bool(p.get("cu_largos_piece")):
        if str(p.get("ruta") or "").strip():
            return "COBRE_LARGOS:fuente_dxF"
        return "COBRE_LARGOS:poligono_nest"
    if bool(p.get("prefer_source_dxf")) and str(p.get("ruta") or "").strip():
        return "LASER:fuente_dxF_1:1"
    if str(p.get("ruta") or "").strip() and not bool(p.get("prefer_source_dxf")):
        return "BLOQUE:clon_dxf"
    return "POLIGONO:nest_colocado"


def format_placement_spec(p: dict, *, index: int = 0) -> str:
    name = str(p.get("part_name") or p.get("name") or f"PART_{index}")
    ruta = str(p.get("ruta") or "").strip()
    ruta_short = os.path.basename(ruta) if ruta else "(sin fuente)"
    outer = p.get("outer") or p.get("outer_poly") or []
    holes = p.get("holes") or p.get("inner") or []
    n_holes = len([h for h in holes if h])
    marks = p.get("marks") or p.get("mark") or []
    parts = [
        f"nombre={name}",
        f"fuente={ruta_short}",
        f"shift=({float(p.get('shift_x', 0) or 0):.2f},{float(p.get('shift_y', 0) or 0):.2f})",
        f"rot={float(p.get('rot_deg', 0) or 0):.1f}deg",
        f"orig=({float(p.get('orig_minx', 0) or 0):.2f},{float(p.get('orig_miny', 0) or 0):.2f})",
        f"outer_bbox={_fmt_bounds(_poly_bounds_mm(outer))}",
        f"huecos={n_holes}",
        f"marcas={len(marks)}",
    ]
    if bool(p.get("compensated")):
        parts.append("compensada=SI")
    if bool(p.get("plasma_export")):
        parts.append(f"plasma_offset_mm={float(p.get('plasma_offset_mm') or 0):.3f}")
    if bool(p.get("cu_largos_piece")):
        parts.append(
            f"cu_slice={int(p.get('cu_slice_idx', 0) or 0)+1}/"
            f"{int(p.get('cu_slice_count', 1) or 1)}"
        )
    layer = p.get("layer_override")
    if layer:
        parts.append(f"capa={layer}")
    return " | ".join(parts)


def log_sheet_plan(
    *,
    clave: str,
    hoja: dict,
    sheet_info: dict,
    nombre_archivo: str,
    carpeta_principal: str,
    generar_plasma: bool,
    solo_plasma: bool,
    compensada_manual: bool,
    off_manual: float,
    n_principal: int,
    n_plasma: int,
) -> None:
    display = str(hoja.get("sheet_display_name") or hoja.get("placa_id") or "?")
    eff = float(hoja.get("eficiencia") or 0.0)
    log(
        f"HOJA {display} | clave={clave} | archivo={nombre_archivo} | "
        f"placa={sheet_info.get('length')}x{sheet_info.get('width')} mm | "
        f"material={sheet_info.get('material')} | esp={sheet_info.get('thickness')} | "
        f"efic={eff:.1f}% | canal_principal={carpeta_principal} | "
        f"plasma={'SI' if generar_plasma else 'NO'} | solo_plasma={'SI' if solo_plasma else 'NO'} | "
        f"comp_manual={'SI' if compensada_manual else 'NO'} | offset_mm={off_manual:.3f} | "
        f"piezas_laser={n_principal} | piezas_plasma={n_plasma}"
    )


def log_entities_added(
    part_name: str,
    entities,
    *,
    mode: str,
    ok: bool = True,
) -> None:
    if not entities:
        log(f"  -> {part_name}: SIN ENTIDADES ({mode})", level="WARN")
        return
    types: Counter[str] = Counter()
    layers: Counter[str] = Counter()
    xs: list[float] = []
    ys: list[float] = []
    for ent in entities:
        typ = ent.dxftype()
        types[typ] += 1
        layers[str(getattr(ent.dxf, "layer", "") or "")] += 1
        try:
            if typ == "LINE":
                xs.extend([float(ent.dxf.start.x), float(ent.dxf.end.x)])
                ys.extend([float(ent.dxf.start.y), float(ent.dxf.end.y)])
            elif typ == "ARC":
                r = float(ent.dxf.radius)
                c = ent.dxf.center
                xs.extend([float(c.x) - r, float(c.x) + r])
                ys.extend([float(c.y) - r, float(c.y) + r])
            elif typ == "CIRCLE":
                r = float(ent.dxf.radius)
                c = ent.dxf.center
                xs.extend([float(c.x) - r, float(c.x) + r])
                ys.extend([float(c.y) - r, float(c.y) + r])
            elif typ == "LWPOLYLINE":
                for x, y, *_ in ent.get_points("xy"):
                    xs.append(float(x))
                    ys.append(float(y))
        except Exception:
            pass
    bbox = ""
    if xs:
        bbox = (
            f" | bbox=({min(xs):.1f},{min(ys):.1f})-({max(xs):.1f},{max(ys):.1f})"
        )
    typ_str = ",".join(f"{k}x{v}" for k, v in sorted(types.items()))
    lay_str = ",".join(f"{k}x{v}" for k, v in sorted(layers.items()))
    level = "INFO" if ok else "WARN"
    log(
        f"  -> {part_name}: OK {len(entities)} ent | tipos=[{typ_str}] | "
        f"capas=[{lay_str}]{bbox} | modo={mode}",
        level=level,
    )


def log_export_start(
    out_path: str,
    sheet: dict,
    placements: list,
    *,
    canal: str,
    title: str,
    strict: bool,
) -> None:
    sheet_len = float(sheet.get("length", 0) or 0)
    sheet_w = float(sheet.get("width", 0) or 0)
    log_section(f"CREANDO DXF [{canal}]")
    log(f"ruta_destino={out_path}")
    log(f"titulo={title}")
    log(f"placa={sheet_len:.1f}x{sheet_w:.1f} mm | arga={sheet.get('arga_code', '')}")
    log(f"material={sheet.get('material', '')} | espesor={sheet.get('thickness', '')}")
    log(f"piezas_en_lista={len(placements or [])} | strict={strict}")
    log(f"log_archivo={_LOG_FILE}")


def log_export_done(
    out_path: str,
    *,
    canal: str,
    exported_pieces: int,
    file_size: int | None = None,
) -> None:
    size_kb = ""
    if file_size is None and os.path.isfile(out_path):
        try:
            file_size = os.path.getsize(out_path)
        except OSError:
            file_size = None
    if file_size is not None:
        size_kb = f" | tamano={file_size / 1024:.1f} KB"
    log(
        f"DXF LISTO [{canal}]: {out_path} | piezas={exported_pieces}{size_kb}",
        level="OK",
    )


def log_export_error(out_path: str, exc: BaseException, *, canal: str) -> None:
    log(f"DXF FALLO [{canal}]: {out_path} | {type(exc).__name__}: {exc}", level="ERROR")
