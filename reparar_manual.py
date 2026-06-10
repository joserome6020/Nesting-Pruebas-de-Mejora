import ezdxf
import os
import math
import sys
import tkinter as tk
from tkinter import filedialog
from datetime import datetime

# =========================================================================
# CLASE PROCESADOR DXF (MODO QUIRÚRGICO V6)
# =========================================================================
class ProcesadorDXF:
    def __init__(self):
        # 1. Capas para CORTE (Se unen y clasifican)
        self.LAYERS_CORTE = [
            'IV_INTERIOR_PROFILES', 
            'IV_OUTER_PROFILE'  # Corregido singular
        ]
        
        # 2. Capas para MARCADO (Solo se copian)
        self.LAYERS_MARK = [
            'IV_MARK_SURFACE', 
            'IV_MARK_TROUGHT', 
            'IV_FEATURE_PROFILES', 
            'IV_FEATURE_PROFILES_DOWN'
        ]

    # --- HERRAMIENTAS MATEMÁTICAS ---
    def _dist(self, p1, p2):
        return math.hypot(p1[0]-p2[0], p1[1]-p2[1])

    def _extraer_primitivas(self, entity):
        puntos = [] 
        dxftype = entity.dxftype()
        if dxftype == 'LINE':
            start = entity.dxf.start
            end = entity.dxf.end
            puntos = [(start.x, start.y, 0), (end.x, end.y, 0)]
        elif dxftype == 'ARC':
            start = entity.start_point
            end = entity.end_point
            start_angle = entity.dxf.start_angle
            end_angle = entity.dxf.end_angle
            if end_angle < start_angle: end_angle += 360
            sweep_angle = math.radians(end_angle - start_angle)
            bulge = math.tan(sweep_angle / 4)
            puntos = [(start.x, start.y, bulge), (end.x, end.y, 0)]
        elif dxftype == 'LWPOLYLINE':
            with entity.points('xyb') as points:
                puntos = list(points)
                if entity.is_closed and len(puntos) > 0:
                    puntos.append(puntos[0])
        elif dxftype == 'CIRCLE':
            c = entity.dxf.center
            r = entity.dxf.radius
            p1 = (c.x - r, c.y, 1)
            p2 = (c.x + r, c.y, 1)
            puntos = [p1, p2, p1] 
        return puntos

    def _invertir_cadena(self, cadena):
        nueva = []
        for i in range(len(cadena)-1, -1, -1):
            x, y, _ = cadena[i]
            new_bulge = 0
            if i > 0: new_bulge = -cadena[i-1][2]
            nueva.append((x, y, new_bulge))
        return nueva

    def _unir_primitivas_agresivo(self, lista_cadenas, tolerancia_mm=0.1):
        if not lista_cadenas: return []
        pool = lista_cadenas[:]
        unidos = []
        while pool:
            actual = pool.pop(0)
            cambio = True
            while cambio:
                cambio = False
                act_start = actual[0]
                act_end = actual[-1]
                idx_mejor, mejor_caso, menor_dist = -1, -1, tolerancia_mm 
                for i, candidato in enumerate(pool):
                    cand_start, cand_end = candidato[0], candidato[-1]
                    d1 = self._dist(act_end, cand_start)
                    d2 = self._dist(act_end, cand_end)
                    d3 = self._dist(act_start, cand_end)
                    d4 = self._dist(act_start, cand_start)
                    if d1 < menor_dist: menor_dist, idx_mejor, mejor_caso = d1, i, 1
                    if d2 < menor_dist: menor_dist, idx_mejor, mejor_caso = d2, i, 2
                    if d3 < menor_dist: menor_dist, idx_mejor, mejor_caso = d3, i, 3
                    if d4 < menor_dist: menor_dist, idx_mejor, mejor_caso = d4, i, 4
                if idx_mejor != -1:
                    candidato = pool[idx_mejor]
                    if mejor_caso == 1:
                        actual[-1] = (actual[-1][0], actual[-1][1], candidato[0][2])
                        actual.extend(candidato[1:])
                    elif mejor_caso == 2:
                        cand_inv = self._invertir_cadena(candidato)
                        actual[-1] = (actual[-1][0], actual[-1][1], cand_inv[0][2])
                        actual.extend(cand_inv[1:])
                    elif mejor_caso == 3:
                        candidato[-1] = (candidato[-1][0], candidato[-1][1], actual[0][2])
                        candidato.extend(actual[1:])
                        actual = candidato
                    elif mejor_caso == 4:
                        cand_inv = self._invertir_cadena(candidato)
                        cand_inv[-1] = (cand_inv[-1][0], cand_inv[-1][1], actual[0][2])
                        cand_inv.extend(actual[1:])
                        actual = cand_inv
                    pool.pop(idx_mejor)
                    cambio = True
            unidos.append(actual)
        return unidos

    def _calcular_area_con_bulges(self, puntos):
        area_poligono, area_arcos = 0.0, 0.0
        n = len(puntos)
        if n < 2: return 0.0
        for i in range(n - 1):
            x1, y1, bulge = puntos[i]
            x2, y2, _ = puntos[i+1]
            area_poligono += (x1 * y2 - x2 * y1)
            if bulge != 0:
                try:
                    distancia = math.hypot(x2-x1, y2-y1)
                    theta = 4 * math.atan(abs(bulge))
                    if abs(theta) < 0.0001: continue
                    radius = (distancia / 2) / math.sin(theta / 2)
                    area_segmento = (radius**2 / 2) * (theta - math.sin(theta))
                    if bulge > 0: area_arcos += area_segmento
                    else: area_arcos -= area_segmento
                except: pass
        return abs(0.5 * area_poligono + area_arcos)

    def _guardar_reporte(self, ruta, lineas):
        try:
            with open(ruta, 'w', encoding='utf-8') as f:
                f.write("\n".join(lineas))
        except: pass

    # --- LÓGICA PRINCIPAL ---
    def limpiar_archivo(self, ruta_entrada, ruta_salida):
        # Preparación de Reporte (Se guarda junto al archivo de salida)
        nombre_archivo = os.path.basename(ruta_entrada)
        carpeta_out = os.path.dirname(ruta_salida)
        
        # Asegurar que exista la carpeta Procesados
        if not os.path.exists(carpeta_out):
            os.makedirs(carpeta_out)
            
        ruta_reporte = os.path.join(carpeta_out, f"REPORTE_{nombre_archivo}.txt")
        
        log = []
        def agregar_log(msg):
            print(msg)
            log.append(msg)

        agregar_log(f"=== REPORTE DE PROCESAMIENTO DXF ===")
        agregar_log(f"Fecha: {datetime.now()}")
        agregar_log(f"Original: {ruta_entrada}")
        agregar_log(f"Salida:   {ruta_salida}")
        agregar_log(f"="*40)

        try:
            doc_in = ezdxf.readfile(ruta_entrada)
            msp_in = doc_in.modelspace()
            
            geometria_corte = [] 
            geometria_mark = []
            stats_layers = {}
            entidades_ignoradas = 0

            agregar_log("\n[FASE 1] FILTRADO DE CAPAS:")
            
            for e in msp_in:
                if e.dxftype() in ['DIMENSION', 'MTEXT', 'LEADER', 'HATCH', 'TEXT']: continue
                
                layer_original = e.dxf.layer.upper().strip()
                stats_layers[layer_original] = stats_layers.get(layer_original, 0) + 1
                
                try:
                    pts = self._extraer_primitivas(e)
                    if not pts: continue
                    
                    if layer_original in self.LAYERS_CORTE:
                        geometria_corte.append(pts)
                    elif layer_original in self.LAYERS_MARK:
                        geometria_mark.append(pts)
                    else:
                        entidades_ignoradas += 1
                except: pass

            for lay, count in stats_layers.items():
                status = "IGNORADO"
                if lay in self.LAYERS_CORTE: status = "-> CORTE"
                elif lay in self.LAYERS_MARK: status = "-> MARK"
                agregar_log(f"  - Capa '{lay}': {count} entidades {status}")
            
            if entidades_ignoradas > 0:
                agregar_log(f"  > Se descartaron {entidades_ignoradas} entidades de otras capas.")

            if len(geometria_corte) == 0:
                agregar_log("\n[ERROR] No hay entidades de CORTE válidas.")
                self._guardar_reporte(ruta_reporte, log)
                return False

            # UNIÓN
            agregar_log(f"\n[FASE 2] UNIÓN DE GEOMETRÍA:")
            unidos = self._unir_primitivas_agresivo(geometria_corte, 0.1)
            if len(unidos) > 1: unidos = self._unir_primitivas_agresivo(unidos, 1.0)
            if len(unidos) > 1: unidos = self._unir_primitivas_agresivo(unidos, 3.0)
            agregar_log(f"  > Geometrías unidas finales: {len(unidos)}")

            # CLASIFICACIÓN
            cerrados = []
            abiertos = [] # Ahora se ignorarán silenciosamente, pero se reportan
            
            for cadena in unidos:
                if self._dist(cadena[0], cadena[-1]) < 3.0:
                    bulge_last = cadena[-1][2]
                    cadena[-1] = (cadena[0][0], cadena[0][1], bulge_last)
                    area = self._calcular_area_con_bulges(cadena)
                    if area > 1.0: 
                        cerrados.append({'pts': cadena, 'area': area})
                    else:
                        abiertos.append(cadena) # Ruido / Basura
                else:
                    abiertos.append(cadena) # Líneas que no cerraron

            agregar_log(f"\n[FASE 3] RESULTADOS:")
            agregar_log(f"  > Piezas/Agujeros Detectados: {len(cerrados)}")
            if len(abiertos) > 0:
                agregar_log(f"  > Elementos abiertos/ruido descartados: {len(abiertos)}")

            # ESCRITURA
            doc_out = ezdxf.new('R2010')
            msp_out = doc_out.modelspace()
            
            doc_out.layers.new(name='CUT_OUTER', dxfattribs={'color': 1})
            doc_out.layers.new(name='CUT_INNER', dxfattribs={'color': 3})
            doc_out.layers.new(name='MARK', dxfattribs={'color': 4})
            # Eliminada capa OPEN_FAIL

            if cerrados:
                cerrados.sort(key=lambda x: x['area'], reverse=True)
                # Outer
                msp_out.add_lwpolyline(cerrados[0]['pts'], format='xyb', dxfattribs={'layer': 'CUT_OUTER', 'closed': True})
                # Inner
                for item in cerrados[1:]:
                    msp_out.add_lwpolyline(item['pts'], format='xyb', dxfattribs={'layer': 'CUT_INNER', 'closed': True})
            
            # Mark
            for m in geometria_mark: 
                msp_out.add_lwpolyline(m, format='xyb', dxfattribs={'layer': 'MARK'})

            doc_out.saveas(ruta_salida)
            agregar_log(f"\n[EXITO] Guardado en carpeta 'Procesados'")
            
            self._guardar_reporte(ruta_reporte, log)
            return True

        except Exception as e:
            import traceback
            agregar_log(f"\n[ERROR] {str(e)}\n{traceback.format_exc()}")
            self._guardar_reporte(ruta_reporte, log)
            return False

