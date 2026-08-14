"""Laboratorio de simulaciÃ³n de nesting en una sola placa (sin inventario multi-hoja)."""
from __future__ import annotations

import copy
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from shapely import affinity

from .algorithm_bridge import empaquetar_una_hoja_mc, engine_name
from .cut_gaps_table import PLATE_TO_PIECE_DEFAULT_IN
from .efficiency_metrics import actualizar_eficiencias_hoja
from .geometry_parser import recuperar_geometria_robusta_detalle
from .manager import _crear_poly_nesting_seguro, enriquecer_piezas_hoja_con_fuentes
from .nest_optimization import NEST_MODES, get_nest_profile


IN_TO_MM = 25.4


@dataclass
class SimPieceEntry:
    ruta: str
    qty: int = 1
    nombre: str = ""
    ref_image: str = ""

    def display_name(self) -> str:
        if self.nombre:
            return self.nombre
        return os.path.splitext(os.path.basename(self.ruta))[0]


@dataclass
class SimRunResult:
    ok: bool
    hoja: dict | None = None
    restos: list = field(default_factory=list)
    piezas_input: list = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    error: str = ""

    def summary_text(self) -> str:
        return "\n".join(self.log_lines)


def inches_to_mm(value: float) -> float:
    return float(value or 0) * IN_TO_MM


def mm_to_inches(value: float) -> float:
    return float(value or 0) / IN_TO_MM


def piezas_pack_desde_hoja(hoja: dict) -> list[dict]:
    """Pool de empaque desde piezas colocadas.

    Preferencia: re-leer DXF (geometrÃ­a completa con barrenos/cavidades).
    Fallback: poligonos de la hoja (a menudo pierden interiores â†’ huecos vacÃ­os).
    """
    from collections import OrderedDict

    from .manager import _as_pack_piece_from_colocada, _is_virtual_piece

    items: list[dict] = []
    for p in (hoja or {}).get("piezas") or []:
        if not isinstance(p, dict):
            continue
        if _is_virtual_piece(str(p.get("nombre") or "")):
            continue
        items.append(p)

    groups: OrderedDict[tuple, list[dict]] = OrderedDict()
    for p in items:
        ruta = str(p.get("ruta") or p.get("dxf_path") or "").strip()
        base = nombre_pieza_agrupado(p.get("nombre"))
        if ruta and os.path.isfile(ruta):
            key = ("ruta", os.path.abspath(ruta), base)
        else:
            key = ("nom", base)
        groups.setdefault(key, []).append(p)

    out: list[dict] = []
    from_dxf = 0
    from_poly = 0
    for key, plist in groups.items():
        qty = len(plist)
        p0 = plist[0]
        calibre = str(p0.get("calibre") or "")
        material = str(p0.get("material") or "")
        if key[0] == "ruta":
            batch, err = piece_from_dxf(
                key[1],
                nombre=str(key[2] or nombre_pieza_agrupado(p0.get("nombre"))),
                qty=qty,
                calibre=calibre or "SIM",
                material=material or "A36",
            )
            if batch and not err:
                out.extend(batch)
                from_dxf += len(batch)
                continue
        for p in plist:
            pack = _as_pack_piece_from_colocada(p)
            if pack is not None:
                out.append(pack)
                from_poly += 1

    # Anota en la primera pieza metadatos de diagnÃ³stico (consumidos por el LAB log).
    if out:
        out[0]["_lab_geom_src"] = {"dxf": from_dxf, "poly": from_poly, "total": len(out)}
    return out


def piezas_pack_limpias(piezas: list[dict]) -> list[dict]:
    """Copia el pool sin metadatos de diagnÃ³stico del LAB."""
    clean: list[dict] = []
    for p in piezas or []:
        if not isinstance(p, dict):
            continue
        q = dict(p)
        q.pop("_lab_geom_src", None)
        clean.append(q)
    return clean


def dims_placa_desde_hoja(hoja: dict) -> tuple[float, float]:
    w = float((hoja or {}).get("placa_w") or (hoja or {}).get("w") or 0)
    h = float((hoja or {}).get("placa_h") or (hoja or {}).get("h") or 0)
    return w, h


def params_motor_desde_hoja(hoja: dict) -> dict[str, Any]:
    return {
        "kerf_in": float((hoja or {}).get("kerf_usado") or (hoja or {}).get("kerf") or 0.25),
        "margin_in": float((hoja or {}).get("margin_usado") or (hoja or {}).get("margin") or PLATE_TO_PIECE_DEFAULT_IN),
        "corner": str((hoja or {}).get("corner_usado") or "INFERIOR IZQUIERDA"),
        "opt": str((hoja or {}).get("opt_usado") or "OPTIMIZAR LARGO Y ANCHO"),
    }


