# ==========================================
# utils_nesting.py
# Funciones de Lógica, Archivos y Base de Datos
# ==========================================
import os
import re
import copy
import csv
import glob

def _espesor_pulgadas_desde_clave(clave: str) -> float:
    """Extrae espesor numérico (pulg.) de claves tipo '0.188_A 36' para ordenar."""
    base = str(clave or "").strip().split("_", 1)[0].strip().replace(",", ".")
    if not base:
        return float("inf")
    if "/" in base:
        try:
            num, den = base.split("/", 1)
            return float(num) / float(den)
        except Exception:
            return float("inf")
    try:
        return float(base)
    except Exception:
        return float("inf")


def clave_nesting_sort_key(clave: str) -> tuple:
    """Orden: menor calibre/espesor → mayor; desempate por material."""
    esp = _espesor_pulgadas_desde_clave(clave)
    mat = str(clave or "").split("_", 1)[1].strip().upper() if "_" in str(clave) else ""
    return (esp, mat, str(clave).upper())


def _normalizar_espesor_a_calibre(valor_calibre):
    """
    Alinea el formato de espesor con la lógica de reporte_cortes/calibre_nominal.
    """
    try:
        from interface.postgres_connector import _normalizar_calibre_nominal
        nominal = _normalizar_calibre_nominal(valor_calibre)
        if nominal is None or str(nominal).strip() == "":
            return str(valor_calibre or "").strip() or "N/A"
        return str(nominal).strip()
    except Exception:
        raw = str(valor_calibre or "").strip()
        if not raw:
            return "N/A"
        base = raw.split("_", 1)[0].strip()
        return base or "N/A"

def obtener_siguiente_consecutivo(db_config):
    """Busca en PostgreSQL el último número de W.O. y devuelve el siguiente.
       Si el servidor está desconectado, ABORTA y lanza un error estricto."""
    try:
        import psycopg2
        conexion = psycopg2.connect(**db_config)
        cursor = conexion.cursor()
        
        cursor.execute("SELECT work_order FROM reporte_cortes WHERE work_order LIKE 'W.O.%'")
        resultados = cursor.fetchall()
        
        max_num = 0
        for fila in resultados:
            texto_wo = fila[0]
            numeros = re.findall(r"W\.O\.\s*(\d+)", texto_wo)
            if numeros:
                num = int(numeros[0])
                if num > max_num:
                    max_num = num
                    
        return max_num + 1
        
    except Exception as e:
        raise Exception(f"⛔ SIN CONEXIÓN AL SERVIDOR MES.\nNo se pueden generar carpetas.\nVerifique la red o que la IP 192.168.2.80 esté encendida.\n\nDetalle técnico: {e}")
    finally:
        if 'conexion' in locals() and conexion:
            cursor.close()
            conexion.close()
            
