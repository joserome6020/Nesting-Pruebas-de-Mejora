import concurrent.futures
import multiprocessing
import random
import threading
import copy
import re
import time
import os
from collections import Counter
from contextlib import contextmanager
from datetime import datetime

from modules.plate_stock import stock_permite_nesting
from shapely.geometry import box, Polygon, LineString
from shapely import affinity
from shapely.ops import unary_union
from shapely.prepared import prep

try:
    from interface.utils_nesting import (
        clave_nesting_sort_key as _orden_clave_nesting,
        clave_orientacion_cobre_ruta,
        es_material_cobre,
    )
except ImportError:
    def _orden_clave_nesting(clave: str) -> tuple:
        return (str(clave or "").upper(),)

    def es_material_cobre(material) -> bool:
        m = str(material or "").strip().upper()
        return m in ("CU", "COBRE", "COPPER") or "COBRE" in m or "COPPER" in m

    def clave_orientacion_cobre_ruta(ruta) -> str:
        return os.path.normcase(os.path.normpath(str(ruta or "")))

from .geometry_parser import (
    recuperar_geometria_robusta,
    recuperar_geometria_robusta_detalle,
    reconstruir_poly_seguro,
    reconstruir_marks,
    generar_texto_vectorial,
    poligonos_desde_shapely,
    interiores_poly,
)
from .algorithm_bridge import empaquetar_una_hoja_mc, engine_name as nesting_engine_name
from .engine_registry import list_engine_metas, is_engine_ready
from .cut_gaps_table import CutGapTableError, gaps_for_calibre
from .nest_engine_context import (
    ENGINE_ARGA_FORCE,
    ENGINE_ARGA_LITE,
    ENGINE_SVGNEST_ULTRA,
    get_active_engine_id,
    normalize_engine_id,
    set_active_engine_id,
)


def _es_motor_arga_force(engine_id=None) -> bool:
    """ARGA FORCE (alias legacy arga_base ya normalizado a arga_force)."""
    eid = normalize_engine_id(engine_id if engine_id is not None else get_active_engine_id())
    return eid == ENGINE_ARGA_FORCE


def _usar_pack_combinado_grupo(pendientes_est, accesorios) -> bool:
    """
    Empacar estructurales+accesorios en la misma llamada C++.
    FORCE siempre; Lite/otros cuando Compact-lite está ON (evita P1 vacía + P2 floja).
    """
    if not (pendientes_est and accesorios):
        return False
    try:
        from .giga_cal11_galv import should_force_giga_engine

        if should_force_giga_engine():
            return True
    except Exception:
        pass
    if _es_motor_arga_force():
        return True
    try:
        from .compact_lite import compact_enabled

        return bool(compact_enabled())
    except Exception:
        return False


def _clave_es_cobre(clave) -> bool:
    """True si la clave de grupo es cobre (independiente del motor de acero)."""
    s = str(clave or "").strip().upper()
    if not s:
        return False
    if s.endswith("_CU") or s.endswith("|CU") or "| CU" in s:
        return True
    if "_" in s:
        mat = s.split("_", 1)[1]
        return bool(es_material_cobre(mat))
    return bool(es_material_cobre(s))


def _early_exit_sim_placa_activo() -> bool:
    """Early-exit de candidatas: FORCE/LITE; Ultra en Selección Auto."""
    eid = normalize_engine_id(get_active_engine_id())
    if eid in (ENGINE_ARGA_FORCE, ENGINE_ARGA_LITE):
        return True
    if eid == ENGINE_SVGNEST_ULTRA:
        # Manual Ultra fija formatos; Auto deja _plate_formats_allowed = None.
        return True
    return False


def _plate_sel_parallel_workers() -> int:
    """Workers para SIM multi-formato. 1 = secuencial (default producción).

    Activar evidencia/candidata: ARGA_PLATE_SEL_PARALLEL=1
    """
    raw = str(os.environ.get("ARGA_PLATE_SEL_PARALLEL", "") or "").strip().lower()
    if raw not in ("1", "true", "yes", "on"):
        return 1
    try:
        from .nest_hardware import hardware_nest_budget

        n = int(hardware_nest_budget().get("plate_pool_workers") or 1)
    except Exception:
        n = 2
    return max(1, min(6, n))


