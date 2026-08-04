import re

from modules.herinox_sync import HerinoxPlateSync
from modules import plate_stock as _plate_stock


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
        try:
            from modules.herinox_catalog_cache import copiar_plates_xlsx_seed_si_falta

            copiar_plates_xlsx_seed_si_falta()
        except Exception:
            pass
        resultado = self._sync.refresh()
        if resultado.ok:
            self._datos_empresa, self._datos_proveedor = self._sync.get_sheet_rows()
            disp = sum(
                1
                for p in (self._datos_empresa or [])
                if isinstance(p, (list, tuple))
                and len(p) > 8
                and self._stock_permite_nesting(p[8])
            )
            print(
                f"[PLATES] Sync Herinox OK | empresa={len(self._datos_empresa or [])} "
                f"| proveedor={len(self._datos_proveedor or [])} "
                f"| disponibles_nesting={disp}"
            )
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
    def normalizar_estado_stock(valor_stock) -> str:
        return _plate_stock.normalizar_estado_stock(valor_stock)

    @staticmethod
    def stock_de_fila(placa) -> str:
        return _plate_stock.stock_de_fila(placa)

    @staticmethod
    def _stock_permite_nesting(valor_stock) -> bool:
        return _plate_stock.stock_permite_nesting(valor_stock)

    def filtrar_placas_para_nesting(self, placas):
        """Solo placas EMPRESA con stock Herinox = DISPONIBLE."""
        disponibles = []
        for placa in placas or []:
            if not isinstance(placa, (list, tuple)) or len(placa) <= 8:
                continue
            if self._stock_permite_nesting(placa[8]):
                disponibles.append(placa)
        return disponibles

    def obtener_datos_placas(self):
        """
        Función puente: El motor de Nesting y otras pestañas llaman a esta función por defecto.
        """
        datos_empresa, _datos_proveedor = self.obtener_datos_placas_divididos()
        total = len(datos_empresa or [])
        datos_empresa_disponibles = self.filtrar_placas_para_nesting(datos_empresa)
        excluidas = total - len(datos_empresa_disponibles)
        if total:
            print(
                f"[PLATES] Nesting: {len(datos_empresa_disponibles)}/{total} placas EMPRESA "
                f"DISPONIBLE ({excluidas} excluidas por stock Herinox)"
            )
        # Remanentes de inventario (CSV/Postgres) antepuestos con costo bajo.
        try:
            from modules.nesting_engine.remnants_inventory import (
                inject_remnants_into_datos_placas,
            )

            datos_empresa_disponibles = inject_remnants_into_datos_placas(
                datos_empresa_disponibles
            )
        except Exception as ex:
            print(f"[PLATES] remnants inject skip: {ex}", flush=True)
        # MODO ESTRICTO: stock físico EMPRESA + remanentes (si hay).
        return datos_empresa_disponibles
