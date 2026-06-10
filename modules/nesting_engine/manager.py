import concurrent.futures
import multiprocessing
import threading
import copy
import re
import time
import os
from datetime import datetime
from shapely.geometry import box, Polygon, LineString
from shapely import affinity
from shapely.ops import unary_union

from .geometry_parser import (
    recuperar_geometria_robusta,
    reconstruir_poly_seguro,
    reconstruir_marks,
    generar_texto_vectorial,
)
from .algorithm import empaquetar_una_hoja_mc
from .efficiency_metrics import actualizar_eficiencias_hoja, calcular_eficiencias_grupo
from .exporter import exportar_resultados_a_dxf
from .rtz_overlays import sincronizar_overlays_grupo, sincronizar_overlays_resultados

DEBUG_DIR = r"C:\NEST_EXPORTS"
DEBUG_LOG_NESTING = os.path.join(DEBUG_DIR, "nesting_debug_geometry.txt")
DEFAULT_KERF_IN = 0.3
DEFAULT_MARGIN_IN = 0.15
THICKNESS_TOLERANCE_PCT = 0.15


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

def _piece_name_base(nombre: str) -> str:
    return (
        str(nombre or "")
        .replace("REF__", "")
        .replace("TATUAJE__", "")
        .replace("RETAZO_GUILLOTINA__", "")
        .replace("REMANENTE__", "")
        .strip()
    )