def _plate_sel_fast_rank_activo() -> bool:
    """Ranking con Force 1 semilla + re-nest final de la ganadora.

    ARGA_PLATE_SEL_FAST=1 (evidencia). No cambia política de score; acelera SIM.
    """
    raw = str(os.environ.get("ARGA_PLATE_SEL_FAST", "") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


@contextmanager
def _force_seeds_override(n_seeds: int):
    prev = os.environ.get("ARGA_FORCE_SEEDS")
    os.environ["ARGA_FORCE_SEEDS"] = str(max(1, int(n_seeds)))
    try:
        from .nest_hardware import hardware_nest_budget

        hardware_nest_budget.cache_clear()
    except Exception:
        pass
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("ARGA_FORCE_SEEDS", None)
        else:
            os.environ["ARGA_FORCE_SEEDS"] = prev
        try:
            from .nest_hardware import hardware_nest_budget

            hardware_nest_budget.cache_clear()
        except Exception:
            pass

# Cancelación NestFab-like visible en helpers de módulo (mismo hilo / mismo proceso).
_CANCEL_TLS = threading.local()


def _bind_pack_cancel_checker(fn):
    prev = getattr(_CANCEL_TLS, "fn", None)
    _CANCEL_TLS.fn = fn
    return prev


def _unbind_pack_cancel_checker(prev):
    _CANCEL_TLS.fn = prev


def _active_pack_cancel_checker():
    return getattr(_CANCEL_TLS, "fn", None)
from .efficiency_metrics import (
    actualizar_eficiencias_hoja,
    calcular_eficiencias_grupo,
    nombre_rtz_para_placa,
)
from .nest_optimization import (
    get_engine_profile,
    get_nest_profile,
    score_placa_lower_bound,
    score_placa_simulacion,
)
from .plate_selection_probe import record_probe
from .exporter import exportar_resultados_a_dxf
from .cu_largos_nesting import procesar_grupo_largos_cu
from .cu_inventory import (
    inventario_barras_largos_cu,
    validar_inventario_cu_resultado,
)
from .rtz_overlays import (
    sincronizar_overlays_grupo,
    sincronizar_overlays_resultados,
    _rtz_hojas_de_madre,
    _inferir_global_rtz,
    _translate_poligonos_for_overlay,
)

DEBUG_DIR = r"C:\NEST_EXPORTS"
DEBUG_LOG_NESTING = os.path.join(DEBUG_DIR, "nesting_debug_geometry.txt")
DEFAULT_KERF_IN = 0.15
# Placa→pieza es una constante de planta (foto TABLA GAPS DE CORTE = 0.250").
# Todo motor debe usar este valor cuando no se le pase override explícito, para
# que el fallback no baje el margen a 0.15" (bug histórico previo a la tabla).
from .cut_gaps_table import PLATE_TO_PIECE_DEFAULT_IN  # noqa: E402
DEFAULT_MARGIN_IN = PLATE_TO_PIECE_DEFAULT_IN
# Exacto: mismo calibre / redondeo (0.1046 ↔ 0.105). NO cruza 11↔12.
THICKNESS_EXACT_ABS_IN = 0.005
# DESACTIVADO: la tolerancia % provocaba usar cal. 11 por 12 y reclasificar grupos.
# Se deja en 0 por compat; el clasificador ya NO usa fallback de tolerancia.
THICKNESS_TOLERANCE_PCT = 0.0
SLIDE_STEP_MM = 4.0
TRANSFER_GRID_STEP_MM = 12.0
TRANSFER_ROTATIONS = (0, 90, 180, 270)


def _dbg_nesting(msg: str):
    try:
        # MATCH-OK inunda I/O en jobs grandes; solo con ARGA_NEST_VERBOSE=1.
        if msg.startswith("[MATCH-OK") or msg.startswith("[MATCH-FALLBACK"):
            if str(os.environ.get("ARGA_NEST_VERBOSE", "")).strip() not in (
                "1",
                "true",
                "TRUE",
                "yes",
                "YES",
            ):
                return
        os.makedirs(DEBUG_DIR, exist_ok=True)
        linea = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(linea)
        with open(DEBUG_LOG_NESTING, "a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception:
        pass


def _plate_format_key_mm(w_mm: float, h_mm: float) -> str:
    w_in = round(float(w_mm) / 25.4, 3)
    h_in = round(float(h_mm) / 25.4, 3)
    a, b = sorted((w_in, h_in))
    return f"{a:.3f}x{b:.3f}"


def _parse_plate_selection(selection: dict | None) -> tuple[set[str] | None, dict[str, int] | None]:
    """
    Retorna (formatos_permitidos|None, limites_qty|None).
    limites_qty siempre None: el nest no corta por cantidad de hojas.
    formats_allowed solo restringe tamaños si mode=manual.
    """
    if not selection or selection.get("mode") == "auto":
        return None, None
    items = selection.get("items") or []
    if not items:
        return None, None
    allowed: set[str] = set()
    for item in items:
        key = str(item.get("key") or "").strip()
        if not key:
            try:
                w_in = float(item.get("w_in") or 0)
                h_in = float(item.get("h_in") or 0)
                a, b = sorted((round(w_in, 3), round(h_in, 3)))
                key = f"{a:.3f}x{b:.3f}"
            except Exception:
                continue
        allowed.add(key)
    return (allowed or None), None


def _fmt_bounds(poly):
    try:
        minx, miny, maxx, maxy = poly.bounds
        return f"({minx:.3f}, {miny:.3f}) -> ({maxx:.3f}, {maxy:.3f}) | w={maxx-minx:.3f} | h={maxy-miny:.3f}"
    except Exception:
        return "SIN_BOUNDS"


def _safe_geom_type(g):
    try:
        return g.geom_type
    except Exception:
        return "UNKNOWN"


def _safe_area(g):
    try:
        return float(g.area)
    except Exception:
        return 0.0


def _safe_is_valid(g):
    try:
        return bool(g.is_valid)
    except Exception:
        return False


def _safe_holes(poly):
    try:
        return len(poly.interiors)
    except Exception:
        return 0


def _safe_marks_info(marks):
    try:
        if marks is None:
            return "marks=None"
        if marks.is_empty:
            return "marks=EMPTY"
        if hasattr(marks, "geom_type"):
            if marks.geom_type == "LineString":
                return "marks=LineString(1)"
            if marks.geom_type == "MultiLineString":
                return f"marks=MultiLineString({len(list(marks.geoms))})"
        return f"marks={getattr(marks, 'geom_type', 'UNKNOWN')}"
    except Exception:
        return "marks=ERROR"

from .sheet_integrity import _piece_name_base
def _is_virtual_piece(nombre: str) -> bool:
    n = str(nombre or "")
    return (
        n.startswith("REF__")
        or n.startswith("TATUAJE__")
        or n.startswith("RETAZO_GUILLOTINA__")
        or n.startswith("CU_CORTE__")
        or n.startswith("REMANENTE__")
    )


def _rebuild_marks_geom(marks_paths):
    lineas = []
    for mk in (marks_paths or []):
        try:
            if mk and len(mk) >= 2:
                ls = LineString(mk)
                if not ls.is_empty and ls.length > 0:
                    lineas.append(ls)
        except Exception:
            pass

    if not lineas:
        return None

    try:
        return unary_union(lineas)
    except Exception:
        return lineas[0]


def _safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def _is_gauge_range_half_to_2(raw_thk, thk_val):
    # Nueva regla: rango 1/2" a 2"
    if thk_val is None:
        return False
    # Evita interpretar calibres enteros tipo 16, 14, etc. como válidos para 12KW.
    if thk_val > 2.0:
        return False
    return 0.5 <= thk_val <= 2.0


def _debe_forzar_sin_mini_nest(req_cal: str, placa_w_mm: float, placa_h_mm: float) -> bool:
    """
    Reglas de negocio solicitadas:
      1) CAMA LASER 12 KW SIN MINI NEST:
         rango 1/2 a 2 y placa <= 60x120 in
      2) CAMA LASER SIN MINI NEST:
         espesor <= 3/8 in y largo <= 120 in
    Si cumple cualquiera, NO se deben generar hojas RTZ.
    """
    raw = str(req_cal or "").strip()
    thk_val = _safe_float(raw)
    w_in = min(float(placa_w_mm) / 25.4, float(placa_h_mm) / 25.4)
    l_in = max(float(placa_w_mm) / 25.4, float(placa_h_mm) / 25.4)

    regla_12kw_sin_mini = _is_gauge_range_half_to_2(raw, thk_val) and w_in <= 60.0 and l_in <= 120.0
    regla_cama_sin_mini = (thk_val is not None and thk_val <= 0.375 and l_in <= 120.0)
    return bool(regla_12kw_sin_mini or regla_cama_sin_mini)


# Mini-nests RTZ: límite físico de la cama láser — ninguna placa/remanente reutilizable
# puede ser mayor que 120\" × 60\" (ancho × largo; mismas convenciones que placa_w / placa_h en mm).
RTZ_MINI_NEST_MAX_ANCHO_MM = 120.0 * 25.4
RTZ_MINI_NEST_MAX_LARGO_MM = 60.0 * 25.4
# Retazos menores a 20\" × 20\" no generan RTZ ni mini-nest.
RTZ_TAMANO_MIN_IN = 20.0
RTZ_TAMANO_MIN_MM = RTZ_TAMANO_MIN_IN * 25.4
# Barrenos/orificios: umbral más estricto (≥22\") para no abrir RTZ
# “apenas legales” (~20.25\") con 1–3 pzas y baja utilización.
RTZ_HOLE_TAMANO_MIN_IN = 22.0
RTZ_MINI_NEST_AREA_MIN_MM2 = RTZ_TAMANO_MIN_MM * RTZ_TAMANO_MIN_MM
# Metal overlap mínimo para rechazar un RTZ proyectado sobre la madre.
RTZ_REJECT_OVERLAP_MM2 = 100.0


def _es_pieza_fisica_hoja(nombre: str) -> bool:
    n = str(nombre or "")
    return not (_is_virtual_piece(n) or n.startswith("REF__"))


def _hole_ya_reutilizado_en_madre(hole_poly, hoja, min_area_mm2: float = 500.0) -> bool:
    """True si el barreno ya tiene piezas físicas anidadas en la madre."""
    if hole_poly is None or getattr(hole_poly, "is_empty", True):
        return True
    for p in hoja.get("piezas") or []:
        if not _es_pieza_fisica_hoja(p.get("nombre")):
            continue
        g = reconstruir_poly_seguro(p.get("poligonos") or [])
        if g is None or g.is_empty:
            continue
        try:
            c = g.centroid
            if hole_poly.contains(c) or hole_poly.covers(c):
                return True
            inter = hole_poly.intersection(g)
            if float(getattr(inter, "area", 0.0) or 0.0) >= float(min_area_mm2):
                return True
        except Exception:
            continue
    return False


def _rtz_proyectado_choca_madre(hoja_madre, retazo, hoja_retazo, tol_mm2=RTZ_REJECT_OVERLAP_MM2) -> bool:
    """
    True si proyectar las piezas del mini-nest RTZ sobre la madre solapa metal real.
    Evita el empalme REF/RTZ vs piezas ya colocadas (y entre sí sobre la madre).
    """
    if not hoja_madre or not retazo or not hoja_retazo:
        return True
    gx = float(retazo.get("global_x") or 0.0)
    gy = float(retazo.get("global_y") or 0.0)
    solids = []
    for p in hoja_madre.get("piezas") or []:
        if not _es_pieza_fisica_hoja(p.get("nombre")):
            continue
        g = reconstruir_poly_seguro(p.get("poligonos") or [])
        if g is None or g.is_empty:
            continue
        solids.append(g)
    if not solids:
        return False

    projected = []
    for p_acc in hoja_retazo.get("piezas") or []:
        nom = str(p_acc.get("nombre") or "")
        if nom.startswith("REMANENTE__") or nom.startswith("TATUAJE__"):
            continue
        rings = p_acc.get("poligonos") or []
        if not rings:
            continue
        try:
            moved = _translate_poligonos_for_overlay(rings, gx, gy)
            g = Polygon(moved[0], moved[1:] if len(moved) > 1 else None)
            if g.is_empty:
                continue
            if not g.is_valid:
                g = g.buffer(0)
            projected.append(g)
        except Exception:
            continue

    for g in projected:
        for s in solids:
            try:
                if float(g.intersection(s).area) >= float(tol_mm2):
                    return True
            except Exception:
                continue
        for g2 in projected:
            if g2 is g:
                continue
            try:
                if float(g.intersection(g2).area) >= float(tol_mm2):
                    return True
            except Exception:
                continue
    return False


def _retazo_cumple_tamano_minimo(w_mm, h_mm, *, tipo: str | None = None):
    w_mm = float(w_mm or 0.0)
    h_mm = float(h_mm or 0.0)
    if w_mm <= 0.0 or h_mm <= 0.0:
        return False
    w_in = w_mm / 25.4
    h_in = h_mm / 25.4
    min_in = RTZ_HOLE_TAMANO_MIN_IN if str(tipo or "").upper() == "HOLE" else RTZ_TAMANO_MIN_IN
    return min(w_in, h_in) >= min_in


def _filtrar_retazo_por_tamano_minimo(retazo):
    if not isinstance(retazo, dict):
        return None
    w = float(retazo.get("w") or 0.0)
    h = float(retazo.get("h") or 0.0)
    if not _retazo_cumple_tamano_minimo(w, h, tipo=retazo.get("tipo")):
        return None
    return retazo


def _clamp_retazo_mini_nest_a_cama_laser(retazo: dict):
    """
    Garantiza que el área de mini-nest no exceda la cama láser (máx. 120\" × 60\").

    Recorta el retazo virtual a ese rectángulo en coordenadas locales, desde el origen
    del retazo ya normalizado. Si el recorte no deja área útil, devuelve None y no se
    genera mini-nest para ese retazo.
    """
    if not isinstance(retazo, dict):
        return None
    w0 = float(retazo.get("w") or 0.0)
    h0 = float(retazo.get("h") or 0.0)
    if not _retazo_cumple_tamano_minimo(w0, h0, tipo=retazo.get("tipo")):
        return None
    poly = retazo.get("poly_borde")
    if poly is None or getattr(poly, "is_empty", True):
        return None
    max_w = RTZ_MINI_NEST_MAX_ANCHO_MM
    max_h = RTZ_MINI_NEST_MAX_LARGO_MM
    if w0 <= max_w + 1e-3 and h0 <= max_h + 1e-3:
        return retazo
    cw = min(w0, max_w)
    ch = min(h0, max_h)
    try:
        clipped = poly.intersection(box(0.0, 0.0, cw, ch))
    except Exception:
        return None
    if clipped.is_empty:
        return None
    if clipped.geom_type == "Polygon":
        poly_ok = clipped
    elif clipped.geom_type == "MultiPolygon":
        poly_ok = max(clipped.geoms, key=lambda g: float(g.area))
    else:
        return None
    try:
        if not poly_ok.is_valid:
            poly_ok = poly_ok.buffer(0)
    except Exception:
        return None
    if poly_ok.is_empty or float(poly_ok.area) < RTZ_MINI_NEST_AREA_MIN_MM2:
        return None
    minx, miny, maxx, maxy = poly_ok.bounds
    w1, h1 = maxx - minx, maxy - miny
    if w1 < 1.0 or h1 < 1.0 or not _retazo_cumple_tamano_minimo(w1, h1, tipo=retazo.get("tipo")):
        return None
    poly_local = affinity.translate(poly_ok, -minx, -miny)
    out = dict(retazo)
    out["w"] = w1
    out["h"] = h1
    out["poly_borde"] = poly_local
    return out


def _crear_poly_nesting_seguro(poly_exact):
    """Fidelidad 1:1 al DXF: no simplificar nunca la geometría de trabajo."""
    return poly_exact


def _marks_geom_to_lista(marks_geom):
    if marks_geom is None or getattr(marks_geom, "is_empty", True):
        return []
    geoms = list(marks_geom.geoms) if hasattr(marks_geom, "geoms") else [marks_geom]
    out = []
    for g in geoms:
        try:
            if len(g.coords) >= 2:
                out.append(list(g.coords))
        except Exception:
            pass
    return out


def _origen_rotacion_pieza(poly):
    """Mismo criterio que el motor C++: rotar alrededor del centroide del contorno."""
    try:
        c = poly.centroid
        return (float(c.x), float(c.y))
    except Exception:
        return (0.0, 0.0)


def enriquecer_piezas_hoja_con_fuentes(hoja: dict, piezas_origen: list) -> int:
    """
    Asocia ruta DXF, origen y transformación a piezas ya colocadas en una hoja.
    Usado tras renest de placa (empaquetar_con_reintentos / recalcular_hoja_full).
    Devuelve cuántas piezas reales se enriquecieron.
    """
    if not isinstance(hoja, dict) or not piezas_origen:
        return 0

    source_map: dict[str, list] = {}
    for p_orig in piezas_origen:
        if not isinstance(p_orig, dict):
            continue
        base = _piece_name_base(p_orig.get("nombre"))
        if not base:
            continue
        item = dict(p_orig)
        if item.get("poly") is not None and item.get("poly_exact") is None:
            item["poly_exact"] = item.get("poly")
        source_map.setdefault(base, []).append(item)

    enriquecidas = 0
    for p_final in hoja.get("piezas") or []:
        nombre_final = str(p_final.get("nombre") or "")
        if _is_virtual_piece(nombre_final):
            continue

        base = _piece_name_base(nombre_final)
        candidatos = source_map.get(base, [])
        if not candidatos:
            continue

        p_orig = candidatos.pop(0)
        ruta = str(p_orig.get("ruta") or "").strip()
        if not ruta:
            continue

        if p_orig.get("debug_id"):
            p_final["debug_id"] = p_orig.get("debug_id")

        p_final["ruta"] = ruta
        p_final["orig_minx"] = p_orig.get("orig_minx", 0.0)
        p_final["orig_miny"] = p_orig.get("orig_miny", 0.0)
        if p_orig.get("plasma_compensada_manual"):
            p_final["plasma_compensada_manual"] = True
            p_final["plasma_offset_mm_manual"] = float(
                p_orig.get("plasma_offset_mm_manual") or 0.0
            )
            if p_orig.get("plasma_fuente_ya_compensada"):
                p_final["plasma_fuente_ya_compensada"] = True
            if p_orig.get("ruta_plasma"):
                p_final["ruta_plasma"] = p_orig.get("ruta_plasma")
        if p_orig.get("cu_especial_vertical"):
            p_final["cu_especial_vertical"] = True

        transform = _inferir_transformacion_desde_resultado(p_orig, p_final)
        rot_origin = _origen_rotacion_pieza(p_orig.get("poly_exact") or p_orig.get("poly"))
        p_final["rot_origin_cx"] = rot_origin[0]
        p_final["rot_origin_cy"] = rot_origin[1]

        if transform:
            p_final["rot_deg"] = transform["rot_deg"]
            p_final["shift_x"] = transform["shift_x"]
            p_final["shift_y"] = transform["shift_y"]
        else:
            p_final["rot_deg"] = 0.0
            p_final["shift_x"] = 0.0
            p_final["shift_y"] = 0.0

        _colocar_geometria_exacta_en_pieza(p_orig, p_final, transform)
        enriquecidas += 1

    return enriquecidas


def catalogo_rutas_desde_datos_partes(datos_partes, clave: str) -> dict[str, str]:
    """Mapa nombre pieza → ruta DXF desde PARTS / datos_partes_actuales."""
    material_hoja = clave.split("_")[1] if "_" in clave else str(clave or "")
    calibre_hoja = clave.split("_")[0] if "_" in clave else ""
    out: dict[str, str] = {}
    for row in datos_partes or []:
        if not row or len(row) < 6:
            continue
        p_nom = str(row[0] or "").strip()
        mat = str(row[1] or "").strip()
        cal = str(row[3] or "").strip()
        ruta = str(row[5] or "").strip()
        if not p_nom or not ruta:
            continue
        if not MotorNesting._coinciden(calibre_hoja, cal):
            continue
        if not MotorNesting._coinciden(material_hoja, mat):
            continue
        out.setdefault(_piece_name_base(p_nom), ruta)
        out.setdefault(p_nom, ruta)
    return out


def aplicar_rutas_catalogo_en_hoja(hoja: dict, catalogo: dict[str, str]) -> int:
    """Completa pz['ruta'] faltante desde catálogo PARTS."""
    if not isinstance(hoja, dict) or not catalogo:
        return 0
    aplicadas = 0
    for pz in hoja.get("piezas") or []:
        if str(pz.get("ruta") or "").strip():
            continue
        nom = str(pz.get("nombre", "") or "").strip()
        base = _piece_name_base(nom)
        ruta = catalogo.get(nom) or catalogo.get(base) or ""
        if ruta and os.path.isfile(str(ruta)):
            pz["ruta"] = str(ruta)
            aplicadas += 1
    return aplicadas


def enriquecer_hoja_export_desde_partes(hoja: dict, clave: str, datos_partes) -> int:
    """
    Antes de export DXF: solo completa rutas DXF faltantes desde PARTS.
    No re-infiere transformaciones (corrompe shift si poligonos ya están en placa).
    """
    if not isinstance(hoja, dict) or not datos_partes:
        return 0
    catalogo = catalogo_rutas_desde_datos_partes(datos_partes, clave)
    return aplicar_rutas_catalogo_en_hoja(hoja, catalogo)


def _colocar_geometria_exacta_en_pieza(p_orig: dict, p_final: dict, transform: dict | None):
    """
    No reescribe poligonos: la posición la define el motor de nesting.
    Solo completa marcas si el motor no devolvió ninguna.
    """
    marcas_motor = list(p_final.get("marcas") or [])
    if marcas_motor:
        return

    pe = p_orig.get("poly_exact") or p_orig.get("poly")
    if pe is None or pe.is_empty:
        return

    rot = float((transform or {}).get("rot_deg", 0.0) or 0.0)
    sx = float((transform or {}).get("shift_x", 0.0) or 0.0)
    sy = float((transform or {}).get("shift_y", 0.0) or 0.0)
    origin = _origen_rotacion_pieza(pe)

    me = p_orig.get("marks_exact") or p_orig.get("marks")
    if me is not None and not getattr(me, "is_empty", True):
        mk = affinity.translate(affinity.rotate(me, rot, origin=origin), sx, sy)
        lista = _marks_geom_to_lista(mk)
        if lista:
            p_final["marcas"] = lista


def _inferir_transformacion_desde_resultado(p_orig: dict, p_final: dict):
    """
    Mantiene el contrato actual del exporter:
    retorna rot_deg, shift_x, shift_y
    pero infiriéndolos con más información geométrica:
    - contorno completo
    - agujeros
    - marcaje si existe
    """
    try:
        poly_local = p_orig.get("poly_exact") or p_orig.get("poly")
        if poly_local is None or poly_local.is_empty:
            return None

        final_poly = reconstruir_poly_seguro(p_final.get("poligonos", []))
        if final_poly is None or final_poly.is_empty:
            return None

        nminx, nminy, _, _ = final_poly.bounds
        final_poly_zero = affinity.translate(final_poly, -nminx, -nminy)

        marks_local = p_orig.get("marks_exact") or p_orig.get("marks")
        final_marks = _rebuild_marks_geom(p_final.get("marcas", []))
        if final_marks is not None and not final_marks.is_empty:
            final_marks_zero = affinity.translate(final_marks, -nminx, -nminy)
        else:
            final_marks_zero = None

        rot_origin = _origen_rotacion_pieza(poly_local)
        best = None
        best_score = -10**9

        # Incluye 45° solo para *inferir* pose vs DXF (nests viejos / Ultra).
        # FORCE empaqueta solo 0/90/180/270; si no se prueba 45 aquí, un nest
        # histórico a 45° se reescribe mal y empalma en refresh.
        for ang in (0, 45, 90, 135, 180, 225, 270, 315):
            test_poly = affinity.rotate(poly_local, ang, origin=rot_origin)
            tminx, tminy, _, _ = test_poly.bounds
            test_poly_zero = affinity.translate(test_poly, -tminx, -tminy)

            # IoU del polígono completo, no solo exterior
            inter_area = test_poly_zero.intersection(final_poly_zero).area
            union_area = test_poly_zero.union(final_poly_zero).area
            poly_iou = (inter_area / union_area) if union_area > 0 else 0.0

            # Penalización por cambiar agujeros
            hole_penalty = abs(_safe_holes(test_poly_zero) - _safe_holes(final_poly_zero)) * 0.25

            marks_score = 0.0
            if (
                marks_local is not None
                and not getattr(marks_local, "is_empty", True)
                and final_marks_zero is not None
                and not getattr(final_marks_zero, "is_empty", True)
            ):
                try:
                    test_marks = affinity.rotate(marks_local, ang, origin=rot_origin)
                    test_marks_zero = affinity.translate(test_marks, -tminx, -tminy)

                    # buffer pequeño para comparar líneas
                    a = test_marks_zero.buffer(1.0, cap_style=2, join_style=2)
                    b = final_marks_zero.buffer(1.0, cap_style=2, join_style=2)

                    marks_union = a.union(b).area
                    marks_inter = a.intersection(b).area
                    marks_score = (marks_inter / marks_union) if marks_union > 0 else 0.0
                except Exception:
                    marks_score = 0.0

            score = poly_iou + (marks_score * 0.35) - hole_penalty

            if score > best_score:
                best_score = score
                best = {
                    "rot_deg": ang,
                    "shift_x": nminx - tminx,
                    "shift_y": nminy - tminy,
                    "poly_iou": float(poly_iou),
                }

                if poly_iou >= 0.999 and marks_score >= 0.999:
                    break

        # Rechazar match flojo: mejor no inventar rotación (evita empalmes en display).
        if best is not None and float(best.get("poly_iou", 0.0) or 0.0) < 0.92:
            return None
        if best is not None:
            best.pop("poly_iou", None)
        return best
    except Exception:
        return None


def _area_total_piezas(piezas) -> float:
    total = 0.0
    for p in piezas or []:
        total += float(p.get("area", 0.0) or 0.0)
    return total


def _es_cola_de_grupo(pendientes_est, accesorios) -> bool:
    """True cuando el material restante es poco vs una placa estándar."""
    restantes = list(pendientes_est or []) + list(accesorios or [])
    if len(restantes) <= 8:
        return True
    areas = [float(p.get("area", 0) or 0) for p in restantes]
    if not areas:
        return False
    total = sum(areas)
    max_piece = max(areas)
    # Cola: pocas piezas grandes ya colocadas o área total modesta
    return total < max(2_500_000.0, max_piece * 12.0)


def _ordenar_placas_cola(pendientes, placas_validas):
    """Favorece placas más ajustadas al área restante (menos desperdicio)."""
    if not pendientes or not placas_validas:
        return placas_validas
    total_area = _area_total_piezas(pendientes)
    if total_area <= 0:
        return placas_validas

    def key(p):
        pa = float(p.get("w", 0) or 0) * float(p.get("h", 0) or 0)
        if pa <= 0:
            return float("inf")
        util = total_area / pa
        precio = float(p.get("precio", 0) or 0)
        # Ideal: util 0.35–0.90; penalizar placa demasiado grande
        if util > 0.95:
            waste = 0.05
        elif util < 0.15:
            waste = 1.0 - util + 0.5
        else:
            waste = max(0.0, 0.70 - util) if util < 0.70 else max(0.0, util - 0.88) * 0.5
        return (waste * 800.0) + (precio * 0.02)

    return sorted(placas_validas, key=key)


def _refinar_hoja_empaque(
    hoja,
    piezas_origen,
    w_placa,
    h_placa,
    kerf,
    margin,
    opt,
    corner,
    limite_poly=None,
    intentos=12,
    mc_iterations=None,
):
    """Reempaqueta la hoja ganadora buscando mejor compactación (mismo set de piezas)."""
    from .sheet_integrity import batch_reempaque_desde_hoja

    if not hoja or not piezas_origen:
        return hoja

    batch = batch_reempaque_desde_hoja(hoja, piezas_origen)
    if not batch:
        return hoja

    mc_iters = int(
        mc_iterations if mc_iterations is not None
        else get_nest_profile().get("mc_iterations", 15)
    )
    n_intentos = max(1, int(intentos))
    try:
        from .giga_cal11_galv import should_force_giga_engine

        if should_force_giga_engine():
            n_intentos = 1
            mc_iters = 1
    except Exception:
        pass
    mejor = hoja
    mejor_area = float(hoja.get("area_usada", 0) or 0)
    mejor_n = len(hoja.get("piezas") or [])

    for intento in range(n_intentos):
        if intento == 0:
            orden = sorted(batch, key=lambda x: float(x.get("area", 0) or 0), reverse=True)
        else:
            orden = batch.copy()
            random.shuffle(orden)
            orden.sort(key=lambda x: float(x.get("area", 0) or 0), reverse=True)

        nh, sobras = _safe_empaquetar_una_hoja_mc(
            orden,
            w_placa,
            h_placa,
            kerf,
            margin,
            opt,
            corner,
            limite_poly=limite_poly,
            debug_tag=f"refinar_hoja|try={intento + 1}",
            mc_iterations=mc_iters,
        )
        if not nh or not nh.get("piezas"):
            continue
        n_col = len([p for p in nh["piezas"] if not str(p.get("nombre", "")).startswith("REMANENTE")])
        area = float(nh.get("area_usada", 0) or 0)
        if sobras:
            if area > mejor_area or (area == mejor_area and n_col > mejor_n):
                mejor_area = area
                mejor_n = n_col
                mejor = nh
            continue
        if area >= mejor_area:
            mejor_area = area
            mejor_n = n_col
            mejor = nh
            break

    n_esperado = len(batch)
    n_mejor = len(
        [p for p in (mejor.get("piezas") or []) if not str(p.get("nombre", "")).startswith("REMANENTE")]
    )
    if n_mejor < n_esperado:
        return hoja
    return mejor


def _filtrar_placas_para_accesorios(piezas, placas_validas):
    """
    Con accesorios restantes, descarta placas gigantes cuando el área de piezas
    no justifica una madre 240x96 (cola de accesorios / RTZ).
    """
    if not piezas or not placas_validas:
        return placas_validas
    total_area = _area_total_piezas(piezas)
    if total_area <= 0:
        return placas_validas

    scored = []
    for p in placas_validas:
        pa = float(p.get("w", 0) or 0) * float(p.get("h", 0) or 0)
        if pa <= 0:
            continue
        util = total_area / pa
        scored.append((p, util, pa))

    if not scored:
        return placas_validas

    # Si cabe razonablemente en placas medianas, no simular las enormes (<18% util)
    medianas = [t for t in scored if 0.18 <= t[1] <= 0.92]
    if len(medianas) >= 2:
        medianas.sort(key=lambda t: (abs(t[1] - 0.62), t[2]))
        return [t[0] for t in medianas]
    scored.sort(key=lambda t: (abs(t[1] - 0.55), t[2]))
    return [t[0] for t in scored[: max(3, len(scored))]]


def _empaquetar_mejor_hoja_mc(
    piezas,
    w_placa,
    h_placa,
    kerf,
    margin,
    opt,
    corner,
    *,
    limite_poly=None,
    debug_tag="",
    mc_iterations=None,
    solo_accesorios=False,
    accesorios_retries=14,
):
    """Empaque de hoja: accesorios/RTZ con reintentos; estructurales en un paso MC."""
    if solo_accesorios and piezas:
        base = sorted(
            [copy.deepcopy(p) for p in piezas],
            key=lambda x: float(x.get("area", 0) or 0),
            reverse=True,
        )
        mc_iters = int(mc_iterations or get_nest_profile().get("mc_iterations", 15))
        mejor_hoja = None
        mejor_restos = list(piezas)
        mejor_n = len(piezas) + 1

        max_retries = max(1, int(accesorios_retries or 14))
        # Lotes grandes: 1 intento. 8×267 piezas con VFM multi-hueco = minutos/placa.
        if len(base) > 40:
            max_retries = 1
        for intento in range(max_retries):
            if intento == 0:
                batch = base
            else:
                batch = base.copy()
                random.shuffle(batch)
                batch.sort(key=lambda x: float(x.get("area", 0) or 0), reverse=True)

            nh, sobras = _safe_empaquetar_una_hoja_mc(
                batch,
                w_placa,
                h_placa,
                kerf,
                margin,
                opt,
                corner,
                limite_poly=limite_poly,
                debug_tag=f"{debug_tag}|acc_try={intento + 1}",
                mc_iterations=mc_iters,
            )
            if not nh or not nh.get("piezas"):
                continue
            n_sob = len(sobras or [])
            if not sobras:
                return nh, []
            if n_sob < mejor_n:
                mejor_n = n_sob
                mejor_hoja = nh
                mejor_restos = list(sobras or [])

        if mejor_hoja:
            return mejor_hoja, mejor_restos

    return _safe_empaquetar_una_hoja_mc(
        piezas,
        w_placa,
        h_placa,
        kerf,
        margin,
        opt,
        corner,
        limite_poly=limite_poly,
        debug_tag=debug_tag,
        mc_iterations=mc_iterations,
    )


ARGA_VOID_MIN_AREA_MM2 = 40.0 * 40.0
# ~200 in²: VFM/HFM grandes entran como estructurales (antes 499 in² las
# mandaba a “solo accesorios” y disparaba 8 reintentos × 267 piezas).
ARGA_AREA_ESTRUCTURAL_MM2 = 200 * 645.16


def _es_pieza_estructural(p, umbral=None):
    um = float(umbral if umbral is not None else ARGA_AREA_ESTRUCTURAL_MM2)
    area = float(p.get("area", 0) or 0)
    if area > um:
        return True
    poly = p.get("poly")
    try:
        interiors = getattr(poly, "interiors", None) or ()
        if interiors:
            hole_area = 0.0
            for ring in interiors:
                try:
                    hole_area += float(Polygon(ring).area)
                except Exception:
                    continue
            if hole_area >= ARGA_VOID_MIN_AREA_MM2:
                return True
    except Exception:
        pass
    return False


def _split_pool_estructural_accesorio(piezas, umbral=None):
    est, acc = [], []
    for p in piezas or []:
        if _es_pieza_estructural(p, umbral):
            est.append(p)
        else:
            acc.append(p)
    return est, acc


def _empaquetar_arga_combinado(
    estructurales,
    accesorios,
    w_placa,
    h_placa,
    kerf,
    margin,
    opt,
    corner,
    *,
    mc_iterations=1,
    debug_tag="",
):
    """
  Una sola llamada C++: estructurales primero, luego gap-fill de accesorios
  en huecos libres e interiores (motor ARGA Base).
    """
    batch = [copy.deepcopy(p) for p in (estructurales or [])]
    batch.extend(copy.deepcopy(p) for p in (accesorios or []))
    if not batch:
        return None, [], list(accesorios or [])

    hoja, restos = _safe_empaquetar_una_hoja_mc(
        batch,
        w_placa,
        h_placa,
        kerf,
        margin,
        opt,
        corner,
        debug_tag=debug_tag,
        mc_iterations=mc_iterations,
    )
    if not hoja or not hoja.get("piezas"):
        return hoja, list(estructurales or []), list(accesorios or [])
    restos_est, restos_acc = _split_pool_estructural_accesorio(restos)
    return hoja, restos_est, restos_acc


def _as_pack_piece_from_colocada(p):
    """Convierte pieza ya colocada en hoja al formato de empaque (origen local)."""
    poly = p.get("poly") if p else None
    if poly is None or getattr(poly, "is_empty", True):
        poly = reconstruir_poly_seguro((p or {}).get("poligonos") or [])
    if poly is None or getattr(poly, "is_empty", True):
        return None

    marks_geom = _rebuild_marks_geom((p or {}).get("marcas") or [])
    if marks_geom is None:
        marks_geom = LineString()

    minx, miny, _, _ = poly.bounds
    out = {
        "nombre": str((p or {}).get("nombre", "")),
        "poly": affinity.translate(poly, -minx, -miny),
        "marks": affinity.translate(marks_geom, -minx, -miny) if not marks_geom.is_empty else marks_geom,
        "area": float((p or {}).get("area", poly.area) or poly.area),
        "calibre": (p or {}).get("calibre", ""),
        "material": (p or {}).get("material", ""),
    }
    for k in ("debug_id", "ruta", "orig_minx", "orig_miny"):
        if (p or {}).get(k) is not None:
            out[k] = (p or {}).get(k)
    return out


def _piezas_expulsadas_a_pool(expulsadas) -> list:
    """Reinyecta al pool piezas sacadas por pokayoke kerf (siempre con ``poly``)."""
    pool = []
    for raw in expulsadas or []:
        if not isinstance(raw, dict):
            continue
        pack = _as_pack_piece_from_colocada(raw)
        if pack is not None and pack.get("poly") is not None:
            pool.append(pack)
            continue
        # Último recurso: si ya trae poly usable, normalizar a origen.
        poly = raw.get("poly")
        if poly is not None and not getattr(poly, "is_empty", True):
            try:
                minx, miny, _, _ = poly.bounds
                p2 = dict(raw)
                p2["poly"] = affinity.translate(poly, -minx, -miny)
                pool.append(p2)
            except Exception:
                pass
    return pool


def _zonas_libres_hoja_madre(hoja, w_placa, h_placa, kerf_in, margin_in):
    """Regiones libres en placa madre: huecos entre piezas + interiores de piezas grandes."""
    if not isinstance(hoja, dict):
        return []

    kerf_radio = (float(kerf_in or 0.0) * 25.4) / 2.0
    margin_px = float(margin_in or 0.0) * 25.4
    sheet = box(margin_px, margin_px, w_placa - margin_px, h_placa - margin_px)
    if sheet.is_empty:
        return []

    ocupados = []
    for p in hoja.get("piezas") or []:
        nombre = str(p.get("nombre") or "")
        if _is_virtual_piece(nombre):
            continue
        poly = reconstruir_poly_seguro(p.get("poligonos") or [])
        if poly is None or poly.is_empty:
            continue
        try:
            ocupados.append(poly.buffer(kerf_radio, join_style=2))
        except Exception:
            ocupados.append(poly)

    zonas = []
    if ocupados:
        try:
            libre = sheet.difference(unary_union(ocupados))
        except Exception:
            libre = sheet
        if not libre.is_empty:
            geoms = list(libre.geoms) if libre.geom_type == "MultiPolygon" else [libre]
            for g in geoms:
                if g.geom_type != "Polygon" or float(g.area) < ARGA_VOID_MIN_AREA_MM2:
                    continue
                zonas.append(g)

    for p in hoja.get("piezas") or []:
        nombre = str(p.get("nombre") or "")
        if _is_virtual_piece(nombre):
            continue
        poly = reconstruir_poly_seguro(p.get("poligonos") or [])
        if poly is None or poly.is_empty:
            continue
        for interior in interiores_poly(poly):
            try:
                hole = Polygon(interior)
            except Exception:
                continue
            if hole.is_empty or float(hole.area) < ARGA_VOID_MIN_AREA_MM2:
                continue
            zonas.append(hole)

    zonas.sort(key=lambda z: float(z.area), reverse=True)
    return zonas


def _rellenar_accesorios_en_huecos_hoja(
    hoja,
    accesorios,
    w_placa,
    h_placa,
    kerf,
    margin,
    opt,
    corner,
    *,
    mc_iterations=1,
    accesorios_retries=6,
    clave="",
    solo_interiores=False,
):
    """
    Coloca accesorios en huecos de una placa ya comprometida (p. ej. sin RTZ).
    Usar solo cuando el empaquetado combinado no cubrió interiores; una pasada segura.
    """
    from .sheet_integrity import calcular_restos_desde_colocados

    if not accesorios or not isinstance(hoja, dict) or not hoja.get("piezas"):
        return accesorios

    pool = copy.deepcopy(accesorios)
    kerf_mm = (float(kerf or 0.0) * 25.4) / 2.0

    if solo_interiores:
        zonas = []
        for p in hoja.get("piezas") or []:
            if _is_virtual_piece(str(p.get("nombre") or "")):
                continue
            poly = reconstruir_poly_seguro(p.get("poligonos") or [])
            if poly is None:
                continue
            for interior in interiores_poly(poly):
                try:
                    hole = Polygon(interior)
                    if float(hole.area) >= ARGA_VOID_MIN_AREA_MM2:
                        zonas.append(hole)
                except Exception:
                    pass
        zonas.sort(key=lambda z: float(z.area), reverse=True)
    else:
        zonas = _zonas_libres_hoja_madre(hoja, w_placa, h_placa, kerf, margin)

    if not zonas:
        return pool

    colocados_total = 0
    for zona in zonas:
        if not pool:
            break

        minx, miny, maxx, maxy = zona.bounds
        w_z = maxx - minx
        h_z = maxy - miny
        if w_z < 5.0 or h_z < 5.0:
            continue

        try:
            zona_pack = zona.buffer(-kerf_mm, join_style=2) if kerf_mm > 0 else zona
            if zona_pack.is_empty:
                zona_pack = zona
        except Exception:
            zona_pack = zona

        zona_pack = _polygon_usable_for_limite(zona_pack)
        if zona_pack is None:
            continue

        # Restar piezas YA colocadas en esa zona (evita empalmes al rellenar barrenos).
        try:
            ocupado = []
            for p_ex in hoja.get("piezas") or []:
                if not _es_pieza_fisica_hoja(p_ex.get("nombre")):
                    continue
                g_ex = reconstruir_poly_seguro(p_ex.get("poligonos") or [])
                if g_ex is None or g_ex.is_empty:
                    continue
                if zona_pack.intersects(g_ex):
                    ocupado.append(g_ex)
            if ocupado:
                zona_pack = zona_pack.difference(unary_union(ocupado))
                zona_pack = _polygon_usable_for_limite(zona_pack)
                if zona_pack is None:
                    continue
        except Exception:
            pass

        minx, miny, maxx, maxy = zona_pack.bounds
        w_z = maxx - minx
        h_z = maxy - miny
        if w_z < 5.0 or h_z < 5.0:
            continue

        area_z = float(zona_pack.area)
        candidatos = []
        restantes = []
        for p in pool:
            area_p = float(p.get("area", 0) or 0)
            if area_p > area_z * 0.95:
                restantes.append(p)
                continue
            poly = p.get("poly")
            if poly is None:
                restantes.append(p)
                continue
            bx0, by0, bx1, by1 = poly.bounds
            w_p, h_p = bx1 - bx0, by1 - by0
            min_p, max_p = min(w_p, h_p), max(w_p, h_p)
            min_z, max_z = min(w_z, h_z), max(w_z, h_z)
            if min_p <= min_z + 3.0 and max_p <= max_z + 3.0:
                candidatos.append(copy.deepcopy(p))
            else:
                restantes.append(p)

        pool = restantes
        if not candidatos:
            continue

        poly_local = affinity.translate(zona_pack, -minx, -miny)
        hoja_z, _restos_z = _empaquetar_mejor_hoja_mc(
            candidatos,
            w_z,
            h_z,
            kerf,
            margin,
            opt,
            corner,
            limite_poly=poly_local,
            debug_tag=f"clave={clave} | hueco_backfill",
            mc_iterations=mc_iterations,
            solo_accesorios=True,
            accesorios_retries=max(6, int(accesorios_retries or 6)),
        )
        if not hoja_z or not hoja_z.get("piezas"):
            pool.extend(candidatos)
            continue

        for p_acc in hoja_z.get("piezas") or []:
            if _is_virtual_piece(str(p_acc.get("nombre") or "")):
                continue
            p_clon = copy.deepcopy(p_acc)
            if p_clon.get("poligonos"):
                p_clon["poligonos"] = _translate_poligonos_for_overlay(
                    p_clon["poligonos"], minx, miny
                )
            # Rechazar si aún empalma metal real de la madre.
            g_new = reconstruir_poly_seguro(p_clon.get("poligonos") or [])
            choca = False
            if g_new is not None and not g_new.is_empty:
                for p_ex in hoja.get("piezas") or []:
                    if not _es_pieza_fisica_hoja(p_ex.get("nombre")):
                        continue
                    g_ex = reconstruir_poly_seguro(p_ex.get("poligonos") or [])
                    if g_ex is None or g_ex.is_empty:
                        continue
                    try:
                        if float(g_new.intersection(g_ex).area) >= 100.0:
                            choca = True
                            break
                    except Exception:
                        continue
            if choca:
                _dbg_nesting(
                    f"[HUECO-BACKFILL-SKIP-OVERLAP] clave={clave} | "
                    f"pieza={p_acc.get('nombre')}"
                )
                continue
            if p_clon.get("marcas"):
                nuevas_marcas = []
                for line_coords in p_clon["marcas"]:
                    try:
                        nuevas_marcas.append(
                            list(
                                affinity.translate(
                                    LineString(line_coords), xoff=minx, yoff=miny
                                ).coords
                            )
                        )
                    except Exception:
                        nuevas_marcas.append(line_coords)
                p_clon["marcas"] = nuevas_marcas
            hoja.setdefault("piezas", []).append(p_clon)
            colocados_total += 1

    pool = calcular_restos_desde_colocados(accesorios, hoja)

    if colocados_total:
        actualizar_eficiencias_hoja(hoja)
        _dbg_nesting(
            f"[HUECO-BACKFILL] clave={clave} | piezas_colocadas={colocados_total} | "
            f"accesorios_restantes={len(pool)} | solo_interiores={solo_interiores}"
        )
    return pool


ARGA_REDIST_UMBRAL_EF = 52.0
ARGA_REDIST_PIEZAS_SUELTAS_MAX = 8
# Post-proceso (redistribuir / huecos): simulaciones rápidas; el empaque principal no se toca.
ARGA_POST_MC_ITERATIONS = 1
ARGA_POST_ACC_RETRIES = 2
ARGA_REDIST_MAX_DESTINOS = 6
ARGA_REDIST_MAX_PASADAS = 4
# Si la absorción completa falla, no intentar 40+ piezas sueltas (minutos/placa).
ARGA_REDIST_MAX_PIEZAS_SUELTAS = 8


def _polygon_usable_for_limite(geom):
    """Normaliza zona libre a un solo Polygon (buffer puede devolver MultiPolygon)."""
    if geom is None or getattr(geom, "is_empty", False):
        return None
    if geom.geom_type == "MultiPolygon":
        polys = [
            g for g in geom.geoms
            if getattr(g, "geom_type", "") == "Polygon" and not g.is_empty
        ]
        if not polys:
            return None
        return max(polys, key=lambda g: float(g.area))
    if geom.geom_type == "Polygon":
        return geom
    return None


def _pieza_cabe_bbox_en_placa(pieza, w_placa, h_placa, tol=10.0):
    poly = pieza.get("poly")
    if poly is None:
        return True
    minx, miny, maxx, maxy = poly.bounds
    w_req, h_req = maxx - minx, maxy - miny
    max_req, min_req = max(w_req, h_req), min(w_req, h_req)
    max_p, min_p = max(w_placa, h_placa), min(w_placa, h_placa)
    return max_p >= (max_req - tol) and min_p >= (min_req - tol)


def _params_placa_hoja(hoja):
    return {
        "kerf": float(hoja.get("kerf_usado", DEFAULT_KERF_IN) or DEFAULT_KERF_IN),
        "margin": float(hoja.get("margin_usado", DEFAULT_MARGIN_IN) or DEFAULT_MARGIN_IN),
        "opt": hoja.get("opt_usado", "OPTIMIZAR LARGO Y ANCHO"),
        "corner": hoja.get("corner_usado", "INFERIOR IZQUIERDA"),
        "w": float(hoja.get("placa_w", 0.0) or 0.0),
        "h": float(hoja.get("placa_h", 0.0) or 0.0),
    }


def _meta_placa_desde_hoja(hoja):
    return {
        k: hoja.get(k)
        for k in (
            "placa_id",
            "placa_w",
            "placa_h",
            "precio_placa",
            "kerf_usado",
            "margin_usado",
            "opt_usado",
            "corner_usado",
            "es_retazo",
            "origen_placa",
            "sheet_uid",
            "_nest_list_idx",
        )
    }


def _aplicar_renest_en_hoja(plantilla, nueva, params):
    meta = _meta_placa_desde_hoja(plantilla)
    plantilla.clear()
    plantilla.update(nueva)
    plantilla.update(meta)
    plantilla["placa_w"] = params["w"]
    plantilla["placa_h"] = params["h"]
    plantilla["kerf_usado"] = params["kerf"]
    plantilla["margin_usado"] = params["margin"]
    plantilla["opt_usado"] = params["opt"]
    plantilla["corner_usado"] = params["corner"]
    actualizar_eficiencias_hoja(plantilla)


def _piezas_pack_en_hoja(hoja):
    out = []
    for p in (hoja.get("piezas") or []):
        if _is_virtual_piece(str(p.get("nombre") or "")):
            continue
        pp = _as_pack_piece_from_colocada(p)
        if pp is not None:
            out.append(pp)
    return out


def _simular_renest_agregar(
    destino,
    piezas_extra_pack,
    *,
    mc_iterations=1,
    accesorios_retries=8,
):
    """Prueba si destino puede absorber piezas extra re-empacando toda la placa."""
    if not piezas_extra_pack:
        return False, None
    base = _piezas_pack_en_hoja(destino)
    combinadas = base + [copy.deepcopy(p) for p in piezas_extra_pack]
    params = _params_placa_hoja(destino)
    w, h = params["w"], params["h"]
    if w <= 0 or h <= 0:
        return False, None

    esperadas = Counter(str(p.get("nombre") or "") for p in combinadas)
    tiene_est = any(_es_pieza_estructural(p) for p in combinadas)
    nueva, sobras = _empaquetar_mejor_hoja_mc(
        combinadas,
        w,
        h,
        params["kerf"],
        params["margin"],
        params["opt"],
        params["corner"],
        debug_tag="redist_agregar",
        mc_iterations=mc_iterations,
        solo_accesorios=not tiene_est,
        accesorios_retries=max(6, int(accesorios_retries or 8)),
    )
    if sobras or not nueva or not nueva.get("piezas"):
        return False, None

    colocadas = Counter(
        str(p.get("nombre") or "")
        for p in nueva.get("piezas") or []
        if not _is_virtual_piece(str(p.get("nombre") or ""))
    )
    if colocadas != esperadas:
        return False, None
    return True, nueva


def _renest_hoja_desde_pack(hoja, piezas_pack, *, mc_iterations=1, accesorios_retries=8):
    if not piezas_pack:
        return True
    params = _params_placa_hoja(hoja)
    w, h = params["w"], params["h"]
    if w <= 0 or h <= 0:
        return False
    tiene_est = any(_es_pieza_estructural(p) for p in piezas_pack)
    nueva, sobras = _empaquetar_mejor_hoja_mc(
        [copy.deepcopy(p) for p in piezas_pack],
        w,
        h,
        params["kerf"],
        params["margin"],
        params["opt"],
        params["corner"],
        debug_tag="redist_renest_origen",
        mc_iterations=mc_iterations,
        solo_accesorios=not tiene_est,
        accesorios_retries=max(6, int(accesorios_retries or 8)),
    )
    if sobras or not nueva or not nueva.get("piezas"):
        return False
    _aplicar_renest_en_hoja(hoja, nueva, params)
    # --- Lite hole-fill + Venom (re-nesteo interno) ---
    import os
    try:
        from .venom_hole_fill import apply_lite_hole_fill

        _engine_id = os.environ.get("ARGA_MOTOR_NESTING", "svgnest_ultra")
        apply_lite_hole_fill(hoja, engine_id=_engine_id)
    except Exception:
        pass
    try:
        from . import venom_ai

        _engine_id = os.environ.get("ARGA_MOTOR_NESTING", "svgnest_ultra")
        venom_ai.apply_smart_polisher(hoja, _engine_id)
    except Exception:
        pass
    # --------------------------------------------
    return True


def _placa_candidata_redistribuir(hoja):
    if not isinstance(hoja, dict) or hoja.get("es_retazo"):
        return False
    ef = float(hoja.get("eficiencia", 0) or 0)
    n = len(_piezas_pack_en_hoja(hoja))
    if n <= 0:
        return False
    if ef < ARGA_REDIST_UMBRAL_EF:
        return True
    if n <= ARGA_REDIST_PIEZAS_SUELTAS_MAX and ef < 68.0:
        return True
    return False


def _redistribuir_placas_subutilizadas_arga(
    hojas_finales,
    kerf,
    margin,
    opt,
    corner,
    *,
    mc_iterations=1,
    accesorios_retries=8,
    clave="",
    solo_absorcion=True,
):
    """
    Consolida piezas de placas muy vacías hacia placas densas del mismo grupo
    (mismo calibre+material). Fill-first: no balancea hacia hojas flojas.

    Por defecto solo_absorcion=True: intenta absorber la placa completa (rápido).
    Pieza-a-pieza solo si solo_absorcion=False y n <= ARGA_REDIST_MAX_PIEZAS_SUELTAS.
    """
    if not hojas_finales:
        return hojas_finales, 0.0

    madres = [h for h in hojas_finales if isinstance(h, dict) and not h.get("es_retazo")]
    rtz = [h for h in hojas_finales if isinstance(h, dict) and h.get("es_retazo")]
    if len(madres) < 2:
        return hojas_finales, 0.0

    ahorro_total = 0.0
    cambiado = True
    pasada = 0
    while cambiado and pasada < ARGA_REDIST_MAX_PASADAS:
        pasada += 1
        cambiado = False
        origenes = [h for h in madres if _placa_candidata_redistribuir(h)]
        origenes.sort(key=lambda h: float(h.get("eficiencia", 0) or 0))

        for origen in list(origenes):
            if origen not in madres:
                continue
            pool_origen = _piezas_pack_en_hoja(origen)
            if not pool_origen:
                continue

            destinos = [
                h for h in madres if h is not origen and not h.get("es_retazo")
            ]
            destinos.sort(
                key=lambda h: float(h.get("eficiencia", 0) or 0),
                reverse=True,
            )
            destinos = destinos[:ARGA_REDIST_MAX_DESTINOS]
            if not destinos:
                continue

            # 1) Intento barato: absorber TODA la placa spars en un destino denso.
            absorbido = False
            for destino in destinos:
                params_d = _params_placa_hoja(destino)
                if not all(
                    _pieza_cabe_bbox_en_placa(p, params_d["w"], params_d["h"])
                    for p in pool_origen
                ):
                    continue
                ok, nueva = _simular_renest_agregar(
                    destino,
                    pool_origen,
                    mc_iterations=mc_iterations,
                    accesorios_retries=accesorios_retries,
                )
                if not ok or nueva is None:
                    continue
                params = _params_placa_hoja(destino)
                _aplicar_renest_en_hoja(destino, nueva, params)
                ahorro_total += float(origen.get("precio_placa", 0) or 0)
                madres.remove(origen)
                cambiado = True
                absorbido = True
                _dbg_nesting(
                    f"[REDISTRIBUIR-ABSORBER] clave={clave} | "
                    f"origen={origen.get('placa_id')} | destino={destino.get('placa_id')} | "
                    f"piezas={len(pool_origen)} | dest_ef={float(destino.get('eficiencia', 0) or 0):.1f}% | "
                    f"ahorro=${float(origen.get('precio_placa', 0) or 0):.2f}"
                )
                break
            if absorbido:
                continue

            # 2) Pieza a pieza solo bajo demanda y con pocas piezas (caro).
            if solo_absorcion or len(pool_origen) > ARGA_REDIST_MAX_PIEZAS_SUELTAS:
                if not solo_absorcion:
                    _dbg_nesting(
                        f"[REDISTRIBUIR-SKIP-SUELTAS] clave={clave} | "
                        f"origen={origen.get('placa_id')} | n={len(pool_origen)} | "
                        "absorción falló; no se reparte pieza-a-pieza (costo alto)"
                    )
                continue

            movidas = 0
            for pieza in sorted(list(pool_origen), key=lambda p: float(p.get("area", 0) or 0)):
                opciones = []
                for destino in destinos:
                    if destino not in madres:
                        continue
                    params_d = _params_placa_hoja(destino)
                    if not _pieza_cabe_bbox_en_placa(
                        pieza, params_d["w"], params_d["h"]
                    ):
                        continue
                    ok, nueva = _simular_renest_agregar(
                        destino,
                        [pieza],
                        mc_iterations=mc_iterations,
                        accesorios_retries=accesorios_retries,
                    )
                    if not ok or nueva is None:
                        continue
                    ef_old = float(destino.get("eficiencia", 0) or 0)
                    ef_new = float(nueva.get("eficiencia", 0) or 0)
                    opciones.append((destino, nueva, ef_old, ef_new))

                if not opciones:
                    continue

                opciones.sort(key=lambda t: (-t[2], -t[3]))
                destino, nueva, ef_old, ef_new = opciones[0]
                params = _params_placa_hoja(destino)
                snap_destino = copy.deepcopy(destino)
                _aplicar_renest_en_hoja(destino, nueva, params)
                try:
                    pool_origen.remove(pieza)
                except ValueError:
                    for i, p in enumerate(pool_origen):
                        if p is pieza:
                            pool_origen.pop(i)
                            break
                if pool_origen:
                    if not _renest_hoja_desde_pack(
                        origen,
                        pool_origen,
                        mc_iterations=mc_iterations,
                        accesorios_retries=accesorios_retries,
                    ):
                        _aplicar_renest_en_hoja(destino, snap_destino, params)
                        pool_origen.append(pieza)
                        _dbg_nesting(
                            f"[REDISTRIBUIR-ROLLBACK] clave={clave} | pieza={pieza.get('nombre')} | "
                            f"origen={origen.get('placa_id')} | destino={destino.get('placa_id')} | "
                            "re-nest origen falló; se revierte destino"
                        )
                        continue
                movidas += 1
                cambiado = True
                _dbg_nesting(
                    f"[REDISTRIBUIR] clave={clave} | pieza={pieza.get('nombre')} | "
                    f"desde_ef={float(origen.get('eficiencia', 0) or 0):.1f}% | "
                    f"hacia={destino.get('placa_id')} | dest_ef_antes={ef_old:.1f}% | "
                    f"dest_ef_despues={ef_new:.1f}%"
                )

            if not movidas:
                continue

            if pool_origen:
                if not _renest_hoja_desde_pack(
                    origen,
                    pool_origen,
                    mc_iterations=mc_iterations,
                    accesorios_retries=accesorios_retries,
                ):
                    _dbg_nesting(
                        f"[REDISTRIBUIR-WARN] clave={clave} | origen={origen.get('placa_id')} | "
                        "no se pudo re-nestear piezas restantes tras movimientos"
                    )
            else:
                ahorro_total += float(origen.get("precio_placa", 0) or 0)
                if origen in madres:
                    madres.remove(origen)
                _dbg_nesting(
                    f"[REDISTRIBUIR-PLACA-ELIMINADA] clave={clave} | placa_id={origen.get('placa_id')} | "
                    f"ahorro=${float(origen.get('precio_placa', 0) or 0):.2f}"
                )

    return madres + rtz, ahorro_total


def _rellenar_huecos_en_placas_madre(
    hojas_finales,
    kerf,
    margin,
    opt,
    corner,
    *,
    mc_iterations=1,
    accesorios_retries=8,
    clave="",
):
    """
    Mueve accesorios de placas poco llenas a otras placas del lote
    con re-empaque transaccional (sin append suelto que duplica inventario).
    """
    if not hojas_finales:
        return hojas_finales

    vacias: set[int] = set()
    madres = [h for h in hojas_finales if isinstance(h, dict) and not h.get("es_retazo")]
    if len(madres) < 2:
        return hojas_finales

    donantes = sorted(
        [h for h in madres if _placa_candidata_redistribuir(h)],
        key=lambda h: float(h.get("eficiencia", 0) or 0),
    )
    receptores = sorted(madres, key=lambda h: -float(h.get("eficiencia", 0) or 0))

    for donante in donantes:
        pool = _piezas_pack_en_hoja(donante)
        accesorios = [p for p in pool if not _es_pieza_estructural(p)]
        estructurales = [p for p in pool if _es_pieza_estructural(p)]
        if not accesorios:
            continue

        movidas = 0
        for pieza in sorted(list(accesorios), key=lambda p: float(p.get("area", 0) or 0)):
            colocada = False
            for receptor in receptores:
                if receptor is donante:
                    continue
                ok, nueva = _simular_renest_agregar(
                    receptor,
                    [pieza],
                    mc_iterations=mc_iterations,
                    accesorios_retries=accesorios_retries,
                )
                if not ok or nueva is None:
                    continue
                params = _params_placa_hoja(receptor)
                snap_receptor = copy.deepcopy(receptor)
                _aplicar_renest_en_hoja(receptor, nueva, params)
                try:
                    accesorios.remove(pieza)
                except ValueError:
                    for i, p in enumerate(accesorios):
                        if p is pieza:
                            accesorios.pop(i)
                            break
                restantes_don = estructurales + accesorios
                if restantes_don:
                    if not _renest_hoja_desde_pack(
                        donante,
                        restantes_don,
                        mc_iterations=mc_iterations,
                        accesorios_retries=accesorios_retries,
                    ):
                        _aplicar_renest_en_hoja(receptor, snap_receptor, params)
                        accesorios.append(pieza)
                        _dbg_nesting(
                            f"[HUECO-ENTRE-ROLLBACK] clave={clave} | pieza={pieza.get('nombre')} | "
                            f"donante={donante.get('placa_id')} | receptor={receptor.get('placa_id')}"
                        )
                        continue
                movidas += 1
                colocada = True
                break
            if not colocada:
                break

        if movidas > 0:
            _dbg_nesting(
                f"[HUECO-ENTRE-PLACAS] clave={clave} | donante={donante.get('placa_id')} | "
                f"colocados={movidas} | restantes={len(accesorios)}"
            )

        if not estructurales and not accesorios:
            vacias.add(id(donante))

    if not vacias:
        return hojas_finales
    return [h for h in hojas_finales if id(h) not in vacias]


def _validar_inventario_hojas(piezas, hojas, *, clave="", kerf_global=DEFAULT_KERF_IN):
    from .sheet_integrity import sanitizar_hojas_grupo, validar_colocacion_completa

    hojas_chk = sanitizar_hojas_grupo(
        piezas, copy.deepcopy(hojas), clave=clave, kerf_global=kerf_global
    )
    ok, msg = validar_colocacion_completa(piezas, hojas_chk)
    return ok, msg, hojas_chk


def _post_proceso_arga_seguro(
    piezas,
    hojas_finales,
    costo_total_lote,
    kerf,
    margin,
    opt,
    corner,
    *,
    clave="",
):
    """
    Post FORCE entre madres (absorber placa spars re-empaquetando).

    Desactivado por defecto: tras un nest 51+1 repetía FORCE (~minutos) casi
    siempre sin poder absorber, duplicando tiempo. El empaque por hoja debe
    cerrar bien a la primera. Reactivar: ARGA_POST_REDIST=1.
    """
    if not hojas_finales or not _es_motor_arga_force():
        return hojas_finales, costo_total_lote

    madres_antes = sum(
        1 for h in hojas_finales if isinstance(h, dict) and not h.get("es_retazo")
    )
    flag = str(os.environ.get("ARGA_POST_REDIST", "")).strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        _dbg_nesting(
            f"[POST-ARGA-SKIP] clave={clave} | hojas={len(hojas_finales)} | "
            f"madres={madres_antes} | redistribuir=off"
        )
        return hojas_finales, costo_total_lote

    _dbg_nesting(
        f"[POST-ARGA-ON] clave={clave} | hojas={len(hojas_finales)} | madres={madres_antes}"
    )

    base = copy.deepcopy(hojas_finales)
    costo = float(costo_total_lote or 0.0)

    try:
        redist, ahorro = _redistribuir_placas_subutilizadas_arga(
            copy.deepcopy(base),
            kerf,
            margin,
            opt,
            corner,
            mc_iterations=ARGA_POST_MC_ITERATIONS,
            accesorios_retries=ARGA_POST_ACC_RETRIES,
            clave=clave,
            solo_absorcion=True,
        )
        ok, msg, redist_ok = _validar_inventario_hojas(
            piezas, redist, clave=clave, kerf_global=kerf
        )
        if ok:
            base = redist_ok
            costo = max(0.0, costo - float(ahorro or 0.0))
            _dbg_nesting(
                f"[POST-ARGA-REDIST-OK] clave={clave} | ahorro=${float(ahorro or 0):.2f} | "
                f"hojas={len(base)}"
            )
        else:
            _dbg_nesting(f"[POST-ARGA-REDIST-ROLLBACK] clave={clave} | {msg}")
    except Exception as exc:
        _dbg_nesting(f"[POST-ARGA-REDIST-ERR] clave={clave} | {exc}")

    return base, costo


def _placas_que_caben_pieza(placas, max_req, min_req, tol=10.0):
    out = []
    for p in placas or []:
        max_p, min_p = max(p["w"], p["h"]), min(p["w"], p["h"])
        if max_p >= (max_req - tol) and min_p >= (min_req - tol):
            out.append(p)
    return out


def _estimar_costo_lookahead(
    restos_est,
    restos_acc,
    placas_validas,
    config_kerf,
    config_margin,
    config_opt,
    config_corner,
    mc_fast,
):
    """Simula una placa siguiente barata para penalizar candidatas que dejan muchos restos."""
    if not placas_validas:
        return 0.0
    if not restos_est and not restos_acc:
        return 0.0

    target = restos_est[0] if restos_est else restos_acc[0]
    poly = target.get("poly")
    if poly is None:
        return float(len(restos_est) + len(restos_acc)) * 50.0

    minx, miny, maxx, maxy = poly.bounds
    max_req = max(maxx - minx, maxy - miny)
    min_req = min(maxx - minx, maxy - miny)
    candidatos = _placas_que_caben_pieza(placas_validas, max_req, min_req)
    if not candidatos:
        return float(len(restos_est) + len(restos_acc)) * 80.0

    candidatos = sorted(candidatos, key=lambda x: (x.get("precio_lb", 0), x.get("precio", 0)))[:3]
    mejor_extra = float("inf")
    for p2 in candidatos:
        if restos_est:
            h2, r2 = _safe_empaquetar_una_hoja_mc(
                restos_est,
                p2["w"],
                p2["h"],
                config_kerf,
                config_margin,
                config_opt,
                config_corner,
                debug_tag="lookahead-est",
                mc_iterations=mc_fast,
            )
            restos_count = len(r2) + len(restos_acc)
        else:
            h2, r2 = _safe_empaquetar_una_hoja_mc(
                restos_acc,
                p2["w"],
                p2["h"],
                config_kerf,
                config_margin,
                config_opt,
                config_corner,
                debug_tag="lookahead-acc",
                mc_iterations=mc_fast,
            )
            restos_count = len(r2)

        if not h2.get("piezas"):
            continue
        extra = float(p2.get("precio", 0.0) or 0.0)
        extra += score_placa_simulacion(p2, h2, restos_count=restos_count) * 0.50
        mejor_extra = min(mejor_extra, extra)

    if mejor_extra == float("inf"):
        return float(len(restos_est) + len(restos_acc)) * 60.0
    return mejor_extra


def _safe_empaquetar_una_hoja_mc(
    piezas,
    w_placa,
    h_placa,
    kerf_override=DEFAULT_KERF_IN,
    margin_override=DEFAULT_MARGIN_IN,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_poly=None,
    debug_tag="",
    mc_iterations=None,
    cancel_checker=None,
):
    from .nest_poka_yoke import marcar_pack_fault

    hoja_vacia = {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0}
    restos_default = list(piezas or [])
    cc = cancel_checker if cancel_checker is not None else _active_pack_cancel_checker()

    clave_tok = None
    try:
        from .giga_cal11_galv import clave_desde_debug_tag
        from .nest_engine_context import get_pack_group_clave, set_pack_group_clave

        clave_pack = clave_desde_debug_tag(debug_tag) or get_pack_group_clave()
        if clave_pack:
            clave_tok = set_pack_group_clave(clave_pack)
    except Exception:
        clave_tok = None

    try:
        result = empaquetar_una_hoja_mc(
            piezas,
            w_placa,
            h_placa,
            kerf_override,
            margin_override,
            opt_override,
            corner_override,
            limite_poly=limite_poly,
            mc_iterations=mc_iterations,
            cancel_checker=cc,
        )

        if result is None:
            _dbg_nesting(f"[SAFE-EMPAQUE-NONE] {debug_tag} | resultado=None")
            return marcar_pack_fault(hoja_vacia, "resultado_none"), restos_default

        if not isinstance(result, (tuple, list)) or len(result) != 2:
            _dbg_nesting(f"[SAFE-EMPAQUE-FORMATO-INVALIDO] {debug_tag} | tipo={type(result).__name__}")
            return (
                marcar_pack_fault(hoja_vacia, f"formato_invalido:{type(result).__name__}"),
                restos_default,
            )

        hoja, restos = result

        if hoja is None:
            _dbg_nesting(f"[SAFE-EMPAQUE-HOJA-NONE] {debug_tag}")
            hoja = hoja_vacia

        if restos is None:
            _dbg_nesting(f"[SAFE-EMPAQUE-RESTOS-NONE] {debug_tag}")
            restos = restos_default

        # Sellar kerf/margin de tabla en la hoja y pokayoke fail-closed.
        if isinstance(hoja, dict):
            try:
                hoja["kerf_usado"] = float(kerf_override or 0.0)
            except Exception:
                pass
            try:
                hoja["margin_usado"] = float(margin_override or 0.0)
            except Exception:
                pass
            hoja.setdefault("placa_w", float(w_placa or 0))
            hoja.setdefault("placa_h", float(h_placa or 0))
            if hoja.get("piezas"):
                from .nest_poka_yoke import (
                    colocar_piezas_cerca_origen,
                    reparar_separacion_minima_hoja,
                    validar_separacion_minima_hoja,
                )

                ok_gap, detail_gap = validar_separacion_minima_hoja(
                    hoja,
                    float(kerf_override or 0.0),
                    margin_in=float(margin_override or 0.0),
                    w_placa=float(w_placa or 0.0),
                    h_placa=float(h_placa or 0.0),
                )
                if not ok_gap:
                    # Renest/recalc: el packer ya colocó; no rearmar el nido.
                    es_renest = False
                    try:
                        from .nest_engine_context import is_ultra_renest_accept_mode

                        es_renest = bool(is_ultra_renest_accept_mode()) or (
                            "recalc" in str(debug_tag or "").lower()
                        )
                    except Exception:
                        es_renest = "recalc" in str(debug_tag or "").lower()
                    ok_fix, det_fix, expulsadas = reparar_separacion_minima_hoja(
                        hoja,
                        float(kerf_override or 0.0),
                        margin_in=float(margin_override or 0.0),
                        w_placa=float(w_placa or 0.0),
                        h_placa=float(h_placa or 0.0),
                        permitir_expulsar=not es_renest,
                    )
                    if ok_fix and hoja.get("piezas"):
                        if expulsadas and not es_renest:
                            still = colocar_piezas_cerca_origen(
                                hoja,
                                expulsadas,
                                kerf_in=float(kerf_override or 0.0),
                                margin_in=float(margin_override or 0.0),
                                w_placa=float(w_placa or 0.0),
                                h_placa=float(h_placa or 0.0),
                            )
                            if still:
                                msg_r = (
                                    f"[POKA-KERF-REPAIR] {debug_tag} | "
                                    f"expulsadas={len(still)} | was={detail_gap}"
                                )
                                print(msg_r, flush=True)
                                _dbg_nesting(msg_r)
                                restos = list(restos or []) + _piezas_expulsadas_a_pool(
                                    still
                                )
                            else:
                                msg_r = (
                                    f"[POKA-KERF-NUDGE] {debug_tag} | "
                                    f"reinject={len(expulsadas)} | was={detail_gap}"
                                )
                                print(msg_r, flush=True)
                                _dbg_nesting(msg_r)
                        elif "ok_separado" in str(det_fix or ""):
                            msg_r = (
                                f"[POKA-KERF-NUDGE] {debug_tag} | "
                                f"{det_fix} | was={detail_gap}"
                            )
                            print(msg_r, flush=True)
                            _dbg_nesting(msg_r)
                    else:
                        # No vaciar la hoja: P03/0.5 cabía; el fail era el repair
                        # apuntando al homónimo equivocado. Conservar lo colocado.
                        if hoja.get("piezas"):
                            if expulsadas and not es_renest:
                                restos = list(restos or []) + _piezas_expulsadas_a_pool(
                                    expulsadas
                                )
                            msg = (
                                f"[POKA-KERF-REPAIR] {debug_tag} | parcial "
                                f"expulsadas={len(expulsadas)} quedan={len(hoja.get('piezas') or [])} "
                                f"| was={detail_gap}"
                            )
                            print(msg, flush=True)
                            _dbg_nesting(msg)
                        else:
                            msg = (
                                f"[POKA-KERF-FAIL] {debug_tag} | kerf={kerf_override} "
                                f"margin={margin_override} | {detail_gap}"
                            )
                            print(msg, flush=True)
                            _dbg_nesting(msg)
                            return (
                                marcar_pack_fault(
                                    {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0},
                                    f"kerf_gap:{detail_gap}",
                                ),
                                restos_default,
                            )

        return hoja, restos

    except Exception as e:
        _dbg_nesting(f"[SAFE-EMPAQUE-EXCEPTION] {debug_tag} | {e}")
        return marcar_pack_fault(hoja_vacia, str(e)), restos_default
    finally:
        if clave_tok is not None:
            try:
                from .nest_engine_context import reset_pack_group_clave

                reset_pack_group_clave(clave_tok)
            except Exception:
                pass

class MotorNesting:
    def __init__(self):
        self.margen_corte = 0.2 * 25.4
        self.escala_dxf = 25.4
        self._cancel_checker = None
        self.orientacion_cobre_por_ruta = {}
        self.cu_especial_por_ruta = {}
        self.plasma_compensada_por_ruta = {}
        self.plasma_dxf_por_ruta = {}
        self.orientacion_corte_por_ruta = {}
        self.orientacion_corte_bloqueada_por_ruta = {}
        self.active_engine_id = get_active_engine_id()
        self._ultima_comparacion_motores = None
        self._remnant_ids_consumidos: set[str] = set()
        try:
            profile = get_engine_profile(self.active_engine_id)
            mode = str(os.environ.get("ARGA_NEST_MODE", "first")).strip().lower()
            ready = [m.engine_id for m in list_engine_metas() if is_engine_ready(m.engine_id)]
            print(
                f"[NESTING ENGINE] active={self.active_engine_id} | "
                f"backend={nesting_engine_name()} | ready={ready} | "
                f"mode={mode} mc={profile.get('mc_iterations')} "
                f"lookahead={profile.get('lookahead')} refine={profile.get('refine_hoja')} | "
                f"hw cpus={profile.get('logical_cpus')} "
                f"threads={profile.get('nest_threads')} "
                f"ultra_pop={profile.get('ga_population')} "
                f"force_seeds={profile.get('force_parallel_seeds')} "
                f"ram={float(profile.get('ram_gb') or 0):.1f}GB"
            )
        except Exception:
            try:
                print(f"[NESTING ENGINE] backend={nesting_engine_name()}")
            except Exception:
                pass

    def set_cancel_checker(self, fn):
        self._cancel_checker = fn

    def __getstate__(self):
        state = self.__dict__.copy()
        # El checker apunta a métodos Qt (SistemaNestingPro) y no es serializable.
        state["_cancel_checker"] = None
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self._cancel_checker = state.get("_cancel_checker")

    def _cancelado(self) -> bool:
        try:
            return bool(self._cancel_checker and self._cancel_checker())
        except Exception:
            return False

    def recuperar_geometria_robusta(self, ruta):
        return recuperar_geometria_robusta(ruta)

    def empaquetar_una_hoja_mc(
        self,
        piezas,
        w_placa,
        h_placa,
        kerf_override=DEFAULT_KERF_IN,
        margin_override=DEFAULT_MARGIN_IN,
        opt_override="OPTIMIZAR LARGO Y ANCHO",
        corner_override="INFERIOR IZQUIERDA",
        limite_poly=None,
    ):
        return _safe_empaquetar_una_hoja_mc(
            piezas,
            w_placa,
            h_placa,
            kerf_override,
            margin_override,
            opt_override,
            corner_override,
            limite_poly=limite_poly,
            debug_tag="empaque_mc_ui",
            cancel_checker=self._cancelado,
        )

    def empaquetar_con_reintentos(
        self,
        piezas,
        w_placa,
        h_placa,
        kerf_override=DEFAULT_KERF_IN,
        margin_override=DEFAULT_MARGIN_IN,
        opt_override="OPTIMIZAR LARGO Y ANCHO",
        corner_override="INFERIOR IZQUIERDA",
        limite_poly=None,
        intentos=8,
        debug_tag="empaque_reintentos",
    ):
        """
        Reempaqueta piezas con varios intentos (orden + shuffle) hasta colocar todas
        o devolver el mejor resultado parcial. Usado al recalcular/renestear placas.
        """
        if not piezas:
            return None

        w = float(w_placa or 0)
        h = float(h_placa or 0)
        if w <= 0 or h <= 0:
            return None

        base = sorted(
            [copy.deepcopy(p) for p in piezas],
            key=lambda x: float(x.get("area", 0) or 0),
            reverse=True,
        )
        n = max(1, int(intentos or 1))
        mc_iters = int(get_nest_profile().get("mc_iterations", 15))
        try:
            from .giga_cal11_galv import clave_desde_debug_tag, should_force_giga_engine

            if should_force_giga_engine(clave_desde_debug_tag(debug_tag)):
                n = 1
                mc_iters = 1
        except Exception:
            pass
        mejor_parcial = None
        mejor_area = -1.0
        mejor_resto_n = len(base) + 1
        t0_pack = time.perf_counter()

        for intento in range(n):
            if self._cancelado():
                break

            if intento == 0:
                batch = base
            else:
                batch = base.copy()
                random.shuffle(batch)
                batch.sort(key=lambda x: float(x.get("area", 0) or 0), reverse=True)

            t_try = time.perf_counter()
            nh, sobras = _safe_empaquetar_una_hoja_mc(
                batch,
                w,
                h,
                kerf_override,
                margin_override,
                opt_override,
                corner_override,
                limite_poly=limite_poly,
                debug_tag=f"{debug_tag}|try={intento + 1}",
                mc_iterations=mc_iters,
                cancel_checker=self._cancelado,
            )
            if not nh:
                continue

            n_sob = len(sobras or [])
            area = float(nh.get("area_usada", 0) or 0)
            n_ok = len(nh.get("piezas") or [])
            print(
                f"[EMPAQUE-TRY] {debug_tag}|try={intento + 1}/{n} | "
                f"colocadas={n_ok} restos={n_sob} | "
                f"{time.perf_counter() - t_try:.1f}s "
                f"(total {time.perf_counter() - t0_pack:.1f}s)",
                flush=True,
            )
            if not sobras:
                return actualizar_eficiencias_hoja(nh)

            if n_sob < mejor_resto_n or (n_sob == mejor_resto_n and area > mejor_area):
                mejor_resto_n = n_sob
                mejor_area = area
                mejor_parcial = nh

        return actualizar_eficiencias_hoja(mejor_parcial) if mejor_parcial else None

    def recalcular_hoja_full(
        self,
        hoja_data,
        nuevo_kerf,
        nuevo_margen,
        nueva_opt,
        nueva_esquina,
    ):
        """
        Reempaqueta todas las piezas reales de una hoja a partir de sus polígonos
        serializados (cuando no hay rutas DXF en datos_partes_actuales).
        """
        piezas_a_reprocesar = []
        for p in hoja_data.get("piezas") or []:
            nom = str(p.get("nombre", "") or "")
            if nom.startswith("REMANENTE__"):
                continue
            poly = reconstruir_poly_seguro(p.get("poligonos") or [])
            marks = reconstruir_marks(p.get("marcas") or [])
            if poly is None or poly.is_empty:
                continue
            minx, miny, _, _ = poly.bounds
            poly_norm = affinity.translate(poly, -minx, -miny)
            marks_norm = (
                affinity.translate(marks, -minx, -miny) if not marks.is_empty else marks
            )
            piezas_a_reprocesar.append(
                {
                    "nombre": nom,
                    "poly": poly_norm,
                    "marks": marks_norm,
                    "area": float(p.get("area", poly.area) or poly.area),
                    "calibre": p.get("calibre", ""),
                    "material": p.get("material", ""),
                }
            )

        if not piezas_a_reprocesar:
            return None

        piezas_a_reprocesar.sort(key=lambda x: x["area"], reverse=True)
        
        # --- EDDIE AI + HIVE ML (cualquier motor) ---
        import os
        try:
            from modules.nesting_engine.ai_heuristic import smart_seed_order
            from modules.nesting_engine.hive_mind_nests import (
                force_eddie_policy,
                suggest_seed_policy,
            )

            _engine_id = os.environ.get("ARGA_MOTOR_NESTING", "svgnest_ultra")
            sug = suggest_seed_policy(
                piezas_a_reprocesar,
                w_placa=float(hoja_data.get("placa_w", 0) or 0),
                h_placa=float(hoja_data.get("placa_h", 0) or 0),
                kerf=float(hoja_data.get("kerf_usado", 0.15) or 0.15),
            )
            force_eddie_policy(
                str(_engine_id), str(sug.get("policy") or "host_parasite")
            )
            piezas_a_reprocesar = smart_seed_order(
                piezas_a_reprocesar, engine_id=_engine_id
            )
        except Exception as e:
            import traceback
            with open(r"c:\Proyectos\New Arga Nesting Suite\_logs\eddie_debug.log", "a") as f:
                f.write(f"ERROR EN EDDIE MANUAL: {e}\n{traceback.format_exc()}\n")
        # --------------------------------

        w = float(hoja_data.get("placa_w", 0) or 0)
        h = float(hoja_data.get("placa_h", 0) or 0)
        if w <= 0 or h <= 0:
            return None

        mejor_resultado = None
        for _ in range(3):
            nh, sobras = _safe_empaquetar_una_hoja_mc(
                piezas_a_reprocesar,
                w,
                h,
                nuevo_kerf,
                nuevo_margen,
                nueva_opt,
                nueva_esquina,
                debug_tag="recalc_hoja_full",
            )
            if not sobras:
                mejor_resultado = nh
                break

        if not mejor_resultado:
            return None
            
        # --- Lite hole-fill + Venom (re-nesteo manual) ---
        import os
        try:
            from .venom_hole_fill import apply_lite_hole_fill

            engine_id = os.environ.get("ARGA_MOTOR_NESTING", "svgnest_ultra")
            apply_lite_hole_fill(mejor_resultado, engine_id=engine_id)
        except Exception:
            pass
        try:
            from . import venom_ai
            engine_id = os.environ.get("ARGA_MOTOR_NESTING", "svgnest_ultra")
            venom_ai.apply_smart_polisher(mejor_resultado, engine_id)
        except Exception as e:
            import traceback
            with open(r"c:\Proyectos\New Arga Nesting Suite\_logs\venom_debug.log", "a") as f:
                f.write(f"ERROR EN VENOM MANUAL: {e}\n{traceback.format_exc()}\n")
        # --------------------------------------------

        mejor_resultado.update(
            {
                "placa_id": hoja_data.get("placa_id"),
                "placa_w": w,
                "placa_h": h,
                "precio_placa": hoja_data.get("precio_placa", 0),
                "kerf_usado": nuevo_kerf,
                "margin_usado": nuevo_margen,
                "opt_usado": nueva_opt,
                "corner_usado": nueva_esquina,
            }
        )
        for meta_k in (
            "origen_placa",
            "es_retazo",
            "id_remanente_usado",
            "lote_desc",
            "lote_mult",
            "poly_borde_retazo",
        ):
            if meta_k in hoja_data:
                mejor_resultado[meta_k] = hoja_data[meta_k]
        return actualizar_eficiencias_hoja(mejor_resultado)

    @staticmethod
    def _extraer_numero(valor):
        try:
            if isinstance(valor, (int, float)): return float(valor)
            limpio = str(valor).replace('$', '').replace(',', '.').strip()
            nums = re.findall(r"[-+]?\d*\.\d+|\d+", limpio)
            return float(nums[0]) if nums else 0.0
        except: return 0.0

    @staticmethod
    def _thickness_inches_for_match(value):
        """Pulgadas de espesor; si el texto es calibre entero (11, 12…) usa tabla acero."""
        txt = str(value or "").strip().replace(",", ".")
        if not txt:
            return None
        if re.fullmatch(r"\d{1,2}", txt):
            try:
                from modules.herinox_sync import HerinoxPlateSync

                mapped = HerinoxPlateSync.STEEL_GAUGE_TO_INCHES.get(int(txt))
                if mapped is not None:
                    return float(mapped)
            except Exception:
                pass
        return MotorNesting._parse_thickness_value(txt)

    @staticmethod
    def _nearest_steel_gauge(thk_in: float) -> int | None:
        try:
            from modules.herinox_sync import HerinoxPlateSync

            tabla = HerinoxPlateSync.STEEL_GAUGE_TO_INCHES
        except Exception:
            return None
        best_g = None
        best_d = 1e9
        for g, inches in tabla.items():
            d = abs(float(inches) - float(thk_in))
            if d < best_d:
                best_d = d
                best_g = int(g)
        if best_g is None or best_d > 0.008:
            return None
        return best_g

    @staticmethod
    def _espesor_exacto(val1, val2) -> bool:
        """Mismo calibre (abs ≤ 0.005\" o mismo gauge nominal). No cruza 11↔12."""
        v1 = str(val1 or "").strip().upper()
        v2 = str(val2 or "").strip().upper()
        if v1 and v1 == v2:
            return True
        n1 = MotorNesting._thickness_inches_for_match(v1)
        n2 = MotorNesting._thickness_inches_for_match(v2)
        if n1 is None or n2 is None:
            return False
        if abs(n1 - n2) <= float(THICKNESS_EXACT_ABS_IN):
            return True
        g1 = MotorNesting._nearest_steel_gauge(n1)
        g2 = MotorNesting._nearest_steel_gauge(n2)
        return g1 is not None and g1 == g2

    @staticmethod
    def _espesor_con_tolerancia(val1, val2) -> bool:
        """LEGACY: ya no hay fallback de tolerancia. Solo exacto."""
        return MotorNesting._espesor_exacto(val1, val2)

    @staticmethod
    def _coinciden(val1, val2):
        """
        Material: familia normalizada.
        Espesor/calibre: SOLO exacto (no cruza gauges vecinos).
        """
        v1 = str(val1).strip().upper()
        v2 = str(val2).strip().upper()
        if v1 == v2:
            return True

        n1 = MotorNesting._thickness_inches_for_match(v1)
        n2 = MotorNesting._thickness_inches_for_match(v2)
        if n1 is not None and n2 is not None:
            return MotorNesting._espesor_exacto(v1, v2)

        try:
            from modules.consulta_herinox_bridge import normalize_material

            m1 = normalize_material(v1).strip().upper()
            m2 = normalize_material(v2).strip().upper()
            if m1 and m2 and m1 == m2:
                return True
        except Exception:
            pass

        tiene_numeros = any(char.isdigit() for char in v1) or any(
            char.isdigit() for char in v2
        )
        if not tiene_numeros:
            if v1 in v2 or v2 in v1:
                return True
        return False

    @staticmethod
    def _delta_espesor(req_cal, p_cal) -> float:
        n1 = MotorNesting._thickness_inches_for_match(req_cal)
        n2 = MotorNesting._thickness_inches_for_match(p_cal)
        if n1 is None or n2 is None:
            return 999.0
        return abs(n1 - n2)

    def _clasificar_placas_por_calibre(
        self, req_cal, req_mat, datos_placas
    ) -> tuple[list[dict], str]:
        """
        Solo placas del MISMO calibre (exacto).
        Sin tolerancia %: no saltar 11↔12 ni reclasificar el grupo.
        """
        placas_exactas_emp: list[dict] = []
        placas_exactas_prov: list[dict] = []

        for placa in datos_placas or []:
            if not stock_permite_nesting(
                placa[8] if isinstance(placa, (list, tuple)) and len(placa) > 8 else ""
            ):
                continue
            try:
                p_cal = placa[0]
                p_mat = placa[1]
            except Exception:
                continue
            if not self._coinciden(req_mat, p_mat):
                continue

            w_in = self._extraer_numero(placa[3])
            h_in = self._extraer_numero(placa[4])
            if w_in <= 0 or h_in <= 0:
                continue

            placa_id = str(placa[2])
            consumidos = getattr(self, "_remnant_ids_consumidos", None) or set()
            if placa_id in consumidos:
                continue

            libras_totales_placa = (
                self._extraer_numero(placa[5]) if len(placa) > 5 else 0.0
            )
            origen_placa = str(placa[9]).upper() if len(placa) > 9 else "EMPRESA"
            precio_mxn = self._extraer_numero(placa[6]) if len(placa) > 6 else 0.0
            precio_usd_lb = (
                self._extraer_numero(placa[10])
                if len(placa) > 10
                else (self._extraer_numero(placa[7]) if len(placa) > 7 else 0.0)
            )
            costo_placa_completa = (
                precio_mxn
                if precio_mxn > 0
                else (libras_totales_placa * precio_usd_lb)
            )
            es_rem = (
                "REMANENTE" in origen_placa
                or placa_id.upper().startswith("PL-")
                or placa_id.upper().startswith("REM-")
            )
            # Preferir remanentes: precio efectivo bajo + flag de orden
            if es_rem and costo_placa_completa <= 0:
                costo_placa_completa = max(1.0, w_in * h_in * 0.01)
            datos_placa_dict = {
                "data": placa,
                "w": w_in * 25.4,
                "h": h_in * 25.4,
                "precio": costo_placa_completa,
                "id": placa_id,
                "origen": origen_placa,
                "precio_lb": 0.0 if es_rem else precio_usd_lb,
                "calibre": str(p_cal).strip(),
                "delta_thk": self._delta_espesor(req_cal, p_cal),
                "es_remanente": es_rem,
            }

            es_emp = (
                "EMPRESA" in origen_placa
                or "REMANENTE" in origen_placa
                or origen_placa.strip() == ""
            )
            if self._espesor_exacto(req_cal, p_cal):
                (placas_exactas_emp if es_emp else placas_exactas_prov).append(
                    datos_placa_dict
                )

        exactas = placas_exactas_emp if placas_exactas_emp else placas_exactas_prov
        if exactas:
            exactas.sort(
                key=lambda x: (
                    0 if x.get("es_remanente") else 1,
                    x.get("delta_thk", 999.0),
                    x["precio_lb"],
                    x["precio"],
                )
            )
            return exactas, "exacto"

        # Sin stock del calibre pedido: no improvisar con gauge vecino.
        return [], "exacto"

    @staticmethod
    def _merge_resultado_en_mapa(resultados: dict, clave: str, nuevo: dict) -> None:
        """Fusiona hojas si la clave ya existe (salto de calibre → grupo destino)."""
        if not clave:
            return
        prev = resultados.get(clave)
        if (
            isinstance(prev, dict)
            and isinstance(nuevo, dict)
            and "hojas" in prev
            and "hojas" in nuevo
            and not prev.get("error")
            and not nuevo.get("error")
        ):
            hojas = list(prev.get("hojas") or []) + list(nuevo.get("hojas") or [])
            merged = dict(prev)
            merged["hojas"] = hojas
            # Unir demanda: si no se concatena el pool, el poka-yoke cree que
            # faltan piezas del segundo lote (plasma/láser) tras la fusión.
            if prev.get("piezas_pool_engine") or nuevo.get("piezas_pool_engine"):
                pool = list(prev.get("piezas_pool") or []) + list(
                    nuevo.get("piezas_pool") or []
                )
                merged["piezas_pool"] = pool
                merged["piezas_pool_engine"] = True
            pend = list(prev.get("piezas_pendientes") or []) + list(
                nuevo.get("piezas_pendientes") or []
            )
            if pend:
                merged["piezas_pendientes"] = pend
            merged["costo_total"] = float(prev.get("costo_total") or 0) + float(
                nuevo.get("costo_total") or 0
            )
            merged["costo_empresa"] = float(prev.get("costo_empresa") or 0) + float(
                nuevo.get("costo_empresa") or 0
            )
            merged["costo_proveedor"] = float(prev.get("costo_proveedor") or 0) + float(
                nuevo.get("costo_proveedor") or 0
            )
            try:
                from .rtz_overlays import sincronizar_overlays_grupo
                from .efficiency_metrics import calcular_eficiencias_grupo

                sincronizar_overlays_grupo(hojas)
                merged.update(calcular_eficiencias_grupo(hojas))
            except Exception:
                pass
            resultados[clave] = merged
            return
        resultados[clave] = nuevo

    @staticmethod
    def _partir_piezas_plasma(piezas):
        plasma, normal = [], []
        for p in piezas or []:
            if isinstance(p, dict) and p.get("plasma_compensada_manual"):
                plasma.append(p)
            else:
                normal.append(p)
        return normal, plasma

    @staticmethod
    def _jobs_acero_separando_plasma(grupos_acero_ord):
        """Un job por lote; si hay plasma PARTS, primero lote plasma y luego el resto."""
        jobs = []
        for clave, piezas in grupos_acero_ord:
            normal, plasma = MotorNesting._partir_piezas_plasma(piezas)
            if plasma:
                jobs.append((clave, plasma, True))
            if normal:
                jobs.append((clave, normal, False))
        return jobs

    @staticmethod
    def _marcar_resultado_lote_plasma(resultado_grupo):
        """Marca hojas de un lote nestado solo con piezas compensadas (PARTS)."""
        if not isinstance(resultado_grupo, dict) or resultado_grupo.get("error"):
            return resultado_grupo
        hojas = resultado_grupo.get("hojas") or []
        for h in hojas:
            off = 0.0
            n_comp = 0
            for pz in h.get("piezas") or []:
                if not isinstance(pz, dict):
                    continue
                nom = str(pz.get("nombre") or "")
                if nom.startswith(
                    ("REF__", "TATUAJE__", "RETAZO_GUILLOTINA__", "CU_CORTE__", "REMANENTE__")
                ):
                    continue
                pz["plasma_compensada_manual"] = True
                off_pz = float(pz.get("plasma_offset_mm_manual") or 0.0)
                if off_pz > 0:
                    off = off_pz
                n_comp += 1
            if n_comp > 0:
                h["plasma_compensado_manual"] = True
                if off > 0:
                    h["plasma_offset_mm_manual"] = off
                h["plasma_piezas_compensadas"] = int(n_comp)
        return resultado_grupo

    @staticmethod
    def _parse_thickness_value(value):
        txt = str(value or "").strip().replace(",", ".")
        if not txt:
            return None
        if re.search(r"[A-Z]", txt.upper()):
            return None
        try:
            # Formato mixto: "1 1/2"
            if " " in txt and "/" in txt:
                whole, frac = txt.split(" ", 1)
                num, den = frac.split("/", 1)
                den_f = float(den)
                if abs(den_f) < 1e-12:
                    return None
                return float(whole) + (float(num) / den_f)
            # Fracción simple: "3/16"
            if "/" in txt:
                num, den = txt.split("/", 1)
                den_f = float(den)
                if abs(den_f) < 1e-12:
                    return None
                return float(num) / den_f
            return float(txt)
        except Exception:
            return None

    def ejecutar_nesting_visual(
        self,
        lista_partes,
        datos_placas,
        progress_callback=None,
        config_kerf=DEFAULT_KERF_IN,
        config_margin=DEFAULT_MARGIN_IN,
        config_corner="INFERIOR IZQUIERDA",
        config_opt="OPTIMIZAR LARGO Y ANCHO",
        wo_name="PENDIENTE",
        engine_id=None,
        plate_selection=None,
    ):
        def notificar(msg, porcentaje):
            if progress_callback: progress_callback(msg, porcentaje)

        engine_token = None
        resolved_engine = normalize_engine_id(engine_id or self.active_engine_id)
        try:
            engine_token = set_active_engine_id(resolved_engine)
            self.active_engine_id = resolved_engine
        except Exception:
            pass

        allowed, limits = _parse_plate_selection(plate_selection)
        self._plate_formats_allowed = allowed
        self._plate_format_limits = limits
        self._plate_format_used = {}
        self._remnant_ids_consumidos = set()
        self._last_wo_name = str(wo_name or "")

        def _release_engine_context():
            if engine_token is not None:
                from .nest_engine_context import reset_active_engine_id

                reset_active_engine_id(engine_token)
            self._plate_formats_allowed = None
            self._plate_format_limits = None
            self._plate_format_used = {}

        if not lista_partes:
            _release_engine_context()
            return {"error": "Lista vacía."}

        try:
            result = self._ejecutar_nesting_visual_core(
                lista_partes,
                datos_placas,
                progress_callback=progress_callback,
                config_kerf=config_kerf,
                config_margin=config_margin,
                config_corner=config_corner,
                config_opt=config_opt,
                wo_name=wo_name,
                resolved_engine=resolved_engine,
            )
            try:
                from .ai_heuristic import ai_learn_from_feedback
                ai_learn_from_feedback()
            except Exception:
                pass
            try:
                from .ai_ranker import train_from_telemetry
                from .ai_telemetry import ai_ranker_enabled, summarize

                if ai_ranker_enabled():
                    tr = train_from_telemetry(min_events=8)
                    try:
                        from .ai_ranker import last_policy, record_policy_reward

                        # Refuerzo inmediato del nest actual si hay efi en result
                        efi_now = 0.0
                        if isinstance(result, dict):
                            efi_now = float(
                                (result.get("eficiencia") or result.get("efi") or 0) or 0
                            )
                        if efi_now > 0.5 and last_policy():
                            record_policy_reward(last_policy(), efi_now)
                    except Exception:
                        pass
                    print(f"[AI-RANKER] train={tr.get('ok')} bandit_upd={tr.get('bandit_updates')}", flush=True)
                print(f"[AI-TELEMETRY] {summarize()}", flush=True)
            except Exception:
                pass
            return result
        finally:
            _release_engine_context()

    def _ejecutar_nesting_visual_core(
        self,
        lista_partes,
        datos_placas,
        progress_callback=None,
        config_kerf=DEFAULT_KERF_IN,
        config_margin=DEFAULT_MARGIN_IN,
        config_corner="INFERIOR IZQUIERDA",
        config_opt="OPTIMIZAR LARGO Y ANCHO",
        wo_name="PENDIENTE",
        resolved_engine=None,
    ):
        def notificar(msg, porcentaje):
            if progress_callback:
                progress_callback(msg, porcentaje)

        resolved_engine = normalize_engine_id(resolved_engine or get_active_engine_id())
        total_dxf = len(lista_partes)

        try:
            if os.path.exists(DEBUG_LOG_NESTING):
                os.remove(DEBUG_LOG_NESTING)
        except Exception:
            pass

        _dbg_nesting("============================================================")
        _dbg_nesting("[NUEVA-EJECUCION] Inicio de corrida de nesting")
        _dbg_nesting(
            f"[PARAMS] engine={resolved_engine} | kerf={config_kerf} | margin={config_margin} | "
            f"corner={config_corner} | opt={config_opt} | wo={wo_name}"
        )
        _dbg_nesting("============================================================")
        grupos = {}
        piezas_parser_fallidas = []
        
        for i, (pieza, mat, qty, cal, st, ruta) in enumerate(lista_partes):
            notificar(f"Analizando geometría: {pieza}...", (i / total_dxf) * 0.15)
            clave = f"{str(cal).strip().upper()}_{str(mat).strip().upper()}"
            if clave not in grupos:
                grupos[clave] = []

            _dbg_nesting(
                f"[PRE-PARSER] clave={clave} | pieza={pieza} | qty={qty} | status={st} | ruta={ruta}"
            )

            ruta_parse = ruta
            plasma_flag = False
            plasma_off = 0.0
            ruta_plasma = ""
            if not es_material_cobre(mat):
                clave_ruta = clave_orientacion_cobre_ruta(ruta)
                plasma_flag = bool(
                    (getattr(self, "plasma_compensada_por_ruta", {}) or {}).get(
                        clave_ruta, False
                    )
                )
            if plasma_flag:
                try:
                    from modules.plasma_compensator import (
                        asegurar_dxf_plasma_compensado,
                        compute_plasma_offset_mm,
                    )

                    thk_in = self._parse_thickness_value(cal)
                    if thk_in is None:
                        thk_in = float(self._extraer_numero(cal) or 0.0)
                    plasma_off = float(compute_plasma_offset_mm(float(thk_in or 0.0)))
                    mapa_dxf = getattr(self, "plasma_dxf_por_ruta", {}) or {}
                    prec = str(mapa_dxf.get(clave_ruta) or "").strip()
                    if prec and os.path.isfile(prec):
                        ruta_parse = prec
                        ruta_plasma = prec
                    else:
                        out_dxf, err_dxf = asegurar_dxf_plasma_compensado(
                            ruta, plasma_off
                        )
                        if out_dxf and os.path.isfile(out_dxf):
                            ruta_parse = out_dxf
                            ruta_plasma = out_dxf
                            if not hasattr(self, "plasma_dxf_por_ruta") or self.plasma_dxf_por_ruta is None:
                                self.plasma_dxf_por_ruta = {}
                            self.plasma_dxf_por_ruta[clave_ruta] = out_dxf
                        else:
                            plasma_flag = False
                            plasma_off = 0.0
                            _dbg_nesting(
                                f"[PLASMA-DXF-FAIL] clave={clave} | pieza={pieza} | {err_dxf}"
                            )
                    if plasma_flag:
                        _dbg_nesting(
                            f"[PLASMA-DXF] clave={clave} | pieza={pieza} | "
                            f"offset_mm={plasma_off:.3f} | src={ruta_parse}"
                        )
                except Exception as exc:
                    plasma_flag = False
                    plasma_off = 0.0
                    ruta_parse = ruta
                    _dbg_nesting(
                        f"[PLASMA-DXF-ERR] clave={clave} | pieza={pieza} | {exc}"
                    )

            poly, marks, err_geom = recuperar_geometria_robusta_detalle(ruta_parse)

            if es_material_cobre(mat):
                rot_deg = int(
                    (getattr(self, "orientacion_cobre_por_ruta", {}) or {}).get(
                        clave_orientacion_cobre_ruta(ruta), 0
                    )
                ) % 360
                if rot_deg:
                    try:
                        cx, cy = poly.centroid.x, poly.centroid.y
                        poly = affinity.rotate(poly, rot_deg, origin=(cx, cy), use_radians=False)
                        if marks is not None and not marks.is_empty:
                            marks = affinity.rotate(marks, rot_deg, origin=(cx, cy), use_radians=False)
                        _dbg_nesting(
                            f"[COBRE-ROT-PARTS] clave={clave} | pieza={pieza} | ruta={ruta} | rot={rot_deg}°"
                        )
                    except Exception as exc:
                        _dbg_nesting(
                            f"[COBRE-ROT-FAIL] clave={clave} | pieza={pieza} | ruta={ruta} | err={exc}"
                        )
            else:
                # Metal: orientación bloqueada en PARTS (visor) → bake + grain_locked.
                clave_ruta = clave_orientacion_cobre_ruta(ruta)
                bloqueada = bool(
                    (getattr(self, "orientacion_corte_bloqueada_por_ruta", {}) or {}).get(
                        clave_ruta, False
                    )
                )
                if bloqueada:
                    rot_deg = int(
                        (getattr(self, "orientacion_corte_por_ruta", {}) or {}).get(
                            clave_ruta, 0
                        )
                    ) % 360
                    if rot_deg:
                        try:
                            cx, cy = poly.centroid.x, poly.centroid.y
                            poly = affinity.rotate(
                                poly, rot_deg, origin=(cx, cy), use_radians=False
                            )
                            if marks is not None and not marks.is_empty:
                                marks = affinity.rotate(
                                    marks, rot_deg, origin=(cx, cy), use_radians=False
                                )
                            _dbg_nesting(
                                f"[ORIENT-LOCK-ROT] clave={clave} | pieza={pieza} | "
                                f"ruta={ruta} | rot={rot_deg}°"
                            )
                        except Exception as exc:
                            _dbg_nesting(
                                f"[ORIENT-LOCK-ROT-FAIL] clave={clave} | pieza={pieza} | "
                                f"ruta={ruta} | err={exc}"
                            )

            if poly is None:
                motivo = err_geom or "recuperar_geometria_robusta devolvió None"
                _dbg_nesting(
                    f"[PARSER-FAIL] clave={clave} | pieza={pieza} | ruta={ruta} | "
                    f"motivo={motivo}"
                )
                piezas_parser_fallidas.append(
                    {
                        "pieza": pieza,
                        "ruta": ruta,
                        "archivo": os.path.basename(str(ruta or "")),
                        "error": motivo,
                    }
                )
                continue

            n_mark_segs = 0
            try:
                if marks is not None and not marks.is_empty:
                    if marks.geom_type == "LineString":
                        n_mark_segs = 1
                    elif marks.geom_type == "MultiLineString":
                        n_mark_segs = len(list(marks.geoms))
            except Exception:
                pass

            _dbg_nesting(
                f"[PARSER-OK] clave={clave} | pieza={pieza} | ruta={ruta} | "
                f"geom_type={_safe_geom_type(poly)} | area={_safe_area(poly):.3f} | "
                f"valid={_safe_is_valid(poly)} | holes={_safe_holes(poly)} | "
                f"bounds={_fmt_bounds(poly)} | {_safe_marks_info(marks)} | "
                f"mark_segs={n_mark_segs}"
            )

            minx, miny, _, _ = poly.bounds

            poly_exact = affinity.translate(poly, -minx, -miny)

            if marks is not None and not marks.is_empty:
                marks_exact = affinity.translate(marks, -minx, -miny)
            else:
                marks_exact = marks

            _dbg_nesting(
                f"[POST-TRANSLATE] clave={clave} | pieza={pieza} | "
                f"orig_minx={minx:.3f} | orig_miny={miny:.3f} | "
                f"bounds_exact={_fmt_bounds(poly_exact)}"
            )

            # Geometría de trabajo para nesting, pero sin destruir la exacta
            poly_nesting = _crear_poly_nesting_seguro(poly_exact)

            _dbg_nesting(
                f"[POST-NESTING-PROXY] clave={clave} | pieza={pieza} | "
                f"geom_type={_safe_geom_type(poly_nesting)} | area={_safe_area(poly_nesting):.3f} | "
                f"valid={_safe_is_valid(poly_nesting)} | holes={_safe_holes(poly_nesting)} | "
                f"bounds={_fmt_bounds(poly_nesting)}"
            )

            if poly_nesting is not None and not poly_nesting.is_valid:
                _dbg_nesting(
                    f"[INVALID-BEFORE-BUFFER0] clave={clave} | pieza={pieza} | "
                    f"area={_safe_area(poly_nesting):.3f} | bounds={_fmt_bounds(poly_nesting)}"
                )
                poly_nesting = poly_nesting.buffer(0)

                _dbg_nesting(
                    f"[POST-BUFFER0] clave={clave} | pieza={pieza} | "
                    f"geom_type={_safe_geom_type(poly_nesting)} | area={_safe_area(poly_nesting):.3f} | "
                    f"valid={_safe_is_valid(poly_nesting)} | holes={_safe_holes(poly_nesting)} | "
                    f"bounds={_fmt_bounds(poly_nesting)}"
                )

            if poly_exact is None or poly_exact.is_empty:
                _dbg_nesting(
                    f"[GEOM-EMPTY-EXACT] clave={clave} | pieza={pieza} | ruta={ruta}"
                )
                piezas_parser_fallidas.append(
                    {
                        "pieza": pieza,
                        "ruta": ruta,
                        "archivo": os.path.basename(str(ruta or "")),
                        "error": "Geometría exacta vacía tras normalizar el DXF.",
                    }
                )
                continue

            if poly_nesting is None or poly_nesting.is_empty:
                poly_nesting = poly_exact

            for idx_qty in range(int(qty)):
                especial_cu = False
                if es_material_cobre(mat):
                    especial_cu = bool(
                        (getattr(self, "cu_especial_por_ruta", {}) or {}).get(
                            clave_orientacion_cobre_ruta(ruta), False
                        )
                    )
                item_pz = {
                    "nombre": pieza,
                    # Geometría exacta al motor: misma malla que exporta/visibiliza (con barrenos).
                    "poly": poly_exact,
                    "marks": marks_exact,
                    "area": poly_exact.area,
                    "calibre": str(cal).strip(),
                    "material": str(mat).strip(),
                    # Láser / fuente 1:1: siempre el Processed original.
                    "ruta": ruta,
                    "orig_minx": minx,
                    "orig_miny": miny,
                    # NUEVO: respaldo exacto para exportación/reconstrucción
                    "poly_exact": poly_exact,
                    "marks_exact": marks_exact,
                    "cu_especial_vertical": especial_cu,
                    "debug_id": f"{clave}::{pieza}::rep{idx_qty + 1}",
                }
                clave_ruta_lock = clave_orientacion_cobre_ruta(ruta)
                if bool(
                    (getattr(self, "orientacion_corte_bloqueada_por_ruta", {}) or {}).get(
                        clave_ruta_lock, False
                    )
                ):
                    # Orientación de PARTS ya horneada en poly: el packer no puede girar más.
                    item_pz["grain_locked"] = True
                    item_pz["allowed_rotations"] = [0]
                    item_pz["orientacion_corte_bloqueada"] = True
                    item_pz["orientacion_corte_deg"] = int(
                        (getattr(self, "orientacion_corte_por_ruta", {}) or {}).get(
                            clave_ruta_lock, 0
                        )
                    ) % 360
                if plasma_flag:
                    item_pz["plasma_compensada_manual"] = True
                    item_pz["plasma_offset_mm_manual"] = float(plasma_off)
                    item_pz["plasma_fuente_ya_compensada"] = True
                    if ruta_plasma:
                        item_pz["ruta_plasma"] = ruta_plasma
                grupos[clave].append(item_pz)

            _dbg_nesting(
                f"[GRUPO-ADD] clave={clave} | pieza={pieza} | qty_insertada={int(qty)} | "
                f"total_grupo={len(grupos[clave])}"
            )

        _dbg_nesting("============================================================")
        _dbg_nesting("[RESUMEN-GRUPOS] Inicio resumen de grupos antes de multiproceso")
        for clave_g, piezas_g in grupos.items():
            nombres_unicos = sorted({p.get("nombre", "SIN_NOMBRE") for p in piezas_g})
            _dbg_nesting(
                f"[RESUMEN-GRUPO] clave={clave_g} | total_piezas={len(piezas_g)} | "
                f"nombres={nombres_unicos}"
            )
        _dbg_nesting("============================================================")
        total_grupos_con_piezas = sum(1 for _, piezas_g in grupos.items() if piezas_g)

        _dbg_nesting(
            f"[RESUMEN-GRUPOS-CON-PIEZAS] total={total_grupos_con_piezas}"
        )

        if total_grupos_con_piezas == 0:
            _dbg_nesting(
                "[ABORT] Ningún grupo quedó con piezas válidas después del parser/cleanup"
            )
            return {"error": "No se obtuvo ninguna geometría válida después del parser."}

        self._ultima_auditoria_dxf = {
            "total": total_dxf,
            "ok": max(0, total_dxf - len(piezas_parser_fallidas)),
            "omitidos": list(piezas_parser_fallidas),
        }

        if piezas_parser_fallidas:
            det_items = []
            for item in piezas_parser_fallidas[:8]:
                if isinstance(item, dict):
                    det_items.append(
                        f"{item.get('pieza', '?')}: {item.get('error', 'sin detalle')}"
                    )
                else:
                    det_items.append(str(item))
            det = "; ".join(det_items)
            if len(piezas_parser_fallidas) > 8:
                det += f" (+{len(piezas_parser_fallidas) - 8} más)"
            _dbg_nesting(f"[ABORT] Piezas sin geometría válida: {det}")
            return {
                "error": (
                    f"No se pudo leer la geometría de {len(piezas_parser_fallidas)} pieza(s). "
                    f"Revise los DXF antes de nestear: {det}"
                ),
                "dxf_audit": self._ultima_auditoria_dxf,
            }
        
        resultados = {}
        notificar("Iniciando Multiprocesamiento...", 0.16)

        grupos_con_piezas = {
            clave: piezas
            for clave, piezas in grupos.items()
            if piezas
        }

        # Poka-yoke PRE-PACK: stock + AABB + smoke de 1 pieza (falla antes del nest caro).
        ok_pre, msg_pre = self._preflight_grupos_antes_pack(
            grupos_con_piezas,
            datos_placas,
            config_kerf=config_kerf,
            config_margin=config_margin,
            config_opt=config_opt,
            config_corner=config_corner,
        )
        if not ok_pre:
            _dbg_nesting(f"[PREFLIGHT-FAIL] {msg_pre}")
            return {"error": msg_pre, "dxf_audit": self._ultima_auditoria_dxf}

        # Evita re-smoke en grupos de esta corrida (renesteo de calibre sí vuelve a validar).
        self._preflight_done = True

        # Cobre = canal propio (largos CU). Nunca entra a Ultra / perfiles de motor acero.
        grupos_cobre = {
            k: v for k, v in grupos_con_piezas.items() if _clave_es_cobre(k)
        }
        grupos_acero = {
            k: v for k, v in grupos_con_piezas.items() if not _clave_es_cobre(k)
        }
        grupos_cobre_ord = sorted(
            grupos_cobre.items(), key=lambda kv: _orden_clave_nesting(kv[0])
        )
        grupos_acero_ord = sorted(
            grupos_acero.items(), key=lambda kv: _orden_clave_nesting(kv[0])
        )

        total_lotes_reales = len(grupos_con_piezas)
        n_cobre = len(grupos_cobre_ord)
        n_acero = len(grupos_acero_ord)

        if total_lotes_reales == 0:
            _dbg_nesting("[ABORT] No hay grupos válidos para enviar a multiproceso")
            self._preflight_done = False
            return {"error": "No hay grupos válidos para procesar."}

        _dbg_nesting(
            f"[SPLIT-MATERIAL] cobre={n_cobre} | acero={n_acero} | "
            f"engine_acero={resolved_engine}"
        )

        # --- 1) Cobre primero: sin motor, sin kerf/margin/opt/corner de UI ---
        if grupos_cobre_ord:
            notificar("Nesteando cobre (largos CU)...", 0.16)
            for i, (clave, piezas) in enumerate(grupos_cobre_ord):
                if self._cancelado():
                    notificar(
                        "Nesting detenido: se conserva lo calculado hasta ahora.",
                        0.16 + (i / max(1, total_lotes_reales)) * 0.84,
                    )
                    break
                notificar(
                    f"Cobre largos {i + 1}/{n_cobre}: {clave}",
                    0.16 + (i / max(1, total_lotes_reales)) * 0.84,
                )
                try:
                    clave_w, resultado_grupo = self._procesar_grupo_parallel(
                        clave,
                        piezas,
                        datos_placas,
                        0.0,  # kerf ignorado en CU
                        0.0,  # margin ignorado en CU
                        "LARGOS CU",
                        "INFERIOR IZQUIERDA",
                        wo_name,
                        cu_routing_override="largos",
                    )
                    self._merge_resultado_en_mapa(
                        resultados, clave_w or clave, resultado_grupo
                    )
                except Exception as exc:
                    print(f"Error en Lote cobre {clave}: {exc}")
                    resultados[clave] = {"error": f"Error en cálculo cobre: {exc}"}

        # --- 2) Acero: motor seleccionado (Ultra / Force / etc.) ---
        if grupos_acero_ord and not self._cancelado():
            nest_profile = get_engine_profile(resolved_engine)
            nestfab_continual = (
                normalize_engine_id(resolved_engine) == ENGINE_SVGNEST_ULTRA
                and bool(nest_profile.get("continual_until_user_stops"))
            )
            base_pct = 0.16 + (n_cobre / max(1, total_lotes_reales)) * 0.84

            if nestfab_continual:
                notificar(
                    "SVGNest Ultra: mejora continua (Cancelar = aceptar lo mejor)...",
                    base_pct,
                )
                prev_cc = _bind_pack_cancel_checker(self._cancelado)
                acero_jobs = self._jobs_acero_separando_plasma(grupos_acero_ord)
                n_jobs = max(1, len(acero_jobs))
                try:
                    for i, (clave, piezas, es_plasma_lote) in enumerate(acero_jobs):
                        if self._cancelado():
                            notificar(
                                "Nesting detenido: se conserva lo calculado hasta ahora.",
                                base_pct + (i / n_jobs) * (1.0 - base_pct),
                            )
                            break
                        tag = "plasma" if es_plasma_lote else "láser"
                        notificar(
                            f"Ultra lote acero {i + 1}/{n_jobs} ({tag}): {clave}",
                            base_pct + (i / n_jobs) * (1.0 - base_pct),
                        )
                        try:
                            clave_w, resultado_grupo = self._procesar_grupo_parallel(
                                clave,
                                piezas,
                                datos_placas,
                                config_kerf,
                                config_margin,
                                config_opt,
                                config_corner,
                                wo_name,
                            )
                            if es_plasma_lote:
                                resultado_grupo = self._marcar_resultado_lote_plasma(
                                    resultado_grupo
                                )
                            self._merge_resultado_en_mapa(
                                resultados, clave_w or clave, resultado_grupo
                            )
                        except Exception as exc:
                            print(f"Error en Lote {clave}: {exc}")
                            resultados[clave] = {"error": f"Error en cálculo: {exc}"}
                finally:
                    _unbind_pack_cancel_checker(prev_cc)
            else:
                # Multiproceso normal solo para acero.
                acero_jobs = self._jobs_acero_separando_plasma(grupos_acero_ord)
                n_jobs = max(1, len(acero_jobs))
                nucleos_totales = multiprocessing.cpu_count()
                nucleos_a_usar = max(1, min(nucleos_totales - 2, n_jobs))
                if not str(os.environ.get("ARGA_NEST_OMP_THREADS", "")).strip():
                    intra = max(1, nucleos_totales // max(1, nucleos_a_usar))
                    os.environ["ARGA_NEST_OMP_THREADS"] = str(intra)

                try:
                    mp_manager = multiprocessing.Manager()
                    cancel_event = mp_manager.Event()
                except Exception:
                    mp_manager = None
                    cancel_event = None

                plate_allowed = getattr(self, "_plate_formats_allowed", None)
                plate_limits = getattr(self, "_plate_format_limits", None)
                from .giga_cal11_galv import (
                    ENGINE_ID as GIGA_ID,
                    should_force_giga_engine,
                )

                with concurrent.futures.ProcessPoolExecutor(
                    max_workers=nucleos_a_usar,
                    initializer=_nesting_worker_bootstrap,
                ) as executor:
                    futuros = {
                        executor.submit(
                            _procesar_grupo_parallel_worker,
                            (
                                clave,
                                piezas,
                                datos_placas,
                                config_kerf,
                                config_margin,
                                config_opt,
                                config_corner,
                                wo_name,
                                (
                                    GIGA_ID
                                    if should_force_giga_engine(clave)
                                    else resolved_engine
                                ),
                                cancel_event,
                                plate_allowed,
                                plate_limits,
                            ),
                        ): (clave, es_plasma_lote)
                        for clave, piezas, es_plasma_lote in acero_jobs
                    }

                    pendientes = set(futuros.keys())
                    completados = 0
                    while pendientes:
                        if self._cancelado():
                            if cancel_event is not None:
                                try:
                                    cancel_event.set()
                                except Exception:
                                    pass
                            for fut in list(pendientes):
                                fut.cancel()
                            notificar(
                                "Nesting cancelado: deteniendo workers...",
                                base_pct
                                + (completados / n_jobs) * (1.0 - base_pct),
                            )
                            break

                        done, not_done = concurrent.futures.wait(
                            pendientes,
                            timeout=0.4,
                            return_when=concurrent.futures.FIRST_COMPLETED,
                        )
                        if not done:
                            continue

                        for futuro in done:
                            pendientes.discard(futuro)
                            clave, es_plasma_lote = futuros[futuro]
                            try:
                                raw_result = futuro.result()

                                if raw_result is None:
                                    raise RuntimeError("El worker regresó None")

                                if isinstance(raw_result, tuple) and len(raw_result) == 2:
                                    clave_worker, resultado_grupo = raw_result
                                    if not clave_worker:
                                        clave_worker = clave
                                    if es_plasma_lote:
                                        resultado_grupo = self._marcar_resultado_lote_plasma(
                                            resultado_grupo
                                        )
                                    self._merge_resultado_en_mapa(
                                        resultados, clave_worker, resultado_grupo
                                    )
                                elif isinstance(raw_result, dict):
                                    if es_plasma_lote:
                                        raw_result = self._marcar_resultado_lote_plasma(
                                            raw_result
                                        )
                                    self._merge_resultado_en_mapa(
                                        resultados, clave, raw_result
                                    )
                                else:
                                    raise RuntimeError(
                                        f"Salida inesperada del worker: tipo={type(raw_result).__name__}"
                                    )

                            except Exception as exc:
                                print(f"Error en Lote {clave}: {exc}")
                                resultados[clave] = {"error": f"Error en cálculo: {exc}"}

                            # Fail-fast: un grupo duro fallido detiene hermanos (cancel_event).
                            try:
                                from .nest_poka_yoke import es_resultado_grupo_fallido

                                grp_chk = resultados.get(clave)
                                try:
                                    if isinstance(raw_result, tuple) and len(raw_result) == 2:
                                        _cw = raw_result[0] or clave
                                        grp_chk = resultados.get(_cw, raw_result[1])
                                    elif isinstance(raw_result, dict):
                                        grp_chk = resultados.get(clave, raw_result)
                                except NameError:
                                    pass
                                if es_resultado_grupo_fallido(grp_chk):
                                    if cancel_event is not None:
                                        try:
                                            cancel_event.set()
                                        except Exception:
                                            pass
                                    for fut_pend in list(pendientes):
                                        fut_pend.cancel()
                                    pendientes.clear()
                                    notificar(
                                        f"Fallo duro en {clave}: deteniendo workers hermanos...",
                                        base_pct
                                        + (completados / n_jobs)
                                        * (1.0 - base_pct),
                                    )
                            except Exception:
                                pass

                            completados += 1
                            progreso_actual = (
                                base_pct
                                + (completados / n_jobs) * (1.0 - base_pct)
                            )
                            notificar(
                                f"Acero procesado: {completados}/{n_jobs}",
                                progreso_actual,
                            )

                if mp_manager is not None:
                    try:
                        mp_manager.shutdown()
                    except Exception:
                        pass

        notificar("Construyendo modelos visuales...", 1.0)
        self._ultima_auditoria_dxf = {
            "total": total_dxf,
            "ok": total_dxf,
            "omitidos": [],
        }
        # Motor de acero no aplica a cobre; se documenta solo para trazabilidad UI.
        resultados["_nest_engine_id"] = resolved_engine
        if grupos_cobre:
            resultados["_nest_cobre_engine"] = "largos_cu"
        self._preflight_done = False
        return resultados

    def ejecutar_comparacion_motores_visual(
        self,
        lista_partes,
        datos_placas,
        progress_callback=None,
        config_kerf=DEFAULT_KERF_IN,
        config_margin=DEFAULT_MARGIN_IN,
        config_corner="INFERIOR IZQUIERDA",
        config_opt="OPTIMIZAR LARGO Y ANCHO",
        wo_name="PENDIENTE",
    ):
        """Opción B: corre todos los motores de acero en paralelo (cobre excluido)."""
        from .engine_compare import ejecutar_comparacion_motores

        def _factory():
            child = MotorNesting()
            child.set_cancel_checker(self._cancel_checker)
            child.orientacion_cobre_por_ruta = dict(self.orientacion_cobre_por_ruta or {})
            child.cu_especial_por_ruta = dict(self.cu_especial_por_ruta or {})
            child.plasma_compensada_por_ruta = dict(
                self.plasma_compensada_por_ruta or {}
            )
            child.plasma_dxf_por_ruta = dict(self.plasma_dxf_por_ruta or {})
            child.orientacion_corte_por_ruta = dict(
                self.orientacion_corte_por_ruta or {}
            )
            child.orientacion_corte_bloqueada_por_ruta = dict(
                self.orientacion_corte_bloqueada_por_ruta or {}
            )
            return child

        bundle = ejecutar_comparacion_motores(
            _factory,
            lista_partes,
            datos_placas,
            progress_callback=progress_callback,
            cancel_checker=self._cancelado,
            config_kerf=config_kerf,
            config_margin=config_margin,
            config_corner=config_corner,
            config_opt=config_opt,
            wo_name=wo_name,
        )
        self._ultima_comparacion_motores = bundle
        return bundle

    def _preflight_grupos_antes_pack(
        self,
        grupos_con_piezas: dict,
        datos_placas,
        *,
        config_kerf=DEFAULT_KERF_IN,
        config_margin=DEFAULT_MARGIN_IN,
        config_opt="OPTIMIZAR LARGO Y ANCHO",
        config_corner="INFERIOR IZQUIERDA",
    ) -> tuple[bool, str]:
        """
        Poka-yoke antes del nest caro: por grupo valida stock, AABB de la
        pieza más grande y un smoke-pack de 1 pieza en la mejor placa.
        """
        from .cu_inventory import inventario_barras_largos_cu
        from .nest_poka_yoke import es_pack_fault, motivo_pack_fault

        fallas: list[str] = []
        formats_allowed = getattr(self, "_plate_formats_allowed", None)

        for clave, piezas in sorted(
            (grupos_con_piezas or {}).items(),
            key=lambda kv: _orden_clave_nesting(kv[0]),
        ):
            if not piezas:
                continue
            partes = str(clave).split("_", 1)
            req_cal = partes[0]
            req_mat = partes[1] if len(partes) > 1 else ""

            placas_ok, match_mode = self._clasificar_placas_por_calibre(
                req_cal, req_mat, datos_placas
            )
            # Preflight: filas mínimas (sin precio) — reclasificar a dicts ligeros
            placas_ok = [
                {
                    "data": p.get("data"),
                    "w": p["w"],
                    "h": p["h"],
                    "precio": p.get("precio", 0.0),
                    "id": p.get("id", ""),
                    "origen": p.get("origen", "EMPRESA"),
                    "precio_lb": p.get("precio_lb", 0.0),
                    "calibre": p.get("calibre", ""),
                }
                for p in placas_ok
            ]
            _ = match_mode

            if not placas_ok:
                fallas.append(
                    f"{clave}: sin inventario de placas ({len(piezas)} pieza(s))."
                )
                continue

            if _clave_es_cobre(clave) or es_material_cobre(req_mat):
                barras = inventario_barras_largos_cu(placas_ok)
                if not barras:
                    fallas.append(
                        f"{clave}: sin barras CU 144\"×1.75–6\" para largos."
                    )
                continue
            try:
                group_kerf, group_margin, _rule_gap = gaps_for_calibre(req_cal)
            except CutGapTableError as exc:
                fallas.append(f"{clave}: {exc}")
                continue

            # Pieza más exigente por bounding box
            target = max(
                piezas,
                key=lambda pz: (
                    max(
                        float(pz["poly"].bounds[2] - pz["poly"].bounds[0]),
                        float(pz["poly"].bounds[3] - pz["poly"].bounds[1]),
                    )
                    if pz.get("poly") is not None
                    else 0.0
                ),
            )
            try:
                minx, miny, maxx, maxy = target["poly"].bounds
                w_req = float(maxx - minx)
                h_req = float(maxy - miny)
            except Exception:
                fallas.append(
                    f"{clave}: geometría inválida en pieza "
                    f"{target.get('nombre') or '?'}."
                )
                continue
            max_req, min_req = max(w_req, h_req), min(w_req, h_req)

            candidatas = []
            for p in placas_ok:
                fk = _plate_format_key_mm(
                    float(p.get("w", 0.0) or 0.0),
                    float(p.get("h", 0.0) or 0.0),
                )
                if formats_allowed is not None and fk not in formats_allowed:
                    continue
                max_p = max(float(p["w"]), float(p["h"]))
                min_p = min(float(p["w"]), float(p["h"]))
                if max_p >= (max_req - 10.0) and min_p >= (min_req - 10.0):
                    candidatas.append(p)

            if not candidatas:
                fallas.append(
                    f"{clave}: la pieza {target.get('nombre')} "
                    f"({w_req/25.4:.2f}\"×{h_req/25.4:.2f}\") no cabe en ninguna "
                    f"placa del catálogo"
                    + (
                        " (tras filtro de formatos seleccionados)"
                        if formats_allowed is not None
                        else ""
                    )
                    + "."
                )
                continue

            # Smoke-pack: 1 pieza en la placa más grande candidata
            placa_smoke = max(
                candidatas,
                key=lambda p: float(p["w"]) * float(p["h"]),
            )
            hoja_sm, _restos = _safe_empaquetar_una_hoja_mc(
                [copy.deepcopy(target)],
                placa_smoke["w"],
                placa_smoke["h"],
                group_kerf,
                group_margin,
                config_opt,
                config_corner,
                debug_tag=f"preflight|{clave}|{target.get('nombre')}",
                mc_iterations=1,
            )
            if es_pack_fault(hoja_sm):
                fallas.append(
                    f"{clave}: fallo de motor al probar {target.get('nombre')} "
                    f"en placa {placa_smoke.get('id')}: {motivo_pack_fault(hoja_sm)}"
                )
                continue
            if not (hoja_sm or {}).get("piezas"):
                fallas.append(
                    f"{clave}: no se pudo colocar {target.get('nombre')} "
                    f"({w_req/25.4:.2f}\"×{h_req/25.4:.2f}\") ni sola en placa "
                    f"{placa_smoke.get('id')} "
                    f"({float(placa_smoke['w'])/25.4:.1f}\"×"
                    f"{float(placa_smoke['h'])/25.4:.1f}\"). "
                    f"Revise DXF / kerf / margin."
                )

        if fallas:
            texto = "\n".join(f"• {f}" for f in fallas[:12])
            if len(fallas) > 12:
                texto += f"\n(+{len(fallas) - 12} más)"
            return (
                False,
                "Poka-yoke pre-nest: hay grupos que no se pueden anidar.\n"
                "Corrija stock, selección de placas o DXF antes de continuar.\n\n"
                f"{texto}",
            )
        return True, ""

    def _procesar_grupo_parallel(
        self,
        clave,
        piezas,
        datos_placas,
        config_kerf,
        config_margin,
        config_opt,
        config_corner,
        wo_name="PENDIENTE",
        q_msg=None,
        cu_routing_override=None,
        sin_rtz=False,
        cu_separacion_in=None,
        cu_largo_sin_separacion_in=None,
    ):
        prev_cc = _bind_pack_cancel_checker(self._cancelado)
        clave_tok = None
        engine_tok = None
        prev_self_engine = getattr(self, "active_engine_id", None)
        try:
            import os
            from modules.nesting_engine.ai_heuristic import smart_seed_order
            from modules.nesting_engine.giga_cal11_galv import (
                ENGINE_ID as GIGA_ID,
                should_force_giga_engine,
            )
            from modules.nesting_engine.hive_mind_nests import (
                force_eddie_policy,
                suggest_seed_policy,
            )
            from modules.nesting_engine.nest_engine_context import (
                set_active_engine_id,
                set_pack_group_clave,
            )

            clave_tok = set_pack_group_clave(str(clave or ""))
            if should_force_giga_engine(clave):
                engine_tok = set_active_engine_id(GIGA_ID)
                self.active_engine_id = GIGA_ID
                print(
                    f"[GIGA-CAL11] grupo {clave} → motor nativo {GIGA_ID}",
                    flush=True,
                )

            _engine_id = getattr(self, "active_engine_id", "default")
            try:
                kerf_g = 0.15
                if isinstance(config_kerf, dict):
                    kerf_g = float(next(iter(config_kerf.values()), 0.15) or 0.15)
                else:
                    kerf_g = float(config_kerf or 0.15)
                sug = suggest_seed_policy(piezas or [], kerf=kerf_g)
                force_eddie_policy(str(_engine_id), str(sug.get("policy") or "host_parasite"))
                print(
                    f"[HIVE-ML] engine={_engine_id} suggest={sug.get('policy')} "
                    f"conf={sug.get('confidence')} neighbors={sug.get('neighbors')}",
                    flush=True,
                )
            except Exception as _hive_exc:
                print(f"[HIVE-ML] seed skip: {_hive_exc}", flush=True)
            piezas = smart_seed_order(piezas or [], engine_id=_engine_id)

            return self._procesar_grupo_parallel_impl(
                clave,
                piezas,
                datos_placas,
                config_kerf,
                config_margin,
                config_opt,
                config_corner,
                wo_name=wo_name,
                q_msg=q_msg,
                cu_routing_override=cu_routing_override,
                sin_rtz=sin_rtz,
                cu_separacion_in=cu_separacion_in,
                cu_largo_sin_separacion_in=cu_largo_sin_separacion_in,
            )
        finally:
            try:
                from modules.nesting_engine.nest_engine_context import (
                    reset_active_engine_id,
                    reset_pack_group_clave,
                )

                if engine_tok is not None:
                    reset_active_engine_id(engine_tok)
                if clave_tok is not None:
                    reset_pack_group_clave(clave_tok)
            except Exception:
                pass
            if prev_self_engine is not None:
                self.active_engine_id = prev_self_engine
            _unbind_pack_cancel_checker(prev_cc)

    def _procesar_grupo_parallel_impl(
        self,
        clave,
        piezas,
        datos_placas,
        config_kerf,
        config_margin,
        config_opt,
        config_corner,
        wo_name="PENDIENTE",
        q_msg=None,
        cu_routing_override=None,
        sin_rtz=False,
        cu_separacion_in=None,
        cu_largo_sin_separacion_in=None,
    ):
        partes_clave = clave.split('_', 1) 
        req_cal = partes_clave[0]
        req_mat = partes_clave[1] if len(partes_clave) > 1 else ""
        es_cobre_grupo = str(req_mat).strip().upper() == "CU" or es_material_cobre(req_mat)
        regla_gap = None
        if not es_cobre_grupo:
            try:
                config_kerf, config_margin, regla_gap = gaps_for_calibre(req_cal)
            except CutGapTableError as exc:
                return clave, {"error": str(exc)}

        _dbg_nesting(
            f"[GRUPO-START] clave={clave} | piezas={len(piezas)} | "
            f"kerf={config_kerf} | margin={config_margin} | "
            f"gap_regla={regla_gap.get('label') if regla_gap else 'CU'} | "
            f"opt={config_opt} | corner={config_corner} | wo={wo_name}"
        )
        print(
            f"[KERF-TABLA] clave={clave} | entre_piezas={float(config_kerf):.3f}in | "
            f"placa_pieza={float(config_margin):.3f}in | "
            f"regla={(regla_gap or {}).get('label') or 'CU'}",
            flush=True,
        )
        for pz in piezas:
            poly_dbg = pz.get("poly")
            _dbg_nesting(
                f"[GRUPO-PIEZA] clave={clave} | debug_id={pz.get('debug_id', 'SIN_DEBUG_ID')} | "
                f"nombre={pz.get('nombre')} | area={pz.get('area', 0.0):.3f} | "
                f"bounds={_fmt_bounds(poly_dbg) if poly_dbg is not None else 'SIN_POLY'} | "
                f"ruta={pz.get('ruta', 'SIN_RUTA')}"
            )

        placas_ok, match_mode = self._clasificar_placas_por_calibre(
            req_cal, req_mat, datos_placas
        )

        _dbg_nesting(
            f"[PLACAS-CANDIDATAS] clave={clave} | match={match_mode} | "
            f"n={len(placas_ok)}"
        )

        for placa_dbg in placas_ok:
            _dbg_nesting(
                f"[PLACA-OK] clave={clave} | placa_id={placa_dbg.get('id')} | "
                f"cal={placa_dbg.get('calibre')} | delta={placa_dbg.get('delta_thk', 0):.4f} | "
                f"origen={placa_dbg.get('origen')} | w_mm={placa_dbg.get('w', 0.0):.3f} | "
                f"h_mm={placa_dbg.get('h', 0.0):.3f} | precio={placa_dbg.get('precio', 0.0):.3f} | "
                f"precio_lb={placa_dbg.get('precio_lb', 0.0):.3f}"
            )

        if not placas_ok:
            _dbg_nesting(
                f"[SIN-PLACA] clave={clave} | req_cal={req_cal} | req_mat={req_mat}"
            )
            return clave, {"error": f"Sin placa. No se halló inventario para {req_cal} {req_mat}."}

        # Renesteo de calibre / worker: preflight si la corrida completa no lo hizo.
        if not getattr(self, "_preflight_done", False):
            ok_pf, msg_pf = self._preflight_grupos_antes_pack(
                {clave: piezas},
                datos_placas,
                config_kerf=config_kerf,
                config_margin=config_margin,
                config_opt=config_opt,
                config_corner=config_corner,
            )
            if not ok_pf:
                _dbg_nesting(f"[PREFLIGHT-GRUPO-FAIL] clave={clave} | {msg_pf}")
                return clave, {"error": msg_pf}

        if es_cobre_grupo:
            # Cobre largos: canal propio. Ignora motor de acero, kerf, margin, opt y corner.
            placas_largos = inventario_barras_largos_cu(placas_ok)
            _dbg_nesting(
                f"[CU-LARGOS] clave={clave} | barras={len(placas_largos)} | "
                f"override={str(cu_routing_override or '').strip() or 'auto'} | "
                f"(motor/kerf/margin/opt/corner NO aplican)"
            )
            if not placas_largos:
                return clave, {
                    "error": (
                        f"Sin barras CU 144\"×2–6\" en inventario para {req_cal} {req_mat}."
                    )
                }
            clave_out, resultado_largos = procesar_grupo_largos_cu(
                clave,
                piezas,
                placas_largos,
                wo_name=wo_name,
                dbg_fn=_dbg_nesting,
                exigir_colocacion_total=True,
                separacion_in=(
                    float(cu_separacion_in)
                    if cu_separacion_in is not None
                    else None
                ),
                largo_sin_separacion_in=(
                    float(cu_largo_sin_separacion_in)
                    if cu_largo_sin_separacion_in is not None
                    else None
                ),
            )
            if (
                isinstance(resultado_largos, dict)
                and not resultado_largos.get("error")
                and piezas
            ):
                ok_inv, msg_inv = validar_inventario_cu_resultado(piezas, resultado_largos)
                if not ok_inv:
                    return clave_out, {"error": msg_inv}
            return clave_out, resultado_largos

        # Ya vienen ordenadas por delta_thk, precio_lb, precio. No reordenar solo por $.
        formatos_vistos = set()
        placas_unicas_simulacion = []
        for p in placas_ok:
            formato = f"{p['w']}x{p['h']}"
            if formato not in formatos_vistos:
                formatos_vistos.add(formato)
                placas_unicas_simulacion.append(p)

        # Herinox DISPONIBLE = catálogo de formatos. NUNCA cupo de hojas.
        # Ningún motor (Lite/Force/Ultra) falla por “se acabaron N filas”.
        # formats_allowed (Ultra manual) solo filtra QUÉ tamaños usar, no cuántas hojas.
        formats_allowed_pre = getattr(self, "_plate_formats_allowed", None)
        format_limits_grupo: dict[str, int] = {}
        format_used_grupo: dict[str, int] = {}
        _dbg_nesting(
            f"[FORMAT-CATALOGO] clave={clave} | "
            f"allowed={'manual' if formats_allowed_pre else 'auto'} | "
            f"limites_hojas=0 (desactivados)"
        )

        AREA_LIMITE_MM2 = ARGA_AREA_ESTRUCTURAL_MM2
        estructurales = [p for p in piezas if p['area'] > AREA_LIMITE_MM2]
        accesorios_base = [p for p in piezas if p['area'] <= AREA_LIMITE_MM2]

        nest_profile = get_engine_profile(get_active_engine_id())
        mc_iters = int(nest_profile.get("mc_iterations", 1))
        mc_fast = int(nest_profile.get("mc_lookahead_iterations", 1))
        use_lookahead = bool(nest_profile.get("lookahead", False))
        refine_hoja = bool(nest_profile.get("refine_hoja", False))
        cu_acc_retries = int(nest_profile.get("accesorios_retries", 1))
        cu_refinar_intentos = int(nest_profile.get("refinar_intentos", 0))
        _dbg_nesting(
            f"[NEST-PROFILE] clave={clave} | mc={mc_iters} | lookahead={use_lookahead} | "
            f"mc_fast={mc_fast} | refine={refine_hoja} | acc_retries={cu_acc_retries}"
        )

        hojas_finales = []
        costo_total_lote = 0
        inventario_aviso = ""
        contador_rtz_grupo = 1
        
        pendientes_est = copy.deepcopy(estructurales)
        accesorios = copy.deepcopy(accesorios_base)
        num_placa_actual = 1

        while pendientes_est or accesorios:
            if self._cancelado():
                _dbg_nesting(f"[CANCEL] clave={clave} | abortando grupo (NestFab stop)")
                break
            pool_est_snapshot = copy.deepcopy(pendientes_est)
            pool_acc_snapshot = copy.deepcopy(accesorios)
            usar_pack_combinado = _usar_pack_combinado_grupo(pendientes_est, accesorios)
            if usar_pack_combinado:
                _dbg_nesting(
                    f"[PACK-COMBINADO] clave={clave} | est={len(pendientes_est)} | "
                    f"acc={len(accesorios)} | motor={get_active_engine_id()}"
                )
            pool_combined_snapshot = (
                pool_est_snapshot + pool_acc_snapshot if usar_pack_combinado else None
            )
            if pendientes_est: pendientes_est.sort(key=lambda x: x['area'], reverse=True)
            if accesorios: accesorios.sort(key=lambda x: x['area'], reverse=True)
            
            target_piece = pendientes_est[0] if pendientes_est else accesorios[0]
            minx, miny, maxx, maxy = target_piece['poly'].bounds
            w_req, h_req = maxx - minx, maxy - miny
            max_req, min_req = max(w_req, h_req), min(w_req, h_req)
            _dbg_nesting(
                f"[TARGET-PIEZA] clave={clave} | "
                f"debug_id={target_piece.get('debug_id', 'SIN_DEBUG_ID')} | "
                f"nombre={target_piece.get('nombre')} | "
                f"bounds={_fmt_bounds(target_piece.get('poly'))} | "
                f"w_req={w_req:.3f} | h_req={h_req:.3f} | "
                f"max_req={max_req:.3f} | min_req={min_req:.3f}"
            )
                        
            placas_simulacion_validas = []
            for p in placas_unicas_simulacion:
                max_p, min_p = max(p['w'], p['h']), min(p['w'], p['h'])
                if max_p >= (max_req - 10.0) and min_p >= (min_req - 10.0):
                    placas_simulacion_validas.append(p)
            _dbg_nesting(
                f"[PLACAS-SIM-VALIDAS] clave={clave} | total={len(placas_simulacion_validas)}"
            )

            for placa_sim in placas_simulacion_validas:
                _dbg_nesting(
                    f"[PLACA-SIM-OK] clave={clave} | placa_id={placa_sim.get('id')} | "
                    f"w_mm={placa_sim.get('w', 0.0):.3f} | h_mm={placa_sim.get('h', 0.0):.3f} | "
                    f"origen={placa_sim.get('origen')} | precio={placa_sim.get('precio', 0.0):.3f}"
                )
                    
            if not placas_simulacion_validas:
                _dbg_nesting(
                    f"[SIN-PLACA-SIM-VALIDA] clave={clave} | "
                    f"target={target_piece.get('nombre')} | "
                    f"debug_id={target_piece.get('debug_id', 'SIN_DEBUG_ID')} | "
                    f"w_req={w_req:.3f} | h_req={h_req:.3f}"
                )
                return clave, {"error": f"Error: La pieza {target_piece['nombre']} es demasiado grande para el inventario."}

            candidatos_sim = placas_simulacion_validas
            solo_accesorios_fase = not pendientes_est and bool(accesorios)
            if solo_accesorios_fase:
                candidatos_sim = _filtrar_placas_para_accesorios(accesorios, placas_simulacion_validas)
                _dbg_nesting(
                    f"[ACCESORIOS-FASE] clave={clave} | candidatos={len(candidatos_sim)}/{len(placas_simulacion_validas)} | "
                    f"area={_area_total_piezas(accesorios):.0f}"
                )
            elif _es_cola_de_grupo(pendientes_est, accesorios):
                candidatos_sim = _ordenar_placas_cola(
                    pendientes_est + accesorios,
                    placas_simulacion_validas,
                )
                _dbg_nesting(
                    f"[COLA-GRUPO] clave={clave} | piezas_restantes={len(pendientes_est) + len(accesorios)} | "
                    f"area_restante={_area_total_piezas(pendientes_est + accesorios):.0f}"
                )

            mejor_hoja_temp = None
            mejor_score = float('inf')
            mejor_restos_est = []
            mejor_restos_acc = []
            mejor_placa = None
            mejor_restos_total = None
            mejor_precio = None

            formats_allowed = getattr(self, "_plate_formats_allowed", None)
            format_limits = format_limits_grupo
            format_used = format_used_grupo

            # Early-exit: FORCE o Ultra en Auto (sin formatos forzados a mano).
            use_early_exit = (
                _early_exit_sim_placa_activo()
                and formats_allowed is None
            )
            area_pend_score = _area_total_piezas(pendientes_est) + _area_total_piezas(
                accesorios
            )

            # SIM multi-placa: nunca Ultra continual (aunque renesteo Ultra esté en accept-mode).
            from .nest_engine_context import reset_ultra_sim_bounded, set_ultra_sim_bounded

            sim_bound_token = set_ultra_sim_bounded(True)
            parallel_workers = _plate_sel_parallel_workers()
            fast_rank = _plate_sel_fast_rank_activo()
            _dbg_nesting(
                f"[SIM-PLACA-MODE] clave={clave} | parallel_workers={parallel_workers} | "
                f"fast_rank={fast_rank} | candidatos={len(candidatos_sim)} | "
                f"early_exit={use_early_exit}"
            )

            def _skip_candidato(cand, mejor_sc, mejor_pr, mejor_restos):
                fmt_key = _plate_format_key_mm(
                    float(cand.get("w", 0.0) or 0.0),
                    float(cand.get("h", 0.0) or 0.0),
                )
                if formats_allowed is not None and fmt_key not in formats_allowed:
                    return "fmt"
                lim = format_limits.get(fmt_key)
                if lim is not None and int(format_used.get(fmt_key, 0)) >= int(lim):
                    return "lim"
                cand_precio = float(cand.get("precio", 0.0) or 0.0)
                if (
                    use_early_exit
                    and mejor_restos == 0
                    and mejor_pr is not None
                    and cand_precio >= float(mejor_pr) - 1e-9
                ):
                    return "early-exit-full"
                if use_early_exit and mejor_sc < float("inf"):
                    lb = score_placa_lower_bound(
                        cand, area_piezas_pendientes=area_pend_score
                    )
                    if lb >= mejor_sc:
                        return "early-exit-lb"
                return None

            def _eval_candidato(candidato_placa):
                """Nest+score de una candidata. Thread-safe si C++ libera GIL."""
                sim_est = copy.deepcopy(pendientes_est)
                sim_acc = copy.deepcopy(accesorios)
                record_probe(
                    "sim_start",
                    clave=str(clave),
                    placa_id=str(candidato_placa.get("id") or ""),
                    w_mm=float(candidato_placa.get("w", 0.0) or 0.0),
                    h_mm=float(candidato_placa.get("h", 0.0) or 0.0),
                    precio=float(candidato_placa.get("precio", 0.0) or 0.0),
                )
                _sim_t0 = time.perf_counter()
                hoja_sim = None
                restos_sim = []
                restos_est_out = []
                restos_acc_out = []
                modo = ""
                if sim_est:
                    if usar_pack_combinado:
                        hoja_sim, restos_est_out, restos_acc_out = _empaquetar_arga_combinado(
                            sim_est,
                            sim_acc,
                            candidato_placa["w"],
                            candidato_placa["h"],
                            config_kerf,
                            config_margin,
                            config_opt,
                            config_corner,
                            mc_iterations=mc_iters,
                            debug_tag=(
                                f"clave={clave} | placa_id={candidato_placa.get('id')} | "
                                "modo=combinado"
                            ),
                        )
                        restos_sim = restos_est_out + restos_acc_out
                        if not hoja_sim or not hoja_sim.get("piezas"):
                            return None
                        modo = "combinado"
                    else:
                        hoja_sim, restos_sim = _safe_empaquetar_una_hoja_mc(
                            sim_est,
                            candidato_placa["w"],
                            candidato_placa["h"],
                            config_kerf,
                            config_margin,
                            config_opt,
                            config_corner,
                            debug_tag=(
                                f"clave={clave} | placa_id={candidato_placa.get('id')} | "
                                "modo=estructurales"
                            ),
                            mc_iterations=mc_iters,
                        )
                        restos_est_out = restos_sim
                        restos_acc_out = sim_acc
                        modo = "estructurales"
                elif sim_acc:
                    hoja_sim, restos_sim = _empaquetar_mejor_hoja_mc(
                        sim_acc,
                        candidato_placa["w"],
                        candidato_placa["h"],
                        config_kerf,
                        config_margin,
                        config_opt,
                        config_corner,
                        debug_tag=(
                            f"clave={clave} | placa_id={candidato_placa.get('id')} | "
                            "modo=accesorios"
                        ),
                        mc_iterations=mc_iters,
                        solo_accesorios=True,
                        accesorios_retries=cu_acc_retries,
                    )
                    restos_est_out = []
                    restos_acc_out = restos_sim
                    modo = "accesorios"
                else:
                    return None

                _dbg_nesting(
                    f"[SIM-PLACA-RESULT] clave={clave} | placa_id={candidato_placa.get('id')} | "
                    f"modo={modo} | piezas_colocadas={len((hoja_sim or {}).get('piezas', []))} | "
                    f"area_usada={(hoja_sim or {}).get('area_usada', 0.0):.3f} | "
                    f"restos={len(restos_sim)}"
                )
                from .nest_poka_yoke import es_pack_fault, motivo_pack_fault

                if es_pack_fault(hoja_sim):
                    record_probe(
                        "sim_fault",
                        clave=str(clave),
                        placa_id=str(candidato_placa.get("id") or ""),
                        elapsed_ms=(time.perf_counter() - _sim_t0) * 1000.0,
                        detail=str(motivo_pack_fault(hoja_sim) or ""),
                    )
                    return None
                if not hoja_sim.get("piezas"):
                    return None
                from .nest_poka_yoke import validar_integridad_bloque_hojas

                hoja_chk = dict(hoja_sim)
                hoja_chk["placa_w"] = float(candidato_placa.get("w", 0.0) or 0.0)
                hoja_chk["placa_h"] = float(candidato_placa.get("h", 0.0) or 0.0)
                hoja_chk["placa_id"] = candidato_placa.get("id")
                ok_sim_s, msg_sim_s = validar_integridad_bloque_hojas([hoja_chk])
                if not ok_sim_s:
                    record_probe(
                        "sim_integrity_fail",
                        clave=str(clave),
                        placa_id=str(candidato_placa.get("id") or ""),
                        elapsed_ms=(time.perf_counter() - _sim_t0) * 1000.0,
                        detail=str(msg_sim_s or ""),
                    )
                    return None
                restos_count = len(restos_est_out) + len(restos_acc_out)
                area_restos = _area_total_piezas(restos_est_out) + _area_total_piezas(
                    restos_acc_out
                )
                piezas_colocadas = len(hoja_sim.get("piezas") or [])
                lookahead_cost = 0.0
                if use_lookahead and restos_count > 0:
                    lookahead_cost = _estimar_costo_lookahead(
                        restos_est_out,
                        restos_acc_out,
                        placas_simulacion_validas,
                        config_kerf,
                        config_margin,
                        config_opt,
                        config_corner,
                        mc_fast,
                    )
                score = score_placa_simulacion(
                    candidato_placa,
                    hoja_sim,
                    restos_count=restos_count,
                    area_restos=area_restos,
                    piezas_colocadas=piezas_colocadas,
                    lookahead_cost=lookahead_cost,
                )
                _sim_elapsed_ms = (time.perf_counter() - _sim_t0) * 1000.0
                _dbg_nesting(
                    f"[SIM-PLACA-SCORE] clave={clave} | placa_id={candidato_placa.get('id')} | "
                    f"score={score:.6f} | elapsed_ms={_sim_elapsed_ms:.1f}"
                )
                record_probe(
                    "sim_result",
                    clave=str(clave),
                    placa_id=str(candidato_placa.get("id") or ""),
                    w_mm=float(candidato_placa.get("w", 0.0) or 0.0),
                    h_mm=float(candidato_placa.get("h", 0.0) or 0.0),
                    precio=float(candidato_placa.get("precio", 0.0) or 0.0),
                    piezas_colocadas=int(piezas_colocadas),
                    area_usada=float(hoja_sim.get("area_usada", 0.0) or 0.0),
                    restos=int(restos_count),
                    score=float(score),
                    elapsed_ms=float(_sim_elapsed_ms),
                )
                return {
                    "score": float(score),
                    "hoja": hoja_sim,
                    "restos_est": restos_est_out,
                    "restos_acc": restos_acc_out,
                    "placa": candidato_placa,
                    "restos_total": int(restos_count),
                    "precio": float(candidato_placa.get("precio", 0.0) or 0.0),
                }

            # Orden por cota inferior solo en modo paralelo (misma optima,
            # mejor early-exit entre olas). Secuencial conserva orden histórico.
            if parallel_workers > 1:
                candidatos_ordenados = sorted(
                    list(candidatos_sim),
                    key=lambda p: (
                        score_placa_lower_bound(
                            p, area_piezas_pendientes=area_pend_score
                        ),
                        float(p.get("precio", 0.0) or 0.0),
                        str(p.get("id") or ""),
                    ),
                )
            else:
                candidatos_ordenados = list(candidatos_sim)
            pendientes_cand = list(candidatos_ordenados)
            while pendientes_cand:
                # Primera candidata sola: permite early-exit antes de abrir ola paralela.
                # Sin esto, N workers nestearían N formatos aunque el 1º ya cierre el job.
                ola_cap = 1 if mejor_score == float("inf") else max(1, parallel_workers)
                ola = []
                while pendientes_cand and len(ola) < ola_cap:
                    cand = pendientes_cand.pop(0)
                    motivo = _skip_candidato(
                        cand, mejor_score, mejor_precio, mejor_restos_total
                    )
                    if motivo is not None:
                        if motivo in ("early-exit-full", "early-exit-lb"):
                            _dbg_nesting(
                                f"[SIM-PLACA-SKIP] clave={clave} | "
                                f"placa_id={cand.get('id')} | detail={motivo}"
                            )
                            record_probe(
                                "sim_skip",
                                clave=str(clave),
                                placa_id=str(cand.get("id") or ""),
                                w_mm=float(cand.get("w", 0.0) or 0.0),
                                h_mm=float(cand.get("h", 0.0) or 0.0),
                                precio=float(cand.get("precio", 0.0) or 0.0),
                                detail=str(motivo),
                            )
                        continue
                    ola.append(cand)
                if not ola:
                    continue
                if q_msg:
                    q_msg.put(
                        f"[{req_cal}] Procesando Placa #{num_placa_actual} | "
                        f"Quedan: {len(pendientes_est) + len(accesorios)} piezas..."
                    )
                resultados_ola = []
                import contextlib as _ctxlib

                rank_cm = (
                    _force_seeds_override(1) if fast_rank else _ctxlib.nullcontext()
                )
                with rank_cm:
                    if len(ola) == 1 or parallel_workers <= 1:
                        for cand in ola:
                            _dbg_nesting(
                                f"[SIM-PLACA-START] clave={clave} | placa_id={cand.get('id')} | "
                                f"w_mm={cand.get('w', 0.0):.3f} | h_mm={cand.get('h', 0.0):.3f} | "
                                f"precio={cand.get('precio', 0.0):.3f} | "
                                f"pendientes_est={len(pendientes_est)} | "
                                f"accesorios={len(accesorios)}"
                            )
                            resultados_ola.append(_eval_candidato(cand))
                    else:
                        _dbg_nesting(
                            f"[SIM-PLACA-OLA] clave={clave} | size={len(ola)} | "
                            f"workers={parallel_workers} | ids="
                            + ",".join(str(c.get("id") or "") for c in ola)
                        )
                        with concurrent.futures.ThreadPoolExecutor(
                            max_workers=len(ola)
                        ) as pool:
                            futs = [pool.submit(_eval_candidato, c) for c in ola]
                            for fut in concurrent.futures.as_completed(futs):
                                try:
                                    resultados_ola.append(fut.result())
                                except Exception as exc:
                                    _dbg_nesting(
                                        f"[SIM-PLACA-OLA-ERR] clave={clave} | {exc}"
                                    )
                for res in resultados_ola:
                    if not res:
                        continue
                    if res["score"] < mejor_score:
                        mejor_score = res["score"]
                        mejor_hoja_temp = res["hoja"]
                        mejor_restos_est = res["restos_est"]
                        mejor_restos_acc = res["restos_acc"]
                        mejor_placa = res["placa"]
                        mejor_restos_total = int(res["restos_total"])
                        mejor_precio = float(res["precio"])

            # Ranking rápido usó 1 semilla: re-nest de la ganadora con presupuesto completo.
            if (
                fast_rank
                and mejor_placa is not None
                and mejor_hoja_temp is not None
            ):
                _dbg_nesting(
                    f"[SIM-PLACA-REFINE] clave={clave} | placa_id={mejor_placa.get('id')} | "
                    "re-nest ganadora con semillas completas"
                )
                refined = _eval_candidato(mejor_placa)
                if refined and refined.get("hoja"):
                    mejor_score = refined["score"]
                    mejor_hoja_temp = refined["hoja"]
                    mejor_restos_est = refined["restos_est"]
                    mejor_restos_acc = refined["restos_acc"]
                    mejor_restos_total = int(refined["restos_total"])
                    mejor_precio = float(refined["precio"])

            reset_ultra_sim_bounded(sim_bound_token)

            if not mejor_hoja_temp:
                # Diagnóstico: distinguir stock / selección / packer
                n_sim = len(candidatos_sim)
                n_skip_fmt = 0
                n_skip_lim = 0
                for cand in candidatos_sim:
                    fk = _plate_format_key_mm(
                        float(cand.get("w", 0.0) or 0.0),
                        float(cand.get("h", 0.0) or 0.0),
                    )
                    if formats_allowed is not None and fk not in formats_allowed:
                        n_skip_fmt += 1
                        continue
                    lim = format_limits.get(fk)
                    if lim is not None and int(format_used.get(fk, 0)) >= int(lim):
                        n_skip_lim += 1
                n_utiles = n_sim - n_skip_fmt - n_skip_lim
                tgt = target_piece.get("nombre") or "?"
                _dbg_nesting(
                    f"[ERROR-CRITICO-EMPAQUE] clave={clave} | req_cal={req_cal} | req_mat={req_mat} | "
                    f"piezas_grupo={len(piezas)} | placas_candidatas={len(placas_ok)} | "
                    f"sim={n_sim} skip_fmt={n_skip_fmt} skip_lim={n_skip_lim} utiles={n_utiles} | "
                    f"target={tgt} | motivo=no se obtuvo ninguna hoja con piezas"
                )

                for pz in piezas:
                    poly_dbg = pz.get("poly")
                    _dbg_nesting(
                        f"[ERROR-PIEZA-CANDIDATA] clave={clave} | debug_id={pz.get('debug_id', 'SIN_DEBUG_ID')} | "
                        f"nombre={pz.get('nombre')} | area={pz.get('area', 0.0):.3f} | "
                        f"bounds={_fmt_bounds(poly_dbg) if poly_dbg is not None else 'SIN_POLY'} | "
                        f"ruta={pz.get('ruta', 'SIN_RUTA')}"
                    )

                if hojas_finales:
                    # Ya hay placas buenas: no tumbar el grupo entero.
                    # El candado de inventario al final marca incompleto.
                    _dbg_nesting(
                        f"[EMPAQUE-STOP-PARCIAL] clave={clave} | "
                        f"hojas_ok={len(hojas_finales)} | target={tgt} | "
                        f"utiles={n_utiles} — se conserva progreso"
                    )
                    break

                if n_utiles <= 0:
                    if n_skip_fmt > 0:
                        msg_err = (
                            f"{clave}: las placas de este calibre quedaron fuera por la "
                            f"selección manual de formatos. Pieza pendiente: {tgt}."
                        )
                    else:
                        msg_err = (
                            f"{clave}: no hay formato de placa en catálogo para {tgt} "
                            f"({req_cal} {req_mat}). Revise Herinox."
                        )
                else:
                    msg_err = (
                        f"{clave}: el motor no pudo acomodar piezas en "
                        f"{n_utiles} formato(s) candidato(s) (objetivo {tgt}). "
                        f"Revise DXF / kerf / margin."
                    )
                return clave, {"error": msg_err}

            hoja_ganadora = mejor_hoja_temp
            candidato_ganador = mejor_placa
            try:
                win_key = _plate_format_key_mm(
                    float(candidato_ganador.get("w", 0.0) or 0.0),
                    float(candidato_ganador.get("h", 0.0) or 0.0),
                )
                format_used_grupo[win_key] = int(
                    format_used_grupo.get(win_key, 0)
                ) + 1
            except Exception:
                pass
            forzar_sin_mini_nest = _debe_forzar_sin_mini_nest(
                req_cal, candidato_ganador["w"], candidato_ganador["h"]
            )

            # Refinar compactación solo en modos con refine_hoja (standard/max)
            if refine_hoja and cu_refinar_intentos > 0:
                piezas_pool_ref = pendientes_est if pendientes_est else accesorios
                hoja_ref = _refinar_hoja_empaque(
                    hoja_ganadora,
                    piezas_pool_ref,
                    candidato_ganador["w"],
                    candidato_ganador["h"],
                    config_kerf,
                    config_margin,
                    config_opt,
                    config_corner,
                    intentos=cu_refinar_intentos,
                    mc_iterations=mc_iters,
                )
                if hoja_ref and hoja_ref.get("piezas"):
                    hoja_ganadora = hoja_ref

            from .sheet_integrity import calcular_restos_desde_colocados

            if usar_pack_combinado and pool_combined_snapshot is not None:
                pre_restos = calcular_restos_desde_colocados(
                    pool_combined_snapshot, hoja_ganadora
                )
                _, pre_acc = _split_pool_estructural_accesorio(pre_restos)
                if pre_acc:
                    _rellenar_accesorios_en_huecos_hoja(
                        hoja_ganadora,
                        pre_acc,
                        candidato_ganador["w"],
                        candidato_ganador["h"],
                        config_kerf,
                        config_margin,
                        config_opt,
                        config_corner,
                        mc_iterations=mc_iters,
                        accesorios_retries=cu_acc_retries,
                        clave=clave,
                        solo_interiores=False,
                    )
                restos_all = calcular_restos_desde_colocados(
                    pool_combined_snapshot, hoja_ganadora
                )
                mejor_restos_est, mejor_restos_acc = _split_pool_estructural_accesorio(
                    restos_all
                )
            elif pendientes_est:
                mejor_restos_est = calcular_restos_desde_colocados(
                    pool_est_snapshot, hoja_ganadora
                )
            else:
                mejor_restos_acc = calcular_restos_desde_colocados(
                    pool_acc_snapshot, hoja_ganadora
                )
            
            _dbg_nesting(
                f"[SIM-PLACA-GANADORA] clave={clave} | placa_id={candidato_ganador.get('id')} | "
                f"w_mm={candidato_ganador.get('w', 0.0):.3f} | h_mm={candidato_ganador.get('h', 0.0):.3f} | "
                f"precio={candidato_ganador.get('precio', 0.0):.3f} | "
                f"piezas_colocadas={len(hoja_ganadora.get('piezas', []))} | "
                f"area_usada={hoja_ganadora.get('area_usada', 0.0):.3f}"
            )
            record_probe(
                "sim_winner",
                clave=str(clave),
                placa_id=str(candidato_ganador.get("id") or ""),
                w_mm=float(candidato_ganador.get("w", 0.0) or 0.0),
                h_mm=float(candidato_ganador.get("h", 0.0) or 0.0),
                precio=float(candidato_ganador.get("precio", 0.0) or 0.0),
                piezas_colocadas=len(hoja_ganadora.get("piezas") or []),
                area_usada=float(hoja_ganadora.get("area_usada", 0.0) or 0.0),
                score=float(mejor_score) if mejor_score < float("inf") else None,
            )

            hoja_ganadora.update({
                'placa_id': candidato_ganador['id'], 'placa_w': candidato_ganador['w'],
                'placa_h': candidato_ganador['h'], 'precio_placa': candidato_ganador['precio'],
                'placa_cal': str(candidato_ganador.get('calibre') or req_cal).strip(),
                'placa_match_mode': match_mode,
                'kerf_usado': config_kerf, 'margin_usado': config_margin,
                'opt_usado': config_opt, 'corner_usado': config_corner,
                'es_retazo': False, 'origen_placa': candidato_ganador['origen']
            })
            # Consumir remanente de inventario (no reutilizar en la misma corrida)
            try:
                origen_g = str(candidato_ganador.get("origen") or "").upper()
                pid_g = str(candidato_ganador.get("id") or "")
                if candidato_ganador.get("es_remanente") or "REMANENTE" in origen_g:
                    if not hasattr(self, "_remnant_ids_consumidos"):
                        self._remnant_ids_consumidos = set()
                    self._remnant_ids_consumidos.add(pid_g)
                    from .remnants_inventory import mark_remnant_used

                    area_in2 = (
                        float(candidato_ganador.get("w", 0) or 0)
                        * float(candidato_ganador.get("h", 0) or 0)
                        / (25.4 * 25.4)
                    )
                    mk = mark_remnant_used(pid_g, area_in2=area_in2)
                    _dbg_nesting(
                        f"[REMNANT-USED] clave={clave} | placa_id={pid_g} | {mk}"
                    )
            except Exception as rem_ex:
                _dbg_nesting(f"[REMNANT-USED-ERR] {rem_ex}")
            
            if usar_pack_combinado:
                pendientes_est = mejor_restos_est
                accesorios = mejor_restos_acc
            elif pendientes_est:
                pendientes_est = mejor_restos_est
            else:
                accesorios = mejor_restos_acc

            # Compact-lite: backfill remanente L + band-close ANTES de RTZ / hoja nueva.
            pool_fill = list(pendientes_est or []) + list(accesorios or [])
            if pool_fill or (hoja_ganadora.get("piezas") or []):
                from .sheet_integrity import calcular_restos_por_delta, contar_piezas_reales_hoja
                from . import compact_lite

                conteo_antes = contar_piezas_reales_hoja(hoja_ganadora)
                engine_compact = str(
                    getattr(self, "active_engine_id", None) or "arga_lite"
                )
                pool_after = compact_lite.densify_sheet(
                    hoja_ganadora,
                    pool_fill,
                    w_placa=float(candidato_ganador["w"]),
                    h_placa=float(candidato_ganador["h"]),
                    kerf=config_kerf,
                    margin=config_margin,
                    opt=config_opt,
                    corner=config_corner,
                    mc_iterations=max(1, int(mc_iters or 1)),
                    clave=clave,
                    engine_id=engine_compact,
                )
                try:
                    from .venom_hole_fill import apply_lite_hole_fill

                    apply_lite_hole_fill(hoja_ganadora, engine_id=engine_compact)
                except Exception as hole_ex:
                    _dbg_nesting(f"[LITE-HOLE-FILL-ERR] {hole_ex}")
                # Post-compact: no permitir que band-close/fill reduzcan el kerf de tabla.
                try:
                    from .nest_poka_yoke import (
                        reparar_separacion_minima_hoja,
                        validar_separacion_minima_hoja,
                    )

                    ok_post, det_post = validar_separacion_minima_hoja(
                        hoja_ganadora,
                        float(config_kerf or 0.0),
                        margin_in=float(config_margin or 0.0),
                        w_placa=float(candidato_ganador["w"]),
                        h_placa=float(candidato_ganador["h"]),
                    )
                    if not ok_post:
                        ok_fix, det_fix, expulsadas = reparar_separacion_minima_hoja(
                            hoja_ganadora,
                            float(config_kerf or 0.0),
                            margin_in=float(config_margin or 0.0),
                            w_placa=float(candidato_ganador["w"]),
                            h_placa=float(candidato_ganador["h"]),
                        )
                        if ok_fix and hoja_ganadora.get("piezas"):
                            if expulsadas:
                                rein = _piezas_expulsadas_a_pool(expulsadas)
                                pendientes_est = list(pendientes_est) + rein
                                msg_post = (
                                    f"[POKA-KERF-REPAIR] post-compact clave={clave} | "
                                    f"expulsadas={len(expulsadas)} reinject={len(rein)} | "
                                    f"was={det_post}"
                                )
                                print(msg_post, flush=True)
                                _dbg_nesting(msg_post)
                            elif "ok_separado" in str(det_fix or ""):
                                msg_post = (
                                    f"[POKA-KERF-NUDGE] post-compact clave={clave} | "
                                    f"{det_fix} | was={det_post}"
                                )
                                print(msg_post, flush=True)
                                _dbg_nesting(msg_post)
                        else:
                            msg_post = (
                                f"[POKA-KERF-FAIL] post-compact clave={clave} | {det_post}"
                            )
                            print(msg_post, flush=True)
                            _dbg_nesting(msg_post)
                            raise RuntimeError(
                                f"El nest viola la TABLA GAPS DE CORTE ({det_post}). "
                                "No se envía a piso."
                            )
                except RuntimeError:
                    raise
                except Exception as poka_ex:
                    _dbg_nesting(f"[POKA-KERF-CHECK-ERR] {poka_ex}")
                delta = contar_piezas_reales_hoja(hoja_ganadora) - conteo_antes
                if delta:
                    pendientes_est = calcular_restos_por_delta(pendientes_est, delta)
                    accesorios = calcular_restos_por_delta(accesorios, delta)
                    _dbg_nesting(
                        f"[COMPACT-ANTES-RTZ] clave={clave} | "
                        f"colocadas={sum(delta.values())} | "
                        f"restan_est={len(pendientes_est)} | restan_acc={len(accesorios)} | "
                        f"pool_after={len(pool_after or [])}"
                    )
                elif pool_fill and not delta:
                    # Solo band-close; pools intactos.
                    _dbg_nesting(
                        f"[COMPACT-ANTES-RTZ] clave={clave} | solo_band_close | "
                        f"pool={len(pool_fill)}"
                    )
                actualizar_eficiencias_hoja(hoja_ganadora)

            try:
                from .giga_cal11_galv import fill_vfm_open_channels, should_force_giga_engine

                if should_force_giga_engine(clave):
                    if not isinstance(pendientes_est, list):
                        pendientes_est = list(pendientes_est or [])
                    if not isinstance(accesorios, list):
                        accesorios = list(accesorios or [])
                    n0 = len(hoja_ganadora.get("piezas") or [])
                    fill_vfm_open_channels(hoja_ganadora, pendientes_est)
                    fill_vfm_open_channels(hoja_ganadora, accesorios)
                    if len(hoja_ganadora.get("piezas") or []) != n0:
                        actualizar_eficiencias_hoja(hoja_ganadora)
            except Exception as giga_ch_ex:
                _dbg_nesting(f"[GIGA-CAL11] post-compact canal skip: {giga_ch_ex}")

            if sin_rtz:
                _dbg_nesting(
                    f"[SIN-RTZ-PLASMA] clave={clave} | placa_id={candidato_ganador.get('id')} | "
                    "renesteo compensado plasma: placa madre sin mini-nest ni hojas RTZ."
                )
                from .nest_poka_yoke import validar_integridad_bloque_hojas

                ok_c, msg_c = validar_integridad_bloque_hojas([hoja_ganadora])
                if not ok_c:
                    _dbg_nesting(
                        f"[COMMIT-PLACA-INTEGRIDAD] clave={clave} | {msg_c}"
                    )
                    return clave, {
                        "error": (
                            f"Integridad al commit de placa {candidato_ganador.get('id')}: "
                            f"{msg_c}"
                        ),
                    }
                hojas_finales.append(hoja_ganadora)
                costo_total_lote += candidato_ganador['precio']
                num_placa_actual += 1
                continue

            mini_nests_locales = []
            retazos_virtuales = []
            
            for p in list(hoja_ganadora['piezas']):
                if not _es_pieza_fisica_hoja(p.get('nombre')):
                    continue
                poly = reconstruir_poly_seguro(p['poligonos'])
                for interior in interiores_poly(poly):
                    hole_poly = Polygon(interior)
                    minx, miny, maxx, maxy = hole_poly.bounds
                    w_r, h_r = maxx - minx, maxy - miny
                    if not _retazo_cumple_tamano_minimo(w_r, h_r, tipo="HOLE"):
                        continue
                    # Barreno ya reutilizado en madre: no abrir RTZ encima (causa empalmes).
                    if _hole_ya_reutilizado_en_madre(hole_poly, hoja_ganadora):
                        _dbg_nesting(
                            f"[RTZ-SKIP-HOLE-OCUPADO] clave={clave} | "
                            f"placa={candidato_ganador.get('id')} | "
                            f"host={p.get('nombre')} | {w_r/25.4:.1f}x{h_r/25.4:.1f}\""
                        )
                        continue
                    id_retazo = nombre_rtz_para_placa(
                        contador_rtz_grupo, req_cal, wo_name, largo_mm=h_r, ancho_mm=w_r
                    )
                    poly_local = affinity.translate(hole_poly, -minx, -miny)
                    retazos_virtuales.append({
                        "id": id_retazo,
                        "w": w_r,
                        "h": h_r,
                        "poly_borde": poly_local,
                        "tipo": "HOLE",
                        "global_x": minx,
                        "global_y": miny,
                    })
                    contador_rtz_grupo += 1
                            
            max_x, max_y = 0, 0
            for p in list(hoja_ganadora['piezas']):
                if not _es_pieza_fisica_hoja(p.get('nombre')):
                    continue
                poly = reconstruir_poly_seguro(p['poligonos'])
                if poly:
                    _, _, mx, my = poly.bounds
                    if mx > max_x: max_x = mx
                    if my > max_y: max_y = my
                    
            w_orig, h_orig = candidato_ganador['w'], candidato_ganador['h']
            forzar_sin_mini_nest = _debe_forzar_sin_mini_nest(req_cal, w_orig, h_orig)
            if forzar_sin_mini_nest:
                _dbg_nesting(
                    f"[SIN-MINI-NEST] clave={clave} | placa_id={candidato_ganador.get('id')} | "
                    f"req_cal={req_cal} | w_in={min(w_orig,h_orig)/25.4:.3f} | l_in={max(w_orig,h_orig)/25.4:.3f} | "
                    "se mantiene placa madre intacta (sin hojas RTZ)."
                )
            
            if w_orig - max_x > 150:
                rem_der = box(max_x, 0, w_orig, h_orig)
                minx, miny, maxx, maxy = rem_der.bounds
                w_rem, h_rem = maxx - minx, maxy - miny
                if _retazo_cumple_tamano_minimo(w_rem, h_rem):
                    id_retazo = nombre_rtz_para_placa(
                        contador_rtz_grupo, req_cal, wo_name, largo_mm=h_rem, ancho_mm=w_rem
                    )
                    retazos_virtuales.append({"id": id_retazo, "w": w_rem, "h": h_rem, "poly_borde": affinity.translate(rem_der, -minx, -miny), "tipo": "SOBRANTE", "global_x": minx, "global_y": miny})
                    contador_rtz_grupo += 1

            if h_orig - max_y > 150:
                rem_arr = box(0, max_y, max_x, h_orig)
                minx, miny, maxx, maxy = rem_arr.bounds
                w_rem, h_rem = maxx - minx, maxy - miny
                if _retazo_cumple_tamano_minimo(w_rem, h_rem):
                    id_retazo = nombre_rtz_para_placa(
                        contador_rtz_grupo, req_cal, wo_name, largo_mm=h_rem, ancho_mm=w_rem
                    )
                    retazos_virtuales.append({"id": id_retazo, "w": w_rem, "h": h_rem, "poly_borde": affinity.translate(rem_arr, -minx, -miny), "tipo": "SOBRANTE", "global_x": minx, "global_y": miny})
                    contador_rtz_grupo += 1

            retazos_virtuales = [
                r
                for r in (
                    _filtrar_retazo_por_tamano_minimo(_clamp_retazo_mini_nest_a_cama_laser(r))
                    for r in retazos_virtuales
                )
                if r is not None
            ]

            for retazo in retazos_virtuales:
                rtz_usado = False

                if pendientes_est or accesorios:
                    candidatos_seguro = []
                    area_retazo = retazo['w'] * retazo['h']
                    
                    for p in pendientes_est + accesorios:
                        if p['area'] > (area_retazo * 0.85):
                            continue
                            
                        w_p, h_p = p['poly'].bounds[2] - p['poly'].bounds[0], p['poly'].bounds[3] - p['poly'].bounds[1]
                        min_p, max_p = min(w_p, h_p), max(w_p, h_p)
                        min_r, max_r = min(retazo['w'], retazo['h']), max(retazo['w'], retazo['h'])
                        if min_p <= min_r + 2.0 and max_p <= max_r + 2.0:
                            candidatos_seguro.append(copy.deepcopy(p))
                            
                    if candidatos_seguro:
                        hoja_retazo, restos_mezclados = _empaquetar_mejor_hoja_mc(
                            candidatos_seguro,
                            retazo['w'],
                            retazo['h'],
                            config_kerf,
                            config_margin,
                            config_opt,
                            config_corner,
                            limite_poly=retazo['poly_borde'],
                            debug_tag=f"clave={clave} | retazo={retazo.get('id')} | modo=retazo",
                            mc_iterations=mc_iters,
                            solo_accesorios=True,
                        )
                        hoja_ref_rtz = None
                        if refine_hoja and cu_refinar_intentos > 0:
                            hoja_ref_rtz = _refinar_hoja_empaque(
                                hoja_retazo,
                                candidatos_seguro,
                                retazo['w'],
                                retazo['h'],
                                config_kerf,
                                config_margin,
                                config_opt,
                                config_corner,
                                limite_poly=retazo['poly_borde'],
                                intentos=max(1, cu_refinar_intentos),
                                mc_iterations=mc_iters,
                            )
                        if hoja_ref_rtz and hoja_ref_rtz.get("piezas"):
                            hoja_retazo = hoja_ref_rtz
                            from .sheet_integrity import calcular_restos_desde_colocados

                            restos_mezclados = calcular_restos_desde_colocados(
                                candidatos_seguro, hoja_retazo
                            )
                        
                        if hoja_retazo.get('piezas'):
                            # Rechazar RTZ que empalmaría al proyectarse sobre la madre.
                            if _rtz_proyectado_choca_madre(
                                hoja_ganadora, retazo, hoja_retazo
                            ):
                                _dbg_nesting(
                                    f"[RTZ-REJECT-OVERLAP] clave={clave} | "
                                    f"retazo={retazo.get('id')} | tipo={retazo.get('tipo')} | "
                                    f"placa={candidato_ganador.get('id')}"
                                )
                                continue

                            rtz_usado = True

                            from .sheet_integrity import calcular_restos_desde_colocados

                            pendientes_est = calcular_restos_desde_colocados(
                                pendientes_est, hoja_retazo
                            )
                            accesorios = calcular_restos_desde_colocados(
                                accesorios, hoja_retazo
                            )

                            hoja_retazo.update({
                                'placa_id': retazo['id'],
                                'placa_w': retazo['w'],
                                'placa_h': retazo['h'],
                                'precio_placa': 0.0,
                                'kerf_usado': config_kerf,
                                'margin_usado': config_margin,
                                'opt_usado': config_opt,
                                'corner_usado': config_corner,
                                'es_retazo': True,
                                'poly_borde_retazo': list(retazo['poly_borde'].exterior.coords),
                                'origen_placa': candidato_ganador['origen'],
                                'global_x': retazo['global_x'],
                                'global_y': retazo['global_y'],
                                'retazo_tipo': retazo.get('tipo', 'SOBRANTE'),
                            })
                            
                            cx_local, cy_local = retazo['w'] / 2, retazo['h'] / 2
                            w_disp, h_disp = retazo['w'] * 0.5, retazo['h'] * 0.5
                            w_texto_local = max(50, min(w_disp * 0.85, 400))
                            h_texto_local = max(15, min(h_disp * 0.85, 40))
                            marks_t_local = generar_texto_vectorial(retazo['id'], cx_local, cy_local, w_texto_local, h_texto_local)
                            dummy_p_local = [[
                                (cx_local - 1, cy_local - 1),
                                (cx_local + 1, cy_local - 1),
                                (cx_local + 1, cy_local + 1),
                                (cx_local - 1, cy_local + 1),
                                (cx_local - 1, cy_local - 1)
                            ]]
                            hoja_retazo['piezas'].append({
                                "nombre": f"TATUAJE__{retazo['id']}",
                                "poligonos": dummy_p_local,
                                "marcas": marks_t_local,
                                "area": 0.0,
                                "calibre": req_cal,
                                "material": req_mat
                            })
                            
                            mini_nests_locales.append(hoja_retazo)

                            gx, gy = retazo['global_x'], retazo['global_y']
                            for p_acc in hoja_retazo['piezas']:
                                if p_acc['nombre'].startswith("REMANENTE__") or p_acc['nombre'].startswith("TATUAJE__"):
                                    continue

                                p_clon = copy.deepcopy(p_acc)
                                if forzar_sin_mini_nest:
                                    p_clon['nombre'] = f"{p_clon['nombre']}"
                                else:
                                    p_clon['nombre'] = f"REF__{p_clon['nombre']}"
                                    p_clon["rtz_overlay_id"] = retazo["id"]

                                if p_clon['poligonos']:
                                    p_clon['poligonos'] = _translate_poligonos_for_overlay(
                                        p_clon['poligonos'], gx, gy
                                    )
                                    
                                if p_clon['marcas']:
                                    nuevas_marcas = []
                                    for line_coords in p_clon['marcas']:
                                        try:
                                            nuevas_marcas.append(list(affinity.translate(LineString(line_coords), xoff=gx, yoff=gy).coords))
                                        except:
                                            nuevas_marcas.append(line_coords)
                                    p_clon['marcas'] = nuevas_marcas
                                    
                                hoja_ganadora['piezas'].append(p_clon)

                            actualizar_eficiencias_hoja(hoja_ganadora)

                if not rtz_usado:
                    continue
                if forzar_sin_mini_nest:
                    # No etiquetar ni generar remanentes/RTZ visibles cuando la regla pide placa intacta.
                    continue

                gx, gy = retazo['global_x'], retazo['global_y']
                espacio_libre = retazo['poly_borde']
                polys_restar = []
                
                for p_acc in hoja_ganadora['piezas']:
                    if (
                        p_acc['nombre'].startswith("REMANENTE__")
                        or p_acc['nombre'].startswith("TATUAJE__")
                        or p_acc['nombre'].startswith("RETAZO_GUILLOTINA__")
                        or p_acc['nombre'].startswith("CU_CORTE__")
                        or p_acc['nombre'].startswith("REF__")
                    ):
                        continue
                    try:
                        p_poly = Polygon(p_acc['poligonos'][0]).buffer(10.0)
                        p_poly_local = affinity.translate(p_poly, -gx, -gy)
                        if espacio_libre.intersects(p_poly_local):
                            polys_restar.append(p_poly_local)
                    except Exception:
                        pass
                
                if polys_restar:
                    try:
                        espacio_libre = espacio_libre.difference(unary_union(polys_restar))
                    except:
                        pass
                
                cx_local, cy_local = retazo['w'] / 2, retazo['h'] / 2
                w_disp, h_disp = retazo['w'] * 0.5, retazo['h'] * 0.5
                
                if not espacio_libre.is_empty:
                    if espacio_libre.geom_type == 'MultiPolygon':
                        best_poly = max(espacio_libre.geoms, key=lambda a: a.area)
                    elif espacio_libre.geom_type == 'Polygon':
                        best_poly = espacio_libre
                    else:
                        best_poly = None
                    
                    if best_poly:
                        minx_e, miny_e, maxx_e, maxy_e = best_poly.bounds
                        w_disp, h_disp = (maxx_e - minx_e), (maxy_e - miny_e)
                        cx_local, cy_local = best_poly.centroid.x, best_poly.centroid.y
                        if not best_poly.contains(best_poly.centroid):
                            rep_point = best_poly.representative_point()
                            cx_local, cy_local = rep_point.x, rep_point.y
                            w_disp *= 0.6
                            h_disp *= 0.6
                
                cx_t_global, cy_t_global = gx + cx_local, gy + cy_local
                w_texto = max(50, min(w_disp * 0.85, 400))
                h_texto = max(15, min(h_disp * 0.85, 40))
                
                marks_t_global = generar_texto_vectorial(retazo['id'], cx_t_global, cy_t_global, w_texto, h_texto)
                dummy_p_global = [[
                    (cx_t_global - 1, cy_t_global - 1),
                    (cx_t_global + 1, cy_t_global - 1),
                    (cx_t_global + 1, cy_t_global + 1),
                    (cx_t_global - 1, cy_t_global + 1),
                    (cx_t_global - 1, cy_t_global - 1)
                ]]
                
                if retazo['tipo'] == "HOLE":
                    hoja_ganadora['piezas'].append({
                        "nombre": f"TATUAJE__{retazo['id']}",
                        "poligonos": dummy_p_global,
                        "marcas": marks_t_global,
                        "area": 0.0,
                        "calibre": req_cal,
                        "material": req_mat
                    })
                else:
                    min_x, min_y, max_x, max_y = gx, gy, gx + retazo['w'], gy + retazo['h']
                    poly_g = [[
                        (min_x, min_y),
                        (max_x, min_y),
                        (max_x, max_y),
                        (min_x, max_y),
                        (min_x, min_y)
                    ]]

                    hoja_ganadora['piezas'].append({
                        "nombre": f"RETAZO_GUILLOTINA__{retazo['id']}",
                        "poligonos": poly_g,
                        "marcas": [],
                        "area": 0.0,
                        "calibre": req_cal,
                        "material": req_mat
                    })

                    hoja_ganadora['piezas'].append({
                        "nombre": f"TATUAJE__{retazo['id']}",
                        "poligonos": dummy_p_global,
                        "marcas": marks_t_global,
                        "area": 0.0,
                        "calibre": req_cal,
                        "material": req_mat
                    })
            # Poka-yoke: commit atómico madre(+RTZ) solo si no hay solapes (incl. RTZ).
            bloque_commit = [hoja_ganadora]
            if not forzar_sin_mini_nest:
                bloque_commit.extend(mini_nests_locales)
            from .nest_poka_yoke import validar_integridad_bloque_hojas

            ok_c, msg_c = validar_integridad_bloque_hojas(bloque_commit)
            if not ok_c:
                _dbg_nesting(
                    f"[COMMIT-PLACA-INTEGRIDAD] clave={clave} | "
                    f"placa={candidato_ganador.get('id')} | {msg_c}"
                )
                return clave, {
                    "error": (
                        f"Integridad al commit de placa {candidato_ganador.get('id')}: "
                        f"{msg_c}"
                    ),
                }
            hojas_finales.append(hoja_ganadora)
            if not forzar_sin_mini_nest:
                hojas_finales.extend(mini_nests_locales)
            costo_total_lote += candidato_ganador['precio']
            num_placa_actual += 1

        if hojas_finales:
            if _es_motor_arga_force():
                hojas_finales, costo_total_lote = _post_proceso_arga_seguro(
                    piezas,
                    hojas_finales,
                    costo_total_lote,
                    config_kerf,
                    config_margin,
                    config_opt,
                    config_corner,
                    clave=clave,
                )

            from .sheet_integrity import sanitizar_hojas_grupo, validar_colocacion_completa

            hojas_finales = sanitizar_hojas_grupo(
                piezas, hojas_finales, clave=clave, kerf_global=config_kerf
            )
            ok_inv, msg_inv = validar_colocacion_completa(piezas, hojas_finales)
            if not ok_inv:
                _dbg_nesting(f"[INVENTARIO-INCOMPLETO] clave={clave} | {msg_inv}")
                inventario_aviso = msg_inv
                from .nest_poka_yoke import allow_incomplete_nest, aplicar_resultado_inventario

                # Conservar hojas ya nestéadas; el poka marca error/aviso sin borrar progreso.
                grupo_parcial = {
                    "hojas": hojas_finales,
                    "costo_total": costo_total_lote,
                    "advertencia": msg_inv,
                    "inventario_incompleto": True,
                }
                aplicar_resultado_inventario(
                    grupo_parcial, ok_inv=False, msg_inv=msg_inv
                )
                if not allow_incomplete_nest():
                    grupo_parcial["error"] = (
                        f"{msg_inv} "
                        "(Poka-yoke: nest incompleto rechazado. "
                        "ARGA_ALLOW_INCOMPLETE_NEST=1 para aviso suave.)"
                    )
                return clave, grupo_parcial
            # Construimos mapa 1-a-1 por nombre base para no agarrar siempre
            # la primera coincidencia cuando hay piezas repetidas.
            source_map = {}
            for p_orig in piezas:
                base = _piece_name_base(p_orig.get("nombre"))
                source_map.setdefault(base, []).append(p_orig)

            for hoja in hojas_finales:
                for p_final in hoja.get('piezas', []):
                    nombre_final = str(p_final.get("nombre") or "")

                    # Estas piezas no vienen de un AutoDXF real exportable
                    if _is_virtual_piece(nombre_final):
                        continue

                    base = _piece_name_base(nombre_final)
                    candidatos = source_map.get(base, [])

                    if not candidatos:
                        _dbg_nesting(
                            f"[MATCH-FAIL] clave={clave} | nombre_final={nombre_final} | base={base}"
                        )
                        continue

                    # Consumo secuencial para evitar usar siempre el mismo original
                    p_orig = candidatos.pop(0)

                    if p_orig.get("debug_id"):
                        p_final["debug_id"] = p_orig.get("debug_id")

                    p_final['ruta'] = p_orig.get('ruta')
                    p_final['orig_minx'] = p_orig.get('orig_minx', 0.0)
                    p_final['orig_miny'] = p_orig.get('orig_miny', 0.0)

                    transform = _inferir_transformacion_desde_resultado(p_orig, p_final)

                    rot_origin = _origen_rotacion_pieza(
                        p_orig.get("poly_exact") or p_orig.get("poly")
                    )
                    p_final["rot_origin_cx"] = rot_origin[0]
                    p_final["rot_origin_cy"] = rot_origin[1]

                    if transform:
                        p_final['rot_deg'] = transform['rot_deg']
                        p_final['shift_x'] = transform['shift_x']
                        p_final['shift_y'] = transform['shift_y']

                        _dbg_nesting(
                            f"[MATCH-OK] clave={clave} | nombre_final={nombre_final} | "
                            f"rot={p_final['rot_deg']} | "
                            f"shift_x={p_final['shift_x']:.3f} | "
                            f"shift_y={p_final['shift_y']:.3f}"
                        )
                    else:
                        p_final['rot_deg'] = 0.0
                        p_final['shift_x'] = 0.0
                        p_final['shift_y'] = 0.0

                        _dbg_nesting(
                            f"[MATCH-FALLBACK] clave={clave} | nombre_final={nombre_final} | "
                            f"se aplicó fallback neutro"
                        )

                    _colocar_geometria_exacta_en_pieza(p_orig, p_final, transform)
                    n_holes = max(0, len(p_final.get("poligonos") or []) - 1)
                    n_marks = len(p_final.get("marcas") or [])
                    _dbg_nesting(
                        f"[MATCH-OK-META] clave={clave} | nombre_final={nombre_final} | "
                        f"anillos={len(p_final.get('poligonos') or [])} | holes={n_holes} | "
                        f"marcas={n_marks}"
                    )

            # Tras remap DXF 1:1: revalidar solapes (geom exacta puede diferir del pack).
            from .nest_poka_yoke import validar_integridad_bloque_hojas

            ok_post, msg_post = validar_integridad_bloque_hojas(hojas_finales)
            if not ok_post:
                _dbg_nesting(f"[POST-EXACT-INTEGRIDAD] clave={clave} | {msg_post}")
                return clave, {
                    "error": (
                        f"Integridad tras geometría exacta DXF: {msg_post}"
                    ),
                }
        
        if hojas_finales:
            costo_empresa = sum(
                h.get('precio_placa', 0.0)
                for h in hojas_finales
                if h.get('origen_placa') == "EMPRESA" and not h.get('es_retazo')
            )
            costo_proveedor = sum(
                h.get('precio_placa', 0.0)
                for h in hojas_finales
                if h.get('origen_placa') != "EMPRESA" and not h.get('es_retazo')
            )

            # --- Compact-lite (barato) + Venom opt-in ---
            import os
            venom_reward_total = 0.0
            venom_compact_pre = 0.0
            venom_compact_post = 0.0
            venom_sheets = 0
            engine_id = getattr(self, "active_engine_id", "default")
            try:
                from . import compact_lite

                if compact_lite.compact_enabled():
                    for hoja in hojas_finales:
                        if isinstance(hoja, dict) and (hoja.get("piezas") or []):
                            compact_lite.apply_band_compact(hoja, engine_id=engine_id)
            except Exception as compact_ex:
                _dbg_nesting(f"[COMPACT-BATCH-ERR] {compact_ex}")
            # Lite hole-fill DESPUÉS del compact final (huéspedes en orificios).
            try:
                from .venom_hole_fill import apply_lite_hole_fill, lite_hole_fill_enabled

                if lite_hole_fill_enabled():
                    for hoja in hojas_finales:
                        if isinstance(hoja, dict) and (hoja.get("piezas") or []):
                            apply_lite_hole_fill(hoja, engine_id=engine_id)
            except Exception as hole_ex:
                _dbg_nesting(f"[LITE-HOLE-FILL-BATCH-ERR] {hole_ex}")
            try:
                from . import venom_ai

                for hoja in hojas_finales:
                    venom_ai.apply_smart_polisher(hoja, engine_id)
                    if "venom_reward" in hoja:
                        venom_reward_total += float(hoja.get("venom_reward") or 0.0)
                        venom_compact_pre += float(hoja.get("venom_compactness_pre") or 0.0)
                        venom_compact_post += float(hoja.get("venom_compactness_post") or 0.0)
                        venom_sheets += 1
            except Exception as e:
                import traceback
                with open(r"c:\Proyectos\New Arga Nesting Suite\_logs\venom_debug.log", "a") as f:
                    f.write(f"ERROR EN VENOM BATCH: {e}\n{traceback.format_exc()}\n")
            # ------------------------------------

            sincronizar_overlays_grupo(hojas_finales)
            efi_grupo = calcular_eficiencias_grupo(hojas_finales)
            resultado_placas = {
                "placa": "Óptima",
                "dim": "Multi",
                "hojas": hojas_finales,
                "piezas_pool": [
                    {"nombre": str(p.get("nombre") or "")}
                    for p in (piezas or [])
                    if str(p.get("nombre") or "")
                ],
                "piezas_pool_engine": True,
                "costo_total": costo_total_lote,
                "costo_empresa": costo_empresa,
                "costo_proveedor": costo_proveedor,
                "reporte": "Reporte Generado.",
                "match_placa": match_mode,
                "calibre_pieza": req_cal,
                **efi_grupo,
            }
            if inventario_aviso:
                resultado_placas["advertencia"] = inventario_aviso

            clave_out = clave
            
            # --- APRENDIZAJE IA POR MOTOR (señal de grupo + compactación Venom) ---
            import os
            from .ai_heuristic import record_telemetry, get_last_seed_info
            engine_id = getattr(self, "active_engine_id", None) or os.environ.get(
                "ARGA_MOTOR_NESTING", "svgnest_ultra"
            )
            eff_real = float(
                efi_grupo.get("eficiencia_tanque_real")
                or efi_grupo.get("efficiency_real")
                or efi_grupo.get("eficiencia_real")
                or 0.0
            )
            seed_info = get_last_seed_info(engine_id)
            nest_reward = None
            c_pre = c_post = None
            if venom_sheets > 0:
                c_pre = venom_compact_pre / venom_sheets
                c_post = venom_compact_post / venom_sheets
                # Reward compuesto: eficiencia real del tanque + Δcompactación media.
                nest_reward = eff_real + (venom_reward_total / venom_sheets)
            if eff_real > 0.5 or nest_reward is not None:
                record_telemetry(
                    piezas,
                    eff_real,
                    engine_id,
                    compactness_pre=c_pre,
                    compactness_post=c_post,
                    seed_policy=seed_info.get("policy"),
                    nest_reward=nest_reward if nest_reward is not None else eff_real,
                )
            try:
                from .ai_telemetry import log_nest_event

                hojas = list(resultado_placas.get("hojas") or [])
                rem_ids = [
                    str(h.get("placa_id") or "")
                    for h in hojas
                    if "REMANENTE" in str(h.get("origen_placa") or "").upper()
                    or str(h.get("placa_id") or "").upper().startswith("PL-")
                ]
                log_nest_event(
                    wo=str(getattr(self, "_last_wo_name", "") or ""),
                    calibre=str(req_cal or ""),
                    material=str(req_mat or ""),
                    engine=str(engine_id or ""),
                    profile=str(os.environ.get("ARGA_NEST_MODE") or ""),
                    n_piezas=len(piezas or []),
                    n_sheets=len(hojas),
                    efi=float(eff_real or 0.0),
                    remnant_used=bool(rem_ids),
                    remnant_ids=rem_ids,
                    seed_policy=str(seed_info.get("policy") or ""),
                    seed_order=[str(p.get("nombre") or "") for p in (piezas or [])],
                    nest_reward=float(
                        nest_reward if nest_reward is not None else eff_real or 0.0
                    ),
                    source="manager_grupo",
                    extra={"clave": str(clave), "match_mode": str(match_mode)},
                )
            except Exception:
                pass
            # --------------------------------
            
            return clave_out, resultado_placas
        else:
            return clave, {
                "error": (
                    f"No se generaron hojas para {clave}: el nest quedó vacío "
                    f"tras el empaque. Revise inventario, cancelación o DXF."
                )
            }

    def _as_pack_piece_visual(self, p):
        poly = reconstruir_poly_seguro(p.get("poligonos") or [])
        if poly is None or poly.is_empty:
            return None

        marks_geom = _rebuild_marks_geom(p.get("marcas") or [])
        if marks_geom is None:
            marks_geom = LineString()

        minx, miny, _, _ = poly.bounds
        pieza_pack = {
            "nombre": str(p.get("nombre", "")),
            "poly": affinity.translate(poly, -minx, -miny),
            "marks": affinity.translate(marks_geom, -minx, -miny) if not marks_geom.is_empty else marks_geom,
            "area": float(p.get("area", poly.area) or poly.area),
            "calibre": p.get("calibre", ""),
            "material": p.get("material", ""),
            "ruta": p.get("ruta", ""),
        }
        # Una transferencia vuelve a empacar origen/destino. Conservar estos
        # campos evita que una pieza compensada pierda su geometría/estado al
        # pasar por el pack visual.
        for campo in (
            "debug_id",
            "plasma_compensada_manual",
            "plasma_offset_mm_manual",
            "plasma_fuente_ya_compensada",
            "ruta_plasma",
        ):
            if p.get(campo) is not None:
                pieza_pack[campo] = p.get(campo)
        return pieza_pack

    def _piezas_reales_en_hoja(self, hoja):
        piezas = []
        for p in (hoja.get("piezas") or []):
            if _is_virtual_piece(str(p.get("nombre", ""))):
                continue
            piezas.append(p)
        return piezas

    def _conteo_piezas_reales_en_hojas(self, hojas):
        conteo: dict[str, int] = {}
        for h in hojas or []:
            if not isinstance(h, dict):
                continue
            for p in self._piezas_reales_en_hoja(h):
                nom = str(p.get("nombre", "") or "")
                if nom:
                    conteo[nom] = conteo.get(nom, 0) + 1
        return conteo

    def _misma_pieza_visual(self, a, b):
        if a is b or id(a) == id(b):
            return True
        if not isinstance(a, dict) or not isinstance(b, dict):
            return False
        dbg_a = str(a.get("debug_id") or "").strip()
        dbg_b = str(b.get("debug_id") or "").strip()
        if dbg_a and dbg_b and dbg_a == dbg_b:
            return True
        if str(a.get("nombre", "")) != str(b.get("nombre", "")):
            return False
        if a.get("poligonos") == b.get("poligonos"):
            return True
        # Tras deepcopy entre WOs, la geometría colocada suele conservar el mismo offset.
        try:
            return (
                abs(float(a.get("shift_x", 0) or 0) - float(b.get("shift_x", 0) or 0)) <= 1e-6
                and abs(float(a.get("shift_y", 0) or 0) - float(b.get("shift_y", 0) or 0)) <= 1e-6
                and abs(float(a.get("rot_deg", 0) or 0) - float(b.get("rot_deg", 0) or 0)) <= 1e-6
            )
        except (TypeError, ValueError):
            return False

    def _grupo_de_hoja(self, resultados_nesting, hoja):
        if not isinstance(hoja, dict):
            return None
        for _, grupo in resultados_nesting.items():
            if isinstance(grupo, dict) and hoja in (grupo.get("hojas") or []):
                return grupo
        uid = str(hoja.get("sheet_uid") or "").strip()
        if uid:
            for _, grupo in resultados_nesting.items():
                if not isinstance(grupo, dict):
                    continue
                for h in (grupo.get("hojas") or []):
                    if str(h.get("sheet_uid") or "").strip() == uid:
                        return grupo
        return None

    def _idx_hoja_en_grupo(self, hojas, hoja):
        if not isinstance(hojas, list) or not isinstance(hoja, dict):
            return -1
        try:
            return hojas.index(hoja)
        except ValueError:
            pass
        uid = str(hoja.get("sheet_uid") or "").strip()
        if uid:
            for i, h in enumerate(hojas):
                if str(h.get("sheet_uid") or "").strip() == uid:
                    return i
        pid = str(hoja.get("placa_id", "") or "")
        es_rtz = bool(hoja.get("es_retazo", False))
        w_ref = float(hoja.get("placa_w", 0) or 0)
        h_ref = float(hoja.get("placa_h", 0) or 0)
        nest_idx = hoja.get("_nest_list_idx")
        if nest_idx is not None:
            ni = int(nest_idx)
            if 0 <= ni < len(hojas):
                h_cand = hojas[ni]
                if (
                    str(h_cand.get("placa_id", "") or "") == pid
                    and bool(h_cand.get("es_retazo", False)) == es_rtz
                    and abs(float(h_cand.get("placa_w", 0) or 0) - w_ref) <= 0.5
                    and abs(float(h_cand.get("placa_h", 0) or 0) - h_ref) <= 0.5
                ):
                    return ni
        # Evitar primera coincidencia ambigua por placa_id: solo si es única.
        if pid:
            matches = [
                i
                for i, h in enumerate(hojas)
                if str(h.get("placa_id", "") or "") == pid
                and bool(h.get("es_retazo", False)) == es_rtz
            ]
            if len(matches) == 1:
                return matches[0]
        return -1

    def _resolver_hoja_viva(self, resultados, hoja):
        """Devuelve la referencia actual de la hoja dentro de resultados."""
        if not isinstance(hoja, dict):
            return hoja
        grupo = self._grupo_de_hoja(resultados, hoja)
        if not isinstance(grupo, dict):
            return hoja
        hojas = grupo.get("hojas") or []
        if hoja in hojas:
            return hoja
        idx = self._idx_hoja_en_grupo(hojas, hoja)
        if 0 <= idx < len(hojas):
            return hojas[idx]
        return hoja

    def _pieza_real_en_hoja_por_idx(self, hoja, idx):
        piezas = (hoja or {}).get("piezas") or []
        if not isinstance(idx, int) or idx < 0 or idx >= len(piezas):
            return None
        p = piezas[idx]
        if _is_virtual_piece(str(p.get("nombre", ""))):
            return None
        return p

    def _resolver_candidatos_transferencia(
        self, hoja_origen, piezas_especificas, indices=None
    ):
        """Empareja piezas seleccionadas con las de la hoja (tolerante a re-nest)."""
        todas_origen = self._piezas_reales_en_hoja(hoja_origen)
        if not piezas_especificas:
            return list(todas_origen)

        indices = list(indices or [])
        candidatos = []
        usados = set()

        for k, ps in enumerate(piezas_especificas):
            idx_hint = indices[k] if k < len(indices) else None
            encontrada = None
            nombre_ps = str(ps.get("nombre", "") or "")
            dbg_ps = str(ps.get("debug_id") or "").strip()

            if idx_hint is not None:
                p_idx = self._pieza_real_en_hoja_por_idx(hoja_origen, idx_hint)
                if p_idx is not None and id(p_idx) not in usados:
                    nombre_idx = str(p_idx.get("nombre", "") or "")
                    dbg_idx = str(p_idx.get("debug_id") or "").strip()
                    if (
                        not nombre_ps
                        or nombre_idx == nombre_ps
                        or (dbg_ps and dbg_idx and dbg_idx == dbg_ps)
                        or self._misma_pieza_visual(p_idx, ps)
                    ):
                        encontrada = p_idx

            if encontrada is None:
                for p in todas_origen:
                    if id(p) in usados:
                        continue
                    if p is ps or id(p) == id(ps):
                        encontrada = p
                        break

            if encontrada is None and dbg_ps:
                for p in todas_origen:
                    if id(p) in usados:
                        continue
                    if str(p.get("debug_id") or "").strip() == dbg_ps:
                        encontrada = p
                        break

            if encontrada is None:
                matches = [
                    p
                    for p in todas_origen
                    if id(p) not in usados and str(p.get("nombre", "") or "") == nombre_ps
                ]
                if len(matches) == 1:
                    encontrada = matches[0]
                elif len(matches) > 1 and idx_hint is not None:
                    piezas = (hoja_origen or {}).get("piezas") or []
                    if 0 <= idx_hint < len(piezas):
                        target = piezas[idx_hint]
                        for p in matches:
                            if p is target or id(p) == id(target):
                                encontrada = p
                                break
                    if encontrada is None:
                        for p in matches:
                            if self._misma_pieza_visual(p, ps):
                                encontrada = p
                                break
                if encontrada is None and matches:
                    for p in matches:
                        if self._misma_pieza_visual(p, ps):
                            encontrada = p
                            break
                    if encontrada is None:
                        encontrada = matches[0]

            if encontrada is None:
                for p in todas_origen:
                    if id(p) in usados:
                        continue
                    if self._misma_pieza_visual(p, ps):
                        encontrada = p
                        break

            if encontrada is not None:
                candidatos.append(encontrada)
                usados.add(id(encontrada))

        return candidatos

    def _localizar_hoja_origen(
        self, resultados_nesting, pieza_info, hoja_origen=None, idx_hint=None
    ):
        origen_grupo = None
        origen_hoja = None
        idx_origen = -1

        if isinstance(hoja_origen, dict):
            piezas = hoja_origen.get("piezas") or []
            origen_grupo = self._grupo_de_hoja(resultados_nesting, hoja_origen)

            if isinstance(idx_hint, int):
                p_idx = self._pieza_real_en_hoja_por_idx(hoja_origen, idx_hint)
                if p_idx is not None and origen_grupo is not None:
                    return origen_grupo, hoja_origen, idx_hint

            for i, p in enumerate(piezas):
                if p is pieza_info or id(p) == id(pieza_info):
                    if origen_grupo is not None:
                        return origen_grupo, hoja_origen, i

            for i, p in enumerate(piezas):
                if self._misma_pieza_visual(p, pieza_info):
                    if origen_grupo is not None:
                        return origen_grupo, hoja_origen, i

            nombre_pieza = str(pieza_info.get("nombre", "") or "")
            matches = [
                (i, p)
                for i, p in enumerate(piezas)
                if not _is_virtual_piece(str(p.get("nombre", "")))
                and str(p.get("nombre", "")) == nombre_pieza
            ]
            if len(matches) == 1 and origen_grupo is not None:
                return origen_grupo, hoja_origen, matches[0][0]
            return None, None, -1

        nombre_pieza = str(pieza_info.get("nombre", "") or "")
        for _, grupo in resultados_nesting.items():
            if not isinstance(grupo, dict):
                continue
            for hoja in (grupo.get("hojas") or []):
                for i, p in enumerate(hoja.get("piezas") or []):
                    if str(p.get("nombre", "")) != nombre_pieza:
                        continue
                    if self._misma_pieza_visual(p, pieza_info):
                        return grupo, hoja, i
                    if origen_hoja is None:
                        origen_grupo = grupo
                        origen_hoja = hoja
                        idx_origen = i
        return origen_grupo, origen_hoja, idx_origen

    def _pack_piezas_destino(self, hoja_destino):
        piezas_dest = []
        for p in self._piezas_reales_en_hoja(hoja_destino):
            pp = self._as_pack_piece_visual(p)
            if pp is not None:
                piezas_dest.append(pp)
        return piezas_dest

    def _params_hoja(self, hoja):
        return {
            "kerf": float(hoja.get("kerf_usado", DEFAULT_KERF_IN) or DEFAULT_KERF_IN),
            "margin": float(hoja.get("margin_usado", DEFAULT_MARGIN_IN) or DEFAULT_MARGIN_IN),
            "opt": hoja.get("opt_usado", "OPTIMIZAR LARGO Y ANCHO"),
            "corner": hoja.get("corner_usado", "INFERIOR IZQUIERDA"),
            "w": float(hoja.get("placa_w", 0.0) or 0.0),
            "h": float(hoja.get("placa_h", 0.0) or 0.0),
        }

    def _copiar_meta_placa(self, nueva, plantilla, params):
        nueva.update({
            "placa_id": plantilla.get("placa_id"),
            "placa_w": params["w"],
            "placa_h": params["h"],
            "precio_placa": plantilla.get("precio_placa", 0.0),
            "origen_placa": plantilla.get("origen_placa", "EMPRESA"),
            "es_retazo": plantilla.get("es_retazo", False),
            "id_remanente_usado": plantilla.get("id_remanente_usado"),
            "kerf_usado": params["kerf"],
            "margin_usado": params["margin"],
            "opt_usado": params["opt"],
            "corner_usado": params["corner"],
        })
        if plantilla.get("poly_borde_retazo") is not None:
            nueva["poly_borde_retazo"] = plantilla.get("poly_borde_retazo")

    def _es_pieza_overlay(self, nombre):
        n = str(nombre or "")
        return _is_virtual_piece(n) or n.startswith("REF__")

    def _extraer_overlays_hoja(self, hoja):
        overlays = []
        for p in (hoja.get("piezas") or []):
            if self._es_pieza_overlay(str(p.get("nombre", ""))):
                overlays.append(copy.deepcopy(p))
        return overlays

    def _fusionar_piezas_con_overlays(self, piezas_nuevas, overlays):
        merged = list(piezas_nuevas or [])
        if not overlays:
            return merged
        nombres = {str(p.get("nombre", "")) for p in merged}
        for ov in overlays:
            nom = str(ov.get("nombre", ""))
            if nom and nom not in nombres:
                merged.append(ov)
                nombres.add(nom)
        return merged

    def _limite_poly_desde_hoja(self, hoja):
        if not hoja.get("es_retazo"):
            return None
        coords = hoja.get("poly_borde_retazo")
        if not coords:
            return None
        try:
            poly = Polygon(coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                return None
            return poly
        except Exception:
            return None

    def _rtz_hoja_para_id(self, madre, hojas_grupo, rtz_id):
        if not isinstance(madre, dict) or not isinstance(hojas_grupo, list):
            return None
        idx = self._idx_hoja_en_grupo(hojas_grupo, madre)
        if idx < 0:
            return None
        rid = str(rtz_id or "")
        for rtz in _rtz_hojas_de_madre(hojas_grupo, idx):
            if str(rtz.get("placa_id", "") or "") == rid:
                return rtz
        return None

    def _rtz_ids_vacios_en_madre(self, madre, hojas_grupo):
        if not isinstance(madre, dict) or not isinstance(hojas_grupo, list):
            return ()
        idx = self._idx_hoja_en_grupo(hojas_grupo, madre)
        if idx < 0:
            return ()
        vacios = []
        for rtz in _rtz_hojas_de_madre(hojas_grupo, idx):
            rid = str(rtz.get("placa_id", "") or "")
            if rid and not self._piezas_reales_en_hoja(rtz):
                vacios.append(rid)
        return tuple(vacios)

    def _excluir_rtz_para_transfer(self, hoja_origen, hoja_destino, hojas_grupo):
        ids = list(
            self._rtz_ids_a_liberar_en_destino(hoja_origen, hoja_destino, hojas_grupo)
        )
        ids.extend(self._rtz_ids_vacios_en_madre(hoja_destino, hojas_grupo))
        return tuple(dict.fromkeys(ids))

    def _zonas_rtz_reservadas_madre(self, madre, hojas_grupo=None, excluir_rtz_ids=None):
        """Áreas de placa madre reservadas a RTZ/mini-nest (no anidar piezas madre)."""
        zonas = []
        if not isinstance(madre, dict) or madre.get("es_retazo"):
            return zonas

        excluir = {str(x) for x in (excluir_rtz_ids or ()) if x}

        def _agregar(poly):
            if poly is None or getattr(poly, "is_empty", True):
                return
            for z in zonas:
                try:
                    inter = z.intersection(poly)
                    if not inter.is_empty and inter.area >= min(z.area, poly.area) * 0.95:
                        return
                except Exception:
                    pass
            zonas.append(poly)

        for p in (madre.get("piezas") or []):
            nom = str(p.get("nombre", "") or "")
            if nom.startswith("RETAZO_GUILLOTINA__"):
                rid = nom.replace("RETAZO_GUILLOTINA__", "", 1)
                if rid in excluir:
                    continue
                rtz = self._rtz_hoja_para_id(madre, hojas_grupo, rid)
                if rtz is not None and not self._piezas_reales_en_hoja(rtz):
                    continue
                _agregar(reconstruir_poly_seguro(p.get("poligonos") or []))

        guillotine_rids = {
            str(p.get("nombre", "") or "").replace("RETAZO_GUILLOTINA__", "", 1)
            for p in (madre.get("piezas") or [])
            if str(p.get("nombre", "") or "").startswith("RETAZO_GUILLOTINA__")
        }

        if not isinstance(hojas_grupo, list):
            return zonas
        idx = self._idx_hoja_en_grupo(hojas_grupo, madre)
        if idx < 0:
            return zonas

        for rtz in _rtz_hojas_de_madre(hojas_grupo, idx):
            rtz_id = str(rtz.get("placa_id", "") or "")
            if rtz_id in excluir:
                continue
            # RTZ vacío sin overlay guillotine: el área sigue libre en la madre.
            if not self._piezas_reales_en_hoja(rtz) and rtz_id not in guillotine_rids:
                continue
            gx, gy = _inferir_global_rtz(madre, rtz)
            rw = float(rtz.get("placa_w", 0) or 0)
            rh = float(rtz.get("placa_h", 0) or 0)
            borde = rtz.get("poly_borde_retazo")
            if borde and len(borde) >= 3:
                try:
                    local = Polygon(borde)
                    if not local.is_valid:
                        local = local.buffer(0)
                    if not local.is_empty:
                        _agregar(affinity.translate(local, xoff=gx, yoff=gy))
                        continue
                except Exception:
                    pass
            if rw > 0 and rh > 0:
                _agregar(box(gx, gy, gx + rw, gy + rh))
        return zonas

    def _rtz_ids_a_liberar_en_destino(self, hoja_origen, hoja_destino, hojas_grupo):
        """
        Si la pieza sale de un RTZ hacia su placa madre padre, no reservar esa zona RTZ.
        """
        if not isinstance(hoja_origen, dict) or not hoja_origen.get("es_retazo"):
            return ()
        if not isinstance(hojas_grupo, list):
            return ()
        idx = self._idx_hoja_en_grupo(hojas_grupo, hoja_origen)
        if idx <= 0:
            return ()
        madre = hojas_grupo[idx - 1]
        if madre is not hoja_destino or madre.get("es_retazo"):
            return ()
        rid = str(hoja_origen.get("placa_id", "") or "")
        return (rid,) if rid else ()

    def _pieza_invade_zona_rtz(self, poly, zona, clearance_mm=0.0):
        """True solo si hay solape de área dentro del RTZ (tocar el borde no cuenta)."""
        if poly is None or poly.is_empty or zona is None or getattr(zona, "is_empty", True):
            return False
        try:
            gap = float(clearance_mm or 0.0)
            test = poly.buffer(gap / 2.0) if gap > 1e-6 else poly
            inter = test.intersection(zona)
            if inter.is_empty:
                return False
            return float(inter.area) > 1.0
        except Exception:
            return False

    def _piezas_invaden_zonas_rtz(self, hoja_resultado, zonas_rtz, clearance_mm=0.0):
        if not zonas_rtz or not isinstance(hoja_resultado, dict):
            return False
        for p in (hoja_resultado.get("piezas") or []):
            if _is_virtual_piece(str(p.get("nombre", ""))):
                continue
            poly = reconstruir_poly_seguro(p.get("poligonos") or [])
            if poly is None or poly.is_empty:
                continue
            for zona in zonas_rtz:
                if self._pieza_invade_zona_rtz(poly, zona, clearance_mm):
                    return True
        return False

    def _limite_util_destino_transfer(self, hoja, margin_mm):
        limite = self._limite_poly_desde_hoja(hoja)
        if limite is not None:
            if margin_mm > 1e-6:
                try:
                    inner = limite.buffer(-margin_mm)
                    if not inner.is_empty:
                        return inner
                except Exception:
                    pass
            return limite
        params = self._params_hoja(hoja)
        w = float(params["w"] or 0)
        h = float(params["h"] or 0)
        if w <= 0 or h <= 0:
            return None
        m = max(0.0, float(margin_mm or 0.0))
        return box(m, m, w - m, h - m)

    def _obstaculos_transfer_destino(self, hoja_destino, kerf_mm):
        gap = max(0.0, float(kerf_mm or 0.0) / 2.0)
        obstaculos = []
        for p in self._piezas_reales_en_hoja(hoja_destino):
            poly = reconstruir_poly_seguro(p.get("poligonos") or [])
            if poly is None or poly.is_empty:
                continue
            try:
                obst = poly.buffer(gap) if gap > 1e-6 else poly
                if not obst.is_empty:
                    obstaculos.append(obst)
            except Exception:
                obstaculos.append(poly)
        return obstaculos

    def _poly_dentro_limite_transfer(self, poly, limite):
        if limite is None or poly is None or poly.is_empty:
            return True
        try:
            return limite.contains(poly) or limite.covers(poly)
        except Exception:
            try:
                return limite.intersection(poly).area >= poly.area * 0.995
            except Exception:
                return False

    def _poly_colisiona_obstaculos_transfer(self, poly, obstaculos):
        if poly is None or poly.is_empty:
            return True
        gap_test = poly.buffer(0.05) if not poly.is_empty else poly
        for obs in obstaculos:
            try:
                if gap_test.intersects(obs):
                    inter = gap_test.intersection(obs)
                    if not inter.is_empty and float(inter.area) > 0.5:
                        return True
            except Exception:
                return True
        return False

    def _poly_invade_zonas_rtz_transfer(self, poly, zonas_rtz, clearance_mm):
        if not zonas_rtz or poly is None or poly.is_empty:
            return False
        for zona in zonas_rtz:
            if self._pieza_invade_zona_rtz(poly, zona, clearance_mm):
                return True
        return False

    def _build_variaciones_transfer(self, poly_src, marks_src, w_placa, h_placa, margin_mm, kerf_radio):
        variaciones = []
        if poly_src is None or poly_src.is_empty:
            return variaciones
        marks_src = marks_src if marks_src is not None else LineString()
        origin = poly_src.centroid
        for angulo in TRANSFER_ROTATIONS:
            poly_rot = poly_src if angulo == 0 else affinity.rotate(poly_src, angulo, origin=origin)
            marks_rot = marks_src
            if angulo != 0 and not marks_src.is_empty:
                marks_rot = affinity.rotate(marks_src, angulo, origin=origin)

            minx, miny, maxx, maxy = poly_rot.bounds
            w_p, h_p = maxx - minx, maxy - miny
            poly_rot = affinity.translate(poly_rot, -minx, -miny)
            if not marks_rot.is_empty:
                marks_rot = affinity.translate(marks_rot, -minx, -miny)

            if w_p > (w_placa - (2.0 * margin_mm) + 5.0) or h_p > (h_placa - (2.0 * margin_mm) + 5.0):
                continue

            try:
                coords = list(poly_rot.exterior.coords)
                poly_shell = Polygon(coords).buffer(0.01).simplify(0.1, preserve_topology=False)
                poly_buff = poly_shell.buffer(kerf_radio, resolution=2, join_style=1)
                if poly_buff.geom_type == "MultiPolygon":
                    poly_buff = max(poly_buff.geoms, key=lambda g: g.area)
                if not poly_buff.is_valid:
                    poly_buff = poly_buff.buffer(0)
            except Exception:
                poly_buff = poly_rot.convex_hull.buffer(kerf_radio)

            b_minx, b_miny, b_maxx, b_maxy = poly_buff.bounds
            m_minx, m_miny, m_maxx, m_maxy = poly_rot.bounds
            variaciones.append(
                {
                    "rot": angulo,
                    "poly": poly_rot,
                    "poly_buff": poly_buff,
                    "marks": marks_rot,
                    "b_minx": b_minx,
                    "b_miny": b_miny,
                    "b_maxx": b_maxx,
                    "b_maxy": b_maxy,
                    "m_minx": m_minx,
                    "m_miny": m_miny,
                    "m_maxx": m_maxx,
                    "m_maxy": m_maxy,
                }
            )
        return variaciones

    def _fijas_y_anclas_destino_transfer(self, hoja_destino, kerf_radio):
        fijas_bounds = []
        fijas_preps = []
        anclajes = []
        for p in self._piezas_reales_en_hoja(hoja_destino):
            poly = reconstruir_poly_seguro(p.get("poligonos") or [])
            if poly is None or poly.is_empty:
                continue
            try:
                coords = list(poly.exterior.coords)
                poly_shell = Polygon(coords).buffer(0.01).simplify(0.1, preserve_topology=False)
                poly_buff = poly_shell.buffer(kerf_radio, resolution=2, join_style=1)
                if poly_buff.geom_type == "MultiPolygon":
                    poly_buff = max(poly_buff.geoms, key=lambda g: g.area)
                if not poly_buff.is_valid:
                    poly_buff = poly_buff.buffer(0)
            except Exception:
                poly_buff = poly.convex_hull.buffer(kerf_radio)
            b = poly_buff.bounds
            fijas_bounds.append(b)
            fijas_preps.append(prep(poly_buff))
            anclajes.append((b[2] + 1.0, b[1]))
            anclajes.append((b[0], b[3] + 1.0))
        return fijas_bounds, fijas_preps, anclajes

    def _comprobar_colision_transfer(self, px, py, var, limite_prep, l_bounds, fijas_bounds, fijas_preps):
        b_minx = var["b_minx"]
        b_miny = var["b_miny"]
        b_maxx = var["b_maxx"]
        b_maxy = var["b_maxy"]
        cmx = px + b_minx
        cmy = py + b_miny
        cMx = px + b_maxx
        cMy = py + b_maxy

        if limite_prep is not None:
            l_minx, l_miny, l_maxx, l_maxy = l_bounds
            if cmx < l_minx or cmy < l_miny or cMx > l_maxx or cMy > l_maxy:
                return True
            moved = affinity.translate(var["poly_buff"], px, py)
            if not limite_prep.contains(moved):
                return True

        c_buff_local = None
        for idx, f_b in enumerate(fijas_bounds):
            if not (cMx <= f_b[0] + 0.05 or cmx >= f_b[2] - 0.05 or cMy <= f_b[1] + 0.05 or cmy >= f_b[3] - 0.05):
                if c_buff_local is None:
                    c_buff_local = affinity.translate(var["poly_buff"], px, py)
                if fijas_preps[idx].intersects(c_buff_local):
                    return True
        return False

    def _anclas_transfer_destino(self, limite, obstaculos, margin_mm, kerf_mm, w, h):
        del limite, kerf_mm, w, h  # anclas derivadas de piezas fijas + esquina inferior izquierda
        anclajes = {(margin_mm, margin_mm)}
        for obs in obstaculos:
            try:
                minx, miny, maxx, maxy = obs.bounds
            except Exception:
                continue
            anclajes_pieza = (
                (maxx + 1.0, miny),
                (minx, maxy + 1.0),
                (minx, miny),
                (maxx, miny),
                (minx, maxy),
                (maxx, maxy),
            )
            for ax, ay in anclajes_pieza:
                anclajes.add((ax, ay))
        return sorted(anclajes, key=lambda t: (t[0] * t[0]) + (t[1] * t[1]))

    def _clonar_hoja_para_sim_transfer(self, hoja):
        if not isinstance(hoja, dict):
            return {"piezas": []}
        return {
            "placa_id": hoja.get("placa_id"),
            "placa_w": hoja.get("placa_w"),
            "placa_h": hoja.get("placa_h"),
            "es_retazo": hoja.get("es_retazo", False),
            "poly_borde_retazo": hoja.get("poly_borde_retazo"),
            "kerf_usado": hoja.get("kerf_usado"),
            "margin_usado": hoja.get("margin_usado"),
            "opt_usado": hoja.get("opt_usado"),
            "corner_usado": hoja.get("corner_usado"),
            "piezas": [copy.deepcopy(p) for p in (hoja.get("piezas") or [])],
        }

    def _maximo_lote_incremental(self, hoja_destino, candidatos_raw, hojas_grupo=None, hoja_origen=None):
        """
        Empareja piezas una a una sin re-nestear el destino (rápido y estable).
        Prueba varios órdenes para maximizar cuántas caben.
        """
        if not candidatos_raw:
            return [], None

        excluir_rtz = self._excluir_rtz_para_transfer(
            hoja_origen, hoja_destino, hojas_grupo
        )

        ordenes = [
            lambda x: float(x.get("area", 0.0) or 0.0),
            lambda x: -float(x.get("area", 0.0) or 0.0),
            lambda x: str(x.get("nombre", "") or ""),
        ]
        mejor_lote = []
        mejor_dest = None

        for key_fn in ordenes:
            sim = self._clonar_hoja_para_sim_transfer(hoja_destino)
            lote = []
            nueva_dest = None
            for p in sorted(candidatos_raw, key=key_fn):
                nueva_dest = self._intentar_colocacion_incremental(
                    sim,
                    p,
                    hojas_grupo,
                    excluir_rtz_ids=excluir_rtz,
                )
                if nueva_dest is None:
                    continue
                lote.append(p)
                sim["piezas"] = list(nueva_dest.get("piezas") or [])
            if len(lote) > len(mejor_lote):
                mejor_lote = lote
                mejor_dest = nueva_dest

        return mejor_lote, mejor_dest

    def _pieza_colocada_incremental(self, pieza_orig, var, px, py):
        poly_local = var.get("poly")
        if poly_local is None or poly_local.is_empty:
            return None
        marks_local = var.get("marks")
        poly_placed = affinity.translate(poly_local, px, py)

        marcas = list(pieza_orig.get("marcas") or [])
        if marks_local is not None and not getattr(marks_local, "is_empty", True):
            marks_placed = affinity.translate(marks_local, px, py)
            marcas = _marks_geom_to_lista(marks_placed) or marcas

        return {
            "nombre": str(pieza_orig.get("nombre", "") or ""),
            "poligonos": poligonos_desde_shapely(poly_placed),
            "marcas": marcas,
            "area": float(pieza_orig.get("area", poly_placed.area) or poly_placed.area),
            "calibre": pieza_orig.get("calibre", ""),
            "material": pieza_orig.get("material", ""),
        }

    def _evaluar_posicion_transfer(
        self,
        px,
        py,
        var,
        *,
        margin_mm,
        w,
        h,
        limite_prep,
        l_bounds,
        fijas_bounds,
        fijas_preps,
        zonas_rtz,
        clearance_rtz,
        rechazos=None,
    ):
        """Valida una posición candidata (con slide) para transferencia incremental."""
        if (
            px + var["m_minx"] + 1e-6 < margin_mm
            or py + var["m_miny"] + 1e-6 < margin_mm
            or px + var["m_maxx"] > w - margin_mm + 1e-6
            or py + var["m_maxy"] > h - margin_mm + 1e-6
        ):
            if rechazos is not None:
                rechazos["limite"] += 1
            return None
        if self._comprobar_colision_transfer(
            px, py, var, limite_prep, l_bounds, fijas_bounds, fijas_preps
        ):
            if rechazos is not None:
                rechazos["colision"] += 1
            return None

        hubo_movimiento = True
        while hubo_movimiento:
            hubo_movimiento = False
            test_px = px - SLIDE_STEP_MM
            if test_px + var["m_minx"] >= margin_mm:
                if not self._comprobar_colision_transfer(
                    test_px, py, var, limite_prep, l_bounds, fijas_bounds, fijas_preps
                ):
                    px = test_px
                    hubo_movimiento = True
            test_py = py - SLIDE_STEP_MM
            if test_py + var["m_miny"] >= margin_mm:
                if not self._comprobar_colision_transfer(
                    px, test_py, var, limite_prep, l_bounds, fijas_bounds, fijas_preps
                ):
                    py = test_py
                    hubo_movimiento = True

        poly_test = affinity.translate(var["poly"], px, py)
        if self._poly_invade_zonas_rtz_transfer(poly_test, zonas_rtz, clearance_rtz):
            if rechazos is not None:
                rechazos["rtz"] += 1
            return None
        return (px * px) + (py * py), var, px, py

    def _barrido_grid_colocacion_transfer(
        self,
        variaciones,
        *,
        margin_mm,
        w,
        h,
        limite_prep,
        l_bounds,
        fijas_bounds,
        fijas_preps,
        zonas_rtz,
        clearance_rtz,
        rechazos=None,
    ):
        """
        Fallback cuando las anclas no alcanzan huecos aislados (p. ej. franja libre
        lateral en placas ya anidadas). Devuelve la primera posición válida encontrada.
        """
        step = max(SLIDE_STEP_MM, float(TRANSFER_GRID_STEP_MM))
        max_fija_x = margin_mm
        max_fija_y = margin_mm
        for b in fijas_bounds:
            max_fija_x = max(max_fija_x, float(b[2]))
            max_fija_y = max(max_fija_y, float(b[3]))

        for var in variaciones:
            span_x = var["b_maxx"] - var["b_minx"]
            span_y = var["b_maxy"] - var["b_miny"]
            if span_x <= 0 or span_y <= 0:
                continue

            # Priorizar franja derecha y banda superior (huecos típicos tras nesting).
            ax_min = margin_mm
            ax_max = w - margin_mm - span_x
            ay_min = margin_mm
            ay_max = h - margin_mm - span_y
            x_starts = [ax_min]
            if max_fija_x + step < ax_max:
                x_starts.append(min(max_fija_x, ax_max))
            y_starts = [ay_min]
            if max_fija_y + step < ay_max:
                y_starts.append(min(max_fija_y, ay_max))

            for ax0 in x_starts:
                ax = ax0
                while ax <= ax_max + 0.1:
                    for ay0 in y_starts:
                        ay = ay0
                        while ay <= ay_max + 0.1:
                            candidato = self._evaluar_posicion_transfer(
                                ax - var["b_minx"],
                                ay - var["b_miny"],
                                var,
                                margin_mm=margin_mm,
                                w=w,
                                h=h,
                                limite_prep=limite_prep,
                                l_bounds=l_bounds,
                                fijas_bounds=fijas_bounds,
                                fijas_preps=fijas_preps,
                                zonas_rtz=zonas_rtz,
                                clearance_rtz=clearance_rtz,
                                rechazos=rechazos,
                            )
                            if candidato is not None:
                                return candidato
                            ay += step
                    ax += step
        return None

    def _intentar_colocacion_incremental(
        self, hoja_destino, pieza_mover, hojas_grupo=None, excluir_rtz_ids=None
    ):
        """
        Coloca la pieza nueva sobre el destino sin mover las existentes.
        Usa la misma lógica de anclas + slide del motor C++/Cython.
        """
        pack_piece = self._as_pack_piece_visual(pieza_mover)
        if pack_piece is None:
            _dbg_nesting(
                f"[TRANSFER] geometría inválida en incremental "
                f"{hoja_destino.get('placa_id')} / {pieza_mover.get('nombre')}"
            )
            return None

        params = self._params_hoja(hoja_destino)
        kerf_in = float(params["kerf"] or DEFAULT_KERF_IN)
        margin_in = float(params["margin"] or DEFAULT_MARGIN_IN)
        kerf_radio = (kerf_in * 25.4) / 2.0
        margin_mm = margin_in * 25.4
        w = float(params["w"] or 0)
        h = float(params["h"] or 0)
        if w <= 0 or h <= 0:
            return None

        limite = self._limite_util_destino_transfer(hoja_destino, margin_mm)
        limite_prep = None
        l_bounds = (margin_mm, margin_mm, w - margin_mm, h - margin_mm)
        if limite is not None:
            try:
                limite_eval = limite.buffer(0.1)
                limite_prep = prep(limite_eval)
                l_bounds = limite_eval.bounds
            except Exception:
                limite_prep = prep(limite)
                l_bounds = limite.bounds

        fijas_bounds, fijas_preps, anclajes_fijas = self._fijas_y_anclas_destino_transfer(
            hoja_destino, kerf_radio
        )
        obstaculos = self._obstaculos_transfer_destino(hoja_destino, kerf_in * 25.4)
        anclajes = self._anclas_transfer_destino(
            limite, obstaculos, margin_mm, kerf_in * 25.4, w, h
        )
        anclajes_set = set(anclajes)
        anclajes_set.update(anclajes_fijas)
        anclajes = sorted(anclajes_set, key=lambda t: (t[0] * t[0]) + (t[1] * t[1]))

        variaciones = self._build_variaciones_transfer(
            pack_piece["poly"],
            pack_piece.get("marks"),
            w,
            h,
            margin_mm,
            kerf_radio,
        )
        if not variaciones:
            _dbg_nesting(
                f"[TRANSFER] pieza demasiado grande para destino "
                f"{hoja_destino.get('placa_id')} / {pieza_mover.get('nombre')}"
            )
            return None

        zonas_rtz = (
            []
            if hoja_destino.get("es_retazo")
            else self._zonas_rtz_reservadas_madre(
                hoja_destino, hojas_grupo, excluir_rtz_ids=excluir_rtz_ids
            )
        )
        clearance_rtz = kerf_in * 25.4

        mejor = None
        rechazos = {"colision": 0, "rtz": 0, "limite": 0}
        ctx_eval = {
            "margin_mm": margin_mm,
            "w": w,
            "h": h,
            "limite_prep": limite_prep,
            "l_bounds": l_bounds,
            "fijas_bounds": fijas_bounds,
            "fijas_preps": fijas_preps,
            "zonas_rtz": zonas_rtz,
            "clearance_rtz": clearance_rtz,
            "rechazos": rechazos,
        }
        for var in variaciones:
            for anchor_x, anchor_y in anclajes:
                candidato = self._evaluar_posicion_transfer(
                    anchor_x - var["b_minx"],
                    anchor_y - var["b_miny"],
                    var,
                    **ctx_eval,
                )
                if candidato is not None and (
                    mejor is None or candidato[0] < mejor[0]
                ):
                    mejor = candidato

        if mejor is None:
            mejor = self._barrido_grid_colocacion_transfer(
                variaciones,
                **ctx_eval,
            )
            if mejor is not None:
                _dbg_nesting(
                    f"[TRANSFER] colocación por grilla en "
                    f"{hoja_destino.get('placa_id')} para {pieza_mover.get('nombre')}"
                )

        if mejor is None:
            libera = (
                f" libera_rtz={','.join(excluir_rtz_ids)}"
                if excluir_rtz_ids
                else ""
            )
            _dbg_nesting(
                f"[TRANSFER] incremental sin hueco en "
                f"{hoja_destino.get('placa_id')} para {pieza_mover.get('nombre')}"
                f" (col={rechazos['colision']} rtz={rechazos['rtz']} "
                f"lim={rechazos['limite']}{libera})"
            )
            return None

        _, var, px, py = mejor
        pieza_colocada = self._pieza_colocada_incremental(pieza_mover, var, px, py)
        if pieza_colocada is None:
            return None

        piezas_out = list(hoja_destino.get("piezas") or [])
        piezas_out.append(pieza_colocada)
        area_usada = 0.0
        for p in piezas_out:
            if _is_virtual_piece(str(p.get("nombre", ""))):
                continue
            try:
                area_usada += float(p.get("area", 0.0) or 0.0)
            except Exception:
                pass
        denom = w * h
        return {
            "piezas": piezas_out,
            "area_usada": area_usada,
            "eficiencia": (area_usada / denom) * 100.0 if denom > 0 else 0.0,
        }

    def _empaquetar_respetando_rtz_madre(
        self,
        piezas_pack,
        hoja,
        hojas_grupo=None,
        *,
        debug_tag="transfer",
        intentos=24,
        excluir_rtz_ids=None,
    ):
        """Empaqueta en placa madre evitando solapar zonas RTZ asociadas."""
        params = self._params_hoja(hoja)
        w = float(params["w"] or 0)
        h = float(params["h"] or 0)
        if w <= 0 or h <= 0 or not piezas_pack:
            return None, list(piezas_pack or [])

        limite_poly = self._limite_poly_desde_hoja(hoja)
        zonas = self._zonas_rtz_reservadas_madre(
            hoja, hojas_grupo, excluir_rtz_ids=excluir_rtz_ids
        )
        clearance = float(params["kerf"] or DEFAULT_KERF_IN) * 25.4

        base = sorted(
            [copy.deepcopy(p) for p in piezas_pack],
            key=lambda x: float(x.get("area", 0) or 0),
            reverse=True,
        )
        n = max(1, int(intentos or 1))
        try:
            from .giga_cal11_galv import clave_desde_debug_tag, should_force_giga_engine

            if should_force_giga_engine(clave_desde_debug_tag(debug_tag)):
                n = 1
        except Exception:
            pass

        for intento in range(n):
            if intento == 0:
                batch = base
            else:
                batch = base.copy()
                random.shuffle(batch)
                batch.sort(key=lambda x: float(x.get("area", 0) or 0), reverse=True)

            nh, sobras = _safe_empaquetar_una_hoja_mc(
                batch,
                w,
                h,
                params["kerf"],
                params["margin"],
                params["opt"],
                params["corner"],
                limite_poly=limite_poly,
                debug_tag=f"{debug_tag}|try={intento + 1}",
            )
            if not nh or sobras:
                continue
            if zonas and self._piezas_invaden_zonas_rtz(nh, zonas, clearance):
                continue
            return nh, []

        return None, list(piezas_pack)

    def _simular_renest_en_destino(
        self,
        hoja_destino,
        piezas_dest_base,
        piezas_mover_raw,
        hojas_grupo=None,
        intentos_mc=48,
        hoja_origen=None,
    ):
        excluir_rtz = self._excluir_rtz_para_transfer(
            hoja_origen, hoja_destino, hojas_grupo
        )
        piezas_pack = list(piezas_dest_base)
        for p in piezas_mover_raw:
            pp = self._as_pack_piece_visual(p)
            if pp is None:
                _dbg_nesting(
                    f"[TRANSFER] geometría inválida al simular destino: {p.get('nombre')}"
                )
                return False, None
            piezas_pack.append(pp)
        if not piezas_pack:
            return False, None

        params = self._params_hoja(hoja_destino)
        if params["w"] <= 0 or params["h"] <= 0:
            return False, None

        nueva_dest, sobras = self._empaquetar_respetando_rtz_madre(
            piezas_pack,
            hoja_destino,
            hojas_grupo,
            debug_tag="transfer_dest_batch",
            intentos=int(intentos_mc or 48),
            excluir_rtz_ids=excluir_rtz,
        )
        if nueva_dest is None or sobras:
            return False, None
        colocadas = [
            p
            for p in (nueva_dest.get("piezas") or [])
            if not _is_virtual_piece(str(p.get("nombre", "")))
        ]
        esperadas = Counter(str(p.get("nombre", "") or "") for p in piezas_pack)
        ubicadas = Counter(str(p.get("nombre", "") or "") for p in colocadas)
        if ubicadas != esperadas:
            _dbg_nesting(
                f"[TRANSFER] conteo destino inválido: esperadas={dict(esperadas)} "
                f"colocadas={dict(ubicadas)}"
            )
            return False, None
        return True, nueva_dest

    def _maximo_lote_transferible(
        self,
        hoja_destino,
        piezas_dest_base,
        candidatos_raw,
        hojas_grupo=None,
        hoja_origen=None,
        intentos_mc=12,
    ):
        """Respaldo lento (MC). Preferir _maximo_lote_incremental."""
        if not candidatos_raw:
            return []
        intentos_todas = max(12, int(intentos_mc or 12))
        intentos_parcial = max(8, intentos_todas // 2)
        ok_todas, _ = self._simular_renest_en_destino(
            hoja_destino,
            piezas_dest_base,
            candidatos_raw,
            hojas_grupo,
            intentos_mc=intentos_todas,
            hoja_origen=hoja_origen,
        )
        if ok_todas:
            return list(candidatos_raw)

        mejor = []
        for p in sorted(
            candidatos_raw,
            key=lambda x: float(x.get("area", 0.0) or 0.0),
        ):
            trial = mejor + [p]
            ok, _ = self._simular_renest_en_destino(
                hoja_destino,
                piezas_dest_base,
                trial,
                hojas_grupo,
                intentos_mc=intentos_parcial,
                hoja_origen=hoja_origen,
            )
            if ok:
                mejor = trial
        return mejor

    def _renest_origen_tras_transferencia(self, origen_hoja, mover_ids, hojas_grupo=None):
        """Quita las piezas movidas del origen sin re-nestear las que quedan."""
        del hojas_grupo
        piezas_res = []
        area_usada = 0.0
        for p in (origen_hoja.get("piezas") or []):
            if id(p) in mover_ids:
                continue
            piezas_res.append(p)
            if _is_virtual_piece(str(p.get("nombre", ""))):
                continue
            try:
                area_usada += float(p.get("area", 0.0) or 0.0)
            except Exception:
                pass

        params_o = self._params_hoja(origen_hoja)
        w = float(params_o.get("w") or 0)
        h = float(params_o.get("h") or 0)
        denom = w * h
        nueva_orig = {
            "piezas": piezas_res,
            "area_usada": area_usada,
            "eficiencia": (area_usada / denom) * 100.0 if denom > 0 else 0.0,
        }
        return nueva_orig, params_o

    def _eliminar_hoja_origen_si_vacia(self, origen_grupo, origen_hoja):
        if not isinstance(origen_grupo, dict) or not isinstance(origen_grupo.get("hojas"), list):
            return
        if len(self._piezas_reales_en_hoja(origen_hoja)) > 0:
            return
        hojas = origen_grupo["hojas"]
        try:
            idx = hojas.index(origen_hoja)
        except ValueError:
            return
        tiene_retazos = (
            idx + 1 < len(hojas) and bool(hojas[idx + 1].get("es_retazo", False))
        )
        if tiene_retazos:
            return
        try:
            hojas.remove(origen_hoja)
        except ValueError:
            pass

    def _conteo_piezas_en_grupos(self, *grupos):
        conteo = {}
        vistos = set()
        for grupo in grupos:
            if not isinstance(grupo, dict):
                continue
            gid = id(grupo)
            if gid in vistos:
                continue
            vistos.add(gid)
            for nom, cnt in self._conteo_piezas_reales_en_hojas(grupo.get("hojas") or []).items():
                conteo[nom] = conteo.get(nom, 0) + int(cnt)
        return conteo

    def _aplicar_transferencia_lote(
        self,
        origen_grupo,
        origen_hoja,
        hoja_destino,
        piezas_mover,
        nueva_dest,
        dest_grupo=None,
    ):
        dest_grupo = dest_grupo or origen_grupo
        overlays_dest = self._extraer_overlays_hoja(hoja_destino)
        overlays_orig = self._extraer_overlays_hoja(origen_hoja)
        mover_ids = {id(p) for p in piezas_mover}

        hojas_orig = origen_grupo.get("hojas") if isinstance(origen_grupo, dict) else None
        nueva_orig, params_o = self._renest_origen_tras_transferencia(
            origen_hoja, mover_ids, hojas_orig
        )

        params_d = self._params_hoja(hoja_destino)
        self._copiar_meta_placa(nueva_dest, hoja_destino, params_d)
        self._copiar_meta_placa(nueva_orig, origen_hoja, params_o)
        nueva_dest["piezas"] = self._fusionar_piezas_con_overlays(
            nueva_dest.get("piezas") or [],
            overlays_dest,
        )
        nueva_orig["piezas"] = self._fusionar_piezas_con_overlays(
            nueva_orig.get("piezas") or [],
            overlays_orig,
        )
        hoja_destino.update(nueva_dest)
        misma_hoja = origen_hoja is hoja_destino
        if not misma_hoja:
            origen_hoja.update(nueva_orig)
        if isinstance(origen_grupo, dict) and not misma_hoja:
            hojas = origen_grupo.get("hojas") or []
            sincronizar_overlays_grupo(hojas)
            for h in hojas:
                actualizar_eficiencias_hoja(h, hojas_grupo=hojas)
            calcular_eficiencias_grupo(hojas)
        elif misma_hoja:
            actualizar_eficiencias_hoja(hoja_destino)
        else:
            actualizar_eficiencias_hoja(hoja_destino)
            if not misma_hoja:
                actualizar_eficiencias_hoja(origen_hoja)

        if dest_grupo is not origen_grupo and isinstance(dest_grupo, dict):
            hojas_dest = dest_grupo.get("hojas") or []
            sincronizar_overlays_grupo(hojas_dest)
            for h in hojas_dest:
                actualizar_eficiencias_hoja(h, hojas_grupo=hojas_dest)
            calcular_eficiencias_grupo(hojas_dest)

        if not misma_hoja:
            self._eliminar_hoja_origen_si_vacia(origen_grupo, origen_hoja)

        if dest_grupo is not origen_grupo:
            self._ajustar_piezas_pool_cross_wo(
                origen_grupo, dest_grupo, piezas_mover
            )

    def _quitar_piezas_de_pool(self, grupo, piezas_mover):
        if not isinstance(grupo, dict) or not grupo.get("piezas_pool_engine"):
            return
        pool = list(grupo.get("piezas_pool") or [])
        if not pool or not piezas_mover:
            return
        # Una entrada de piezas_mover = una sola baja en el pool.
        # Antes se descontaba por nombre exacto Y además por base → 2× por pieza
        # (Top_Cover_1 mudada 1 vez quitaba 2 del pool → reconciliar borraba una placa).
        pendientes = Counter(
            str(p.get("nombre") or "").strip()
            for p in piezas_mover
            if str(p.get("nombre") or "").strip()
        )
        nuevo = []
        for entry in pool:
            nom = str(entry.get("nombre") or "").strip()
            if not nom:
                nuevo.append(entry)
                continue
            if pendientes.get(nom, 0) > 0:
                pendientes[nom] -= 1
                continue
            base = _piece_name_base(nom)
            hit = None
            for key, cnt in pendientes.items():
                if cnt > 0 and _piece_name_base(key) == base:
                    hit = key
                    break
            if hit is not None:
                pendientes[hit] -= 1
                continue
            nuevo.append(entry)
        grupo["piezas_pool"] = nuevo

    def _agregar_piezas_a_pool(self, grupo, piezas_mover):
        if not isinstance(grupo, dict) or not grupo.get("piezas_pool_engine"):
            return
        pool = list(grupo.get("piezas_pool") or [])
        for p in piezas_mover:
            nom = str(p.get("nombre") or "").strip()
            if nom:
                pool.append({"nombre": nom})
        grupo["piezas_pool"] = pool

    def _ajustar_piezas_pool_cross_wo(self, origen_grupo, dest_grupo, piezas_mover):
        """Evita que reconciliar elimine la placa destino al recibir piezas de otra WO."""
        if not piezas_mover:
            return
        if isinstance(dest_grupo, dict):
            self._agregar_piezas_a_pool(dest_grupo, piezas_mover)
        if isinstance(origen_grupo, dict) and origen_grupo is not dest_grupo:
            self._quitar_piezas_de_pool(origen_grupo, piezas_mover)

    def transferir_piezas_a_placa(
        self,
        resultados_nesting,
        hoja_origen,
        hoja_destino,
        piezas_especificas=None,
        piezas_indices=None,
        resultados_destino=None,
    ):
        """
        Renestea destino con todas las piezas candidatas juntas y mueve el máximo lote posible.
        """
        resultado = {
            "ok": False,
            "movidas": 0,
            "restantes": 0,
            "solicitadas": 0,
            "motivo": "",
        }
        try:
            if (
                not isinstance(resultados_nesting, dict)
                or not isinstance(hoja_origen, dict)
                or not isinstance(hoja_destino, dict)
            ):
                resultado["motivo"] = "datos_invalidos"
                return resultado
            if hoja_origen is hoja_destino:
                resultado["motivo"] = "misma_placa"
                return resultado

            origen_grupo = self._grupo_de_hoja(resultados_nesting, hoja_origen)
            if origen_grupo is None:
                resultado["motivo"] = "origen_no_encontrado"
                return resultado

            resultados_dest = (
                resultados_destino
                if resultados_destino is not None
                else resultados_nesting
            )
            dest_grupo = self._grupo_de_hoja(resultados_dest, hoja_destino)
            if dest_grupo is None:
                resultado["motivo"] = "destino_no_encontrado"
                return resultado

            hoja_origen = self._resolver_hoja_viva(resultados_nesting, hoja_origen)
            hoja_destino = self._resolver_hoja_viva(resultados_dest, hoja_destino)
            if hoja_origen is hoja_destino:
                resultado["motivo"] = "misma_placa"
                return resultado

            hojas_grupo_orig = origen_grupo.get("hojas") or []
            hojas_grupo_dest = dest_grupo.get("hojas") or []
            cross_wo = dest_grupo is not origen_grupo
            intentos_renest = 24 if cross_wo else 12
            conteo_antes = self._conteo_piezas_en_grupos(origen_grupo, dest_grupo)
            candidatos = self._resolver_candidatos_transferencia(
                hoja_origen,
                piezas_especificas,
                indices=piezas_indices,
            )

            resultado["solicitadas"] = len(piezas_especificas or candidatos)
            if piezas_especificas and not candidatos:
                resultado["motivo"] = "pieza_no_encontrada"
                _dbg_nesting(
                    "[TRANSFER] candidatos vacíos tras selección "
                    f"(indices={piezas_indices})"
                )
                return resultado
            if not candidatos:
                resultado["motivo"] = "sin_piezas"
                return resultado

            # Una sola pieza: intento directo (más fiable en ida/vuelta y con RTZ).
            if len(candidatos) == 1:
                idx_hint = (
                    piezas_indices[0]
                    if piezas_indices and len(piezas_indices) > 0
                    else None
                )
                if self.transferir_y_reoptimizar(
                    resultados_nesting,
                    candidatos[0],
                    hoja_destino,
                    hoja_origen=hoja_origen,
                    idx_hint=idx_hint,
                    resultados_destino=resultados_dest,
                    dest_grupo=dest_grupo,
                ):
                    resultado["movidas"] = 1
                    resultado["restantes"] = len(self._piezas_reales_en_hoja(hoja_origen))
                    resultado["ok"] = True
                    return resultado

            es_masiva = piezas_especificas is None
            movidas_total = 0
            pendientes_inicial = len(candidatos)

            if es_masiva and cross_wo and len(candidatos) > 1:
                piezas_dest_base = self._pack_piezas_destino(hoja_destino)
                ok_all, nueva_dest = self._simular_renest_en_destino(
                    hoja_destino,
                    piezas_dest_base,
                    candidatos,
                    hojas_grupo_dest,
                    intentos_mc=max(48, intentos_renest * 2),
                    hoja_origen=hoja_origen,
                )
                if ok_all and nueva_dest:
                    self._aplicar_transferencia_lote(
                        origen_grupo,
                        hoja_origen,
                        hoja_destino,
                        candidatos,
                        nueva_dest,
                        dest_grupo=dest_grupo,
                    )
                    resultado["movidas"] = len(candidatos)
                    resultado["restantes"] = len(
                        self._piezas_reales_en_hoja(hoja_origen)
                    )
                    resultado["solicitadas"] = pendientes_inicial
                    resultado["ok"] = True
                    return resultado

            while True:
                if es_masiva:
                    pendientes = self._piezas_reales_en_hoja(hoja_origen)
                else:
                    pendientes = self._resolver_candidatos_transferencia(
                        hoja_origen,
                        piezas_especificas,
                        indices=piezas_indices,
                    )
                if not pendientes:
                    break

                lote, nueva_dest = self._maximo_lote_incremental(
                    hoja_destino, pendientes, hojas_grupo_dest, hoja_origen=hoja_origen
                )

                if not lote:
                    piezas_dest_base = self._pack_piezas_destino(hoja_destino)
                    lote = self._maximo_lote_transferible(
                        hoja_destino,
                        piezas_dest_base,
                        pendientes,
                        hojas_grupo_dest,
                        hoja_origen=hoja_origen,
                        intentos_mc=intentos_renest,
                    )
                    if lote:
                        ok, nueva_dest = self._simular_renest_en_destino(
                            hoja_destino,
                            piezas_dest_base,
                            lote,
                            hojas_grupo_dest,
                            intentos_mc=intentos_renest,
                            hoja_origen=hoja_origen,
                        )
                        if not ok or nueva_dest is None:
                            lote = []

                if not lote or nueva_dest is None:
                    resto_fb = (
                        self._piezas_reales_en_hoja(hoja_origen)
                        if es_masiva
                        else self._resolver_candidatos_transferencia(
                            hoja_origen,
                            piezas_especificas,
                            indices=piezas_indices,
                        )
                    )
                    for p in sorted(
                        resto_fb,
                        key=lambda x: float(x.get("area", 0.0) or 0.0),
                    ):
                        if self.transferir_y_reoptimizar(
                            resultados_nesting,
                            p,
                            hoja_destino,
                            hoja_origen=hoja_origen,
                            resultados_destino=resultados_dest,
                            dest_grupo=dest_grupo,
                        ):
                            movidas_total += 1
                    if movidas_total > 0:
                        break
                    resultado["movidas"] = 0
                    resultado["restantes"] = len(
                        self._piezas_reales_en_hoja(hoja_origen)
                    )
                    resultado["ok"] = False
                    resultado["motivo"] = "sin_espacio"
                    return resultado

                self._aplicar_transferencia_lote(
                    origen_grupo,
                    hoja_origen,
                    hoja_destino,
                    lote,
                    nueva_dest,
                    dest_grupo=dest_grupo,
                )
                movidas_total += len(lote)

                if not es_masiva:
                    break
                if len(lote) >= len(pendientes):
                    break

            conteo_despues = self._conteo_piezas_en_grupos(origen_grupo, dest_grupo)
            if conteo_despues != conteo_antes:
                _dbg_nesting(
                    f"[TRANSFER] conteo multigrupo {conteo_antes} -> {conteo_despues}"
                )
            resultado["movidas"] = movidas_total
            resultado["restantes"] = len(self._piezas_reales_en_hoja(hoja_origen))
            resultado["solicitadas"] = pendientes_inicial
            resultado["ok"] = movidas_total > 0
            if not resultado["ok"]:
                resultado["motivo"] = "sin_espacio"
            return resultado
        except Exception as e:
            _dbg_nesting(f"[TRANSFER-BATCH-ERROR] {e}")
            resultado["motivo"] = "error"
            return resultado

    def transferir_y_reoptimizar(
        self,
        resultados_nesting,
        pieza_info,
        hoja_destino,
        hoja_origen=None,
        idx_hint=None,
        resultados_destino=None,
        dest_grupo=None,
    ):
        """
        Mueve una pieza de su hoja origen a una hoja destino y reoptimiza ambas.
        Devuelve True si la transferencia fue posible.
        """
        try:
            if (
                not isinstance(resultados_nesting, dict)
                or not isinstance(pieza_info, dict)
                or not isinstance(hoja_destino, dict)
            ):
                return False

            nombre_pieza = str(pieza_info.get("nombre", "") or "")
            if not nombre_pieza or _is_virtual_piece(nombre_pieza):
                return False

            origen_grupo, origen_hoja, idx_origen = self._localizar_hoja_origen(
                resultados_nesting,
                pieza_info,
                hoja_origen=hoja_origen,
                idx_hint=idx_hint,
            )
            if origen_hoja is None or idx_origen < 0:
                return False
            if origen_hoja is hoja_destino:
                return False

            resultados_dest = (
                resultados_destino
                if resultados_destino is not None
                else resultados_nesting
            )
            if dest_grupo is None:
                dest_grupo = self._grupo_de_hoja(resultados_dest, hoja_destino)
            if dest_grupo is None:
                return False
            hoja_destino = self._resolver_hoja_viva(resultados_dest, hoja_destino)

            pieza_mover = origen_hoja["piezas"][idx_origen]
            if self._as_pack_piece_visual(pieza_mover) is None:
                return False

            params_d = self._params_hoja(hoja_destino)
            if params_d["w"] <= 0 or params_d["h"] <= 0:
                return False

            hojas_grupo_orig = (
                (origen_grupo or {}).get("hojas")
                if isinstance(origen_grupo, dict)
                else None
            )
            hojas_grupo_dest = (
                (dest_grupo or origen_grupo or {}).get("hojas")
                if isinstance(dest_grupo or origen_grupo, dict)
                else hojas_grupo_orig
            )
            excluir_rtz = self._excluir_rtz_para_transfer(
                origen_hoja, hoja_destino, hojas_grupo_dest
            )
            nueva_dest = self._intentar_colocacion_incremental(
                hoja_destino,
                pieza_mover,
                hojas_grupo_dest,
                excluir_rtz_ids=excluir_rtz,
            )
            if nueva_dest is None:
                piezas_dest = self._pack_piezas_destino(hoja_destino)
                piezas_dest.append(self._as_pack_piece_visual(pieza_mover))
                nueva_dest, sobras_dest = self._empaquetar_respetando_rtz_madre(
                    piezas_dest,
                    hoja_destino,
                    hojas_grupo_dest,
                    debug_tag="transfer_dest",
                    intentos=24 if dest_grupo is not origen_grupo else 12,
                    excluir_rtz_ids=excluir_rtz,
                )
                if nueva_dest is None or sobras_dest:
                    _dbg_nesting(
                        f"[TRANSFER] falló destino {hoja_destino.get('placa_id')} "
                        f"pieza={nombre_pieza} incremental+renest"
                    )
                    return False

            self._aplicar_transferencia_lote(
                origen_grupo,
                origen_hoja,
                hoja_destino,
                [pieza_mover],
                nueva_dest,
                dest_grupo=dest_grupo,
            )
            return True
        except Exception as e:
            _dbg_nesting(f"[TRANSFER-ERROR] {e}")
            return False

    # =========================================================================
    # 🚀 NUEVO: PUENTE DE EXPORTACIÓN (Soluciona el AttributeError en main.py)
    # =========================================================================
    def exportar_resultados_a_dxf(
        self,
        resultados,
        out_dir,
        base_name="NEST",
        generar_step=False,
        wo_label=None,
        es_swo=False,
        swo_id=None,
        datos_partes=None,
        motor_3d="freecad",
        progress_cb=None,
    ):
        """Redirige la orden de la interfaz gráfica hacia el archivo exporter.py"""
        return exportar_resultados_a_dxf(
            resultados,
            out_dir,
            base_name,
            generar_step,
            wo_label=wo_label,
            es_swo=es_swo,
            swo_id=swo_id,
            datos_partes=datos_partes,
            motor_3d=motor_3d,
            progress_cb=progress_cb,
        )


def _nesting_worker_bootstrap():
    try:
        from modules.win_dll_bootstrap import bootstrap_proceso_nesting

        bootstrap_proceso_nesting()
    except Exception:
        pass


def _procesar_grupo_parallel_worker(job):
    """Worker de proceso: instancia limpia sin referencias a la UI Qt."""
    cancel_event = None
    plate_allowed = None
    plate_limits = None
    if len(job) >= 12:
        (
            clave,
            piezas,
            datos_placas,
            config_kerf,
            config_margin,
            config_opt,
            config_corner,
            wo_name,
            engine_id,
            cancel_event,
            plate_allowed,
            plate_limits,
        ) = job[:12]
    elif len(job) >= 10:
        (
            clave,
            piezas,
            datos_placas,
            config_kerf,
            config_margin,
            config_opt,
            config_corner,
            wo_name,
            engine_id,
            cancel_event,
        ) = job[:10]
    else:
        (
            clave,
            piezas,
            datos_placas,
            config_kerf,
            config_margin,
            config_opt,
            config_corner,
            wo_name,
            engine_id,
        ) = job
    set_active_engine_id(engine_id)
    motor = MotorNesting()
    motor._plate_formats_allowed = plate_allowed
    motor._plate_format_limits = plate_limits
    motor._plate_format_used = {}
    if cancel_event is not None:
        motor.set_cancel_checker(lambda: bool(cancel_event.is_set()))
    prev = _bind_pack_cancel_checker(motor._cancelado)
    try:
        return motor._procesar_grupo_parallel(
            clave,
            piezas,
            datos_placas,
            config_kerf,
            config_margin,
            config_opt,
            config_corner,
            wo_name,
        )
    finally:
        _unbind_pack_cancel_checker(prev)