import concurrent.futures
import multiprocessing
import ezdxf
from ezdxf import path
import copy
import re
import csv
import os
from datetime import datetime

from shapely.geometry import Polygon, LineString, MultiLineString, box
from shapely.ops import polygonize, unary_union
from shapely import affinity
from shapely.prepared import prep  

class MotorNesting:
    def __init__(self):
        self.margen_corte = 0.2 * 25.4
        self.escala_dxf = 25.4   
        self.iteraciones_monte_carlo = 1 
        self.rotaciones = [0, 90, 180, 270]
        
        self.LAYER_OUTER = ["CUT_OUTER", "OUTER", "CORTE_EXTERNO", "0"] 
        self.LAYER_INNER = ["CUT_INNER", "INNER", "CORTE_INTERNO"]
        self.LAYER_MARK = ["MARK", "MARKING", "ETCH", "TEXT", "MARCADO"]

    def _entidad_a_lineas(self, entity):
        lineas = []
        try:
            p = path.make_path(entity)
            vertices = list(p.flattening(distance=0.5)) 
            if len(vertices) > 1:
                v_scaled = [(v[0] * self.escala_dxf, v[1] * self.escala_dxf) for v in vertices]
                lineas.append(LineString(v_scaled))
        except Exception: pass
        return lineas

    def recuperar_geometria_robusta(self, ruta_dxf):
        try:
            doc = ezdxf.readfile(ruta_dxf)
            msp = doc.modelspace()
            lines_outer, lines_inner, lines_mark = [], [], []

            for entity in msp:
                layer_name = str(entity.dxf.layer).upper().strip()
                geo = self._entidad_a_lineas(entity)
                if not geo: continue

                if any(x in layer_name for x in self.LAYER_MARK): lines_mark.extend(geo)
                elif any(x in layer_name for x in self.LAYER_INNER): lines_inner.extend(geo)
                elif any(x in layer_name for x in self.LAYER_OUTER): lines_outer.extend(geo)
                else: lines_outer.extend(geo) 

            candidatos_outer = list(polygonize(lines_outer))
            if not candidatos_outer:
                if not lines_outer and lines_inner: candidatos_outer = list(polygonize(lines_inner))
            
            if not candidatos_outer: return None, None
            
            shell_poly = max(candidatos_outer, key=lambda x: x.area)
            holes = []
            if lines_inner:
                candidatos_inner = list(polygonize(lines_inner))
                for h in candidatos_inner:
                    if shell_poly.buffer(0.1).contains(h.centroid): holes.append(h)
            
            pieza_final = Polygon(shell_poly.exterior.coords, [h.exterior.coords for h in holes])
            if not pieza_final.is_valid: pieza_final = pieza_final.buffer(0)
            if pieza_final.area < 1.0: return None, None

            marcas_final = MultiLineString(lines_mark) if lines_mark else MultiLineString()
            return pieza_final, marcas_final
        except Exception:
            return None, None

    def _extraer_numero(self, valor):
        try:
            if isinstance(valor, (int, float)): return float(valor)
            limpio = str(valor).replace('$', '').replace(',', '.').strip()
            nums = re.findall(r"[-+]?\d*\.\d+|\d+", limpio)
            return float(nums[0]) if nums else 0.0
        except: return 0.0

    def _coinciden(self, val1, val2):
        v1 = str(val1).strip().upper()
        v2 = str(val2).strip().upper()
        if v1 == v2 or v1 in v2 or v2 in v1: return True
        try: return abs(float(v1) - float(v2)) < 0.0001
        except: return False

    def _generar_texto_vectorial(self, texto, cx, cy, rw, rh):
        """NUEVO MÉTODO: Usa Matplotlib nativo para asegurar la conversión 100% funcional del texto a vectores"""
        try:
            from matplotlib.textpath import TextPath
            from matplotlib.font_manager import FontProperties
            
            fp = FontProperties(family="sans-serif", weight="bold")
            tp = TextPath((0, 0), texto, size=10, prop=fp)
            
            polys = tp.to_polygons()
            if not polys: return []
            
            lineas = []
            all_x, all_y = [], []
            for poly in polys:
                coords = [(float(pt[0]), float(pt[1])) for pt in poly]
                if len(coords) > 1:
                    lineas.append(coords)
                    all_x.extend([pt[0] for pt in coords])
                    all_y.extend([pt[1] for pt in coords])
            
            if not lineas: return []
            
            minx, maxx = min(all_x), max(all_x)
            miny, maxy = min(all_y), max(all_y)
            text_w = maxx - minx
            text_h = maxy - miny
            
            if text_h <= 0: text_h = 1
            if text_w <= 0: text_w = 1
            
            es_vertical = rh > rw
            
            # Cálculo de factor de escala para ajustarse a los límites (rw, rh)
            if es_vertical:
                target_h = min(40.0, rw * 0.8) 
                scale = target_h / text_h
                if text_w * scale > rh * 0.85:
                    scale = (rh * 0.85) / text_w
            else:
                target_h = min(40.0, rh * 0.8)
                scale = target_h / text_h
                if text_w * scale > rw * 0.85:
                    scale = (rw * 0.85) / text_w
            
            mid_x = (minx + maxx) / 2
            mid_y = (miny + maxy) / 2
            
            marcas_finales = []
            for l in lineas:
                trazo = []
                for v in l:
                    x = (v[0] - mid_x) * scale
                    y = (v[1] - mid_y) * scale
                    if es_vertical:
                        x, y = -y, x 
                    trazo.append((x + cx, y + cy))
                marcas_finales.append(trazo)
            return marcas_finales
        except Exception as e:
            print(f"Error generando texto vectorial: {e}")
            return []

    def registrar_remanente_automatico(self, material, calibre, area_placa_mm2, area_usada_mm2, w_mm, h_mm, piezas_colocadas):
        return None, 0, 0, 0, 0

    def ejecutar_nesting_visual(self, lista_partes, datos_placas, progress_callback=None, 
                                config_kerf=0.2, config_margin=0.0, config_corner="INFERIOR IZQUIERDA", config_opt="OPTIMIZAR LARGO Y ANCHO", wo_name="PENDIENTE"):
        def notificar(msg, porcentaje):
            if progress_callback: progress_callback(msg, porcentaje)

        if not lista_partes: return {"error": "Lista vacía."}
        grupos = {}
        total_dxf = len(lista_partes)
        
        for i, (pieza, mat, qty, cal, st, ruta) in enumerate(lista_partes):
            notificar(f"Analizando geometría: {pieza}...", (i / total_dxf) * 0.15)
            clave = f"{str(cal).strip().upper()}_{str(mat).strip().upper()}"
            if clave not in grupos: grupos[clave] = []
            
            poly, marks = self.recuperar_geometria_robusta(ruta)
            if poly:
                minx, miny, _, _ = poly.bounds
                poly = affinity.translate(poly, -minx, -miny)
                if not marks.is_empty: marks = affinity.translate(marks, -minx, -miny)
                
                poly = poly.simplify(1.0, preserve_topology=False)
                if not poly.is_valid: poly = poly.buffer(0)
                
                for _ in range(int(qty)):
                    grupos[clave].append({
                        "nombre": pieza, "poly": poly, "marks": marks, 
                        "area": poly.area, "calibre": str(cal).strip(), "material": str(mat).strip()
                    })

        resultados = {}
        notificar("Iniciando Multiprocesamiento...", 0.16)
        
        nucleos_totales = multiprocessing.cpu_count()
        nucleos_a_usar = max(1, min(nucleos_totales - 2, len(grupos)))
        
        with concurrent.futures.ProcessPoolExecutor(max_workers=nucleos_a_usar) as executor:
            futuros = {
                executor.submit(self._procesar_grupo_parallel, clave, piezas, datos_placas, config_kerf, config_margin, config_opt, config_corner, wo_name): clave
                for clave, piezas in grupos.items() if piezas
            }
            
            for i, futuro in enumerate(concurrent.futures.as_completed(futuros)):
                clave = futuros[futuro]
                try:
                    _, resultado_grupo = futuro.result()
                    resultados[clave] = resultado_grupo
                except Exception as exc:
                    print(f"Error en Lote {clave}: {exc}")
                    resultados[clave] = {"error": f"Error en cálculo: {exc}"}
                    
                progreso_actual = 0.16 + ((i + 1) / len(grupos)) * 0.84
                notificar(f"Lotes procesados: {i + 1}/{len(grupos)}", progreso_actual)

        notificar("Construyendo modelos visuales...", 1.0)
        return resultados
    

    def _procesar_grupo_parallel(self, clave, piezas, datos_placas, config_kerf, config_margin, config_opt, config_corner, wo_name="PENDIENTE"):
        import copy
        from shapely.geometry import box, Polygon
        from shapely import affinity
        from shapely.ops import unary_union
        
        partes_clave = clave.split('_', 1) 
        req_cal = partes_clave[0]
        req_mat = partes_clave[1] if len(partes_clave) > 1 else ""

        placas_ok = []
        for placa in datos_placas:
            p_cal = placa[0]
            p_mat = placa[1]
            if self._coinciden(req_cal, p_cal) and self._coinciden(req_mat, p_mat):
                w_in = self._extraer_numero(placa[3]) 
                h_in = self._extraer_numero(placa[4])
                libras_totales_placa = self._extraer_numero(placa[5])
                precio_por_libra = self._extraer_numero(placa[7])
                
                if w_in > 0 and h_in > 0 and libras_totales_placa > 0:
                    costo_placa_completa = libras_totales_placa * precio_por_libra
                    placas_ok.append({
                        "data": placa, "w": w_in * 25.4, "h": h_in * 25.4, 
                        "precio": costo_placa_completa, "id": str(placa[2])
                    })

        if not placas_ok: 
            return clave, {"error": f"Sin placa. No se halló inventario de {req_cal} {req_mat}."}

        placas_ok.sort(key=lambda x: x['w'] * x['h'])
        
        AREA_LIMITE_MM2 = 499 * 645.16
        estructurales = []
        accesorios = []
        for p in piezas:
            if p['area'] > AREA_LIMITE_MM2:
                estructurales.append(p)
            else:
                accesorios.append(p)

        hojas_finales = []
        costo_total_grupo = 0
        
        pendientes_est = copy.deepcopy(estructurales)
        
        if pendientes_est:
            while pendientes_est:
                area_pendiente = sum(p['area'] for p in pendientes_est)
                placas_a_probar = [placas_ok[0]]
                for placa in placas_ok:
                    if (placa['w'] * placa['h']) >= (area_pendiente * 1.15):
                        if placa not in placas_a_probar: placas_a_probar.append(placa)
                        break
                if placas_ok[-1] not in placas_a_probar: placas_a_probar.append(placas_ok[-1])

                mejor_cand_hoja = None; mejor_cand_restos = []; mejor_cand_placa = None; mejor_score = float('inf') 
                
                for placa in placas_a_probar:
                    hoja_temp, restos_temp = self.empaquetar_una_hoja_mc(pendientes_est, placa['w'], placa['h'], config_kerf, config_margin, config_opt, config_corner)
                    if not hoja_temp['piezas']: continue 
                    
                    area_placa_actual = placa['w'] * placa['h']
                    score = area_placa_actual - hoja_temp['area_usada'] if len(restos_temp) == 0 else area_placa_actual / (hoja_temp['area_usada'] + 1)
                    
                    if score < mejor_score:
                        mejor_score = score; mejor_cand_hoja = hoja_temp; mejor_cand_restos = restos_temp; mejor_cand_placa = placa

                if not mejor_cand_hoja or len(mejor_cand_hoja.get('piezas', [])) == 0: 
                    break 
                    
                mejor_cand_hoja.update({
                    'placa_id': mejor_cand_placa['id'], 'placa_w': mejor_cand_placa['w'],
                    'placa_h': mejor_cand_placa['h'], 'precio_placa': mejor_cand_placa['precio'],
                    'kerf_usado': config_kerf, 'margin_usado': config_margin,
                    'opt_usado': config_opt, 'corner_usado': config_corner,
                    'es_retazo': False 
                })
                
                # =========================================================
                # EXTRACCIÓN DE RETAZOS (SOLO PARA ACCESORIOS)
                # =========================================================
                if accesorios:
                    retazos_virtuales = []
                    contador_rtz = 1
                    
                    # Extraer Holes
                    for p in list(mejor_cand_hoja['piezas']):
                        if "REMANENTE__" in p['nombre']: continue
                        poly = self._reconstruir_poly_seguro(p['poligonos'])
                        if poly and len(poly.interiors) > 0:
                            for interior in poly.interiors:
                                hole_poly = Polygon(interior)
                                if hole_poly.area > 16000:
                                    id_retazo = f"RTZ{contador_rtz}-{req_cal}-{wo_name}"
                                    minx, miny, maxx, maxy = hole_poly.bounds
                                    w_r, h_r = maxx - minx, maxy - miny
                                    poly_local = affinity.translate(hole_poly, -minx, -miny)
                                    
                                    retazos_virtuales.append({
                                        "id": id_retazo, "w": w_r, "h": h_r, "poly_borde": poly_local,
                                        "tipo": "HOLE", "global_x": minx, "global_y": miny
                                    })
                                    contador_rtz += 1
                                    
                    # Extraer Sobrantes con Guillotina
                    max_x, max_y = 0, 0
                    for p in list(mejor_cand_hoja['piezas']):
                        poly = self._reconstruir_poly_seguro(p['poligonos'])
                        if poly:
                            _, _, mx, my = poly.bounds
                            if mx > max_x: max_x = mx
                            if my > max_y: max_y = my
                            
                    w_orig, h_orig = mejor_cand_placa['w'], mejor_cand_placa['h']
                    
                    if w_orig - max_x > 150: 
                        rem_der = box(max_x, 0, w_orig, h_orig)
                        if rem_der.area > 32000:
                            id_retazo = f"RTZ{contador_rtz}-{req_cal}-{wo_name}"
                            minx, miny, maxx, maxy = rem_der.bounds
                            w_r, h_r = maxx - minx, maxy - miny
                            poly_local = affinity.translate(rem_der, -minx, -miny)
                            
                            retazos_virtuales.append({
                                "id": id_retazo, "w": w_r, "h": h_r, "poly_borde": poly_local,
                                "tipo": "SOBRANTE", "global_x": minx, "global_y": miny
                            })
                            contador_rtz += 1
                            
                    if h_orig - max_y > 150:
                        rem_arr = box(0, max_y, max_x, h_orig)
                        if rem_arr.area > 32000:
                            id_retazo = f"RTZ{contador_rtz}-{req_cal}-{wo_name}"
                            minx, miny, maxx, maxy = rem_arr.bounds
                            w_r, h_r = maxx - minx, maxy - miny
                            poly_local = affinity.translate(rem_arr, -minx, -miny)
                            
                            retazos_virtuales.append({
                                "id": id_retazo, "w": w_r, "h": h_r, "poly_borde": poly_local,
                                "tipo": "SOBRANTE", "global_x": minx, "global_y": miny
                            })
                            contador_rtz += 1

                    # PROCESAR RETAZOS
                    for retazo in retazos_virtuales:
                        if not accesorios: break
                        hoja_retazo, restos_acc = self.empaquetar_una_hoja_mc(
                            accesorios, retazo['w'], retazo['h'], 
                            config_kerf, config_margin, config_opt, config_corner, 
                            limite_poly=retazo['poly_borde']
                        )
                        
                        if hoja_retazo['piezas']: 
                            hoja_retazo.update({
                                'placa_id': retazo['id'], 'placa_w': retazo['w'], 'placa_h': retazo['h'], 
                                'precio_placa': 0.0, 
                                'kerf_usado': config_kerf, 'margin_usado': config_margin,
                                'opt_usado': config_opt, 'corner_usado': config_corner,
                                'es_retazo': True, 
                                'poly_borde_retazo': list(retazo['poly_borde'].exterior.coords)
                            })
                            
                            gx, gy = retazo['global_x'], retazo['global_y']

                            # =========================================================
                            # LÓGICA ESPACIAL: BUSCAR ESPACIO LIBRE PARA EL TATUAJE
                            # =========================================================
                            espacio_libre = retazo['poly_borde']
                            polys_restar = []
                            for p_acc in hoja_retazo['piezas']:
                                if p_acc['nombre'].startswith("REMANENTE__"): continue
                                try:
                                    p_poly = Polygon(p_acc['poligonos'][0]).buffer(10.0) 
                                    polys_restar.append(p_poly)
                                except: pass
                            
                            if polys_restar:
                                try:
                                    union_r = unary_union(polys_restar)
                                    espacio_libre = espacio_libre.difference(union_r)
                                except: pass
                            
                            cx_local, cy_local = retazo['w']/2, retazo['h']/2
                            w_disp, h_disp = retazo['w']*0.5, retazo['h']*0.5
                            
                            if not espacio_libre.is_empty:
                                if espacio_libre.geom_type == 'MultiPolygon':
                                    best_poly = max(espacio_libre.geoms, key=lambda a: a.area)
                                elif espacio_libre.geom_type == 'Polygon':
                                    best_poly = espacio_libre
                                else:
                                    best_poly = None
                                
                                if best_poly:
                                    minx_e, miny_e, maxx_e, maxy_e = best_poly.bounds
                                    w_disp = (maxx_e - minx_e)
                                    h_disp = (maxy_e - miny_e)
                                    cx_local, cy_local = best_poly.centroid.x, best_poly.centroid.y
                                    
                                    if not best_poly.contains(best_poly.centroid):
                                        rep_point = best_poly.representative_point()
                                        cx_local, cy_local = rep_point.x, rep_point.y
                                        w_disp *= 0.6
                                        h_disp *= 0.6
                            
                            cx_t_global = gx + cx_local
                            cy_t_global = gy + cy_local

                            w_texto = min(w_disp * 0.85, 400)
                            h_texto = min(h_disp * 0.85, 40)
                            if w_texto < 20: w_texto = 50
                            if h_texto < 10: h_texto = 15
                            
                            # Generar marcas vectoriales nativas (Locales y Globales)
                            marks_t_local  = self._generar_texto_vectorial(retazo['id'], cx_local, cy_local, w_texto, h_texto)
                            marks_t_global = self._generar_texto_vectorial(retazo['id'], cx_t_global, cy_t_global, w_texto, h_texto)
                            
                            dummy_p_local  = [[(cx_local-1, cy_local-1), (cx_local+1, cy_local-1), (cx_local+1, cy_local+1), (cx_local-1, cy_local+1), (cx_local-1, cy_local-1)]]
                            dummy_p_global = [[(cx_t_global-1, cy_t_global-1), (cx_t_global+1, cy_t_global-1), (cx_t_global+1, cy_t_global+1), (cx_t_global-1, cy_t_global+1), (cx_t_global-1, cy_t_global-1)]]
                            
                            # 1. Inyectar TATUAJE en la Hoja Local (Para que exporte e impacte en la Pestaña RTZ)
                            hoja_retazo['piezas'].append({
                                "nombre": f"TATUAJE__{retazo['id']}",
                                "poligonos": dummy_p_local, "marcas": marks_t_local, "area": 0.0, "calibre": req_cal, "material": req_mat
                            })
                            hojas_finales.append(hoja_retazo)
                            accesorios = restos_acc

                            # 2. INYECTAR GUILLOTINA Y TATUAJE A LA HOJA MAESTRA (Placa General)
                            if retazo['tipo'] == "HOLE":
                                mejor_cand_hoja['piezas'].append({
                                    "nombre": f"TATUAJE__{retazo['id']}",
                                    "poligonos": dummy_p_global, "marcas": marks_t_global, "area": 0.0, "calibre": req_cal, "material": req_mat
                                })
                            else: # SOBRANTE
                                min_x, min_y, max_x, max_y = gx, gy, gx + retazo['w'], gy + retazo['h']
                                poly_g = [[(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y), (min_x, min_y)]]
                                
                                mejor_cand_hoja['piezas'].append({
                                    "nombre": f"RETAZO_GUILLOTINA__{retazo['id']}",
                                    "poligonos": poly_g, "marcas": [], "area": 0.0, "calibre": req_cal, "material": req_mat
                                })
                                mejor_cand_hoja['piezas'].append({
                                    "nombre": f"TATUAJE__{retazo['id']}",
                                    "poligonos": dummy_p_global, "marcas": marks_t_global, "area": 0.0, "calibre": req_cal, "material": req_mat
                                })
                            
                            # CLONAR LOS ACCESORIOS COMO "FANTASMAS" GLOBALES
                            for p_acc in hoja_retazo['piezas']:
                                if p_acc['nombre'].startswith("REMANENTE__") or p_acc['nombre'].startswith("TATUAJE__"): continue
                                p_clon = copy.deepcopy(p_acc)
                                p_clon['nombre'] = f"REF__{p_clon['nombre']}"
                                
                                if p_clon['poligonos']:
                                    nuevos_polys = []
                                    from shapely.geometry import Polygon
                                    from shapely.affinity import translate
                                    for pol_coords in p_clon['poligonos']:
                                        try:
                                            p_temp = Polygon(pol_coords)
                                            p_temp_trans = translate(p_temp, xoff=gx, yoff=gy)
                                            nuevos_polys.append(list(p_temp_trans.exterior.coords))
                                        except: nuevos_polys.append(pol_coords)
                                    p_clon['poligonos'] = nuevos_polys
                                    
                                if p_clon['marcas']:
                                    nuevas_marcas = []
                                    from shapely.geometry import LineString
                                    from shapely.affinity import translate
                                    for line_coords in p_clon['marcas']:
                                        try:
                                            l_temp = LineString(line_coords)
                                            l_temp_trans = translate(l_temp, xoff=gx, yoff=gy)
                                            nuevas_marcas.append(list(l_temp_trans.coords))
                                        except: nuevas_marcas.append(line_coords)
                                    p_clon['marcas'] = nuevas_marcas
                                    
                                mejor_cand_hoja['piezas'].append(p_clon)

                hojas_finales.append(mejor_cand_hoja)
                costo_total_grupo += mejor_cand_placa['precio']
                pendientes_est = mejor_cand_restos

        if accesorios:
            pendientes_acc = copy.deepcopy(accesorios)
            while pendientes_acc:
                area_pendiente = sum(p['area'] for p in pendientes_acc)
                placas_a_probar = [placas_ok[0]]
                for placa in placas_ok:
                    if (placa['w'] * placa['h']) >= (area_pendiente * 1.15):
                        if placa not in placas_a_probar: placas_a_probar.append(placa)
                        break
                if placas_ok[-1] not in placas_a_probar: placas_a_probar.append(placas_ok[-1])

                mejor_cand_hoja = None; mejor_cand_restos = []; mejor_cand_placa = None; mejor_score = float('inf') 
                for placa in placas_a_probar:
                    hoja_temp, restos_temp = self.empaquetar_una_hoja_mc(pendientes_acc, placa['w'], placa['h'], config_kerf, config_margin, config_opt, config_corner)
                    if not hoja_temp['piezas']: continue 
                    score = (placa['w'] * placa['h']) - hoja_temp['area_usada']
                    if score < mejor_score:
                        mejor_score = score; mejor_cand_hoja = hoja_temp; mejor_cand_restos = restos_temp; mejor_cand_placa = placa

                if not mejor_cand_hoja or len(mejor_cand_hoja.get('piezas', [])) == 0: break 
                    
                mejor_cand_hoja.update({
                    'placa_id': mejor_cand_placa['id'], 'placa_w': mejor_cand_placa['w'],
                    'placa_h': mejor_cand_placa['h'], 'precio_placa': mejor_cand_placa['precio'],
                    'kerf_usado': config_kerf, 'margin_usado': config_margin,
                    'opt_usado': config_opt, 'corner_usado': config_corner,
                    'es_retazo': False
                })
                hojas_finales.append(mejor_cand_hoja)
                costo_total_grupo += mejor_cand_placa['precio']
                pendientes_acc = mejor_cand_restos

        if hojas_finales:
            return clave, {"placa": "Varias", "dim": "Multi", "hojas": hojas_finales, "costo_total": costo_total_grupo, "reporte": f"Reporte Generado."}
        else: 
            return clave, {"error": "Error de empaquetado. La pieza es más grande que las placas disponibles."}

    def empaquetar_una_hoja_mc(self, piezas, w_placa, h_placa, kerf_override=0.2, margin_override=0.0, opt_override="OPTIMIZAR LARGO Y ANCHO", corner_override="INFERIOR IZQUIERDA", limite_poly=None):
        mejor_hoja = {"piezas": [], "area_usada": 0.0, "eficiencia": 0.0}
        mejor_restos = list(piezas)
        
        margin_px = (margin_override * 25.4) if margin_override else 0.0
        w_util = w_placa - (2 * margin_px)
        h_util = h_placa - (2 * margin_px)
        if w_util <= 0 or h_util <= 0: return mejor_hoja, mejor_restos

        pool_ordenado = copy.deepcopy(piezas)
        pool_ordenado.sort(key=lambda x: x['area'], reverse=True)
        kerf_a_usar = kerf_override if kerf_override is not None else 0.2

        for i in range(self.iteraciones_monte_carlo):
            hoja, restos = self.llenar_una_hoja_ultrafast(pool_ordenado, w_placa, h_placa, kerf_a_usar, margin_override, opt_override, corner_override, limite_poly)
            if hoja["area_usada"] > mejor_hoja["area_usada"]:
                mejor_hoja = hoja; mejor_restos = restos
                if hoja["eficiencia"] > 90.0: break 
        
        piezas_finales = []
        for p in mejor_hoja['piezas']:
            poly = self._reconstruir_poly_seguro(p['poligonos'])
            marks = self._reconstruir_marks(p.get('marcas', []))
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

    def llenar_una_hoja_ultrafast(self, pendientes, w_placa, h_placa, kerf_custom=0.2, margin_custom=0.0, opt_mode="OPTIMIZAR LARGO Y ANCHO", corner_mode="INFERIOR IZQUIERDA", limite_poly=None):
        from shapely.geometry import Polygon, box
        from shapely import affinity
        from shapely.prepared import prep

        hoja = {"piezas": [], "area_usada": 0.0}
        fijas_polys_reales = [] 
        fijas_polys = []
        fijas_preps = [] 
        fijas_bounds = [] 
        pendientes_sig = []
        occupied_log_max_u, occupied_log_max_v = 0.0, 0.0
        
        kerf_radio = (kerf_custom * 25.4) / 2.0 
        margin_px = (margin_custom * 25.4) if margin_custom else 0.0
        
        limite_efectivo = limite_poly.buffer(-margin_px) if limite_poly is not None else None
        
        # --- Interpretación de Gravedad ---
        c_str = str(corner_mode).strip().upper()
        es_derecha = "DERECHA" in c_str
        es_superior = "SUPERIOR" in c_str
        
        for p_data in pendientes:
            poly_orig = p_data['poly']; marks_orig = p_data['marks'] 
            variaciones = []
            
            for angulo in self.rotaciones:
                poly_rot = poly_orig if angulo == 0 else affinity.rotate(poly_orig, angulo, origin='centroid')
                marks_rot = marks_orig
                if angulo != 0 and not marks_orig.is_empty: marks_rot = affinity.rotate(marks_rot, angulo, origin=poly_orig.centroid)

                minx, miny, maxx, maxy = poly_rot.bounds
                w_p, h_p = maxx - minx, maxy - miny
                poly_rot = affinity.translate(poly_rot, -minx, -miny)
                if not marks_rot.is_empty: marks_rot = affinity.translate(marks_rot, -minx, -miny)
                
                if w_p > (w_placa - 2*margin_px) or h_p > (h_placa - 2*margin_px): continue
                
                try:
                    coords = list(poly_rot.exterior.coords)
                    if len(coords) > 150: poly_shell = poly_rot.convex_hull
                    else:
                        poly_shell = Polygon(coords).buffer(0)
                        poly_shell = poly_shell.simplify(1.0, preserve_topology=False)
                        
                    poly_buff = poly_shell.buffer(kerf_radio, resolution=2, join_style=1)
                    if not poly_buff.is_valid: poly_buff = poly_buff.buffer(0)
                except:
                    poly_buff = box(*poly_rot.bounds).buffer(kerf_radio)

                b_minx, b_miny, b_maxx, b_maxy = poly_buff.bounds
                variaciones.append({
                    "w": w_p, "h": h_p, "poly": poly_rot, "poly_buff": poly_buff, 
                    "marks": marks_rot, "b_minx": b_minx, "b_miny": b_miny, "b_maxx": b_maxx, "b_maxy": b_maxy
                })

            if not variaciones:
                pendientes_sig.append(p_data)
                continue

            mejor_cand = None
            mejor_score = float('inf')

            for var in variaciones:
                w_p, h_p = var['w'], var['h']
                
                paso_x = max(3.0, w_p / 15.0)
                paso_y = max(3.0, h_p / 15.0)

                # Control de dirección (Eje X)
                if es_derecha: 
                    x_start, x_end, x_step_val = w_placa - margin_px - w_p, margin_px - 0.001, -paso_x
                else: 
                    x_start, x_end, x_step_val = margin_px, w_placa - margin_px - w_p + 0.001, paso_x
                
                # Control de dirección (Eje Y)
                if es_superior: 
                    y_start, y_end, y_step_val = h_placa - margin_px - h_p, margin_px - 0.001, -paso_y
                else: 
                    y_start, y_end, y_step_val = margin_px, h_placa - margin_px - h_p + 0.001, paso_y

                y = y_start
                colocada_rotacion = False
                
                while (y >= y_end if es_superior else y <= y_end):
                    x = x_start
                    while (x >= x_end if es_derecha else x <= x_end):
                        c_minx, c_miny = x + var['b_minx'], y + var['b_miny']
                        c_maxx, c_maxy = x + var['b_maxx'], y + var['b_maxy']
                        
                        choque = False
                        cand_buff = None 
                        
                        if limite_efectivo is not None:
                            cand_buff = affinity.translate(var['poly_buff'], x, y)
                            if not limite_efectivo.buffer(0.1).contains(cand_buff):
                                choque = True

                        if not choque:
                            for idx, (f_bounds, fprep) in enumerate(zip(fijas_bounds, fijas_preps)):
                                f_minx, f_miny, f_maxx, f_maxy = f_bounds
                                if not (c_maxx <= f_minx or c_minx >= f_maxx or c_maxy <= f_miny or c_miny >= f_maxy):
                                    if cand_buff is None: cand_buff = affinity.translate(var['poly_buff'], x, y)
                                    if fprep.intersects(cand_buff):
                                        dentro_de_un_hueco = False
                                        p_fija_real = fijas_polys_reales[idx]
                                        
                                        for interior in p_fija_real.interiors:
                                            if Polygon(interior).contains(cand_buff):
                                                dentro_de_un_hueco = True
                                                break
                                        
                                        if not dentro_de_un_hueco:
                                            choque = True; break
                        
                        if not choque:
                            # --- CÁLCULO DE PUNTAJE (GRAVEDAD 360) ---
                            dist_x = (w_placa - (x + w_p)) if es_derecha else x
                            dist_y = (h_placa - (y + h_p)) if es_superior else y
                            
                            u_max, v_max = dist_x + w_p, dist_y + h_p
                            score = dist_y * 1000 + dist_x 
                            
                            if score < mejor_score:
                                mejor_score = score
                                mejor_cand = (var, x, y, (max(occupied_log_max_u, u_max), max(occupied_log_max_v, v_max)))
                            
                            colocada_rotacion = True; break 
                        x += x_step_val
                    if colocada_rotacion: break
                    y += y_step_val

            if mejor_cand:
                var, x, y, log_dims = mejor_cand
                occupied_log_max_u, occupied_log_max_v = log_dims
                
                cand_final = affinity.translate(var['poly'], x, y)
                cand_marks_final = affinity.translate(var['marks'], x, y) if not var['marks'].is_empty else None
                cand_buff_final = affinity.translate(var['poly_buff'], x, y)
                
                fijas_polys_reales.append(cand_final)
                fijas_polys.append(cand_buff_final)
                fijas_preps.append(prep(cand_buff_final)) 
                fijas_bounds.append(cand_buff_final.bounds)
                
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
            else: pendientes_sig.append(p_data)
        
        hoja["eficiencia"] = (hoja["area_usada"] / (w_placa * h_placa)) * 100 if (w_placa * h_placa) > 0 else 0
        return hoja, pendientes_sig

    def _reconstruir_poly_seguro(self, lista_poligonos):
        if not lista_poligonos: return None
        outer = lista_poligonos[0]
        holes = lista_poligonos[1:] if len(lista_poligonos) > 1 else []
        try:
            poly = Polygon(outer, holes)
            if not poly.is_valid: poly = poly.buffer(0)
            return poly
        except: return None

    def _reconstruir_marks(self, lista_coords_marcas):
        if not lista_coords_marcas: return MultiLineString()
        lines = [LineString(coords) for coords in lista_coords_marcas]
        return MultiLineString(lines)

    def recalcular_hoja_full(self, hoja_data, nuevo_kerf, nuevo_margen, nueva_opt, nueva_esquina):
        piezas_a_reprocesar = []
        for p in hoja_data['piezas']:
            if p['nombre'].startswith("REMANENTE__"): continue
            poly = self._reconstruir_poly_seguro(p['poligonos'])
            marks = self._reconstruir_marks(p.get('marcas', []))
            if poly:
                minx, miny, _, _ = poly.bounds
                poly_norm = affinity.translate(poly, -minx, -miny)
                marks_norm = affinity.translate(marks, -minx, -miny) if not marks.is_empty else marks
                piezas_a_reprocesar.append({
                    "nombre": p['nombre'], "poly": poly_norm, "marks": marks_norm,
                    "area": p['area'], "calibre": p.get("calibre", ""), "material": p.get("material", "")
                })
        
        piezas_a_reprocesar.sort(key=lambda x: x['area'], reverse=True)
        w, h = hoja_data['placa_w'], hoja_data['placa_h']
        mejor_resultado = None
        for _ in range(3):
            nh, sobras = self.empaquetar_una_hoja_mc(piezas_a_reprocesar, w, h, nuevo_kerf, nuevo_margen, nueva_opt, nueva_esquina)
            if len(sobras) == 0:
                mejor_resultado = nh; break
        
        if not mejor_resultado: return None 
        
        mat = hoja_data['piezas'][0].get("material", "") if hoja_data['piezas'] else ""
        cal = hoja_data['piezas'][0].get("calibre", "") if hoja_data['piezas'] else ""
        id_rem, rx, ry, rw, rh = self.registrar_remanente_automatico(mat, cal, w*h, mejor_resultado['area_usada'], w, h, mejor_resultado['piezas'])
        if id_rem and rw > 0 and rh > 0:
            cx, cy = rx + (rw / 2.0), ry + (rh / 2.0)
            poly_rem = [[(rx, ry), (rx+rw, ry), (rx+rw, ry+rh), (rx, ry+rh), (rx, ry)]]
            mejor_resultado['piezas'].append({
                "nombre": f"REMANENTE__{id_rem}",
                "poligonos": poly_rem,
                "marcas": self._generar_texto_vectorial(id_rem, cx, cy, rw, rh),
                "area": 0.0,
                "calibre": cal,
                "material": mat
            })
            
        mejor_resultado.update({
            'placa_id': hoja_data['placa_id'], 'placa_w': w, 'placa_h': h, 'precio_placa': hoja_data.get('precio_placa', 0),
            'kerf_usado': nuevo_kerf, 'margin_usado': nuevo_margen, 'opt_usado': nueva_opt, 'corner_usado': nueva_esquina
        })
        return mejor_resultado

    def transferir_y_reoptimizar(self, resultados_nesting, pieza_info, hoja_destino):
        if pieza_info['nombre'].startswith("REMANENTE__"): return False
        
        kerf_destino = hoja_destino.get('kerf_usado', 0.2); margin_destino = hoja_destino.get('margin_usado', 0.0)
        opt_destino = hoja_destino.get('opt_usado', "OPTIMIZAR LARGO Y ANCHO"); corner_destino = hoja_destino.get('corner_usado', "INFERIOR IZQUIERDA")

        origen_grupo = None; origen_hoja_obj = None; indice_pieza_origen = -1; pieza_poly = None; pieza_marks = None
        
        for grupo_k, grupo_v in resultados_nesting.items():
            if 'hojas' not in grupo_v: continue
            for hoja in grupo_v['hojas']:
                for i, p in enumerate(hoja['piezas']):
                    if p['nombre'] == pieza_info['nombre']: 
                        pieza_poly = self._reconstruir_poly_seguro(p['poligonos'])
                        pieza_marks = self._reconstruir_marks(p.get('marcas', []))
                        origen_grupo = grupo_v; origen_hoja_obj = hoja; indice_pieza_origen = i
                        break
                if origen_hoja_obj: break
            if origen_hoja_obj: break
        
        if not origen_hoja_obj or not pieza_poly: return False

        piezas_destino_sim = []
        for p in hoja_destino['piezas']:
            if p['nombre'].startswith("REMANENTE__"): continue
            poly = self._reconstruir_poly_seguro(p['poligonos']); marks = self._reconstruir_marks(p.get('marcas', []))
            if poly:
                mx, my, _, _ = poly.bounds
                piezas_destino_sim.append({"nombre": p['nombre'], "poly": affinity.translate(poly, -mx, -my), "marks": affinity.translate(marks, -mx, -my) if not marks.is_empty else marks, "area": p['area'], "calibre": p.get("calibre", ""), "material": p.get("material", "")})
        
        minx, miny, _, _ = pieza_poly.bounds
        piezas_destino_sim.append({"nombre": pieza_info['nombre'], "poly": affinity.translate(pieza_poly, -minx, -miny), "marks": affinity.translate(pieza_marks, -minx, -miny) if not pieza_marks.is_empty else pieza_marks, "area": pieza_poly.area, "calibre": pieza_info.get("calibre", ""), "material": pieza_info.get("material", "")})
        piezas_destino_sim.sort(key=lambda x: x['area'], reverse=True)
        
        w_dest, h_dest = hoja_destino['placa_w'], hoja_destino['placa_h']
        nueva_hoja_dest, sobras_dest = self.empaquetar_una_hoja_mc(piezas_destino_sim, w_dest, h_dest, kerf_destino, margin_destino, opt_destino, corner_destino)
        if len(sobras_dest) > 0: return False 
            
        kerf_origen = origen_hoja_obj.get('kerf_usado', 0.2); margin_origen = origen_hoja_obj.get('margin_usado', 0.0)
        opt_origen = origen_hoja_obj.get('opt_usado', "OPTIMIZAR LARGO Y ANCHO"); corner_origen = origen_hoja_obj.get('corner_usado', "INFERIOR IZQUIERDA")

        piezas_origen_remanentes = []
        for i, p in enumerate(origen_hoja_obj['piezas']):
            if i == indice_pieza_origen or p['nombre'].startswith("REMANENTE__"): continue 
            poly = self._reconstruir_poly_seguro(p['poligonos']); marks = self._reconstruir_marks(p.get('marcas', []))
            if poly:
                mx, my, _, _ = poly.bounds
                piezas_origen_remanentes.append({"nombre": p['nombre'], "poly": affinity.translate(poly, -mx, -my), "marks": affinity.translate(marks, -mx, -my) if not marks.is_empty else marks, "area": p['area'], "calibre": p.get("calibre", ""), "material": p.get("material", "")})
        piezas_origen_remanentes.sort(key=lambda x: x['area'], reverse=True)
        
        w_orig, h_orig = origen_hoja_obj['placa_w'], origen_hoja_obj['placa_h']
        nueva_hoja_orig, _ = self.empaquetar_una_hoja_mc(piezas_origen_remanentes, w_orig, h_orig, kerf_origen, margin_origen, opt_origen, corner_origen)

        if nueva_hoja_dest['piezas']:
            mat_d = nueva_hoja_dest['piezas'][0].get("material", "")
            cal_d = nueva_hoja_dest['piezas'][0].get("calibre", "")
            id_rem_d, rx_d, ry_d, rw_d, rh_d = self.registrar_remanente_automatico(mat_d, cal_d, w_dest*h_dest, nueva_hoja_dest['area_usada'], w_dest, h_dest, nueva_hoja_dest['piezas'])
            if id_rem_d and rw_d > 0 and rh_d > 0:
                cx_d, cy_d = rx_d + (rw_d / 2.0), ry_d + (rh_d / 2.0)
                poly_rem_d = [[(rx_d, ry_d), (rx_d+rw_d, ry_d), (rx_d+rw_d, ry_d+rh_d), (rx_d, ry_d+rh_d), (rx_d, ry_d)]]
                nueva_hoja_dest['piezas'].append({
                    "nombre": f"REMANENTE__{id_rem_d}",
                    "poligonos": poly_rem_d,
                    "marcas": self._generar_texto_vectorial(id_rem_d, cx_d, cy_d, rw_d, rh_d),
                    "area": 0.0, "calibre": cal_d, "material": mat_d
                })

        if nueva_hoja_orig['piezas']:
            mat_o = nueva_hoja_orig['piezas'][0].get("material", "")
            cal_o = nueva_hoja_orig['piezas'][0].get("calibre", "")
            id_rem_o, rx_o, ry_o, rw_o, rh_o = self.registrar_remanente_automatico(mat_o, cal_o, w_orig*h_orig, nueva_hoja_orig['area_usada'], w_orig, h_orig, nueva_hoja_orig['piezas'])
            if id_rem_o and rw_o > 0 and rh_o > 0:
                cx_o, cy_o = rx_o + (rw_o / 2.0), ry_o + (rh_o / 2.0)
                poly_rem_o = [[(rx_o, ry_o), (rx_o+rw_o, ry_o), (rx_o+rw_o, ry_o+rh_o), (rx_o, ry_o+rh_o), (rx_o, ry_o)]]
                nueva_hoja_orig['piezas'].append({
                    "nombre": f"REMANENTE__{id_rem_o}",
                    "poligonos": poly_rem_o,
                    "marcas": self._generar_texto_vectorial(id_rem_o, cx_o, cy_o, rw_o, rh_o),
                    "area": 0.0, "calibre": cal_o, "material": mat_o
                })

        hoja_destino.update(nueva_hoja_dest)
        hoja_destino.update({'kerf_usado': kerf_destino, 'margin_usado': margin_destino, 'opt_usado': opt_destino, 'corner_usado': corner_destino})
        origen_hoja_obj.update(nueva_hoja_orig)
        origen_hoja_obj.update({'kerf_usado': kerf_origen, 'margin_usado': margin_origen, 'opt_usado': opt_origen, 'corner_usado': corner_origen})
        
        if len(origen_hoja_obj['piezas']) == 0:
            if origen_hoja_obj in origen_grupo['hojas']: origen_grupo['hojas'].remove(origen_hoja_obj)
        
        return True

    def exportar_resultados_a_dxf(self, resultados: dict, out_dir: str, base_name: str = "NEST", generar_step: bool = False):
        try:
            from modules.nest_exporter import export_nest_to_dxf
        except ImportError:
            from nest_exporter import export_nest_to_dxf
            
        import config
        from freecad_runner import ejecutar_macro_freecad
        from shapely.geometry import Polygon
        import os

        job_root_dir = os.path.join(out_dir, base_name)
        rutas = {
            "laser_dxf": os.path.join(job_root_dir, "Robot_Laser", "DXF"),
            "plasma_dxf": os.path.join(job_root_dir, "Robot_Plasma", "DXF"),
            "laser_step_A": os.path.join(job_root_dir, "Robot_Laser", "STEP", "Cama A"),
            "laser_step_B": os.path.join(job_root_dir, "Robot_Laser", "STEP", "Cama B"),
            "plasma_step_A": os.path.join(job_root_dir, "Robot_Plasma", "STEP", "Cama A"),
            "plasma_step_B": os.path.join(job_root_dir, "Robot_Plasma", "STEP", "Cama B"),
            "camas_laser_dxf": os.path.join(job_root_dir, "Camas_Laser_Manual", "DXF") 
        }

        for r in rutas.values():
            os.makedirs(r, exist_ok=True)

        exportados_laser = []
        thickness_para_step = getattr(config, 'FREECAD_THK_MM', 6.35)

        for clave, data in (resultados or {}).items():
            if not isinstance(data, dict) or "error" in data or "hojas" not in data:
                continue

            espesor_pulgadas = 0.25 
            try:
                thickness_str = clave.split("_", 1)[0]
                espesor_pulgadas = float(thickness_str)
                thickness_para_step = espesor_pulgadas * 25.4
            except: pass 

            PLASMA_OFFSET_MM = (0.250 if espesor_pulgadas > 0.75 else 0.0125) * 25.4

            for idx_hoja, hoja in enumerate(data.get("hojas", []), start=1):
                w_mm = hoja.get('placa_w', 2438.4)
                h_mm = hoja.get('placa_h', 1219.2)
                es_retazo = hoja.get('es_retazo', False) 

                sheet_info = {
                    "length": float(w_mm), "width": float(h_mm),
                    "material": clave.split("_", 1)[1] if "_" in clave else clave,
                    "thickness": thickness_str if 'thickness_str' in locals() else "",
                    "arga_code": str(hoja.get("placa_id", "STOCK")),
                }

                placements_laser = []
                placements_plasma = []

                if es_retazo and 'poly_borde_retazo' in hoja:
                    placements_laser.append({
                        "part_name": f"BORDE_RETAZO_{hoja['placa_id']}",
                        "outer": hoja['poly_borde_retazo'], 
                        "holes": [],
                        "marks": [],
                        "ruta": "" 
                    })

                for pz in hoja.get("piezas", []):
                    nom = pz.get("nombre", "PART")
                    
                    if nom.startswith("REF__"): 
                        continue 
                        
                    if nom.startswith("TATUAJE_"):
                        placements_laser.append({
                            "part_name": nom, "outer": [], "holes": [], "marks": pz.get("marcas", []), "ruta": ""
                        })
                        continue

                    pols = pz.get("poligonos", [])
                    if not pols: continue

                    placements_laser.append({
                        "part_name": nom,
                        "outer": pols[0], 
                        "holes": pols[1:] if len(pols) > 1 else [],
                        "marks": pz.get("marcas", []),
                        "ruta": pz.get("ruta") 
                    })

                    if not es_retazo and not nom.startswith("RETAZO_GUILLOTINA"):
                        try:
                            outer_poly = Polygon(pols[0])
                            plasma_outer = list(outer_poly.buffer(PLASMA_OFFSET_MM, join_style=2).exterior.coords)
                            
                            plasma_holes = []
                            for h in (pols[1:] if len(pols) > 1 else []):
                                h_comp = Polygon(h).buffer(-PLASMA_OFFSET_MM, join_style=2)
                                if not h_comp.is_empty:
                                    plasma_holes.append(list(h_comp.exterior.coords))
                        except:
                            plasma_outer, plasma_holes = pols[0], pols[1:]

                        placements_plasma.append({
                            "part_name": nom + "_PLASMA",
                            "outer": plasma_outer, "holes": plasma_holes,
                            "marks": pz.get("marcas", [])
                        })

                safe_clave = clave.replace(" ", "_").replace("/", "_")
                nombre_archivo = f"{base_name}_{safe_clave}_HOJA_{idx_hoja:02d}.dxf"

                if es_retazo:
                    path_camas = os.path.join(rutas["camas_laser_dxf"], nombre_archivo)
                    export_nest_to_dxf(path_camas, sheet_info, placements_laser, title=f"CAMA LASER (ACCESORIOS) | {clave}")
                else:
                    path_l = os.path.join(rutas["laser_dxf"], nombre_archivo)
                    export_nest_to_dxf(path_l, sheet_info, placements_laser, title=f"ROBOT LASER | {clave}")
                    exportados_laser.append(path_l)

                    path_p = os.path.join(rutas["plasma_dxf"], nombre_archivo)
                    export_nest_to_dxf(path_p, sheet_info, placements_plasma, title=f"ROBOT PLASMA | {clave}")

        if exportados_laser and generar_step:
            self._lanzar_freecad_robotica(rutas, thickness_para_step, PLASMA_OFFSET_MM)

        if str(base_name).startswith("SWO-"):
            self._enviar_reporte_a_api(base_name, resultados)

        return exportados_laser
    
    def _enviar_reporte_a_api(self, nombre_swo, datos_resultados):
        import json
        import urllib.request
        url_api = "http://127.0.0.1:8000/api/reportes/guardar"
        payload = {"swo": nombre_swo, "snapshot": datos_resultados}
        try:
            data_json = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(url_api, data=data_json, method='POST')
            req.add_header('Content-Type', 'application/json')
            with urllib.request.urlopen(req, timeout=5) as response:
                respuesta = json.loads(response.read().decode('utf-8'))
                if respuesta.get("estatus") == "ok":
                    print(f"✅ [ÉXITO] Reporte {nombre_swo} inyectado a la Base de Datos para la Web.")
        except Exception as e:
            print(f"❌ [ERROR] API Web: {str(e)}")

    def _lanzar_freecad_robotica(self, rutas, thk, plasma_off):
        from freecad_runner import ejecutar_macro_freecad
        import os
        os.environ["FREECAD_PLASMA_OFFSET"] = "0.0"
        ejecutar_macro_freecad(rutas["laser_dxf"], rutas["laser_step_A"], thk, "TR", 4235, -1015, -700)
        ejecutar_macro_freecad(rutas["laser_dxf"], rutas["laser_step_B"], thk, "BR", 4235, 840, -700)
        os.environ["FREECAD_PLASMA_OFFSET"] = str(plasma_off)
        ejecutar_macro_freecad(rutas["plasma_dxf"], rutas["plasma_step_A"], thk, "TR", 4235, -1015, -700)
        ejecutar_macro_freecad(rutas["plasma_dxf"], rutas["plasma_step_B"], thk, "BR", 4235, 840, -700)