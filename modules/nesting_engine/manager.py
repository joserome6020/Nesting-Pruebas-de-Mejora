import concurrent.futures
import multiprocessing
import random
import threading
import copy
import re
import time
import os
from collections import Counter
from datetime import datetime
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
    reconstruir_poly_seguro,
    reconstruir_marks,
    generar_texto_vectorial,
    poligonos_desde_shapely,
    interiores_poly,
)
from .algorithm_bridge import empaquetar_una_hoja_mc, engine_name as nesting_engine_name
from .efficiency_metrics import (
    actualizar_eficiencias_hoja,
    calcular_eficiencias_grupo,
    nombre_rtz_para_placa,
)
from .nest_optimization import get_nest_profile, score_placa_simulacion
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
DEFAULT_KERF_IN = 0.3
DEFAULT_MARGIN_IN = 0.15
THICKNESS_TOLERANCE_PCT = 0.15
SLIDE_STEP_MM = 4.0
TRANSFER_ROTATIONS = (0, 90, 180, 270)


def _dbg_nesting(msg: str):
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        linea = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(linea)
        with open(DEBUG_LOG_NESTING, "a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception:
        pass


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
RTZ_MINI_NEST_AREA_MIN_MM2 = RTZ_TAMANO_MIN_MM * RTZ_TAMANO_MIN_MM


def _retazo_cumple_tamano_minimo(w_mm, h_mm):
    w_mm = float(w_mm or 0.0)
    h_mm = float(h_mm or 0.0)
    if w_mm <= 0.0 or h_mm <= 0.0:
        return False
    w_in = w_mm / 25.4
    h_in = h_mm / 25.4
    return min(w_in, h_in) >= RTZ_TAMANO_MIN_IN


def _filtrar_retazo_por_tamano_minimo(retazo):
    if not isinstance(retazo, dict):
        return None
    w = float(retazo.get("w") or 0.0)
    h = float(retazo.get("h") or 0.0)
    if not _retazo_cumple_tamano_minimo(w, h):
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
    if not _retazo_cumple_tamano_minimo(w0, h0):
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
    if w1 < 1.0 or h1 < 1.0 or not _retazo_cumple_tamano_minimo(w1, h1):
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

        for ang in (0, 90, 180, 270):
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
                }

                if poly_iou >= 0.999 and marks_score >= 0.999:
                    break

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
    mejor = hoja
    mejor_area = float(hoja.get("area_usada", 0) or 0)
    mejor_n = len(hoja.get("piezas") or [])

    for intento in range(max(1, int(intentos))):
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
):
    hoja_vacia = {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0}
    restos_default = list(piezas or [])

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
        )

        if result is None:
            _dbg_nesting(f"[SAFE-EMPAQUE-NONE] {debug_tag} | resultado=None")
            return hoja_vacia, restos_default

        if not isinstance(result, (tuple, list)) or len(result) != 2:
            _dbg_nesting(f"[SAFE-EMPAQUE-FORMATO-INVALIDO] {debug_tag} | tipo={type(result).__name__}")
            return hoja_vacia, restos_default

        hoja, restos = result

        if hoja is None:
            _dbg_nesting(f"[SAFE-EMPAQUE-HOJA-NONE] {debug_tag}")
            hoja = hoja_vacia

        if restos is None:
            _dbg_nesting(f"[SAFE-EMPAQUE-RESTOS-NONE] {debug_tag}")
            restos = restos_default

        return hoja, restos

    except Exception as e:
        _dbg_nesting(f"[SAFE-EMPAQUE-EXCEPTION] {debug_tag} | {e}")
        return hoja_vacia, restos_default