def crear_estructura_carpetas(
    ruta_origen_dxfs, consecutivo, qty_tanks, es_swo=False, modo_local=False
):
    """Crea el árbol de directorios estandarizado de Grupo Arga.
       Redirige a 'Máxima Optimización' si es una S.W.O."""
    if es_swo:
        nombre_carpeta_raiz = f"S.W.O {int(consecutivo):02d} X{qty_tanks}"
        if modo_local:
            try:
                from interface.qt.export_paths import desktop_nesteos_locales

                ruta_base_wo = os.path.join(
                    desktop_nesteos_locales(), "Máxima Optimización", nombre_carpeta_raiz
                )
            except Exception:
                ruta_base_wo = os.path.join(
                    os.path.expanduser("~"),
                    "Desktop",
                    "Nesteos Locales",
                    "Máxima Optimización",
                    nombre_carpeta_raiz,
                )
        elif "ARGA METALS CORPORATE SYSTEM" in ruta_origen_dxfs:
            raiz_arga = ruta_origen_dxfs.split("ARGA METALS CORPORATE SYSTEM")[0]
            ruta_base_wo = os.path.join(raiz_arga, "Máxima Optimización", nombre_carpeta_raiz)
        else:
            raiz_arga = "X:\\"
            ruta_base_wo = os.path.join(raiz_arga, "Máxima Optimización", nombre_carpeta_raiz)
    else:
        nombre_carpeta_raiz = f"W.O. {consecutivo} X{qty_tanks}"
        ruta_base_wo = os.path.join(ruta_origen_dxfs, nombre_carpeta_raiz)

    arbol = {
        "ARGA MODEL CORE": ["3D NESTING", "DXF NESTING", "PLANOS", "STL"],
        "MATERIALS": ["LISTA LARGOS 2"],
        "PRODUCTION": ["BENDING", "CORTE SIERRACINTA", "LASER CUTTING", "MAQUINADOS", "PINTURA", "SOLDADURA"],
        "QUALITY": []
    }
    
    try:
        os.makedirs(ruta_base_wo, exist_ok=True)
        for carpeta_principal, subcarpetas in arbol.items():
            ruta_prin = os.path.join(ruta_base_wo, carpeta_principal)
            os.makedirs(ruta_prin, exist_ok=True)
            for subcarpeta in subcarpetas:
                ruta_sub = os.path.join(ruta_prin, subcarpeta)
                os.makedirs(ruta_sub, exist_ok=True)
        return nombre_carpeta_raiz, ruta_base_wo
    except Exception as e:
        print(f"❌ [ERROR] No se pudo crear las carpetas: {e}")
        return nombre_carpeta_raiz, ruta_origen_dxfs
    
