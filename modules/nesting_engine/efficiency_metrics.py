"""
Métricas de eficiencia de nesting.

Eficiencia directa (por placa):
    Placa madre: área de piezas anidadas en el layout principal / área rectangular de la placa.
    RTZ: área de piezas en el retazo / rectángulo del retazo (placa_w × placa_h).

Eficiencia real (por placa):
    Placa madre: aprovechamiento absoluto del material comprado =
        (piezas en madre + piezas en sus RTZ asociados) / área de la placa madre.
    RTZ: área de piezas / área real del polígono del retazo (poly_borde_retazo).

Eficiencia del tanque (por calibre/grupo):
    real    -> suma área piezas reales (sin duplicar REF__) / suma área placas madre.
    directa -> promedio ponderado de eficiencia directa de cada placa madre.
"""

from shapely.geometry import Polygon

from .geometry_parser import area_poligonos_colocados

# Umbral para ofrecer "ignorar placa" en deducción de inventario/compras.
UMBRAL_EFICIENCIA_IGNORAR_PLACA = 25.0


def _es_pieza_real_nombre(nombre: str) -> bool:
    n = str(nombre or "")
    return not (
        n.startswith("REF__")
        or n.startswith("TATUAJE__")
        or n.startswith("RETAZO_GUILLOTINA__")
        or n.startswith("CU_CORTE__")
        or n.startswith("REMANENTE__")
    )


def _area_pieza_colocada(p) -> float:
    """Área neta en mm² desde polígonos colocados (fuente de verdad visual)."""
    pols = (p or {}).get("poligonos") or []
    area_geom = area_poligonos_colocados(pols)
    if area_geom > 0.0:
        return area_geom
    return float((p or {}).get("area", 0.0) or 0.0)


def area_piezas_reales_hoja(hoja) -> float:
    total = 0.0
    for p in (hoja or {}).get("piezas") or []:
        if not _es_pieza_real_nombre(p.get("nombre", "")):
            continue
        a = _area_pieza_colocada(p)
        if a > 0.0:
            p["area"] = a
        total += a
    return total


def _area_placa_rect(hoja) -> float:
    w = float((hoja or {}).get("placa_w", 0.0) or 0.0)
    h = float((hoja or {}).get("placa_h", 0.0) or 0.0)
    return max(0.0, w * h)


def _area_retazo_real(hoja) -> float:
    if not (hoja or {}).get("es_retazo"):
        return 0.0
    if (hoja or {}).get("poly_borde_retazo"):
        try:
            poly = Polygon((hoja or {}).get("poly_borde_retazo") or [])
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly is not None and (not poly.is_empty):
                return float(poly.area)
        except Exception:
            pass
    return _area_placa_rect(hoja)


def _indices_madre(hojas):
    return [i for i, h in enumerate(hojas or []) if isinstance(h, dict) and not h.get("es_retazo")]


def _rtz_por_madre(hojas):
    madres = _indices_madre(hojas)
    mapa = {idx: [] for idx in madres}
    for i, hoja in enumerate(hojas or []):
        if not isinstance(hoja, dict) or not hoja.get("es_retazo"):
            continue
        madre_idx = None
        for m in madres:
            if m < i:
                madre_idx = m
            else:
                break
        if madre_idx is not None:
            mapa.setdefault(madre_idx, []).append(hoja)
    return mapa


def _metricas_hoja_aislada(hoja) -> dict:
    area_piezas = area_piezas_reales_hoja(hoja)
    area_placa = _area_placa_rect(hoja)

    if (hoja or {}).get("es_retazo"):
        area_real = _area_retazo_real(hoja)
        efi_directa = (area_piezas / area_placa * 100.0) if area_placa > 0.0 else 0.0
        efi_real = (area_piezas / area_real * 100.0) if area_real > 0.0 else 0.0
    else:
        efi_directa = (area_piezas / area_placa * 100.0) if area_placa > 0.0 else 0.0
        efi_real = efi_directa

    return {
        "area_piezas_mm2": area_piezas,
        "area_placa_mm2": area_placa,
        "area_util_real_mm2": _area_retazo_real(hoja) if (hoja or {}).get("es_retazo") else area_placa,
        "eficiencia_directa": efi_directa,
        "eficiencia_real": min(100.0, efi_real),
        "eficiencia": efi_directa,
    }


def calcular_eficiencias_hoja(hoja, hojas_grupo=None) -> dict:
    if not isinstance(hoja, dict):
        return {}

    hojas = hojas_grupo if isinstance(hojas_grupo, list) else None
    if not hojas:
        return _metricas_hoja_aislada(hoja)

    rtz_map = _rtz_por_madre(hojas)
    try:
        idx = hojas.index(hoja)
    except ValueError:
        return _metricas_hoja_aislada(hoja)

    area_piezas = area_piezas_reales_hoja(hoja)
    area_placa = _area_placa_rect(hoja)

    if hoja.get("es_retazo"):
        area_real = _area_retazo_real(hoja)
        efi_directa = (area_piezas / area_placa * 100.0) if area_placa > 0.0 else 0.0
        efi_real = (area_piezas / area_real * 100.0) if area_real > 0.0 else 0.0
        return {
            "area_piezas_mm2": area_piezas,
            "area_placa_mm2": area_placa,
            "area_util_real_mm2": area_real,
            "eficiencia_directa": efi_directa,
            "eficiencia_real": min(100.0, efi_real),
            "eficiencia": efi_directa,
        }

    area_rtz = sum(area_piezas_reales_hoja(r) for r in rtz_map.get(idx, []))
    area_total_fisica = area_piezas + area_rtz
    efi_directa = (area_piezas / area_placa * 100.0) if area_placa > 0.0 else 0.0
    efi_real = (area_total_fisica / area_placa * 100.0) if area_placa > 0.0 else 0.0

    return {
        "area_piezas_mm2": area_piezas,
        "area_placa_mm2": area_placa,
        "area_util_real_mm2": area_placa,
        "area_piezas_con_rtz_mm2": area_total_fisica,
        "eficiencia_directa": efi_directa,
        "eficiencia_real": min(100.0, efi_real),
        "eficiencia": efi_directa,
    }


