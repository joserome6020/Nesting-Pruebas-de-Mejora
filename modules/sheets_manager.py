import re

from modules.herinox_sync import HerinoxPlateSync


class PlatesManager:
    def __init__(self):
        """
        Gestiona el inventario de placas desde react-Herinox (API o PostgreSQL).
        Misma estructura de filas y pestañas SHEETS (EMPRESA / PROVEEDOR).
        """
        self.MAPA_COLUMNAS = {
            "Thickness": "Thickness",
            "Material": "Material",
            "Arga Code": "Arga Code",
            "Length": "Length",
            "Width": "Width",
            "LB": "LB",
            "MXN": "MXN",
            "$$/LB": "$$/LB",
            "Stock": "Stock",
        }

        self.ORDEN_SISTEMA = [
            "Thickness",
            "Material",
            "Arga Code",
            "Length",
            "Width",
            "LB",
            "MXN",
            "$$/LB",
            "Stock",
        ]

        self._sync = HerinoxPlateSync()
        self._datos_empresa: list = []
        self._datos_proveedor: list = []

    def sincronizar_desde_react_herinox(self):
        """
        Refresca inventario de placas desde react-Herinox.
        Se ejecuta al iniciar y desde la pestaña SHEETS para mantener datos al día.
        """
        resultado = self._sync.refresh()
        if resultado.ok:
            self._datos_empresa, self._datos_proveedor = self._sync.get_sheet_rows()
        return resultado

    def obtener_datos_placas_divididos(self):
        """Devuelve dos listas: (datos_empresa, datos_proveedor) desde Herinox."""
        if not self._datos_empresa and not self._datos_proveedor:
            resultado = self.sincronizar_desde_react_herinox()
            if not resultado.ok:
                print(f"[PLATES] No se pudo cargar inventario Herinox: {resultado.message}")
                return [], []

        return list(self._datos_empresa), list(self._datos_proveedor)

    @staticmethod
    def _extraer_numero_local(valor):
        """Función auxiliar para limpiar símbolos de moneda y texto."""
        try:
            limpio = str(valor).replace("$", "").replace(",", ".").strip()
            nums = re.findall(r"[-+]?\d*\.\d+|\d+", limpio)
            return float(nums[0]) if nums else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def _stock_permite_nesting(valor_stock) -> bool:
        estado = str(valor_stock or "").strip().upper()
        return estado == "DISPONIBLE"

    def obtener_datos_placas(self):
        """
        Función puente: El motor de Nesting y otras pestañas llaman a esta función por defecto.
        """
        datos_empresa, datos_proveedor = self.obtener_datos_placas_divididos()
        datos_empresa_disponibles = [
            placa
            for placa in (datos_empresa or [])
            if len(placa) > 8 and self._stock_permite_nesting(placa[8])
        ]

        # MODO ESTRICTO: solo stock físico de Grupo Arga (DISPONIBLE en hoja EMPRESA).
        return datos_empresa_disponibles