def listar_placas_desde_resultados(resultados: dict) -> list[dict[str, Any]]:
    """Lista plana de placas madre (misma nomenclatura que lista de nest)."""
    from .efficiency_metrics import formatear_eficiencias_placa
    from .manager import _is_virtual_piece

    out: list[dict[str, Any]] = []
    for clave, grp in (resultados or {}).items():
        if not isinstance(grp, dict):
            continue
        hojas = grp.get("hojas") or []
        # Ãndices P1..Pn por placa_id repetida (igual que tab_nesting).
        for i, hoja in enumerate(hojas):
            if not isinstance(hoja, dict) or hoja.get("es_retazo"):
                continue
            placa_id = str(hoja.get("placa_id") or hoja.get("id") or f"P#{i+1}")
            iguales = [
                j
                for j, h in enumerate(hojas)
                if isinstance(h, dict)
                and not h.get("es_retazo")
                and str(h.get("placa_id") or "") == placa_id
            ]
            sufijo = f" Â· P{iguales.index(i) + 1}" if len(iguales) > 1 else ""
            origen = " (PROVEEDOR)" if hoja.get("origen_placa") == "PROVEEDOR" else ""
            efi_txt = formatear_eficiencias_placa(hoja)
            n = sum(
                1
                for p in (hoja.get("piezas") or [])
                if isinstance(p, dict) and not _is_virtual_piece(str(p.get("nombre") or ""))
            )
            efi = float(hoja.get("eficiencia_directa") or hoja.get("eficiencia") or 0.0)
            out.append(
                {
                    "clave": clave,
                    "hoja_idx": i,
                    "madre_n": (iguales.index(i) + 1) if iguales else 1,
                    "placa_id": placa_id,
                    "n_piezas": n,
                    "efi": efi,
                    "label": f"{placa_id}{sufijo}{origen} | {efi_txt}",
                    "hoja": hoja,
                }
            )
    return out


def piece_from_dxf(
    ruta: str,
    *,
    nombre: str | None = None,
    qty: int = 1,
    calibre: str = "SIM",
    material: str = "A36",
) -> tuple[list[dict], str | None]:
    """Convierte un DXF en piezas listas para el motor (misma normalizaciÃ³n que producciÃ³n)."""
    ruta = os.path.abspath(str(ruta or ""))
    if not os.path.isfile(ruta):
        return [], f"Archivo no encontrado: {ruta}"

    nom_base = str(nombre or os.path.splitext(os.path.basename(ruta))[0]).strip() or "PIEZA"
    poly, marks, err = recuperar_geometria_robusta_detalle(ruta)
    if poly is None:
        return [], err or "No se pudo leer geometrÃ­a del DXF"

    minx, miny, _, _ = poly.bounds
    poly_exact = affinity.translate(poly, -minx, -miny)
    if marks is not None and not marks.is_empty:
        marks_exact = affinity.translate(marks, -minx, -miny)
    else:
        marks_exact = marks

    if poly_exact is None or poly_exact.is_empty:
        return [], "GeometrÃ­a vacÃ­a tras normalizar"

    poly_nesting = _crear_poly_nesting_seguro(poly_exact)
    if poly_nesting is None or poly_nesting.is_empty:
        poly_nesting = poly_exact

    out: list[dict] = []
    n = max(1, int(qty or 1))
    for i in range(n):
        suffix = f"#{i + 1}" if n > 1 else ""
        out.append(
            {
                "nombre": f"{nom_base}{suffix}",
                "poly": poly_exact,
                "marks": marks_exact,
                "area": float(poly_exact.area or 0.0),
                "calibre": str(calibre or "SIM"),
                "material": str(material or "A36"),
                "ruta": ruta,
                "orig_minx": float(minx),
                "orig_miny": float(miny),
                "poly_exact": poly_exact,
                "marks_exact": marks_exact,
                "debug_id": f"sim::{nom_base}::rep{i + 1}",
            }
        )
    return out, None


def build_pieces_from_entries(entries: list[SimPieceEntry]) -> tuple[list[dict], list[str]]:
    piezas: list[dict] = []
    errores: list[str] = []
    for ent in entries or []:
        batch, err = piece_from_dxf(
            ent.ruta,
            nombre=ent.display_name(),
            qty=int(ent.qty or 1),
        )
        if err:
            errores.append(f"{ent.display_name()}: {err}")
            continue
        piezas.extend(batch)
    return piezas, errores


