"""
Numeración global de hojas (H1, H2, …) compartida entre DXF, PDF y base de datos.
"""


def hoja_cuenta_para_numeracion_global(hoja) -> bool:
    """
    Hojas que consumen un H secuencial W.O.-Hn / SWO-Hn.

    Las virtuales RTZCU de cobre (cu_rtz_virtual) usan RTZCU{n}-H{m} y no deben
    avanzar el contador global; si se numeran como W.O.-H* se desfasan madre/RTZ.
    """
    if not isinstance(hoja, dict):
        return False
    if hoja.get("cu_rtz_virtual"):
        return False
    return True


def _grupo_nesting_sort_key():
    try:
        from interface.utils_nesting import grupo_nesting_sort_key
    except ImportError:
        from utils_nesting import grupo_nesting_sort_key
    return grupo_nesting_sort_key


def iterar_grupos_nesting_ordenados(resultados):
    from .resultados_grupos import es_grupo_material_nesting

    sort_key = _grupo_nesting_sort_key()
    claves = sorted(
        (
            k
            for k, v in (resultados or {}).items()
            if es_grupo_material_nesting(k, v)
        ),
        key=lambda k: sort_key(k, (resultados or {}).get(k)),
    )
    for clave in claves:
        grupo = (resultados or {}).get(clave) or {}
        if not isinstance(grupo, dict) or "error" in grupo:
            continue
        yield clave, grupo


def iterar_hojas_ordenadas(resultados):
    for clave, grupo in iterar_grupos_nesting_ordenados(resultados):
        for hoja in grupo.get("hojas", []) or []:
            if isinstance(hoja, dict):
                yield clave, hoja


def resolver_order_label_sheet_codes(
    *,
    order_label: str | None = None,
    es_swo: bool = False,
    swo_id: str | None = None,
    wo_label: str | None = None,
) -> str:
    """Etiqueta usada en sheet_code: SWO-xxx-Hn o W.O. n-Hn."""
    swo = str(swo_id or order_label or "").strip()
    if es_swo and swo.upper().startswith("SWO"):
        return swo
    texto = str(wo_label or order_label or "").strip()
    return texto or "W.O."


def asignar_numeracion_global_hojas(
    resultados,
    order_label: str,
    *,
    sobrescribir: bool = True,
) -> int:
    """
    Asigna sheet_seq y sheet_code únicos en orden global estable (grupos ordenados).
    Devuelve el último sheet_seq asignado.
    """
    etiqueta = str(order_label or "").strip() or "W.O."
    seq = 0
    vistos: set[str] = set()

    for _clave, hoja in iterar_hojas_ordenadas(resultados):
        if not hoja_cuenta_para_numeracion_global(hoja):
            continue

        if (
            not sobrescribir
            and hoja.get("sheet_code")
            and hoja.get("sheet_seq") is not None
        ):
            codigo = str(hoja["sheet_code"]).strip()
            if codigo and codigo not in vistos:
                vistos.add(codigo)
                seq = max(seq, int(hoja["sheet_seq"]))
                continue

        seq += 1
        codigo = f"{etiqueta}-H{seq}"
        while codigo in vistos:
            seq += 1
            codigo = f"{etiqueta}-H{seq}"

        hoja["sheet_seq"] = seq
        hoja["sheet_code"] = codigo
        vistos.add(codigo)

    return seq


def numeracion_hojas_es_consistente(resultados, order_label: str) -> bool:
    """True si todas las hojas tienen sheet_code único acorde al order_label."""
    etiqueta = str(order_label or "").strip() or "W.O."
    prefijo = f"{etiqueta}-H"
    vistos: set[str] = set()
    esperado = 0

    for _clave, hoja in iterar_hojas_ordenadas(resultados):
        if not hoja_cuenta_para_numeracion_global(hoja):
            continue
        esperado += 1
        codigo = str(hoja.get("sheet_code") or "").strip()
        seq = hoja.get("sheet_seq")
        if not codigo.startswith(prefijo):
            return False
        try:
            n = int(codigo[len(prefijo):])
        except ValueError:
            return False
        if n != esperado or seq != esperado:
            return False
        if codigo in vistos:
            return False
        vistos.add(codigo)

    return esperado > 0 or not any(
        True for _ in iterar_hojas_ordenadas(resultados)
    )