# =========================================================================
# INTERFAZ (Actualizada para crear carpeta 'Procesados')
# =========================================================================
if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw() 
    root.attributes('-topmost', True) 

    print("Abriendo selector de archivos...")
    ruta = filedialog.askopenfilename(
        title="Selecciona DXF para Procesar",
        filetypes=[("Archivos DXF", "*.dxf")]
    )
    
    if ruta and os.path.exists(ruta):
        # 1. Definir carpeta origen
        carpeta_origen = os.path.dirname(ruta)
        nombre_archivo = os.path.basename(ruta)
        
        # 2. Definir carpeta destino "Procesados"
        carpeta_procesados = os.path.join(carpeta_origen, "Procesados")
        
        # 3. Crear carpeta si no existe
        if not os.path.exists(carpeta_procesados):
            os.makedirs(carpeta_procesados)
            print(f"📁 Carpeta creada: {carpeta_procesados}")
            
        # 4. Definir ruta final (Mismo nombre de archivo, dentro de Procesados)
        salida = os.path.join(carpeta_procesados, nombre_archivo)
        
        print(f"\nIniciando proceso para: {nombre_archivo}")
        proc = ProcesadorDXF()
        exito = proc.limpiar_archivo(ruta, salida)
        
        if exito:
            print(f"\n✅ LISTO.")
            print(f"Revisa la carpeta 'Procesados' dentro de: {carpeta_origen}")
        else:
            print(f"\n❌ ERROR.")
    
    root.destroy()
    input("\nPresiona Enter para cerrar...")