"""Laboratorio de simulación de nesting en una sola placa (sin inventario multi-hoja)."""
from __future__ import annotations

import copy
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from shapely import affinity

from .algorithm_bridge import empaquetar_una_hoja_mc, engine_name
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


def piece_from_dxf(
    ruta: str,
    *,
    nombre: str | None = None,
    qty: int = 1,
    calibre: str = "SIM",
    material: str = "A36",
) -> tuple[list[dict], str | None]:
    """Convierte un DXF en piezas listas para el motor (misma normalización que producción)."""
    ruta = os.path.abspath(str(ruta or ""))
    if not os.path.isfile(ruta):
        return [], f"Archivo no encontrado: {ruta}"

    nom_base = str(nombre or os.path.splitext(os.path.basename(ruta))[0]).strip() or "PIEZA"
    poly, marks, err = recuperar_geometria_robusta_detalle(ruta)
    if poly is None:
        return [], err or "No se pudo leer geometría del DXF"

    minx, miny, _, _ = poly.bounds
    poly_exact = affinity.translate(poly, -minx, -miny)
    if marks is not None and not marks.is_empty:
        marks_exact = affinity.translate(marks, -minx, -miny)
    else:
        marks_exact = marks

    if poly_exact is None or poly_exact.is_empty:
        return [], "Geometría vacía tras normalizar"

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
    margin_in: float = 0.15,
    corner: str = "INFERIOR IZQUIERDA",
    opt: str = "OPTIMIZAR LARGO Y ANCHO",
    mc_iterations: int | None = None,
    nest_mode: str | None = None,
) -> SimRunResult:
    """Ejecuta empaquetar_una_hoja_mc y enriquece el resultado para visualización."""
    log: list[str] = []
    w_mm = float(w_mm or 0)
    h_mm = float(h_mm or 0)
    if w_mm <= 0 or h_mm <= 0:
        return SimRunResult(ok=False, error="Medidas de placa inválidas", log_lines=["ERROR: placa sin dimensiones"])

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
        f"Placa: {w_mm:.1f} × {h_mm:.1f} mm "
        f"({mm_to_inches(w_mm):.2f}\" × {mm_to_inches(h_mm):.2f}\")"
    )
    log.append(f"Piezas en pool: {len(piezas)} | kerf={kerf_in}\" | margin={margin_in}\"")
    log.append(f"corner={corner} | opt={opt} | mc_iterations={mc}")
    log.append("—" * 48)

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
    log.append(f"Área usada: {float(hoja.get('area_usada', 0) or 0):,.0f} mm²")
    log.append(f"Eficiencia: {float(hoja.get('eficiencia_directa') or hoja.get('eficiencia') or 0):.2f}%")

    if restos:
        log.append("— piezas sin colocar —")
        for p in restos:
            poly = p.get("poly")
            if poly is not None:
                minx, miny, maxx, maxy = poly.bounds
                bw, bh = maxx - minx, maxy - miny
                log.append(
                    f"  · {p.get('nombre')} | {bw:.1f}×{bh:.1f} mm | área={float(p.get('area', 0) or 0):,.0f}"
                )
            else:
                log.append(f"  · {p.get('nombre')} | sin poly")

    if colocados:
        log.append("— piezas colocadas —")
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
        "margin_in": float(data.get("margin_in") or 0.15),
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


def _pieces_to_native(piezas: list[dict]) -> list[dict]:
    from .algorithm_bridge import _piece_to_native

    return [_piece_to_native(p) for p in (piezas or [])]


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
    margin_in: float = 0.15,
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


def texto_paso_timeline(paso: dict, *, paso_idx: int, total: int) -> str:
    nom = str(paso.get("nombre") or "")
    if not paso.get("colocada"):
        return (
            f"Paso {paso_idx}/{total}: intento '{nom}' — NO cabe "
            f"({paso.get('variaciones_evaluadas', 0)} rotaciones probadas)"
        )
    px = float(paso.get("px") or 0)
    py = float(paso.get("py") or 0)
    return (
        f"Paso {paso_idx}/{total}: COLOCA '{nom}' | "
        f"cat={paso.get('categoria')} | estrategia={paso.get('estrategia') or 'n/a'} | "
        f"rot={paso.get('rotacion_grados')}° | "
        f"pos=({px:.1f}, {py:.1f}) mm | score={float(paso.get('score') or 0):.2f} | "
        f"bbox={float(paso.get('bbox_w_mm') or 0):.1f}x{float(paso.get('bbox_h_mm') or 0):.1f} mm"
    )
