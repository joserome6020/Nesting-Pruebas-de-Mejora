import copy
import random
import os
import math
from shapely.geometry import Polygon, MultiLineString, LineString
from shapely import affinity
from shapely.prepared import prep

from modules.nesting_engine.geometry_parser import recuperar_geometria_robusta, reconstruir_poly_seguro, reconstruir_marks, generar_texto_vectorial

# --- PRUEBA DE CARGA ---
ruta_prueba = os.path.join(os.path.dirname(__file__), "PRUEBA_MOTOR_OPTIMIZADO.txt")
try:
    with open(ruta_prueba, "w") as f:
        f.write("¡Motor híbrido base: estructural + columnas; irregulares bottom-left (sin compactación por hueco)!")
except Exception as e:
    pass
# ----------------------

ITERACIONES_MONTE_CARLO = 3
ROTACIONES = [0, 90, 180, 270]

# Piezas muy grandes (segmentos, bases, etc.): mejor bottom-left clásico.
AREA_ESTRUCTURAL_UMBRAL_MM2 = 2_500_000.0


def _rectangularidad(poly):
    """Cociente área_bbox / área_real. ~1 si es rectángulo."""
    try:
        minx, miny, maxx, maxy = poly.bounds
        w = maxx - minx
        h = maxy - miny
        if w <= 0 or h <= 0:
            return 0.0
        bbox_a = w * h
        a = float(poly.area)
        if a <= 1e-6:
            return 0.0
        return bbox_a / a
    except Exception:
        return 0.0


def _clasificar_pieza(poly, area_val):
    """0=estructural grande, 1=rectangular-like, 2=intermedia, 3=irregular."""
    try:
        a = float(area_val or poly.area or 0.0)
    except Exception:
        a = 0.0
    r = _rectangularidad(poly)
    if a >= AREA_ESTRUCTURAL_UMBRAL_MM2:
        return 0
    if r >= 0.57:
        return 1
    if r < 0.52:
        return 3
    return 2


def _sort_key_pool(p):
    """Orden de colocación: grandes estructurales, bloques rectangulares por nombre, resto."""
    poly = p['poly']
    nombre = str(p.get('nombre') or '')
    try:
        area = float(p.get('area') or poly.area or 0.0)
    except Exception:
        area = 0.0
    clase = _clasificar_pieza(poly, area)
    return (clase, -area, nombre)


def _build_variaciones(poly_src, marks_src, w_placa, h_placa, margin_px, kerf_radio):
    variaciones = []
    for angulo in ROTACIONES:
        poly_rot = poly_src if angulo == 0 else affinity.rotate(poly_src, angulo, origin='centroid')
        marks_rot = marks_src
        if angulo != 0 and not marks_src.is_empty:
            marks_rot = affinity.rotate(marks_rot, angulo, origin=poly_src.centroid)

        minx, miny, maxx, maxy = poly_rot.bounds
        w_p, h_p = maxx - minx, maxy - miny
        poly_rot = affinity.translate(poly_rot, -minx, -miny)
        if not marks_rot.is_empty:
            marks_rot = affinity.translate(marks_rot, -minx, -miny)

        if w_p > (w_placa - 2 * margin_px + 5.0) or h_p > (h_placa - 2 * margin_px + 5.0):
            continue

        try:
            coords = list(poly_rot.exterior.coords)
            poly_shell = Polygon(coords).buffer(0.01).simplify(0.1, preserve_topology=False)
            poly_buff = poly_shell.buffer(kerf_radio, resolution=2, join_style=1)
            if poly_buff.geom_type == 'MultiPolygon':
                poly_buff = max(poly_buff.geoms, key=lambda a: a.area)
            if not poly_buff.is_valid:
                poly_buff = poly_buff.buffer(0)
        except Exception:
            poly_buff = poly_rot.convex_hull.buffer(kerf_radio)

        b_minx, b_miny, b_maxx, b_maxy = poly_buff.bounds
        variaciones.append({
            "w": w_p, "h": h_p, "poly": poly_rot, "poly_buff": poly_buff,
            "marks": marks_rot, "b_minx": b_minx, "b_miny": b_miny, "b_maxx": b_maxx, "b_maxy": b_maxy,
        })
    return variaciones