class MotorNesting:
    def __init__(self):
        self.margen_corte = 0.2 * 25.4
        self.escala_dxf = 25.4
        self._cancel_checker = None
        self.orientacion_cobre_por_ruta = {}
        try:
            profile = get_nest_profile()
            mode = str(os.environ.get("ARGA_NEST_MODE", "first")).strip().lower()
            print(
                f"[NESTING ENGINE] backend={nesting_engine_name()} | "
                f"mode={mode} mc={profile.get('mc_iterations')} "
                f"lookahead={profile.get('lookahead')} refine={profile.get('refine_hoja')}"
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
        mejor_parcial = None
        mejor_area = -1.0
        mejor_resto_n = len(base) + 1

        for intento in range(n):
            if self._cancelado():
                break

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
                kerf_override,
                margin_override,
                opt_override,
                corner_override,
                limite_poly=limite_poly,
                debug_tag=f"{debug_tag}|try={intento + 1}",
                mc_iterations=mc_iters,
            )
            if not nh:
                continue

            n_sob = len(sobras or [])
            area = float(nh.get("area_usada", 0) or 0)
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
    def _coinciden(val1, val2):
        v1 = str(val1).strip().upper()
        v2 = str(val2).strip().upper()
        if v1 == v2:
            return True

        n1 = MotorNesting._parse_thickness_value(v1)
        n2 = MotorNesting._parse_thickness_value(v2)
        if n1 is not None and n2 is not None:
            mayor = max(abs(n1), abs(n2))
            if mayor <= 1e-9:
                return True
            return abs(n1 - n2) <= (mayor * THICKNESS_TOLERANCE_PCT)

        tiene_numeros = any(char.isdigit() for char in v1) or any(char.isdigit() for char in v2)
        if not tiene_numeros:
            if v1 in v2 or v2 in v1:
                return True
        return False

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

    def ejecutar_nesting_visual(self, lista_partes, datos_placas, progress_callback=None, config_kerf=DEFAULT_KERF_IN, config_margin=DEFAULT_MARGIN_IN, config_corner="INFERIOR IZQUIERDA", config_opt="OPTIMIZAR LARGO Y ANCHO", wo_name="PENDIENTE"):
        def notificar(msg, porcentaje):
            if progress_callback: progress_callback(msg, porcentaje)

        if not lista_partes: return {"error": "Lista vacía."}
        try:
            if os.path.exists(DEBUG_LOG_NESTING):
                os.remove(DEBUG_LOG_NESTING)
        except Exception:
            pass

        _dbg_nesting("============================================================")
        _dbg_nesting("[NUEVA-EJECUCION] Inicio de corrida de nesting")
        _dbg_nesting(f"[PARAMS] kerf={config_kerf} | margin={config_margin} | corner={config_corner} | opt={config_opt} | wo={wo_name}")
        _dbg_nesting("============================================================")
        grupos = {}
        total_dxf = len(lista_partes)
        piezas_parser_fallidas = []
        
        for i, (pieza, mat, qty, cal, st, ruta) in enumerate(lista_partes):
            notificar(f"Analizando geometría: {pieza}...", (i / total_dxf) * 0.15)
            clave = f"{str(cal).strip().upper()}_{str(mat).strip().upper()}"
            if clave not in grupos:
                grupos[clave] = []

            _dbg_nesting(
                f"[PRE-PARSER] clave={clave} | pieza={pieza} | qty={qty} | status={st} | ruta={ruta}"
            )

            poly, marks = recuperar_geometria_robusta(ruta)

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

            if poly is None:
                _dbg_nesting(
                    f"[PARSER-FAIL] clave={clave} | pieza={pieza} | ruta={ruta} | "
                    f"motivo=recuperar_geometria_robusta devolvió None"
                )
                piezas_parser_fallidas.append(f"{pieza} ({ruta})")
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
                piezas_parser_fallidas.append(f"{pieza} ({ruta})")
                continue

            if poly_nesting is None or poly_nesting.is_empty:
                poly_nesting = poly_exact

            for idx_qty in range(int(qty)):
                grupos[clave].append({
                    "nombre": pieza,
                    # Geometría exacta al motor: misma malla que exporta/visibiliza (con barrenos).
                    "poly": poly_exact,
                    "marks": marks_exact,
                    "area": poly_exact.area,
                    "calibre": str(cal).strip(),
                    "material": str(mat).strip(),
                    "ruta": ruta,
                    "orig_minx": minx,
                    "orig_miny": miny,
                    # NUEVO: respaldo exacto para exportación/reconstrucción
                    "poly_exact": poly_exact,
                    "marks_exact": marks_exact,
                    "debug_id": f"{clave}::{pieza}::rep{idx_qty + 1}"
                })

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

        if piezas_parser_fallidas:
            det = ", ".join(piezas_parser_fallidas[:8])
            if len(piezas_parser_fallidas) > 8:
                det += f" (+{len(piezas_parser_fallidas) - 8} más)"
            _dbg_nesting(f"[ABORT] Piezas sin geometría válida: {det}")
            return {
                "error": (
                    f"No se pudo leer la geometría de {len(piezas_parser_fallidas)} pieza(s). "
                    f"Revise los DXF antes de nestear: {det}"
                )
            }
        
        resultados = {}
        notificar("Iniciando Multiprocesamiento...", 0.16)

        grupos_con_piezas = {
            clave: piezas
            for clave, piezas in grupos.items()
            if piezas
        }
        grupos_ordenados = sorted(
            grupos_con_piezas.items(),
            key=lambda kv: _orden_clave_nesting(kv[0]),
        )

        total_lotes_reales = len(grupos_con_piezas)

        if total_lotes_reales == 0:
            _dbg_nesting("[ABORT] No hay grupos válidos para enviar a multiproceso")
            return {"error": "No hay grupos válidos para procesar."}

        nucleos_totales = multiprocessing.cpu_count()
        nucleos_a_usar = max(1, min(nucleos_totales - 2, total_lotes_reales))

        with concurrent.futures.ProcessPoolExecutor(max_workers=nucleos_a_usar) as executor:
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
                    ),
                ): clave
                for clave, piezas in grupos_ordenados
            }

            for i, futuro in enumerate(concurrent.futures.as_completed(futuros)):
                clave = futuros[futuro]
                try:
                    raw_result = futuro.result()

                    if raw_result is None:
                        raise RuntimeError("El worker regresó None")

                    if isinstance(raw_result, tuple) and len(raw_result) == 2:
                        clave_worker, resultado_grupo = raw_result
                        if not clave_worker:
                            clave_worker = clave
                        resultados[clave] = resultado_grupo
                    elif isinstance(raw_result, dict):
                        # fallback tolerante por si algún camino del worker devuelve solo dict
                        resultados[clave] = raw_result
                    else:
                        raise RuntimeError(
                            f"Salida inesperada del worker: tipo={type(raw_result).__name__}"
                        )

                except Exception as exc:
                    print(f"Error en Lote {clave}: {exc}")
                    resultados[clave] = {"error": f"Error en cálculo: {exc}"}

                progreso_actual = 0.16 + ((i + 1) / total_lotes_reales) * 0.84
                notificar(f"Lotes procesados: {i + 1}/{total_lotes_reales}", progreso_actual)

        notificar("Construyendo modelos visuales...", 1.0)
        return resultados

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
    ):
        partes_clave = clave.split('_', 1) 
        req_cal = partes_clave[0]
        req_mat = partes_clave[1] if len(partes_clave) > 1 else ""

        placas_empresa = []
        placas_proveedor = []
        _dbg_nesting(
            f"[GRUPO-START] clave={clave} | piezas={len(piezas)} | "
            f"kerf={config_kerf} | margin={config_margin} | "
            f"opt={config_opt} | corner={config_corner} | wo={wo_name}"
        )

        for pz in piezas:
            poly_dbg = pz.get("poly")
            _dbg_nesting(
                f"[GRUPO-PIEZA] clave={clave} | debug_id={pz.get('debug_id', 'SIN_DEBUG_ID')} | "
                f"nombre={pz.get('nombre')} | area={pz.get('area', 0.0):.3f} | "
                f"bounds={_fmt_bounds(poly_dbg) if poly_dbg is not None else 'SIN_POLY'} | "
                f"ruta={pz.get('ruta', 'SIN_RUTA')}"
            )

        for placa in datos_placas:
            p_cal = placa[0]
            p_mat = placa[1]
            if self._coinciden(req_cal, p_cal) and self._coinciden(req_mat, p_mat):
                w_in = self._extraer_numero(placa[3]) 
                h_in = self._extraer_numero(placa[4])
                
                libras_totales_placa = self._extraer_numero(placa[5]) if len(placa) > 5 else 0.0
                origen_placa = str(placa[9]).upper() if len(placa) > 9 else "EMPRESA"
                precio_mxn = self._extraer_numero(placa[6]) if len(placa) > 6 else 0.0
                precio_usd_lb = self._extraer_numero(placa[10]) if len(placa) > 10 else (self._extraer_numero(placa[7]) if len(placa) > 7 else 0.0)
                
                if w_in > 0 and h_in > 0:
                    costo_placa_completa = precio_mxn if precio_mxn > 0 else (libras_totales_placa * precio_usd_lb)
                    datos_placa_dict = {
                        "data": placa, "w": w_in * 25.4, "h": h_in * 25.4, 
                        "precio": costo_placa_completa, "id": str(placa[2]),
                        "origen": origen_placa, "precio_lb": precio_usd_lb
                    }
                    if "EMPRESA" in origen_placa or origen_placa.strip() == "":
                        placas_empresa.append(datos_placa_dict)
                    else:
                        placas_proveedor.append(datos_placa_dict)

        placas_ok = placas_empresa if placas_empresa else placas_proveedor

        _dbg_nesting(
            f"[PLACAS-CANDIDATAS] clave={clave} | empresa={len(placas_empresa)} | "
            f"proveedor={len(placas_proveedor)}"
        )

        for placa_dbg in placas_ok:
            _dbg_nesting(
                f"[PLACA-OK] clave={clave} | placa_id={placa_dbg.get('id')} | "
                f"origen={placa_dbg.get('origen')} | w_mm={placa_dbg.get('w', 0.0):.3f} | "
                f"h_mm={placa_dbg.get('h', 0.0):.3f} | precio={placa_dbg.get('precio', 0.0):.3f} | "
                f"precio_lb={placa_dbg.get('precio_lb', 0.0):.3f}"
            )

        if not placas_ok:
            _dbg_nesting(
                f"[SIN-PLACA] clave={clave} | req_cal={req_cal} | req_mat={req_mat}"
            )
            return clave, {"error": f"Sin placa. No se halló inventario para {req_cal} {req_mat}."}

        if str(req_mat).strip().upper() == "CU":
            placas_largos = inventario_barras_largos_cu(placas_ok)
            _dbg_nesting(
                f"[CU-LARGOS] clave={clave} | barras={len(placas_largos)} | "
                f"override={str(cu_routing_override or '').strip() or 'auto'}"
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

        placas_ok.sort(key=lambda x: (x['precio_lb'], x['precio']))

        formatos_vistos = set()
        placas_unicas_simulacion = []
        for p in placas_ok:
            formato = f"{p['w']}x{p['h']}"
            if formato not in formatos_vistos:
                formatos_vistos.add(formato)
                placas_unicas_simulacion.append(p)

        AREA_LIMITE_MM2 = 499 * 645.16
        estructurales = [p for p in piezas if p['area'] > AREA_LIMITE_MM2]
        accesorios_base = [p for p in piezas if p['area'] <= AREA_LIMITE_MM2]

        nest_profile = get_nest_profile()
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
        
        pendientes_est = copy.deepcopy(estructurales)
        accesorios = copy.deepcopy(accesorios_base)
        num_placa_actual = 1

        while pendientes_est or accesorios:
            pool_est_snapshot = copy.deepcopy(pendientes_est)
            pool_acc_snapshot = copy.deepcopy(accesorios)
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
            
            for candidato_placa in candidatos_sim:
                sim_est = copy.deepcopy(pendientes_est)
                sim_acc = copy.deepcopy(accesorios)

                _dbg_nesting(
                    f"[SIM-PLACA-START] clave={clave} | placa_id={candidato_placa.get('id')} | "
                    f"w_mm={candidato_placa.get('w', 0.0):.3f} | h_mm={candidato_placa.get('h', 0.0):.3f} | "
                    f"precio={candidato_placa.get('precio', 0.0):.3f} | "
                    f"pendientes_est={len(sim_est)} | accesorios={len(sim_acc)}"
                )

                if q_msg:
                    q_msg.put(
                        f"[{req_cal}] Procesando Placa #{num_placa_actual} | "
                        f"Quedan: {len(sim_est) + len(sim_acc)} piezas..."
                    )

                hoja_sim = None
                restos_sim = []
                restos_est_out = []
                restos_acc_out = []

                if sim_est:
                    hoja_sim, restos_sim = _safe_empaquetar_una_hoja_mc(
                        sim_est,
                        candidato_placa["w"],
                        candidato_placa["h"],
                        config_kerf,
                        config_margin,
                        config_opt,
                        config_corner,
                        debug_tag=f"clave={clave} | placa_id={candidato_placa.get('id')} | modo=estructurales",
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
                        debug_tag=f"clave={clave} | placa_id={candidato_placa.get('id')} | modo=accesorios",
                        mc_iterations=mc_iters,
                        solo_accesorios=True,
                        accesorios_retries=cu_acc_retries,
                    )
                    restos_est_out = []
                    restos_acc_out = restos_sim
                    modo = "accesorios"
                else:
                    continue

                _dbg_nesting(
                    f"[SIM-PLACA-RESULT] clave={clave} | placa_id={candidato_placa.get('id')} | "
                    f"modo={modo} | piezas_colocadas={len(hoja_sim.get('piezas', []))} | "
                    f"area_usada={hoja_sim.get('area_usada', 0.0):.3f} | restos={len(restos_sim)}"
                )

                if not hoja_sim.get("piezas"):
                    continue

                restos_count = len(restos_est_out) + len(restos_acc_out)
                area_restos = _area_total_piezas(restos_est_out) + _area_total_piezas(restos_acc_out)
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

                if score < mejor_score:
                    mejor_score = score
                    mejor_hoja_temp = hoja_sim
                    mejor_restos_est = restos_est_out
                    mejor_restos_acc = restos_acc_out
                    mejor_placa = candidato_placa

            if not mejor_hoja_temp:
                _dbg_nesting(
                    f"[ERROR-CRITICO-EMPAQUE] clave={clave} | req_cal={req_cal} | req_mat={req_mat} | "
                    f"piezas_grupo={len(piezas)} | placas_candidatas={len(placas_ok)} | "
                    f"motivo=no se obtuvo ninguna hoja con piezas"
                )

                for pz in piezas:
                    poly_dbg = pz.get("poly")
                    _dbg_nesting(
                        f"[ERROR-PIEZA-CANDIDATA] clave={clave} | debug_id={pz.get('debug_id', 'SIN_DEBUG_ID')} | "
                        f"nombre={pz.get('nombre')} | area={pz.get('area', 0.0):.3f} | "
                        f"bounds={_fmt_bounds(poly_dbg) if poly_dbg is not None else 'SIN_POLY'} | "
                        f"ruta={pz.get('ruta', 'SIN_RUTA')}"
                    )

                return clave, {"error": "Error de empaquetado crítico. Geometría imposible de anidar."}

            hoja_ganadora = mejor_hoja_temp
            candidato_ganador = mejor_placa

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

            if pendientes_est:
                mejor_restos_est = calcular_restos_desde_colocados(pool_est_snapshot, hoja_ganadora)
            else:
                mejor_restos_acc = calcular_restos_desde_colocados(pool_acc_snapshot, hoja_ganadora)
            
            _dbg_nesting(
                f"[SIM-PLACA-GANADORA] clave={clave} | placa_id={candidato_ganador.get('id')} | "
                f"w_mm={candidato_ganador.get('w', 0.0):.3f} | h_mm={candidato_ganador.get('h', 0.0):.3f} | "
                f"precio={candidato_ganador.get('precio', 0.0):.3f} | "
                f"piezas_colocadas={len(hoja_ganadora.get('piezas', []))} | "
                f"area_usada={hoja_ganadora.get('area_usada', 0.0):.3f}"
            )

            hoja_ganadora.update({
                'placa_id': candidato_ganador['id'], 'placa_w': candidato_ganador['w'],
                'placa_h': candidato_ganador['h'], 'precio_placa': candidato_ganador['precio'],
                'kerf_usado': config_kerf, 'margin_usado': config_margin,
                'opt_usado': config_opt, 'corner_usado': config_corner,
                'es_retazo': False, 'origen_placa': candidato_ganador['origen']
            })
            
            if pendientes_est: pendientes_est = mejor_restos_est
            else: accesorios = mejor_restos_acc

            if sin_rtz:
                _dbg_nesting(
                    f"[SIN-RTZ-PLASMA] clave={clave} | placa_id={candidato_ganador.get('id')} | "
                    "renesteo compensado plasma: placa madre sin mini-nest ni hojas RTZ."
                )
                hojas_finales.append(hoja_ganadora)
                costo_total_lote += candidato_ganador['precio']
                num_placa_actual += 1
                continue

            mini_nests_locales = []
            retazos_virtuales = []
            contador_rtz = 1
            
            for p in list(hoja_ganadora['piezas']):
                if "REMANENTE__" in p['nombre']: continue
                poly = reconstruir_poly_seguro(p['poligonos'])
                for interior in interiores_poly(poly):
                    hole_poly = Polygon(interior)
                    minx, miny, maxx, maxy = hole_poly.bounds
                    w_r, h_r = maxx - minx, maxy - miny
                    if _retazo_cumple_tamano_minimo(w_r, h_r):
                        id_retazo = nombre_rtz_para_placa(
                            contador_rtz, req_cal, wo_name, largo_mm=h_r, ancho_mm=w_r
                        )
                        poly_local = affinity.translate(hole_poly, -minx, -miny)
                        retazos_virtuales.append({"id": id_retazo, "w": w_r, "h": h_r, "poly_borde": poly_local, "tipo": "HOLE", "global_x": minx, "global_y": miny})
                        contador_rtz += 1
                            
            max_x, max_y = 0, 0
            for p in list(hoja_ganadora['piezas']):
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
                        contador_rtz, req_cal, wo_name, largo_mm=h_rem, ancho_mm=w_rem
                    )
                    retazos_virtuales.append({"id": id_retazo, "w": w_rem, "h": h_rem, "poly_borde": affinity.translate(rem_der, -minx, -miny), "tipo": "SOBRANTE", "global_x": minx, "global_y": miny})
                    contador_rtz += 1

            if h_orig - max_y > 150:
                rem_arr = box(0, max_y, max_x, h_orig)
                minx, miny, maxx, maxy = rem_arr.bounds
                w_rem, h_rem = maxx - minx, maxy - miny
                if _retazo_cumple_tamano_minimo(w_rem, h_rem):
                    id_retazo = nombre_rtz_para_placa(
                        contador_rtz, req_cal, wo_name, largo_mm=h_rem, ancho_mm=w_rem
                    )
                    retazos_virtuales.append({"id": id_retazo, "w": w_rem, "h": h_rem, "poly_borde": affinity.translate(rem_arr, -minx, -miny), "tipo": "SOBRANTE", "global_x": minx, "global_y": miny})
                    contador_rtz += 1

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
                        
                        if hoja_retazo['piezas']:
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
                                    # En modo SIN MINI NEST estas piezas deben quedar reales en placa madre.
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
            hojas_finales.append(hoja_ganadora)
            if not forzar_sin_mini_nest:
                hojas_finales.extend(mini_nests_locales)
            costo_total_lote += candidato_ganador['precio']
            num_placa_actual += 1

        # REEMPLAZA ESTE BLOQUE EN manager.py (CASI AL FINAL DE LA FUNCIÓN)
        if hojas_finales:
            from .sheet_integrity import sanitizar_hojas_grupo, validar_colocacion_completa

            hojas_finales = sanitizar_hojas_grupo(
                piezas, hojas_finales, clave=clave, kerf_global=config_kerf
            )
            ok_inv, msg_inv = validar_colocacion_completa(piezas, hojas_finales)
            if not ok_inv:
                _dbg_nesting(f"[INVENTARIO-INCOMPLETO] clave={clave} | {msg_inv}")
                inventario_aviso = msg_inv
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
                **efi_grupo,
            }
            if inventario_aviso:
                resultado_placas["advertencia"] = inventario_aviso
            return clave, resultado_placas
        else:
            return clave, {
                "error": "Error de empaquetado crítico. Geometría imposible de anidar."
            }

    def _as_pack_piece_visual(self, p):
        poly = reconstruir_poly_seguro(p.get("poligonos") or [])
        if poly is None or poly.is_empty:
            return None

        marks_geom = _rebuild_marks_geom(p.get("marcas") or [])
        if marks_geom is None:
            marks_geom = LineString()

        minx, miny, _, _ = poly.bounds
        return {
            "nombre": str(p.get("nombre", "")),
            "poly": affinity.translate(poly, -minx, -miny),
            "marks": affinity.translate(marks_geom, -minx, -miny) if not marks_geom.is_empty else marks_geom,
            "area": float(p.get("area", poly.area) or poly.area),
            "calibre": p.get("calibre", ""),
            "material": p.get("material", ""),
        }

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
        if str(a.get("nombre", "")) != str(b.get("nombre", "")):
            return False
        return a.get("poligonos") == b.get("poligonos")

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

            if idx_hint is not None:
                p_idx = self._pieza_real_en_hoja_por_idx(hoja_origen, idx_hint)
                if p_idx is not None and id(p_idx) not in usados:
                    nombre_ps = str(ps.get("nombre", "") or "")
                    if not nombre_ps or str(p_idx.get("nombre", "") or "") == nombre_ps:
                        encontrada = p_idx

            if encontrada is None:
                for p in todas_origen:
                    if id(p) in usados:
                        continue
                    if p is ps or id(p) == id(ps):
                        encontrada = p
                        break

            if encontrada is None:
                nombre = str(ps.get("nombre", "") or "")
                matches = [
                    p
                    for p in todas_origen
                    if id(p) not in usados and str(p.get("nombre", "") or "") == nombre
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
                if encontrada is None and matches:
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
        for var in variaciones:
            for anchor_x, anchor_y in anclajes:
                px = anchor_x - var["b_minx"]
                py = anchor_y - var["b_miny"]
                if (
                    px + var["b_minx"] < margin_mm - 0.1
                    or py + var["b_miny"] < margin_mm - 0.1
                    or px + var["b_maxx"] > w - margin_mm + 0.1
                    or py + var["b_maxy"] > h - margin_mm + 0.1
                ):
                    rechazos["limite"] += 1
                    continue
                if self._comprobar_colision_transfer(
                    px, py, var, limite_prep, l_bounds, fijas_bounds, fijas_preps
                ):
                    rechazos["colision"] += 1
                    continue

                hubo_movimiento = True
                while hubo_movimiento:
                    hubo_movimiento = False
                    test_px = px - SLIDE_STEP_MM
                    if test_px + var["b_minx"] >= margin_mm:
                        if not self._comprobar_colision_transfer(
                            test_px, py, var, limite_prep, l_bounds, fijas_bounds, fijas_preps
                        ):
                            px = test_px
                            hubo_movimiento = True
                    test_py = py - SLIDE_STEP_MM
                    if test_py + var["b_miny"] >= margin_mm:
                        if not self._comprobar_colision_transfer(
                            px, test_py, var, limite_prep, l_bounds, fijas_bounds, fijas_preps
                        ):
                            py = test_py
                            hubo_movimiento = True

                poly_test = affinity.translate(var["poly"], px, py)
                if self._poly_invade_zonas_rtz_transfer(poly_test, zonas_rtz, clearance_rtz):
                    rechazos["rtz"] += 1
                    continue

                score = (px * px) + (py * py)
                if mejor is None or score < mejor[0]:
                    mejor = (score, var, px, py)

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
        to_remove = Counter(
            str(p.get("nombre") or "").strip()
            for p in piezas_mover
            if str(p.get("nombre") or "").strip()
        )
        to_remove_base = Counter(
            _piece_name_base(str(p.get("nombre") or ""))
            for p in piezas_mover
            if str(p.get("nombre") or "").strip()
        )
        nuevo = []
        for entry in pool:
            nom = str(entry.get("nombre") or "").strip()
            if not nom:
                nuevo.append(entry)
                continue
            base = _piece_name_base(nom)
            if to_remove.get(nom, 0) > 0:
                to_remove[nom] -= 1
                continue
            if to_remove_base.get(base, 0) > 0:
                to_remove_base[base] -= 1
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
        )


def _procesar_grupo_parallel_worker(job):
    """Worker de proceso: instancia limpia sin referencias a la UI Qt."""
    (
        clave,
        piezas,
        datos_placas,
        config_kerf,
        config_margin,
        config_opt,
        config_corner,
        wo_name,
    ) = job
    return MotorNesting()._procesar_grupo_parallel(
        clave,
        piezas,
        datos_placas,
        config_kerf,
        config_margin,
        config_opt,
        config_corner,
        wo_name,
    )