def run_single_sheet_sim(
    piezas: list[dict],
    *,
    w_mm: float,
    h_mm: float,
    kerf_in: float = 0.2,
    margin_in: float = PLATE_TO_PIECE_DEFAULT_IN,
    corner: str = "INFERIOR IZQUIERDA",
    opt: str = "OPTIMIZAR LARGO Y ANCHO",
    mc_iterations: int | None = None,
    nest_mode: str | None = None,
) -> SimRunResult:
    """Ejecuta empaquetar_una_hoja_mc y enriquece el resultado para visualizaciÃ³n."""
    log: list[str] = []
    w_mm = float(w_mm or 0)
    h_mm = float(h_mm or 0)
    if w_mm <= 0 or h_mm <= 0:
        return SimRunResult(ok=False, error="Medidas de placa invÃ¡lidas", log_lines=["ERROR: placa sin dimensiones"])

    if not piezas:
        return SimRunResult(ok=False, error="Sin piezas", log_lines=["ERROR: agrega al menos un DXF"])

    mode = str(nest_mode or os.environ.get("ARGA_NEST_MODE", "standard")).strip().lower()
    if mode in NEST_MODES:
        profile = dict(NEST_MODES[mode])
        log.append(f"Modo nesting: {mode} (mc={profile.get('mc_iterations')})")
    else:
        profile = get_nest_profile()
        log.append(f"Modo nesting: perfil activo (mc={profile.get('mc_iterations')})")

    mc = int(mc_iterations if mc_iterations is not None else profile.get("mc_iterations", 15))
    mc = max(1, min(mc, 50))

    log.append(f"Motor: {engine_name()}")
    log.append(
        f"Placa: {w_mm:.1f} Ã— {h_mm:.1f} mm "
        f"({mm_to_inches(w_mm):.2f}\" Ã— {mm_to_inches(h_mm):.2f}\")"
    )
    log.append(f"Piezas en pool: {len(piezas)} | kerf={kerf_in}\" | margin={margin_in}\"")
    log.append(f"corner={corner} | opt={opt} | mc_iterations={mc}")
    log.append("â€”" * 48)

    t0 = time.perf_counter()
    try:
        hoja, restos = empaquetar_una_hoja_mc(
            piezas,
            w_mm,
            h_mm,
            kerf_override=float(kerf_in),
            margin_override=float(margin_in),
            opt_override=str(opt),
            corner_override=str(corner),
            limite_poly=None,
            mc_iterations=mc,
        )
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000.0
        msg = f"ERROR motor: {exc}"
        log.append(msg)
        return SimRunResult(ok=False, error=str(exc), log_lines=log, elapsed_ms=elapsed)

    elapsed = (time.perf_counter() - t0) * 1000.0

    hoja = hoja or {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0}
    restos = list(restos or [])
    piezas_input = copy.deepcopy(piezas)

    hoja.update(
        {
            "placa_w": w_mm,
            "placa_h": h_mm,
            "kerf_usado": float(kerf_in),
            "margin_usado": float(margin_in),
            "corner_usado": str(corner),
            "opt_usado": str(opt),
            "es_retazo": False,
        }
    )
    enriquecer_piezas_hoja_con_fuentes(hoja, piezas_input)
    hoja = actualizar_eficiencias_hoja(hoja)

    colocados = [p for p in hoja.get("piezas") or [] if not str(p.get("nombre", "")).startswith("REMANENTE")]
    log.append(f"Tiempo: {elapsed:.0f} ms")
    log.append(f"Colocadas: {len(colocados)} / {len(piezas)}")
    log.append(f"Restos: {len(restos)}")
    log.append(f"Ãrea usada: {float(hoja.get('area_usada', 0) or 0):,.0f} mmÂ²")
    log.append(f"Eficiencia: {float(hoja.get('eficiencia_directa') or hoja.get('eficiencia') or 0):.2f}%")

    if restos:
        log.append("â€” piezas sin colocar â€”")
        for p in restos:
            poly = p.get("poly")
            if poly is not None:
                minx, miny, maxx, maxy = poly.bounds
                bw, bh = maxx - minx, maxy - miny
                log.append(
                    f"  Â· {p.get('nombre')} | {bw:.1f}Ã—{bh:.1f} mm | Ã¡rea={float(p.get('area', 0) or 0):,.0f}"
                )
            else:
                log.append(f"  Â· {p.get('nombre')} | sin poly")

    if colocados:
        log.append("â€” piezas colocadas â€”")
        for i, p in enumerate(colocados, 1):
            pols = p.get("poligonos") or []
            if pols:
                xs = [pt[0] for pt in pols[0]]
                ys = [pt[1] for pt in pols[0]]
                bw, bh = max(xs) - min(xs), max(ys) - min(ys)
                log.append(
                    f"  {i:02d}. {p.get('nombre')} | bbox~{bw:.1f}x{bh:.1f} mm en placa"
                )
            else:
                log.append(f"  {i:02d}. {p.get('nombre')}")

    ok = len(restos) == 0 and len(colocados) > 0
    return SimRunResult(
        ok=ok,
        hoja=hoja,
        restos=restos,
        piezas_input=piezas_input,
        log_lines=log,
        elapsed_ms=elapsed,
    )