def empaquetar_una_hoja_mc(piezas, w_placa, h_placa, kerf_override=0.2, margin_override=0.0, opt_override="OPTIMIZAR LARGO Y ANCHO", corner_override="INFERIOR IZQUIERDA", limite_poly=None):
    mejor_hoja = {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0}
    mejor_restos = list(piezas)

    margin_px = (margin_override * 25.4) if margin_override else 0.0
    w_util = w_placa - (2 * margin_px)
    h_util = h_placa - (2 * margin_px)
    if w_util <= 0 or h_util <= 0:
        return mejor_hoja, mejor_restos

    pool_base = copy.deepcopy(piezas)
    pool_base.sort(key=_sort_key_pool)
    kerf_a_usar = kerf_override if kerf_override is not None else 0.2

    for i in range(ITERACIONES_MONTE_CARLO):
        pool_intento = copy.deepcopy(pool_base)
        if i > 0:
            mutaciones_por_grupo = {}
            for p in pool_intento:
                nom = p['nombre']
                if nom not in mutaciones_por_grupo:
                    mutaciones_por_grupo[nom] = random.uniform(0.85, 1.15)
            pool_intento.sort(
                key=lambda p: (
                    _sort_key_pool(p)[0],
                    -(p['area'] * mutaciones_por_grupo[p['nombre']]),
                    p['nombre'],
                )
            )

        hoja, restos = llenar_una_hoja_ultrafast(
            pool_intento, w_placa, h_placa, kerf_a_usar, margin_override, opt_override, corner_override, limite_poly
        )

        if hoja["area_usada"] > mejor_hoja["area_usada"]:
            mejor_hoja = hoja
            mejor_restos = restos
            if hoja["eficiencia"] > 91.0:
                break

    mejor_hoja["eficiencia"] = (mejor_hoja["area_usada"] / (w_placa * h_placa)) * 100 if (w_placa * h_placa) > 0 else 0
    return mejor_hoja, mejor_restos


