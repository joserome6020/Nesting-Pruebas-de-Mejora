import ezdxf
from ezdxf import path
from shapely.geometry import Polygon, LineString, MultiLineString
from shapely.ops import polygonize, linemerge # <-- Aquí agregamos linemerge

# Constantes de configuración de capas
ESCALA_DXF = 25.4   
LAYER_OUTER = ["CUT_OUTER", "OUTER", "CORTE_EXTERNO", "0"] 
LAYER_INNER = ["CUT_INNER", "INNER", "CORTE_INTERNO"]
LAYER_MARK = ["MARK", "MARKING", "ETCH", "TEXT", "MARCADO"]

def entidad_a_lineas(entity, escala=ESCALA_DXF):
    lineas = []
    try:
        p = path.make_path(entity)
        vertices = list(p.flattening(distance=0.5)) 
        if len(vertices) > 1:
            v_scaled = [(v[0] * escala, v[1] * escala) for v in vertices]
            lineas.append(LineString(v_scaled))
    except Exception: pass
    return lineas

def recuperar_geometria_robusta(ruta_dxf):
    try:
        doc = ezdxf.readfile(ruta_dxf)
        msp = doc.modelspace()
        lines_outer, lines_inner, lines_mark = [], [], []

        for entity in msp:
            # Ignorar texto puro, cotas o bloques que no sean vectores de corte
            if entity.dxftype() not in ['LINE', 'LWPOLYLINE', 'POLYLINE', 'ARC', 'CIRCLE', 'ELLIPSE', 'SPLINE']:
                continue

            layer_name = str(entity.dxf.layer).upper().strip()
            geo = entidad_a_lineas(entity)
            if not geo: continue

            # --- FILTRO ESTRICTO DE CAPAS ---
            if any(x in layer_name for x in LAYER_MARK): 
                lines_mark.extend(geo)
            elif any(x in layer_name for x in LAYER_INNER): 
                lines_inner.extend(geo)
            elif any(x in layer_name for x in LAYER_OUTER): 
                lines_outer.extend(geo)
            # ELIMINAMOS EL 'ELSE' (Si hay basura en otra capa, se ignora por completo)

        if not lines_outer:
            return None, None

        # --- SOLDADURA DE MICRO-HUECOS ---
        # linemerge une los segmentos sueltos del DXF antes de intentar cerrarlos
        merged_outer = linemerge(lines_outer)
        candidatos_outer = list(polygonize(merged_outer))

        # Intento de rescate si el DXF viene muy abierto (cierra huecos milimétricos)
        if not candidatos_outer:
            outer_line = MultiLineString(lines_outer)
            candidatos_outer = list(polygonize(outer_line.buffer(0.01).exterior))

        if not candidatos_outer: return None, None
        
        # El contorno de la pieza siempre será el polígono más grande
        shell_poly = max(candidatos_outer, key=lambda x: x.area)
        
        holes = []
        if lines_inner:
            merged_inner = linemerge(lines_inner)
            candidatos_inner = list(polygonize(merged_inner))
            for h in candidatos_inner:
                # El hueco debe estar estrictamente DENTRO de la pieza
                if shell_poly.contains(h.centroid): 
                    holes.append(h)
        
        # Armamos la pieza final: Contorno real - Agujeros reales
        pieza_final = Polygon(shell_poly.exterior.coords, [h.exterior.coords for h in holes])
        
        # Si la geometría es rara, la reparamos internamente sin engordarla
        if not pieza_final.is_valid: 
            pieza_final = pieza_final.buffer(0)
            
        if pieza_final.area < 1.0: return None, None

        # Las marcas se quedan como vectores aislados
        marcas_final = MultiLineString(lines_mark) if lines_mark else MultiLineString()
        
        return pieza_final, marcas_final

    except Exception as e:
        print(f"Error procesando DXF {ruta_dxf}: {e}")
        return None, None

def reconstruir_poly_seguro(lista_poligonos):
    if not lista_poligonos: return None
    outer = lista_poligonos[0]
    holes = lista_poligonos[1:] if len(lista_poligonos) > 1 else []
    try:
        poly = Polygon(outer, holes)
        if not poly.is_valid: poly = poly.buffer(0)
        return poly
    except: return None

def reconstruir_marks(lista_coords_marcas):
    if not lista_coords_marcas: return MultiLineString()
    lines = [LineString(coords) for coords in lista_coords_marcas]
    return MultiLineString(lines)

def generar_texto_vectorial(texto, cx, cy, rw, rh):
    """Mantiene Matplotlib nativo para la conversión del texto a vectores"""
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