def scenario_to_dict(
    *,
    plate_w_in: float,
    plate_h_in: float,
    kerf_in: float,
    margin_in: float,
    corner: str,
    opt: str,
    nest_mode: str,
    mc_iterations: int,
    entries: list[SimPieceEntry],
    ref_image: str = "",
    notes: str = "",
) -> dict[str, Any]:
    return {
        "version": 1,
        "plate_w_in": float(plate_w_in),
        "plate_h_in": float(plate_h_in),
        "kerf_in": float(kerf_in),
        "margin_in": float(margin_in),
        "corner": str(corner),
        "opt": str(opt),
        "nest_mode": str(nest_mode),
        "mc_iterations": int(mc_iterations),
        "ref_image": str(ref_image or ""),
        "notes": str(notes or ""),
        "pieces": [
            {
                "ruta": e.ruta,
                "qty": int(e.qty or 1),
                "nombre": e.nombre,
                "ref_image": e.ref_image,
            }
            for e in entries
        ],
    }


def scenario_from_dict(data: dict) -> tuple[dict, list[SimPieceEntry]]:
    entries = []
    for row in data.get("pieces") or []:
        entries.append(
            SimPieceEntry(
                ruta=str(row.get("ruta") or ""),
                qty=int(row.get("qty") or 1),
                nombre=str(row.get("nombre") or ""),
                ref_image=str(row.get("ref_image") or ""),
            )
        )
    params = {
        "plate_w_in": float(data.get("plate_w_in") or 96),
        "plate_h_in": float(data.get("plate_h_in") or 240),
        "kerf_in": float(data.get("kerf_in") or 0.2),
        "margin_in": float(data.get("margin_in") or PLATE_TO_PIECE_DEFAULT_IN),
        "corner": str(data.get("corner") or "INFERIOR IZQUIERDA"),
        "opt": str(data.get("opt") or "OPTIMIZAR LARGO Y ANCHO"),
        "nest_mode": str(data.get("nest_mode") or "standard"),
        "mc_iterations": int(data.get("mc_iterations") or 15),
        "ref_image": str(data.get("ref_image") or ""),
        "notes": str(data.get("notes") or ""),
    }
    return params, entries


def save_scenario_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_scenario_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@dataclass
class SimTimelineResult:
    ok: bool
    hoja: dict | None = None
    restos: list = field(default_factory=list)
    pasos: list = field(default_factory=list)
    orden_piezas: list = field(default_factory=list)
    mc_iteracion_ganadora: int = 0
    mc_orden_modo: str = ""
    w_mm: float = 0.0
    h_mm: float = 0.0
    elapsed_ms: float = 0.0
    error: str = ""
    engine_id: str = ""


def _pieces_to_native(piezas: list[dict]) -> list[dict]:
    from .algorithm_bridge import _piece_to_native

    return [_piece_to_native(p) for p in (piezas or [])]


def nombre_pieza_agrupado(nombre: str) -> str:
    """Quita sufijo #N para que la tabla agrupe Cant. como el nest real."""
    import re

    s = str(nombre or "").strip()
    base = re.sub(r"#\d+\s*$", "", s).strip()
    return base or s


def hoja_con_nombres_agrupados(hoja: dict | None) -> dict | None:
    if not isinstance(hoja, dict):
        return hoja
    out = copy.deepcopy(hoja)
    for p in out.get("piezas") or []:
        if isinstance(p, dict):
            p["nombre"] = nombre_pieza_agrupado(p.get("nombre"))
    return out


def _centroid_pieza(pieza: dict) -> tuple[float, float]:
    pols = (pieza or {}).get("poligonos") or []
    if not pols or not pols[0]:
        return 0.0, 0.0
    xs = [float(pt[0]) for pt in pols[0] if isinstance(pt, (list, tuple)) and len(pt) >= 2]
    ys = [float(pt[1]) for pt in pols[0] if isinstance(pt, (list, tuple)) and len(pt) >= 2]
    if not xs or not ys:
        return 0.0, 0.0
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _bbox_pieza_mm(pieza: dict) -> tuple[float, float]:
    pols = (pieza or {}).get("poligonos") or []
    if not pols or not pols[0]:
        return 0.0, 0.0
    xs = [float(pt[0]) for pt in pols[0] if isinstance(pt, (list, tuple)) and len(pt) >= 2]
    ys = [float(pt[1]) for pt in pols[0] if isinstance(pt, (list, tuple)) and len(pt) >= 2]
    if not xs or not ys:
        return 0.0, 0.0
    return max(xs) - min(xs), max(ys) - min(ys)


