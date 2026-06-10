import copy
import random
from shapely.geometry import Polygon, box, MultiLineString, LineString
from shapely import affinity
from shapely.prepared import prep

from .geometry_parser import reconstruir_poly_seguro, reconstruir_marks, generar_texto_vectorial

ITERACIONES_MONTE_CARLO = 3
ROTACIONES = [0, 90, 180, 270]

def registrar_remanente_automatico(material, calibre, area_placa_mm2, area_usada_mm2, w_mm, h_mm, piezas_colocadas):
    return None, 0, 0, 0, 0

def empaquetar_una_hoja_mc(piezas, w_placa, h_placa, kerf_override=0.2, margin_override=0.0, opt_override="OPTIMIZAR LARGO Y ANCHO", corner_override="INFERIOR IZQUIERDA", limite_poly=None):
    mejor_hoja = {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0}
    mejor_restos = list(piezas)
    
    margin_px = (margin_override * 25.4) if margin_override else 0.0
    w_util = w_placa - (2 * margin_px)
    h_util = h_placa - (2 * margin_px)
    if w_util <= 0 or h_util <= 0: return mejor_hoja, mejor_restos

    def sort_key(p):
        minx, miny, maxx, maxy = p['poly'].bounds
        return (p['area'], max(maxx-minx, maxy-miny))

    pool_base = copy.deepcopy(piezas)
    pool_base.sort(key=sort_key, reverse=True)
    kerf_a_usar = kerf_override if kerf_override is not None else 0.2

    for i in range(ITERACIONES_MONTE_CARLO):
        pool_intento = copy.deepcopy(pool_base)
        if i > 0:
            pool_intento.sort(key=lambda p: p['area'] * random.uniform(0.85, 1.15), reverse=True)
            
        hoja, restos = llenar_una_hoja_ultrafast(pool_intento, w_placa, h_placa, kerf_a_usar, margin_override, opt_override, corner_override, limite_poly)
        
        if hoja["area_usada"] > mejor_hoja["area_usada"]:
            mejor_hoja = hoja
            mejor_restos = restos
            if hoja["eficiencia"] > 91.0: break 
    
    piezas_finales = []
    for p in mejor_hoja['piezas']:
        poly = reconstruir_poly_seguro(p['poligonos'])
        marks = reconstruir_marks(p.get('marcas', []))
        if poly:
            v_outer = list(poly.exterior.coords)
            v_holes = [list(h.coords) for h in poly.interiors]
            v_marks = []
            if not marks.is_empty:
                if marks.geom_type == 'LineString': v_marks.append(list(marks.coords))
                elif marks.geom_type == 'MultiLineString':
                    for line in marks.geoms: v_marks.append(list(line.coords))
            p['poligonos'] = [v_outer] + v_holes
            p['marcas'] = v_marks
            piezas_finales.append(p)
    
    mejor_hoja['piezas'] = piezas_finales
    mejor_hoja["eficiencia"] = (mejor_hoja["area_usada"] / (w_placa * h_placa)) * 100 if (w_placa * h_placa) > 0 else 0
    return mejor_hoja, mejor_restos

