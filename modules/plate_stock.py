"""Estado de stock de placas sin dependencias de BD (seguro en workers multiproceso)."""


def normalizar_estado_stock(valor_stock) -> str:
    """Misma semántica que HerinoxPlateSync._to_disponibilidad (default: NO DISPONIBLE)."""
    if isinstance(valor_stock, bool):
        return "DISPONIBLE" if valor_stock else "NO DISPONIBLE"
    txt = str(valor_stock or "").strip().upper()
    if txt in {"DISPONIBLE", "1", "TRUE", "SI", "YES"}:
        return "DISPONIBLE"
    if txt in {"NO DISPONIBLE", "NO_DISPONIBLE", "0", "FALSE", "NO"}:
        return "NO DISPONIBLE"
    if txt in {"NO EXISTENTE", "N/A", "NA"}:
        return "NO EXISTENTE"
    return "NO DISPONIBLE"


def stock_de_fila(placa) -> str:
    if not isinstance(placa, (list, tuple)) or len(placa) <= 8:
        return "NO DISPONIBLE"
    return normalizar_estado_stock(placa[8])


def stock_permite_nesting(valor_stock) -> bool:
    return normalizar_estado_stock(valor_stock) == "DISPONIBLE"