def timeline_sintetica_desde_hoja(
    hoja: dict,
    restos: list,
    *,
    w_mm: float,
    h_mm: float,
    elapsed_ms: float = 0.0,
    engine_id: str = "",
    error: str = "",
) -> SimTimelineResult:
    """Construye pasos 1..N a partir del nest final (motores sin API timeline)."""
    hoja = dict(hoja or {})
    hoja.setdefault("placa_w", w_mm)
    hoja.setdefault("placa_h", h_mm)
    hoja.setdefault("es_retazo", False)
    actualizar_eficiencias_hoja(hoja)
    pasos: list[dict] = []
    for p in hoja.get("piezas") or []:
        if not isinstance(p, dict):
            continue
        px, py = _centroid_pieza(p)
        bw, bh = _bbox_pieza_mm(p)
        pasos.append(
            {
                "colocada": True,
                "nombre": str(p.get("nombre") or ""),
                "pieza": copy.deepcopy(p),
                "px": px,
                "py": py,
                "rotacion_grados": float(p.get("rotacion") or p.get("rotation") or 0),
                "categoria": str(p.get("categoria") or "piece"),
                "estrategia": str(engine_id or "pack"),
                "score": float(p.get("area") or 0),
                "bbox_w_mm": bw,
                "bbox_h_mm": bh,
                "variaciones_evaluadas": 0,
            }
        )
    colocadas = len(pasos)
    ok = colocadas > 0 and len(restos or []) == 0 and not error
    return SimTimelineResult(
        ok=ok,
        hoja=hoja,
        restos=list(restos or []),
        pasos=pasos,
        orden_piezas=[str(p.get("nombre") or "") for p in pasos],
        w_mm=float(w_mm),
        h_mm=float(h_mm),
        elapsed_ms=float(elapsed_ms),
        error=str(error or ""),
        engine_id=str(engine_id or ""),
    )


def _serialize_pieza_pack(p: dict) -> dict:
    from .algorithm_bridge import _piece_to_native

    native = _piece_to_native(p)
    native["ruta"] = str(p.get("ruta") or "")
    return native


def _deserialize_pieza_pack(native: dict) -> dict:
    from shapely.geometry import LineString, MultiLineString, Polygon

    rings = list(native.get("rings") or [])
    poly = None
    if rings:
        exterior = [(float(x), float(y)) for x, y in rings[0]]
        holes = [[(float(x), float(y)) for x, y in r] for r in rings[1:]]
        try:
            poly = Polygon(exterior, holes)
        except Exception:
            poly = Polygon(exterior)

    marks = None
    mark_rings = list(native.get("marks") or [])
    lines = []
    for ring in mark_rings:
        pts = [(float(x), float(y)) for x, y in (ring or [])]
        if len(pts) >= 2:
            lines.append(LineString(pts))
    if len(lines) == 1:
        marks = lines[0]
    elif len(lines) > 1:
        marks = MultiLineString(lines)

    return {
        "nombre": str(native.get("nombre") or ""),
        "area": float(native.get("area") or (poly.area if poly is not None else 0.0)),
        "calibre": str(native.get("calibre") or ""),
        "material": str(native.get("material") or ""),
        "ruta": str(native.get("ruta") or ""),
        "poly": poly,
        "marks": marks,
    }


def _timeline_to_dict(tl: SimTimelineResult) -> dict:
    return {
        "ok": bool(tl.ok),
        "hoja": tl.hoja,
        "restos_nombres": [str(p.get("nombre") or "") for p in (tl.restos or []) if isinstance(p, dict)],
        "pasos": tl.pasos,
        "orden_piezas": list(tl.orden_piezas or []),
        "mc_iteracion_ganadora": int(tl.mc_iteracion_ganadora or 0),
        "mc_orden_modo": str(tl.mc_orden_modo or ""),
        "w_mm": float(tl.w_mm or 0),
        "h_mm": float(tl.h_mm or 0),
        "elapsed_ms": float(tl.elapsed_ms or 0),
        "error": str(tl.error or ""),
        "engine_id": str(tl.engine_id or ""),
    }


def _timeline_from_dict(data: dict, piezas_orig: list[dict] | None = None) -> SimTimelineResult:
    restos: list = []
    if piezas_orig:
        placed = Counter(
            str(p.get("nombre") or "") for p in ((data.get("hoja") or {}).get("piezas") or [])
        )
        for p in piezas_orig:
            nom = str(p.get("nombre") or "")
            if placed.get(nom, 0) > 0:
                placed[nom] -= 1
            else:
                restos.append(copy.deepcopy(p))
    return SimTimelineResult(
        ok=bool(data.get("ok")),
        hoja=data.get("hoja"),
        restos=restos,
        pasos=list(data.get("pasos") or []),
        orden_piezas=list(data.get("orden_piezas") or []),
        mc_iteracion_ganadora=int(data.get("mc_iteracion_ganadora") or 0),
        mc_orden_modo=str(data.get("mc_orden_modo") or ""),
        w_mm=float(data.get("w_mm") or 0),
        h_mm=float(data.get("h_mm") or 0),
        elapsed_ms=float(data.get("elapsed_ms") or 0),
        error=str(data.get("error") or ""),
        engine_id=str(data.get("engine_id") or ""),
    )