def _is_virtual_piece(nombre: str) -> bool:
    n = str(nombre or "")
    return (
        n.startswith("REF__")
        or n.startswith("TATUAJE__")
        or n.startswith("RETAZO_GUILLOTINA__")
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
    """
    Genera una versión de trabajo SOLO si no altera topología real.
    Si hay cualquier duda, regresa la geometría exacta.
    """
    if poly_exact is None or poly_exact.is_empty:
        return poly_exact

    try:
        poly_try = poly_exact.simplify(0.10, preserve_topology=True)

        if poly_try is None or poly_try.is_empty:
            return poly_exact

        if not poly_try.is_valid:
            poly_try = poly_try.buffer(0)

        if poly_try is None or poly_try.is_empty or not poly_try.is_valid:
            return poly_exact

        # No permitimos perder agujeros
        if _safe_holes(poly_try) != _safe_holes(poly_exact):
            return poly_exact

        # No permitimos cambios de área apreciables
        area_base = max(float(poly_exact.area), 1.0)
        delta_area = abs(float(poly_try.area) - float(poly_exact.area)) / area_base
        if delta_area > 0.001:
            return poly_exact

        return poly_try
    except Exception:
        return poly_exact


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

        best = None
        best_score = -10**9

        for ang in (0, 90, 180, 270):
            test_poly = affinity.rotate(poly_local, ang, origin=(0, 0))
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
                    test_marks = affinity.rotate(marks_local, ang, origin=(0, 0))
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

def _safe_empaquetar_una_hoja_mc(
    piezas,
    w_placa,
    h_placa,
    kerf_override=DEFAULT_KERF_IN,
    margin_override=DEFAULT_MARGIN_IN,
    opt_override="OPTIMIZAR LARGO Y ANCHO",
    corner_override="INFERIOR IZQUIERDA",
    limite_poly=None,
    debug_tag=""
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
            limite_poly=limite_poly
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
        
        for i, (pieza, mat, qty, cal, st, ruta) in enumerate(lista_partes):
            notificar(f"Analizando geometría: {pieza}...", (i / total_dxf) * 0.15)
            clave = f"{str(cal).strip().upper()}_{str(mat).strip().upper()}"
            if clave not in grupos:
                grupos[clave] = []

            _dbg_nesting(
                f"[PRE-PARSER] clave={clave} | pieza={pieza} | qty={qty} | status={st} | ruta={ruta}"
            )

            poly, marks = recuperar_geometria_robusta(ruta)

            if poly is None:
                _dbg_nesting(
                    f"[PARSER-FAIL] clave={clave} | pieza={pieza} | ruta={ruta} | "
                    f"motivo=recuperar_geometria_robusta devolvió None"
                )
                continue

            _dbg_nesting(
                f"[PARSER-OK] clave={clave} | pieza={pieza} | ruta={ruta} | "
                f"geom_type={_safe_geom_type(poly)} | area={_safe_area(poly):.3f} | "
                f"valid={_safe_is_valid(poly)} | holes={_safe_holes(poly)} | "
                f"bounds={_fmt_bounds(poly)} | {_safe_marks_info(marks)}"
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
                continue

            if poly_nesting is None or poly_nesting.is_empty:
                poly_nesting = poly_exact

            for idx_qty in range(int(qty)):
                grupos[clave].append({
                    "nombre": pieza,
                    # Para el motor actual dejamos poly como geometría de trabajo
                    "poly": poly_nesting,
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
        
        resultados = {}
        notificar("Iniciando Multiprocesamiento...", 0.16)

        grupos_con_piezas = {
            clave: piezas
            for clave, piezas in grupos.items()
            if piezas
        }

        total_lotes_reales = len(grupos_con_piezas)

        if total_lotes_reales == 0:
            _dbg_nesting("[ABORT] No hay grupos válidos para enviar a multiproceso")
            return {"error": "No hay grupos válidos para procesar."}

        nucleos_totales = multiprocessing.cpu_count()
        nucleos_a_usar = max(1, min(nucleos_totales - 2, total_lotes_reales))

        with concurrent.futures.ProcessPoolExecutor(max_workers=nucleos_a_usar) as executor:
            futuros = {
                executor.submit(
                    self._procesar_grupo_parallel,
                    clave,
                    piezas,
                    datos_placas,
                    config_kerf,
                    config_margin,
                    config_opt,
                    config_corner,
                    wo_name
                ): clave
                for clave, piezas in grupos_con_piezas.items()
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

    def _procesar_grupo_parallel(self, clave, piezas, datos_placas, config_kerf, config_margin, config_opt, config_corner, wo_name="PENDIENTE", q_msg=None):
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

        hojas_finales = []
        costo_total_lote = 0
        
        pendientes_est = copy.deepcopy(estructurales)
        accesorios = copy.deepcopy(accesorios_base)
        num_placa_actual = 1

        while pendientes_est or accesorios:
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

            mejor_hoja_temp = None
            mejor_score = float('inf')
            mejor_restos_est = []
            mejor_restos_acc = []
            mejor_placa = None
            
            for candidato_placa in placas_simulacion_validas:
                sim_est = copy.deepcopy(pendientes_est)
                sim_acc = copy.deepcopy(accesorios)

                _dbg_nesting(
                    f"[SIM-PLACA-START] clave={clave} | placa_id={candidato_placa.get('id')} | "
                    f"w_mm={candidato_placa.get('w', 0.0):.3f} | h_mm={candidato_placa.get('h', 0.0):.3f} | "
                    f"precio={candidato_placa.get('precio', 0.0):.3f} | "
                    f"pendientes_est={len(sim_est)} | accesorios={len(sim_acc)}"
                )

                if q_msg: q_msg.put(f"[{req_cal}] Procesando Placa #{num_placa_actual} | Quedan: {len(sim_est) + len(sim_acc)} piezas...")

                if sim_est:
                    hoja_sim, restos_sim = _safe_empaquetar_una_hoja_mc(
                        sim_est,
                        candidato_placa['w'],
                        candidato_placa['h'],
                        config_kerf,
                        config_margin,
                        config_opt,
                        config_corner,
                        debug_tag=f"clave={clave} | placa_id={candidato_placa.get('id')} | modo=estructurales"
                    )
                    _dbg_nesting(
                        f"[SIM-PLACA-RESULT] clave={clave} | placa_id={candidato_placa.get('id')} | "
                        f"modo=estructurales | piezas_colocadas={len(hoja_sim.get('piezas', []))} | "
                        f"area_usada={hoja_sim.get('area_usada', 0.0):.3f} | restos={len(restos_sim)}"
                    )
                    if hoja_sim['piezas']:
                        area = hoja_sim['area_usada']
                        efi = area / (candidato_placa['w'] * candidato_placa['h'])
                        costo_por_area = candidato_placa['precio'] / area
                        
                        # CASTIGO DE OPTIMIZACIÓN
                        if efi < 0.60: penalizacion = 100.0 + ((1.0 - efi) * 50.0)
                        else: penalizacion = 1.0 + ((1.0 - efi) ** 2) * 5.0 
                            
                        score = costo_por_area * penalizacion
                            
                        if score < mejor_score:
                            mejor_score = score
                            mejor_hoja_temp = hoja_sim
                            mejor_restos_est = restos_sim
                            mejor_placa = candidato_placa
                            
                elif sim_acc:
                    hoja_sim, restos_sim = _safe_empaquetar_una_hoja_mc(
                        sim_acc,
                        candidato_placa['w'],
                        candidato_placa['h'],
                        config_kerf,
                        config_margin,
                        config_opt,
                        config_corner,
                        debug_tag=f"clave={clave} | placa_id={candidato_placa.get('id')} | modo=accesorios"
                    )
                    _dbg_nesting(
                        f"[SIM-PLACA-RESULT] clave={clave} | placa_id={candidato_placa.get('id')} | "
                        f"modo=accesorios | piezas_colocadas={len(hoja_sim.get('piezas', []))} | "
                        f"area_usada={hoja_sim.get('area_usada', 0.0):.3f} | restos={len(restos_sim)}"
                    )
                    if hoja_sim['piezas']:
                        area = hoja_sim['area_usada']
                        efi = area / (candidato_placa['w'] * candidato_placa['h'])
                        costo_por_area = candidato_placa['precio'] / area
                        
                        # CASTIGO DE OPTIMIZACIÓN
                        if efi < 0.60: penalizacion = 100.0 + ((1.0 - efi) * 50.0)
                        else: penalizacion = 1.0 + ((1.0 - efi) ** 2) * 5.0 
                            
                        score = costo_por_area * penalizacion
                        
                        if score < mejor_score:
                            mejor_score = score
                            mejor_hoja_temp = hoja_sim
                            mejor_restos_acc = restos_sim
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
                
            mini_nests_locales = []
            retazos_virtuales = []
            contador_rtz = 1
            
            for p in list(hoja_ganadora['piezas']):
                if "REMANENTE__" in p['nombre']: continue
                poly = reconstruir_poly_seguro(p['poligonos'])
                if poly and len(poly.interiors) > 0:
                    for interior in poly.interiors:
                        hole_poly = Polygon(interior)
                        minx, miny, maxx, maxy = hole_poly.bounds
                        w_r, h_r = maxx - minx, maxy - miny
                        if _retazo_cumple_tamano_minimo(w_r, h_r):
                            id_retazo = f"RTZ{contador_rtz}-{req_cal}-{wo_name}"
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
                    id_retazo = f"RTZ{contador_rtz}-{req_cal}-{wo_name}"
                    retazos_virtuales.append({"id": id_retazo, "w": w_rem, "h": h_rem, "poly_borde": affinity.translate(rem_der, -minx, -miny), "tipo": "SOBRANTE", "global_x": minx, "global_y": miny})
                    contador_rtz += 1

            if h_orig - max_y > 150:
                rem_arr = box(0, max_y, max_x, h_orig)
                minx, miny, maxx, maxy = rem_arr.bounds
                w_rem, h_rem = maxx - minx, maxy - miny
                if _retazo_cumple_tamano_minimo(w_rem, h_rem):
                    id_retazo = f"RTZ{contador_rtz}-{req_cal}-{wo_name}"
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
                        hoja_retazo, restos_mezclados = _safe_empaquetar_una_hoja_mc(
                            candidatos_seguro,
                            retazo['w'],
                            retazo['h'],
                            config_kerf,
                            config_margin,
                            config_opt,
                            config_corner,
                            limite_poly=retazo['poly_borde'],
                            debug_tag=f"clave={clave} | retazo={retazo.get('id')} | modo=retazo"
                        )
                        
                        if hoja_retazo['piezas']:
                            rtz_usado = True

                            piezas_usadas = [p['nombre'] for p in hoja_retazo['piezas'] if not p['nombre'].startswith("REMANENTE")]
                            
                            def remover_usadas(lista_piezas, usadas_nombres):
                                nueva_lista = []
                                for item in lista_piezas:
                                    if item['nombre'] in usadas_nombres:
                                        usadas_nombres.remove(item['nombre'])
                                    else:
                                        nueva_lista.append(item)
                                return nueva_lista
                                
                            pendientes_est = remover_usadas(pendientes_est, list(piezas_usadas))
                            accesorios = remover_usadas(accesorios, list(piezas_usadas))

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
                                
                                if p_clon['poligonos']:
                                    nuevos_polys = []
                                    for pol_coords in p_clon['poligonos']:
                                        try:
                                            nuevos_polys.append(list(affinity.translate(Polygon(pol_coords), xoff=gx, yoff=gy).exterior.coords))
                                        except:
                                            nuevos_polys.append(pol_coords)
                                    p_clon['poligonos'] = nuevos_polys
                                    
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

                    p_final['ruta'] = p_orig.get('ruta')
                    p_final['orig_minx'] = p_orig.get('orig_minx', 0.0)
                    p_final['orig_miny'] = p_orig.get('orig_miny', 0.0)

                    transform = _inferir_transformacion_desde_resultado(p_orig, p_final)

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
            return clave, {
                "placa": "Óptima",
                "dim": "Multi",
                "hojas": hojas_finales,
                "costo_total": costo_total_lote,
                "costo_empresa": costo_empresa,
                "costo_proveedor": costo_proveedor,
                "reporte": "Reporte Generado.",
                **efi_grupo,
            }
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

    def _misma_pieza_visual(self, a, b):
        if a is b:
            return True
        if str(a.get("nombre", "")) != str(b.get("nombre", "")):
            return False
        return a.get("poligonos") == b.get("poligonos")

    def _localizar_hoja_origen(self, resultados_nesting, pieza_info, hoja_origen=None):
        origen_grupo = None
        origen_hoja = None
        idx_origen = -1

        if isinstance(hoja_origen, dict):
            for i, p in enumerate(hoja_origen.get("piezas") or []):
                if self._misma_pieza_visual(p, pieza_info):
                    for _, grupo in resultados_nesting.items():
                        if not isinstance(grupo, dict):
                            continue
                        if hoja_origen in (grupo.get("hojas") or []):
                            return grupo, hoja_origen, i
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

    def _simular_renest_en_destino(self, hoja_destino, piezas_dest_base, piezas_mover_raw):
        piezas_pack = list(piezas_dest_base)
        for p in piezas_mover_raw:
            pp = self._as_pack_piece_visual(p)
            if pp is not None:
                piezas_pack.append(pp)
        if not piezas_pack:
            return False, None

        params = self._params_hoja(hoja_destino)
        if params["w"] <= 0 or params["h"] <= 0:
            return False, None

        piezas_pack.sort(key=lambda x: x["area"], reverse=True)
        limite_poly = self._limite_poly_desde_hoja(hoja_destino)
        nueva_dest, sobras = _safe_empaquetar_una_hoja_mc(
            piezas_pack,
            params["w"],
            params["h"],
            params["kerf"],
            params["margin"],
            params["opt"],
            params["corner"],
            limite_poly=limite_poly,
            debug_tag="transfer_dest_batch",
        )
        if sobras:
            return False, None
        return True, nueva_dest

    def _maximo_lote_transferible(self, hoja_destino, piezas_dest_base, candidatos_raw):
        if not candidatos_raw:
            return []
        ok_todas, _ = self._simular_renest_en_destino(hoja_destino, piezas_dest_base, candidatos_raw)
        if ok_todas:
            return list(candidatos_raw)

        mejor = []
        for p in sorted(
            candidatos_raw,
            key=lambda x: float(x.get("area", 0.0) or 0.0),
            reverse=True,
        ):
            trial = mejor + [p]
            ok, _ = self._simular_renest_en_destino(hoja_destino, piezas_dest_base, trial)
            if ok:
                mejor = trial
        return mejor

    def _aplicar_transferencia_lote(self, origen_grupo, origen_hoja, hoja_destino, piezas_mover, nueva_dest):
        overlays_dest = self._extraer_overlays_hoja(hoja_destino)
        overlays_orig = self._extraer_overlays_hoja(origen_hoja)
        mover_ids = {id(p) for p in piezas_mover}
        piezas_orig_pack = []
        for p in (origen_hoja.get("piezas") or []):
            if id(p) in mover_ids or _is_virtual_piece(str(p.get("nombre", ""))):
                continue
            pp = self._as_pack_piece_visual(p)
            if pp is not None:
                piezas_orig_pack.append(pp)

        params_o = self._params_hoja(origen_hoja)
        if piezas_orig_pack:
            nueva_orig, _ = _safe_empaquetar_una_hoja_mc(
                piezas_orig_pack,
                params_o["w"],
                params_o["h"],
                params_o["kerf"],
                params_o["margin"],
                params_o["opt"],
                params_o["corner"],
                debug_tag="transfer_orig_batch",
            )
        else:
            nueva_orig = {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0}

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
        origen_hoja.update(nueva_orig)
        if isinstance(origen_grupo, dict):
            hojas = origen_grupo.get("hojas") or []
            sincronizar_overlays_grupo(hojas)
            for h in hojas:
                actualizar_eficiencias_hoja(h, hojas_grupo=hojas)
            calcular_eficiencias_grupo(hojas)
        else:
            actualizar_eficiencias_hoja(hoja_destino)
            actualizar_eficiencias_hoja(origen_hoja)

        if len(origen_hoja.get("piezas") or []) == 0 and isinstance(origen_grupo.get("hojas"), list):
            try:
                origen_grupo["hojas"].remove(origen_hoja)
            except Exception:
                pass

    def transferir_piezas_a_placa(
        self,
        resultados_nesting,
        hoja_origen,
        hoja_destino,
        piezas_especificas=None,
    ):
        """
        Renestea destino con todas las piezas candidatas juntas y mueve el máximo lote posible.
        """
        resultado = {
            "ok": False,
            "movidas": 0,
            "restantes": 0,
            "solicitadas": 0,
        }
        try:
            if (
                not isinstance(resultados_nesting, dict)
                or not isinstance(hoja_origen, dict)
                or not isinstance(hoja_destino, dict)
            ):
                return resultado
            if hoja_origen is hoja_destino:
                return resultado

            origen_grupo = None
            for _, grupo in resultados_nesting.items():
                if isinstance(grupo, dict) and hoja_origen in (grupo.get("hojas") or []):
                    origen_grupo = grupo
                    break
            if origen_grupo is None:
                return resultado

            todas_origen = self._piezas_reales_en_hoja(hoja_origen)
            if piezas_especificas:
                candidatos = []
                usados = set()
                for ps in piezas_especificas:
                    for p in todas_origen:
                        if id(p) in usados:
                            continue
                        if p is ps or self._misma_pieza_visual(p, ps):
                            candidatos.append(p)
                            usados.add(id(p))
                            break
            else:
                candidatos = list(todas_origen)

            resultado["solicitadas"] = len(candidatos)
            if not candidatos:
                return resultado

            piezas_dest_base = self._pack_piezas_destino(hoja_destino)
            lote = self._maximo_lote_transferible(hoja_destino, piezas_dest_base, candidatos)

            if not lote:
                movidas_fb = 0
                while True:
                    restantes = self._piezas_reales_en_hoja(hoja_origen)
                    if not restantes:
                        break
                    movio = False
                    for p in list(restantes):
                        if self.transferir_y_reoptimizar(
                            resultados_nesting,
                            p,
                            hoja_destino,
                            hoja_origen=hoja_origen,
                        ):
                            movidas_fb += 1
                            movio = True
                            break
                    if not movio:
                        break
                resultado["movidas"] = movidas_fb
                resultado["restantes"] = len(self._piezas_reales_en_hoja(hoja_origen))
                resultado["ok"] = movidas_fb > 0
                return resultado

            ok, nueva_dest = self._simular_renest_en_destino(hoja_destino, piezas_dest_base, lote)
            if not ok or nueva_dest is None:
                return resultado

            self._aplicar_transferencia_lote(origen_grupo, hoja_origen, hoja_destino, lote, nueva_dest)
            resultado["movidas"] = len(lote)
            resultado["restantes"] = len(self._piezas_reales_en_hoja(hoja_origen))
            resultado["ok"] = True
            return resultado
        except Exception as e:
            _dbg_nesting(f"[TRANSFER-BATCH-ERROR] {e}")
            return resultado

    def transferir_y_reoptimizar(self, resultados_nesting, pieza_info, hoja_destino, hoja_origen=None):
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
                resultados_nesting, pieza_info, hoja_origen=hoja_origen
            )
            if origen_hoja is None or idx_origen < 0:
                return False
            if origen_hoja is hoja_destino:
                return False

            pieza_mover = origen_hoja["piezas"][idx_origen]
            piezas_dest = self._pack_piezas_destino(hoja_destino)
            pp_mover = self._as_pack_piece_visual(pieza_mover)
            if pp_mover is None:
                return False
            piezas_dest.append(pp_mover)

            params_d = self._params_hoja(hoja_destino)
            if params_d["w"] <= 0 or params_d["h"] <= 0:
                return False

            piezas_dest.sort(key=lambda x: x["area"], reverse=True)
            limite_poly = self._limite_poly_desde_hoja(hoja_destino)
            nueva_dest, sobras_dest = _safe_empaquetar_una_hoja_mc(
                piezas_dest,
                params_d["w"],
                params_d["h"],
                params_d["kerf"],
                params_d["margin"],
                params_d["opt"],
                params_d["corner"],
                limite_poly=limite_poly,
                debug_tag="transfer_dest",
            )
            if sobras_dest:
                return False

            self._aplicar_transferencia_lote(
                origen_grupo,
                origen_hoja,
                hoja_destino,
                [pieza_mover],
                nueva_dest,
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
    wo_label=None
    ):
        """Redirige la orden de la interfaz gráfica hacia el archivo exporter.py"""
        return exportar_resultados_a_dxf(
            resultados,
            out_dir,
            base_name,
            generar_step,
            wo_label=wo_label
        )