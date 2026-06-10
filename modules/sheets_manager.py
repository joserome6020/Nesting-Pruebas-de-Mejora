import pandas as pd
import os
import shutil
import sys
from tkinter import messagebox
import config  # <--- NUEVO: Importamos nuestro gestor de rutas
from modules.herinox_sync import HerinoxPlateSync
from modules.plates_inventory import mirror_dev_plates_to_dist

class PlatesManager: 
    def __init__(self):
        """
        Gestiona la base de datos local de placas en formato .xlsx
        Ubicación: modules/Plates.xlsx
        """
        # =========================================================
        # NUEVO: Ruta persistente a prueba de .exe
        # =========================================================
        self.file_path = config.ruta_persistente(os.path.join("modules", "Plates.xlsx"))
        
        # Aseguramos que la carpeta "modules" exista al lado del .exe
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)

        # Si no existe el Excel persistente, intentamos sembrarlo desde el recurso empaquetado.
        # Esto evita que el .exe cree un Plates.xlsx vacío en primer arranque.
        if not os.path.exists(self.file_path):
            try:
                plantilla = config.ruta_recurso(os.path.join("modules", "Plates.xlsx"))
                if os.path.exists(plantilla):
                    shutil.copy2(plantilla, self.file_path)
            except Exception:
                pass
        
        # --- CONFIGURACIÓN DE MAPEO (Actualizado para leer MXN) ---
        self.MAPA_COLUMNAS = {
            "Thickness": "Thickness",   
            "Material":  "Material",    
            "Arga Code": "Arga Code",   
            "Length":    "Length",      
            "Width":     "Width",       
            "LB":        "LB",          
            "MXN":       "MXN",         # <--- ACTUALIZADO: Ahora busca MXN en el Excel
            "$$/LB":     "$$/LB",       
            "Stock":     "Stock"        
        }

        self.ORDEN_SISTEMA = [
            "Thickness", "Material", "Arga Code", "Length", 
            "Width", "LB", "MXN", "$$/LB", "Stock" # <--- ACTUALIZADO
        ]

    def sincronizar_desde_react_herinox(self):
        """
        Sincroniza Plates.xlsx contra react-Herinox usando Arga Code.
        Se ejecuta al iniciar para mantener Material/medidas/precio en MXN actualizados.
        """
        sync = HerinoxPlateSync()
        resultado = sync.run(self.file_path)
        if resultado.ok and not getattr(sys, "frozen", False):
            try:
                mirrored = mirror_dev_plates_to_dist()
                if mirrored:
                    print(f"[PLATES] Inventario espejado repo->dist: {mirrored}")
            except Exception as exc:
                print(f"[PLATES] No se pudo espejar inventario a dist: {exc}")
        return resultado

    def obtener_datos_placas_divididos(self):
        """Lee el archivo Excel y devuelve dos listas: (datos_empresa, datos_proveedor)"""
        if not os.path.exists(self.file_path):
            try:
                headers_excel = list(self.MAPA_COLUMNAS.values())
                df_vacio = pd.DataFrame(columns=headers_excel)
                df_vacio.to_excel(self.file_path, index=False)
            except: pass
            return [], [] 
        
        try:
            dic_dfs = pd.read_excel(self.file_path, sheet_name=[0, 1], dtype=str)
            
            df_empresa = dic_dfs.get(0)
            df_proveedor = dic_dfs.get(1)
            
            datos_empresa = self._procesar_dataframe(df_empresa) if df_empresa is not None else []
            datos_proveedor = self._procesar_dataframe(df_proveedor) if df_proveedor is not None else []
            
            # =========================================================
            # ETIQUETADO Y PRECIO ESTANDARIZADO (USD/LB)
            # =========================================================
            # "$$/LB" ya se considera en USD/LB (calculado en sync Herinox con DOF).
            for placa in datos_empresa:
                precio_usd = self._extraer_numero_local(placa[7])  # Posición 7 = USD/LB
                placa.append("EMPRESA")  # Se agrega en la posición 9
                placa.append(precio_usd) # Se agrega en la posición 10 (Precio estandarizado en USD/LB)
                
            # 2. Procesar Proveedor
            for placa in datos_proveedor:
                precio_usd = self._extraer_numero_local(placa[7])
                placa.append("PROVEEDOR") # Se agrega en la posición 9
                placa.append(precio_usd)  # Se agrega en la posición 10 (Precio estandarizado en USD/LB)
            # =========================================================

            return datos_empresa, datos_proveedor

        except Exception as e:
            print(f"Error crítico leyendo Plates.xlsx: {e}")
            return [], []

    def _extraer_numero_local(self, valor):
        """Función auxiliar para limpiar símbolos de moneda y texto"""
        import re
        try:
            limpio = str(valor).replace('$', '').replace(',', '.').strip()
            nums = re.findall(r"[-+]?\d*\.\d+|\d+", limpio)
            return float(nums[0]) if nums else 0.0
        except: return 0.0

    @staticmethod
    def _stock_permite_nesting(valor_stock) -> bool:
        estado = str(valor_stock or "").strip().upper()
        return estado == "DISPONIBLE"

    def _procesar_dataframe(self, df):
        """Función auxiliar que aplica tu lógica de limpieza a cualquier dataframe que le pasemos"""
        if df is None or df.empty:
            return []
            
        # Limpiar nombres de columnas
        df.columns = df.columns.str.strip()
        
        # Verificar y Renombrar columnas
        mapa_inverso = {v: k for k, v in self.MAPA_COLUMNAS.items()}
        df_renombrado = df.rename(columns=mapa_inverso)
        
        # Validar que existan las columnas críticas
        columnas_presentes = df_renombrado.columns.tolist()
        faltantes = [col for col in self.ORDEN_SISTEMA if col not in columnas_presentes]
        
        if faltantes:
            for col in faltantes:
                df_renombrado[col] = "0"
        
        # Reordenar y limpiar datos
        df_final = df_renombrado[self.ORDEN_SISTEMA].fillna("")
        
        # Convertir a lista de listas
        return df_final.values.tolist()

    def obtener_datos_placas(self):
        """
        Función puente: El motor de Nesting y otras pestañas llaman a esta función por defecto.
        """
        datos_empresa, datos_proveedor = self.obtener_datos_placas_divididos()
        # Regla operativa de planta:
        # solo placas con Stock=DISPONIBLE se consideran candidatas para nesting.
        datos_empresa_disponibles = [
            placa for placa in (datos_empresa or [])
            if len(placa) > 8 and self._stock_permite_nesting(placa[8])
        ]
        
        # ==============================================================================
        # 🛑 INTERRUPTOR DE INVENTARIO (FEATURE TOGGLE)
        # ==============================================================================
        # MODO ESTRICTO: Obliga al motor a usar SOLO el stock físico de Grupo Arga.
        # Si se acaba la lámina, el motor se detendrá y dejará las piezas como "pendientes".
        return datos_empresa_disponibles 
        
        # ------------------------------------------------------------------------------
        # MODO INFINITO (COMENTADO PARA EL FUTURO): 
        # Si algún día necesitas que el sistema invente placas virtuales (P1, P2...) 
        # cuando se acabe tu stock, comenta el 'return' de arriba y descomenta el de abajo:
        #
        # return datos_empresa_disponibles + [
        #     p for p in (datos_proveedor or [])
        #     if len(p) > 8 and self._stock_permite_nesting(p[8])
        # ]
        # ==============================================================================