def actualizar_eficiencias_hoja(hoja, hojas_grupo=None) -> dict:
    if not isinstance(hoja, dict):
        return hoja
    hoja.update(calcular_eficiencias_hoja(hoja, hojas_grupo=hojas_grupo))
    return hoja


def calcular_eficiencias_grupo(hojas) -> dict:
    hojas = [h for h in (hojas or []) if isinstance(h, dict)]
    rtz_map = _rtz_por_madre(hojas)

    area_solo_madre = 0.0
    area_total_fisica = 0.0
    area_madre_total = 0.0

    for i, hoja in enumerate(hojas):
        if hoja.get("es_retazo"):
            continue

        actualizar_eficiencias_hoja(hoja, hojas_grupo=hojas)

        area_m = area_piezas_reales_hoja(hoja)
        area_r = sum(area_piezas_reales_hoja(r) for r in rtz_map.get(i, []))
        area_placa = _area_placa_rect(hoja)

        area_solo_madre += area_m
        area_total_fisica += area_m + area_r
        area_madre_total += area_placa

    for hoja in hojas:
        if not hoja.get("es_retazo"):
            continue
        actualizar_eficiencias_hoja(hoja, hojas_grupo=hojas)

    efi_tanque_directa = (
        (area_solo_madre / area_madre_total * 100.0) if area_madre_total > 0.0 else 0.0
    )
    efi_tanque_real = (
        (area_total_fisica / area_madre_total * 100.0) if area_madre_total > 0.0 else 0.0
    )

    return {
        "eficiencia_tanque_real": min(100.0, efi_tanque_real),
        "eficiencia_tanque_directa": efi_tanque_directa,
        "area_piezas_tanque_mm2": area_total_fisica,
        "area_piezas_solo_madre_mm2": area_solo_madre,
        "area_placas_madre_mm2": area_madre_total,
    }


def _es_pieza_contable(pieza) -> bool:
    nom = str((pieza or {}).get("nombre", "") or "")
    return not (
        nom.startswith("REMANENTE__")
        or nom.startswith("REF__")
        or nom.startswith("RETAZO_GUILLOTINA__")
        or nom.startswith("CU_CORTE__")
        or nom.startswith("TATUAJE__")
    )


def contar_piezas_hoja(hoja) -> int:
    piezas = (hoja or {}).get("piezas") or []
    return sum(1 for p in piezas if _es_pieza_contable(p))


def contar_piezas_grupo(info_grupo) -> int:
    if not isinstance(info_grupo, dict):
        return 0
    total = 0
    for hoja in info_grupo.get("hojas") or []:
        total += contar_piezas_hoja(hoja)
    return total


def formatear_eficiencias_placa(hoja) -> str:
    d = float((hoja or {}).get("eficiencia_directa", (hoja or {}).get("eficiencia", 0.0)) or 0.0)
    r = float((hoja or {}).get("eficiencia_real", d) or 0.0)
    n = contar_piezas_hoja(hoja)
    return f"DIR {d:.1f}% | REAL {r:.1f}% · {n} PZAS"


def formatear_eficiencias_tanque(info_grupo) -> str:
    if not isinstance(info_grupo, dict):
        return ""
    d = float(info_grupo.get("eficiencia_tanque_directa", 0.0) or 0.0)
    r = float(info_grupo.get("eficiencia_tanque_real", 0.0) or 0.0)
    n = contar_piezas_grupo(info_grupo)
    return f"TANQUE DIR {d:.1f}% | REAL {r:.1f}% · {n} PZAS"


def eficiencia_para_umbral_ignorar(hoja, hojas_grupo=None) -> float:
    """
    Métrica para decidir si ofrecer ignorar la placa en deducción.
    Usa eficiencia REAL (madre + RTZ / área placa madre): material total aprovechado
    del laminado comprado, no solo lo visible en el layout principal.
    """
    if not isinstance(hoja, dict) or hoja.get("es_retazo"):
        return 100.0
    if hojas_grupo:
        return float(calcular_eficiencias_hoja(hoja, hojas_grupo=hojas_grupo).get("eficiencia_real", 0.0) or 0.0)
    return float(
        hoja.get("eficiencia_real", hoja.get("eficiencia_directa", hoja.get("eficiencia", 0.0))) or 0.0
    )


def hoja_cuenta_para_deduccion(hoja) -> bool:
    """Placas madre marcadas como ignoradas no descuentan inventario ni costo."""
    if not isinstance(hoja, dict) or hoja.get("es_retazo"):
        return False
    return not bool(hoja.get("ignorar_deduccion", False))


def placa_debe_mostrar_opcion_ignorar(hoja, hojas_grupo=None) -> bool:
    if not isinstance(hoja, dict) or hoja.get("es_retazo"):
        return False
    if hoja.get("ignorar_deduccion"):
        return True
    return eficiencia_para_umbral_ignorar(hoja, hojas_grupo) < UMBRAL_EFICIENCIA_IGNORAR_PLACA


def actualizar_eficiencias_resultados(resultados_nesting) -> None:
    if not isinstance(resultados_nesting, dict):
        return
    for _, grupo in resultados_nesting.items():
        if not isinstance(grupo, dict) or "error" in grupo:
            continue
        hojas = grupo.get("hojas")
        if not isinstance(hojas, list):
            continue
        grupo.update(calcular_eficiencias_grupo(hojas))