def _plate_sim_process_entry(payload: dict) -> dict:
    """Entry point multiproceso (spawn): no congela el GIL del proceso UI."""
    root = str(payload.get("root") or "")
    if root and root not in sys.path:
        sys.path.insert(0, root)
    try:
        from modules.win_dll_bootstrap import bootstrap_proceso_nesting

        bootstrap_proceso_nesting()
    except Exception:
        pass

    piezas = [_deserialize_pieza_pack(p) for p in (payload.get("piezas") or [])]
    tl = run_plate_sim(
        piezas,
        w_mm=float(payload.get("w_mm") or 0),
        h_mm=float(payload.get("h_mm") or 0),
        kerf_in=float(payload.get("kerf_in") or 0.2),
        margin_in=float(payload.get("margin_in") or PLATE_TO_PIECE_DEFAULT_IN),
        corner=str(payload.get("corner") or "INFERIOR IZQUIERDA"),
        opt=str(payload.get("opt") or "OPTIMIZAR LARGO Y ANCHO"),
        mc_iterations=int(payload.get("mc_iterations") or 1),
        engine_id=str(payload.get("engine_id") or "arga_base"),
        isolate_process=False,
    )
    out_path = str(payload.get("out_path") or "")
    data = _timeline_to_dict(tl)
    if out_path:
        import pickle

        with open(out_path, "wb") as f:
            pickle.dump(("ok", data), f, protocol=pickle.HIGHEST_PROTOCOL)
        return {"written": out_path}
    return data


def _plate_sim_process_queue_entry(payload: dict, queue=None) -> None:
    """Worker spawn: escribe resultado a archivo (evita colgar Queue con payloads grandes)."""
    out_path = str(payload.get("out_path") or "")
    try:
        data = _plate_sim_process_entry(payload)
        if out_path and isinstance(data, dict) and data.get("written"):
            return
        if out_path:
            import pickle

            with open(out_path, "wb") as f:
                pickle.dump(("ok", data), f, protocol=pickle.HIGHEST_PROTOCOL)
            return
        if queue is not None:
            queue.put(("ok", data))
    except Exception as exc:  # pragma: no cover
        if out_path:
            try:
                import pickle

                with open(out_path, "wb") as f:
                    pickle.dump(("err", str(exc)), f, protocol=pickle.HIGHEST_PROTOCOL)
            except Exception:
                pass
        elif queue is not None:
            queue.put(("err", str(exc)))


