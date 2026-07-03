"""Verifica que placas NO DISPONIBLES nunca entren al motor de nesting."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.sheets_manager import PlatesManager
from modules.herinox_catalog_cache import cargar_respaldo_placas


def main():
    mgr = PlatesManager()
    emp, prov, meta = cargar_respaldo_placas()
    if not emp:
        print("SKIP: sin respaldo local de placas")
        return 0

    mgr._datos_empresa = emp
    mgr._datos_proveedor = prov
    nesting = mgr.obtener_datos_placas()

    no_disp = [
        str(p[2])
        for p in emp
        if len(p) > 8 and mgr.normalizar_estado_stock(p[8]) != "DISPONIBLE"
    ]
    usados_no_disp = [
        code
        for code in no_disp
        if any(str(p[2]) == code for p in nesting)
    ]

    print(f"Respaldo: {meta.get('source', '?')} @ {meta.get('updated_at', '?')}")
    print(f"EMPRESA total={len(emp)} | NO DISPONIBLE={len(no_disp)} | nesting={len(nesting)}")
    if usados_no_disp:
        print(f"FAIL: placas no disponibles en nesting: {usados_no_disp[:10]}")
        return 1
    print("OK: ninguna placa NO DISPONIBLE en inventario de nesting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