def llenar_una_hoja_ultrafast(pendientes, w_placa, h_placa, kerf_custom=0.2, margin_custom=0.0, opt_mode="OPTIMIZAR LARGO Y ANCHO", corner_mode="INFERIOR IZQUIERDA", limite_poly=None):
    hoja = {"piezas": [], "area_usada": 0.0}
    fijas_polys_reales = []
    fijas_bounds = []
    fijas_preps = []
    fijas_huecos_preps = []
    pendientes_sig = []

    cdef double kerf_radio = (kerf_custom * 25.4) / 2.0
    cdef double margin_px = (margin_custom * 25.4) if margin_custom else 0.0

    cdef double px, py, ax, ay, b_minx, b_miny, b_maxx, b_maxy
    cdef double score, mejor_score
    cdef int hubo_movimiento
    cdef double test_px, test_py

    anclajes = [(margin_px, margin_px)]

    limite_efectivo = limite_poly.buffer(-margin_px) if limite_poly is not None else None
    limite_efectivo_eval = limite_efectivo.buffer(0.1) if limite_efectivo is not None else None
    l_minx, l_miny, l_maxx, l_maxy = limite_efectivo_eval.bounds if limite_efectivo_eval else (0, 0, 0, 0)
    limite_prep = prep(limite_efectivo_eval) if limite_efectivo_eval is not None else None

    def comprobar_colision(float pos_x, float pos_y, var_dict):
        cdef float cmx = pos_x + var_dict['b_minx']
        cdef float cmy = pos_y + var_dict['b_miny']
        cdef float cMx = pos_x + var_dict['b_maxx']
        cdef float cMy = pos_y + var_dict['b_maxy']

        if limite_prep is not None:
            if cmx < l_minx or cmy < l_miny or cMx > l_maxx or cMy > l_maxy:
                return True
            if not limite_prep.contains(affinity.translate(var_dict['poly_buff'], pos_x, pos_y)):
                return True

        c_buff_local = None
        for idx, f_b in enumerate(fijas_bounds):
            if not (cMx <= f_b[0] + 0.05 or cmx >= f_b[2] - 0.05 or cMy <= f_b[1] + 0.05 or cmy >= f_b[3] - 0.05):
                if c_buff_local is None:
                    c_buff_local = affinity.translate(var_dict['poly_buff'], pos_x, pos_y)
                if fijas_preps[idx].intersects(c_buff_local):
                    return True
        return False

    for p_data in pendientes:
        poly_orig = p_data['poly']
        marks_orig = p_data['marks']

        try:
            area_pieza = float(p_data.get('area') or poly_orig.area or 0.0)
        except Exception:
            area_pieza = 0.0
        rectangularidad = _rectangularidad(poly_orig)
        es_estructural_grande = area_pieza >= AREA_ESTRUCTURAL_UMBRAL_MM2
        es_rectangular = (not es_estructural_grande) and rectangularidad >= 0.57

        variaciones = _build_variaciones(poly_orig, marks_orig, w_placa, h_placa, margin_px, kerf_radio)

        if not variaciones:
            pendientes_sig.append(p_data)
            continue

        mejor_cand = None
        mejor_score = float('inf')

        for var in variaciones:
            b_minx = var['b_minx']
            b_miny = var['b_miny']
            b_maxx = var['b_maxx']
            b_maxy = var['b_maxy']

            for anclaje in anclajes:
                ax = anclaje[0]
                ay = anclaje[1]

                px = ax - b_minx
                py = ay - b_miny

                if (
                    px + b_minx < margin_px - 0.1
                    or py + b_miny < margin_px - 0.1
                    or px + b_maxx > w_placa - margin_px + 0.1
                    or py + b_maxy > h_placa - margin_px + 0.1
                ):
                    continue
                if comprobar_colision(px, py, var):
                    continue

                hubo_movimiento = 1
                while hubo_movimiento == 1:
                    hubo_movimiento = 0

                    test_px = px - 4.0
                    if test_px + b_minx >= margin_px:
                        if not comprobar_colision(test_px, py, var):
                            px = test_px
                            hubo_movimiento = 1

                    test_py = py - 4.0
                    if test_py + b_miny >= margin_px:
                        if not comprobar_colision(px, test_py, var):
                            py = test_py
                            hubo_movimiento = 1

                if es_estructural_grande:
                    score = (px * px) + (py * py)
                elif es_rectangular:
                    score = (px * 1000000.0) + py + ((py * py) * 0.00001)
                else:
                    # Irregulares e intermedios: mismo bottom-left que la base que funcionaba bien en triángulos.
                    score = (px * px) + (py * py)

                if score < mejor_score:
                    mejor_score = score
                    mejor_cand = {
                        "var": var,
                        "px": px,
                        "py": py,
                        "new_anchors": [
                            (px + b_maxx + 1.0, py + b_miny),
                            (px + b_minx, py + b_maxy + 1.0),
                        ],
                    }

        if mejor_cand:
            var = mejor_cand['var']
            cx = mejor_cand['px']
            cy = mejor_cand['py']

            cand_final = affinity.translate(var['poly'], cx, cy)
            cand_marks_final = affinity.translate(var['marks'], cx, cy) if not var['marks'].is_empty else None
            cand_buff_final = affinity.translate(var['poly_buff'], cx, cy)

            fijas_polys_reales.append(cand_final)
            fijas_bounds.append(cand_buff_final.bounds)
            fijas_preps.append(prep(cand_buff_final))

            anclajes.extend(mejor_cand['new_anchors'])
            anclajes = [a for a in anclajes if a[0] <= w_placa - margin_px and a[1] <= h_placa - margin_px]

            v_outer = list(cand_final.exterior.coords)
            v_holes = [list(h.coords) for h in cand_final.interiors]
            v_marks = []
            if cand_marks_final:
                if cand_marks_final.geom_type == 'LineString':
                    v_marks.append(list(cand_marks_final.coords))
                elif cand_marks_final.geom_type == 'MultiLineString':
                    for line in cand_marks_final.geoms:
                        v_marks.append(list(line.coords))

            hoja["piezas"].append({
                "nombre": p_data['nombre'],
                "poligonos": [v_outer] + v_holes,
                "marcas": v_marks,
                "area": p_data['area'],
                "calibre": p_data['calibre'],
                "material": p_data['material'],
            })
            hoja["area_usada"] += p_data['area']
        else:
            pendientes_sig.append(p_data)

    hoja["eficiencia"] = (hoja["area_usada"] / (w_placa * h_placa)) * 100 if (w_placa * h_placa) > 0 else 0
    return hoja, pendientes_sig