def run_plate_sim(
    piezas: list[dict],
    *,
    w_mm: float,
    h_mm: float,
    kerf_in: float = 0.2,
    margin_in: float = PLATE_TO_PIECE_DEFAULT_IN,
    corner: str = "INFERIOR IZQUIERDA",
    opt: str = "OPTIMIZAR LARGO Y ANCHO",
    mc_iterations: int = 1,
    engine_id: str = "arga_base",
    isolate_process: bool | None = None,
    timeout_s: float = 600.0,
    cancel_checker=None,
) -> SimTimelineResult:
    """Empaqueta una placa con el motor elegido; timeline nativa o sintÃ©tica.

    isolate_process:
      - None (default): proceso aparte solo en motores lentos (burke/libnest/svgnest).
      - True/False: forzar.
    """
    from .engines.types import PackSheetRequest
    from .engine_registry import empaquetar_una_hoja_detalle, is_engine_ready
    from .nest_engine_context import (
        normalize_engine_id,
        reset_active_engine_id,
        set_active_engine_id,
    )

    eid = normalize_engine_id(engine_id)
    w_mm = float(w_mm or 0)
    h_mm = float(h_mm or 0)
    if w_mm <= 0 or h_mm <= 0 or not piezas:
        return SimTimelineResult(ok=False, error="Parametros invalidos", engine_id=eid)

    if isolate_process is None:
        # ARGA Base debe ser rÃ¡pido en-hilo (~s). Proceso aparte solo para NFP/GA.
        # Con cancel_checker (NestFab continuo) SIEMPRE en-hilo.
        if cancel_checker is not None:
            isolate_process = False
        else:
            isolate_process = eid in ("burke_blf", "libnest2d", "svgnest_ultra")

    if isolate_process and cancel_checker is not None:
        isolate_process = False

    if isolate_process:
        import multiprocessing as mp
        import pickle
        import tempfile

        fd, out_path = tempfile.mkstemp(prefix="arga_lab_sim_", suffix=".pkl")
        os.close(fd)
        payload = {
            "root": _repo_root(),
            "piezas": [_serialize_pieza_pack(p) for p in piezas],
            "w_mm": w_mm,
            "h_mm": h_mm,
            "kerf_in": float(kerf_in),
            "margin_in": float(margin_in),
            "corner": str(corner),
            "opt": str(opt),
            "mc_iterations": max(1, int(mc_iterations or 1)),
            "engine_id": eid,
            "out_path": out_path,
        }
        ctx = mp.get_context("spawn")
        proc = ctx.Process(target=_plate_sim_process_queue_entry, args=(payload, None))
        try:
            proc.start()
            proc.join(timeout=float(timeout_s or 600.0))
            if proc.is_alive():
                proc.terminate()
                proc.join(5)
                return SimTimelineResult(
                    ok=False,
                    error=f"Timeout motor '{eid}' ({timeout_s:.0f}s).",
                    engine_id=eid,
                    w_mm=w_mm,
                    h_mm=h_mm,
                )
            if not os.path.isfile(out_path) or os.path.getsize(out_path) <= 0:
                return SimTimelineResult(
                    ok=False,
                    error=f"Proceso motor '{eid}' terminÃ³ sin resultado.",
                    engine_id=eid,
                    w_mm=w_mm,
                    h_mm=h_mm,
                )
            with open(out_path, "rb") as f:
                status, data = pickle.load(f)
            if status != "ok":
                return SimTimelineResult(
                    ok=False,
                    error=str(data),
                    engine_id=eid,
                    w_mm=w_mm,
                    h_mm=h_mm,
                )
            return _timeline_from_dict(data, piezas)
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass

    if not is_engine_ready(eid):
        return SimTimelineResult(
            ok=False,
            error=f"Motor '{eid}' no estÃ¡ listo / no compilado.",
            engine_id=eid,
            w_mm=w_mm,
            h_mm=h_mm,
        )

    # ARGA Base de producciÃ³n = empaquetar_una_hoja_base (cavidades/pasillos).
    # NO usar empaquetar_una_hoja_timeline: es un packer legacy distinto y deja
    # los huecos de VFM vacÃ­os aunque packer_base ya los rellene.
    token = set_active_engine_id(eid)
    t0 = time.perf_counter()
    try:
        result = empaquetar_una_hoja_detalle(
            PackSheetRequest(
                piezas=piezas,
                w_placa=w_mm,
                h_placa=h_mm,
                kerf_override=float(kerf_in),
                margin_override=float(margin_in),
                opt_override=str(opt),
                corner_override=str(corner),
                mc_iterations=max(1, int(mc_iterations or 1)),
                cancel_checker=cancel_checker,
            ),
            engine_id=eid,
        )
    except Exception as exc:
        reset_active_engine_id(token)
        return SimTimelineResult(
            ok=False,
            error=str(exc),
            engine_id=eid,
            w_mm=w_mm,
            h_mm=h_mm,
            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
        )
    reset_active_engine_id(token)
    elapsed = (time.perf_counter() - t0) * 1000.0
    hoja = dict(result.hoja or {})
    hoja.update(
        {
            "placa_w": w_mm,
            "placa_h": h_mm,
            "kerf_usado": float(kerf_in),
            "margin_usado": float(margin_in),
            "corner_usado": str(corner),
            "opt_usado": str(opt),
            "es_retazo": False,
        }
    )
    return timeline_sintetica_desde_hoja(
        hoja,
        list(result.restos or []),
        w_mm=w_mm,
        h_mm=h_mm,
        elapsed_ms=elapsed,
        engine_id=eid,
        error=str(result.error or ""),
    )


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _use_lab_engine() -> bool:
    return str(os.environ.get("ARGA_NEST_LAB", "")).strip().lower() in ("1", "true", "yes", "lab")


def _load_lab_bridge_module():
    lab_engine = os.path.join(_repo_root(), "LAB SIMULATOR", "engine")
    if lab_engine not in sys.path:
        sys.path.insert(0, lab_engine)
    from bridge import load_lab_cpp

    return load_lab_cpp()


def _load_algorithm_cpp_module():
    import importlib.util

    root = os.path.dirname(os.path.abspath(__file__))
    candidates: list[str] = []
    for name in (
        "algorithm_cpp.cp314-win_amd64.pyd",
        "algorithm_cpp.cp313-win_amd64.pyd",
        "algorithm_cpp.pyd",
    ):
        for folder in (
            os.path.join(root, "cpp", "build", "Release"),
            os.path.join(root, "cpp", "build"),
            root,
        ):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                candidates.append(path)

    def _load_path(path: str):
        spec = importlib.util.spec_from_file_location("algorithm_cpp", path)
        if not spec or not spec.loader:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    for path in candidates:
        if "build" in path.replace("\\", "/"):
            mod = _load_path(path)
            if mod is not None and hasattr(mod, "empaquetar_una_hoja_timeline"):
                return mod

    try:
        from . import algorithm_cpp as mod

        if hasattr(mod, "empaquetar_una_hoja_timeline"):
            return mod
    except ImportError:
        pass

    for path in candidates:
        mod = _load_path(path)
        if mod is not None:
            return mod

    raise ImportError("algorithm_cpp no disponible (compila build_cpp_engine.ps1)")


