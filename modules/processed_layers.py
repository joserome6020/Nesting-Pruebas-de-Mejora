import ezdxf
import os
import math
from datetime import datetime

class ProcesadorDXF:
    def __init__(self):
        self.LAYERS_CORTE = ['IV_INTERIOR_PROFILES', 'IV_OUTER_PROFILE']
        self.LAYERS_MARK = ['MARK', 'ETCH', 'SCRIBE', 'ENGRAVE', 'MARCAJE', 'GRABADO']

    def _extraer_primitivas(self, entity):
        # NOTA: CIRCLE no se procesa aquí; se maneja aparte para conservar su pureza geométrica.
        puntos = []
        dxftype = entity.dxftype()
        if dxftype == 'LINE':
            start, end = entity.dxf.start, entity.dxf.end
            puntos = [(start.x, start.y, 0), (end.x, end.y, 0)]
        elif dxftype == 'ARC':
            start, end = entity.start_point, entity.end_point
            sa, ea = entity.dxf.start_angle, entity.dxf.end_angle
            if ea < sa:
                ea += 360
            bulge = math.tan(math.radians(ea - sa) / 4)
            puntos = [(start.x, start.y, bulge), (end.x, end.y, 0)]
        elif dxftype == 'LWPOLYLINE':
            with entity.points('xyb') as points:
                puntos = list(points)
                if entity.is_closed and len(puntos) > 0:
                    puntos.append(puntos[0])
        return puntos

    def _dist(self, p1, p2):
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

    def _invertir_cadena(self, cadena):
        nueva = []
        for i in range(len(cadena)-1, -1, -1):
            x, y, _ = cadena[i]
            bg = -cadena[i-1][2] if i > 0 else 0
            nueva.append((x, y, bg))
        return nueva

    def _unir_primitivas_agresivo(self, lista_cadenas, tolerancia=0.005):
        if not lista_cadenas: return []
        pool, unidos = lista_cadenas[:], []
        while pool:
            actual = pool.pop(0)
            cambio = True
            while cambio:
                cambio = False
                as_pt, ae_pt = actual[0], actual[-1]
                idx_mejor, mejor_caso, menor_dist = -1, -1, tolerancia 
                for i, c in enumerate(pool):
                    cs, ce = c[0], c[-1]
                    d1, d2 = self._dist(ae_pt, cs), self._dist(ae_pt, ce)
                    d3, d4 = self._dist(as_pt, ce), self._dist(as_pt, cs)
                    if d1 < menor_dist: menor_dist, idx_mejor, mejor_caso = d1, i, 1
                    if d2 < menor_dist: menor_dist, idx_mejor, mejor_caso = d2, i, 2
                    if d3 < menor_dist: menor_dist, idx_mejor, mejor_caso = d3, i, 3
                    if d4 < menor_dist: menor_dist, idx_mejor, mejor_caso = d4, i, 4
                if idx_mejor != -1:
                    cand = pool.pop(idx_mejor)
                    if mejor_caso == 1:
                        actual[-1] = (actual[-1][0], actual[-1][1], cand[0][2])
                        actual.extend(cand[1:])
                    elif mejor_caso == 2:
                        cinv = self._invertir_cadena(cand)
                        actual[-1] = (actual[-1][0], actual[-1][1], cinv[0][2])
                        actual.extend(cinv[1:])
                    elif mejor_caso == 3:
                        cand[-1] = (cand[-1][0], cand[-1][1], actual[0][2])
                        cand.extend(actual[1:])
                        actual = cand
                    elif mejor_caso == 4:
                        cinv = self._invertir_cadena(cand)
                        cinv[-1] = (cinv[-1][0], cinv[-1][1], actual[0][2])
                        cinv.extend(actual[1:])
                        actual = cinv
                    cambio = True
            unidos.append(actual)
        return unidos

    def _calcular_area_con_bulges(self, puntos):
        area, arcos = 0.0, 0.0
        if len(puntos) < 2: return 0.0
        for i in range(len(puntos) - 1):
            x1, y1, b = puntos[i]
            x2, y2, _ = puntos[i+1]
            area += (x1 * y2 - x2 * y1)
            if b != 0:
                try:
                    d = math.hypot(x2-x1, y2-y1)
                    theta = 4 * math.atan(abs(b))
                    r = (d / 2) / math.sin(theta / 2)
                    seg = (r**2 / 2) * (theta - math.sin(theta))
                    arcos += seg if b > 0 else -seg
                except: pass
        return abs(0.5 * area + arcos)

    def _escribir_log(self, ruta, msg):
        try:
            with open(ruta, 'a', encoding='utf-8') as f: 
                f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        except: pass

    # --- FUNCIÓN MODIFICADA PARA CREAR 'Processed Files' AUTOMÁTICAMENTE ---
    def limpiar_archivo(self, ruta_entrada, ruta_salida_ignorada=None):
        # 1. Resolver destino final:
        #    - si se pasa ruta_salida_ignorada, respetarla (nuevo flujo AutoDXF centralizado),
        #    - en caso contrario, usar Processed Files junto al archivo fuente (compat legacy).
        if ruta_salida_ignorada:
            ruta_salida_real = os.path.abspath(str(ruta_salida_ignorada))
            carpeta_out = os.path.dirname(ruta_salida_real)
            nombre_archivo = os.path.basename(ruta_salida_real)
        else:
            carpeta_origen = os.path.dirname(ruta_entrada)
            nombre_archivo = os.path.basename(ruta_entrada)
            carpeta_out = os.path.join(carpeta_origen, "Processed Files")
            ruta_salida_real = os.path.join(carpeta_out, nombre_archivo)

        if not os.path.exists(carpeta_out):
            os.makedirs(carpeta_out)
        
        ruta_reporte = os.path.join(carpeta_out, "_LOG_PROCESO.txt")
        self._escribir_log(ruta_reporte, f"\n=== PROCESANDO: {nombre_archivo} ===")
        
        try:
            doc_in = ezdxf.readfile(ruta_entrada)
            
            geo_para_unir = []   # Aquí van líneas, arcos y polilíneas abiertas
            geo_mark_poly = []   # Marcas que son polilíneas
            objetos_finales = [] # Aquí guardaremos diccionarios {'type':..., 'data':..., 'area':...}
            
            # --- FASE 1: Clasificación y Extracción ---
            for e in doc_in.modelspace():
                if e.dxftype() in ['DIMENSION', 'MTEXT', 'LEADER', 'HATCH', 'TEXT']: continue
                
                layer = e.dxf.layer.upper().strip()
                es_corte = layer in self.LAYERS_CORTE
                es_mark = any(m in layer for m in self.LAYERS_MARK)
                
                if not (es_corte or es_mark): continue

                # CASO ESPECIAL: CÍRCULOS
                if e.dxftype() == 'CIRCLE':
                    centro = e.dxf.center
                    radio = e.dxf.radius
                    area = math.pi * (radio ** 2)
                    
                    obj_struct = {
                        'type': 'CIRCLE',
                        'data': {'center': (centro.x, centro.y), 'radius': radio},
                        'area': area,
                        'is_cut': es_corte
                    }
                    
                    if es_corte:
                        objetos_finales.append(obj_struct)
                    else:
                        obj_struct['layer_override'] = 'MARK'
                        objetos_finales.append(obj_struct)
                
                # CASO ESTÁNDAR: LÍNEAS, ARCOS, POLILÍNEAS
                else:
                    pts = self._extraer_primitivas(e)
                    if not pts: continue
                    
                    if es_corte:
                        geo_para_unir.append(pts)
                    elif es_mark:
                        geo_mark_poly.append(pts)
            
            if not geo_para_unir and not objetos_finales and not geo_mark_poly: 
                return False
            
            # --- FASE 2: Unir Geometrías Abiertas ---
            unidos = self._unir_primitivas_agresivo(geo_para_unir, 0.001)
            if len(unidos) > 1: unidos = self._unir_primitivas_agresivo(unidos, 0.005)
            if len(unidos) > 1: unidos = self._unir_primitivas_agresivo(unidos, 0.02)

            abiertos_descartados = 0
            
            for c in unidos:
                if self._dist(c[0], c[-1]) < 0.01:
                    c[-1] = (c[0][0], c[0][1], c[-1][2]) 
                    area = self._calcular_area_con_bulges(c)

                    # No descartar por area minima: puede haber barrenos pequenos
                    # que son totalmente validos y deben conservarse.
                    if area <= 0:
                        area = 1e-12

                    objetos_finales.append({
                        'type': 'POLY',
                        'data': c,
                        'area': area,
                        'is_cut': True
                    })
                else:
                    abiertos_descartados += 1

            # --- FASE 3: Crear DXF de Salida ---
            doc_out = ezdxf.new('R2010')
            doc_out.header['$INSUNITS'] = 1  
            doc_out.header['$MEASUREMENT'] = 0 

            msp = doc_out.modelspace()
            
            doc_out.layers.new('CUT_OUTER', dxfattribs={'color': 1}) 
            doc_out.layers.new('CUT_INNER', dxfattribs={'color': 3}) 
            doc_out.layers.new('MARK', dxfattribs={'color': 4})      

            cortes = [x for x in objetos_finales if x.get('is_cut')]
            marcas = [x for x in objetos_finales if not x.get('is_cut')] 

            if cortes:
                cortes.sort(key=lambda x: x['area'], reverse=True)
                
                primero = cortes[0]
                layer_name = 'CUT_OUTER'
                
                if primero['type'] == 'CIRCLE':
                    msp.add_circle(primero['data']['center'], primero['data']['radius'], dxfattribs={'layer': layer_name})
                else:
                    msp.add_lwpolyline(primero['data'], format='xyb', dxfattribs={'layer': layer_name, 'closed': True})
                
                for item in cortes[1:]:
                    layer_name = 'CUT_INNER'
                    if item['type'] == 'CIRCLE':
                        msp.add_circle(item['data']['center'], item['data']['radius'], dxfattribs={'layer': layer_name})
                    else:
                        msp.add_lwpolyline(item['data'], format='xyb', dxfattribs={'layer': layer_name, 'closed': True})

            for m in geo_mark_poly:
                msp.add_lwpolyline(m, format='xyb', dxfattribs={'layer': 'MARK'})
            
            for m in marcas:
                if m['type'] == 'CIRCLE':
                    msp.add_circle(m['data']['center'], m['data']['radius'], dxfattribs={'layer': 'MARK'})

            # AQUÍ ES DONDE GUARDAMOS EL ARCHIVO EN LA NUEVA CARPETA
            doc_out.saveas(ruta_salida_real)
            self._escribir_log(ruta_reporte, f"  > Objetos Procesados: {len(cortes)} | Descartados: {abiertos_descartados}")
            return True
            
        except Exception as e:
            self._escribir_log(ruta_reporte, f"ERROR: {str(e)}")
            return False