def generar_combinaciones_lotes(T):
    escenarios = [[(T, 1)]]
    if T >= 3:
        q3, r3 = T // 3, T % 3
        if r3 == 0: escenarios.append([(3, q3)])
        else: escenarios.append([(3, q3), (r3, 1)])
    m1 = T // 2
    m2 = T - m1
    if m1 != m2: escenarios.append([(m1, 1), (m2, 1)])
    else: escenarios.append([(m1, 2)])
    for f in range(4, T):
        if T % f == 0 and f != m1 and f != m2:
            escenarios.append([(f, T//f)])
            break
    unique = []
    for e in escenarios:
        e_sorted = sorted(e)
        if e_sorted not in unique: unique.append(e_sorted)
    return unique

def escalar_piezas(datos, T_original, k_objetivo):
    nuevos_datos = []
    for item in datos:
        pieza, mat, qty, cal, st, ruta = item
        try:
            base_qty = max(1, int(round(float(qty) / T_original)))
            nueva_qty = base_qty * k_objetivo
        except: nueva_qty = qty
        nuevos_datos.append((pieza, mat, str(nueva_qty), cal, st, ruta))
    return nuevos_datos

def ensamblar_escenario(esc, nestings):
    lista_ordenes = []
    costo_total_proyecto = 0
    eficiencia_sum = 0
    hojas_count = 0
    
    for lote_k, lote_mult in esc:
        res_k = nestings[lote_k]
        for _ in range(lote_mult):
            orden_dict = {}
            for clave, info in res_k.items():
                orden_dict[clave] = copy.deepcopy(info)
                if "hojas" in orden_dict[clave]:
                    for hoja in orden_dict[clave]["hojas"]:
                        hoja["lote_k"] = lote_k
                        hoja["lote_mult"] = 1
                        hoja["lote_desc"] = f"Lote {lote_k}X"
                        if not hoja.get("es_retazo") and not hoja.get("ignorar_deduccion"):
                            costo_total_proyecto += hoja.get('precio_placa', 0)
                        eficiencia_sum += hoja.get('eficiencia', 0)
                        hojas_count += 1
            lista_ordenes.append({"lote_k": lote_k, "data": orden_dict})

    efi_promedio = eficiencia_sum / max(1, hojas_count)
    return lista_ordenes, costo_total_proyecto, efi_promedio

def generar_csv_compras(ruta_job, nombre_wo, resultados, ruta_destino=None, datos_piezas=None, es_swo=False, db_config=None):
    """Calcula el Cost Split con Trazabilidad y lo inyecta EXCLUSIVAMENTE a PostgreSQL."""
    import os
    import psycopg2
    import glob
    import csv
    import re  # 🚀 NUEVO: Para cazar el sufijo X
    
    es_swo_real = es_swo or "S.W.O" in nombre_wo.upper() or "SWO" in nombre_wo.upper()
    
    print("\n" + "="*60)
    print("🕵️ INICIANDO RASTREO DE DATOS COMERCIALES Y FINANCIEROS")
    print("="*60)
    
    # =====================================================================
    # 1. ESCALADOR DE CARPETAS Y EXTRACCIÓN DEL CSV (BLINDADO)
    # =====================================================================
    ruta_busqueda = os.path.normpath(ruta_job)
    max_intentos = 5
    archivo_csv = None
    
    while max_intentos > 0:
        patron_csv = os.path.join(ruta_busqueda, "job_data_*.csv")
        archivos_encontrados = glob.glob(patron_csv)
        if archivos_encontrados:
            archivo_csv = archivos_encontrados[0]
            print(f"[RASTREO] ¡CSV Encontrado!: {os.path.basename(archivo_csv)}")
            break
        nueva_ruta = os.path.dirname(ruta_busqueda)
        if nueva_ruta == ruta_busqueda: break 
        ruta_busqueda = nueva_ruta
        max_intentos -= 1

    cliente_def, producto_def, job_def = "DESCONOCIDO", "DESCONOCIDO", os.path.basename(ruta_busqueda)
    cantidad_def, sstl_def, carbon_def = 1, 0.0, 0.0
    mapa_trazabilidad = {}
    
    try:
        if archivo_csv:
            with open(archivo_csv, mode='r', encoding='utf-8-sig') as f:
                lector = csv.DictReader(f)
                for fila in lector:
                    job_def = fila.get('Job Number', fila.get('Job', job_def)) 
                    cliente_def = fila.get('Cliente', cliente_def)
                    producto_def = fila.get('Producto', producto_def)
                    
                    try: cantidad_def = int(float(str(fila.get('Cantidad', '1')).replace(',', '').strip()))
                    except: cantidad_def = 1
                    try: sstl_def = float(str(fila.get('SSTL', '0')).replace(',', '').strip())
                    except: sstl_def = 0.0
                    try: carbon_def = float(str(fila.get('Carbon Steel', '0')).replace(',', '').strip())
                    except: carbon_def = 0.0
                    break 
            print(f"✅ [ÉXITO CSV] Job: {job_def} | Qty: {cantidad_def} | SSTL: {sstl_def} | Carbon: {carbon_def}")
        else:
            print(f"⚠️ [RASTREO FALLIDO] No se encontró el archivo job_data subiendo desde: {ruta_job}")
    except Exception as e:
        print(f"❌ [ERROR CSV] Falló la lectura: {e}")

    datos_financieros = {"cantidad": cantidad_def, "sstl": sstl_def, "carbon": carbon_def}
    print("="*60 + "\n")

    # =====================================================================
    # 2. CARGA DEL DICCIONARIO S.W.O. + RED DE SEGURIDAD ERP
    # =====================================================================
    if db_config:
        try:
            with psycopg2.connect(**db_config) as conn:
                with conn.cursor() as cursor:
                    if es_swo_real:
                        prefijos_unicos = set()
                        for clave_mat, info_mat in resultados.items():
                            for hoja in info_mat.get("hojas", []):
                                for pieza in hoja.get("piezas", []):
                                    n_pieza = pieza.get("nombre", "").upper()
                                    if "__" in n_pieza: 
                                        prefijos_unicos.add(n_pieza.split("__")[0].strip())
                        
                        for prefijo in prefijos_unicos:
                            # Intento 1: Diccionario (Ignorando mayúsculas/minúsculas)
                            cursor.execute("SELECT cliente, job_numero, producto FROM diccionario_swo WHERE prefijo_carpeta ILIKE %s ORDER BY id DESC LIMIT 1", (prefijo,))
                            reg = cursor.fetchone()
                            
                            # Intento 2: Buscar en la historia del ERP
                            if not reg:
                                cursor.execute("""
                                    SELECT j.cliente, j.job_number, j.producto 
                                    FROM erp_work_orders w
                                    JOIN erp_jobs j ON w.id_job = j.id_job
                                    WHERE w.nombre_wo ILIKE %s
                                    ORDER BY w.id_wo DESC LIMIT 1
                                """, (prefijo,))
                                reg = cursor.fetchone()

                            if reg:
                                mapa_trazabilidad[prefijo] = {"cli": reg[0], "job": reg[1], "prod": reg[2], "wo_orig": prefijo}
        except Exception as e: 
            print(f"⚠️ [ERROR DB] Trazabilidad interna falló: {e}")

    # =====================================================================
    # 3. CÁLCULOS MATEMÁTICOS Y DE ÁREA
    # =====================================================================
    reporte_placas = {}
    piezas_detalladas_db = {} 
    
    for clave_mat, info_mat in resultados.items():
        espesor = _normalizar_espesor_a_calibre(clave_mat)
        for idx, hoja in enumerate(info_mat.get("hojas", [])):
            p_id_base = hoja.get("placa_id", "DESCONOCIDO")
            sheet_uid = f"{p_id_base} P{idx+1}"
            precio_placa = float(hoja.get("precio_placa", 0.0))
            excluir_compra = bool(hoja.get("ignorar_deduccion")) and not hoja.get("es_retazo")
            
            largo_in = float(hoja.get("placa_h", 0.0)) / 25.4
            ancho_in = float(hoja.get("placa_w", 0.0)) / 25.4
            area_total_placa_in2 = largo_in * ancho_in
            
            if sheet_uid not in reporte_placas:
                reporte_placas[sheet_uid] = {
                    "largo_in": largo_in, "ancho_in": ancho_in, "espesor": espesor, 
                    "area_total_in2": area_total_placa_in2, "precio": precio_placa,
                    "ignorar_deduccion": excluir_compra, "asignaciones": {}
                }
            
            for pieza in hoja.get("piezas", []):
                nombre_crudo = pieza.get("nombre", "Desconocido").upper()
                if any(i in nombre_crudo for i in ["RETAZO", "TATUAJE", "BORDE", "REMANENTE", "RTZ", "REF__"]): continue
                
                area_pieza_in2 = float(pieza.get("area", 0.0)) / 645.16
                
                c_cli_f, c_prod_f, c_job_f = cliente_def, producto_def, job_def
                # POR DEFECTO: Lo llamamos SIN_ASIGNAR si la pieza es huérfana
                c_wo_f = nombre_wo if not es_swo_real else f"{nombre_wo} (SIN_ASIGNAR)"
                
                if es_swo_real:
                    if "__" in nombre_crudo:
                        prefijo = nombre_crudo.split("__")[0].strip()
                        c_wo_f = prefijo  # 🚀 BLINDAJE: Jamás será PIEZAS_BASE, siempre toma el prefijo real
                        if prefijo in mapa_trazabilidad:
                            d = mapa_trazabilidad[prefijo]
                            c_cli_f, c_prod_f, c_job_f = d["cli"], d["prod"], d["job"]
                    else:
                        c_wo_f = f"{nombre_wo} (SIN_ASIGNAR)" # Aplica para piezas sin prefijo como LIFTINGLUG

                clave_grupo = (c_cli_f, c_prod_f, c_job_f, c_wo_f)
                reporte_placas[sheet_uid]["asignaciones"][clave_grupo] = reporte_placas[sheet_uid]["asignaciones"].get(clave_grupo, 0.0) + area_pieza_in2

                if sheet_uid not in piezas_detalladas_db: piezas_detalladas_db[sheet_uid] = {}
                if c_wo_f not in piezas_detalladas_db[sheet_uid]: piezas_detalladas_db[sheet_uid][c_wo_f] = {}
                
                nombre_limpio = nombre_crudo.replace("_PLASMA", "").replace("_TAB", "").replace("_OXI", "").strip()
                piezas_detalladas_db[sheet_uid][c_wo_f][nombre_limpio] = piezas_detalladas_db[sheet_uid][c_wo_f].get(nombre_limpio, 0) + 1

    # =====================================================================
    # 4. PRORRATEO Y CONSTRUCCIÓN DE FILAS
    # =====================================================================
    # 🚀 NUEVO: Cazador de QTY (Busca la X y el número al final de la W.O.)
    match_qty = re.search(r'X(\d+)', nombre_wo.upper())
    qty_lote = int(match_qty.group(1)) if match_qty else 1

    datos_para_erp = []
    datos_para_costos = []
    for s_id, d in reporte_placas.items():
        area_tot = d["area_total_in2"]
        precio = d["precio"]
        area_asig_acumulada = sum(d["asignaciones"].values())
        
        scrap_total_placa = max(0.0, area_tot - area_asig_acumulada)
        costo_scrap_total_placa = (scrap_total_placa / area_tot * precio) if area_tot > 0 else 0
        
        for (cli, prod, job, wo_o), area_p in d["asignaciones"].items():
            pct_area = (area_p / area_tot) if area_tot > 0 else 0
            costo_p = pct_area * precio
            
            proporcion_cliente = (area_p / area_asig_acumulada) if area_asig_acumulada > 0 else 0
            scrap_cliente = scrap_total_placa * proporcion_cliente
            costo_scrap_cliente = costo_scrap_total_placa * proporcion_cliente
            
            fila = (
                nombre_wo, qty_lote, s_id, round(d["largo_in"], 2), round(d["ancho_in"], 2), 
                str(d["espesor"]), round(precio, 2), round(area_tot, 2), 
                cli, prod, job, wo_o, round(area_p, 2), 
                round(pct_area*100, 2), round(costo_p, 2), 
                round(scrap_cliente, 2), round(costo_scrap_cliente, 2)
            )
            datos_para_erp.append(fila)
            if not d.get("ignorar_deduccion"):
                datos_para_costos.append(fila)

    # =====================================================================
    # 5. INYECCIÓN DIRECTA A POSTGRESQL 
    # =====================================================================
    if db_config and datos_para_erp:
        try:
            if datos_para_costos:
                with psycopg2.connect(**db_config) as conn:
                    with conn.cursor() as cur:
                        query = """INSERT INTO costos_prorrateo (work_order, qty_lote, plate_id, largo_in, ancho_in, espesor, precio_placa, area_total_in2, cliente, producto, job, wo_origen, area_asignada_in2, porcentaje_area, costo_asignado, scrap_in2, costo_scrap) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
                        cur.executemany(query, datos_para_costos)
                    conn.commit()
                print(f"✅ [BD] Costos_prorrateo guardado ({len(datos_para_costos)} filas; {len(datos_para_erp) - len(datos_para_costos)} placa(s) sobrante omitidas).")
            else:
                print("ℹ️ [BD] Sin filas de compra: todas las placas madre están marcadas como sobrante.")

            try:
                from postgres_connector import guardar_tracking_erp
                guardar_tracking_erp(nombre_wo, datos_para_erp, piezas_detalladas_db, db_config, datos_financieros)
            except Exception as e_erp:
                print(f"⚠️ [Error ERP Tracking]: {e_erp}")

        except Exception as e: 
            print(f"❌ [Error Crítico en costos_prorrateo]: {e}")