def _load_timeline_cpp_module():
    if _use_lab_engine():
        return _load_lab_bridge_module()
    return _load_algorithm_cpp_module()


def run_timeline_sim(
    piezas: list[dict],
    *,
    w_mm: float,
    h_mm: float,
    kerf_in: float = 0.2,
    margin_in: float = PLATE_TO_PIECE_DEFAULT_IN,
    corner: str = "INFERIOR IZQUIERDA",
    opt: str = "OPTIMIZAR LARGO Y ANCHO",
    mc_iterations: int = 1,
) -> SimTimelineResult:
    """Empaqueta y devuelve paso a paso (orden del pool ganador en MC)."""
    w_mm = float(w_mm or 0)
    h_mm = float(h_mm or 0)
    if w_mm <= 0 or h_mm <= 0 or not piezas:
        return SimTimelineResult(ok=False, error="Parametros invalidos")

    mc = max(1, min(int(mc_iterations or 1), 50))
    t0 = time.perf_counter()
    try:
        algorithm_cpp = _load_timeline_cpp_module()

        native = _pieces_to_native(piezas)
        raw = algorithm_cpp.empaquetar_una_hoja_timeline(
            native,
            w_mm,
            h_mm,
            float(kerf_in),
            float(margin_in),
            str(opt),
            str(corner),
            None,
            mc,
        )
    except Exception as exc:
        return SimTimelineResult(ok=False, error=str(exc), elapsed_ms=(time.perf_counter() - t0) * 1000.0)

    elapsed = (time.perf_counter() - t0) * 1000.0
    hoja = dict(raw.get("hoja") or {})
    restos = list(raw.get("restos") or [])
    pasos = list(raw.get("pasos") or [])
    colocadas = len(hoja.get("piezas") or [])
    ok = len(restos) == 0 and colocadas > 0

    hoja.update(
        {
            "placa_w": w_mm,
            "placa_h": h_mm,
            "kerf_usado": float(kerf_in),
            "margin_usado": float(margin_in),
            "corner_usado": str(corner),
            "opt_usado": str(opt),
            "es_retazo": False,
        }
    )
    return SimTimelineResult(
        ok=ok,
        hoja=hoja,
        restos=restos,
        pasos=pasos,
        orden_piezas=list(raw.get("orden_piezas") or []),
        mc_iteracion_ganadora=int(raw.get("mc_iteracion_ganadora") or 0),
        mc_orden_modo=str(raw.get("mc_orden_modo") or ""),
        w_mm=w_mm,
        h_mm=h_mm,
        elapsed_ms=elapsed,
    )


def hoja_en_paso_timeline(timeline: SimTimelineResult, paso_idx: int) -> tuple[dict, int | None]:
    """
    Construye hoja parcial hasta paso_idx (0 = placa vacia).
    Devuelve (hoja, indice_pieza_resaltada o None).
    """
    piezas: list[dict] = []
    highlight: int | None = None
    pasos = timeline.pasos or []
    lim = max(0, min(int(paso_idx), len(pasos)))
    for i in range(lim):
        paso = pasos[i]
        if paso.get("colocada") and paso.get("pieza"):
            piezas.append(dict(paso["pieza"]))
            if i == lim - 1:
                highlight = len(piezas) - 1
    area = sum(float(p.get("area") or 0) for p in piezas)
    denom = float(timeline.w_mm or 1) * float(timeline.h_mm or 1)
    efi = (area / denom * 100.0) if denom > 0 else 0.0
    hoja = {
        "placa_w": timeline.w_mm,
        "placa_h": timeline.h_mm,
        "piezas": piezas,
        "area_usada": area,
        "eficiencia": efi,
        "eficiencia_directa": efi,
        "es_retazo": False,
    }
    return hoja, highlight


def texto_paso_timeline(paso: dict, *, paso_idx: int, total: int, qty_mismo: int | None = None) -> str:
    nom = str(paso.get("nombre") or "")
    qty_txt = f" Ã—{qty_mismo}" if qty_mismo and qty_mismo > 1 else ""
    if not paso.get("colocada"):
        return (
            f"Paso {paso_idx}/{total}: intento '{nom}'{qty_txt} â€” NO cabe "
            f"({paso.get('variaciones_evaluadas', 0)} rotaciones probadas)"
        )
    px = float(paso.get("px") or 0)
    py = float(paso.get("py") or 0)
    return (
        f"Paso {paso_idx}/{total}: COLOCA '{nom}'{qty_txt} | "
        f"cat={paso.get('categoria')} | estrategia={paso.get('estrategia') or 'n/a'} | "
        f"rot={paso.get('rotacion_grados')}Â° | "
        f"pos=({px:.1f}, {py:.1f}) mm | score={float(paso.get('score') or 0):.2f} | "
        f"bbox={float(paso.get('bbox_w_mm') or 0):.1f}x{float(paso.get('bbox_h_mm') or 0):.1f} mm"
    )