def llenar_una_hoja_ultrafast(pendientes, w_placa, h_placa, kerf_custom=0.2, margin_custom=0.0, opt_mode="OPTIMIZAR LARGO Y ANCHO", corner_mode="INFERIOR IZQUIERDA", limite_poly=None):
    hoja = {"piezas": [], "area_usada": 0.0}
    fijas_polys_reales = [] 
    fijas_bounds = [] 
    fijas_preps = [] 
    fijas_huecos_preps = [] 
    pendientes_sig = []
    occupied_log_max_u, occupied_log_max_v = 0.0, 0.0
    
    kerf_radio = (kerf_custom * 25.4) / 2.0 
    margin_px = (margin_custom * 25.4) if margin_custom else 0.0
    
    limite_efectivo = limite_poly.buffer(-margin_px) if limite_poly is not None else None
    limite_efectivo_eval = limite_efectivo.buffer(0.1) if limite_efectivo is not None else None
    l_minx, l_miny, l_maxx, l_maxy = limite_efectivo_eval.bounds if limite_efectivo_eval else (0,0,0,0)
    limite_prep = prep(limite_efectivo_eval) if limite_efectivo_eval is not None else None
    
    c_str = str(corner_mode).strip().upper()
    es_derecha = "DERECHA" in c_str
    es_superior = "SUPERIOR" in c_str

    def comprobar_colision(px, py, v_data):
        c_minx, c_miny = px + v_data['b_minx'], py + v_data['b_miny']
        c_maxx, c_maxy = px + v_data['b_maxx'], py + v_data['b_maxy']
        
        if limite_prep is not None:
            if c_minx < l_minx or c_miny < l_miny or c_maxx > l_maxx or c_maxy > l_maxy: return True
            c_buff = affinity.translate(v_data['poly_buff'], px, py)
            if not limite_prep.contains(c_buff): return True
        
        c_buff = None
        for i_idx, (f_bounds, fprep) in enumerate(zip(fijas_bounds, fijas_preps)):
            f_minx, f_miny, f_maxx, f_maxy = f_bounds
            if not (c_maxx <= f_minx or c_minx >= f_maxx or c_maxy <= f_miny or c_miny >= f_maxy):
                if c_buff is None: c_buff = affinity.translate(v_data['poly_buff'], px, py)
                try:
                    if fprep.intersects(c_buff):
                        dentro_de_hueco = False
                        for h_prep in fijas_huecos_preps[i_idx]:
                            if h_prep.contains(c_buff):
                                dentro_de_hueco = True
                                break
                        if not dentro_de_hueco: return True
                except: return True 
        return False
    
    # 🚀 OPTIMIZACIÓN: SELLADOR DE PLACAS
    fallos_consecutivos = 0
    MAX_FALLOS = 12 

    for p_data in pendientes:
        if fallos_consecutivos >= MAX_FALLOS:
            pendientes_sig.append(p_data)
            continue

        poly_orig = p_data['poly']; marks_orig = p_data['marks'] 
        variaciones = []
        
        for angulo in ROTACIONES:
            poly_rot = poly_orig if angulo == 0 else affinity.rotate(poly_orig, angulo, origin='centroid')
            marks_rot = marks_orig
            if angulo != 0 and not marks_orig.is_empty: marks_rot = affinity.rotate(marks_rot, angulo, origin=poly_orig.centroid)

            minx, miny, maxx, maxy = poly_rot.bounds
            w_p, h_p = maxx - minx, maxy - miny
            poly_rot = affinity.translate(poly_rot, -minx, -miny)
            if not marks_rot.is_empty: marks_rot = affinity.translate(marks_rot, -minx, -miny)
            
            if w_p > (w_placa - 2*margin_px + 15.0) or h_p > (h_placa - 2*margin_px + 15.0): continue
            
            try:
                coords = list(poly_rot.exterior.coords)
                poly_shell = Polygon(coords).buffer(0.01).simplify(0.1, preserve_topology=False)
                poly_buff = poly_shell.buffer(kerf_radio, resolution=2, join_style=1)
                if poly_buff.geom_type == 'MultiPolygon': poly_buff = max(poly_buff.geoms, key=lambda a: a.area)
                if not poly_buff.is_valid: poly_buff = poly_buff.buffer(0)
            except:
                poly_buff = poly_rot.convex_hull.buffer(kerf_radio)

            b_minx, b_miny, b_maxx, b_maxy = poly_buff.bounds
            variaciones.append({
                "w": w_p, "h": h_p, "poly": poly_rot, "poly_buff": poly_buff, 
                "marks": marks_rot, "b_minx": b_minx, "b_miny": b_miny, "b_maxx": b_maxx, "b_maxy": b_maxy
            })

        if not variaciones:
            fallos_consecutivos += 1
            pendientes_sig.append(p_data)
            continue

        mejor_cand = None
        mejor_score = float('inf')

        for var in variaciones:
            w_p, h_p = var['w'], var['h']
            
            paso_grueso = max(40.0, min(w_placa, h_placa) / 25.0) 
            paso_fino = 5.0

            if es_derecha: x_start, x_end, x_step_val = w_placa - margin_px - w_p, margin_px - 0.001, -paso_grueso
            else: x_start, x_end, x_step_val = margin_px, w_placa - margin_px - w_p + 0.001, paso_grueso
            
            if es_superior: y_start, y_end, y_step_val = h_placa - margin_px - h_p, margin_px - 0.001, -paso_grueso
            else: y_start, y_end, y_step_val = margin_px, h_placa - margin_px - h_p + 0.001, paso_grueso

            puntos_prueba = []
            y = y_start
            while (y >= y_end if es_superior else y <= y_end):
                x = x_start
                while (x >= x_end if es_derecha else x <= x_end):
                    puntos_prueba.append((x, y))
                    x += x_step_val
                y += y_step_val

            def calc_gravedad(pt):
                px, py = pt
                dx = (w_placa - (px + w_p)) if es_derecha else px
                dy = (h_placa - (py + h_p)) if es_superior else py
                return (dx ** 2) + (dy ** 2)
            
            puntos_prueba.sort(key=calc_gravedad)

            for x, y in puntos_prueba:
                if not comprobar_colision(x, y, var):
                    x_fino, y_fino = x, y
                    max_deslizamientos = int(paso_grueso / paso_fino) + 5
                    
                    intentos = 0
                    while intentos < max_deslizamientos:
                        intentos += 1
                        test_x = x_fino + (paso_fino if es_derecha else -paso_fino)
                        if (es_derecha and test_x > w_placa - margin_px - w_p) or (not es_derecha and test_x < margin_px): break
                        if comprobar_colision(test_x, y_fino, var): break
                        x_fino = test_x
                        
                    intentos = 0
                    while intentos < max_deslizamientos:
                        intentos += 1
                        test_y = y_fino + (paso_fino if es_superior else -paso_fino)
                        if (es_superior and test_y > h_placa - margin_px - h_p) or (not es_superior and test_y < margin_px): break
                        if comprobar_colision(x_fino, test_y, var): break
                        y_fino = test_y

                    score = calc_gravedad((x_fino, y_fino))
                    if score < mejor_score:
                        mejor_score = score
                        u_max, v_max = x_fino + w_p, y_fino + h_p
                        mejor_cand = (var, x_fino, y_fino, (max(occupied_log_max_u, u_max), max(occupied_log_max_v, v_max)))
                    break 

        if mejor_cand:
            var, x, y, log_dims = mejor_cand
            occupied_log_max_u, occupied_log_max_v = log_dims
            
            cand_final = affinity.translate(var['poly'], x, y)
            cand_marks_final = affinity.translate(var['marks'], x, y) if not var['marks'].is_empty else None
            cand_buff_final = affinity.translate(var['poly_buff'], x, y)
            
            fijas_polys_reales.append(cand_final)
            fijas_bounds.append(cand_buff_final.bounds)
            fijas_preps.append(prep(cand_buff_final)) 
            
            huecos_preparados = []
            for interior in cand_final.interiors:
                try:
                    h_poly = Polygon(interior).buffer(0)
                    if h_poly.is_valid: huecos_preparados.append(prep(h_poly))
                except: pass
            fijas_huecos_preps.append(huecos_preparados)
            
            v_outer = list(cand_final.exterior.coords)
            v_holes = [list(h.coords) for h in cand_final.interiors]
            v_marks = []
            if cand_marks_final:
                if cand_marks_final.geom_type == 'LineString': v_marks.append(list(cand_marks_final.coords))
                elif cand_marks_final.geom_type == 'MultiLineString':
                    for line in cand_marks_final.geoms: v_marks.append(list(line.coords))

            hoja["piezas"].append({
                "nombre": p_data['nombre'], "poligonos": [v_outer] + v_holes, "marcas": v_marks,
                "area": p_data['area'], "calibre": p_data.get("calibre", ""), "material": p_data.get("material", "")
            })
            hoja["area_usada"] += p_data['area']
            fallos_consecutivos = 0 # ¡Reset porque sí cupo!
        else: 
            fallos_consecutivos += 1 # ¡Aumenta el fallo!
            pendientes_sig.append(p_data)
    
    hoja["eficiencia"] = (hoja["area_usada"] / (w_placa * h_placa)) * 100 if (w_placa * h_placa) > 0 else 0
    return hoja, pendientes_sig

# (Deja el resto de tu archivo original intacto, recalcular_hoja_full, etc.)